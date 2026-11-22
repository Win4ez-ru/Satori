"""Inert bounded text extraction; no document execution or resource retrieval."""

from html.parser import HTMLParser

MAX_DISPLAY_CHARACTERS = 65_536
_SKIP = frozenset(
    {"head", "script", "style", "template", "noscript", "svg", "canvas", "iframe", "object"}
)
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
_BLOCK = frozenset(
    {"article", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "hr", "li", "p", "tr"}
)


class InvalidPublicText(ValueError):
    """Closed text parser failure, never containing source text."""


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip: list[str] = []
        self.characters = 0
        self.truncated = False
        self.tags = 0

    def _append(self, text: str) -> None:
        remaining = MAX_DISPLAY_CHARACTERS - self.characters
        if len(text) > remaining:
            self.truncated = True
        text = text[:remaining]
        self.parts.append(text)
        self.characters += len(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs  # href/src/style/handlers are neither interpreted nor returned.
        self.tags += 1
        if self.tags > 20_000:
            raise InvalidPublicText("content_invalid")
        if self.skip:
            if tag not in _VOID:
                self.skip.append(tag)
                if len(self.skip) > 128:
                    raise InvalidPublicText("content_invalid")
        elif tag in _SKIP:
            self.skip.append(tag)
        elif tag in _BLOCK:
            self._append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in self.skip:
                index = len(self.skip) - 1 - self.skip[::-1].index(tag)
                self.skip = self.skip[:index]
        elif tag in _BLOCK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self._append(data)


def extract_public_text(raw: bytes, media_type: str) -> tuple[str, bool]:
    """Strict UTF-8 text, optionally HTML-to-text; the boolean means display truncation."""
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise InvalidPublicText("encoding_unsupported") from None
    if media_type == "text/html":
        parser = _TextParser()
        try:
            parser.feed(text)
            parser.close()
        except (ValueError, RecursionError):
            raise InvalidPublicText("content_invalid") from None
        text, partial = "".join(parser.parts).strip(), parser.truncated
    else:
        partial = len(text) > MAX_DISPLAY_CHARACTERS
        text = text[:MAX_DISPLAY_CHARACTERS]
    if any(
        (ord(char) < 32 and char not in "\t\r\n")
        or 127 <= ord(char) <= 159
        or char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        for char in text
    ):
        raise InvalidPublicText("content_invalid")
    return text, partial
