"""Explicit larger binary-file bounds preserve default JSON and private-file guards."""

from pathlib import Path

import pytest

from satori.desktop.mobile_gateway.contracts import GatewayFault
from satori.desktop.mobile_gateway.private_store import (
    MAX_JSON_BYTES,
    MAX_PRIVATE_FILE_BYTES,
    PrivateStore,
)


def test_default_file_bound_is_unchanged_and_explicit_binary_bound_can_reopen(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "private"
    store = PrivateStore(directory)
    path = store.path("synthetic.db")
    path.write_bytes(b"x" * (MAX_JSON_BYTES + 1))
    path.chmod(0o600)
    with pytest.raises(GatewayFault):
        store.path("synthetic.db")
    store.close()
    with pytest.raises(GatewayFault):
        PrivateStore(directory)
    larger = PrivateStore(directory, max_file_bytes=MAX_PRIVATE_FILE_BYTES)
    try:
        assert larger.path("synthetic.db") == path
    finally:
        larger.close()


@pytest.mark.parametrize("bound", [0, -1, True, 1.5, MAX_PRIVATE_FILE_BYTES + 1])
def test_explicit_file_bound_is_finite_positive_integer(tmp_path: Path, bound: int) -> None:
    with pytest.raises(GatewayFault):
        PrivateStore(tmp_path / "private", max_file_bytes=bound)
    assert not (tmp_path / "private").exists()


def test_larger_binary_bound_does_not_expand_json_read_or_write(tmp_path: Path) -> None:
    store = PrivateStore(tmp_path / "private", max_file_bytes=MAX_PRIVATE_FILE_BYTES)
    try:
        path = store.path("synthetic.json")
        path.write_bytes(b'"' + b"x" * MAX_JSON_BYTES + b'"')
        path.chmod(0o600)
        with pytest.raises(GatewayFault):
            store.read_json("synthetic.json")
        with pytest.raises(GatewayFault):
            store.write_json("other.json", {"value": "x" * MAX_JSON_BYTES})
    finally:
        store.close()


@pytest.mark.parametrize("mutation", ["mode", "symlink", "hardlink"])
def test_larger_bound_preserves_private_file_invariants(tmp_path: Path, mutation: str) -> None:
    store = PrivateStore(tmp_path / "private", max_file_bytes=MAX_PRIVATE_FILE_BYTES)
    try:
        path = store.path("synthetic.db")
        path.write_bytes(b"synthetic")
        path.chmod(0o600)
        if mutation == "mode":
            path.chmod(0o644)
        elif mutation == "symlink":
            path.rename(path.with_suffix(".kept"))
            path.symlink_to(path.with_suffix(".kept"))
        else:
            path.with_suffix(".linked").hardlink_to(path)
        with pytest.raises(GatewayFault):
            store.path("synthetic.db")
    finally:
        store.close()


def test_explicit_maximum_rejects_binary_file_above_64_mib(tmp_path: Path) -> None:
    store = PrivateStore(tmp_path / "private", max_file_bytes=MAX_PRIVATE_FILE_BYTES)
    try:
        path = store.path("synthetic.db")
        with path.open("wb") as file:
            file.truncate(MAX_PRIVATE_FILE_BYTES)
        path.chmod(0o600)
        assert store.path("synthetic.db") == path
        with path.open("r+b") as file:
            file.truncate(MAX_PRIVATE_FILE_BYTES + 1)
        with pytest.raises(GatewayFault):
            store.path("synthetic.db")
    finally:
        store.close()
