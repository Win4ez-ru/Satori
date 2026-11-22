"""Explicit pinned neural-speech resources; validation never downloads or writes."""

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic
from urllib.request import OpenerDirector, Request
from uuid import uuid4

from satori.desktop.voice.models import (
    ModelFile,
    VoiceModelError,
    _check_url,
    _directory,
    _interrupt_on_terminate,
    _opener,
    _publish,
)

MODEL_REPOSITORY = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit"
MODEL_REVISION = "f35faf19b0cc2160865af64ecf0f22f83d335135"
MAX_MODEL_BYTES = 3_000_000_000
MAX_MODEL_FILES = 32
MAX_PATH_DEPTH = 2
CHUNK_BYTES = 1_000_000
PROGRESS_BYTES = 4_000_000
NETWORK_TIMEOUT_SECONDS = 20
MAX_FILE_DOWNLOAD_SECONDS = 900
MAX_SETUP_SECONDS = 1800
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

# Immutable revision metadata and public-file digests define these expected resource pins.
MODEL_FILES = (
    ModelFile(
        ".gitattributes", 1519, "11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361"
    ),
    ModelFile(
        "README.md", 1068, "481e4e53d6adf50558eb60eb15eae7eea67a3ca467dbe8e4eef9159f8d17d1f8"
    ),
    ModelFile(
        "config.json", 6058, "5a1dd8d7c9f4d04d5bdac309e9e748f16a96b0724d9c4de4974982dd84078715"
    ),
    ModelFile(
        "generation_config.json",
        245,
        "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa",
    ),
    ModelFile(
        "merges.txt", 1_671_839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"
    ),
    ModelFile(
        "model.safetensors",
        1_625_226_859,
        "45a5b5ccd3cc473b9d3890ea922937036b56b948e8bd8c678e725329abdd1d00",
    ),
    ModelFile(
        "model.safetensors.index.json",
        71_786,
        "8ea3f7115ca2b729e5af4e83b2580d1be97dd54d80e8ef7aa27257c04d84d325",
    ),
    ModelFile(
        "preprocessor_config.json",
        127,
        "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119",
    ),
    ModelFile(
        "speech_tokenizer/config.json",
        2336,
        "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167",
    ),
    ModelFile(
        "speech_tokenizer/configuration.json",
        76,
        "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd",
    ),
    ModelFile(
        "speech_tokenizer/model.safetensors",
        682_293_092,
        "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
    ),
    ModelFile(
        "speech_tokenizer/preprocessor_config.json",
        234,
        "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb",
    ),
    ModelFile(
        "tokenizer_config.json",
        7344,
        "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670",
    ),
    ModelFile(
        "vocab.json", 2_776_833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
    ),
)


def _manifest() -> tuple[ModelFile, ...]:
    files = MODEL_FILES
    if not files or len(files) > MAX_MODEL_FILES:
        raise VoiceModelError("model_configuration_invalid")
    names: set[str] = set()
    parents: set[str] = set()
    total = 0
    for item in files:
        path = PurePosixPath(item.name)
        if (
            not item.name
            or path.is_absolute()
            or str(path) != item.name
            or not 1 <= len(path.parts) <= MAX_PATH_DEPTH
            or any(
                part in {".", ".."}
                or not part.isascii()
                or any(not (c.isalnum() or c in "._-") for c in part)
                for part in path.parts
            )
            or item.name in names
            or type(item.size) is not int
            or item.size <= 0
            or len(item.sha256) != 64
            or any(c not in "0123456789abcdef" for c in item.sha256)
        ):
            raise VoiceModelError("model_configuration_invalid")
        names.add(item.name)
        if len(path.parts) == 2:
            parents.add(path.parts[0])
        total += item.size
    if names & parents or total > MAX_MODEL_BYTES:
        raise VoiceModelError("model_configuration_invalid")
    return files


def _layout(files: tuple[ModelFile, ...]) -> dict[str, tuple[ModelFile, ...]]:
    groups: dict[str, list[ModelFile]] = {"": []}
    for item in files:
        parent, _, _ = item.name.rpartition("/")
        groups.setdefault(parent, []).append(item)
    return {parent: tuple(items) for parent, items in groups.items()}


def _valid_files(descriptor: int, files: tuple[ModelFile, ...]) -> bool:
    for item in files:
        name = PurePosixPath(item.name).name
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
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


def _valid(descriptor: int, files: tuple[ModelFile, ...]) -> bool:
    groups = _layout(files)
    root_entries = {PurePosixPath(item.name).parts[0] for item in files}
    if set(os.listdir(descriptor)) != root_entries or not _valid_files(descriptor, groups[""]):
        return False
    for parent, items in groups.items():
        if not parent:
            continue
        child = os.open(parent, _DIRECTORY_FLAGS, dir_fd=descriptor)
        try:
            if set(os.listdir(child)) != {PurePosixPath(item.name).name for item in items}:
                return False
            if not _valid_files(child, items):
                return False
        finally:
            os.close(child)
    return True


def validate_model_directory(path: Path) -> bool:
    """Read only the exact bounded file tree through link-free directory descriptors."""

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


@dataclass
class _Progress:
    total: int
    callback: Callable[[int, int], None] | None
    last: int = 0

    def update(self, downloaded: int, *, complete: bool = False) -> None:
        if self.callback is not None and (
            downloaded - self.last >= PROGRESS_BYTES or (complete and downloaded != self.last)
        ):
            self.callback(downloaded, self.total)
            self.last = downloaded


@dataclass(frozen=True)
class _OwnedFile:
    parent: str
    name: str
    device: int
    inode: int


def _download(
    opener: OpenerDirector,
    descriptor: int,
    item: ModelFile,
    downloaded: int,
    progress: _Progress,
    deadline: float,
    created_files: list[_OwnedFile],
) -> None:
    deadline = min(deadline, monotonic() + MAX_FILE_DOWNLOAD_SECONDS)
    url = (
        f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{item.name}"
        "?download=true"
    )
    _check_url(url)
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "Satori-D2/1"})
    with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        _check_url(response.geturl())
        length = response.headers.get("Content-Length")
        if (
            response.status != 200
            or response.headers.get("Content-Encoding", "identity").lower() != "identity"
            or (length is not None and (not length.isdecimal() or int(length) != item.size))
        ):
            raise VoiceModelError("model_integrity_failed")
        parent, _, name = item.name.rpartition("/")
        target = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor
        )
        metadata = os.fstat(target)
        created_files.append(_OwnedFile(parent, name, metadata.st_dev, metadata.st_ino))
        received = 0
        digest = hashlib.sha256()
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
                if received < item.size:
                    progress.update(downloaded + received)
            if received != item.size or digest.hexdigest() != item.sha256:
                raise VoiceModelError("model_integrity_failed")
            stream.flush()
            os.fsync(stream.fileno())
        progress.update(downloaded + received, complete=True)


def _same_directory(parent: int, name: str, descriptor: int) -> bool:
    try:
        original = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        return (original.st_dev, original.st_ino) == (current.st_dev, current.st_ino)
    except OSError:
        return False


def _cleanup(
    parent: int | None,
    staging: str | None,
    staged: int | None,
    children: dict[str, int],
    created_files: list[_OwnedFile],
) -> None:
    owned = (
        parent is not None
        and staging is not None
        and staged is not None
        and _same_directory(parent, staging, staged)
    )
    if owned and staged is not None:
        for item in created_files:
            descriptor = children.get(item.parent) if item.parent else staged
            if descriptor is None or (
                item.parent and not _same_directory(staged, item.parent, descriptor)
            ):
                continue
            with suppress(OSError):
                current = os.stat(item.name, dir_fd=descriptor, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (item.device, item.inode):
                    os.unlink(item.name, dir_fd=descriptor)
        for name, descriptor in children.items():
            if _same_directory(staged, name, descriptor):
                with suppress(OSError):
                    os.rmdir(name, dir_fd=staged)
    for descriptor in children.values():
        os.close(descriptor)
    if staged is not None:
        os.close(staged)
    if owned and staging is not None and parent is not None:
        with suppress(OSError):
            os.rmdir(staging, dir_fd=parent)
    if parent is not None:
        os.close(parent)


def setup_model_directory(
    path: Path, *, progress: Callable[[int, int], None] | None = None
) -> None:
    """Explicitly fetch fixed public resources into private owned staging, never overwrite."""

    files = _manifest()
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise VoiceModelError("model_configuration_invalid")
    if validate_model_directory(path):
        return
    parent: int | None = None
    staged: int | None = None
    staging: str | None = None
    children: dict[str, int] = {}
    created_files: list[_OwnedFile] = []
    try:
        deadline = monotonic() + MAX_SETUP_SECONDS
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
        for name in _layout(files):
            if name:
                os.mkdir(name, mode=0o700, dir_fd=staged)
                children[name] = os.open(name, _DIRECTORY_FLAGS, dir_fd=staged)
        opener = _opener()
        reporter = _Progress(sum(item.size for item in files), progress)
        if progress is not None:
            progress(0, reporter.total)
        downloaded = 0
        for item in files:
            if monotonic() > deadline:
                raise VoiceModelError("model_download_timeout")
            item_parent, _, _ = item.name.rpartition("/")
            descriptor = children[item_parent] if item_parent else staged
            _download(opener, descriptor, item, downloaded, reporter, deadline, created_files)
            downloaded += item.size
        if not _valid(staged, files):
            raise VoiceModelError("model_integrity_failed")
        for descriptor in children.values():
            os.fsync(descriptor)
        os.fsync(staged)
        if not _same_directory(parent, staging, staged):
            raise VoiceModelError("model_publish_failed")
        _publish(parent, staging, path.name)
        staging = None
        os.fsync(parent)
    except VoiceModelError:
        raise
    except (OSError, ValueError) as error:
        raise VoiceModelError("model_setup_failed") from error
    finally:
        _cleanup(parent, staging, staged, children, created_files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit local Satori neural-speech model setup")
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    arguments = parser.parse_args(argv)

    def report(downloaded: int, total: int) -> None:
        print(
            json.dumps(
                {"event": "progress", "downloaded_bytes": downloaded, "total_bytes": total},
                separators=(",", ":"),
            ),
            flush=True,
        )

    with _interrupt_on_terminate():
        try:
            if arguments.check_only:
                if not validate_model_directory(arguments.model_directory):
                    raise VoiceModelError("model_integrity_failed")
            else:
                setup_model_directory(arguments.model_directory, progress=report)
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
