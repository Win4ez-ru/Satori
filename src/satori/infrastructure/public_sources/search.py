"""A narrow Wikipedia search adapter; no general-web or browser-result claims."""

import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import quote, urlencode

from satori.infrastructure.public_sources.public_web import PublicWebResult, read_public_url

MAX_QUERY_CHARACTERS = 300
MAX_SEARCH_ITEMS = 5
MAX_SNIPPET_CHARACTERS = 1200
Language = Literal["ru", "en"]


class WikipediaSearchError(ValueError):
    """A closed failure, containing no query, remote exception or response text."""

    def __init__(
        self, code: Literal["query_invalid", "language_unsupported", "search_response_invalid"]
    ):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WikipediaSearchItem:
    title: str = field(repr=False)
    url: str = field(repr=False)
    excerpt: str = field(repr=False)
    description: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class WikipediaSearchResult:
    provider: Literal["wikipedia"]
    language: Language
    status: Literal["ok", "empty"]
    items: tuple[WikipediaSearchItem, ...] = field(repr=False)
    observed_at: datetime
    expires_at: datetime
    raw_bytes: int
    coverage: str = "Wikipedia only; first five matching pages, not general web coverage"


class _Snippet(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "template", "noscript", "head"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template", "noscript", "head"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _plain(value: object, maximum: int, *, html: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum * 8:
        raise WikipediaSearchError("search_response_invalid")
    if html:
        parser = _Snippet()
        parser.feed(value)
        parser.close()
        value = " ".join(parser.parts)
    # Explicitly discard remote control/format characters; never interpret formatting.
    return " ".join(
        "".join(
            char if not unicodedata.category(char).startswith("C") else " " for char in value
        ).split()
    )[:maximum]


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WikipediaSearchError("search_response_invalid")
        result[key] = value
    return result


def wikipedia_search_url(query: str, language: Language = "ru") -> str:
    """Build the sole fixed-origin GET endpoint from the explicit user query."""
    if (
        type(query) is not str
        or not 1 <= len(query) <= MAX_QUERY_CHARACTERS
        or query.strip() != query
        or any(unicodedata.category(char).startswith("C") for char in query)
    ):
        raise WikipediaSearchError("query_invalid")
    if type(language) is not str or language not in {"ru", "en"}:
        raise WikipediaSearchError("language_unsupported")
    return f"https://{language}.wikipedia.org/w/rest.php/v1/search/page?" + urlencode(
        {"q": query, "limit": MAX_SEARCH_ITEMS}
    )


def search_wikipedia(
    query: str,
    language: Language = "ru",
    *,
    reader: Callable[[str], PublicWebResult] = read_public_url,
) -> WikipediaSearchResult:
    """One explicitly requested public lookup, with no extra fetch or thumbnail load."""
    url = wikipedia_search_url(query, language)
    response = reader(url)
    if (
        response.url != url
        or response.media_type != "application/json"
        or response.status != "complete"
    ):
        raise WikipediaSearchError("search_response_invalid")
    try:
        payload = json.loads(response.text, object_pairs_hook=_object)
    except (ValueError, RecursionError):
        raise WikipediaSearchError("search_response_invalid") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
        raise WikipediaSearchError("search_response_invalid")
    pages = payload["pages"]
    if len(pages) > MAX_SEARCH_ITEMS:
        raise WikipediaSearchError("search_response_invalid")
    items: list[WikipediaSearchItem] = []
    keys: set[str] = set()
    for page in pages:
        if not isinstance(page, dict) or type(page.get("id")) is not int or page["id"] <= 0:
            raise WikipediaSearchError("search_response_invalid")
        key = page.get("key")
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 512
            or key in keys
            or any(unicodedata.category(char).startswith("C") for char in key)
        ):
            raise WikipediaSearchError("search_response_invalid")
        keys.add(key)
        title = _plain(page.get("title"), 512)
        excerpt = _plain(page.get("excerpt"), MAX_SNIPPET_CHARACTERS, html=True)
        description = page.get("description")
        if not title:
            raise WikipediaSearchError("search_response_invalid")
        items.append(
            WikipediaSearchItem(
                title=title,
                # A remote key cannot choose another scheme, origin, port or query.
                url=f"https://{language}.wikipedia.org/wiki/{quote(key, safe='')}",
                excerpt=excerpt,
                description=None if description is None else _plain(description, 512),
            )
        )
    return WikipediaSearchResult(
        provider="wikipedia",
        language=language,
        status="ok" if items else "empty",
        items=tuple(items),
        observed_at=response.observed_at,
        expires_at=response.expires_at,
        raw_bytes=response.raw_bytes,
    )


def duckduckgo_browser_url(query: str) -> str:
    """An explicit browser target only. This function neither opens nor reads it."""
    # Reuse the same strict, finite query policy without making a search request.
    wikipedia_search_url(query)
    return "https://duckduckgo.com/?" + urlencode({"q": query})
