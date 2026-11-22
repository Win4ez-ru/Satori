"""Conservative cloud-only text admission; never rewrite canonical or rendered content."""

import re
import unicodedata

import regex  # type: ignore[import-untyped]

from satori.desktop.voice.speech_contracts import SpeechError

_GRAPHEMES = regex.compile(r"\X")
_PICTOGRAPHIC = regex.compile(r"\p{Extended_Pictographic}|\p{Emoji_Presentation}")
_MATH_SYMBOLS = frozenset("➕➖➗✖")  # noqa: RUF001 - explicit mathematical operators
_TEXT_SYMBOLS = frozenset("©®™")
_TILDE_FENCE = re.compile(r"(?m)^ {0,3}~{3,}")
_EMPHASIS = re.compile(r"\*{1,3}|_{1,3}|~~")


def _decorative_cluster(cluster: str) -> bool:
    # Keycaps, explicit emoji presentation and emoji tags remain unsafe even when
    # another character in their cluster happens to be a mathematical operator.
    if any(char in {"\u20e3", "\ufe0f"} or 0xE0020 <= ord(char) <= 0xE007F for char in cluster):
        return True
    base, marks = cluster[0], cluster[1:]
    if (unicodedata.category(base) == "Sm" or base in _MATH_SYMBOLS | _TEXT_SYMBOLS) and all(
        unicodedata.category(char) in {"Mn", "Me"} for char in marks
    ):
        return False
    return bool(_PICTOGRAPHIC.search(cluster))


def _residual_emphasis(text: str) -> bool:
    for match in _EMPHASIS.finditer(text):
        start, after = match.span()
        delimiter = match.group()[0]
        if (
            after < len(text)
            and not text[after].isspace()
            and text[after] not in {delimiter, "="}
            and (not start or (not text[start - 1].isalnum() and text[start - 1] != delimiter))
        ):
            return True
    return False


def admit_cloud_speech_text(text: str) -> None:
    """Refuse content that could be read as direction or unsupported presentation.

    NFKC is inspection only. The closed presentation-residue list is ASCII backticks,
    line-leading tilde fences and residual emphasis openers; this is not a general
    Markdown/HTML parser or a proof of semantic prompt-injection immunity. A literal
    code string such as **kwargs may conservatively remain text-only.
    """

    inspected = unicodedata.normalize("NFKC", text)
    if (
        "[" in inspected
        or "]" in inspected
        or "`" in text
        or _TILDE_FENCE.search(text)
        or _residual_emphasis(text)
        or any(_decorative_cluster(match.group()) for match in _GRAPHEMES.finditer(text))
    ):
        raise SpeechError("speech_text_unsupported")
