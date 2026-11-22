"""Explicit, pinned public-model setup; opening the app never calls this module's setup."""

import argparse
import ctypes
import errno
import hashlib
import json
import os
import signal
import ssl
import stat
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http.client import HTTPMessage
from pathlib import Path
from time import monotonic
from types import FrameType
from typing import IO, NoReturn
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import uuid4

MODEL_REPOSITORY = "istupakov/gigaam-v3-onnx"
MODEL_REVISION = "322c3b29492673eb7d0b434bfa9dfb8653e34d02"
MAX_MODEL_BYTES = 250_000_000
CHUNK_BYTES = 1_000_000
NETWORK_TIMEOUT_SECONDS = 20
MAX_FILE_DOWNLOAD_SECONDS = 600
MODEL_SETUP_ERROR_CODES = frozenset(
    {
        "model_configuration_invalid",
        "model_network_rejected",
        "model_integrity_failed",
        "model_download_timeout",
        "model_destination_exists",
        "model_publish_unsupported",
        "model_publish_failed",
        "model_setup_failed",
        "model_setup_cancelled",
    }
)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_ALLOWED_HOSTS = frozenset(
    {
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
        "us.aws.cdn.hf.co",
        "cdn-lfs.huggingface.co",
        "cdn-lfs-us-1.hf.co",
        "cdn-lfs-eu-1.hf.co",
    }
)


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str
    size: int
    sha256: str


MODEL_FILES = (
    ModelFile(
        "v3_e2e_rnnt_encoder.int8.onnx",
        224_570_477,
        "4e0e076a6076cd110277e529b8ac8f32cd5297f7fbebad5341ae8ddb7d00817b",
    ),
    ModelFile(
        "config.json",
        135,
        "0641fcf73f4af791c73f05083e38a658ff5dcbee3534a0c61396c10f4b97f0fe",
    ),
    ModelFile(
        "v3_e2e_rnnt_decoder.int8.onnx",
        1_159_170,
        "89014e134865615b91e037157e46e389b1271e6072460efc010ea08e61e23146",
    ),
    ModelFile(
        "v3_e2e_rnnt_joint.int8.onnx",
        687_791,
        "ade116563dbf66e503b0994efab6b5861412743e52bf31c39fc3fffa3783d5d1",
    ),
    ModelFile(
        "v3_e2e_rnnt_vocab.txt",
        13_354,
        "39abae20e692998290c574e606f11a9edef2902a1995463fcff63d1490cf22b7",
    ),
    ModelFile(
        "LICENSE.txt",
        1070,
        "f00de6715714c7a63d08639cdbfaa40224eefc407302614bd19f1a8b98c875aa",
    ),
)


class VoiceModelError(ValueError):
    """A static setup failure code; transport never exposes exception bodies."""

    def __init__(self, code: str) -> None:
        self.code = code if code in MODEL_SETUP_ERROR_CODES else "model_setup_failed"
        super().__init__(self.code)


def _manifest() -> tuple[ModelFile, ...]:
    files = MODEL_FILES
    if (
        not files
        or len({item.name for item in files}) != len(files)
        or sum(item.size for item in files) > MAX_MODEL_BYTES
        or any(
            not item.name
            or Path(item.name).name != item.name
            or item.name in {".", ".."}
            or item.size <= 0
            or len(item.sha256) != 64
            or any(character not in "0123456789abcdef" for character in item.sha256)
            for item in files
        )
    ):
        raise VoiceModelError("model_configuration_invalid")
    return files


def _directory(path: Path, *, create: bool = False) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise VoiceModelError("model_configuration_invalid")
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _valid(descriptor: int, files: tuple[ModelFile, ...]) -> bool:
    if set(os.listdir(descriptor)) != {item.name for item in files}:
        return False
    for item in files:
        source = os.open(item.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(source, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != item.size:
                return False
            digest = hashlib.sha256()
            remaining = item.size
            while remaining:
                data = stream.read(min(CHUNK_BYTES, remaining))
                if not data:
                    return False
                digest.update(data)
                remaining -= len(data)
            if stream.read(1) or digest.hexdigest() != item.sha256:
                return False
    return True


def validate_model_directory(path: Path) -> bool:
    """Accept only an absolute, link-free directory containing the exact pinned files."""

    descriptor: int | None = None
    try:
        files = _manifest()
        descriptor = _directory(path)
        return _valid(descriptor, files)
    except (OSError, ValueError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _check_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        allowed = (
            parsed.scheme == "https"
            and parsed.hostname in _ALLOWED_HOSTS
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise VoiceModelError("model_network_rejected")


class _PinnedRedirects(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> OpenerDirector:
    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
    # No ambient proxy credentials, cookies, auth handler or environment token.
    return build_opener(ProxyHandler({}), HTTPSHandler(context=context), _PinnedRedirects())


def _download(
    opener: OpenerDirector,
    descriptor: int,
    item: ModelFile,
    downloaded: int,
    total: int,
    progress: Callable[[int, int], None] | None,
) -> None:
    url = (
        f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{item.name}"
        "?download=true"
    )
    _check_url(url)
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "Satori-D2/1"})
    deadline = monotonic() + MAX_FILE_DOWNLOAD_SECONDS
    with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        _check_url(response.geturl())
        length = response.headers.get("Content-Length")
        encoding = response.headers.get("Content-Encoding", "identity")
        if (
            response.status != 200
            or encoding.lower() != "identity"
            or (length is not None and (not length.isdecimal() or int(length) != item.size))
        ):
            raise VoiceModelError("model_integrity_failed")
        target = os.open(
            item.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=descriptor,
        )
        digest = hashlib.sha256()
        received = 0
        with os.fdopen(target, "wb") as stream:
            while True:
                if monotonic() > deadline:
                    raise VoiceModelError("model_download_timeout")
                chunk = response.read(min(CHUNK_BYTES, item.size - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > item.size:
                    raise VoiceModelError("model_integrity_failed")
                digest.update(chunk)
                stream.write(chunk)
                if progress is not None:
                    progress(downloaded + received, total)
            if received != item.size or digest.hexdigest() != item.sha256:
                raise VoiceModelError("model_integrity_failed")
            stream.flush()
            os.fsync(stream.fileno())


def _publish(parent: int, staging: str, destination: str) -> None:
    """macOS atomic no-replace directory rename; an empty racing directory is preserved."""

    if sys.platform != "darwin":
        raise VoiceModelError("model_publish_unsupported")
    # macOS SDK sys/stdio.h: RENAME_EXCL = 0x00000004, renameatx_np since macOS 10.12.
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameatx_np
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(staging), parent, os.fsencode(destination), 0x00000004):
        code = ctypes.get_errno()
        if code in {errno.EEXIST, errno.ENOTEMPTY}:
            raise VoiceModelError("model_destination_exists")
        raise VoiceModelError("model_publish_failed")


def setup_model_directory(
    path: Path, *, progress: Callable[[int, int], None] | None = None
) -> None:
    """Download to a unique owned sibling and publish only a complete verified model.

    Existing incomplete/unrelated destinations are never repaired or removed. Interrupted
    staging is not resumed; another explicit attempt creates its own bounded staging area.
    """

    files = _manifest()
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise VoiceModelError("model_configuration_invalid")
    if validate_model_directory(path):
        return
    parent: int | None = None
    staged: int | None = None
    staging: str | None = None
    created_files: list[str] = []
    try:
        parent = _directory(path.parent, create=True)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise VoiceModelError("model_destination_exists")
        staging = f".{path.name}.download-{uuid4().hex}"
        os.mkdir(staging, mode=0o700, dir_fd=parent)
        staged = os.open(staging, _DIRECTORY_FLAGS, dir_fd=parent)
        opener = _opener()
        total = sum(item.size for item in files)
        downloaded = 0
        if progress is not None:
            progress(0, total)
        for item in files:
            created_files.append(item.name)
            _download(opener, staged, item, downloaded, total, progress)
            downloaded += item.size
        if not _valid(staged, files):
            raise VoiceModelError("model_integrity_failed")
        os.fsync(staged)
        _publish(parent, staging, path.name)
        staging = None
        os.fsync(parent)
    except VoiceModelError:
        raise
    except (OSError, ValueError) as error:
        raise VoiceModelError("model_setup_failed") from error
    finally:
        owned_staging = False
        if staged is not None and staging is not None and parent is not None:
            with suppress(OSError):
                original = os.fstat(staged)
                current = os.stat(staging, dir_fd=parent, follow_symlinks=False)
                owned_staging = (original.st_dev, original.st_ino) == (
                    current.st_dev,
                    current.st_ino,
                )
        if staged is not None:
            if owned_staging:
                for name in created_files:
                    with suppress(OSError):
                        os.unlink(name, dir_fd=staged)
            os.close(staged)
        if owned_staging and staging is not None and parent is not None:
            with suppress(OSError):
                os.rmdir(staging, dir_fd=parent)
        if parent is not None:
            os.close(parent)


@contextmanager
def _interrupt_on_terminate() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def interrupt(signum: int, frame: FrameType | None) -> NoReturn:
        del signum, frame
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit local Satori speech-model setup")
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    arguments = parser.parse_args(argv)

    def progress(downloaded: int, total: int) -> None:
        print(
            json.dumps({"event": "progress", "downloaded_bytes": downloaded, "total_bytes": total}),
            flush=True,
        )

    with _interrupt_on_terminate():
        try:
            if arguments.check_only:
                if not validate_model_directory(arguments.model_directory):
                    raise VoiceModelError("model_integrity_failed")
            else:
                setup_model_directory(arguments.model_directory, progress=progress)
        except VoiceModelError as error:
            print(json.dumps({"event": "error", "code": error.code}), flush=True)
            return 1
        except KeyboardInterrupt:
            print(json.dumps({"event": "error", "code": "model_setup_cancelled"}), flush=True)
            return 130
        except Exception:
            print(json.dumps({"event": "error", "code": "model_setup_failed"}), flush=True)
            return 1
        print(json.dumps({"event": "result", "ready": True}), flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
