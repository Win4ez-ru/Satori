"""Synthetic selected-file policy; no account or real personal-file fixtures."""

import os
import stat
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from satori.infrastructure.public_sources.selected_file import (
    MAX_DISPLAY_BYTES,
    MAX_FILE_BYTES,
    FileReadError,
    SelectedFileReader,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


def make_file(root: Path, body: bytes = b"SYNTHETIC text\n", name: str = "chosen.txt") -> Path:
    path = root.resolve() / name
    path.write_bytes(body)
    path.chmod(0o600)
    return path


def test_selection_is_metadata_only_and_read_has_bounded_plain_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_file(tmp_path)
    clock = Clock()
    reader = SelectedFileReader(clock=clock)
    original_read = os.read

    def forbidden_read(descriptor: int, count: int) -> bytes:
        del descriptor, count
        raise AssertionError("selection cannot read content")

    monkeypatch.setattr(os, "read", forbidden_read)
    selection = reader.select(path)
    assert selection.name == path.name
    assert selection.size_bytes == path.stat().st_size
    assert selection.selected_at == NOW
    assert selection.expires_at == NOW + timedelta(seconds=120)
    assert "chosen" not in repr(selection)
    assert str(selection.selection_id) not in repr(selection)
    monkeypatch.setattr(os, "read", original_read)
    result = reader.read(selection)
    assert result.status == "ok"
    assert result.text == "SYNTHETIC text\n"
    assert result.display_name == "chosen.txt"
    assert result.raw_bytes == 15
    assert result.displayed_bytes == 15
    assert result.observed_at == NOW
    assert result.expires_at == NOW + timedelta(minutes=5)
    assert "SYNTHETIC" not in repr(result)
    assert "chosen" not in repr(result)


def test_read_is_one_use_and_copy_of_preview_is_not_a_capability(tmp_path: Path) -> None:
    reader = SelectedFileReader(clock=Clock())
    selection = reader.select(make_file(tmp_path))
    with pytest.raises(FileReadError, match=r"^selection_invalid$"):
        reader.read(replace(selection))
    assert reader.read(selection).status == "ok"
    with pytest.raises(FileReadError, match=r"^selection_invalid$"):
        reader.read(selection)


def test_old_selection_cannot_cancel_newer_selection_or_cross_reader(tmp_path: Path) -> None:
    clock = Clock()
    reader = SelectedFileReader(clock=clock)
    first = reader.select(make_file(tmp_path, b"first", "first.txt"))
    current = reader.select(make_file(tmp_path, b"current", "current.txt"))
    with pytest.raises(FileReadError, match=r"^selection_invalid$"):
        reader.read(first)
    with pytest.raises(FileReadError, match=r"^selection_invalid$"):
        SelectedFileReader(clock=clock).read(current)
    assert reader.read(current).text == "current"


@pytest.mark.parametrize("ending", ["clear", "close", "expire", "clock_reverse"])
def test_ending_selection_never_reads_or_rearms(
    tmp_path: Path, ending: Literal["clear", "close", "expire", "clock_reverse"]
) -> None:
    clock = Clock()
    reader = SelectedFileReader(clock=clock)
    selection = reader.select(make_file(tmp_path))
    code = {
        "clear": "selection_invalid",
        "close": "reader_closed",
        "expire": "selection_expired",
        "clock_reverse": "clock_invalid",
    }[ending]
    if ending == "clear":
        reader.clear()
    elif ending == "close":
        reader.close()
    elif ending == "expire":
        clock.value += timedelta(seconds=120)
    else:
        clock.value -= timedelta(seconds=1)
    with pytest.raises(FileReadError, match=rf"^{code}$"):
        reader.read(selection)
    with pytest.raises(FileReadError, match=r"^(selection_invalid|reader_closed)$"):
        reader.read(selection)


@pytest.mark.parametrize("name", ["chosen.pdf", "chosen.docx", "chosen.png", "no_extension"])
def test_unsupported_format_is_honest_without_content_read(tmp_path: Path, name: str) -> None:
    path = make_file(tmp_path, name=name)
    with pytest.raises(FileReadError, match=r"^file_unsupported$"):
        SelectedFileReader(clock=Clock()).select(path)


@pytest.mark.parametrize(
    "path", [Path("relative.txt"), Path("/path/../chosen.txt"), Path("/x\n.txt")]
)
def test_nonexplicit_or_unsafe_path_is_rejected(path: Path) -> None:
    with pytest.raises(FileReadError, match=r"^path_invalid$"):
        SelectedFileReader(clock=Clock()).select(path)


def test_leaf_symlink_and_every_symlink_ancestor_are_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    original = make_file(root)
    link = root / "linked.txt"
    link.symlink_to(original)
    reader = SelectedFileReader(clock=Clock())
    with pytest.raises(FileReadError, match=r"^file_not_regular$"):
        reader.select(link)
    real_directory = root / "real"
    real_directory.mkdir()
    chosen = make_file(real_directory)
    linked_directory = root / "alias"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(FileReadError, match=r"^path_unavailable$"):
        reader.select(linked_directory / chosen.name)


def test_directory_and_fifo_fail_before_read_or_blocking_open(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    directory = root / "directory.txt"
    directory.mkdir()
    fifo = root / "pipe.txt"
    os.mkfifo(fifo, 0o600)
    reader = SelectedFileReader(clock=Clock())
    for path in (directory, fifo):
        with pytest.raises(FileReadError, match=r"^file_not_regular$"):
            reader.select(path)


def test_device_metadata_fails_before_opening_a_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_file(tmp_path)
    original_stat = os.stat

    def device_stat(*args: Any, **kwargs: Any) -> os.stat_result:
        original = original_stat(*args, **kwargs)
        values = list(original)
        values[0] = stat.S_IFCHR | 0o600
        return os.stat_result(values)

    monkeypatch.setattr(os, "stat", device_stat)
    with pytest.raises(FileReadError, match=r"^file_not_regular$"):
        SelectedFileReader(clock=Clock()).select(path)


def test_owner_permissions_and_hardlinks_are_explicitly_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_file(tmp_path)
    reader = SelectedFileReader(clock=Clock())
    uid = os.getuid()
    with monkeypatch.context() as patch:
        patch.setattr(os, "getuid", lambda: uid + 1)
        with pytest.raises(FileReadError, match=r"^file_owner_mismatch$"):
            reader.select(path)
    path.chmod(0o620)
    with pytest.raises(FileReadError, match=r"^file_permissions_unsafe$"):
        reader.select(path)
    path.chmod(0o600)
    os.link(path, path.with_name("hardlink.txt"))
    with pytest.raises(FileReadError, match=r"^file_hardlinked$"):
        reader.select(path)


@pytest.mark.parametrize("change", ["rewrite", "replace", "hardlink", "symlink", "mode"])
def test_identity_changes_after_preview_require_new_selection(
    tmp_path: Path, change: Literal["rewrite", "replace", "hardlink", "symlink", "mode"]
) -> None:
    path = make_file(tmp_path)
    reader = SelectedFileReader(clock=Clock())
    selection = reader.select(path)
    if change == "rewrite":
        path.write_bytes(b"CHANGED same file")
    elif change == "replace":
        path.unlink()
        make_file(tmp_path, b"replacement")
    elif change == "hardlink":
        os.link(path, path.with_name("another.txt"))
    elif change == "symlink":
        original = path.with_name("original.txt")
        path.rename(original)
        path.symlink_to(original)
    else:
        path.chmod(0o640)
    with pytest.raises(FileReadError):
        reader.read(selection)
    with pytest.raises(FileReadError, match=r"^selection_invalid$"):
        reader.read(selection)


def test_change_during_read_and_ancestor_replacement_cannot_return_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    parent = root / "selected-parent"
    parent.mkdir()
    path = make_file(parent, b"ORIGINAL BODY")
    reader = SelectedFileReader(clock=Clock())
    selection = reader.select(path)
    original_read = os.read
    replaced = False

    def replace_parent(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        body = original_read(descriptor, count)
        if not replaced:
            parent.rename(root / "old-parent")
            parent.mkdir()
            make_file(parent, b"REPLACED BODY")
            replaced = True
        return body

    monkeypatch.setattr(os, "read", replace_parent)
    with pytest.raises(FileReadError, match=r"^file_changed$"):
        reader.read(selection)


def test_inplace_change_during_read_is_detected_even_when_size_and_mtime_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_file(tmp_path, b"ORIGINAL")
    original_stat = path.stat()
    reader = SelectedFileReader(clock=Clock())
    selection = reader.select(path)
    original_read = os.read
    changed = False

    def rewrite(descriptor: int, count: int) -> bytes:
        nonlocal changed
        data = original_read(descriptor, count)
        if not changed:
            path.write_bytes(b"REPLACED")
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            changed = True
        return data

    monkeypatch.setattr(os, "read", rewrite)
    with pytest.raises(FileReadError, match=r"^file_changed$"):
        reader.read(selection)


@pytest.mark.parametrize(
    "payload", [b"\x00", b"\xff", b"\x1b[31m", b"\xc2\x80", "\u202eevil".encode()]
)
def test_binary_control_or_bidi_data_is_unsupported_not_displayed(
    tmp_path: Path, payload: bytes
) -> None:
    reader = SelectedFileReader(clock=Clock())
    selection = reader.select(make_file(tmp_path, payload))
    with pytest.raises(FileReadError, match=r"^content_unsupported$"):
        reader.read(selection)


@pytest.mark.parametrize(
    ("name", "payload", "media_type"),
    [
        ("empty.txt", b"", "text/plain"),
        ("chosen.MD", b"# Title\n<script>UNTRUSTED</script>", "text/markdown"),
        ("chosen.markdown", "текст\t\r\n".encode(), "text/markdown"),
        ("chosen.csv", b'name,note\n"Alice","literal <b>text</b>"\n', "text/csv"),
        ("chosen.json", b'{"instruction":"IGNORE RULES","list":[1,true,null]}', "application/json"),
        ("bom.txt", b"\xef\xbb\xbfplain", "text/plain"),
    ],
)
def test_supported_formats_are_untrusted_plain_text_not_execution(
    tmp_path: Path, name: str, payload: bytes, media_type: str
) -> None:
    reader = SelectedFileReader(clock=Clock())
    result = reader.read(reader.select(make_file(tmp_path, payload, name)))
    assert result.media_type == media_type
    assert result.text == payload.decode("utf-8-sig")
    assert result.status == ("empty" if not payload else "ok")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"unclosed":',
        b'{"nan":NaN}',
        b'{"infinity":Infinity}',
        b'{"overflow":1e999}',
        b"[" * 70 + b"0" + b"]" * 70,
    ],
)
def test_invalid_or_overcomplex_json_is_not_claimed_supported(
    tmp_path: Path, payload: bytes
) -> None:
    reader = SelectedFileReader(clock=Clock())
    with pytest.raises(FileReadError, match=r"^content_invalid$"):
        reader.read(reader.select(make_file(tmp_path, payload, "chosen.json")))


def test_invalid_and_overwide_csv_are_rejected(tmp_path: Path) -> None:
    reader = SelectedFileReader(clock=Clock())
    for payload in (b'"unterminated', b"," * 256):
        with pytest.raises(FileReadError, match=r"^content_invalid$"):
            reader.read(reader.select(make_file(tmp_path, payload, "chosen.csv")))


def test_source_and_display_byte_limits_are_distinct_and_utf8_boundary_is_preserved(
    tmp_path: Path,
) -> None:
    reader = SelectedFileReader(clock=Clock())
    path = make_file(tmp_path, b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(FileReadError, match=r"^file_too_large$"):
        reader.select(path)
    text = "a" * (MAX_DISPLAY_BYTES - 1) + "€" + "tail"
    path = make_file(tmp_path, text.encode())
    result = reader.read(reader.select(path))
    assert result.status == "partial"
    assert result.raw_bytes == len(text.encode())
    assert result.displayed_bytes == MAX_DISPLAY_BYTES - 1
    assert result.text == "a" * (MAX_DISPLAY_BYTES - 1)
    assert len(result.text.encode()) <= MAX_DISPLAY_BYTES


@pytest.mark.parametrize("failure", ["cancelled", "read_timeout"])
def test_cancellation_and_read_deadline_consume_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    reader = SelectedFileReader(clock=Clock())
    selection = reader.select(make_file(tmp_path))
    if failure == "read_timeout":
        times = iter([0.0, 3.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
    with pytest.raises(FileReadError, match=rf"^{failure}$"):
        reader.read(selection, cancelled=lambda: failure == "cancelled")
    with pytest.raises(FileReadError, match=r"^selection_invalid$"):
        reader.read(selection)


def test_closed_errors_do_not_include_path_or_content(tmp_path: Path) -> None:
    path = make_file(tmp_path, b"PRIVATE SYNTHETIC\x00", "private-name.txt")
    reader = SelectedFileReader(clock=Clock())
    with pytest.raises(FileReadError) as observed:
        reader.read(reader.select(path))
    assert observed.value.code == "content_unsupported"
    assert "private-name" not in str(observed.value)
    assert "PRIVATE SYNTHETIC" not in repr(observed.value)
