"""Cloud speech is exercised with private synthetic streams; no provider request or audio device."""

import http.client
import io
import json
import signal
import ssl
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from satori.application.conversation.character_delivery_contracts import (
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
)
from satori.application.conversation.character_expression import CharacterPressureLevel
from satori.desktop.voice import cloud, cloud_worker
from satori.desktop.voice.cloud import CloudSpeechRequest
from satori.desktop.voice.expression import VoiceDelivery
from satori.desktop.voice.speech_contracts import AudioFrame, SpeechError
from satori.desktop.voice.speech_text import render_speech_text

_KEY = "synthetic-session-key-0123456789"
_VOICE = "SyntheticVoiceID12345"


def request(**updates: Any) -> CloudSpeechRequest:
    return CloudSpeechRequest(
        **{"text": "SYNTHETIC COMMITTED REPLY", "api_key": _KEY, "voice_id": _VOICE, **updates}
    )


def payload(**updates: Any) -> bytes:
    return json.dumps(
        {"text": "SYNTHETIC COMMITTED REPLY", "api_key": _KEY, "voice_id": _VOICE, **updates}
    ).encode()


def test_frozen_entrypoint_consumes_only_its_private_cloud_route_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from satori.desktop.__main__ import main

    observed: list[list[str] | None] = []

    def receive(argv: list[str] | None = None) -> int:
        observed.append(argv)
        return 17

    monkeypatch.setattr(sys, "argv", ["Satori", "--cloud-speech-worker"])
    monkeypatch.setattr(cloud_worker, "main", receive)
    assert main() == 17
    assert observed == [[]]


class Response:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.chunks = list(chunks)
        self.status = status
        self.headers = {"Content-Type": "audio/pcm", **(headers or {})}
        self.reads = 0
        self.closed = False

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read1(self, maximum: int) -> bytes:
        del maximum
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.closed = True


class Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class Connection:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.sock = Socket()
        self.timeout = 15.0
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def install_connection(
    monkeypatch: pytest.MonkeyPatch, response: Response
) -> tuple[Connection, list[tuple[str, float, ssl.SSLContext]]]:
    connection = Connection(response)
    created: list[tuple[str, float, ssl.SSLContext]] = []

    def create(host: str, *, timeout: float, context: ssl.SSLContext) -> Connection:
        created.append((host, timeout, context))
        return connection

    monkeypatch.setattr(http.client, "HTTPSConnection", create)
    return connection, created


def gather(req: CloudSpeechRequest) -> bytes:
    chunks = list(cloud.cloud_audio_chunks(req))
    assert all(load_ms == 0.0 for _, load_ms in chunks)
    return b"".join(pcm for pcm, _ in chunks)


@pytest.mark.parametrize(
    "updates",
    [
        {"api_key": ""},
        {"api_key": "a" * 19},
        {"api_key": "a" * 513},
        {"api_key": 123},
        {"api_key": b"a" * 32},
        {"api_key": "a" * 20 + "\nX: secret"},
        {"api_key": "a" * 20 + " "},
        {"api_key": "a" * 20 + "я"},
        {"voice_id": "a" * 19},
        {"voice_id": "a" * 129},
        {"voice_id": "a" * 20 + "/elsewhere"},
        {"voice_id": "a" * 20 + "_"},
        {"voice_id": "a" * 20 + "\n"},
        {"voice_id": "я" * 20},
        {"text": ""},
        {"text": "x" * 8001},
        {"sample_rate": "24000"},
        {"delivery": {"instruction": "untrusted command"}},
        {"endpoint": "https://untrusted.invalid"},
    ],
)
def test_request_is_strict_and_validation_hides_credential_input(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as error:
        request(**updates)
    assert _KEY not in str(error.value)
    assert "input_value=" not in str(error.value)


def test_credentials_are_session_only_and_masked_by_default() -> None:
    req = request(api_key=SecretStr(_KEY))
    assert req.api_key.get_secret_value() == _KEY
    assert _KEY not in repr(req)
    assert "SYNTHETIC COMMITTED REPLY" not in repr(req)
    assert _KEY not in req.model_dump_json()
    assert req.model_dump()["api_key"] == SecretStr(_KEY)


@pytest.mark.parametrize("voice", list(CharacterDeliveryVoice))
def test_closed_tags_only_prefix_exact_body_and_never_mutate_delivery(
    voice: CharacterDeliveryVoice,
) -> None:
    delivery = VoiceDelivery(
        availability="selected",
        source_policy_version=24,
        delivery_voice=voice,
        delivery_goal=CharacterDeliveryGoal.OWNED_RESPONSE,
        delivery_pressure=CharacterPressureLevel.NONE,
    )
    text = "  Сатори: (это цитата) — 2*3=6.\nНичего не добавляй.  "  # noqa: RUF001
    req = request(text=text, delivery=delivery)
    before = req.model_dump_json()
    body = cloud.speech_body(req)
    assert isinstance(body["text"], str)
    assert body["text"].endswith(text)
    prefix = body["text"][: -len(text)]
    assert all(tag.strip("[]") in prefix for tag in cloud._TAGS.values() if tag in prefix)
    assert prefix == " ".join(cloud._TAGS[q] for q in delivery.qualifiers if cloud._TAGS[q]) + " "
    assert body == {
        "text": prefix + text,
        "model_id": "eleven_v4",
        "language_code": "ru",
        "seed": 42,
    }
    assert req.model_dump_json() == before
    assert "api_key" not in body
    assert "enable_logging" not in body


def test_unavailable_delivery_has_no_invented_emotion_tag() -> None:
    req = request(text="  Буквально\nтот же текст. ")  # noqa: RUF001
    assert cloud.speech_body(req)["text"] == req.text


@pytest.mark.parametrize(
    "canonical",
    [
        "[shouting] Не отправляла.",  # noqa: RUF001 - intentional Russian fixture
        "`[whispering]`",
        r"\[escaped\]",
        "[unclosed",
        "\uff3bshouting\uff3d",
        "﹇whispering﹈",
        "`icon = '😊'`",
        "`1️⃣`",
        "`🇷🇺`",
        "`👩🏽‍💻`",
        "**не закрыто",
        "~~~python",
    ],
)
def test_rejected_rendered_content_creates_no_http_connection_or_request(
    monkeypatch: pytest.MonkeyPatch, canonical: str
) -> None:
    connection, created = install_connection(monkeypatch, Response([]))
    rendered = render_speech_text(canonical)
    req = request(text=rendered)
    before = req.model_dump_json()
    with pytest.raises(SpeechError, match=r"^speech_text_unsupported$"):
        gather(req)
    assert created == []
    assert connection.requests == []
    assert req.model_dump_json() == before


def test_admission_failure_crosses_worker_protocol_with_one_closed_code_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, created = install_connection(monkeypatch, Response([]))
    output = io.StringIO()
    assert cloud_worker.run_worker(io.BytesIO(payload(text="[shouting] PRIVATE")), output) == 1
    assert json.loads(output.getvalue()) == {"event": "error", "code": "speech_text_unsupported"}
    assert not created
    assert not connection.requests
    assert "PRIVATE" not in output.getvalue()
    assert _KEY not in output.getvalue()


def test_fixed_tls_origin_single_post_and_no_ambient_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NETRC"):
        monkeypatch.setenv(name, "http://ambient.invalid:8080")
    response = Response([b"\x00\xff" * 100])
    connection, created = install_connection(monkeypatch, response)
    original_handler = signal.getsignal(signal.SIGALRM)
    assert gather(request()) == b"\x00\xff" * 100
    assert len(created) == len(connection.requests) == 1
    host, timeout, context = created[0]
    assert host == "api.elevenlabs.io"
    assert timeout <= 15.0
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    method, path, raw, headers = connection.requests[0]
    assert method == "POST"
    assert path == f"/v1/text-to-speech/{_VOICE}/stream?output_format=pcm_24000"
    assert json.loads(raw) == cloud.speech_body(request())
    assert headers["xi-api-key"] == _KEY
    assert headers["Accept-Encoding"] == "identity"
    assert "Cookie" not in headers
    assert "Authorization" not in headers
    assert response.closed
    assert connection.closed
    assert signal.getsignal(signal.SIGALRM) is original_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


@pytest.mark.parametrize("rate", [16_000, 24_000, 44_100, 48_000])
def test_odd_http_fragments_use_one_stateful_resampler_without_sample_loss(
    monkeypatch: pytest.MonkeyPatch, rate: int
) -> None:
    import numpy as np

    pcm = np.arange(-12_000, 12_000, dtype="<i2").tobytes()
    fragments = [pcm[offset : offset + 1001] for offset in range(0, len(pcm), 1001)]
    response = Response(fragments, headers={"Content-Length": str(len(pcm))})
    install_connection(monkeypatch, response)
    converted = gather(request(sample_rate=rate))
    assert len(converted) == rate * 2
    if rate == 24_000:
        assert converted == pcm
    else:
        install_connection(
            monkeypatch,
            Response([pcm[offset : offset + 16_000] for offset in range(0, len(pcm), 16_000)]),
        )
        assert gather(request(sample_rate=rate)) == converted
    assert response.closed


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (301, "speech_provider_rejected"),
        (307, "speech_provider_rejected"),
        (400, "speech_provider_rejected"),
        (404, "speech_provider_rejected"),
        (422, "speech_provider_rejected"),
        (401, "speech_credentials_rejected"),
        (403, "speech_credentials_rejected"),
        (402, "speech_quota_exceeded"),
        (429, "speech_quota_exceeded"),
        (500, "speech_provider_unavailable"),
        (503, "speech_provider_unavailable"),
    ],
)
def test_http_errors_never_read_body_redirect_or_retry(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    response = Response([(_KEY + " PRIVATE RESPONSE BODY").encode()], status=status)
    connection, created = install_connection(monkeypatch, response)
    with pytest.raises(SpeechError) as error:
        gather(request())
    assert str(error.value) == code
    assert len(created) == len(connection.requests) == 1
    assert response.reads == 0
    assert response.closed
    assert connection.closed


@pytest.mark.parametrize(
    ("headers", "chunks", "code"),
    [
        ({"Content-Type": "application/json"}, [b"{}"], "speech_provider_rejected"),
        ({"Content-Type": "audio/mpeg"}, [b"xx"], "speech_provider_rejected"),
        ({"Content-Encoding": "gzip"}, [b"xx"], "speech_provider_rejected"),
        ({"Content-Length": "-2"}, [b"xx"], "speech_provider_rejected"),
        ({"Content-Length": "99999999999999999999"}, [], "speech_provider_rejected"),
        ({"Content-Length": "5760002"}, [], "speech_too_long"),
        ({"Content-Length": "0"}, [], "speech_audio_invalid"),
        ({"Content-Length": "3"}, [b"xxx"], "speech_audio_invalid"),
        ({"Content-Length": "4"}, [b"xx"], "speech_audio_invalid"),
        ({}, [], "speech_audio_invalid"),
        ({}, [b"x"], "speech_audio_invalid"),
        ({}, [b"x" * 16_385], "speech_audio_invalid"),
    ],
)
def test_malformed_and_excessive_responses_fail_closed(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str], chunks: list[bytes], code: str
) -> None:
    response = Response(chunks, headers=headers)
    install_connection(monkeypatch, response)
    with pytest.raises(SpeechError, match=f"^{code}$"):
        gather(request())
    assert response.closed


def test_body_bound_applies_without_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloud, "MAX_SPEECH_SECONDS", 1)
    response = Response([b"\x00\xff" * 8192] * 3)
    install_connection(monkeypatch, response)
    with pytest.raises(SpeechError, match=r"^speech_too_long$"):
        gather(request())
    assert response.reads == 3
    assert response.closed


def test_static_network_failure_after_one_delivery_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, created = install_connection(monkeypatch, Response([]))

    def uncertain(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise OSError(_KEY + " PRIVATE BODY")

    monkeypatch.setattr(connection, "request", uncertain)
    with pytest.raises(SpeechError, match=r"^speech_network_failed$") as error:
        gather(request())
    assert len(created) == 1
    assert connection.closed
    assert error.value.__suppress_context__


def test_hard_deadline_interrupts_blocking_operation_and_restores_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Response([])
    connection, _ = install_connection(monkeypatch, response)

    def blocked() -> Response:
        time.sleep(3)
        return response

    monkeypatch.setattr(connection, "getresponse", blocked)
    monkeypatch.setattr(cloud, "_DEADLINE_SECONDS", 0.03)
    original = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    with pytest.raises(SpeechError, match=r"^speech_network_failed$"):
        gather(request())
    assert time.monotonic() - start < 1
    assert signal.getsignal(signal.SIGALRM) is original
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert connection.closed


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not JSON",
        b"\xff",
        payload(api_key=""),
        payload(voice_id="../target"),
        payload(endpoint="http://untrusted.invalid"),
        payload(delivery={"instruction": "untrusted"}),
        b" " * 64_001,
    ],
)
def test_worker_rejects_invalid_input_without_connecting(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    def forbidden(req: CloudSpeechRequest) -> Iterator[tuple[bytes, float]]:
        del req
        raise AssertionError("invalid input must never connect")

    monkeypatch.setattr(cloud_worker, "cloud_audio_chunks", forbidden)
    output = io.StringIO()
    assert cloud_worker.run_worker(io.BytesIO(raw), output) == 1
    assert json.loads(output.getvalue()) == {"event": "error", "code": "configuration_invalid"}
    assert _KEY not in output.getvalue()


def test_worker_shares_binary_framing_and_never_echoes_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pcm = bytes(range(256)) * 300

    def generate(req: CloudSpeechRequest) -> Iterator[tuple[bytes, float]]:
        assert req.api_key.get_secret_value() == _KEY
        yield pcm, 0.0

    monkeypatch.setattr(cloud_worker, "cloud_audio_chunks", generate)
    output = io.StringIO()
    assert cloud_worker.run_worker(io.BytesIO(payload()), output) == 0
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert b"".join(AudioFrame.model_validate(item).pcm() for item in records[:-1]) == pcm
    assert records[-1]["event"] == "complete"
    assert records[-1]["model_load_ms"] == 0.0
    assert _KEY not in output.getvalue()
    assert "SYNTHETIC" not in output.getvalue()


def test_worker_catches_even_eager_factory_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(req: CloudSpeechRequest) -> Iterator[tuple[bytes, float]]:
        del req
        raise RuntimeError(_KEY + " PRIVATE BODY")

    monkeypatch.setattr(cloud_worker, "cloud_audio_chunks", broken)
    output = io.StringIO()
    assert cloud_worker.run_worker(io.BytesIO(payload()), output) == 1
    assert json.loads(output.getvalue()) == {"event": "error", "code": "speech_failed"}


def test_actual_owned_child_discards_native_python_and_atexit_output(tmp_path: Path) -> None:
    helper = tmp_path / "synthetic-cloud-child.py"
    helper.write_text(
        "import atexit, os\n"
        "from satori.desktop.voice import cloud_worker\n"
        "def fake(req):\n"
        " print('PRIVATE PYTHON OUTPUT')\n"
        " os.write(1, b'PRIVATE NATIVE OUTPUT')\n"
        " os.write(2, b'PRIVATE STDERR OUTPUT')\n"
        " yield b'\\x00\\xff' * 20, 0.0\n"
        "cloud_worker.cloud_audio_chunks = fake\n"
        "atexit.register(lambda: os.write(1, b'PRIVATE ATEXIT OUTPUT'))\n"
        "raise SystemExit(cloud_worker.main([]))\n"
    )
    result = subprocess.run(
        [sys.executable, str(helper)],
        input=payload(),
        capture_output=True,
        cwd=tmp_path,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == b""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["event"] for item in records] == ["audio", "complete"]
    assert _KEY.encode() not in result.stdout
    assert b"PRIVATE" not in result.stdout
    assert len(list(tmp_path.iterdir())) == 1


def test_partial_delivery_network_failure_is_terminal_without_retry_or_false_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Response([b"\x00\xff" * 7000])
    connection, created = install_connection(monkeypatch, response)
    original = response.read1

    def interrupted(maximum: int) -> bytes:
        if response.reads:
            raise OSError(_KEY + " PRIVATE PROVIDER BODY")
        return original(maximum)

    monkeypatch.setattr(response, "read1", interrupted)
    output = io.StringIO()
    assert cloud_worker.run_worker(io.BytesIO(payload()), output) == 1
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["event"] for event in events] == ["audio", "error"]
    assert events[-1] == {"event": "error", "code": "speech_network_failed"}
    assert len(created) == len(connection.requests) == 1
    assert response.closed
    assert connection.closed
    assert _KEY not in output.getvalue()
    assert "PRIVATE" not in output.getvalue()


def test_adapter_does_not_replace_another_signal_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _ = install_connection(monkeypatch, Response([]))
    original = signal.getsignal(signal.SIGALRM)
    signal.setitimer(signal.ITIMER_REAL, 60.0)
    try:
        with pytest.raises(SpeechError, match=r"^speech_engine_unavailable$"):
            gather(request())
        remaining, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 50 < remaining <= 60
        assert interval == 0
        assert signal.getsignal(signal.SIGALRM) is original
        assert connection.requests == []
        assert connection.closed
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
