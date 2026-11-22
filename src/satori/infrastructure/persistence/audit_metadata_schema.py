"""Closed raw audit event shapes; metadata classification never grants write authority."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from satori.core.personality import CANONICAL_TRAIT_KEYS
from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION_V1,
    REFLECTION_POLICY_VERSION_V2,
    REFLECTION_POLICY_VERSION_V3,
)

MAX_AUDIT_BYTES = 131_072
MAX_AUDIT_ARRAY = 512
MAX_AUDIT_DEPTH = 32
MAX_AUDIT_NODES = 8192

REASONS: dict[str, frozenset[str]] = {
    "episode_formation": frozenset(
        {
            "below_importance_threshold",
            "assistant_output_not_evidence",
            "evidence_missing",
            "source_grounded_episode",
            "summary_missing",
            "provider_selected_skip",
            "unsupported_proposal_schema",
            "invalid_skip_payload",
            "interaction_not_completed",
            "source_message_missing",
            "scores_missing",
            "evidence_quote_not_grounded",
            "summary_too_long",
        }
    ),
    "memory_lifecycle": frozenset(
        {"episode_lifecycle_noop", "episode_archived", "episode_reactivated"}
    ),
    "position_formation": frozenset(
        {"proposal_rejected", "owner_changes_applied", "no_new_eligible_evidence"}
    ),
    "reflection_disabled": frozenset({"target_owner_not_enabled"}),
    "reflection_inclination": frozenset(
        {
            "insufficient_inclination_evidence_diversity",
            "inclination_weakened",
            "inclination_source_identity_mismatch",
            "inclination_target_version_conflict",
            "inclination_target_key_mismatch",
            "inclination_target_not_found",
            "insufficient_comparative_formation_signal",
            "inclination_delta_immaterial_or_bound_saturated",
            "inclination_cooldown",
            "inclination_source_outside_fixed_set",
            "inclination_observation_span_too_short",
            "inclination_strengthened",
            "insufficient_positive_formation_signal",
            "inclination_topics_not_distinct",
            "provider_confidence_too_low",
            "inclination_delta_immaterial_or_budget_exhausted",
            "eligible_inclination_created",
            "insufficient_preference_evidence_diversity",
        }
    ),
    "reflection_personality": frozenset(
        {
            "personality_fixed_source_set_invalid",
            "personality_evidence_root_already_used",
            "personality_support_share_too_low",
            "personality_observation_span_too_short",
            "personality_source_from_future",
            "personality_target_version_conflict",
            "insufficient_personality_support",
            "personality_trait_cooldown",
            "personality_checkpoint_distance_budget_exhausted",
            "personality_source_outside_fixed_set",
            "personality_checkpoint_version_from_future",
            "personality_evolution_applied",
            "personality_history_from_future",
            "personality_checkpoint_schema_mismatch",
            "personality_source_coverage_too_low",
            "personality_trait_value_bound",
            "personality_checkpoint_not_found",
            "personality_lifetime_global_budget_exhausted",
            "personality_checkpoint_hash_mismatch",
            "personality_rolling_global_budget_exhausted",
            "personality_checkpoint_identity_mismatch",
            "personality_lifetime_trait_budget_exhausted",
            "provider_confidence_too_low",
            "personality_history_identity_mismatch",
            "personality_checkpoint_baseline_mismatch",
            "personality_activation_distance_budget_exhausted",
            "personality_source_ineligible",
            "personality_global_cooldown",
            "personality_source_identity_mismatch",
            "personality_rolling_trait_budget_exhausted",
            "insufficient_personality_evidence_diversity",
        }
    ),
    "reflection_position": frozenset({"position_owner_rejected", "position_owner_accepted"}),
    "relationship_transition": frozenset(
        {"bounded_relationship_event_applied", "bounded_no_effect", "low_confidence"}
    ),
    "semantic_formation": frozenset(
        {
            "no_material_change",
            "no_claims",
            "all_claims_rejected",
            "competing_inferences",
            "claims_applied",
        }
    ),
    "user_model_formation": frozenset(
        {"owner_changes_applied", "proposal_rejected", "no_supported_current_claims"}
    ),
    "world_model_formation": frozenset(
        {"owner_changes_applied", "proposal_rejected", "no_supported_current_claims"}
    ),
}

_FIELDS = {
    "activation": "seed_id seed_schema_version seed_content_hash",
    "episode_formation": (
        "source_interaction_id formation_version policy_version decision_id reason_code"
    ),
    "memory_lifecycle": (
        "decision_id request_id identity_id counterparty_id from_status "
        "to_status expected_version result_version policy_version reason_code "
        "approved_by preview_digest"
    ),
    "semantic_formation": (
        "decision_id source_memory_id formation_version policy_version "
        "reason_code claim_ids created_count merged_count superseded_count "
        "disputed_count rejected_count"
    ),
    "user_model_formation": (
        "decision_id source_interaction_id formation_version policy_version "
        "reason_code claim_ids created_count merged_count superseded_count "
        "disputed_count rejected_count"
    ),
    "world_model_formation": (
        "decision_id source_interaction_id formation_version policy_version "
        "reason_code claim_ids created_count merged_count superseded_count "
        "disputed_count rejected_count"
    ),
    "position_formation": (
        "decision_id source_interaction_id formation_version policy_version "
        "reason_code position_ids created_count merged_count superseded_count "
        "competing_count rejected_count"
    ),
    "reflection_position": (
        "outcome_id proposal_id target_policy_version reason_code position_ids"
    ),
    "reflection_inclination": (
        "outcome_id proposal_id target_policy_version reason_code "
        "inclination_id inclination_kind aggregate_version revision_id "
        "evidence_ids new_evidence_count prior_score new_score applied_delta "
        "prior_confidence new_confidence prior_stability new_stability"
    ),
    "reflection_personality": (
        "outcome_id proposal_id target_policy_version reason_code revision_id "
        "aggregate_version evidence_ids evidence_count"
    ),
    "reflection_disabled": "outcome_id target_policy_version reason_code identity_id",
    "relationship_transition": (
        "decision_id transition_id interaction_id source_user_message_id "
        "session_id categories reason_code policy_version "
        "appraisal_schema_version"
    ),
    "checkpoint_approval": "approval_id checkpoint_id checkpoint_hash aggregate_version",
    "personality_restore": (
        "restore_id revision_id source_checkpoint_id source_checkpoint_hash "
        "resulting_checkpoint_id before_aggregate_version "
        "after_aggregate_version changed_trait_keys"
    ),
    "source_withdrawal": (
        "owner aggregate_id request_id policy_generation receipt_id before_version after_version"
    ),
}
_OWNER_FIELDS = {
    "activation": None,
    "episode_formation": "decision_id",
    "memory_lifecycle": "decision_id",
    "semantic_formation": "decision_id",
    "user_model_formation": "decision_id",
    "world_model_formation": "decision_id",
    "position_formation": "decision_id",
    "relationship_transition": "decision_id",
    "reflection_position": "outcome_id",
    "reflection_inclination": "outcome_id",
    "reflection_personality": "outcome_id",
    "reflection_disabled": "outcome_id",
    "checkpoint_approval": "approval_id",
    "personality_restore": "restore_id",
    "source_withdrawal": "receipt_id",
}
_OPTIONAL = frozenset(
    {
        "inclination_id",
        "inclination_kind",
        "aggregate_version",
        "revision_id",
        "transition_id",
        "prior_score",
        "new_score",
        "applied_delta",
        "prior_confidence",
        "new_confidence",
        "prior_stability",
        "new_stability",
    }
)
_NUMBERS = frozenset(
    {
        "prior_score",
        "new_score",
        "applied_delta",
        "prior_confidence",
        "new_confidence",
        "prior_stability",
        "new_stability",
    }
)
_LISTS = frozenset(
    {
        "claim_ids",
        "position_ids",
        "evidence_ids",
        "source_message_ids",
        "categories",
        "changed_trait_keys",
    }
)
_HEADER = frozenset(
    {
        "event_id",
        "schema_version",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "occurred_at",
        "trace_id",
    }
)


def _invalid() -> ValueError:
    return ValueError("audit_metadata_invalid")


def document(value: object) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in items:
            if key in result:
                raise _invalid()
            result[key] = item
        return result

    try:
        if isinstance(value, str):
            if len(value.encode()) > MAX_AUDIT_BYTES:
                raise _invalid()
            parsed = json.loads(
                value,
                object_pairs_hook=pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(_invalid()),
            )
        else:
            parsed = value
        nodes = 0
        pending = [(parsed, 0)]
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if depth > MAX_AUDIT_DEPTH or nodes > MAX_AUDIT_NODES:
                raise _invalid()
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        if len(json.dumps(parsed, allow_nan=False).encode()) > MAX_AUDIT_BYTES:
            raise _invalid()
        return parsed
    except (ValueError, TypeError, RecursionError):
        raise _invalid() from None


def text(value: object, *, maximum: int = 128) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise _invalid()
    return value


def integer(value: object, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def string_list(value: object, *, unique: bool = True) -> tuple[str, ...]:
    parsed = document(value)
    if not isinstance(parsed, list) or len(parsed) > MAX_AUDIT_ARRAY:
        raise _invalid()
    result = tuple(text(item) for item in parsed)
    if unique and len(result) != len(set(result)):
        raise _invalid()
    return result


def utc_time(value: object) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise _invalid() from None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _invalid()
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def owner_record_field(family: AuditMetadataFamily) -> str | None:
    if family.value not in _OWNER_FIELDS:
        raise _invalid()
    return _OWNER_FIELDS[family.value]


def validate_event_shape(
    row: Mapping[str, object], family: AuditMetadataFamily
) -> dict[str, object]:
    """Exact supported keys, code vocabulary and non-coerced bounded scalar/list types."""
    if (
        family.value not in _FIELDS
        or type(row.get("schema_version")) is not int
        or row["schema_version"] != 1
    ):
        raise _invalid()
    for name in _HEADER - {"occurred_at", "schema_version"}:
        text(row.get(name), maximum=64 if name in {"event_type", "aggregate_type"} else 128)
    utc_time(row.get("occurred_at"))
    decoded = document(row.get("details"))
    if not isinstance(decoded, dict):
        raise _invalid()
    details = cast(dict[str, object], decoded)
    fields = set(_FIELDS[family.value].split())
    if family.value == "episode_formation" and row["event_type"] == "memory.episode_created":
        fields.update({"evidence_ids", "source_message_ids"})
    if set(details) != fields:
        raise _invalid()
    exact_events = {
        "activation": "satori.activation",
        "checkpoint_approval": "personality.checkpoint_approved",
        "personality_restore": "personality.checkpoint_restored",
        "source_withdrawal": "source_support_withdrawn",
    }
    if family.value in exact_events:
        if row["event_type"] != exact_events[family.value]:
            raise _invalid()
    else:
        prefixes = {
            "episode_formation": "memory.episode_",
            "memory_lifecycle": "memory.",
            "semantic_formation": "memory.semantic_",
            "user_model_formation": "models.user_",
            "world_model_formation": "models.world_",
            "position_formation": "positions.",
            "reflection_position": "reflection.position_",
            "reflection_inclination": "reflection.inclination_",
            "reflection_personality": "reflection.personality_",
            "reflection_disabled": "reflection.",
            "relationship_transition": "relationship.decision_",
        }
        endings = (
            {"created", "skipped", "rejected"}
            if family.value == "episode_formation"
            else {"accepted", "rejected"}
            if family.value.startswith("reflection_")
            else REASONS["memory_lifecycle"]
            if family.value == "memory_lifecycle"
            else {"applied", "skipped", "rejected"}
        )
        if row["event_type"] not in {prefixes[family.value] + suffix for suffix in endings}:
            raise _invalid()
    for name, value in details.items():
        if value is None and name in _OPTIONAL:
            continue
        if name in _LISTS:
            values = string_list(value, unique=name != "source_message_ids")
            if name == "changed_trait_keys" and not set(values) <= set(CANONICAL_TRAIT_KEYS):
                raise _invalid()
            if name == "categories":
                from satori.domain.relationship import RelationshipEventCategory

                if not set(values) <= {item.value for item in RelationshipEventCategory}:
                    raise _invalid()
        elif name in _NUMBERS:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not (-1.0 if name in {"prior_score", "new_score", "applied_delta"} else 0.0)
                <= value
                <= 1.0
            ):
                raise _invalid()
        elif name.endswith("_count") or name == "policy_generation":
            integer(value)
        elif name.endswith("_version"):
            integer(value, minimum=1)
            disabled_policy = (
                family is AuditMetadataFamily.REFLECTION_DISABLED
                and name == "target_policy_version"
            )
            if disabled_policy and value not in {
                REFLECTION_POLICY_VERSION_V1,
                REFLECTION_POLICY_VERSION_V2,
                REFLECTION_POLICY_VERSION_V3,
            }:
                raise _invalid()
            if (
                name
                in {
                    "formation_version",
                    "policy_version",
                    "target_policy_version",
                    "appraisal_schema_version",
                    "seed_schema_version",
                }
                and not disabled_policy
                and value != 1
            ):
                raise _invalid()
        else:
            text(value)
        if (name.endswith("_hash") or name == "preview_digest") and (
            type(value) is not str
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise _invalid()
    if "reason_code" in details and details["reason_code"] not in REASONS.get(
        family.value, frozenset()
    ):
        raise _invalid()
    if family.value == "memory_lifecycle" and (
        details["from_status"] not in {"active", "archived"}
        or details["to_status"] not in {"active", "archived"}
        or details["approved_by"] != "local_operator"
    ):
        raise _invalid()
    if family.value == "source_withdrawal" and details["owner"] not in {
        "semantic_memory",
        "user_model_claim",
        "world_model_claim",
        "position",
        "inclination",
    }:
        raise _invalid()
    if family.value == "reflection_inclination" and details["inclination_kind"] not in {
        None,
        "interest",
        "preference",
    }:
        raise _invalid()
    return details


def event_digest(row: Mapping[str, object]) -> str:
    """Canonical event seal; time formatting is metadata normalization only."""
    header = {name: row[name] for name in _HEADER}
    header["occurred_at"] = utc_time(header["occurred_at"])
    header["details"] = document(row.get("details"))
    try:
        return hashlib.sha256(
            json.dumps(
                header, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode()
        ).hexdigest()
    except (ValueError, TypeError, RecursionError):
        raise _invalid() from None
