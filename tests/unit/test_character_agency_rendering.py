"""Provider-safe rendering for the Checkpoint 14.3 agency decision."""

# ruff: noqa: RUF001  # Russian provider guidance is intentional test evidence.

from __future__ import annotations

import hashlib
from dataclasses import replace
from statistics import median

import pytest
from tests.test_checkpoint142_character_presence_v26 import (
    _affect,
    _intent,
    _relationship,
    _runtime_context,
    _strategy,
)

from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.conversation.character_agency import (
    CHARACTER_AGENCY_DECISION_SCHEMA_VERSION,
    CharacterAgencyAct,
    CharacterAgencyDecision,
    CharacterAgencyDrive,
    CharacterAgencyInitiative,
    CharacterAgencyLead,
    CharacterAgencyReason,
    CharacterAgencyStatus,
    CharacterAgencySubject,
)
from satori.application.conversation.character_delivery import (
    CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION,
    CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION,
    CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
    CharacterPresenceProjection,
    decide_character_delivery,
    project_character_affect_profile,
    project_character_presence,
    render_character_presence,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
)
from satori.application.conversation.contracts import (
    RuntimeCharacterContext,
    RuntimePersonalityCue,
)
from satori.application.conversation.disclosure_contracts import ConversationalDisclosureMode

_V27_MARKER = "Trusted current-turn presence Сатори / operational move v2"
_V28_MARKER = "Trusted current-turn agency Сатори"


def _agency(
    *,
    drive: CharacterAgencyDrive = CharacterAgencyDrive.HELP,
    act: CharacterAgencyAct = CharacterAgencyAct.HELP,
    subject: CharacterAgencySubject = CharacterAgencySubject.USER_REQUEST,
    initiative: CharacterAgencyInitiative = CharacterAgencyInitiative.STAY_ON_TOPIC,
    lead: CharacterAgencyLead = CharacterAgencyLead.OBLIGATION_FIRST,
    personality_code: str = "curious_analytical",
    value_key: str = "competence",
    reasons: tuple[CharacterAgencyReason, ...] | None = None,
    subject_ref: str | None = None,
) -> CharacterAgencyDecision:
    refs = ("agency-source-private", *((subject_ref,) if subject_ref is not None else ()))
    reason_codes = reasons or (
        CharacterAgencyReason.CANONICAL_INCLINATION
        if subject is CharacterAgencySubject.CANONICAL_INCLINATION
        else CharacterAgencyReason.DIRECT_REQUEST,
    )
    return CharacterAgencyDecision(
        schema_version=CHARACTER_AGENCY_DECISION_SCHEMA_VERSION,
        status=CharacterAgencyStatus.APPLIED,
        drive=drive,
        act=act,
        subject=subject,
        initiative=initiative,
        lead=lead,
        source_personality_codes=(personality_code,),
        source_value_key=value_key,
        reason_codes=reason_codes,
        source_refs=refs,
        subject_ref=subject_ref,
    )


def _projection(
    *,
    context: RuntimeCharacterContext,
    schema_version: int,
    agency: CharacterAgencyDecision | None,
    topic_inclination_available: bool = False,
) -> CharacterPresenceProjection:
    strategy = _strategy()
    decision = decide_character_delivery(
        strategy,
        intent=_intent(strategy),
        affect_profile=project_character_affect_profile(_affect()),
        personality_codes=BASELINE_CHARACTER_GUIDANCE_CODES,
        relationship_profile="developing_neutral",
        relationship_relevant=False,
        explicit_request=True,
        answer_required=True,
        decision_schema_version=(
            CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION
            if schema_version == CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION
            else CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION
        ),
        disclosure_mode=(
            ConversationalDisclosureMode.SOCIAL
            if agency is not None and CharacterAgencyReason.SOCIAL_EXCHANGE in agency.reason_codes
            else None
        ),
        live_personality=context.personality_expression,
        live_traits=context.traits,
        live_values=context.values,
        agency=agency,
    )
    return project_character_presence(
        decision,
        personality_aggregate_version=context.personality_aggregate_version,
        personality=context.personality_expression,
        traits=context.traits,
        values=context.values,
        emotional_context=_affect(),
        relationship_context=_relationship(),
        affect_profile=project_character_affect_profile(_affect()),
        affect_relevant=False,
        relationship_profile="developing_neutral",
        relationship_relevant=False,
        memory_use_licensed=False,
        canonical_position_available=False,
        topic_inclination_available=topic_inclination_available,
        projection_schema_version=schema_version,
    )


def _render(projection: CharacterPresenceProjection) -> str:
    return render_character_presence(
        projection,
        cognition_template=COGNITION_TEMPLATE_REGISTRY_V3.active,
    )


def test_v28_is_one_compact_agency_first_block_and_v27_is_byte_stable() -> None:
    context = _runtime_context()
    agency = _agency()
    current = _render(
        _projection(
            context=context,
            schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
            agency=agency,
        )
    )
    historical = _render(
        _projection(
            context=context,
            schema_version=CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION,
            agency=None,
        )
    )

    assert current.count(_V28_MARKER) == 1
    assert _V27_MARKER not in current
    assert "\n" not in current
    assert len(current) <= len(historical)
    assert _V27_MARKER in historical
    assert _V28_MARKER not in historical
    assert len(historical) == 762
    assert hashlib.sha256(historical.encode()).hexdigest() == (
        "ad660e707728ff986b9353ce782cf7795461afa2ff5b0dfd394be2ad3a60e554"
    )
    assert "полностью помочь по существу" in current
    assert "прямого запроса собеседника" in current
    assert "Сначала закрой текущий смысл" in current
    assert "не выходи за supplied evidence" in current
    assert "Останься в текущей теме" in current
    for internal in (
        agency.drive.value,
        agency.act.value,
        agency.subject.value,
        agency.initiative.value,
        agency.lead.value,
        *agency.source_personality_codes,
        agency.source_value_key,
        *agency.source_refs,
    ):
        assert internal not in current


def test_v28_uses_one_directional_personality_posture_before_agency_default() -> None:
    context = _runtime_context()
    evolved = replace(
        context,
        personality_aggregate_version=context.personality_aggregate_version + 1,
        personality_expression=replace(
            context.personality_expression,
            schema_version=2,
            cues=(
                RuntimePersonalityCue(
                    code="grounded_optimism",
                    direction="slightly_stronger",
                ),
            ),
        ),
    )
    rendered = _render(
        _projection(
            context=evolved,
            schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
            agency=_agency(),
        )
    )

    assert "сейчас заметнее: оставь направление вперёд" in rendered
    assert "заметь одну конкретную нестыковку" not in rendered
    assert "grounded_optimism" not in rendered
    assert rendered.count(_V28_MARKER) == 1


def test_v28_none_uses_a_neutral_cognition_bridge() -> None:
    agency = _agency(
        drive=CharacterAgencyDrive.NONE,
        act=CharacterAgencyAct.RESPOND,
        initiative=CharacterAgencyInitiative.NONE,
        lead=CharacterAgencyLead.OBLIGATION_FIRST,
        personality_code="considered_directness",
        value_key="truth",
    )
    rendered = _render(
        _projection(
            context=_runtime_context(),
            schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
            agency=agency,
        )
    )

    assert "не добавляет отдельного личного импульса" in rendered
    assert "Выполни обязательный смысл" in rendered
    assert "Начни этим собственным ходом" not in rendered
    assert "Не добавляй второго движения" in rendered


def test_v28_canonical_inclination_is_owner_bounded_and_never_leaks_its_id() -> None:
    agency = _agency(
        drive=CharacterAgencyDrive.EXPLORE,
        act=CharacterAgencyAct.PROPOSE,
        subject=CharacterAgencySubject.CANONICAL_INCLINATION,
        initiative=CharacterAgencyInitiative.SHIFT_ADJACENT,
        lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        value_key="curiosity",
        subject_ref="inclination-private-id",
    )
    rendered = _render(
        _projection(
            context=_runtime_context(),
            schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
            agency=agency,
            topic_inclination_available=True,
        )
    )

    assert "одной supplied склонности Сатори" in rendered
    assert "не превращая её в биографию" in rendered
    assert "inclination-private-id" not in rendered
    assert "supplied-смежный ход" in rendered

    with pytest.raises(ValueError, match="canonical inclination agency"):
        _render(
            _projection(
                context=_runtime_context(),
                schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
                agency=agency,
                topic_inclination_available=False,
            )
        )


def test_v28_paired_agency_fixture_is_not_larger_in_total_or_median_than_v27() -> None:
    context = _runtime_context()
    agencies = (
        _agency(),
        _agency(
            drive=CharacterAgencyDrive.NONE,
            act=CharacterAgencyAct.RESPOND,
            initiative=CharacterAgencyInitiative.NONE,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
            personality_code="considered_directness",
            value_key="truth",
            reasons=(CharacterAgencyReason.DIRECT_QUESTION,),
        ),
        _agency(
            drive=CharacterAgencyDrive.CHALLENGE,
            act=CharacterAgencyAct.CHALLENGE,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
            personality_code="independent_position",
            value_key="intellectual_honesty",
            reasons=(CharacterAgencyReason.DIRECT_REQUEST,),
        ),
        _agency(
            drive=CharacterAgencyDrive.CONNECT,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
            personality_code="warm_perceptive",
            value_key="connection",
            reasons=(CharacterAgencyReason.SOCIAL_EXCHANGE,),
        ),
        _agency(
            drive=CharacterAgencyDrive.CONNECT,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
            personality_code="warm_perceptive",
            value_key="connection",
            reasons=(CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,),
        ),
        _agency(
            drive=CharacterAgencyDrive.CARE,
            act=CharacterAgencyAct.CARE,
            subject=CharacterAgencySubject.USER_EXPLICIT_STATE,
            lead=CharacterAgencyLead.FUSED,
            personality_code="warm_perceptive",
            value_key="compassion",
            reasons=(CharacterAgencyReason.EXPLICIT_DEPLETION,),
        ),
        _agency(
            drive=CharacterAgencyDrive.CLOSE,
            act=CharacterAgencyAct.CLOSE,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
            personality_code="independent_position",
            value_key="autonomy",
            reasons=(CharacterAgencyReason.TOPIC_CLOSURE,),
        ),
    )
    current_lengths = [
        len(
            _render(
                _projection(
                    context=context,
                    schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
                    agency=agency,
                )
            )
        )
        for agency in agencies
    ]
    historical_lengths = [
        len(
            _render(
                _projection(
                    context=context,
                    schema_version=CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION,
                    agency=None,
                )
            )
        )
        for _ in agencies
    ]

    assert sum(current_lengths) <= sum(historical_lengths)
    assert median(current_lengths) <= median(historical_lengths)


def test_v28_current_attention_cannot_license_an_offscreen_activity_or_thought() -> None:
    agency = _agency(
        drive=CharacterAgencyDrive.CONNECT,
        act=CharacterAgencyAct.RESPOND,
        subject=CharacterAgencySubject.CURRENT_EXCHANGE,
        lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        personality_code="warm_perceptive",
        value_key="connection",
        reasons=(CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,),
    )
    rendered = _render(
        _projection(
            context=_runtime_context(),
            schema_version=CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
            agency=agency,
        )
    )

    assert "только из текущего обмена репликами" in rendered
    assert "никакой внесценной деятельности" in rendered
    assert "нового устойчивого интереса не supplied" in rendered
