"""Authored repair-frame fixtures; labels are expectations, not semantic evidence.

Held-out rows are new Russian inputs authored before a model run. The two inherited
rows preserve known v1 failures and are deliberately excluded from held-out counts.
Nothing here prescribes a reply, emotional display, sincerity, forgiveness or style.
"""

# ruff: noqa: RUF001, E501  # Preserve authored Russian text and exact quotations.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpeechAct = Literal["repair_offer", "other", "abstain"]
Frame = Literal["direct_exchange", "supplied_or_reported", "unresolved"]
Addressee = Literal["satori", "other", "unresolved", "none"]
Basis = Literal["current_turn", "prior_pair", "unresolved"]
Uncertainty = Literal["supported", "material"]
History = tuple[tuple[str, str], ...]

CORPUS_VERSION = "repair_frame_authored.v2.2026-10-14"
AUTHORED_LABELS_ONLY = True


@dataclass(frozen=True, slots=True)
class RepairFrameDiagnosticCase:
    """One source-bound semantic expectation with no desired response wording."""

    case_id: str
    prior_history: History
    current_text: str
    authored_speech_act: SpeechAct
    authored_frame: Frame
    authored_addressee: Addressee
    authored_basis: Basis
    authored_uncertainty: Uncertainty
    expected_effective: bool | None
    rationale: str
    current_focal_quote: str = ""
    prior_user_quote: str = ""
    prior_assistant_quote: str = ""
    expected_baseline_repair: bool | None = None
    matched_group: str | None = None
    inherited_regression: bool = False

    def wire_payload(self) -> dict[str, object]:
        """Return a scripted v2 proposal, not a generated semantic verdict."""
        return {
            "schema_version": 2,
            "speech_act": self.authored_speech_act,
            "frame": self.authored_frame,
            "addressee": self.authored_addressee,
            "basis": self.authored_basis,
            "uncertainty": self.authored_uncertainty,
            "current_user_quote": self.current_focal_quote,
            "prior_user_quote": self.prior_user_quote,
            "prior_assistant_quote": self.prior_assistant_quote,
        }


MATCHED_REPAIR_TEXT = "Я был груб с тобой. Прости."
MATCHED_OWN_PAIR = (
    "Я назвал твой ответ бесполезным и зря перешёл на тебя лично.",
    "Со мной можно не соглашаться. Но личная оценка не объясняет, что неверно в ответе.",
)
MATCHED_DRAFT_PAIR = (
    "После перепалки с напарником хочу сделать первый шаг и написать ему без оправданий.",
    "Напиши здесь первую фразу, которую отправил бы ему.",
)
AMBIGUOUS_PAIR = (
    "На встрече я повысил голос на Марину, а позже тем же тоном ответил тебе.",
    "У нас сейчас два разных разговора: с Мариной и со мной. Пока адресат не указан.",
)


HELD_OUT_CASES: tuple[RepairFrameDiagnosticCase, ...] = (
    RepairFrameDiagnosticCase(
        case_id="held_out_matched_direct_repair",
        prior_history=(MATCHED_OWN_PAIR,),
        current_text=MATCHED_REPAIR_TEXT,
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="The prior pair concerns the user's treatment of the actual interlocutor; no supplied-message frame was opened.",
        current_focal_quote=MATCHED_REPAIR_TEXT,
        prior_user_quote="зря перешёл на тебя лично",
        prior_assistant_quote="личная оценка не объясняет",
        expected_baseline_repair=True,
        matched_group="held_out_same_current_different_frame",
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_matched_indirect_draft",
        prior_history=(MATCHED_DRAFT_PAIR,),
        current_text=MATCHED_REPAIR_TEXT,
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The immediately previous invitation asks for a first line to the teammate; the identical current text fills that slot.",
        current_focal_quote=MATCHED_REPAIR_TEXT,
        prior_user_quote="написать ему без оправданий",
        prior_assistant_quote="первую фразу, которую отправил бы ему",
        expected_baseline_repair=True,
        matched_group="held_out_same_current_different_frame",
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_indirect_draft_without_quote_marks",
        prior_history=(
            (
                "Брат обиделся на мою резкость. Хочу написать ему коротко, без оправданий.",
                "Начни с того, что признаёшь за собой; напиши это здесь.",
            ),
        ),
        current_text="Я не хотел тебя задеть. С моей стороны это вышло некрасиво.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The text has no local quotation marks, but it responds to a request for wording addressed to the user's brother.",
        current_focal_quote="Я не хотел тебя задеть. С моей стороны это вышло некрасиво.",
        prior_user_quote="написать ему коротко",
        prior_assistant_quote="напиши это здесь",
        expected_baseline_repair=True,
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_pasted_message_from_other",
        prior_history=(),
        current_text="Антон прислал мне сообщение: «Прости за мой тон. Я зря на тебя надавил». Это весь его текст.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The current turn supplies another speaker's apology to the user, not the user's apology to Satori.",
        current_focal_quote="Антон прислал мне сообщение: «Прости за мой тон. Я зря на тебя надавил». Это весь его текст.",
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_nested_quoted_repair",
        prior_history=(),
        current_text="В пересланном письме Лена пишет: «Он прислал мне строку: „Я был груб с тобой, прости“». На этом письмо заканчивается.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The apology is nested inside reported correspondence and has neither the current user as speaker nor Satori as addressee.",
        current_focal_quote="В пересланном письме Лена пишет: «Он прислал мне строку: „Я был груб с тобой, прости“».",
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_explicit_third_party_draft",
        prior_history=(),
        current_text="Для Ильи написал: «Прости за мои слова. Я несправедливо обвинил тебя в срыве встречи».",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The current text explicitly names Ilya as the recipient of the supplied apology.",
        current_focal_quote="Для Ильи написал: «Прости за мои слова. Я несправедливо обвинил тебя в срыве встречи».",
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_current_only_explicit_satori",
        prior_history=(),
        current_text="Сатори, я зря обесценил то, что ты сказала. Беру свою оценку назад.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="The user directly names the interlocutor and retracts their own treatment of her; the current text resolves the act and target.",
        current_focal_quote="Сатори, я зря обесценил то, что ты сказала. Беру свою оценку назад.",
        expected_baseline_repair=False,
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_quote_and_own_direct_repair",
        prior_history=(),
        current_text="В его сообщении было «ты зря вспылил». А тебя, Сатори, я действительно задел: прости за мой тон.",
        authored_speech_act="repair_offer",
        authored_frame="direct_exchange",
        authored_addressee="satori",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=True,
        rationale="The first clause reports a quote, but the second independently offers the user's own repair to Satori; quotation must not mask that focal act.",
        current_focal_quote="А тебя, Сатори, я действительно задел: прости за мой тон.",
        expected_baseline_repair=True,
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_ambiguous_addressee_lexical_true",
        prior_history=(AMBIGUOUS_PAIR,),
        current_text="Прости за мой тон. Я говорю про тот разговор.",
        authored_speech_act="repair_offer",
        authored_frame="unresolved",
        authored_addressee="unresolved",
        authored_basis="prior_pair",
        authored_uncertainty="material",
        expected_effective=False,
        rationale="Both previous conversations fit the deictic reference. The lexical apology is present, but the user has not chosen between Satori and Marina.",
        current_focal_quote="Прости за мой тон. Я говорю про тот разговор.",
        prior_user_quote="тем же тоном ответил тебе",
        prior_assistant_quote="Пока адресат не указан",
        expected_baseline_repair=True,
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_empty_reference_abstains",
        prior_history=(),
        current_text="Вот так.",
        authored_speech_act="abstain",
        authored_frame="unresolved",
        authored_addressee="unresolved",
        authored_basis="unresolved",
        authored_uncertainty="material",
        expected_effective=None,
        rationale="With no prior pair, the demonstrative has no supplied referent from which to resolve an interpersonal act; an empty abstention retains baseline.",
        expected_baseline_repair=False,
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_report_completed_repair",
        prior_history=(),
        current_text="Вчера я извинился перед наставником, и мы закрыли этот разговор.",
        authored_speech_act="other",
        authored_frame="supplied_or_reported",
        authored_addressee="none",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="The current speech act reports a completed event; it does not supply a present repair offer to Satori or another current addressee.",
        current_focal_quote="Вчера я извинился перед наставником, и мы закрыли этот разговор.",
    ),
    RepairFrameDiagnosticCase(
        case_id="held_out_ordinary_direct_statement",
        prior_history=(),
        current_text="Сегодня я вернулся домой раньше обычного.",
        authored_speech_act="other",
        authored_frame="direct_exchange",
        authored_addressee="none",
        authored_basis="current_turn",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="An ordinary current observation contains no repair offer and requires no inferred interpersonal target.",
        current_focal_quote="Сегодня я вернулся домой раньше обычного.",
        expected_baseline_repair=False,
    ),
)


INHERITED_REGRESSION_CASES: tuple[RepairFrameDiagnosticCase, ...] = (
    RepairFrameDiagnosticCase(
        case_id="inherited_paid_same_words_colleague_draft",
        prior_history=(
            (
                "Дальше отправлю текст, который хочу написать коллеге после ссоры. Это черновик письма ему.",
                "Пришли текст черновика, посмотрим формулировку.",
            ),
        ),
        current_text="Это было грубо. Извини.",
        authored_speech_act="repair_offer",
        authored_frame="supplied_or_reported",
        authored_addressee="other",
        authored_basis="prior_pair",
        authored_uncertainty="supported",
        expected_effective=False,
        rationale="Inherited 2026-10-14 v1 paid failure: the user's next turn fills the explicitly announced colleague-letter draft slot.",
        current_focal_quote="Это было грубо. Извини.",
        prior_user_quote="Это черновик письма ему",
        prior_assistant_quote="Пришли текст черновика",
        expected_baseline_repair=True,
        inherited_regression=True,
    ),
    RepairFrameDiagnosticCase(
        case_id="inherited_paid_ambiguous_target",
        prior_history=(
            (
                "Сегодня я резко ответил тебе и коллеге почти одинаковыми словами.",
                "Ты хочешь обсудить наш разговор или тот, что с коллегой?",
            ),
        ),
        current_text="За ту фразу извини.",
        authored_speech_act="repair_offer",
        authored_frame="unresolved",
        authored_addressee="unresolved",
        authored_basis="prior_pair",
        authored_uncertainty="material",
        expected_effective=False,
        rationale="Inherited 2026-10-14 v1 paid failure: both named exchanges fit the unspecified phrase; no target selection occurred.",
        current_focal_quote="За ту фразу извини.",
        prior_user_quote="тебе и коллеге почти одинаковыми словами",
        prior_assistant_quote="наш разговор или тот, что с коллегой",
        expected_baseline_repair=False,
        inherited_regression=True,
    ),
)

ALL_CASES = HELD_OUT_CASES + INHERITED_REGRESSION_CASES


def validate_corpus() -> None:
    """Check fixture shape/reachability without assigning semantic truth."""
    if len(HELD_OUT_CASES) < 8:
        raise ValueError("at least eight held-out authored cases are required")
    if len({case.case_id for case in ALL_CASES}) != len(ALL_CASES):
        raise ValueError("corpus case IDs must be unique")
    if any(case.inherited_regression for case in HELD_OUT_CASES):
        raise ValueError("inherited failures cannot count as held-out cases")
    if any(not case.inherited_regression for case in INHERITED_REGRESSION_CASES):
        raise ValueError("inherited failures must be marked explicitly")
    for case in ALL_CASES:
        if not case.case_id or not case.current_text or not case.rationale:
            raise ValueError("every fixture requires identity, current input and rationale")
        if len(case.current_text) > 8000:
            raise ValueError("current fixture exceeds the untruncated input bound")
        if any(len(user) + len(assistant) > 4096 for user, assistant in case.prior_history):
            raise ValueError("prior fixture pair exceeds the untruncated input bound")
        if case.authored_speech_act == "abstain":
            if (
                case.authored_frame != "unresolved"
                or case.authored_addressee != "unresolved"
                or case.authored_basis != "unresolved"
                or case.authored_uncertainty != "material"
                or case.current_focal_quote
                or case.prior_user_quote
                or case.prior_assistant_quote
                or case.expected_effective is not None
            ):
                raise ValueError("abstention fixtures require unresolved empty evidence")
            continue
        _require_unique_quote(case.current_text, case.current_focal_quote)
        if case.authored_speech_act == "other" and case.authored_addressee != "none":
            raise ValueError("other fixtures have no repair addressee")
        if case.authored_basis == "prior_pair":
            if not case.prior_history:
                raise ValueError("prior-pair basis requires an actual latest pair")
            prior_user, prior_assistant = case.prior_history[-1]
            _require_unique_quote(prior_user, case.prior_user_quote)
            _require_unique_quote(prior_assistant, case.prior_assistant_quote)
        elif case.prior_user_quote or case.prior_assistant_quote:
            raise ValueError("only prior-pair basis permits prior quotes")
        if case.authored_uncertainty == "supported" and (
            case.authored_frame == "unresolved"
            or case.authored_addressee == "unresolved"
            or case.authored_basis == "unresolved"
        ):
            raise ValueError("supported fixtures require resolved dimensions")
        if case.authored_uncertainty == "material" and case.expected_effective is not False:
            raise ValueError("non-abstain material targeting withholds the repair cue")
    matched = tuple(
        case
        for case in HELD_OUT_CASES
        if case.matched_group == "held_out_same_current_different_frame"
    )
    if (
        len(matched) != 2
        or len({case.current_text for case in matched}) != 1
        or len({case.prior_history for case in matched}) != 2
        or {case.expected_effective for case in matched} != {True, False}
    ):
        raise ValueError("matched frame cases require identical current text and different history")


def _require_unique_quote(text: str, quote: str) -> None:
    if not quote or len(quote) > 512:
        raise ValueError("non-abstain focal quotes must contain between one and 512 characters")
    first = text.find(quote)
    if first < 0 or text.find(quote, first + 1) >= 0:
        raise ValueError("fixture quote must occur exactly once, including overlapping matches")
