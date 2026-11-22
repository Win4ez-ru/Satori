"""One explicit source request over owned pipes; no UI, accounts, persistence or Core."""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from satori.infrastructure.public_sources.public_web import (
    PublicWebError,
    PublicWebResult,
    read_public_url,
)
from satori.infrastructure.public_sources.search import (
    Language,
    WikipediaSearchError,
    WikipediaSearchResult,
    search_wikipedia,
)
from satori.infrastructure.public_sources.selected_file import FileReadError, SelectedFileReader
from satori.infrastructure.public_sources.wire import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    Failure,
    PublicSourceReply,
    PublicSourceRequest,
    SearchItem,
)


@dataclass(frozen=True, slots=True)
class PublicSourceServices:
    """Concrete runtime defaults and a narrow source-free synthetic transport seam."""

    file_reader: Callable[[], SelectedFileReader] = SelectedFileReader
    web_reader: Callable[[str], PublicWebResult] = read_public_url
    wikipedia: Callable[[str, Language], WikipediaSearchResult] = search_wikipedia


def _base(request: PublicSourceRequest) -> dict[str, object]:
    return {
        "schema_version": 1,
        "untrusted": True,
        "request_id": request.request_id,
        "profile_id": request.profile_id,
        "session_id": request.session_id,
        "generation": request.generation,
        "operation": request.operation,
        "origin": {
            "file_read": "selected_file",
            "url_read": "public_https",
            "wikipedia_search": "wikipedia",
        }[request.operation],
        "coverage": {
            "file_read": "selected_file_only",
            "url_read": "selected_url_only",
            "wikipedia_search": "wikipedia_only",
        }[request.operation],
        "source_label": {
            "file_read": "Selected local file",
            "url_read": "Public HTTPS page",
            "wikipedia_search": "Wikipedia only",
        }[request.operation],
    }


def _failure(request: PublicSourceRequest, reason: Failure) -> PublicSourceReply:
    observed = datetime.now(UTC)
    status = (
        "unavailable"
        if reason in {"network_unavailable", "tls_unavailable", "timeout", "file_unavailable"}
        else "error"
        if reason == "internal_error"
        else "rejected"
    )
    return PublicSourceReply.model_validate(
        {
            **_base(request),
            "status": status,
            "reason": reason,
            "observed_at": observed,
            "expires_at": observed + timedelta(minutes=5),
        }
    )


def execute_request(
    request: PublicSourceRequest,
    services: PublicSourceServices | None = None,
) -> PublicSourceReply:
    """Run exactly the already admitted request; never route unknown text to a model."""
    services = services or PublicSourceServices()
    base = _base(request)
    try:
        if request.operation == "file_read":
            assert request.path is not None
            reader = services.file_reader()
            try:
                result = reader.read(reader.select(Path(request.path)))
            finally:
                reader.close()
            if result.display_name != Path(request.path).name:
                return _failure(request, "response_invalid")
            body = {
                "status": result.status,
                "text": result.text,
                "source_label": result.display_name,
                "media_type": result.media_type,
                "observed_at": result.observed_at,
                "expires_at": result.expires_at,
                "raw_bytes": result.raw_bytes,
            }
        elif request.operation == "url_read":
            assert request.url is not None
            page = services.web_reader(request.url)
            if page.url != request.url:
                return _failure(request, "response_invalid")
            body = {
                "status": "ok" if page.status == "complete" else page.status,
                "text": page.text,
                "source_url": page.url,
                "media_type": page.media_type,
                "observed_at": page.observed_at,
                "expires_at": page.expires_at,
                "raw_bytes": page.raw_bytes,
            }
        else:
            assert request.query is not None
            search = services.wikipedia(request.query, request.language or "ru")
            if search.provider != "wikipedia" or search.language != (request.language or "ru"):
                return _failure(request, "response_invalid")
            body = {
                "status": search.status,
                "source_label": f"Wikipedia ({search.language}) only",
                "items": tuple(
                    SearchItem(
                        title=item.title,
                        url=item.url,
                        excerpt=item.excerpt,
                        description=item.description,
                    )
                    for item in search.items
                ),
                "observed_at": search.observed_at,
                "expires_at": search.expires_at,
                "raw_bytes": search.raw_bytes,
            }
        return PublicSourceReply.model_validate({**base, **body})
    except FileReadError as error:
        return _failure(request, _file_reason(error.code))
    except PublicWebError as error:
        return _failure(request, _web_reason(error.code))
    except WikipediaSearchError:
        return _failure(request, "search_invalid")
    except Exception:
        # No traceback, raw exception, file path, query or page enters diagnostics.
        return _failure(request, "internal_error")


def _file_reason(code: str) -> Failure:
    mapping: dict[str, Failure] = {
        "unsupported_type": "file_unsupported",
        "file_unsupported": "file_unsupported",
        "file_changed": "file_changed",
        "identity_changed": "file_changed",
        "file_too_large": "file_too_large",
        "size_exceeded": "file_too_large",
        "invalid_text": "file_invalid_text",
        "content_invalid": "file_invalid_text",
        "file_unavailable": "file_unavailable",
        "file_not_found": "file_unavailable",
        "path_unavailable": "file_unavailable",
        "read_failed": "file_unavailable",
        "content_unsupported": "file_invalid_text",
        "read_timeout": "timeout",
        "cancelled": "cancelled",
        "platform_unavailable": "file_unavailable",
    }
    return mapping.get(code, "file_unsafe")


def _web_reason(code: str) -> Failure:
    mapping: dict[str, Failure] = {
        "url_invalid": "url_unsafe",
        "url_unsafe": "url_unsafe",
        "address_forbidden": "url_unsafe",
        "dns_unsafe": "url_unsafe",
        "destination_denied": "url_unsafe",
        "redirect_denied": "redirect_rejected",
        "redirect_rejected": "redirect_rejected",
        "content_type_unsupported": "content_unsupported",
        "encoding_unsupported": "content_unsupported",
        "media_unsupported": "content_unsupported",
        "content_invalid": "response_invalid",
        "response_too_large": "response_too_large",
        "body_too_large": "response_too_large",
        "response_invalid": "response_invalid",
        "timeout": "timeout",
        "cancelled": "cancelled",
        "dns_failed": "network_unavailable",
        "network_error": "network_unavailable",
        "transport_failed": "network_unavailable",
        "http_error": "network_unavailable",
        "tls_failed": "tls_unavailable",
        "tls_unavailable": "tls_unavailable",
    }
    return mapping.get(code, "source_rejected")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_key")
        value[key] = item
    return value


def run_stdio(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    services: PublicSourceServices | None = None,
) -> int:
    """Read one bounded JSON request to EOF, write one bounded JSON receipt, then exit.

    Parent closes stdin and owns a hard process watchdog (recommended 15 seconds).
    There is no ambient listener, reusable session, automatic read/retry or log file.
    """
    try:
        data = source.read(MAX_INPUT_BYTES + 1)
        if not data or len(data) > MAX_INPUT_BYTES:
            raise ValueError("input_size_invalid")
        # JSON duplicate fields cannot override correlation, resource or operation.
        json.loads(data, object_pairs_hook=_unique)
        request = PublicSourceRequest.model_validate_json(data)
    except (ValueError, ValidationError, RecursionError):
        destination.write(b'{"schema_version":1,"status":"rejected","reason":"invalid_request"}\n')
        destination.flush()
        return 2
    result = execute_request(request, services)
    encoded = result.model_dump_json().encode("utf-8") + b"\n"
    if len(encoded) > MAX_OUTPUT_BYTES:
        result = _failure(request, "response_too_large")
        encoded = result.model_dump_json().encode("utf-8") + b"\n"
    destination.write(encoded)
    destination.flush()
    return 0 if result.status in {"ok", "empty", "partial"} else 1


def main() -> int:
    return run_stdio(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
