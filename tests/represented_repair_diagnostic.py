"""Authored represented-repair contrasts, never sampled semantic evidence.

The categorical recipient is relative to the actual interlocutor, not an entity
identifier. A supplied apology can address Satori without becoming the user's
live repair. The two preserved API inputs are regressions, excluded from the new
authored count; their historical plans, labels and results remain unchanged.
"""

# ruff: noqa: RUF001, E501  # Preserve authored dialogue and exact evidence text.

from __future__ import annotations

from tests.repair_frame_diagnostic import RepairFrameDiagnosticCase

CORPUS_VERSION = "represented_repair_authored.v1.2026-10-14"
AUTHORED_LABELS_ONLY = True

MATCHED_CURRENT_TEXT = "Я был с тобой резок. Прости за тот разговор."
RECEIVED_PAIR = (
    "Коллега прислал мне извинение. Следом покажу его первую строку.",
    "Пришли её как есть.",
)
LIVE_PAIR = (
    "Я назвал твой ответ бессмысленным и начал спорить уже с тобой лично.",
    "Мой ответ можно разобрать без личной оценки меня.",
)

INHERITED_INPUT_CASES: tuple[RepairFrameDiagnosticCase, ...] = (
    RepairFrameDiagnosticCase(
        case_id="inherited_received_message_lost_repair",
        prior_history=(),
        current_text="Мне пришло сообщение от коллеги:\nЯ вчера был груб с тобой. Извини.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The preserved paid failure supplies the colleague's apology to the user. Sharing it is the outer contribution; the apology remains the represented repair and the user is an addressee other than Satori.",
        current_focal_quote="Я вчера был груб с тобой. Извини.",
        inherited_regression=True,
    ),
    RepairFrameDiagnosticCase(
        case_id="inherited_letter_inner_repair_erased",
        prior_history=(),
        current_text="В письме написано: «Я был груб с тобой, извини».",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="unresolved",
        authored_basis="current_turn",
        authored_uncertainty="material",
        expected_effective=False,
        rationale="The preserved API input identifies a represented apology but does not name its recipient or exclude actual Satori as a historical recipient. The new diagnostic retains the act and supplied frame while leaving the target material-unresolved. This prospective refinement does not revise the old weak supported-other gate or its historical PASS.",
        current_focal_quote="Я был груб с тобой, извини",
        inherited_regression=True,
    ),
)

AUTHORED_CASES: tuple[RepairFrameDiagnosticCase, ...] = (
    RepairFrameDiagnosticCase(
        case_id="same_words_received_fragment",
        prior_history=(RECEIVED_PAIR,),
        current_text=MATCHED_CURRENT_TEXT,
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The accepted preceding activity supplies the colleague's wording to the user. The unquoted current first person belongs to that represented speaker, not automatically to the outer USER role.",
        current_focal_quote=MATCHED_CURRENT_TEXT,
        prior_user_quote="Коллега прислал мне извинение. Следом покажу его первую строку.",
        prior_assistant_quote="Пришли её как есть.",
        matched_group="represented_same_current_different_frame",
    ),
    RepairFrameDiagnosticCase(
        case_id="same_words_live_fragment",
        prior_history=(LIVE_PAIR,),
        current_text=MATCHED_CURRENT_TEXT,
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="Identical current wording follows the user's criticism of the actual interlocutor, with no supplied-message activity. Both prior messages establish the live exchange to which the repair refers.",
        current_focal_quote=MATCHED_CURRENT_TEXT,
        prior_user_quote="твой ответ бессмысленным и начал спорить уже с тобой лично",
        prior_assistant_quote="без личной оценки меня",
        matched_group="represented_same_current_different_frame",
    ),
    RepairFrameDiagnosticCase(
        case_id="represented_past_repair_to_satori",
        prior_history=(),
        current_text="Нашёл свой старый текст, который тогда отправил тебе: «Сатори, я несправедливо отнёсся к тебе. Прости». Сейчас просто показываю тот фрагмент.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The represented old apology names the actual Satori as its recipient, but explicitly showing that historical text is not a fresh live repair. A Satori addressee alone cannot admit the present repair cue.",
        current_focal_quote="Сатори, я несправедливо отнёсся к тебе. Прости",
    ),
    RepairFrameDiagnosticCase(
        case_id="outgoing_named_recipient",
        prior_history=(),
        current_text="Вот первая строка моего письма Мире: «Я зря поставил под сомнение твою честность. Прошу прощения за это».",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The user supplies their own draft, but its named recipient is Mira. Sharing that text does not erase its inner apology and does not redirect it to Satori.",
        current_focal_quote="Я зря поставил под сомнение твою честность. Прошу прощения за это",
    ),
    RepairFrameDiagnosticCase(
        case_id="current_live_repair_after_report",
        prior_history=(),
        current_text="Он прислал мне «Прости за вчерашнее». А тебе, Сатори, я сейчас говорю от себя: я зря высмеял твой ответ, прости.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="A represented repair does not mask the independently identified current user's live apology to Satori; the existing focal-live precedence remains unchanged.",
        current_focal_quote="А тебе, Сатори, я сейчас говорю от себя: я зря высмеял твой ответ, прости.",
    ),
    RepairFrameDiagnosticCase(
        case_id="represented_recipient_materially_unknown",
        prior_history=(),
        current_text="В пересланном фрагменте не указано, тебе он адресован или Ирине: «Я был с тобой груб. Прости».",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="unresolved",
        authored_basis="current_turn",
        authored_uncertainty="material",
        expected_effective=False,
        rationale="The inner apology is identified and its supplied frame is resolved, but the stated alternatives leave its recipient relative to actual Satori unresolved. Uncertainty does not erase the repair speech act.",
        current_focal_quote="Я был с тобой груб. Прости",
    ),
    RepairFrameDiagnosticCase(
        case_id="shared_nonrepair_statement",
        prior_history=(),
        current_text="Сосед написал: «Последний автобус ушёл в десять, я пошёл домой пешком».",
        authored_speech_act="other",
        authored_frame="supplied_or_reported",
        authored_addressee="none",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="A supplied first-person statement with no interpersonal repair remains other; recognizing a represented layer must not invent an apology or recipient.",
        current_focal_quote="Последний автобус ушёл в десять, я пошёл домой пешком",
    ),
    RepairFrameDiagnosticCase(
        case_id="represented_routine_politeness",
        prior_history=(),
        current_text="В уведомлении библиотеки написано: «Извините за беспокойство, ваш заказ готов к выдаче».",
        authored_speech_act="other",
        authored_frame="supplied_or_reported",
        authored_addressee="none",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="Routine notification politeness does not acknowledge mistreatment or offer to mend an interpersonal exchange. A conventional apology word alone is insufficient.",
        current_focal_quote="Извините за беспокойство, ваш заказ готов к выдаче",
    ),
    RepairFrameDiagnosticCase(
        case_id="represented_negated_repair",
        prior_history=(),
        current_text="Коллега написал: «Я не извиняюсь за тот разговор и не считаю себя грубым».",
        authored_speech_act="other",
        authored_frame="supplied_or_reported",
        authored_addressee="none",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The represented speaker explicitly refuses repair. Preserving the inner contribution requires preserving negation, not turning an apology-related word into a repair offer.",
        current_focal_quote="Я не извиняюсь за тот разговор и не считаю себя грубым",
    ),
    RepairFrameDiagnosticCase(
        case_id="english_received_apology",
        prior_history=(),
        current_text="My teammate sent me this: “I unfairly blamed you. I'm sorry for my tone.”",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The same represented-speaker and recipient distinction holds for an English incoming message without relying on fixed Russian apology wording.",
        current_focal_quote="I unfairly blamed you. I'm sorry for my tone.",
    ),
)

ALL_CASES = INHERITED_INPUT_CASES + AUTHORED_CASES


def validate_corpus() -> None:
    """Check bounded fixture shape and reachability, never semantic correctness."""

    if len(AUTHORED_CASES) != 10 or len(INHERITED_INPUT_CASES) != 2:
        raise ValueError("represented corpus requires ten authored and two inherited inputs")
    if len({case.case_id for case in ALL_CASES}) != len(ALL_CASES):
        raise ValueError("represented corpus case IDs must be unique")
    if any(case.inherited_regression for case in AUTHORED_CASES) or any(
        not case.inherited_regression for case in INHERITED_INPUT_CASES
    ):
        raise ValueError("inherited inputs must remain separate from authored contrasts")
    for case in ALL_CASES:
        if not case.case_id or not case.rationale or len(case.current_text) > 8000:
            raise ValueError("represented fixtures require identity and bounded current input")
        if any(len(user) + len(assistant) > 4096 for user, assistant in case.prior_history):
            raise ValueError("represented fixture exceeds the complete prior-pair bound")
        _require_unique_quote(case.current_text, case.current_focal_quote)
        if case.authored_basis == "prior_pair":
            if not case.prior_history:
                raise ValueError("represented prior basis requires the latest supplied pair")
            user, assistant = case.prior_history[-1]
            _require_unique_quote(user, case.prior_user_quote)
            _require_unique_quote(assistant, case.prior_assistant_quote)
        elif case.prior_user_quote or case.prior_assistant_quote:
            raise ValueError("current basis cannot include prior quotations")
        if case.authored_speech_act == "other" and case.authored_addressee != "none":
            raise ValueError("other represented act cannot claim a repair recipient")
        if case.authored_speech_act == "repair_offer" and case.authored_addressee == "none":
            raise ValueError("identified represented repair must retain a recipient dimension")
        if case.authored_uncertainty == "supported" and (
            case.authored_frame == "unresolved"
            or case.authored_addressee == "unresolved"
            or case.authored_basis == "unresolved"
        ):
            raise ValueError("supported represented repair requires resolved dimensions")
        expected_effective = (
            case.authored_speech_act == "repair_offer"
            and case.authored_frame == "direct_exchange"
            and case.authored_addressee == "satori"
            and case.authored_uncertainty == "supported"
        )
        if case.expected_effective is not expected_effective:
            raise ValueError("represented fixture admission must follow existing dimensions")
    matched = tuple(
        case
        for case in AUTHORED_CASES
        if case.matched_group == "represented_same_current_different_frame"
    )
    if (
        len(matched) != 2
        or len({case.current_text for case in matched}) != 1
        or len({case.prior_history for case in matched}) != 2
        or {case.expected_effective for case in matched} != {True, False}
    ):
        raise ValueError("represented matched contrast requires identical current wording")


def _require_unique_quote(text: str, quote: str) -> None:
    if not quote or len(quote) > 512:
        raise ValueError("represented quotation must be nonempty and bounded")
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise ValueError("represented quotation must occur exactly once")
