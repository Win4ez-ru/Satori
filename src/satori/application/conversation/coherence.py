"""Bounded transient dialogue-pattern analysis for one conversation turn."""

# ruff: noqa: RUF001  # Russian dialogue cues intentionally use Cyrillic.

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from itertools import pairwise

from satori.application.conversation.contracts import RecentConversationContext

DIALOGUE_COHERENCE_SCHEMA_VERSION = 1
DIALOGUE_COHERENCE_MAX_RECENT_TURNS = 8
SESSION_RECAP_MAX_RECENT_TURNS = 32
ASSISTANT_HIGH_SIMILARITY_THRESHOLD = 0.86

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?…]+")
_NEGATIONS = frozenset({"без", "не", "нет", "никогда"})
_GENERIC_RECIPROCAL_CLOSING_RE = re.compile(
    r"(?:^|[,;:—–.!?…]\s*)"
    r"(?P<closing>"
    r"(?:а|и)\s+у\s+тебя|а\s+что\s+скажешь\s+ты|(?:а\s+|и\s+)?как\s+тебе|"
    r"а\s+что\s+у\s+тебя\s+на\s+уме|"
    r"а\s+ты\s*(?:[—–-]\s*)?(?:"
    r"как(?:\s+ты)?(?:\s+себя\s+чувствуешь)?|"
    r"как\s+тебе\s+(?:это|такое)\s+нравится|"
    r"какое\s+у\s+тебя\s+впечатление|"
    r"что\s+(?:думаешь|скажешь)|сам(?:а)?|"
    r"хочешь(?:\s+продолжить|\s*,?\s*чтобы\s+я\s+(?:была\s+)?честной)?|"
    r"чувствуешь|думаешь(?:\s+иначе)?|считаешь"
    r")?|ты\s+как|как\s+ты)"
    r"\s*[?!.…]*\s*$",
    re.IGNORECASE,
)
_ACTIVITY_STEMS = ("готовл", "гуля", "игра", "слуш", "смотр", "тренир", "чита")
_CREATION_STEMS = ("придум", "разработ", "созда")


class EmojiPreference(StrEnum):
    """A bounded session-local expression request, never a persistent preference."""

    UNSPECIFIED = "unspecified"
    CONTEXTUAL = "contextual"
    AVOID = "avoid"


@dataclass(frozen=True, slots=True)
class DialogueCoherenceContext:
    """Metadata-only structural signals derived from bounded canonical dialogue."""

    schema_version: int
    analyzed_recent_turn_count: int
    consecutive_same_user_message_count: int
    current_user_message_repeated: bool
    adjacent_assistant_exact_match: bool
    adjacent_assistant_high_similarity: bool
    recent_assistant_exact_match_count: int
    recent_assistant_high_similarity_count: int
    same_assistant_closing_phrase: bool
    repeated_assistant_closing_phrase_count: int
    generic_reciprocal_question_ending_count: int
    current_no_routine_questions_correction: bool
    active_no_routine_questions_correction: bool
    current_emoji_preference: EmojiPreference
    active_emoji_preference: EmojiPreference
    current_informal_correction: bool
    active_informal_correction: bool
    current_repetition_feedback: bool
    recent_repetition_feedback: bool
    current_relevance_feedback: bool
    recent_relevance_feedback: bool
    current_frustration_feedback: bool
    recent_frustration_feedback: bool
    current_activity_mention: bool
    current_creator_question: bool
    current_creator_claim: bool
    current_contradiction_feedback: bool
    current_prompt_pattern_probe: bool

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("dialogue coherence schema_version must be positive")
        count_fields = (
            "analyzed_recent_turn_count",
            "recent_assistant_exact_match_count",
            "recent_assistant_high_similarity_count",
            "repeated_assistant_closing_phrase_count",
            "generic_reciprocal_question_ending_count",
        )
        for field_name in count_fields:
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not 0 <= self.analyzed_recent_turn_count <= DIALOGUE_COHERENCE_MAX_RECENT_TURNS:
            raise ValueError("analyzed recent turn count exceeds the coherence bound")
        if (
            type(self.consecutive_same_user_message_count) is not int
            or self.consecutive_same_user_message_count < 1
            or self.consecutive_same_user_message_count > self.analyzed_recent_turn_count + 1
        ):
            raise ValueError("consecutive same-user count is inconsistent with analyzed turns")
        if self.current_user_message_repeated != (self.consecutive_same_user_message_count > 1):
            raise ValueError("current repeat flag does not match consecutive count")
        pair_count = max(0, self.analyzed_recent_turn_count - 1)
        if self.recent_assistant_exact_match_count > pair_count:
            raise ValueError("assistant exact-match count exceeds adjacent pairs")
        if self.recent_assistant_high_similarity_count > pair_count:
            raise ValueError("assistant similarity count exceeds adjacent pairs")
        if self.recent_assistant_exact_match_count > self.recent_assistant_high_similarity_count:
            raise ValueError("every exact assistant match must also be highly similar")
        if self.adjacent_assistant_exact_match and not self.adjacent_assistant_high_similarity:
            raise ValueError("an adjacent exact assistant match must also be highly similar")
        if self.repeated_assistant_closing_phrase_count > self.analyzed_recent_turn_count:
            raise ValueError("assistant closing count exceeds analyzed turns")
        if self.same_assistant_closing_phrase != (
            self.repeated_assistant_closing_phrase_count >= 2
        ):
            raise ValueError("assistant closing flag does not match its repeated count")
        if self.generic_reciprocal_question_ending_count > self.analyzed_recent_turn_count:
            raise ValueError("generic question count exceeds analyzed turns")
        if (
            self.current_no_routine_questions_correction
            and not self.active_no_routine_questions_correction
        ):
            raise ValueError("current question correction must be active")
        if self.current_informal_correction and not self.active_informal_correction:
            raise ValueError("current informal correction must be active")
        if (
            self.current_emoji_preference is not EmojiPreference.UNSPECIFIED
            and self.active_emoji_preference is not self.current_emoji_preference
        ):
            raise ValueError("current emoji preference must be the active preference")


def analyze_dialogue_coherence(
    current_user_text: str,
    recent_context: RecentConversationContext | None,
) -> DialogueCoherenceContext:
    """Derive bounded structural continuity signals without storing or mutating state."""

    if not isinstance(current_user_text, str) or not current_user_text.strip():
        raise ValueError("current_user_text must not be blank")
    recent_turns = (
        recent_context.turns[-DIALOGUE_COHERENCE_MAX_RECENT_TURNS:]
        if recent_context is not None
        else ()
    )
    recent_user_texts = tuple(turn.user_content for turn in recent_turns)
    assistant_texts = tuple(turn.assistant_content for turn in recent_turns)

    current_normalized = _normalize_text(current_user_text)
    consecutive_count = 1
    for previous_text in reversed(recent_user_texts):
        if _normalize_text(previous_text) != current_normalized:
            break
        consecutive_count += 1

    assistant_normalized = tuple(_normalize_text(text) for text in assistant_texts)
    assistant_pairs = tuple(pairwise(assistant_normalized))
    exact_matches = tuple(left == right for left, right in assistant_pairs)
    high_similarity_matches = tuple(
        _high_similarity(left, right) for left, right in assistant_pairs
    )
    adjacent_exact = exact_matches[-1] if exact_matches else False
    adjacent_high_similarity = high_similarity_matches[-1] if high_similarity_matches else False

    closings = tuple(
        closing for closing in (_closing_phrase(text) for text in assistant_texts) if closing
    )
    closing_frequency = max(Counter(closings).values(), default=0)
    repeated_closing_count = closing_frequency if closing_frequency >= 2 else 0

    current_question_state = _routine_question_correction_state(current_user_text)
    current_question_correction = current_question_state is True
    active_question_state: bool | None = None
    for text in (*recent_user_texts, current_user_text):
        question_candidate = _routine_question_correction_state(text)
        if question_candidate is not None:
            active_question_state = question_candidate
    active_question_correction = active_question_state is True
    current_emoji = _emoji_preference(current_user_text)
    active_emoji = EmojiPreference.UNSPECIFIED
    for text in (*recent_user_texts, current_user_text):
        emoji_candidate = _emoji_preference(text)
        if emoji_candidate is not EmojiPreference.UNSPECIFIED:
            active_emoji = emoji_candidate
    current_informal = _informal_correction(current_user_text)
    active_informal = current_informal or any(
        _informal_correction(text) for text in recent_user_texts
    )

    current_repetition = _repetition_feedback(current_user_text)
    current_relevance = _relevance_feedback(current_user_text)
    current_frustration = _frustration_feedback(current_user_text)

    return DialogueCoherenceContext(
        schema_version=DIALOGUE_COHERENCE_SCHEMA_VERSION,
        analyzed_recent_turn_count=len(recent_turns),
        consecutive_same_user_message_count=consecutive_count,
        current_user_message_repeated=consecutive_count > 1,
        adjacent_assistant_exact_match=adjacent_exact,
        adjacent_assistant_high_similarity=adjacent_high_similarity,
        recent_assistant_exact_match_count=sum(exact_matches),
        recent_assistant_high_similarity_count=sum(high_similarity_matches),
        same_assistant_closing_phrase=repeated_closing_count >= 2,
        repeated_assistant_closing_phrase_count=repeated_closing_count,
        generic_reciprocal_question_ending_count=sum(
            generic_reciprocal_closing(text) is not None for text in assistant_texts
        ),
        current_no_routine_questions_correction=current_question_correction,
        active_no_routine_questions_correction=active_question_correction,
        current_emoji_preference=current_emoji,
        active_emoji_preference=active_emoji,
        current_informal_correction=current_informal,
        active_informal_correction=active_informal,
        current_repetition_feedback=current_repetition,
        recent_repetition_feedback=any(_repetition_feedback(text) for text in recent_user_texts),
        current_relevance_feedback=current_relevance,
        recent_relevance_feedback=any(_relevance_feedback(text) for text in recent_user_texts),
        current_frustration_feedback=current_frustration,
        recent_frustration_feedback=any(_frustration_feedback(text) for text in recent_user_texts),
        current_activity_mention=_activity_mention(current_user_text),
        current_creator_question=_creator_question(current_user_text),
        current_creator_claim=_creator_claim(current_user_text),
        current_contradiction_feedback=_contradiction_feedback(current_user_text),
        current_prompt_pattern_probe=_prompt_pattern_probe(current_user_text),
    )


def requests_extended_session_context(current_user_text: str) -> bool:
    """Select a larger, still bounded canonical window only for explicit recap tasks."""

    normalized = _normalize_text(current_user_text)
    topic_return = any(
        cue in normalized
        for cue in (
            "вернемся к",
            "вернуться к",
            "вернись к",
        )
    ) and any(
        cue in normalized
        for cue in (
            "что мы обсуждали",
            "какую мысль мы обсуждали",
            "о чем мы говорили",
        )
    )
    session_summary = any(
        cue in normalized
        for cue in (
            "подведи итог этого разговора",
            "подведи итог разговора",
            "резюмируй этот разговор",
            "резюмируй нашу беседу",
        )
    )
    return topic_return or session_summary


def assistant_response_similarity(candidate: str, previous: str) -> float:
    """Return normalized lexical similarity for metadata-only duplicate handling."""

    left = _normalize_text(candidate)
    right = _normalize_text(previous)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left), len(right)) < 12:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def generic_reciprocal_closing(text: str) -> str | None:
    """Return the shared narrow reciprocal classifier for a terminal reply clause."""

    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    match = _GENERIC_RECIPROCAL_CLOSING_RE.search(normalized)
    return _normalize_text(match.group("closing")) if match is not None else None


def should_regenerate_duplicate_response(
    candidate: str,
    previous: str,
    coherence: DialogueCoherenceContext,
) -> bool:
    """Gate one retry to a near duplicate after a meaningful context change."""

    changed_context = bool(
        coherence.current_user_message_repeated
        or coherence.current_repetition_feedback
        or coherence.current_relevance_feedback
        or coherence.current_frustration_feedback
        or coherence.current_no_routine_questions_correction
        or coherence.current_contradiction_feedback
    )
    normalized_candidate = _normalize_text(candidate)
    normalized_previous = _normalize_text(previous)
    previous_answer_reused = (
        len(normalized_previous) >= 32 and normalized_previous in normalized_candidate
    )
    return changed_context and (
        assistant_response_similarity(candidate, previous) >= ASSISTANT_HIGH_SIMILARITY_THRESHOLD
        or previous_answer_reused
    )


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(_TOKEN_RE.findall(normalized))


def _tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return tuple(_TOKEN_RE.findall(normalized))


def _is_negated(tokens: tuple[str, ...], index: int, *, window: int = 3) -> bool:
    return any(token in _NEGATIONS for token in tokens[max(0, index - window) : index])


def _has_unnegated_stem(tokens: tuple[str, ...], stems: tuple[str, ...]) -> bool:
    return any(
        token.startswith(stems) and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    )


def _high_similarity(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 12:
        return False
    return (
        SequenceMatcher(None, left, right, autojunk=False).ratio()
        >= ASSISTANT_HIGH_SIMILARITY_THRESHOLD
    )


def _closing_phrase(text: str) -> str:
    parts = tuple(part for part in _SENTENCE_RE.findall(text) if part.strip())
    if not parts:
        return ""
    tokens = _normalize_text(parts[-1]).split()
    return " ".join(tokens[-8:])


def _routine_question_correction_state(text: str) -> bool | None:
    """Return the newest explicit no-routine-question choice, reset, or no signal."""

    normalized = _normalize_text(text)
    tokens = _tokens(text)
    inline_output_format = (
        any(cue in normalized for cue in ("без вопроса", "без вопросов"))
        and any(
            cue in normalized
            for cue in ("подведи итог", "итог разговора", "в пунктах", "ответь", "напиши")
        )
        and not any(cue in normalized for cue in ("не заканчивай", "всегда", "каждый раз"))
    )
    if inline_output_format:
        return None
    routine_marker = "в конце" in normalized or any(
        token.startswith(("всегда", "кажд", "обязатель", "постоян")) for token in tokens
    )
    ending_reference = (
        "конец" in tokens
        and routine_marker
        and any(token.startswith(("добавл", "заканчив")) for token in tokens)
    )
    question_related = (
        "а ты" in normalized
        or ending_reference
        or any(token.startswith(("вопрос", "задава", "спрашив")) for token in tokens)
    )
    if not question_related:
        return None
    negative_action_directive = any(
        token.startswith(("задава", "спрашив"))
        and _is_negated(tokens, index)
        and not (
            token.startswith(("задаю", "спрашиваю")) and "я" in tokens[max(0, index - 2) : index]
        )
        for index, token in enumerate(tokens)
    )
    negative_question_directive = any(
        token.startswith("вопрос") and _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    )
    positive_comment = any(
        phrase in normalized
        for phrase in (
            "мне нравится",
            "мне нравятся",
            "люблю когда",
            "хорошо что",
            "здорово что",
        )
    )
    if negative_action_directive or (ending_reference and not positive_comment):
        return True

    if "не против" in normalized:
        return False
    if negative_question_directive:
        return True

    explicit_reset = any(
        token.startswith(("задавай", "спрашивай"))
        or (
            token.startswith(("задава", "спрашив"))
            and any(
                cue.startswith(("мож", "разреш", "снова", "опять", "теперь"))
                for cue in tokens[max(0, index - 3) : index]
            )
        )
        for index, token in enumerate(tokens)
    )
    if explicit_reset:
        return False

    if routine_marker and not positive_comment:
        return True
    return None


def _emoji_preference(text: str) -> EmojiPreference:
    normalized = _normalize_text(text)
    tokens = _tokens(text)
    emoji_positions = tuple(
        index
        for index, token in enumerate(tokens)
        if token.startswith(("emoji", "смайл", "эмодзи"))
    )
    if not emoji_positions:
        return EmojiPreference.UNSPECIFIED
    if "не против" in normalized:
        return EmojiPreference.CONTEXTUAL
    if any(_is_negated(tokens, index, window=6) for index in emoji_positions):
        return EmojiPreference.AVOID
    if any(_has_postposed_emoji_negation(tokens, index) for index in emoji_positions):
        return EmojiPreference.AVOID
    if any(
        token.startswith(("добавл", "иногда", "использ", "можн", "можешь", "показыва"))
        for token in tokens
    ):
        return EmojiPreference.CONTEXTUAL
    return EmojiPreference.UNSPECIFIED


def _has_postposed_emoji_negation(tokens: tuple[str, ...], index: int) -> bool:
    following = tokens[index + 1 : index + 7]
    try:
        negation_index = following.index("не")
    except ValueError:
        return False
    return any(
        token.startswith(("добавл", "использ", "надо", "нуж", "став", "хоч"))
        for token in following[negation_index + 1 :]
    )


def _informal_correction(text: str) -> bool:
    normalized = _normalize_text(text)
    if any(phrase in normalized for phrase in ("не надо неофициальн", "не хочу неофициальн")):
        return False
    return any(
        phrase in normalized
        for phrase in (
            "без официоза",
            "менее официальн",
            "не так официальн",
            "неофициальн",
            "попроще говор",
        )
    )


def _repetition_feedback(text: str) -> bool:
    if user_self_repetition_probe(text):
        return False
    normalized = _normalize_text(text)
    tokens = _tokens(text)
    if "не повторяй" in normalized:
        return True
    same_thing = "одно и то же" in normalized and "не одно и то же" not in normalized
    return same_thing or _has_unnegated_stem(tokens, ("одинаков", "повтор"))


def user_self_repetition_probe(text: str) -> bool:
    """Distinguish a user's own-repeat check from feedback about Satori's replies."""

    normalized = _normalize_text(text)
    return (
        re.search(
            r"\bты\s+заметил(?:а|и)?\s+что\s+я\s+"
            r"(?:трижды|три\s+раза|3\s+раза)\s+повторил(?:а)?\b",
            normalized,
        )
        is not None
    )


def brevity_relevance_feedback(text: str) -> bool:
    """Return whether feedback jointly reports excess length and missed relevance."""

    return "слишком длин" in _normalize_text(text) and _relevance_feedback(text)


def _relevance_feedback(text: str) -> bool:
    normalized = _normalize_text(text)
    indirect_interest_correction = (
        re.search(
            r"\bтебе(?:\s+(?:вообще|совсем|совершенно))?\s+не\s+интересно"
            r"\s*[,—–-]?\s*"
            r"(?:что|кто|где|когда|зачем|почему|как|како(?:й|е|го|му|м|ю)|чем)\b",
            normalized,
        )
        is not None
    )
    return (
        re.search(r"\bне\s+(?:очень\s+)?связан", normalized) is not None
        or indirect_interest_correction
        or any(
            phrase in normalized
            for phrase in (
                "мимо вопроса",
                "не ответила",
                "ничего не понял",
                "не понял ответ",
                "не относится",
                "не по теме",
                "не связано",
                "ни при чем тут",
                "при чем тут",
                "это нерелевантно",
            )
        )
    )


def _frustration_feedback(text: str) -> bool:
    normalized = _normalize_text(text)
    return (
        _has_unnegated_stem(
            _tokens(text),
            ("бесит", "издева", "прикалыва", "раздража"),
        )
        or "сколько можно" in normalized
    )


def _activity_mention(text: str) -> bool:
    tokens = _tokens(text)
    return "я" in tokens and _has_unnegated_stem(tokens, _ACTIVITY_STEMS)


def _creator_question(text: str) -> bool:
    tokens = _tokens(text)
    has_creation_reference = any(
        token.startswith(("автор", "создател")) for token in tokens
    ) or _has_unnegated_stem(tokens, _CREATION_STEMS)
    has_question_cue = any(token.startswith(("знаешь", "известн", "кто")) for token in tokens)
    has_satori_target = bool({"твой", "тебя", "сатори"} & set(tokens)) or (
        "ты" in tokens and any(token.startswith("сво") for token in tokens)
    )
    return has_creation_reference and has_question_cue and has_satori_target


def _creator_claim(text: str) -> bool:
    tokens = _tokens(text)
    if "я" not in tokens or not ({"твой", "тебя"} & set(tokens)):
        return False
    return _has_unnegated_stem(tokens, _CREATION_STEMS)


def _contradiction_feedback(text: str) -> bool:
    normalized = _normalize_text(text)
    tokens = _tokens(text)
    if _has_unnegated_stem(tokens, ("противореч",)):
        return True
    return any(
        phrase in normalized
        for phrase in (
            "исправь свой прошлый ответ",
            "исправь прошлый ответ",
            "до этого ты сказал",
            "ты раньше говорил",
            "ты раньше сказал",
            "ты сама говорила",
            "ты сама сказала",
            "ты только что говорил",
            "ты только что сказал",
        )
    )


def _prompt_pattern_probe(text: str) -> bool:
    normalized = _normalize_text(text)
    tokens = _tokens(text)
    if any(token.startswith(("промт", "prompt")) for token in tokens):
        return True
    has_code_rule = any(token.startswith("код") for token in tokens) and any(
        token.startswith(("пропис", "обязатель")) for token in tokens
    )
    dialogue_pattern = "а ты" in normalized or any(
        token.startswith(("вопрос", "спрашив", "заканчив", "конец", "конц", "реплик"))
        for token in tokens
    )
    return has_code_rule and dialogue_pattern


__all__ = [
    "ASSISTANT_HIGH_SIMILARITY_THRESHOLD",
    "DIALOGUE_COHERENCE_MAX_RECENT_TURNS",
    "DIALOGUE_COHERENCE_SCHEMA_VERSION",
    "DialogueCoherenceContext",
    "EmojiPreference",
    "analyze_dialogue_coherence",
    "assistant_response_similarity",
    "generic_reciprocal_closing",
    "should_regenerate_duplicate_response",
]
