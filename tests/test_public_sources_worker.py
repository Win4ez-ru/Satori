"""One-shot private helper envelopes with synthetic sources and owned test files only."""

import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from satori.infrastructure.public_sources.public_web import PublicWebError, PublicWebResult
from satori.infrastructure.public_sources.search import search_wikipedia
from satori.infrastructure.public_sources.wire import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    PublicSourceReply,
    PublicSourceRequest,
)
from satori.infrastructure.public_sources.worker import (
    PublicSourceServices,
    execute_request,
    run_stdio,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def request(operation: str = "url_read", **changes: object) -> dict[str, object]:
    arguments = {
        "file_read": {"path": "/private/synthetic-only.txt"},
        "url_read": {"url": "https://public.example.invalid/"},
        "wikipedia_search": {"query": "PUBLIC PLANET", "language": "ru"},
    }
    return {
        "schema_version": 1,
        "request_id": str(uuid4()),
        "profile_id": str(uuid4()),
        "session_id": str(uuid4()),
        "generation": 2,
        "operation": operation,
        **arguments.get(operation, {}),
        **changes,
    }


def page(url: str, *, text: str = "PUBLIC SOURCE TEXT") -> PublicWebResult:
    return PublicWebResult(
        url=url,
        text=text,
        media_type="text/plain",
        status="complete" if text else "empty",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        raw_bytes=len(text.encode()),
    )


def invoke(
    body: object, services: PublicSourceServices | None = None
) -> tuple[int, dict[str, Any]]:
    output = io.BytesIO()
    result = run_stdio(io.BytesIO(json.dumps(body).encode()), output, services=services)
    assert len(output.getvalue()) <= MAX_OUTPUT_BYTES
    return result, json.loads(output.getvalue())


def test_exact_web_request_correlation_provenance_and_one_response_without_ambient_io() -> None:
    calls: list[str] = []

    def reader(url: str) -> PublicWebResult:
        calls.append(url)
        return page(url)

    body = request()
    code, result = invoke(body, PublicSourceServices(web_reader=reader))
    assert code == 0
    assert calls == [body["url"]]
    for key in ("request_id", "profile_id", "session_id", "generation", "operation"):
        assert result[key] == body[key]
    assert result["origin"] == "public_https"
    assert result["coverage"] == "selected_url_only"
    assert result["untrusted"] is True
    assert result["text"] == "PUBLIC SOURCE TEXT"
    assert result["status"] == "ok"
    assert "PUBLIC SOURCE TEXT" not in repr(
        PublicSourceReply.model_validate_json(json.dumps(result))
    )


@pytest.mark.parametrize("status", ["complete", "partial", "empty"])
def test_source_complete_partial_and_empty_are_not_collapsed(status: Any) -> None:
    body = request()

    def reader(url: str) -> PublicWebResult:
        return replace(page(url, text="" if status == "empty" else "PUBLIC"), status=status)

    code, result = invoke(body, PublicSourceServices(web_reader=reader))
    assert code == 0
    assert result["status"] == ("ok" if status == "complete" else status)


@pytest.mark.parametrize(
    ("reason", "wire_reason", "status"),
    [
        ("destination_denied", "url_unsafe", "rejected"),
        ("redirect_denied", "redirect_rejected", "rejected"),
        ("media_unsupported", "content_unsupported", "rejected"),
        ("encoding_unsupported", "content_unsupported", "rejected"),
        ("response_too_large", "response_too_large", "rejected"),
        ("timeout", "timeout", "unavailable"),
        ("tls_failed", "tls_unavailable", "unavailable"),
        ("transport_failed", "network_unavailable", "unavailable"),
        ("cancelled", "cancelled", "rejected"),
    ],
)
def test_closed_negative_receipts_never_contain_remote_content_or_look_empty(
    reason: Any, wire_reason: str, status: str
) -> None:
    def reader(url: str) -> PublicWebResult:
        del url
        raise PublicWebError(reason)

    code, result = invoke(request(), PublicSourceServices(web_reader=reader))
    assert code == 1
    assert result["status"] == status
    assert result["reason"] == wire_reason
    assert result["text"] is None
    assert result["source_url"] is None
    assert result["items"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"generation": True},
        {"generation": -1},
        {"request_id": "00000000-0000-0000-0000-000000000000"},
        {"request_id": "bad"},
        {"profile_id": "bad"},
        {"session_id": "bad"},
        {"operation": "send_email"},
        {"path": "/private/OTHER.txt"},
        {"query": "EXTRA"},
        {"language": "en"},
        {"url": "https://PUBLIC/\nsecret"},
        {"token": "UNADMITTED SECRET"},
    ],
)
def test_invalid_wire_does_not_call_any_source_or_echo_sensitive_input(
    changes: dict[str, object],
) -> None:
    calls: list[str] = []

    def reader(url: str) -> PublicWebResult:
        calls.append(url)
        return page(url)

    code, result = invoke({**request(), **changes}, PublicSourceServices(web_reader=reader))
    assert code == 2
    assert result == {"schema_version": 1, "status": "rejected", "reason": "invalid_request"}
    assert calls == []
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("data", [b"", b"[", b"{} {}", b"\xff", b" " * (MAX_INPUT_BYTES + 1)])
def test_malformed_bounded_input_is_rejected(data: bytes) -> None:
    output = io.BytesIO()
    assert run_stdio(io.BytesIO(data), output) == 2
    assert json.loads(output.getvalue())["reason"] == "invalid_request"


def test_duplicate_request_keys_cannot_replace_selected_resource() -> None:
    body = json.dumps(request())[:-1] + ',"url":"https://127.0.0.1/"}'
    output = io.BytesIO()
    assert run_stdio(io.BytesIO(body.encode()), output) == 2
    assert "127.0.0.1" not in output.getvalue().decode()


def test_unexpected_adapter_exception_never_exposes_exception_payload() -> None:
    def reader(url: str) -> PublicWebResult:
        raise RuntimeError(f"UNADMITTED SECRET {url}")

    code, result = invoke(request(), PublicSourceServices(web_reader=reader))
    assert code == 1
    assert result["reason"] == "internal_error"
    assert "UNADMITTED" not in json.dumps(result)


def test_explicit_selected_file_has_one_source_only_and_private_path_is_not_in_receipt(
    tmp_path: Path,
) -> None:
    selected = tmp_path.resolve() / "PUBLIC selected.txt"
    selected.write_text("PUBLIC SELECTED CONTENT")
    code, result = invoke(request("file_read", path=str(selected)))
    assert code == 0
    assert result["origin"] == "selected_file"
    assert result["coverage"] == "selected_file_only"
    assert result["source_label"] == selected.name
    assert result["text"] == "PUBLIC SELECTED CONTENT"
    assert str(selected.parent) not in json.dumps(result)
    assert result["source_url"] is None


def test_unsupported_document_gets_honest_negative_receipt_and_is_not_opened(
    tmp_path: Path,
) -> None:
    selected = tmp_path.resolve() / "PUBLIC.pdf"
    selected.write_bytes(b"%PDF-PUBLIC SYNTHETIC")
    code, result = invoke(request("file_read", path=str(selected)))
    assert code == 1
    assert result["reason"] == "file_unsupported"
    assert result["text"] is None


def test_wikipedia_receipt_labels_coverage_and_never_claims_general_web_or_loads_thumbnail() -> (
    None
):
    calls: list[str] = []

    def reader(url: str) -> PublicWebResult:
        calls.append(url)
        payload = {
            "pages": [
                {
                    "id": 1,
                    "key": "PUBLIC",
                    "title": "PUBLIC TITLE",
                    "excerpt": "PUBLIC EXCERPT",
                    "description": None,
                    "thumbnail": {"url": "https://private/"},
                }
            ]
        }
        return replace(page(url, text=json.dumps(payload)), media_type="application/json")

    def search(query: str, language: Any) -> Any:
        return search_wikipedia(query, language, reader=reader)

    code, result = invoke(request("wikipedia_search"), PublicSourceServices(wikipedia=search))
    assert code == 0
    assert len(calls) == 1
    assert result["origin"] == "wikipedia"
    assert result["coverage"] == "wikipedia_only"
    assert result["source_label"] == "Wikipedia (ru) only"
    assert result["items"][0]["title"] == "PUBLIC TITLE"
    assert "thumbnail" not in json.dumps(result)


def test_response_cannot_mislabel_failure_as_empty_or_mix_source_content() -> None:
    body = PublicSourceRequest.model_validate_json(json.dumps(request()))
    valid = execute_request(body, PublicSourceServices(web_reader=page))
    updates: list[dict[str, object]] = [
        {"status": "empty"},
        {"status": "error"},
        {"reason": "timeout"},
        {"coverage": "wikipedia_only"},
        {"origin": "wikipedia"},
        {"expires_at": NOW + timedelta(hours=1)},
        {"observed_at": NOW.replace(tzinfo=None)},
    ]
    for update in updates:
        with pytest.raises(ValidationError):
            PublicSourceReply.model_validate({**valid.model_dump(), **update})


def test_real_owned_python_helper_has_single_frame_no_source_logging_and_no_network(
    tmp_path: Path,
) -> None:
    selected = tmp_path.resolve() / "PUBLIC.txt"
    selected.write_text("PUBLIC CHILD CONTENT")
    import satori
    import satori.infrastructure.public_sources as package

    launcher = tmp_path / "synthetic_launcher.py"
    launcher.write_text(
        "import sys\n"
        "import satori.infrastructure\n"
        f"satori.infrastructure.__path__.insert(0,{str(Path(package.__file__).parent.parent)!r})\n"
        "from satori.infrastructure.public_sources.worker import main\n"
        "raise SystemExit(main())\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(satori.__file__).resolve().parents[1]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, str(launcher)],
        input=json.dumps(request("file_read", path=str(selected))).encode(),
        capture_output=True,
        timeout=5,
        env=env,
    )
    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.count(b"\n") == 1
    assert json.loads(result.stdout)["text"] == "PUBLIC CHILD CONTENT"
    assert len(result.stdout) <= MAX_OUTPUT_BYTES
