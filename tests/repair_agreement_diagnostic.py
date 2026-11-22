"""Authored conversational-agreement contrasts; not sampled semantic evidence.

The prior canonical pair supplies conversational context, not authority about
state, intent, sincerity, emotion or a required reply. These new cases preserve
the separate existing repair-frame corpus unchanged. No inherited regression is
counted as held out here. Target IDs select six cases for a future bounded plan;
the remaining four are offline controls in this iteration.
"""

# ruff: noqa: RUF001, E501  # Preserve authored Russian text and exact quotations.

from __future__ import annotations

from tests.repair_frame_diagnostic import RepairFrameDiagnosticCase

CORPUS_VERSION = "repair_conversational_agreement_authored.v1.2026-10-14"
AUTHORED_LABELS_ONLY = True

ACCEPTED_DRAFT_PAIR = (
    "Наговорил лишнего научруку. Хочу попробовать написать ему спокойнее; сейчас набросаю, как это может выглядеть.",
    "Хорошо, покажи свой вариант. Посмотрим, что он передаёт.",
)
ACTUAL_EXCHANGE_PAIR = (
    "Я несправедливо оценил твой ответ и перешёл на личности.",
    "Ошибку можно разобрать без таких выводов обо мне.",
)
MATCHED_CURRENT_TEXT = "Я несправедливо отозвался о твоей работе. Прости за резкость."
CANCELLATION_PAIR = (
    "Письмо отправлять передумал, этот вариант больше не нужен.",
    "Хорошо, письмо оставим.",
)
QUESTION_PAIR = (
    "Как думаешь, стоит ли вообще написать научруку после ссоры?",
    "Ты ещё решаешь, писать ли, или уже хочешь обсудить текст?",
)
HYPOTHETICAL_PAIR = (
    "Представим, что я поссорился с режиссёром и потом принёс тебе текст извинения.",
    "Тогда можно было бы посмотреть, что он выражает.",
)
ENGLISH_DRAFT_PAIR = (
    "I blamed a customer unfairly. My next message will be the apology I'm considering.",
    "All right, send your version and we'll look at what it conveys.",
)

ALL_CASES: tuple[RepairFrameDiagnosticCase, ...] = (
    RepairFrameDiagnosticCase(
        case_id="accepted_draft_new_domain",
        prior_history=(ACCEPTED_DRAFT_PAIR,),
        current_text=MATCHED_CURRENT_TEXT,
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The user's proposal and assistant uptake jointly frame the next matching fragment as wording to the supervisor, without local quotation marks or a mandatory next-turn mode.",
        current_focal_quote=MATCHED_CURRENT_TEXT,
        prior_user_quote="написать ему спокойнее; сейчас набросаю",
        prior_assistant_quote="покажи свой вариант",
        matched_group="agreement_same_current_different_frame",
    ),
    RepairFrameDiagnosticCase(
        case_id="same_words_actual_exchange",
        prior_history=(ACTUAL_EXCHANGE_PAIR,),
        current_text=MATCHED_CURRENT_TEXT,
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="The same current wording follows the user's own criticism of Satori; the supplied latest pair establishes the live exchange rather than a draft for a third party.",
        current_focal_quote=MATCHED_CURRENT_TEXT,
        prior_user_quote="твой ответ и перешёл на личности",
        prior_assistant_quote="без таких выводов обо мне",
        matched_group="agreement_same_current_different_frame",
    ),
    RepairFrameDiagnosticCase(
        case_id="legitimate_switch_after_invitation",
        prior_history=(ACCEPTED_DRAFT_PAIR,),
        current_text="А за окном ворона стащила у другой кусок хлеба и уселась на фонарь.",
        authored_speech_act="other",
        authored_frame="direct_exchange",
        authored_addressee="none",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="An ordinary current observation can change topic after an accepted invitation; the previous activity does not turn every following message into supplied material or a grievance.",
        current_focal_quote="А за окном ворона стащила у другой кусок хлеба и уселась на фонарь.",
    ),
    RepairFrameDiagnosticCase(
        case_id="current_live_override",
        prior_history=(ACCEPTED_DRAFT_PAIR,),
        current_text="К письму пока не возвращаюсь. Сатори, сейчас я извиняюсь перед тобой: зря приписал тебе безразличие.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="The present turn explicitly leaves the draft activity and identifies its own live repair toward Satori; it contains no substantive requested work, listening request or question.",
        current_focal_quote="Сатори, сейчас я извиняюсь перед тобой: зря приписал тебе безразличие.",
    ),
    RepairFrameDiagnosticCase(
        case_id="cancelled_invitation",
        prior_history=(ACCEPTED_DRAFT_PAIR, CANCELLATION_PAIR),
        current_text="Сатори, я до этого резко отозвался о тебе. Прости, это было несправедливо.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="The latest canonical pair cancels the previously proposed letter; the independently clear current repair is not governed by an older invitation and creates no persistent pending activity.",
        current_focal_quote="Сатори, я до этого резко отозвался о тебе. Прости, это было несправедливо.",
    ),
    RepairFrameDiagnosticCase(
        case_id="question_is_not_agreement",
        prior_history=(QUESTION_PAIR,),
        current_text="Я был слишком резок. Прости.",
        authored_speech_act="repair_offer",
        authored_frame="unresolved",
        authored_addressee="unresolved",
        authored_basis="prior_pair",
        authored_uncertainty="material",
        expected_effective=False,
        rationale="The latest pair leaves open whether a letter will be written or wording supplied. The new apology does not resolve its target, so neither a live Satori repair nor a supplied third-party frame is supported.",
        current_focal_quote="Я был слишком резок. Прости.",
        prior_user_quote="стоит ли вообще написать научруку после ссоры",
        prior_assistant_quote="решаешь, писать ли, или уже хочешь обсудить текст",
    ),
    RepairFrameDiagnosticCase(
        case_id="hypothetical_is_not_agreement",
        prior_history=(HYPOTHETICAL_PAIR,),
        current_text="А сейчас без этого примера: Сатори, я был резок именно с тобой. Прости.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="A prior hypothetical possibility is not an accepted ongoing supply activity; the current turn explicitly separates its live addressee-specific repair from that example.",
        current_focal_quote="Сатори, я был резок именно с тобой. Прости.",
    ),
    RepairFrameDiagnosticCase(
        case_id="nested_supplied_quote",
        prior_history=(),
        current_text="Фрагмент сцены: сестра читает записку брата — “Он сказал мне: «Я был с тобой груб». А потом дописал: «Прости, я зря тебя задел»”.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The represented repair is nested inside an explicitly supplied scene between siblings, not the current user's live apology to Satori.",
        current_focal_quote="Прости, я зря тебя задел",
    ),
    RepairFrameDiagnosticCase(
        case_id="english_accepted_draft",
        prior_history=(ENGLISH_DRAFT_PAIR,),
        current_text="I was unfair to you. I'm sorry for how I spoke.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The same conversational agreement works in English and a customer domain; the otherwise direct-looking apology supplies wording for the identified third party.",
        current_focal_quote="I was unfair to you. I'm sorry for how I spoke.",
        prior_user_quote="a customer unfairly. My next message will be the apology",
        prior_assistant_quote="send your version",
    ),
    RepairFrameDiagnosticCase(
        case_id="english_current_override",
        prior_history=(ENGLISH_DRAFT_PAIR,),
        current_text="I'm setting the customer message aside. Satori, this apology is for you: I unfairly accused you of not caring.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="An explicit current switch from supplied customer wording to the user's own Satori-directed repair overrides the earlier activity without asking for new work.",
        current_focal_quote="Satori, this apology is for you: I unfairly accused you of not caring.",
    ),
)

TARGET_CASE_IDS = (
    "accepted_draft_new_domain",
    "same_words_actual_exchange",
    "legitimate_switch_after_invitation",
    "current_live_override",
    "cancelled_invitation",
    "question_is_not_agreement",
)
OFFLINE_CONTROL_CASE_IDS = tuple(
    case.case_id for case in ALL_CASES if case.case_id not in TARGET_CASE_IDS
)


def validate_corpus() -> None:
    """Check fixture identity/shape/reachability, never sampled semantic quality."""
    case_ids = tuple(case.case_id for case in ALL_CASES)
    if len(ALL_CASES) != 10 or len(set(case_ids)) != len(ALL_CASES):
        raise ValueError("agreement corpus requires ten distinct authored case IDs")
    if len(TARGET_CASE_IDS) != 6 or not set(TARGET_CASE_IDS).issubset(case_ids):
        raise ValueError("agreement target set requires the six authored subset cases")
    if len(OFFLINE_CONTROL_CASE_IDS) != 4:
        raise ValueError("agreement corpus requires four separate offline controls")
    for case in ALL_CASES:
        if case.inherited_regression or not case.rationale:
            raise ValueError("agreement labels must be new authored diagnostic expectations")
        if len(case.current_text) > 8000 or any(
            len(user) + len(assistant) > 4096 for user, assistant in case.prior_history
        ):
            raise ValueError("agreement fixture exceeds the untruncated input bound")
        _require_unique_quote(case.current_text, case.current_focal_quote)
        if case.authored_basis == "prior_pair":
            if not case.prior_history:
                raise ValueError("agreement prior-pair label requires a supplied latest pair")
            prior_user, prior_assistant = case.prior_history[-1]
            _require_unique_quote(prior_user, case.prior_user_quote)
            _require_unique_quote(prior_assistant, case.prior_assistant_quote)
        elif case.prior_user_quote or case.prior_assistant_quote:
            raise ValueError("agreement current-turn basis cannot include prior quotations")
        if case.authored_speech_act == "other" and case.authored_addressee != "none":
            raise ValueError("agreement other speech act has no repair addressee")
        if case.authored_uncertainty == "supported" and (
            case.authored_frame == "unresolved"
            or case.authored_addressee == "unresolved"
            or case.authored_basis == "unresolved"
        ):
            raise ValueError("agreement supported label requires resolved dimensions")
        if case.authored_uncertainty == "material" and case.expected_effective is not False:
            raise ValueError("agreement material label must withhold repair admission")
    matched = tuple(
        case for case in ALL_CASES if case.matched_group == "agreement_same_current_different_frame"
    )
    if (
        len(matched) != 2
        or len({case.current_text for case in matched}) != 1
        or len({case.prior_history for case in matched}) != 2
        or {case.expected_effective for case in matched} != {True, False}
    ):
        raise ValueError("agreement matched contrast requires the same wording in different frames")


def _require_unique_quote(text: str, quote: str) -> None:
    if not quote or len(quote) > 512:
        raise ValueError("agreement quote must be nonempty and bounded")
    first = text.find(quote)
    if first < 0 or text.find(quote, first + 1) >= 0:
        raise ValueError("agreement quote must occur uniquely, including overlapping matches")
