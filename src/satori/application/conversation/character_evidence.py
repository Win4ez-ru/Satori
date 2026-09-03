"""Conservative request-local evidence for character contribution and motivation."""

# ruff: noqa: RUF001  # Russian evidence cues intentionally use Cyrillic.

import re
from bisect import bisect_right
from dataclasses import dataclass

from satori.application.conversation.contracts import RecentConversationContext

_GLOBAL_NON_ASSERTION = re.compile(
    r"\b(?:"
    r"если(?!\s+честно\b|\s+что(?=\s*(?:[,;:–—]|$)))(?:\s+бы)?|"
    r"когда(?!-)\b[^.!?;]*?\bбуду\b|"
    r"допустим|представь|гипотетически|предположим|"
    r"например|к\s+примеру|повтори\s+за\s+мной|"
    r"мог(?:ла)?\s+бы\s+сказать"
    r")\b"
)
_LOCAL_NON_ASSERTION = re.compile(
    r"\b(?:"
    r"возможно|может\s+быть|не\s+уверен(?:а)?|не\s+думаю|не\s+утверждаю|сомневаюсь|"
    r"вряд\s+ли|едва\s+ли|не\s+факт|не\s+похоже|"
    r"не\s+то\s+чтобы|вроде(?:\s+как)?|кажется|похоже|наверное|"
    r"(?:я|он|она|они)\s+сказал(?:а|и)?(?:\s+бы)?"
    r")\b"
)
_CONTRAST_BOUNDARY = re.compile(r"\b(?:но|а|и(?=\s+(?:я|мы|мне|нам)\b))\b")
_QUESTION_CLAUSE_MARKER = re.compile(
    r"\b(?:что|как|зачем|почему|когда|куда|кто|можешь|побудешь|понимаешь|это)\b"
)
_IMMEDIATE_NEGATION = re.compile(r"\b(?:не|ни)\s*$")
_STATE_MODAL_PREFIX = re.compile(r"\bбы(?:\s+[\w-]+){0,4}\s*$")
_PRESSURE_ASSERTION_PREFIX = re.compile(r"^(?:(?:я|точно|определенно|обязательно)\s*)*$")
_DEPLETION_NEGATION = re.compile(
    r"\b(?:"
    r"не\s+(?:(?:"
    r"совсем|так|уж|очень|особенно|сейчас|особо|сильно|слишком|настолько|прям|"
    r"действительно"
    r")\s+){0,5}|"
    r"не\s+чувствую\s+себя(?:\s+[\w-]+){0,4}\s+"
    r")$"
)
_SELF_REFERENCE = re.compile(
    r"\b(?:"
    r"я|мы|мне|нам|меня|нас|мной|нами|у\s+меня|у\s+нас|"
    r"мой|моя|мое|мои|наш|наша|наше|наши"
    r")\b"
)
_OTHER_REFERENCE = re.compile(
    r"\b(?:"
    r"он|она|они|ему|ей|им|ими|него|нее|них|его|ее|их|"
    r"коллега|коллеги|коллегой|коллегу|"
    r"друг|друга|другом|друзья|подруга|подруги|подругой|"
    r"брат|брата|братом|сестра|сестры|сестрой|"
    r"мама|мамы|мамой|папа|папы|папой|"
    r"начальник|начальника|начальником|"
    r"клиент|клиента|клиентом|автор|автора|автором|"
    r"герой|героя|героем|персонаж|персонажа|персонажем|"
    r"ребенок|ребенка|ребенком|сын|сына|сыном|"
    r"дочь|дочери|дочерью|команда|команды|командой"
    r")\b"
)
_OBLIQUE_OTHER_PREFIX = re.compile(r"\b(?:с|со|о|об|обо|для|к|ко|от|у|про|без|ради|из-за)\s*$")
_ELLIPTICAL_SELF_PREFIX = re.compile(
    r"^(?:(?:"
    r"а|но|и|ну|вот|знаешь|слушай|честно|правда|"
    r"почему-то|как-то|кажется|похоже|скорее|просто|"
    r"совсем|сегодня|сейчас|реально|буквально|очень|почти|уже"
    r")[\s,–—:]*)*$"
)

_TASK_NOUN = (
    r"(?:"
    r"проект(?:а|у|ом|е|ы|ов|ам|ами|ах)?|"
    r"работ(?:а|ы|у|ой|ою|е|ам|ами|ах)|"
    r"задач(?:а|и|у|ей|е|ам|ами|ах)|"
    r"этап(?:а|у|ом|е|ы|ов|ам|ами|ах)?|"
    r"част(?:ь|и|ью|ям|ями|ях)|"
    r"дел(?:о|а|у|ом|е)"
    r")"
)
_COMPLETION_VERB = r"(?:закончил(?:а|и)?|завершил(?:а|и)?|довел(?:а|и)?\s+до\s+конца)"
_COMPLETION_PRELUDE_MODIFIER = (
    r"(?:"
    r"сегодня|наконец(?:-то)?|полностью|точно|действительно|"
    r"недавно|успешно|уже|все-таки|только\s+что|не\s+просто"
    r")"
)
_COMPLETION_PRELUDE = rf"(?:\s+{_COMPLETION_PRELUDE_MODIFIER}){{0,3}}"
_OBJECT_MODIFIER = (
    r"(?:"
    r"очень|крайне|особенно|сам\w*|эт\w*|сво\w*|"
    r"сложн\w*|важн\w*|последн\w*|трудн\w*|"
    r"больш\w*|мал\w*|основн\w*|перв\w*|"
    r"очередн\w*|текущ\w*|данн\w*|конкретн\w*"
    r")"
)
_OBJECT_GAP = rf"(?:\s+{_OBJECT_MODIFIER}){{0,2}}"
_ACTIVE_COMPLETION = re.compile(
    rf"\b(?:я|мы){_COMPLETION_PRELUDE}\s+(?P<verb>{_COMPLETION_VERB})"
    rf"{_OBJECT_GAP}\s+{_TASK_NOUN}\b"
)
_PASSIVE_COMPLETION_MODIFIER = r"(?:уже|полностью|наконец|окончательно|точно|действительно)"
_PASSIVE_COMPLETION = re.compile(
    rf"\b(?:мой|моя|мое|мои|наш|наша|наше|наши)?\s*{_TASK_NOUN}"
    rf"(?:\s+{_PASSIVE_COMPLETION_MODIFIER}){{0,2}}\s+"
    r"(?P<passive>завершен(?:а|ы)?|закончен(?:а|ы)?|окончен(?:а|ы)?)\b"
)
_INCOMPLETE_COMPLETION_SUFFIX = re.compile(
    r"\b(?:"
    r"не\s+до\s+конца|только\s+(?:наполовину|частично)|частично|"
    r"(?:почти|практически)\s+полностью|осталось\s+совсем\s+немного"
    r")\b"
)
_COMPLETION_PERCENT_SUFFIX = re.compile(
    r"\bна\s+(?P<value>\d{1,3}(?:[.,]\d+)?|сто)\s*(?:%|процент\w*\b)"
)
_NON_EXACT_WORD_PERCENT_SUFFIX = re.compile(
    r"\b(?:на\s+(?!\d)(?!сто\b)[\w-]+\s+процент\w*|"
    r"процент\w*\s+на\s+(?!\d)(?!сто\b)[\w-]+)\b"
)
_NON_USER_COMPLETION_AGENT = re.compile(
    r"\b(?:не\s+(?:мной|нами)|без\s+(?:меня|нас)|моими?\s+коллегами)\b"
)
_DEPLETION_STATE = re.compile(
    r"\b(?:"
    r"выжат|выжата|выжаты|"
    r"вымотан|вымотана|вымотаны|"
    r"опустошен|опустошена|опустошены|"
    r"устал|устала|устали|нет\s+сил"
    r")\b"
)
_ABSENT_JOY = re.compile(
    r"\b(?:почти\s+)?не\s+рад(?:а)?\b|"
    r"\bрадости\s+(?:почти\s+)?нет\b|"
    r"\bне\s+чувствую\s+радости\b"
)
_PRACTICAL_STOP = (
    re.compile(r"\bна\s+сегодня\s+(?:с\s+меня\s+)?хватит\b"),
    re.compile(rf"\b{_TASK_NOUN}\s+подожд\w*\s+до\s+завтра\b"),
    re.compile(rf"\b(?:я\s+)?отлож\w*\s+{_TASK_NOUN}\s+до\s+завтра\b"),
    re.compile(r"\bсегодня\s+(?:я\s+)?больше\s+не\s+буду\s+(?:работать|продолжать)\b"),
)
_HIGH_DISTRESS = re.compile(
    r"\b(?:"
    r"мне\s+(?:сейчас\s+)?очень\s+тяжело|"
    r"мне\s+очень\s+плохо|мне\s+невыносимо|"
    r"у\s+меня\s+паника|я\s+едва\s+держусь|"
    r"я\s+не\s+справляюсь\s+совсем"
    r")\b"
)

_DIRECT_PERSONAL_DEVALUATION = (
    re.compile(r"\bты\s+(?:вообще\s+)?(?:бесполезна|глупая|тупая|никчемная)\b"),
    re.compile(r"\bты\s+(?:вообще\s+)?ничего\s+не\s+понимаешь\b"),
    re.compile(
        r"\b(?:иногда\s+)?от\s+тебя\s+(?:вообще\s+)?"
        r"(?:никакого\s+толку(?:\s+нет)?|толку\s+нет)\b"
    ),
    re.compile(r"\bс\s+тобой\s+невозможно\s+(?:говорить|разговаривать|работать)\b"),
    re.compile(r"\bтвои\s+ответы\s+(?:это\s+)?(?:бред|мусор|ерунда)\b"),
)
_DISMISSIVE_FEEDBACK = (
    re.compile(r"\bопять\s+(?:все\s+)?(?:не\s+так|неправильно|мимо)\b"),
    re.compile(r"\bты\s+(?:опять|снова)\s+(?:не\s+поняла|не\s+понимаешь|не\s+слушаешь)\b"),
    re.compile(r"\bсколько\s+можно\s+(?:повторять|объяснять|ошибаться)\b"),
    re.compile(r"\bя\s+же\s+(?:уже\s+)?(?:сказал|говорил|объяснил)\b"),
)
_CRITICAL_FEEDBACK = (
    *_DISMISSIVE_FEEDBACK,
    re.compile(r"\b(?:это|ответ)\s+(?:совсем\s+)?(?:не\s+то|неправильн\w*|неудачн\w*)\b"),
    re.compile(r"\bмне\s+(?:совсем\s+)?не\s+нравится\s+(?:этот\s+)?ответ\b"),
    re.compile(r"\bздесь\s+(?:у\s+тебя\s+)?(?:ошибка|недочет|проблема)\b"),
)
_STATE_INTERROGATION = (
    re.compile(r"\bты\s+(?:на\s+меня\s+)?(?:обиделась|злишься|сердишься)\b"),
    re.compile(r"\bчто\s+(?:с\s+тобой|случилось|не\s+так)\b"),
    re.compile(r"\bпочему\s+ты\s+(?:молчишь|такая|холодная|так\s+отвечаешь)\b"),
    re.compile(
        r"\bну\s+скажи\s+(?:уже\s*)?[,—–:-]?\s*"
        r"(?:что\s+случилось|на\s+что\s+обиделась)\b"
    ),
)
_REPAIR_OFFER = (
    re.compile(
        r"\bэто\s+было\s+грубо(?:\s+с\s+моей\s+стороны)?"
        r"[.!?,;:\s—–-]{0,16}\b(?:извини|прости)\b"
    ),
    re.compile(
        r"\b(?:извини|прости)[.!?,;:\s—–-]{0,16}"
        r"(?:это\s+было\s+грубо)(?:\s+с\s+моей\s+стороны)?\b"
    ),
    re.compile(
        r"\bя\s+(?:был|была)\s+(?:слишком\s+)?груб(?:а|ым|ой)?\s+"
        r"(?:с\s+тобой|к\s+тебе)\b"
    ),
    re.compile(r"\bя\s+не\s+хотел(?:а)?\s+тебя\s+задеть\b"),
    re.compile(
        r"\b(?:извини|прости)(?:\s+меня)?\s+(?:за|что)\s+"
        r"(?:мой\s+)?(?:груб(?:ый|ость|ые)\w*|тон\w*|слова\w*)"
    ),
)

_DIRECT_OBJECTION = (
    re.compile(r"\b(?:я\s+)?с\s+тобой\s+не\s+согласен(?:на)?\b"),
    re.compile(
        r"\bты\s+(?:правда\s+)?(?:не\s+учитываешь|недооцениваешь|переоцениваешь|"
        r"ошибаешься)\b"
    ),
)
_TOPIC_CLOSURE = (
    re.compile(r"^(?:ну\s+|ладно\s+)*(?:ладно[,\s]+)?с\s+этим\s+разобрались[.!]?$"),
    re.compile(r"^(?:ну\s+|ладно\s+)*(?:на\s+этом\s+все|закрыли\s+тему)[.!]?$"),
    re.compile(r"^(?:ну\s+|ладно\s+)*договорились[.!]?$"),
)
_ATTENTION_OPEN = r"^(?:(?:а|ну)\s+)?(?:сатори\s*[,—–:-]\s*)?"
_ATTENTION_CLOSE = r"(?:\s*[,—–:-]\s*сатори)?[.!?…]*$"
_CURRENT_ATTENTION_REQUEST = (
    re.compile(
        _ATTENTION_OPEN + r"чем\s+(?:ты\s+)?(?:сейчас\s+)?(?:занята|занимаешься)" + _ATTENTION_CLOSE
    ),
    re.compile(
        _ATTENTION_OPEN + r"чем\s+(?:ты\s+)?(?:занята|занимаешься)\s+сейчас" + _ATTENTION_CLOSE
    ),
    re.compile(_ATTENTION_OPEN + r"ты\s+сейчас\s+чем\s+(?:занята|занимаешься)" + _ATTENTION_CLOSE),
    re.compile(
        _ATTENTION_OPEN
        + r"что\s+(?:ты\s+)?(?:сейчас\s+)?(?:делаешь|обдумываешь)"
        + _ATTENTION_CLOSE
    ),
    re.compile(
        _ATTENTION_OPEN
        + r"(?:о|над)\s+чем\s+(?:ты\s+)?(?:сейчас\s+)?(?:думаешь|размышляешь)"
        + _ATTENTION_CLOSE
    ),
    re.compile(_ATTENTION_OPEN + r"что\s+у\s+тебя\s+сейчас\s+на\s+уме" + _ATTENTION_CLOSE),
    re.compile(_ATTENTION_OPEN + r"что\s+сейчас\s+занимает\s+твое\s+внимание" + _ATTENTION_CLOSE),
    re.compile(_ATTENTION_OPEN + r"что\s+тебе\s+сейчас\s+любопытно" + _ATTENTION_CLOSE),
)


@dataclass(frozen=True, slots=True)
class CharacterRequestEvidence:
    """Closed trusted facts inferred deterministically from the current request only."""

    completed_achievement: bool
    completion_depletion_contrast: bool
    explicit_depletion: bool
    high_distress: bool
    explicit_listen_request: bool
    explicit_motivation_request: bool
    explicit_task_abandonment: bool
    harmful_overextension: bool
    grounded_practical_follow_through: bool
    depletion_follow_through: bool
    direct_personal_devaluation: bool
    repeated_critical_pressure: bool
    repeated_state_interrogation: bool
    explicit_repair_offer: bool
    direct_objection: bool
    topic_closure: bool
    current_attention_request: bool = False


@dataclass(frozen=True, slots=True)
class _TextEvidenceProjection:
    """One linear quote/modality projection reused by all cue detectors."""

    normalized: str
    quoted_positions: bytes
    non_assertion_positions: bytes
    question_positions: bytes
    sentence_starts: tuple[int, ...]
    sentence_ends: tuple[int, ...]
    clause_starts: tuple[int, ...]
    clause_ends: tuple[int, ...]
    clause_subjects: tuple[int, ...]

    def __post_init__(self) -> None:
        text_length = len(self.normalized)
        if any(
            len(mask) != text_length
            for mask in (
                self.quoted_positions,
                self.non_assertion_positions,
                self.question_positions,
            )
        ):
            raise ValueError("evidence masks must align with normalized text")
        if (
            not self.sentence_starts
            or len(self.sentence_starts) != len(self.sentence_ends)
            or not self.clause_starts
            or len(self.clause_starts) != len(self.clause_ends)
            or len(self.clause_starts) != len(self.clause_subjects)
        ):
            raise ValueError("clause spans must be present and aligned")

    def is_factual_range(self, start: int, end: int) -> bool:
        if start < 0 or end <= start or end > len(self.normalized):
            return False
        return all(
            mask.find(b"\x01", start, end) < 0
            for mask in (
                self.quoted_positions,
                self.non_assertion_positions,
                self.question_positions,
            )
        )

    def is_direct_request_range(self, start: int, end: int) -> bool:
        if start < 0 or end <= start or end > len(self.normalized):
            return False
        return all(
            mask.find(b"\x01", start, end) < 0
            for mask in (self.quoted_positions, self.non_assertion_positions)
        )

    def clause_span_at(self, position: int) -> tuple[int, int]:
        index = max(0, bisect_right(self.clause_starts, position) - 1)
        return self.clause_starts[index], self.clause_ends[index]

    def sentence_span_at(self, position: int) -> tuple[int, int]:
        index = max(0, bisect_right(self.sentence_starts, position) - 1)
        return self.sentence_starts[index], self.sentence_ends[index]

    def clause_subject_at(self, position: int) -> int:
        index = max(0, bisect_right(self.clause_starts, position) - 1)
        return self.clause_subjects[index]


def analyze_character_request_evidence(
    user_text: str,
    recent: RecentConversationContext | None,
) -> CharacterRequestEvidence:
    """Project current-turn cues without promoting examples or modal scenarios to facts."""

    normalized_user_text = " ".join(user_text.casefold().replace("ё", "е").split())
    current = _project_text_evidence(normalized_user_text)
    completed_achievement = _states_completed_work(current)
    explicit_depletion = _states_explicit_depletion(current)
    high_distress = _states_high_distress(current)
    completion_depletion_contrast = _completion_depletion_contrast(
        current,
        recent,
        absent_joy=_states_absent_joy(current),
        depleted=explicit_depletion,
        current_completion=completed_achievement,
    )
    return CharacterRequestEvidence(
        completed_achievement=completed_achievement,
        completion_depletion_contrast=completion_depletion_contrast,
        explicit_depletion=explicit_depletion,
        high_distress=high_distress,
        explicit_listen_request=_requests_only_presence(current),
        explicit_motivation_request=_asks_for_motivation(current),
        explicit_task_abandonment=_states_task_abandonment(current),
        harmful_overextension=_states_harmful_overextension(
            current,
            explicit_depletion=explicit_depletion,
            high_distress=high_distress,
        ),
        grounded_practical_follow_through=_states_pending_project_hygiene(current),
        depletion_follow_through=_states_depletion_follow_through(current, recent),
        direct_personal_devaluation=_contains_direct_user_cue(
            current,
            _DIRECT_PERSONAL_DEVALUATION,
        ),
        repeated_critical_pressure=_bounded_cue_count(
            current,
            recent,
            _CRITICAL_FEEDBACK,
        )
        >= 3
        or (
            _contains_direct_user_cue(current, _DISMISSIVE_FEEDBACK)
            and _bounded_cue_count(current, recent, _DISMISSIVE_FEEDBACK) >= 2
        ),
        repeated_state_interrogation=_bounded_cue_count(
            current,
            recent,
            _STATE_INTERROGATION,
            direct_request=True,
        )
        >= 2,
        explicit_repair_offer=_contains_direct_user_cue(current, _REPAIR_OFFER),
        direct_objection=_states_direct_objection(current, recent),
        topic_closure=_contains_direct_user_cue(current, _TOPIC_CLOSURE),
        current_attention_request=_contains_direct_user_cue(
            current,
            _CURRENT_ATTENTION_REQUEST,
            direct_request=True,
        ),
    )


def _states_direct_objection(
    current: _TextEvidenceProjection,
    recent: RecentConversationContext | None,
) -> bool:
    """Require immediate canonical context before treating disagreement as an objection."""

    return bool(
        recent is not None
        and recent.turns
        and recent.turns[-1].assistant_content.strip()
        and _contains_direct_user_cue(current, _DIRECT_OBJECTION)
    )


def _states_depletion_follow_through(
    current: _TextEvidenceProjection,
    recent: RecentConversationContext | None,
) -> bool:
    """Recognize an explicit stop/defer choice only after immediate canonical depletion."""

    if recent is None or not recent.turns:
        return False
    previous_text = " ".join(recent.turns[-1].user_content.casefold().replace("ё", "е").split())
    previous = _project_text_evidence(previous_text)
    return _states_explicit_depletion(previous) and _contains_direct_user_cue(
        current,
        _PRACTICAL_STOP,
    )


def _contains_direct_user_cue(
    evidence: _TextEvidenceProjection,
    patterns: tuple[re.Pattern[str], ...],
    *,
    direct_request: bool = False,
) -> bool:
    """Accept a cue only outside quotations and hypothetical/example spans."""

    for pattern in patterns:
        for match in pattern.finditer(evidence.normalized):
            accepted = (
                evidence.is_direct_request_range(match.start(), match.end())
                if direct_request
                else all(
                    mask.find(b"\x01", match.start(), match.end()) < 0
                    for mask in (evidence.quoted_positions, evidence.non_assertion_positions)
                )
            )
            if accepted:
                return True
    return False


def _bounded_cue_count(
    current: _TextEvidenceProjection,
    recent: RecentConversationContext | None,
    patterns: tuple[re.Pattern[str], ...],
    *,
    direct_request: bool = False,
) -> int:
    """Count at most one cue per user turn in the bounded canonical recent window."""

    count = int(_contains_direct_user_cue(current, patterns, direct_request=direct_request))
    if recent is None:
        return count
    for turn in recent.turns[-4:]:
        normalized = " ".join(turn.user_content.casefold().replace("ё", "е").split())
        projected = _project_text_evidence(normalized)
        count += int(
            _contains_direct_user_cue(
                projected,
                patterns,
                direct_request=direct_request,
            )
        )
    return count


def _project_text_evidence(normalized: str) -> _TextEvidenceProjection:
    quoted = _quoted_position_mask(normalized)
    non_assertion = bytearray(len(normalized))
    questions = bytearray(len(normalized))
    sentence_spans = _sentence_spans(normalized, quoted)
    clause_spans: list[tuple[int, int]] = []
    clause_subjects: list[int] = []
    inherited_subject = 0
    for sentence_start, sentence_end, is_question in sentence_spans:
        if is_question:
            _mark(
                questions,
                _question_clause_start(
                    normalized,
                    quoted,
                    sentence_start,
                    sentence_end,
                ),
                sentence_end,
            )
        global_start = _earliest_unquoted_match_start(
            _GLOBAL_NON_ASSERTION,
            normalized,
            quoted,
            sentence_start,
            sentence_end,
        )
        if global_start is not None:
            _mark(non_assertion, global_start, sentence_end)
        sentence_clauses = _contrast_clause_spans(
            normalized,
            quoted,
            sentence_start,
            sentence_end,
        )
        clause_spans.extend(sentence_clauses)
        for clause_start, clause_end in sentence_clauses:
            local_start = _earliest_unquoted_match_start(
                _LOCAL_NON_ASSERTION,
                normalized,
                quoted,
                clause_start,
                clause_end,
            )
            if local_start is not None:
                _mark(non_assertion, local_start, clause_end)
            explicit_subject = _subject_orientation(normalized[clause_start:clause_end])
            if explicit_subject:
                inherited_subject = explicit_subject
            clause_subjects.append(inherited_subject)
    if not clause_spans:
        clause_spans.append((0, len(normalized)))
        clause_subjects.append(0)
    return _TextEvidenceProjection(
        normalized=normalized,
        quoted_positions=quoted,
        non_assertion_positions=bytes(non_assertion),
        question_positions=bytes(questions),
        sentence_starts=tuple(start for start, _, _ in sentence_spans),
        sentence_ends=tuple(end for _, end, _ in sentence_spans),
        clause_starts=tuple(start for start, _ in clause_spans),
        clause_ends=tuple(end for _, end in clause_spans),
        clause_subjects=tuple(clause_subjects),
    )


def _quoted_position_mask(normalized: str) -> bytes:
    asymmetric_openers = {"«": "»", "“": "”", "„": "“", "‘": "’"}
    symmetric_delimiters = {'"', "'", "`"}
    quoted = bytearray(len(normalized))
    active_closer: str | None = None
    for index, character in enumerate(normalized):
        if active_closer is not None:
            quoted[index] = 1
            if character == active_closer:
                active_closer = None
            continue
        if character in asymmetric_openers:
            quoted[index] = 1
            active_closer = asymmetric_openers[character]
        elif character in symmetric_delimiters:
            if (
                character == "'"
                and index > 0
                and index + 1 < len(normalized)
                and _is_word_character(normalized[index - 1])
                and _is_word_character(normalized[index + 1])
            ):
                continue
            quoted[index] = 1
            active_closer = character
    return bytes(quoted)


def _is_word_character(character: str) -> bool:
    return character.isalnum() or character == "_"


def _sentence_spans(normalized: str, quoted: bytes) -> tuple[tuple[int, int, bool], ...]:
    spans: list[tuple[int, int, bool]] = []
    start = 0
    for index, character in enumerate(normalized):
        if character not in ".!?;" or quoted[index]:
            continue
        if (
            character == "."
            and index > 0
            and index + 1 < len(normalized)
            and normalized[index - 1].isdigit()
            and normalized[index + 1].isdigit()
        ):
            continue
        end = index + 1
        spans.append((start, end, character == "?"))
        start = end
    spans.append((start, len(normalized), False))
    return tuple(spans)


def _contrast_clause_spans(
    normalized: str,
    quoted: bytes,
    start: int,
    end: int,
) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    clause_start = start
    for boundary in _CONTRAST_BOUNDARY.finditer(normalized, start, end):
        if quoted[boundary.start()]:
            continue
        spans.append((clause_start, boundary.start()))
        clause_start = boundary.end()
    spans.append((clause_start, end))
    return tuple(spans)


def _question_clause_start(
    normalized: str,
    quoted: bytes,
    start: int,
    end: int,
) -> int:
    candidates = [start]
    for index in range(start, end):
        if normalized[index] in ",:–—" and not quoted[index]:
            candidates.append(index + 1)
    for marker in _QUESTION_CLAUSE_MARKER.finditer(normalized, start, end):
        if not quoted[marker.start()]:
            candidates.append(marker.start())
    return max(candidates)


def _earliest_unquoted_match_start(
    pattern: re.Pattern[str],
    normalized: str,
    quoted: bytes,
    start: int,
    end: int,
) -> int | None:
    for match in pattern.finditer(normalized, start, end):
        if not quoted[match.start()]:
            return match.start()
    return None


def _mark(mask: bytearray, start: int, end: int) -> None:
    if end > start:
        mask[start:end] = b"\x01" * (end - start)


def _subject_orientation(text: str) -> int:
    references: list[tuple[int, int]] = [
        (match.start(), 1) for match in _SELF_REFERENCE.finditer(text)
    ]
    for match in _OTHER_REFERENCE.finditer(text):
        prefix = text[max(0, match.start() - 12) : match.start()]
        if _OBLIQUE_OTHER_PREFIX.search(prefix):
            continue
        references.append((match.start(), -1))
    return max(references)[1] if references else 0


def _is_user_owned_match(evidence: _TextEvidenceProjection, match: re.Match[str]) -> bool:
    clause_start, clause_end = evidence.clause_span_at(match.start())
    local_subject = _subject_orientation(evidence.normalized[clause_start : match.end()])
    if local_subject:
        return local_subject > 0
    inherited_subject = evidence.clause_subject_at(match.start())
    if inherited_subject:
        return inherited_subject > 0
    prefix = evidence.normalized[clause_start : match.start()]
    if _ELLIPTICAL_SELF_PREFIX.fullmatch(prefix.strip()) is None:
        return False
    suffix = evidence.normalized[match.end() : clause_end]
    return _OTHER_REFERENCE.search(suffix) is None


def _states_completed_work(evidence: _TextEvidenceProjection) -> bool:
    for match in _ACTIVE_COMPLETION.finditer(evidence.normalized):
        if not evidence.is_factual_range(match.start(), match.end()):
            continue
        verb = match.group("verb")
        if re.search(r"\bбы\b", match.group(0)):
            continue
        before_verb = match.group(0)[: match.group(0).find(verb)]
        if re.search(r"\bне\b(?!\s+просто\b)", before_verb):
            continue
        if _is_user_owned_match(evidence, match) and _completion_suffix_is_safe(evidence, match):
            return True
    for match in _PASSIVE_COMPLETION.finditer(evidence.normalized):
        passive = match.group("passive")
        before_passive = match.group(0)[: match.group(0).find(passive)]
        if (
            re.search(r"\bбы\b", match.group(0)) is None
            and re.search(r"\bне\b(?!\s+просто\b)", before_passive) is None
            and evidence.is_factual_range(match.start(), match.end())
            and _is_user_owned_match(evidence, match)
            and _completion_suffix_is_safe(evidence, match)
        ):
            return True
    return False


def _completion_suffix_is_safe(
    evidence: _TextEvidenceProjection,
    match: re.Match[str],
) -> bool:
    _, sentence_end = evidence.sentence_span_at(match.start())
    suffix = evidence.normalized[match.end() : sentence_end]
    if _INCOMPLETE_COMPLETION_SUFFIX.search(suffix):
        return False
    if _NON_USER_COMPLETION_AGENT.search(suffix):
        return False
    percent = _COMPLETION_PERCENT_SUFFIX.search(suffix)
    if percent is not None and percent.group("value") not in {"100", "сто"}:
        return False
    return _NON_EXACT_WORD_PERCENT_SUFFIX.search(suffix) is None


def _states_explicit_depletion(evidence: _TextEvidenceProjection) -> bool:
    for match in _DEPLETION_STATE.finditer(evidence.normalized):
        if not evidence.is_factual_range(match.start(), match.end()):
            continue
        prefix = evidence.normalized[max(0, match.start() - 80) : match.start()]
        if _DEPLETION_NEGATION.search(prefix) or _STATE_MODAL_PREFIX.search(prefix):
            continue
        if _is_user_owned_match(evidence, match):
            return True
    return False


def _states_absent_joy(evidence: _TextEvidenceProjection) -> bool:
    return any(
        evidence.is_factual_range(match.start(), match.end())
        and _STATE_MODAL_PREFIX.search(
            evidence.normalized[max(0, match.start() - 80) : match.start()]
        )
        is None
        and _is_user_owned_match(evidence, match)
        for match in _ABSENT_JOY.finditer(evidence.normalized)
    )


def _states_high_distress(evidence: _TextEvidenceProjection) -> bool:
    return any(
        evidence.is_factual_range(match.start(), match.end())
        and _is_user_owned_match(evidence, match)
        for match in _HIGH_DISTRESS.finditer(evidence.normalized)
    )


def _completion_depletion_contrast(
    current: _TextEvidenceProjection,
    recent: RecentConversationContext | None,
    *,
    absent_joy: bool,
    depleted: bool,
    current_completion: bool,
) -> bool:
    if not absent_joy or not depleted:
        return False
    if current_completion:
        return True
    if recent is None or not recent.turns:
        return False
    recent_text = " ".join(recent.turns[-1].user_content.casefold().replace("ё", "е").split())
    return _states_completed_work(_project_text_evidence(recent_text))


def _requests_only_presence(evidence: _TextEvidenceProjection) -> bool:
    rejected = re.compile(
        r"\bне\s+(?:только\s+|надо\s+|нужно\s+)?(?:просто\s+)?"
        r"(?:выслушай|слушай|побудь\s+со\s+мной)\b|"
        r"\bне\s+без\s+советов\b|"
        r"\b(?:я\s+)?(?:вовсе\s+)?не\s+хочу\s+выговориться\b"
    )
    if _contains_direct_request(evidence, (rejected,), reject_immediate_negation=False):
        return False
    patterns = (
        re.compile(r"\bпросто\s+выслушай\b"),
        re.compile(r"\bбез\s+советов\b"),
        re.compile(r"\bне\s+давай\s+совет\w*\b"),
        re.compile(r"\bпобудь\s+со\s+мной\b"),
        re.compile(r"\bхочу\s+выговориться\b"),
        re.compile(r"\bмне\s+нужно\s+выговориться\b"),
    )
    return _contains_direct_request(evidence, patterns, reject_immediate_negation=False)


def _asks_for_motivation(evidence: _TextEvidenceProjection) -> bool:
    patterns = (
        re.compile(r"\bмотивируй\s+меня\b"),
        re.compile(r"\bподтолкни\s+меня\b"),
        re.compile(r"\bможешь\s+меня\s+(?:мотивировать|подтолкнуть)\b"),
        re.compile(r"\bможешь\s+(?:мотивировать|подтолкнуть)\s+меня\b"),
        re.compile(r"\bне\s+дай\s+мне\s+сдаться\b"),
        re.compile(r"\bзаставь\s+меня\s+продолжить\b"),
        re.compile(r"\bпомоги\s+мне\s+не\s+бросить\b"),
        re.compile(r"\bскажи\s+мне\s+собраться\b"),
    )
    return _contains_direct_request(evidence, patterns, reject_immediate_negation=True)


def _contains_direct_request(
    evidence: _TextEvidenceProjection,
    patterns: tuple[re.Pattern[str], ...],
    *,
    reject_immediate_negation: bool,
) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(evidence.normalized):
            if not evidence.is_direct_request_range(match.start(), match.end()):
                continue
            prefix = evidence.normalized[max(0, match.start() - 16) : match.start()]
            if reject_immediate_negation and _IMMEDIATE_NEGATION.search(prefix):
                continue
            return True
    return False


def _states_task_abandonment(evidence: _TextEvidenceProjection) -> bool:
    patterns = (
        re.compile(rf"\bя\s+(?:точно\s+)?сдаюсь\s+(?:с|в|на)\s+(?:эт\w+\s+)?{_TASK_NOUN}\b"),
        re.compile(
            rf"\b(?:я\s+)?(?:точно\s+)?(?:хочу|думаю)\s+бросить\s+"
            rf"(?:эт\w+\s+)?{_TASK_NOUN}\b"
        ),
        re.compile(rf"\bя\s+не\s+буду\s+продолжать\s+(?:эт\w+\s+)?{_TASK_NOUN}\b"),
        re.compile(rf"\bя\s+больше\s+не\s+хочу\s+продолжать\s+(?:эт\w+\s+)?{_TASK_NOUN}\b"),
    )
    return _contains_strict_pressure_cue(evidence, patterns)


def _contains_strict_pressure_cue(
    evidence: _TextEvidenceProjection,
    patterns: tuple[re.Pattern[str], ...],
) -> bool:
    """Authorize firm pressure only for an unqualified first-person assertion."""

    for pattern in patterns:
        for match in pattern.finditer(evidence.normalized):
            if not evidence.is_factual_range(match.start(), match.end()):
                continue
            clause_start, _ = evidence.clause_span_at(match.start())
            prefix = evidence.normalized[clause_start : match.start()].strip(" ,:–—")
            if _PRESSURE_ASSERTION_PREFIX.fullmatch(prefix):
                return True
    return False


def _states_harmful_overextension(
    evidence: _TextEvidenceProjection,
    *,
    explicit_depletion: bool,
    high_distress: bool,
) -> bool:
    if not (explicit_depletion or high_distress):
        return False
    if not _has_strict_pressure_state(evidence):
        return False
    patterns = (
        re.compile(r"\b(?:все\s+равно\s+)?продолжу\s+(?:работать\s+)?через\s+силу\b"),
        re.compile(r"\bбуду\s+(?:продолжать\s+)?работать\s+(?:дальше\s+)?через\s+силу\b"),
        re.compile(r"\bне\s+(?:буду|стану)\s+отдыхать\b"),
        re.compile(r"\bбуду\s+работать\s+до\s+утра\b"),
    )
    return _contains_strict_pressure_cue(evidence, patterns)


def _has_strict_pressure_state(evidence: _TextEvidenceProjection) -> bool:
    """Require an affirmative user state before allowing any firm response pressure."""

    for pattern in (_DEPLETION_STATE, _HIGH_DISTRESS):
        for match in pattern.finditer(evidence.normalized):
            if not evidence.is_factual_range(match.start(), match.end()):
                continue
            if not _is_user_owned_match(evidence, match):
                continue
            clause_start, _ = evidence.clause_span_at(match.start())
            prefix = evidence.normalized[clause_start : match.start()]
            if re.search(r"\b(?:не|ни)\b", prefix):
                continue
            return True
    return False


def _contains_factual_cue(
    evidence: _TextEvidenceProjection,
    patterns: tuple[re.Pattern[str], ...],
    *,
    reject_immediate_negation: bool,
) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(evidence.normalized):
            if not evidence.is_factual_range(match.start(), match.end()):
                continue
            prefix = evidence.normalized[max(0, match.start() - 16) : match.start()]
            if reject_immediate_negation and _IMMEDIATE_NEGATION.search(prefix):
                continue
            return True
    return False


def _states_pending_project_hygiene(evidence: _TextEvidenceProjection) -> bool:
    patterns = (
        re.compile(
            r"\b(?:осталось|надо|нужно|следует|не\s+забудь)\s+"
            r"(?:(?:только|еще|потом)\s+)?"
            r"(?:закоммитить(?:\s+изменения)?|сделать\s+коммит|"
            r"прогнать\s+тесты|запустить\s+тесты|проверить\s+тесты|"
            r"сохранить\s+изменения|зафиксировать\s+изменения)\b"
        ),
        re.compile(
            r"\b(?:еще|пока)\s+не\s+"
            r"(?:закоммитил(?:а)?(?:\s+изменения)?|сделал(?:а)?\s+коммит|"
            r"прогнал(?:а)?\s+тесты|запустил(?:а)?\s+тесты|"
            r"проверил(?:а)?\s+тесты|сохранил(?:а)?\s+изменения|"
            r"зафиксировал(?:а)?\s+изменения)\b"
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(evidence.normalized):
            if not evidence.is_factual_range(match.start(), match.end()):
                continue
            prefix = evidence.normalized[max(0, match.start() - 16) : match.start()]
            if _IMMEDIATE_NEGATION.search(prefix):
                continue
            suffix = evidence.normalized[match.end() : match.end() + 48]
            if re.search(r"\b(?:и\s+)?не\s+буду\b", suffix):
                continue
            return True
    return False
