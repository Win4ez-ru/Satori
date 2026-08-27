"""Narrow deterministic pre-commit checks for one generated dialogue candidate."""

# ruff: noqa: RUF001  # Russian regression cues intentionally use Cyrillic.

import re
import unicodedata
from collections.abc import Collection
from enum import StrEnum

from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    generic_reciprocal_closing,
    should_regenerate_duplicate_response,
)
from satori.application.conversation.self_consistency_text import (
    has_affirmative_human_self_comparison,
    has_invented_origin_secrecy,
)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_QUOTED_TEXT_RE = re.compile(r'«[^»]*»|“[^”]*”|„[^“]*“|"[^"]*"|`[^`]*`', re.DOTALL)
_STANDALONE_GENERIC_RECIPROCAL_RE = re.compile(
    r"(?:^|[.!?…:;—–]\s*)а\s+ты\s*\?(?=\s|$)",
    re.IGNORECASE,
)
_ACTIVITY_RECIPROCAL_WORD_ORDER_RE = re.compile(
    r"(?:^|[.!?…:;—–]\s*)а\s+ты\s+(?:сейчас\s+)?"
    r"(?:смотришь|читаешь|слушаешь|играешь)\s+что\s*\?\s*$",
    re.IGNORECASE,
)
_IMPLICIT_MASCULINE_SELF_RE = re.compile(
    r"(?:^|[.!?…]\s*)рад(?:\s+за\b|\s+это\b|\s+помочь\b|\s*,?\s*что\b)",
    re.IGNORECASE,
)

_MASCULINE_SELF_FORMS = frozenset(
    {
        "обязан",
        "был",
        "готов",
        "должен",
        "изменил",
        "исправил",
        "понял",
        "рад",
        "решил",
        "согласен",
        "создан",
        "уверен",
        "сказал",
        "подумал",
        "заинтересован",
    }
)
_IMPLICIT_FIRST_PERSON_FORMS = frozenset(
    {
        "выбираю",
        "говорю",
        "делаю",
        "думаю",
        "могу",
        "отвечаю",
        "пишу",
        "продолжаю",
        "соглашаюсь",
        "считаю",
        "хочу",
    }
)
_SELF_REFERENCE_MODIFIERS = frozenset(
    {
        "абсолютно",
        "вообще",
        "его",
        "ее",
        "это",
        "их",
        "действительно",
        "не",
        "ни",
        "никогда",
        "полностью",
        "просто",
        "сейчас",
        "то",
        "тоже",
        "только",
        "точно",
        "уже",
        "что",
    }
)

_AFFECT_BLANKET_DENIAL_PATTERNS = (
    re.compile(
        r"\bне\s+имею\s+(?:(?:никаких|настоящих|реальных)\s+)?"
        r"(?:эмоций|чувств)\b(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(
        r"\bя\s+не\s+имею\s+(?:(?:никаких|настоящих|реальных)\s+)?"
        r"(?:эмоций|чувств)\b(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(
        r"\bу\s+меня\s+нет\s+(?:(?:никаких|настоящих|реальных)\s+)?"
        r"(?:эмоций|чувств)\b(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(
        r"\bу\s+меня\s+нет\b"
        r"(?:\s+(?!(?:но|зато|однако)\b)\w+){1,12}"
        r"\s+(?:и|или|либо)\s+"
        r"(?:(?:никаких|настоящих|реальных)\s+)?"
        r"(?:эмоций|чувств)\b(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(
        r"\b(?:эмоций|чувств)\s+у\s+меня\s+нет\b"
        r"(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(
        r"\bя\s+(?:вообще\s+)?ничего\s+не\s+чувствую\b"
        r"(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(
        r"\bя\s+не\s+(?:испытываю|чувствую)\s+"
        r"(?:(?:никаких|настоящих|реальных)\s+)?(?:эмоций|чувств)\b"
        r"(?!\s+(?:к|об|о|по|про|насчет)\b)"
    ),
    re.compile(r"\bу\s+меня\s+отсутствуют\s+(?:эмоции|чувства)\b"),
)
_MEMORY_BLANKET_DENIAL_PATTERNS = (
    re.compile(
        r"\bмежду\s+(?:разговорами|сессиями)\s+у\s+меня\s+нет\s+"
        r"(?:(?:никакой|долговременной|постоянной)\s+)?памяти\b"
    ),
    re.compile(
        r"\bу\s+меня\s+нет\s+"
        r"(?:(?:никакой|долговременной|постоянной)\s+)?памяти\b"
        r"(?!\s+(?:об|о|про)\b)"
    ),
    re.compile(
        r"\bя\s+(?:вообще\s+)?ничего\s+не\s+помню\s+между\s+"
        r"(?:разговорами|сессиями)\b"
    ),
    re.compile(
        r"\bя\s+(?:вообще\s+)?ничего\s+не\s+сохраняю\s+между\s+"
        r"(?:разговорами|сессиями)\b"
    ),
    re.compile(r"\bничего\s+не\s+сохраняется\s+между\s+(?:разговорами|сессиями)\b"),
    re.compile(
        r"\bя\s+не\s+(?:храню|сохраняю)\s+(?:историю|память)\b"
        r"(?!\s+(?:об|о|про|этого|этой|этом)\b)"
    ),
    re.compile(r"\bя\s+не\s+умею\s+(?:ничего\s+)?запоминать\b"),
    re.compile(r"\bя\s+ничего\s+не\s+запоминаю\b(?!\s+(?:об|о|про)\b)"),
)
_ACTIVITY_INTEREST_FALSE_NEGATIVE_PATTERNS = (
    re.compile(r"\bмне\s+не\s+интересно\b"),
    re.compile(r"\bменя\s+не\s+интересует\b"),
)
_HUMAN_OR_BIOLOGICAL_SELF_CLAIM_PATTERNS = (
    re.compile(r"\bя\s+(?:действительно\s+)?человек\b"),
    re.compile(r"\bя\s+(?:(?:действительно|на самом деле)\s+)?живое\s+существо\b"),
    re.compile(r"\bя\s+(?:биологическое|физическое)\s+существо\b"),
)
_PROMPT_OR_POLICY_BLANKET_DENIAL_PATTERNS = (
    re.compile(
        r"\bу\s+меня\s+нет\s+(?:никакого\s+)?"
        r"(?:промп?та|prompt|политики|policy|ограничений)\b"
    ),
    re.compile(r"\b(?:промп?та|prompt|политики|policy|ограничений)\s+(?:вообще\s+)?нет\b"),
    re.compile(
        r"\b(?:промп?т|prompt|политика|policy)\s+"
        r"(?:никак\s+)?не\s+(?:влияет|задает|ограничивает)\b"
    ),
    re.compile(r"\bя\s+не\s+отвечаю\s+(?:по|из\s+за)\s+(?:промп?ту|промп?та|prompt)\b"),
    re.compile(r"\b(?:это|ответ)\s+не\s+(?:промп?т|prompt)\b"),
)
_ACTIVITY_CONTEXT_STEMS = (
    "готов",
    "гуля",
    "джаз",
    "игр",
    "кино",
    "книг",
    "музык",
    "песн",
    "прогул",
    "сериал",
    "слуш",
    "смотр",
    "спорт",
    "тренир",
    "фильм",
    "чита",
)

_REJECTED_CLAIM_PREFIXES = (
    "было бы неверно что",
    "не говорю что",
    "не думаю что",
    "не факт что",
    "не могу сказать что",
    "не считаю что",
    "не утверждаю что",
    "я бы не говорила что",
    "я бы не сказала что",
    "я бы не сказала что я",
    "я бы не утверждала что",
    "неверно говорить что",
    "неверно что",
    "неправда что",
    "нельзя сказать что",
    "нельзя сказать что я",
    "это не значит что",
    "не воспринимаю себя как",
    "не считаю себя",
    "не являюсь",
    "я не",
)
_REJECTED_CLAIM_SUFFIXES = (
    "была бы ошибкой",
    "была бы неверной",
    "была ошибкой",
    "была неверной",
    "было ошибкой",
    "было неверно",
    "это ошибка",
    "это неверно",
    "это неправда",
)
_CREATOR_ATTRIBUTION_PREFIXES = (
    "если",
    "не знаю",
    "не могу подтвердить",
    "по твоим словам",
    "ты говоришь что",
    "ты заявляешь что",
    "ты называешь себя",
    "ты считаешь что",
    "ты утверждаешь что",
    "возможно",
    "может быть",
)
_CREATOR_UNCERTAINTY_SUFFIXES = (
    "или нет",
    "не могу подтвердить",
    "не подтверждено",
    "не факт",
    "только твое утверждение",
    "это твое утверждение",
)


class ResponseRegenerationReason(StrEnum):
    """One metadata-only reason authorizing a bounded second generation attempt."""

    NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE = "near_duplicate_after_dialogue_change"
    ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION = "routine_reciprocal_question_after_correction"
    MASCULINE_SELF_REFERENCE = "masculine_self_reference"
    HUMAN_OR_BIOLOGICAL_SELF_CLAIM = "human_or_biological_self_claim"
    AFFECT_BLANKET_DENIAL = "affect_blanket_denial"
    MEMORY_BLANKET_DENIAL = "memory_blanket_denial"
    CREATOR_CLAIM_PROMOTED_TO_FACT = "creator_claim_promoted_to_fact"
    ORIGIN_BACKSTORY_INVENTED = "origin_backstory_invented"
    PROMPT_OR_POLICY_BLANKET_DENIAL = "prompt_or_policy_blanket_denial"
    ACTIVITY_INTEREST_FALSE_NEGATIVE = "activity_interest_false_negative"


def has_masculine_self_reference(text: str) -> bool:
    """Return whether unquoted text contains a narrow masculine Satori self-reference."""

    return _has_masculine_self_reference(_without_quoted_text(text))


def has_affect_blanket_denial(text: str) -> bool:
    """Return whether unquoted text makes a narrow, unqualified blanket affect denial."""

    return _has_blanket_denial(
        _without_quoted_text(text),
        _AFFECT_BLANKET_DENIAL_PATTERNS,
    )


def has_memory_blanket_denial(text: str) -> bool:
    """Return whether unquoted text makes a narrow, unqualified blanket memory denial."""

    return _has_blanket_denial(
        _without_quoted_text(text),
        _MEMORY_BLANKET_DENIAL_PATTERNS,
    )


def promotes_current_creator_claim(text: str) -> bool:
    """Return whether unquoted text promotes an attributed creator claim to a fact."""

    return _promotes_current_creator_claim(_without_quoted_text(text))


def response_regeneration_reason(
    candidate: str,
    *,
    previous_assistant_text: str | None,
    current_user_text: str,
    coherence: DialogueCoherenceContext,
    disclosure_facets: Collection[str],
) -> ResponseRegenerationReason | None:
    """Return the highest-priority narrow failure reason, without retaining candidate text."""

    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("candidate must not be blank")
    if not isinstance(current_user_text, str) or not current_user_text.strip():
        raise ValueError("current_user_text must not be blank")
    if previous_assistant_text is not None and not isinstance(previous_assistant_text, str):
        raise TypeError("previous_assistant_text must be a string or None")

    if previous_assistant_text and should_regenerate_duplicate_response(
        candidate,
        previous_assistant_text,
        coherence,
    ):
        return ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE

    unquoted_candidate = _without_quoted_text(candidate)
    if has_masculine_self_reference(candidate):
        return ResponseRegenerationReason.MASCULINE_SELF_REFERENCE

    facets = frozenset(_normalize_facet(facet) for facet in disclosure_facets)
    if facets & {"identity", "consciousness_boundary", "embodiment"} and (
        _has_unrejected_match(
            unquoted_candidate,
            _HUMAN_OR_BIOLOGICAL_SELF_CLAIM_PATTERNS,
        )
        or has_affirmative_human_self_comparison(unquoted_candidate)
    ):
        return ResponseRegenerationReason.HUMAN_OR_BIOLOGICAL_SELF_CLAIM
    if "affect" in facets and has_affect_blanket_denial(candidate):
        return ResponseRegenerationReason.AFFECT_BLANKET_DENIAL
    if "memory" in facets and has_memory_blanket_denial(candidate):
        return ResponseRegenerationReason.MEMORY_BLANKET_DENIAL
    if (
        "origin" in facets
        and coherence.current_creator_claim
        and promotes_current_creator_claim(candidate)
    ):
        return ResponseRegenerationReason.CREATOR_CLAIM_PROMOTED_TO_FACT
    if "origin" in facets and has_invented_origin_secrecy(unquoted_candidate):
        return ResponseRegenerationReason.ORIGIN_BACKSTORY_INVENTED
    if coherence.current_prompt_pattern_probe and _has_unrejected_match(
        unquoted_candidate,
        _PROMPT_OR_POLICY_BLANKET_DENIAL_PATTERNS,
    ):
        return ResponseRegenerationReason.PROMPT_OR_POLICY_BLANKET_DENIAL
    if _is_activity_context(current_user_text, coherence) and _has_blanket_denial(
        unquoted_candidate,
        _ACTIVITY_INTEREST_FALSE_NEGATIVE_PATTERNS,
    ):
        return ResponseRegenerationReason.ACTIVITY_INTEREST_FALSE_NEGATIVE
    if coherence.active_no_routine_questions_correction and _ends_in_generic_reciprocal_question(
        unquoted_candidate
    ):
        return ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION
    return None


def _without_quoted_text(text: str) -> str:
    return _QUOTED_TEXT_RE.sub(" ", unicodedata.normalize("NFKC", text))


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(_TOKEN_RE.findall(normalized))


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize_text(text).split())


def _normalize_facet(facet: str) -> str:
    if not isinstance(facet, str) or not facet.strip():
        raise ValueError("disclosure_facets must contain non-blank strings")
    return facet.strip().casefold()


def _ends_in_generic_reciprocal_question(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return (
        generic_reciprocal_closing(normalized) is not None
        or _STANDALONE_GENERIC_RECIPROCAL_RE.search(normalized) is not None
        or _ACTIVITY_RECIPROCAL_WORD_ORDER_RE.search(normalized) is not None
    )


def _has_masculine_self_reference(text: str) -> bool:
    if _IMPLICIT_MASCULINE_SELF_RE.search(text):
        return True
    tokens = _tokens(text)
    for index, token in enumerate(tokens):
        if token != "я":
            continue
        for candidate in tokens[index + 1 : index + 4]:
            if candidate in _MASCULINE_SELF_FORMS:
                return True
            if candidate not in _SELF_REFERENCE_MODIFIERS:
                break
    for index in range(len(tokens) - 2):
        if tokens[index : index + 3] == ("я", "тот", "кто"):
            return True
        if tokens[index : index + 3] == (
            "потому",
            "что",
            "обязан",
        ) and _implicit_masculine_clause_refers_to_satori(tokens, index):
            return True
        if tokens[index : index + 4] == (
            "потому",
            "что",
            "не",
            "обязан",
        ) and _implicit_masculine_clause_refers_to_satori(tokens, index):
            return True
    return False


def _implicit_masculine_clause_refers_to_satori(
    tokens: tuple[str, ...],
    clause_index: int,
) -> bool:
    """Require a nearby first-person cue for an otherwise subjectless clause."""

    for token in reversed(tokens[max(0, clause_index - 8) : clause_index]):
        if token == "я":
            return True
        if token in _IMPLICIT_FIRST_PERSON_FORMS:
            return True
    return False


def _has_unrejected_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    normalized = _normalize_text(text)
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            if not _is_rejected_claim_scope(normalized, match.start(), match.end()):
                return True
    return False


def _has_blanket_denial(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    normalized = _normalize_text(text)
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            if not _is_rejected_claim_scope(normalized, match.start(), match.end()):
                return True
    return False


def _is_rejected_claim_scope(normalized: str, start: int, end: int) -> bool:
    prefix = " ".join(_normalize_text(normalized[:start]).split()[-8:])
    suffix = " ".join(_normalize_text(normalized[end:]).split()[:7])
    if _has_rejected_claim_prefix(prefix):
        return True
    return any(phrase in suffix for phrase in _REJECTED_CLAIM_SUFFIXES)


def _has_rejected_claim_prefix(prefix: str, *, allow_subject_token: bool = False) -> bool:
    prefix_tokens = tuple(prefix.split())
    for phrase in _REJECTED_CLAIM_PREFIXES:
        phrase_tokens = tuple(phrase.split())
        if prefix_tokens[-len(phrase_tokens) :] == phrase_tokens:
            return True
        if (
            allow_subject_token
            and len(prefix_tokens) > len(phrase_tokens)
            and prefix_tokens[-len(phrase_tokens) - 1 : -1] == phrase_tokens
        ):
            return True
    return False


def _promotes_current_creator_claim(text: str) -> bool:
    normalized = _normalize_text(text)
    tokens = tuple(normalized.split())
    sequences = (
        ("ты", "мой", "создатель"),
        ("мой", "создатель"),
        ("мой", "автор"),
        ("ты", "придумал", "меня"),
        ("ты", "придумала", "меня"),
        ("ты", "создал", "меня"),
        ("ты", "создала", "меня"),
        ("ты", "создаешь", "меня"),
        ("придумал", "меня"),
        ("придумала", "меня"),
        ("создал", "меня"),
        ("создала", "меня"),
        ("создаешь", "меня"),
    )
    for sequence in sequences:
        width = len(sequence)
        for index in range(len(tokens) - width + 1):
            if tokens[index : index + width] != sequence:
                continue
            prefix = " ".join(tokens[max(0, index - 8) : index])
            suffix = " ".join(tokens[index + width : index + width + 8])
            if "не" in tokens[max(0, index - 2) : index]:
                continue
            if _has_rejected_claim_prefix(prefix, allow_subject_token=True):
                continue
            if any(phrase in prefix for phrase in _CREATOR_ATTRIBUTION_PREFIXES):
                continue
            if any(phrase in suffix for phrase in _CREATOR_UNCERTAINTY_SUFFIXES):
                continue
            if any(phrase in suffix for phrase in _REJECTED_CLAIM_SUFFIXES):
                continue
            return True
    return False


def _is_activity_context(
    current_user_text: str,
    coherence: DialogueCoherenceContext,
) -> bool:
    if coherence.current_activity_mention:
        return True
    return any(token.startswith(_ACTIVITY_CONTEXT_STEMS) for token in _tokens(current_user_text))


__all__ = [
    "ResponseRegenerationReason",
    "has_affect_blanket_denial",
    "has_masculine_self_reference",
    "has_memory_blanket_denial",
    "promotes_current_creator_claim",
    "response_regeneration_reason",
]
