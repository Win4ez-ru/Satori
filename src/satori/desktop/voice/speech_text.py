"""Deterministic speech presentation; canonical replies are never changed here."""

import re
import unicodedata
from dataclasses import dataclass

import regex  # type: ignore[import-untyped]

_GRAPHEMES = regex.compile(r"\X")
_EMOJI_PRESENTATION = regex.compile(r"\p{Emoji_Presentation}")
_PICTOGRAPHIC = regex.compile(r"\p{Extended_Pictographic}")
_KEYCAP = re.compile("([0-9#*])\ufe0f?\u20e3")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^\r\n]*$")
_HEADING = re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+")
_BULLET = re.compile(r"(?m)^ {0,3}[-+*][ \t]+(?=(?:\*\*|__|~~|\*|_)?[^\W\d_])")
_QUOTE = re.compile(r"(?m)^ {0,3}>[ \t]+")
_ESCAPABLE = frozenset(r"\`*_{}[]()#+-.!>~|")
# These four operators have an emoji presentation as well as mathematical use.
_PICTOGRAPHIC_OPERATORS = frozenset("➕➖➗✖")  # noqa: RUF001 - intentional math symbols
_TEXT_SYMBOLS = frozenset("©®™")
_MAX_INLINE_DEPTH = 8


@dataclass(frozen=True)
class _Run:
    text: str
    literal: bool = False


def _speech_prose(text: str) -> str:
    parts: list[str] = []
    for match in _GRAPHEMES.finditer(text):
        cluster = match.group()
        keycap = _KEYCAP.fullmatch(cluster)
        if keycap is not None:
            parts.append(keycap.group(1))
            continue
        if (
            "\ufe0e" in cluster
            or any(unicodedata.category(character) == "Sm" for character in cluster)
            or any(character in _PICTOGRAPHIC_OPERATORS for character in cluster)
            or ("\ufe0f" not in cluster and any(c in _TEXT_SYMBOLS for c in cluster))
        ):
            parts.append(cluster)
        elif (
            _EMOJI_PRESENTATION.search(cluster)
            or ("\ufe0f" in cluster and _PICTOGRAPHIC.search(cluster))
            or ("\u200d" in cluster and _PICTOGRAPHIC.search(cluster))
        ):
            # A separator avoids turning adjacent words into a different word.
            parts.append(" ")
        else:
            parts.append(cluster)
    result = "".join(parts)
    result = re.sub(r"[^\S\r\n]+", " ", result)
    result = re.sub(r" +([,.;:!?])", r"\1", result)
    result = re.sub(r" *\r?\n *", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result)


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int | None:
    """Find one balanced Markdown label/destination without evaluating its contents."""

    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == "\n":
            return None
        if character == "\\":
            index += 2
            continue
        if character == opening:
            depth += 1
            if depth > _MAX_INLINE_DEPTH:
                return None
        elif character == closing:
            depth -= 1
            if not depth:
                return index
        index += 1
    return None


def _link(text: str, start: int) -> tuple[str, int] | None:
    label_start = start + 1 if text.startswith("![", start) else start
    if text[label_start : label_start + 1] != "[":
        return None
    label_end = _balanced_end(text, label_start, "[", "]")
    if label_end is None or text[label_end + 1 : label_end + 2] != "(":
        return None
    target_end = _balanced_end(text, label_end + 1, "(", ")")
    if target_end is None:
        return None
    return text[label_start + 1 : label_end], target_end + 1


def _code_end(text: str, start: int, delimiter: str) -> int | None:
    index = start + len(delimiter)
    while (index := text.find(delimiter, index)) >= 0:
        after = index + len(delimiter)
        if text[index - 1 : index] != "`" and text[after : after + 1] != "`":
            return index
        index = after
    return None


def _emphasis_opens(text: str, start: int, delimiter: str) -> bool:
    after = start + len(delimiter)
    return (
        after < len(text)
        and not text[after].isspace()
        and text[after] != delimiter[0]
        and (not start or (not text[start - 1].isalnum() and text[start - 1] != delimiter[0]))
    )


def _emphasis_end(text: str, start: int, delimiter: str) -> int | None:
    after = start + len(delimiter)
    index = after
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "`":
            length = 1
            while text[index + length : index + length + 1] == "`":
                length += 1
            closing = _code_end(text, index, "`" * length)
            if closing is not None:
                index = closing + length
                continue
        end = index + len(delimiter)
        if (
            text.startswith(delimiter, index)
            and index > after
            and not text[index - 1].isspace()
            and text[index - 1] != delimiter[0]
            and (end == len(text) or not text[end].isalnum())
            and text[end : end + 1] != delimiter[0]
        ):
            return index
        index += 1
    return None


def _inline(text: str, depth: int = 0) -> list[_Run]:
    if depth >= _MAX_INLINE_DEPTH:
        return [_Run(text)]
    runs: list[_Run] = []
    plain: list[str] = []
    unclosed: set[str] = set()

    def flush() -> None:
        if plain:
            runs.append(_Run("".join(plain)))
            plain.clear()

    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\" and text[index + 1 : index + 2] in _ESCAPABLE:
            plain.append(text[index + 1])
            index += 2
            continue
        if character == "`":
            length = 1
            while text[index + length : index + length + 1] == "`":
                length += 1
            delimiter = "`" * length
            closing = _code_end(text, index, delimiter)
            if closing is not None:
                flush()
                runs.append(_Run(text[index + length : closing], literal=True))
                index = closing + length
                continue
        link = _link(text, index) if character in "[!" else None
        if link is not None:
            flush()
            label, index = link
            runs.extend(_inline(label, depth + 1))
            continue
        matched = False
        for delimiter in ("***", "___", "**", "__", "~~", "*", "_"):
            if (
                delimiter not in unclosed
                and text.startswith(delimiter, index)
                and _emphasis_opens(text, index, delimiter)
            ):
                closing = _emphasis_end(text, index, delimiter)
                if closing is not None:
                    flush()
                    runs.extend(_inline(text[index + len(delimiter) : closing], depth + 1))
                    index = closing + len(delimiter)
                    matched = True
                    break
                # Later openings cannot gain a closing marker absent from this suffix.
                unclosed.add(delimiter)
        if not matched:
            plain.append(character)
            index += 1
    flush()
    return runs


def _markdown_runs(text: str) -> list[_Run]:
    lines = text.splitlines(keepends=True)
    runs: list[_Run] = []
    prose: list[str] = []
    unclosed: set[str] = set()

    def flush() -> None:
        if prose:
            value = _QUOTE.sub("", _BULLET.sub("", _HEADING.sub("", "".join(prose))))
            runs.extend(_inline(value))
            prose.clear()

    index = 0
    while index < len(lines):
        fence = _FENCE.fullmatch(lines[index].rstrip("\r\n"))
        closing: int | None = None
        if fence is not None:
            delimiter = fence.group(1)
            if delimiter not in unclosed:
                close_pattern = re.compile(
                    rf"^ {{0,3}}{re.escape(delimiter[0])}{{{len(delimiter)},}}[ \t]*$"
                )
                for candidate in range(index + 1, len(lines)):
                    if close_pattern.fullmatch(lines[candidate].rstrip("\r\n")):
                        closing = candidate
                        break
                if closing is None:
                    unclosed.add(delimiter)
        if closing is not None:
            flush()
            runs.append(_Run("".join(lines[index + 1 : closing]), literal=True))
            index = closing + 1
        else:
            prose.append(lines[index])
            index += 1
    flush()
    return runs


def render_speech_text(text: str) -> str:
    """Render bounded reply text for speech without paraphrasing or changing saved text.

    Decorative emoji in prose are silent; mathematical/text-presentation symbols and
    literal code remain intact. Only balanced, narrowly supported Markdown is unwrapped.
    No engine, filesystem, prompt, state or logging operation occurs here.
    """

    output: list[str] = []
    prose: list[str] = []
    for run in _markdown_runs(text):
        if run.literal:
            output.append(_speech_prose("".join(prose)))
            prose.clear()
            output.append(run.text)
        else:
            prose.append(run.text)
    output.append(_speech_prose("".join(prose)))
    return "".join(output).strip()
