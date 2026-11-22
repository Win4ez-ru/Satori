"""Pure availability projection of the last canonical assistant question-like span.

Punctuation grounds only a structural candidate. It does not establish a question's
intent, the user's uptake, an emotion, a relationship fact or fresh state evidence.
The projection carries offsets rather than assistant text and has no write path.
"""

# Quote delimiters below intentionally preserve their distinct Unicode characters.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from satori.application.conversation.contracts import RecentConversationContext


PRIOR_CONVERSATIONAL_CUE_SCHEMA_VERSION = 1
PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS = 128
PRIOR_CONVERSATIONAL_CUE_MAX_SPAN_CHARS = 512


class PriorConversationalCueKind(StrEnum):
    QUESTION_CANDIDATE = "question_candidate"


@dataclass(frozen=True, slots=True)
class PriorConversationalCue:
    """Request-local observed form; start/end are exact Unicode code-point offsets.

    End is exclusive. IDs identify the last supplied complete canonical turn, not
    fresh user evidence. The full structural sentence must fit the span bound;
    neither the sentence nor its IDs are normalized, shortened or reconstructed.
    """

    schema_version: int
    kind: PriorConversationalCueKind
    prior_interaction_id: str
    prior_user_message_id: str
    prior_assistant_message_id: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != PRIOR_CONVERSATIONAL_CUE_SCHEMA_VERSION
        ):
            raise ValueError("prior conversational cue schema_version must be 1")
        if not isinstance(self.kind, PriorConversationalCueKind):
            raise ValueError("prior conversational cue kind must be typed")
        for field_name in (
            "prior_interaction_id",
            "prior_user_message_id",
            "prior_assistant_message_id",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS
            ):
                raise ValueError("prior conversational cue IDs must be exact and bounded")
        if (
            isinstance(self.start, bool)
            or not isinstance(self.start, int)
            or isinstance(self.end, bool)
            or not isinstance(self.end, int)
            or self.start < 0
            or self.end <= self.start
            or self.end - self.start > PRIOR_CONVERSATIONAL_CUE_MAX_SPAN_CHARS
        ):
            raise ValueError("prior conversational cue offsets must identify a bounded span")


def project_prior_conversational_cue(
    recent_context: RecentConversationContext | None,
) -> PriorConversationalCue | None:
    """Inspect only the last available complete turn; never scavenge earlier questions.

    A candidate is the final unquoted punctuation-delimited sentence ending with
    an unquoted question mark, optionally followed by question/exclamation marks.
    Unquoted .!? delimit sentences; balanced quoted punctuation and line wrapping
    do not. Any unbalanced supported quote or oversized full span fails closed.
    This syntax deliberately leaves rhetorical questions as candidates: no semantic
    uptake, grievance or motive is inferred from question form.
    """

    if recent_context is None or not recent_context.turns:
        return None
    turn = recent_context.turns[-1]
    text = turn.assistant_content
    quoted = _balanced_quote_mask(text)
    if quoted is None:
        return None
    end = len(text.rstrip())
    if end == 0:
        return None

    terminal_start = end
    while (
        terminal_start > 0 and text[terminal_start - 1] in "?!" and not quoted[terminal_start - 1]
    ):
        terminal_start -= 1
    if terminal_start == end or "?" not in text[terminal_start:end]:
        return None

    start = terminal_start
    while start > 0:
        previous = start - 1
        if not quoted[previous] and text[previous] in ".!?":
            break
        start = previous
    while start < terminal_start and text[start].isspace():
        start += 1
    if start == terminal_start or end - start > PRIOR_CONVERSATIONAL_CUE_MAX_SPAN_CHARS:
        return None

    try:
        return PriorConversationalCue(
            schema_version=PRIOR_CONVERSATIONAL_CUE_SCHEMA_VERSION,
            kind=PriorConversationalCueKind.QUESTION_CANDIDATE,
            prior_interaction_id=turn.interaction_id,
            prior_user_message_id=turn.user_message_id,
            prior_assistant_message_id=turn.assistant_message_id,
            start=start,
            end=end,
        )
    except ValueError:
        return None


def prior_conversational_cue_matches_context(
    cue: PriorConversationalCue | None,
    recent_context: RecentConversationContext | None,
) -> bool:
    """Compare the whole cue with exact recomputation from the supplied projection."""

    return cue == project_prior_conversational_cue(recent_context)


def _balanced_quote_mask(text: str) -> tuple[bool, ...] | None:
    """Mask balanced supported quotes without replacing or normalizing characters.

    Nested paired quotes, ASCII quotes and backticks are supported. Backslash escapes
    retain their enclosing quote status. Apostrophes between letters/digits are word
    punctuation rather than quotes. Ambiguous unmatched forms are not recovered.
    """

    paired_quotes = {"«": "»", "‹": "›", "“": "”", "„": "“", "‘": "’", "‚": "‘"}
    symmetric_quotes = frozenset(('"', "'", "`"))
    paired_closers = frozenset(paired_quotes.values())
    expected_closers: list[str] = []
    mask: list[bool] = []
    escaped = False
    for index, character in enumerate(text):
        enclosed = bool(expected_closers)
        if escaped:
            mask.append(enclosed)
            escaped = False
            continue
        if character == "\\":
            mask.append(enclosed)
            escaped = True
            continue
        if (
            character in ("'", "’")
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        ):
            mask.append(enclosed)
            continue
        if expected_closers and character == expected_closers[-1]:
            mask.append(True)
            expected_closers.pop()
        elif character in paired_quotes:
            mask.append(True)
            expected_closers.append(paired_quotes[character])
        elif character in symmetric_quotes:
            mask.append(True)
            expected_closers.append(character)
        elif character in paired_closers:
            return None
        else:
            mask.append(enclosed)
    if expected_closers:
        return None
    return tuple(mask)
