"""Synthetic public Wikipedia search responses; never a live service or user query."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from satori.infrastructure.public_sources.public_web import PublicWebResult
from satori.infrastructure.public_sources.search import (
    WikipediaSearchError,
    duckduckgo_browser_url,
    search_wikipedia,
    wikipedia_search_url,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def response(url: str, payload: object) -> PublicWebResult:
    text = json.dumps(payload)
    return PublicWebResult(
        url=url,
        text=text,
        media_type="application/json",
        status="complete",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        raw_bytes=len(text.encode()),
    )


def page(**changes: object) -> dict[str, object]:
    return {
        "id": 100,
        "key": "Synthetic_public_page",
        "title": "PUBLIC TITLE",
        "excerpt": 'A <span class="searchmatch">public</span> excerpt.',
        "description": "PUBLIC DESCRIPTION",
        "thumbnail": {"url": "https://127.0.0.1/private"},
        **changes,
    }


@pytest.mark.parametrize("language", ["ru", "en"])
def test_search_is_one_fixed_origin_request_and_plain_wikipedia_only_result(language: Any) -> None:
    calls: list[str] = []

    def reader(url: str) -> PublicWebResult:
        calls.append(url)
        return response(url, {"pages": [page()]})

    result = search_wikipedia("PUBLIC PLANET", language, reader=reader)
    assert len(calls) == 1
    parsed = urlsplit(calls[0])
    assert parsed.netloc == f"{language}.wikipedia.org"
    assert parsed.path == "/w/rest.php/v1/search/page"
    assert parse_qs(parsed.query) == {"q": ["PUBLIC PLANET"], "limit": ["5"]}
    assert result.provider == "wikipedia"
    assert result.language == language
    assert result.status == "ok"
    assert "not general web" in result.coverage
    assert result.items[0].excerpt == "A public excerpt."
    assert result.items[0].url == f"https://{language}.wikipedia.org/wiki/Synthetic_public_page"
    assert result.observed_at == NOW
    assert result.expires_at == NOW + timedelta(minutes=5)
    assert "PUBLIC TITLE" not in repr(result)
    assert "PUBLIC DESCRIPTION" not in repr(result.items[0])
    assert "127.0.0.1" not in repr(result)


def test_empty_is_only_a_successful_complete_empty_wikipedia_result() -> None:
    result = search_wikipedia("PUBLIC", reader=lambda url: response(url, {"pages": []}))
    assert result.status == "empty"
    assert result.items == ()
    assert result.provider == "wikipedia"


@pytest.mark.parametrize(
    "query", ["", " leading", "trailing ", "bad\nquery", "x" * 301, "x\x00y", "x\u202ey"]
)
def test_bad_query_is_rejected_before_transport(query: str) -> None:
    calls: list[str] = []

    def reader(url: str) -> PublicWebResult:
        calls.append(url)
        return response(url, {"pages": []})

    with pytest.raises(WikipediaSearchError, match=r"^query_invalid$"):
        search_wikipedia(query, reader=reader)
    assert calls == []


def test_unsupported_language_does_not_choose_an_arbitrary_origin() -> None:
    with pytest.raises(WikipediaSearchError, match=r"^language_unsupported$"):
        wikipedia_search_url("PUBLIC", "localhost")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"pages": None},
        {"pages": {}},
        {"pages": [None]},
        {"pages": [page(id=True)]},
        {"pages": [page(id=0)]},
        {"pages": [page(key="")]},
        {"pages": [page(key="bad\x00key")]},
        {"pages": [page(title=12)]},
        {"pages": [page(title="")]},
        {"pages": [page(excerpt=None)]},
        {"pages": [page(description=[])]},
        {"pages": [page(), page()]},
        {"pages": [page(id=n + 1, key=f"public{n}") for n in range(6)]},
    ],
)
def test_malformed_or_excessive_results_fail_closed(payload: object) -> None:
    with pytest.raises(WikipediaSearchError, match=r"^search_response_invalid$"):
        search_wikipedia("PUBLIC", reader=lambda url: response(url, payload))


@pytest.mark.parametrize(
    "change",
    [{"status": "partial"}, {"media_type": "text/html"}, {"url": "https://other.invalid/"}],
)
def test_partial_wrong_origin_and_wrong_content_type_are_not_search_success(
    change: dict[str, Any],
) -> None:
    def reader(url: str) -> PublicWebResult:
        return replace(response(url, {"pages": [page()]}), **change)

    with pytest.raises(WikipediaSearchError, match=r"^search_response_invalid$"):
        search_wikipedia("PUBLIC", reader=reader)


@pytest.mark.parametrize(
    "body", ['{"pages":[],"pages":[]}', '{"pages":[{"id":1,"id":2}]}', "[", "NaN"]
)
def test_duplicate_json_keys_and_invalid_json_are_not_admitted(body: str) -> None:
    def reader(url: str) -> PublicWebResult:
        return replace(response(url, {}), text=body)

    with pytest.raises(WikipediaSearchError, match=r"^search_response_invalid$"):
        search_wikipedia("PUBLIC", reader=reader)


def test_remote_links_and_script_text_cannot_create_extra_requests_or_change_hit_origin() -> None:
    calls: list[str] = []

    def reader(url: str) -> PublicWebResult:
        calls.append(url)
        return response(
            url,
            {
                "pages": [
                    page(
                        key="//127.0.0.1/?secret=1",
                        excerpt='<script>SECRET</script><img src="https://private/">PUBLIC',
                    )
                ]
            },
        )

    result = search_wikipedia("PUBLIC", reader=reader)
    assert len(calls) == 1
    assert result.items[0].excerpt == "PUBLIC"
    assert urlsplit(result.items[0].url).netloc == "ru.wikipedia.org"
    assert "?secret=" not in result.items[0].url


def test_duckduckgo_fallback_only_builds_target_and_claims_no_open_or_read() -> None:
    url = duckduckgo_browser_url("PUBLIC PLANET")
    assert urlsplit(url).netloc == "duckduckgo.com"
    assert parse_qs(urlsplit(url).query) == {"q": ["PUBLIC PLANET"]}
    assert isinstance(url, str)
