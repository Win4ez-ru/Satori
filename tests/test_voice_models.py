"""Pinned speech-model setup without real network, model weights or audio capture."""

import hashlib
import io
import json
import os
import select
import signal
import ssl
import stat
import subprocess
import sys
from http.client import HTTPMessage
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request

import pytest

from satori.desktop.voice import models


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        data: bytes,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        super().__init__(data)
        self.url = url
        self.headers = headers if headers is not None else {"Content-Length": str(len(data))}
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

    def open(self, request: Request, *, timeout: int) -> FakeResponse:
        assert timeout == models.NETWORK_TIMEOUT_SECONDS
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        filename = request.full_url.split("?")[0].rsplit("/", 1)[1]
        response = FakeResponse(self.payloads[filename], request.full_url, **self.options)
        self.responses.append(response)
        return response


@pytest.fixture
def fixture_model(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bytes], FakeOpener]:
    payloads = {
        "model.bin": b"synthetic-model-not-a-real-weight",
        "config.json": b'{"synthetic":true}',
        "tokenizer.json": b'{"synthetic-tokenizer":true}',
        "vocabulary.txt": b"synthetic vocabulary",
    }
    manifest = tuple(
        models.ModelFile(name, len(data), hashlib.sha256(data).hexdigest())
        for name, data in payloads.items()
    )
    monkeypatch.setattr(models, "MODEL_FILES", manifest)
    opener = FakeOpener(payloads)
    monkeypatch.setattr(models, "_opener", lambda: opener)
    return payloads, opener


def write_model(path: Path, payloads: dict[str, bytes]) -> None:
    path.mkdir()
    for name, data in payloads.items():
        (path / name).write_bytes(data)


def test_fixed_manifest_is_the_reviewed_public_revision() -> None:
    assert models.MODEL_REPOSITORY == "istupakov/gigaam-v3-onnx"
    assert models.MODEL_REVISION == "322c3b29492673eb7d0b434bfa9dfb8653e34d02"
    assert len(models.MODEL_FILES) == 6
    assert sum(item.size for item in models.MODEL_FILES) == 226_431_997
    assert models.MODEL_FILES[0].sha256 == (
        "4e0e076a6076cd110277e529b8ac8f32cd5297f7fbebad5341ae8ddb7d00817b"
    )
    assert {item.name for item in models.MODEL_FILES} == {
        "config.json",
        "LICENSE.txt",
        "v3_e2e_rnnt_vocab.txt",
        "v3_e2e_rnnt_encoder.int8.onnx",
        "v3_e2e_rnnt_decoder.int8.onnx",
        "v3_e2e_rnnt_joint.int8.onnx",
    }


def test_validation_requires_all_exact_pinned_files(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    payloads, _ = fixture_model
    path = tmp_path / "model"
    assert not models.validate_model_directory(path)
    write_model(path, payloads)
    assert models.validate_model_directory(path)
    (path / "model.bin").write_bytes(b"x" * len(payloads["model.bin"]))
    assert not models.validate_model_directory(path)
    (path / "model.bin").write_bytes(payloads["model.bin"])
    (path / "unreviewed.json").write_text("not permitted")
    assert not models.validate_model_directory(path)
    assert not models.validate_model_directory(Path("relative/model"))


@pytest.mark.parametrize("kind", ["directory", "parent", "file"])
def test_validation_rejects_symlinks_without_reading_the_target(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], kind: str
) -> None:
    payloads, _ = fixture_model
    actual = tmp_path / "actual"
    write_model(actual, payloads)
    if kind == "directory":
        path = tmp_path / "link"
        path.symlink_to(actual, target_is_directory=True)
    elif kind == "parent":
        linked = tmp_path / "parent-link"
        linked.symlink_to(tmp_path, target_is_directory=True)
        path = linked / "actual"
    else:
        target = actual / "model.bin"
        target.unlink()
        target.symlink_to(tmp_path / "never-open")
        path = actual
    assert not models.validate_model_directory(path)


def test_validation_rejects_fifo_without_waiting_for_a_writer(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    payloads, _ = fixture_model
    path = tmp_path / "model"
    write_model(path, payloads)
    (path / "model.bin").unlink()
    os.mkfifo(path / "model.bin")
    assert not models.validate_model_directory(path)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS no-replace publication")
def test_setup_publishes_only_after_full_validation_and_is_idempotent(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads, opener = fixture_model
    destination = tmp_path / "new-parent" / "model"
    observed: list[tuple[int, int]] = []
    publish = models._publish

    def inspect_before_publish(parent: int, staging: str, name: str) -> None:
        assert not destination.exists()
        assert models.validate_model_directory(destination.parent / staging)
        publish(parent, staging, name)

    monkeypatch.setattr(models, "_publish", inspect_before_publish)
    models.setup_model_directory(
        destination, progress=lambda done, total: observed.append((done, total))
    )
    assert models.validate_model_directory(destination)
    assert observed[0] == (0, sum(map(len, payloads.values())))
    assert observed[-1][0] == observed[-1][1]
    assert [item[0] for item in observed] == sorted(item[0] for item in observed)
    assert destination.stat().st_mode & 0o777 == 0o700
    assert all((destination / name).stat().st_mode & 0o777 == 0o600 for name in payloads)
    assert list(destination.parent.iterdir()) == [destination]
    assert len(opener.requests) == 4
    assert all(models.MODEL_REVISION in request.full_url for request in opener.requests)
    assert all(request.get_header("Authorization") is None for request in opener.requests)
    models.setup_model_directory(destination)
    assert len(opener.requests) == 4


@pytest.mark.parametrize("existing", ["directory", "file", "symlink"])
def test_existing_incomplete_or_unrelated_destination_is_preserved(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], existing: str
) -> None:
    _, opener = fixture_model
    destination = tmp_path / "model"
    if existing == "directory":
        destination.mkdir()
        (destination / "keep.txt").write_text("private unrelated content")
    elif existing == "file":
        destination.write_text("private unrelated content")
    else:
        destination.symlink_to(tmp_path / "absent")
    before = destination.lstat()
    with pytest.raises(models.VoiceModelError, match="model_destination_exists"):
        models.setup_model_directory(destination)
    assert destination.lstat() == before
    assert not opener.requests
    if existing == "directory":
        assert (destination / "keep.txt").read_text() == "private unrelated content"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("kind", ["parent", "relative"])
def test_setup_refuses_unsafe_destination_paths(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], kind: str
) -> None:
    _, opener = fixture_model
    if kind == "parent":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / "model"
    else:
        path = Path("relative/model")
    with pytest.raises(models.VoiceModelError):
        models.setup_model_directory(path)
    assert not opener.requests
    assert not (tmp_path / "model").exists()


@pytest.mark.parametrize("failure", ["digest", "short", "oversize", "header", "encoding", "status"])
def test_corrupt_or_out_of_bounds_network_data_is_never_ready(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener], failure: str
) -> None:
    payloads, opener = fixture_model
    if failure == "digest":
        opener.payloads = {**payloads, "model.bin": b"x" * len(payloads["model.bin"])}
    elif failure == "short":
        opener.payloads = {**payloads, "model.bin": payloads["model.bin"][:-1]}
        opener.options = {"headers": {}}
    elif failure == "oversize":
        opener.payloads = {**payloads, "model.bin": payloads["model.bin"] + b"extra"}
        opener.options = {"headers": {}}
    elif failure == "header":
        opener.options = {"headers": {"Content-Length": "invalid"}}
    elif failure == "encoding":
        opener.options = {"headers": {"Content-Encoding": "gzip"}}
    else:
        opener.options = {"status": 206}
    destination = tmp_path / "model"
    with pytest.raises(models.VoiceModelError, match="model_integrity_failed"):
        models.setup_model_directory(destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
    assert not models.validate_model_directory(destination)
    assert all(
        0 < size <= models.CHUNK_BYTES
        for response in opener.responses
        for size in response.read_sizes
    )


def test_network_failure_is_static_and_does_not_publish_partial_data(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    _, opener = fixture_model
    opener.error = URLError("PRIVATE-TOKEN-IN-EXCEPTION")
    destination = tmp_path / "model"
    with pytest.raises(models.VoiceModelError, match=r"^model_setup_failed$"):
        models.setup_model_directory(destination)
    assert list(tmp_path.iterdir()) == []


def test_download_has_an_overall_file_deadline(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixture_model
    times = iter([0.0, float(models.MAX_FILE_DOWNLOAD_SECONDS + 1)])
    monkeypatch.setattr(models, "monotonic", lambda: next(times))
    with pytest.raises(models.VoiceModelError, match="model_download_timeout"):
        models.setup_model_directory(tmp_path / "model")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/file",
        "https://huggingface.co.evil.invalid/file",
        "https://cas-bridge.xethub.hf.co.evil.invalid/file",
        "https://us.aws.cdn.hf.co.evil.invalid/file",
        "http://us.aws.cdn.hf.co/file",
        "file:///private/tmp/file",
        "https://user:password@huggingface.co/file",
        "https://huggingface.co:444/file",
        "https://huggingface.co/file#fragment",
    ],
)
def test_redirects_reject_untrusted_origin_before_following(url: str) -> None:
    handler = models._PinnedRedirects()
    with pytest.raises(models.VoiceModelError, match="model_network_rejected"):
        handler.redirect_request(
            Request("https://huggingface.co/start"), io.BytesIO(), 302, "", HTTPMessage(), url
        )


@pytest.mark.parametrize("host", ["cas-bridge.xethub.hf.co", "us.aws.cdn.hf.co"])
def test_redirect_accepts_the_fixed_https_cdn(host: str) -> None:
    handler = models._PinnedRedirects()
    url = f"https://{host}/signed-public-file?temporary=signature"
    result = handler.redirect_request(
        Request("https://huggingface.co/start"), io.BytesIO(), 302, "", HTTPMessage(), url
    )
    assert result is not None
    assert result.full_url == url


def test_https_uses_explicit_certificate_store_and_ignores_ambient_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = ModuleType("certifi")
    monkeypatch.setattr(certificate, "where", lambda: "/reviewed/ca-bundle.pem", raising=False)
    monkeypatch.setitem(sys.modules, "certifi", certificate)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    observed: list[object] = []

    def create_context(*, cafile: str) -> ssl.SSLContext:
        assert cafile == "/reviewed/ca-bundle.pem"
        return context

    def inspect_handlers(*handlers: object) -> object:
        observed.extend(handlers)
        return object()

    monkeypatch.setattr(ssl, "create_default_context", create_context)
    monkeypatch.setattr(models, "build_opener", inspect_handlers)
    monkeypatch.setenv("HTTPS_PROXY", "https://private-user:private-password@untrusted.invalid")
    monkeypatch.setenv("HF_TOKEN", "PRIVATE-TOKEN")
    models._opener()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    proxies = [item for item in observed if isinstance(item, ProxyHandler)]
    assert len(proxies) == 1
    assert vars(proxies[0])["proxies"] == {}
    assert len([item for item in observed if isinstance(item, HTTPSHandler)]) == 1


def test_unknown_error_strings_cannot_cross_setup_protocol() -> None:
    error = models.VoiceModelError("PRIVATE-EXCEPTION-BODY")
    assert error.code == "model_setup_failed"
    assert str(error) == "model_setup_failed"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS no-replace publication")
def test_empty_destination_created_during_download_is_not_overwritten(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixture_model
    destination = tmp_path / "model"
    original = models._publish
    identity: list[os.stat_result] = []

    def race(parent: int, staging: str, name: str) -> None:
        destination.mkdir()
        identity.append(destination.stat())
        original(parent, staging, name)

    monkeypatch.setattr(models, "_publish", race)
    with pytest.raises(models.VoiceModelError, match="model_destination_exists"):
        models.setup_model_directory(destination)
    assert destination.stat() == identity[0]
    assert list(destination.iterdir()) == []
    assert list(tmp_path.iterdir()) == [destination]


def test_cancellation_never_publishes_a_partially_ready_model(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    del fixture_model

    def cancel(downloaded: int, _total: int) -> None:
        if downloaded:
            raise KeyboardInterrupt

    destination = tmp_path / "model"
    with pytest.raises(KeyboardInterrupt):
        models.setup_model_directory(destination, progress=cancel)
    assert not models.validate_model_directory(destination)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS no-replace publication")
def test_interrupt_after_atomic_publish_does_not_delete_the_ready_model(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixture_model
    destination = tmp_path / "model"
    original = models._publish

    def interrupt_after_rename(parent: int, staging: str, name: str) -> None:
        original(parent, staging, name)
        raise KeyboardInterrupt

    monkeypatch.setattr(models, "_publish", interrupt_after_rename)
    with pytest.raises(KeyboardInterrupt):
        models.setup_model_directory(destination)
    assert models.validate_model_directory(destination)
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS no-replace publication")
def test_process_kill_leaves_only_unpublished_owned_staging(
    tmp_path: Path, fixture_model: tuple[dict[str, bytes], FakeOpener]
) -> None:
    del fixture_model
    destination = tmp_path / "model"
    script = (
        "import os,sys,threading\n"
        "from pathlib import Path\n"
        "from satori.desktop.voice import models\n"
        "models._opener=lambda:None\n"
        "def partial(opener,descriptor,item,*args):\n"
        " fd=os.open(item.name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=descriptor)\n"
        " os.write(fd,b'partial-public-model-data');os.close(fd)\n"
        " print('STAGING_READY',flush=True)\n"
        " threading.Event().wait()\n"
        "models._download=partial\n"
        "models.setup_model_directory(Path(sys.argv[1]))\n"
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
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready
        assert process.stdout.readline() == b"STAGING_READY\n"
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
    assert not destination.exists()
    assert not models.validate_model_directory(destination)
    stages = list(tmp_path.iterdir())
    assert len(stages) == 1
    assert stages[0].name.startswith(".model.download-")
    assert stat.S_ISDIR(stages[0].lstat().st_mode)
    partial_files = list(stages[0].iterdir())
    assert len(partial_files) == 1
    partial_file = partial_files[0]
    partial = partial_file.read_bytes()
    models.setup_model_directory(destination)
    assert models.validate_model_directory(destination)
    assert partial_file.read_bytes() == partial
    assert set(tmp_path.iterdir()) == {destination, stages[0]}


def test_graceful_owned_sigterm_cleans_unpublished_staging(tmp_path: Path) -> None:
    destination = tmp_path / "model"
    script = (
        "import hashlib,os,sys,threading\n"
        "from satori.desktop.voice import models as m\n"
        "m.MODEL_FILES=(m.ModelFile('model.bin',4,hashlib.sha256(b'abcd').hexdigest()),)\n"
        "m._opener=lambda:None\n"
        "def partial(opener,descriptor,item,*args):\n"
        " fd=os.open(item.name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=descriptor)\n"
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
    events = [json.loads(line) for line in output.splitlines()]
    assert events[-1] == {"event": "error", "code": "model_setup_cancelled"}
    assert not any(event.get("ready") for event in events)
    assert list(tmp_path.iterdir()) == []


def test_main_outputs_only_content_free_progress_and_static_failure(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, opener = fixture_model
    opener.error = URLError("PRIVATE-URL-AND-TOKEN")
    assert models.main(["--model-directory", str(tmp_path / "model")]) == 1
    output = capsys.readouterr()
    events = [json.loads(line) for line in output.out.splitlines()]
    assert events[0]["event"] == "progress"
    assert events[-1] == {"event": "error", "code": "model_setup_failed"}
    assert "PRIVATE" not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err


def test_setup_rejects_an_oversized_manifest_before_network(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, opener = fixture_model
    monkeypatch.setattr(
        models,
        "MODEL_FILES",
        (models.ModelFile("model.bin", models.MAX_MODEL_BYTES + 1, "0" * 64),),
    )
    with pytest.raises(models.VoiceModelError, match="model_configuration_invalid"):
        models.setup_model_directory(tmp_path / "model")
    assert not opener.requests
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("state", ["valid", "missing", "corrupt"])
def test_check_only_validates_without_setup_network_or_creating_directories(
    tmp_path: Path,
    fixture_model: tuple[dict[str, bytes], FakeOpener],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
) -> None:
    payloads, _ = fixture_model
    destination = tmp_path / "absent-parent" / "model"
    if state != "missing":
        destination.parent.mkdir()
        write_model(destination, payloads)
        if state == "corrupt":
            (destination / "model.bin").write_bytes(b"x" * len(payloads["model.bin"]))
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Read-only integrity check attempted setup or network access")

    monkeypatch.setattr(models, "setup_model_directory", forbidden)
    monkeypatch.setattr(models, "_opener", forbidden)
    code = models.main(["--model-directory", str(destination), "--check-only"])
    output = capsys.readouterr()
    events = [json.loads(line) for line in output.out.splitlines()]
    assert not output.err
    if state == "valid":
        assert code == 0
        assert events == [{"event": "result", "ready": True}]
    else:
        assert code == 1
        assert events == [{"event": "error", "code": "model_integrity_failed"}]
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before
    if state == "corrupt":
        assert (destination / "model.bin").read_bytes() == b"x" * len(payloads["model.bin"])
