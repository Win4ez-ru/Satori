"""Actual native process locks, including shared overlap and bounded exclusion races."""

import multiprocessing
import os
import queue
import sys
from pathlib import Path
from typing import Any

import pytest

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.source_policy.barrier import PolicyBarrier

pytestmark = pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX adapter")


def _hold(
    path: str,
    inode: tuple[int, int],
    exclusive: bool,
    entered: Any,
    release: Any,
) -> None:
    barrier = PolicyBarrier(Path(path), expected_inode=inode, timeout_seconds=2)
    with barrier.lease(exclusive=exclusive):
        entered.put("entered")
        if not release.wait(5):
            raise RuntimeError("test_release_timeout")


def test_native_exclusive_blocks_other_process_shared_with_bounded_timeout(tmp_path: Path) -> None:
    path = tmp_path / "policy.lock"
    inode = PolicyBarrier.create(path)
    context = multiprocessing.get_context("spawn")
    entered, release = context.Queue(), context.Event()
    process = context.Process(target=_hold, args=(str(path), inode, True, entered, release))
    process.start()
    try:
        assert entered.get(timeout=5) == "entered"
        with (
            pytest.raises(SourcePolicyError, match="policy_lock_timeout"),
            PolicyBarrier(path, expected_inode=inode, timeout_seconds=0.05).lease(exclusive=False),
        ):
            pass
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
    assert process.exitcode == 0


def test_native_shared_leases_overlap_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "policy.lock"
    inode = PolicyBarrier.create(path)
    context = multiprocessing.get_context("spawn")
    entered, release = context.Queue(), context.Event()
    process = context.Process(target=_hold, args=(str(path), inode, False, entered, release))
    with PolicyBarrier(path, expected_inode=inode).lease(exclusive=False):
        process.start()
        try:
            assert entered.get(timeout=5) == "entered"
        finally:
            release.set()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
    assert process.exitcode == 0


def test_floor_advance_waits_for_short_final_commit_lease(tmp_path: Path) -> None:
    path = tmp_path / "policy.lock"
    inode = PolicyBarrier.create(path)
    context = multiprocessing.get_context("spawn")
    entered, release = context.Queue(), context.Event()
    process = context.Process(target=_hold, args=(str(path), inode, True, entered, release))
    try:
        with PolicyBarrier(path, expected_inode=inode).lease(exclusive=False):
            process.start()
            with pytest.raises(queue.Empty):
                entered.get(timeout=0.15)
        assert entered.get(timeout=5) == "entered"
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
    assert process.exitcode == 0


def test_barrier_detects_inode_replacement_while_held(tmp_path: Path) -> None:
    path = tmp_path / "policy.lock"
    inode = PolicyBarrier.create(path)
    replacement = tmp_path / "replacement"
    PolicyBarrier.create(replacement)
    with (
        pytest.raises(SourcePolicyError, match="policy_lock_binding_invalid"),
        PolicyBarrier(path, expected_inode=inode).lease(exclusive=False),
    ):
        os.replace(replacement, path)


def test_barrier_missing_or_symlinked_file_never_recreates(tmp_path: Path) -> None:
    path = tmp_path / "absent"
    with (
        pytest.raises(SourcePolicyError, match="policy_lock_unavailable"),
        PolicyBarrier(path).lease(exclusive=True),
    ):
        pass
    assert not path.exists()
    target = tmp_path / "actual"
    PolicyBarrier.create(target)
    path.symlink_to(target)
    with (
        pytest.raises(SourcePolicyError, match="policy_lock_binding_invalid"),
        PolicyBarrier(path).lease(exclusive=True),
    ):
        pass
