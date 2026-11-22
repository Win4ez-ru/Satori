"""Owner-private, bounded gateway metadata; no credential service or home discovery."""

import ctypes
import errno
import fcntl
import json
import math
import os
import re
import secrets
import stat
import sys
from contextlib import suppress
from pathlib import Path
from threading import RLock
from typing import cast

from .contracts import GatewayFault

MAX_JSON_BYTES = 1024 * 1024
MAX_PRIVATE_FILE_BYTES = 64 * 1024 * 1024
_MAX_JSON_NODES = 65_536
_MAX_FILES = 16
_LOCK_NAME = ".owner.lock"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _fault() -> GatewayFault:
    return GatewayFault("operation_failed", 503)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _regular(value: os.stat_result, maximum_bytes: int = MAX_JSON_BYTES) -> None:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink != 1
        or value.st_size > maximum_bytes
    ):
        raise _fault()


class _ACLGuard:
    """FD-bound ACL metadata only; Darwin constants come from the official sys/acl.h."""

    def __init__(self) -> None:
        self._library: ctypes.CDLL | None = None
        if sys.platform == "darwin":
            try:
                library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
                library.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
                library.acl_get_fd_np.restype = ctypes.c_void_p
                library.acl_valid.argtypes = [ctypes.c_void_p]
                library.acl_valid.restype = ctypes.c_int
                library.acl_get_entry.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_void_p),
                ]
                library.acl_get_entry.restype = ctypes.c_int
                library.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
                library.acl_get_tag_type.restype = ctypes.c_int
                library.acl_free.argtypes = [ctypes.c_void_p]
                library.acl_free.restype = ctypes.c_int
                self._library = library
            except (OSError, AttributeError):
                raise _fault() from None
        elif not sys.platform.startswith("linux"):
            raise GatewayFault("not_supported", 503)

    def check(self, descriptor: int) -> None:
        library = self._library
        if library is None:
            try:
                list_attributes = getattr(os, "listxattr", None)
                if not callable(list_attributes):
                    raise GatewayFault("not_supported", 503)
                attributes: object = list_attributes(descriptor)
            except OSError:
                raise _fault() from None
            if not isinstance(attributes, list) or any(
                type(name) is not str for name in attributes
            ):
                raise _fault()
            if any("acl" in name.lower() for name in attributes):
                raise _fault()
            return
        ctypes.set_errno(0)
        acl = library.acl_get_fd_np(descriptor, 0x00000100)  # ACL_TYPE_EXTENDED
        if not acl:
            # Darwin reports ENOENT for an absent extended ACL on a live attached FD.
            # EACCES, unsupported retrieval, zero/stale errno and every other failure reject.
            if ctypes.get_errno() == errno.ENOENT and os.fstat(descriptor).st_nlink > 0:
                return
            raise _fault()
        try:
            if library.acl_valid(acl) != 0:
                raise _fault()
            selector = 0  # ACL_FIRST_ENTRY
            for _ in range(129):  # Official ACL_MAX_ENTRIES=128, plus the end sentinel.
                entry = ctypes.c_void_p()
                ctypes.set_errno(0)
                result = library.acl_get_entry(acl, selector, ctypes.byref(entry))
                # Darwin returns -1/EINVAL at end, unlike the Linux POSIX return convention.
                if result == -1 and ctypes.get_errno() == errno.EINVAL:
                    return
                if result != 0 or not entry.value:
                    raise _fault()
                tag = ctypes.c_int()
                if library.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or tag.value != 2:
                    # ACL_EXTENDED_DENY=2 is safe; ALLOW=1 and every unknown tag reject.
                    raise _fault()
                selector = -1  # ACL_NEXT_ENTRY
            raise _fault()
        finally:
            if library.acl_free(acl) != 0:
                raise _fault()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_metadata")
        result[key] = value
    return result


def _json_bounds(value: object) -> None:
    remaining = MAX_JSON_BYTES
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal remaining, nodes
        nodes += 1
        if depth > 16 or nodes > _MAX_JSON_NODES:
            raise _fault()
        if type(item) is str:
            remaining -= len(item.encode("utf-8"))
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int:
            if abs(item) > 2**64:
                raise _fault()
        elif type(item) is float:
            if not math.isfinite(item):
                raise _fault()
        elif type(item) is list:
            if len(item) > _MAX_JSON_NODES:
                raise _fault()
            for child in item:
                visit(child, depth + 1)
        elif type(item) is dict:
            if len(item) > _MAX_JSON_NODES:
                raise _fault()
            for key, child in item.items():
                if type(key) is not str:
                    raise _fault()
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise _fault()
        if remaining < 0:
            raise _fault()

    visit(value, 0)


def _directory_path(directory: Path) -> Path:
    if not directory.is_absolute() or ".." in directory.parts or "\x00" in str(directory):
        raise _fault()
    # Only the two official root-owned macOS aliases may precede no-follow traversal.
    if sys.platform == "darwin" and len(directory.parts) > 1:
        component = directory.parts[1]
        if component in {"tmp", "var"}:
            alias = Path("/") / component
            metadata = alias.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or os.readlink(alias) not in {
                    f"private/{component}",
                    f"/private/{component}",
                }:
                    raise _fault()
                directory = Path("/private") / component / Path(*directory.parts[2:])
    if directory == Path(directory.anchor):
        raise _fault()
    return directory


class PrivateStore:
    """One owner holds the namespace lock until close; all I/O stays directory-bound.

    File metadata is bounded to 1 MiB by default. An explicit caller may choose
    at most 64 MiB for non-JSON material such as the usage SQLite file; JSON reads
    and writes retain their independent 1 MiB parsing/serialization bounds.
    Existing permissions are never repaired silently. Root-owned sticky OS temporary
    roots are allowed as ancestors for explicitly supplied synthetic-test namespaces;
    arbitrary shared writable parents are rejected. No default runtime path is chosen.
    """

    def __init__(self, directory: Path, *, max_file_bytes: int = MAX_JSON_BYTES) -> None:
        if type(max_file_bytes) is not int or not 1 <= max_file_bytes <= MAX_PRIVATE_FILE_BYTES:
            raise _fault()
        self._max_file_bytes = max_file_bytes
        self._mutex = RLock()
        self._descriptor = -1
        self._lock = -1
        self._directory = directory
        try:
            self._acl = _ACLGuard()
            self._directory = _directory_path(directory)
            self._descriptor = self._open_directory(create=True)
            self._inspect_entries()
            try:
                self._lock = os.open(
                    _LOCK_NAME,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._descriptor,
                )
                os.fchmod(self._lock, 0o600)
                os.fsync(self._descriptor)
            except FileExistsError:
                self._lock = os.open(
                    _LOCK_NAME,
                    os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=self._descriptor,
                )
            lock_metadata = self._regular_fd(self._lock)
            if lock_metadata.st_size != 0:
                raise _fault()
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._ensure_live()
        except (OSError, ValueError, GatewayFault):
            self.close()
            raise _fault() from None

    def _open_directory(self, *, create: bool) -> int:
        descriptor = os.open(self._directory.anchor, _DIRECTORY_FLAGS)
        current = Path(self._directory.anchor)
        try:
            root = os.fstat(descriptor)
            if root.st_uid != 0 or root.st_mode & 0o022:
                raise _fault()
            self._acl.check(descriptor)
            for index, component in enumerate(self._directory.parts[1:]):
                current /= component
                leaf = index == len(self._directory.parts) - 2
                try:
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create or not leaf:
                        raise
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                    os.fchmod(child, 0o700)
                    os.fsync(descriptor)
                try:
                    metadata = os.fstat(child)
                    shared_os_temporary = (
                        current in {Path("/tmp"), Path("/private/tmp")}
                        and metadata.st_uid == 0
                        and stat.S_ISDIR(metadata.st_mode)
                        and stat.S_IMODE(metadata.st_mode) == 0o1777
                    )
                    if (
                        not stat.S_ISDIR(metadata.st_mode)
                        or metadata.st_uid not in {0, os.getuid()}
                        or (metadata.st_mode & 0o022 and not shared_os_temporary)
                        or (
                            leaf
                            and (
                                metadata.st_uid != os.getuid()
                                or stat.S_IMODE(metadata.st_mode) != 0o700
                            )
                        )
                    ):
                        raise _fault()
                    self._acl.check(child)
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _inspect_entries(self) -> None:
        with os.scandir(self._descriptor) as entries:
            for count, entry in enumerate(entries, 1):
                if count > _MAX_FILES:
                    raise _fault()
                self._target(entry.name)

    def _regular_fd(self, descriptor: int) -> os.stat_result:
        before = os.fstat(descriptor)
        _regular(before, self._max_file_bytes)
        self._acl.check(descriptor)
        after = os.fstat(descriptor)
        _regular(after, self._max_file_bytes)
        if _identity(before) != _identity(after) or (
            before.st_mode,
            before.st_size,
            before.st_ctime_ns,
        ) != (after.st_mode, after.st_size, after.st_ctime_ns):
            raise _fault()
        return after

    def _ensure_live(self) -> None:
        if self._descriptor < 0 or self._lock < 0:
            raise _fault()
        current = self._open_directory(create=False)
        try:
            if _identity(os.fstat(current)) != _identity(os.fstat(self._descriptor)):
                raise _fault()
        finally:
            os.close(current)
        held_lock = self._regular_fd(self._lock)
        observed = os.stat(_LOCK_NAME, dir_fd=self._descriptor, follow_symlinks=False)
        _regular(observed, self._max_file_bytes)
        if _identity(observed) != _identity(held_lock) or held_lock.st_size != 0:
            raise _fault()

    def _name(self, name: str) -> str:
        if type(name) is not str or _NAME.fullmatch(name) is None:
            raise _fault()
        return name

    def _target(self, name: str) -> os.stat_result | None:
        try:
            metadata = os.stat(name, dir_fd=self._descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        _regular(metadata, self._max_file_bytes)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=self._descriptor,
        )
        try:
            bound = self._regular_fd(descriptor)
            if _identity(bound) != _identity(metadata):
                raise _fault()
        finally:
            os.close(descriptor)
        return metadata

    def path(self, name: str) -> Path:
        """Validate a TLS filename now; the consumer must use it within this live owner."""
        with self._mutex:
            try:
                name = self._name(name)
                self._ensure_live()
                self._target(name)
                return self._directory / name
            except (OSError, ValueError, GatewayFault):
                raise _fault() from None

    def read_json(self, name: str) -> dict[str, object] | None:
        with self._mutex:
            try:
                name = self._name(name)
                self._ensure_live()
                previous = self._target(name)
                if previous is None:
                    return None
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=self._descriptor,
                )
                try:
                    before = self._regular_fd(descriptor)
                    if _identity(before) != _identity(previous):
                        raise _fault()
                    content = bytearray()
                    while len(content) <= MAX_JSON_BYTES:
                        block = os.read(descriptor, min(8192, MAX_JSON_BYTES + 1 - len(content)))
                        if not block:
                            break
                        content.extend(block)
                    after = os.fstat(descriptor)
                    observed = self._target(name)
                    if (
                        observed is None
                        or _identity(observed) != _identity(after)
                        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or len(content) != after.st_size
                        or len(content) > MAX_JSON_BYTES
                    ):
                        raise _fault()
                finally:
                    os.close(descriptor)
                self._ensure_live()
                value: object = json.loads(content, object_pairs_hook=_unique)
                if type(value) is not dict:
                    raise _fault()
                _json_bounds(value)
                return cast(dict[str, object], value)
            except (OSError, ValueError, TypeError, RecursionError, GatewayFault):
                raise _fault() from None

    def write_json(self, name: str, value: dict[str, object]) -> None:
        with self._mutex:
            stage: str | None = None
            try:
                name = self._name(name)
                if type(value) is not dict:
                    raise _fault()
                _json_bounds(value)
                payload = json.dumps(
                    value,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(payload) > MAX_JSON_BYTES:
                    raise _fault()
                self._ensure_live()
                self._inspect_entries()
                previous = self._target(name)
                stage = ".write-" + secrets.token_hex(16)
                descriptor = os.open(
                    stage,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._descriptor,
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    remaining = memoryview(payload)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0:
                            raise _fault()
                        remaining = remaining[written:]
                    os.fsync(descriptor)
                    staged = self._regular_fd(descriptor)
                finally:
                    os.close(descriptor)
                observed_stage = self._target(stage)
                current = self._target(name)
                if (
                    observed_stage is None
                    or _identity(observed_stage) != _identity(staged)
                    or (previous is None) != (current is None)
                    or (
                        previous is not None
                        and current is not None
                        and (
                            _identity(previous) != _identity(current)
                            or (previous.st_size, previous.st_mtime_ns, previous.st_ctime_ns)
                            != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                        )
                    )
                ):
                    raise _fault()
                self._ensure_live()
                os.replace(stage, name, src_dir_fd=self._descriptor, dst_dir_fd=self._descriptor)
                stage = None
                os.fsync(self._descriptor)
            except (OSError, ValueError, TypeError, RecursionError, GatewayFault):
                raise _fault() from None
            finally:
                if stage is not None:
                    with suppress(OSError):
                        os.unlink(stage, dir_fd=self._descriptor)

    def close(self) -> None:
        with self._mutex:
            if self._lock >= 0:
                with suppress(OSError):
                    fcntl.flock(self._lock, fcntl.LOCK_UN)
                with suppress(OSError):
                    os.close(self._lock)
                self._lock = -1
            if self._descriptor >= 0:
                with suppress(OSError):
                    os.close(self._descriptor)
                self._descriptor = -1
