"""Fixed-inode POSIX shared/exclusive barrier; never held across provider waits."""

import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.artifacts import _bound_parent

if sys.platform in {"darwin", "linux"}:
    import fcntl


class PolicyBarrier:
    def __init__(
        self,
        path: Path,
        *,
        expected_inode: tuple[int, int] | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("invalid_policy_lock_timeout")
        self.path = path
        self.expected_inode = expected_inode
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def create(path: Path) -> tuple[int, int]:
        if sys.platform not in {"darwin", "linux"}:
            raise SourcePolicyError("policy_lock_platform_unsupported")
        try:
            with _bound_parent(path, create=True) as (parent, name):
                descriptor = os.open(
                    name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                    metadata = os.fstat(descriptor)
                    os.fsync(parent)
                    return metadata.st_dev, metadata.st_ino
                finally:
                    os.close(descriptor)
        except OSError:
            raise SourcePolicyError("policy_lock_initialization_failed") from None

    def _validate(self, metadata: os.stat_result) -> tuple[int, int]:
        inode = metadata.st_dev, metadata.st_ino
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or (self.expected_inode is not None and inode != self.expected_inode)
        ):
            raise SourcePolicyError("policy_lock_binding_invalid")
        return inode

    @contextmanager
    def lease(self, *, exclusive: bool) -> Iterator[None]:
        if sys.platform not in {"darwin", "linux"}:
            raise SourcePolicyError("policy_lock_platform_unsupported")
        try:
            with _bound_parent(self.path, create=False) as (parent, name):
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                inode = self._validate(before)
                descriptor = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    if self._validate(os.fstat(descriptor)) != inode:
                        raise SourcePolicyError("policy_lock_binding_invalid")
                    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                    deadline = time.monotonic() + self.timeout_seconds
                    while True:
                        try:
                            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise SourcePolicyError("policy_lock_timeout") from None
                            time.sleep(min(0.01, remaining))
                    try:
                        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if self._validate(current) != inode:
                            raise SourcePolicyError("policy_lock_binding_invalid")
                        yield
                        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if self._validate(current) != inode:
                            raise SourcePolicyError("policy_lock_binding_invalid")
                    finally:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        except SourcePolicyError:
            raise
        except OSError:
            raise SourcePolicyError("policy_lock_unavailable") from None
