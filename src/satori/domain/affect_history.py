"""Retained numerical affect history, separate from unavailable appraisal explanations."""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from satori.domain.affect import (
    AffectiveDelta,
    AffectiveStateSnapshot,
    FastAffectiveState,
    MoodDelta,
    MoodState,
)
from satori.domain.validation import non_blank

APPRAISAL_FIELDS = (
    "pleasantness",
    "activation",
    "novelty",
    "salience",
    "uncertainty",
    "curiosity_signal",
    "interest_signal",
    "humor_signal",
    "concern_signal",
    "frustration_signal",
    "confidence_signal",
)
SNAPSHOT_FIELDS = frozenset(
    {
        "identity_id",
        "schema_version",
        "state_version",
        "mood_version",
        "as_of",
        "emotion_policy_version",
        "appraisal_schema_version",
        "mood_policy_version",
        "fast",
        "mood",
    }
)


@dataclass(frozen=True, slots=True)
class AffectiveTransitionHeader:
    transition_id: str
    identity_id: str
    interaction_id: str
    source_message_id: str
    committed_at: datetime
    explanation_state: str
    source_input_id: str | None
    audit_event_id: str | None
    explanation_action_id: str | None

    def __post_init__(self) -> None:
        for name in ("transition_id", "identity_id", "interaction_id", "source_message_id"):
            _text(getattr(self, name))
        _time(self.committed_at)
        if self.explanation_state not in {"available", "erased"}:
            raise ValueError("affective_history_invalid")
        for name in ("source_input_id", "audit_event_id", "explanation_action_id"):
            _optional(getattr(self, name))
        if self.explanation_state == "available" and self.explanation_action_id is not None:
            raise ValueError("affective_history_invalid")
        if self.explanation_state == "erased" and any(
            getattr(self, name) is None
            for name in ("source_input_id", "audit_event_id", "explanation_action_id")
        ):
            raise ValueError("affective_history_invalid")


@dataclass(frozen=True, slots=True)
class AffectiveTransitionHistory:
    """Exact retained numbers and metadata; contains no proposal or reasons."""

    transition_id: str
    identity_id: str
    interaction_id: str
    source_message_id: str
    trace_id: str
    before: AffectiveStateSnapshot
    after: AffectiveStateSnapshot
    applied_delta: AffectiveDelta
    mood_delta: MoodDelta
    appraisal_scores: tuple[tuple[str, float], ...]
    appraisal_confidence: float
    source_refs: tuple[str, ...]
    provider: str
    model: str
    appraisal_method: str
    committed_at: datetime
    explanation_state: str
    source_input_id: str | None
    audit_event_id: str | None
    explanation_action_id: str | None


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("affective_history_invalid")
        result[key] = value
    return result


def _json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value, object_pairs_hook=_pairs)
        except (ValueError, RecursionError):
            raise ValueError("affective_history_invalid") from None
    return value


def _object(value: object, fields: frozenset[str]) -> dict[str, object]:
    decoded = _json(value)
    if not isinstance(decoded, dict) or set(decoded) != fields:
        raise ValueError("affective_history_invalid")
    return cast(dict[str, object], decoded)


def _number(value: object, *, signed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("affective_history_invalid")
    number = float(value)
    if not math.isfinite(number) or not (-1.0 if signed else 0.0) <= number <= 1.0:
        raise ValueError("affective_history_invalid")
    return number


def _version(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("affective_history_invalid")
    return value


def _time(value: object, *, stored_column: bool = True) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("affective_history_invalid") from None
        if value.tzinfo is None:
            if not stored_column:
                raise ValueError("affective_history_invalid")
            value = value.replace(tzinfo=UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("affective_history_invalid")
    return value.astimezone(UTC)


def _text(value: object, *, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ValueError("affective_history_invalid")
    return non_blank(value, "affective_history_invalid", maximum=maximum)


def _optional(value: object) -> str | None:
    return None if value is None else _text(value)


def _snapshot(value: object) -> AffectiveStateSnapshot:
    data = _object(value, SNAPSHOT_FIELDS)
    fast = _object(data["fast"], frozenset(FastAffectiveState.field_names()))
    mood = _object(data["mood"], frozenset(MoodState.field_names()))
    if any(
        _version(data[name]) != 1
        for name in (
            "schema_version",
            "emotion_policy_version",
            "appraisal_schema_version",
            "mood_policy_version",
        )
    ):
        raise ValueError("affective_history_invalid")
    return AffectiveStateSnapshot(
        identity_id=_text(data["identity_id"]),
        schema_version=_version(data["schema_version"]),
        state_version=_version(data["state_version"]),
        mood_version=_version(data["mood_version"]),
        as_of=_time(data["as_of"], stored_column=False),
        emotion_policy_version=_version(data["emotion_policy_version"]),
        appraisal_schema_version=_version(data["appraisal_schema_version"]),
        mood_policy_version=_version(data["mood_policy_version"]),
        fast=FastAffectiveState.from_mapping(
            {key: _number(item, signed=key == "valence") for key, item in fast.items()}
        ),
        mood=MoodState.from_mapping(
            {key: _number(item, signed=key == "valence") for key, item in mood.items()}
        ),
    )


def validate_affective_numeric_record(row: Mapping[str, object]) -> AffectiveTransitionHistory:
    """Validate raw retained JSON without coercion or reading an explanation body."""
    try:
        before, after = _snapshot(row["state_before"]), _snapshot(row["state_after"])
        identity = _text(row["identity_id"])
        appraisal = _object(row["appraisal_payload"], frozenset(APPRAISAL_FIELDS))
        delta = _object(row["applied_delta"], frozenset(FastAffectiveState.field_names()))
        mood_delta = _object(row["mood_delta"], frozenset(MoodState.field_names()))
        refs = _json(row["source_refs"])
        if not isinstance(refs, list | tuple) or not refs:
            raise ValueError("affective_history_invalid")
        source_refs = tuple(_text(item) for item in refs)
        if len(source_refs) != len(set(source_refs)):
            raise ValueError("affective_history_invalid")
        committed = _time(row["committed_at"])
        if (
            before.identity_id != identity
            or after.identity_id != identity
            or after.state_version != before.state_version + 1
            or after.mood_version != before.mood_version + 1
            or before.as_of != after.as_of
            or committed < after.as_of
            or _time(row["appraised_at"]) != after.as_of
        ):
            raise ValueError("affective_history_invalid")
        for name, expected in (
            ("base_state_version", before.state_version),
            ("resulting_state_version", after.state_version),
            ("base_mood_version", before.mood_version),
            ("resulting_mood_version", after.mood_version),
            ("emotion_policy_version", after.emotion_policy_version),
            ("mood_policy_version", after.mood_policy_version),
            ("appraisal_schema_version", after.appraisal_schema_version),
        ):
            if _version(row[name]) != expected:
                raise ValueError("affective_history_invalid")
        state = row.get("explanation_state", "available")
        action = _optional(row.get("explanation_action_id"))
        input_id, audit_id = (
            _optional(row.get("source_input_id")),
            _optional(row.get("audit_event_id")),
        )
        if state not in {"available", "erased"} or (state == "available" and action is not None):
            raise ValueError("affective_history_invalid")
        if state == "erased" and (action is None or input_id is None or audit_id is None):
            raise ValueError("affective_history_invalid")
        return AffectiveTransitionHistory(
            transition_id=_text(row["transition_id"]),
            identity_id=identity,
            interaction_id=_text(row["interaction_id"]),
            source_message_id=_text(row["source_message_id"]),
            trace_id=_text(row["trace_id"]),
            before=before,
            after=after,
            applied_delta=AffectiveDelta.from_mapping(
                {key: _number(value, signed=True) for key, value in delta.items()}
            ),
            mood_delta=MoodDelta.from_mapping(
                {key: _number(value, signed=True) for key, value in mood_delta.items()}
            ),
            appraisal_scores=tuple(
                (key, _number(appraisal[key], signed=key in {"pleasantness", "confidence_signal"}))
                for key in APPRAISAL_FIELDS
            ),
            appraisal_confidence=_number(row["appraisal_confidence"]),
            source_refs=source_refs,
            provider=_text(row["provider"]),
            model=_text(row["model"], maximum=256),
            appraisal_method=_text(row["appraisal_method"]),
            committed_at=committed,
            explanation_state=state,
            source_input_id=input_id,
            audit_event_id=audit_id,
            explanation_action_id=action,
        )
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("affective_history_invalid") from None
