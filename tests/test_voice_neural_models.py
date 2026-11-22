"""Pinned nested neural resources use synthetic files and no actual network or inference."""

import hashlib
import io
import json
import os
import select
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request

import pytest

from satori.desktop.voice import models, neural_models


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        payload: bytes,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        super().__init__(payload)
        self.url = url
        self.headers = {"Content-Length": str(len(payload))} if headers is None else headers
        self.status = status
        self.read_sizes: list[int] = []

    def geturl(self) -> str:
        return self.url

    def read(self, size: int | None = -1) -> bytes:
        assert size is not None
        self.read_sizes.append(size)
        return super().read(size)


class FakeOpener:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.requests: list[Request] = []
        self.responses: list[FakeResponse] = []
        self.options: dict[str, Any] = {}
        self.error: Exception | None = None
        self.final_url: str | None = None

    def open(self, request: Request, *, timeout: int) -> FakeResponse:
        assert timeout == neural_models.NETWORK_TIMEOUT_SECONDS
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        prefix = f"/resolve/{neural_models.MODEL_REVISION}/"
        name = request.full_url.split(prefix, 1)[1].split("?", 1)[0]
        response = FakeResponse(
            self.payloads[name], self.final_url or request.full_url, **self.options
        )
        self.responses.append(response)
        return response


@pytest.fixture
def fixture_model(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bytes], FakeOpener]:
    payloads = {
        "config.json": b'{"synthetic-root":true}',
        "model.safetensors": b"synthetic-root-weight-not-a-real-model",
        "speech_tokenizer/config.json": b'{"synthetic-nested":true}',
        "speech_tokenizer/model.safetensors": b"synthetic-nested-weight-not-a-real-model",
    }
    files = tuple(
        models.ModelFile(name, len(data), hashlib.sha256(data).hexdigest())
        for name, data in payloads.items()
    )
    monkeypatch.setattr(neural_models, "MODEL_FILES", files)
    opener = FakeOpener(payloads)
    monkeypatch.setattr(neural_models, "_opener", lambda: opener)
    return payloads, opener


def write_model(path: Path, payloads: dict[str, bytes]) -> None:
    path.mkdir()
    for name, data in payloads.items():
        target = path / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)


def test_pinned_manifest_is_an_exact_bounded_nested_resource_tree() -> None:
    assert neural_models.MODEL_REPOSITORY == "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit"
    assert neural_models.MODEL_REVISION == "f35faf19b0cc2160865af64ecf0f22f83d335135"
    assert len(neural_models.MODEL_FILES) == 14
    assert sum(item.size for item in neural_models.MODEL_FILES) == 2_312_059_416
    assert max(len(Path(item.name).parts) for item in neural_models.MODEL_FILES) == 2
    assert {Path(item.name).parent.as_posix() for item in neural_models.MODEL_FILES} == {
        ".",
        "speech_tokenizer",
    }
    weights = {item.name: item.sha256 for item in neural_models.MODEL_FILES}
    assert weights["model.safetensors"] == (
        "45a5b5ccd3cc473b9d3890ea922937036b56b948e8bd8c678e725329abdd1d00"
    )
    assert weights["speech_tokenizer/model.safetensors"] == (
        "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"
    )
    assert all("receipt" not in item.name for item in neural_models.MODEL_FILES)


def test_neural_setup_reuses_the_reviewed_origin_tls_and_noreplace_guards() -> None:
    assert vars(neural_models)["_check_url"] is models._check_url
    assert vars(neural_models)["_directory"] is models._directory
    assert vars(neural_models)["_opener"] is models._opener
    assert vars(neural_models)["_publish"] is models._publish


def test_validation_distinguishes_same_basename_resources_and_detects_tampering(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    payloads, _ = fixture_model
    destination = tmp_path / "model"
    assert not neural_models.validate_model_directory(destination)
    write_model(destination, payloads)
    assert neural_models.validate_model_directory(destination)
    nested = destination / "speech_tokenizer/config.json"
    nested.write_bytes(b"x" * len(payloads["speech_tokenizer/config.json"]))
    assert not neural_models.validate_model_directory(destination)
    nested.write_bytes(payloads["speech_tokenizer/config.json"])
    assert neural_models.validate_model_directory(destination)


@pytest.mark.parametrize(
    "extra", ["receipt.json", "speech_tokenizer/extra.json", "empty-directory"]
)
def test_unreviewed_files_and_empty_directories_are_rejected(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], extra: str
) -> None:
    payloads, _ = fixture_model
    destination = tmp_path / "model"
    write_model(destination, payloads)
    if extra == "empty-directory":
        (destination / extra).mkdir()
    else:
        (destination / extra).write_bytes(b"unreviewed content")
    assert not neural_models.validate_model_directory(destination)


@pytest.mark.parametrize("kind", ["root", "ancestor", "nested-parent", "nested-file", "fifo"])
def test_nested_validation_refuses_links_and_special_files_without_following_them(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], kind: str
) -> None:
    payloads, _ = fixture_model
    destination = tmp_path / "actual"
    write_model(destination, payloads)
    if kind == "root":
        link = tmp_path / "linked"
        link.symlink_to(destination, target_is_directory=True)
        destination = link
    elif kind == "ancestor":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        destination = link / "actual"
    elif kind == "nested-parent":
        (destination / "speech_tokenizer").rename(tmp_path / "outside")
        (destination / "speech_tokenizer").symlink_to(
            tmp_path / "outside", target_is_directory=True
        )
    else:
        nested = destination / "speech_tokenizer/model.safetensors"
        nested.unlink()
        if kind == "nested-file":
            nested.symlink_to(tmp_path / "must-never-read")
        else:
            os.mkfifo(nested)
    assert not neural_models.validate_model_directory(destination)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS atomic no-replace publication")
def test_setup_publishes_verified_nested_files_privately_and_is_idempotent(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads, opener = fixture_model
    destination = tmp_path / "parent" / "model"
    updates: list[tuple[int, int]] = []
    publish = models._publish

    def inspect(parent: int, staging: str, name: str) -> None:
        assert not destination.exists()
        assert neural_models.validate_model_directory(destination.parent / staging)
        publish(parent, staging, name)

    monkeypatch.setattr(neural_models, "_publish", inspect)
    neural_models.setup_model_directory(
        destination, progress=lambda done, total: updates.append((done, total))
    )
    assert neural_models.validate_model_directory(destination)
    assert updates[0] == (0, sum(map(len, payloads.values())))
    assert updates[-1][0] == updates[-1][1]
    assert [done for done, _ in updates] == sorted(done for done, _ in updates)
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / "speech_tokenizer").stat().st_mode & 0o777 == 0o700
    for name, data in payloads.items():
        assert (destination / name).read_bytes() == data
        assert (destination / name).stat().st_mode & 0o777 == 0o600
    assert len(opener.requests) == len(payloads)
    assert all(neural_models.MODEL_REVISION in request.full_url for request in opener.requests)
    assert all(request.get_header("Authorization") is None for request in opener.requests)
    assert all(request.get_header("Accept-encoding") == "identity" for request in opener.requests)
    neural_models.setup_model_directory(destination)
    assert len(opener.requests) == len(payloads)
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_existing_destination_is_preserved_without_downloading(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], kind: str
) -> None:
    _, opener = fixture_model
    destination = tmp_path / "model"
    if kind == "directory":
        destination.mkdir()
        (destination / "keep.txt").write_bytes(b"private unrelated data")
    elif kind == "file":
        destination.write_bytes(b"private unrelated data")
    else:
        destination.symlink_to(tmp_path / "missing")
    before = destination.lstat()
    with pytest.raises(models.VoiceModelError, match="model_destination_exists"):
        neural_models.setup_model_directory(destination)
    assert destination.lstat() == before
    assert opener.requests == []
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("kind", ["relative", "symlink-parent"])
def test_setup_rejects_unowned_paths_before_network_or_destination_creation(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], kind: str
) -> None:
    _, opener = fixture_model
    if kind == "relative":
        destination = Path("relative/model")
    else:
        (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
        destination = tmp_path / "linked/model"
    with pytest.raises(models.VoiceModelError):
        neural_models.setup_model_directory(destination)
    assert opener.requests == []
    assert not (tmp_path / "model").exists()


@pytest.mark.parametrize(
    "fault", ["digest", "short", "oversize", "length", "encoding", "status", "origin"]
)
def test_bad_downloads_never_publish_and_owned_nested_staging_is_cleaned(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], fault: str
) -> None:
    payloads, opener = fixture_model
    name = "speech_tokenizer/model.safetensors"
    if fault == "digest":
        opener.payloads = {**payloads, name: b"x" * len(payloads[name])}
    elif fault == "short":
        opener.payloads = {**payloads, name: payloads[name][:-1]}
        opener.options = {"headers": {}}
    elif fault == "oversize":
        opener.payloads = {**payloads, name: payloads[name] + b"extra"}
        opener.options = {"headers": {}}
    elif fault == "length":
        opener.options = {"headers": {"Content-Length": "invalid"}}
    elif fault == "encoding":
        opener.options = {"headers": {"Content-Encoding": "gzip"}}
    elif fault == "status":
        opener.options = {"status": 206}
    else:
        opener.final_url = "https://huggingface.co.evil.invalid/private"
    expected = "model_network_rejected" if fault == "origin" else "model_integrity_failed"
    with pytest.raises(models.VoiceModelError, match=expected):
        neural_models.setup_model_directory(tmp_path / "model")
    assert list(tmp_path.iterdir()) == []
    assert all(
        0 < size <= neural_models.CHUNK_BYTES for r in opener.responses for size in r.read_sizes
    )


def test_cancel_only_cleans_this_attempt_and_never_previous_partial_downloads(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    del fixture_model
    previous = tmp_path / ".model.download-previous"
    previous.mkdir()
    (previous / "keep.bin").write_bytes(b"old partial artifact")

    def cancel(done: int, _total: int) -> None:
        if done:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        neural_models.setup_model_directory(tmp_path / "model", progress=cancel)
    assert list(tmp_path.iterdir()) == [previous]
    assert (previous / "keep.bin").read_bytes() == b"old partial artifact"


def test_cleanup_does_not_delete_a_replacement_file_at_an_owned_name(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    del fixture_model
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"unrelated replacement")

    def replace_then_cancel(done: int, _total: int) -> None:
        if done:
            stage = next(tmp_path.glob(".model.download-*"))
            foreign.replace(stage / "config.json")
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        neural_models.setup_model_directory(tmp_path / "model", progress=replace_then_cancel)
    stage = next(tmp_path.glob(".model.download-*"))
    assert (stage / "config.json").read_bytes() == b"unrelated replacement"
    assert not (tmp_path / "model").exists()


def test_cleanup_does_not_follow_replaced_nested_directory_ownership(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    payloads, _ = fixture_model
    after_nested_config = sum(
        len(data) for name, data in payloads.items() if name != "speech_tokenizer/model.safetensors"
    )

    def replace_then_cancel(done: int, _total: int) -> None:
        if done >= after_nested_config:
            stage = next(tmp_path.glob(".model.download-*"))
            (stage / "speech_tokenizer").rename(stage / "moved-tokenizer")
            (stage / "speech_tokenizer").mkdir()
            (stage / "speech_tokenizer/keep.txt").write_bytes(b"unrelated replacement")
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        neural_models.setup_model_directory(tmp_path / "model", progress=replace_then_cancel)
    stage = next(tmp_path.glob(".model.download-*"))
    assert (stage / "speech_tokenizer/keep.txt").read_bytes() == b"unrelated replacement"
    assert (stage / "moved-tokenizer/config.json").read_bytes() == payloads[
        "speech_tokenizer/config.json"
    ]
    assert not (tmp_path / "model").exists()


def test_cleanup_does_not_delete_a_replacement_staging_directory(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    payloads, _ = fixture_model
    moved = tmp_path / "moved-evidence"

    def replace_then_cancel(done: int, _total: int) -> None:
        if done:
            stage = next(tmp_path.glob(".model.download-*"))
            stage.rename(moved)
            stage.mkdir()
            (stage / "keep.txt").write_bytes(b"unrelated replacement")
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        neural_models.setup_model_directory(tmp_path / "model", progress=replace_then_cancel)
    stage = next(tmp_path.glob(".model.download-*"))
    assert (stage / "keep.txt").read_bytes() == b"unrelated replacement"
    assert (moved / "config.json").read_bytes() == payloads["config.json"]
    assert not (tmp_path / "model").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS atomic no-replace publication")
def test_racing_empty_destination_is_never_replaced(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixture_model
    destination = tmp_path / "model"
    publish = models._publish
    identity: list[os.stat_result] = []

    def race(parent: int, staging: str, name: str) -> None:
        destination.mkdir()
        identity.append(destination.stat())
        publish(parent, staging, name)

    monkeypatch.setattr(neural_models, "_publish", race)
    with pytest.raises(models.VoiceModelError, match="model_destination_exists"):
        neural_models.setup_model_directory(destination)
    assert destination.stat() == identity[0]
    assert list(destination.iterdir()) == []
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS atomic no-replace publication")
def test_interrupt_after_publication_preserves_the_ready_tree(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixture_model
    destination = tmp_path / "model"
    publish = models._publish

    def interrupt(parent: int, staging: str, name: str) -> None:
        publish(parent, staging, name)
        raise KeyboardInterrupt

    monkeypatch.setattr(neural_models, "_publish", interrupt)
    with pytest.raises(KeyboardInterrupt):
        neural_models.setup_model_directory(destination)
    assert neural_models.validate_model_directory(destination)
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("overall", [False, True])
def test_file_and_whole_setup_deadlines_clean_the_owned_tree(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
    overall: bool,
) -> None:
    del fixture_model
    clock = [0.0]
    monkeypatch.setattr(neural_models, "monotonic", lambda: clock[0])
    if overall:
        monkeypatch.setattr(neural_models, "MAX_FILE_DOWNLOAD_SECONDS", 10_000)
    limit = neural_models.MAX_SETUP_SECONDS if overall else neural_models.MAX_FILE_DOWNLOAD_SECONDS
    original = FakeResponse.read

    def late_read(response: FakeResponse, size: int | None = -1) -> bytes:
        result = original(response, size)
        clock[0] = float(limit + 1)
        return result

    monkeypatch.setattr(FakeResponse, "read", late_read)
    with pytest.raises(models.VoiceModelError, match="model_download_timeout"):
        neural_models.setup_model_directory(tmp_path / "model")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "bad_name", [".", "../bad", "/absolute", "a/../b", "a//b", "a/b/c", "a\\b", "a?token=x"]
)
def test_invalid_manifest_paths_are_rejected_before_network(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
    bad_name: str,
) -> None:
    _, opener = fixture_model
    monkeypatch.setattr(neural_models, "MODEL_FILES", (models.ModelFile(bad_name, 1, "0" * 64),))
    with pytest.raises(models.VoiceModelError, match="model_configuration_invalid"):
        neural_models.setup_model_directory(tmp_path / "model")
    assert opener.requests == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["size", "count", "duplicate", "parent-conflict"])
def test_manifest_resource_limits_are_checked_before_network(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    _, opener = fixture_model
    files: tuple[models.ModelFile, ...]
    if kind == "size":
        files = (models.ModelFile("large.bin", neural_models.MAX_MODEL_BYTES + 1, "0" * 64),)
    elif kind == "count":
        files = tuple(models.ModelFile(f"{i}.bin", 1, "0" * 64) for i in range(33))
    elif kind == "duplicate":
        files = (models.ModelFile("a.bin", 1, "0" * 64),) * 2
    else:
        files = (models.ModelFile("a", 1, "0" * 64), models.ModelFile("a/b", 1, "0" * 64))
    monkeypatch.setattr(neural_models, "MODEL_FILES", files)
    with pytest.raises(models.VoiceModelError, match="model_configuration_invalid"):
        neural_models.setup_model_directory(tmp_path / "model")
    assert opener.requests == []


def test_full_model_progress_fits_the_owned_protocol_buffer_without_real_weights() -> None:
    total = neural_models.MAX_MODEL_BYTES
    updates = [(0, total)]
    reporter = neural_models._Progress(total, lambda done, size: updates.append((done, size)))
    downloaded = 0
    for _ in range(32):
        file_size = total // 32
        for received in range(1_000_000, file_size, 1_000_000):
            reporter.update(downloaded + received)
        downloaded += file_size
        reporter.update(downloaded, complete=True)
    lines = [
        json.dumps(
            {"event": "progress", "downloaded_bytes": done, "total_bytes": size},
            separators=(",", ":"),
        )
        + "\n"
        for done, size in updates
    ]
    assert len(lines) < 900
    assert sum(len(line.encode()) for line in lines) < 65_536
    assert updates[-1] == (total, total)
    assert [done for done, _ in updates] == sorted(done for done, _ in updates)


@pytest.mark.parametrize("ready", [False, True])
def test_check_only_is_read_only_and_never_creates_a_downloader(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ready: bool,
) -> None:
    payloads, opener = fixture_model
    destination = tmp_path / "model"
    if ready:
        write_model(destination, payloads)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("check-only must not create files or a network opener")

    monkeypatch.setattr(neural_models, "_opener", forbidden)
    monkeypatch.setattr(os, "mkdir", forbidden)
    result = neural_models.main(["--model-directory", str(destination), "--check-only"])
    output = capsys.readouterr()
    assert result == (0 if ready else 1)
    assert json.loads(output.out) == (
        {"event": "result", "ready": True}
        if ready
        else {"event": "error", "code": "model_integrity_failed"}
    )
    assert output.err == ""
    assert opener.requests == []
    assert destination.exists() is ready


def test_cli_network_failure_never_prints_paths_or_vendor_error_bodies(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, opener = fixture_model
    opener.error = URLError("PRIVATE URL TOKEN AND ACCOUNT")
    assert neural_models.main(["--model-directory", str(tmp_path / "model")]) == 1
    output = capsys.readouterr()
    events = [json.loads(line) for line in output.out.splitlines()]
    assert events[0]["event"] == "progress"
    assert events[-1] == {"event": "error", "code": "model_setup_failed"}
    assert "PRIVATE" not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err
    assert list(tmp_path.iterdir()) == []


def test_cli_restores_the_previous_sigterm_handler(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    capsys: pytest.CaptureFixture[str],
) -> None:
    del fixture_model
    previous = signal.getsignal(signal.SIGTERM)
    assert neural_models.main(["--model-directory", str(tmp_path / "model"), "--check-only"]) == 1
    assert signal.getsignal(signal.SIGTERM) is previous
    capsys.readouterr()


def test_cli_does_not_install_process_signal_handlers_from_another_thread(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    capsys: pytest.CaptureFixture[str],
) -> None:
    payloads, _ = fixture_model
    destination = tmp_path / "model"
    write_model(destination, payloads)
    previous = signal.getsignal(signal.SIGTERM)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            neural_models.main, ["--model-directory", str(destination), "--check-only"]
        ).result(timeout=5)
    assert result == 0
    assert signal.getsignal(signal.SIGTERM) is previous
    assert json.loads(capsys.readouterr().out) == {"event": "result", "ready": True}


def test_owned_sigterm_cleanup_removes_only_the_unpublished_nested_attempt(tmp_path: Path) -> None:
    destination = tmp_path / "model"
    script = (
        "import hashlib,os,sys,threading\n"
        "from pathlib import Path\n"
        "from satori.desktop.voice import neural_models as m\n"
        "m.MODEL_FILES=(m.ModelFile('speech_tokenizer/model.safetensors',4,"
        "hashlib.sha256(b'abcd').hexdigest()),)\n"
        "m._opener=lambda:None\n"
        "def partial(opener,descriptor,item,downloaded,progress,deadline,created):\n"
        " parent,_,name=item.name.rpartition('/')\n"
        " fd=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=descriptor)\n"
        " s=os.fstat(fd);created.append(m._OwnedFile(parent,name,s.st_dev,s.st_ino))\n"
        " os.write(fd,b'ab');os.close(fd)\n"
        " print('PARTIAL_READY',file=sys.stderr,flush=True)\n"
        " threading.Event().wait()\n"
        "m._download=partial\n"
        "raise SystemExit(m.main(['--model-directory',sys.argv[1]]))\n"
    )
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    try:
        assert process.stderr is not None
        ready, _, _ = select.select([process.stderr], [], [], 10)
        assert ready
        assert process.stderr.readline() == b"PARTIAL_READY\n"
        process.terminate()
        output, errors = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 130
    assert errors == b""
    records = [json.loads(line) for line in output.splitlines()]
    assert records[-1] == {"event": "error", "code": "model_setup_cancelled"}
    assert not any(record.get("ready") for record in records)
    assert list(tmp_path.iterdir()) == []
