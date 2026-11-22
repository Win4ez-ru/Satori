"""Single strict canonical representation of private floor action projections."""

import json
from collections.abc import Iterable, Mapping
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from satori.domain.erasure_appraisal import (
    APPRAISAL_BODY_ERASURE_POLICY_VERSION,
    AppraisalBodyAction,
)
from satori.domain.erasure_bodies import BODY_ERASURE_POLICY_VERSION, BodyAction, BodyErasureAction
from satori.domain.erasure_dialogue import DIALOGUE_BODY_ERASURE_POLICY_VERSION, DialogueBodyAction
from satori.domain.erasure_knowledge import (
    KNOWLEDGE_BODY_ERASURE_POLICY_VERSION,
    KnowledgeBodyAction,
)
from satori.domain.erasure_personality_reasons import (
    CHECKPOINT_REASON_ERASURE_POLICY_VERSION,
    CheckpointReasonAction,
)
from satori.domain.erasure_reflection import (
    REFLECTION_BODY_ERASURE_POLICY_VERSION,
    ReflectionBodyAction,
)

_ADAPTER = TypeAdapter(BodyErasureAction)
_DIALOGUE_ADAPTER = TypeAdapter(DialogueBodyAction)
_KNOWLEDGE_ADAPTER = TypeAdapter(KnowledgeBodyAction)
_REFLECTION_ADAPTER = TypeAdapter(ReflectionBodyAction)
_APPRAISAL_ADAPTER = TypeAdapter(AppraisalBodyAction)
_CHECKPOINT_REASON_ADAPTER = TypeAdapter(CheckpointReasonAction)


def encode_body_action(action: BodyAction) -> dict[str, Any]:
    if isinstance(action, CheckpointReasonAction):
        return cast(dict[str, Any], _CHECKPOINT_REASON_ADAPTER.dump_python(action, mode="json"))
    if isinstance(action, AppraisalBodyAction):
        return cast(dict[str, Any], _APPRAISAL_ADAPTER.dump_python(action, mode="json"))
    if isinstance(action, ReflectionBodyAction):
        return cast(dict[str, Any], _REFLECTION_ADAPTER.dump_python(action, mode="json"))
    if isinstance(action, KnowledgeBodyAction):
        return cast(dict[str, Any], _KNOWLEDGE_ADAPTER.dump_python(action, mode="json"))
    if isinstance(action, DialogueBodyAction):
        return cast(dict[str, Any], _DIALOGUE_ADAPTER.dump_python(action, mode="json"))
    return cast(dict[str, Any], _ADAPTER.dump_python(action, mode="json"))


def decode_body_action(value: dict[str, Any]) -> BodyAction:
    try:
        return _decode_body_action(value)
    except RecursionError:
        raise ValueError("body_action_projection_corrupt") from None
    except ValidationError as error:
        if any(
            detail["type"] == "json_invalid"
            for detail in error.errors(
                include_input=False, include_context=False, include_url=False
            )
        ):
            raise ValueError("body_action_projection_corrupt") from None
        raise


def _decode_body_action(value: dict[str, Any]) -> BodyAction:
    if not isinstance(value, dict):
        raise ValueError("body_action_projection_corrupt")
    proposal = value.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("body_action_projection_corrupt")
    policy = proposal.get("policy_version")
    action: BodyAction
    if policy == BODY_ERASURE_POLICY_VERSION:
        action = _ADAPTER.validate_json(json.dumps(value, allow_nan=False), strict=True)
    elif policy == DIALOGUE_BODY_ERASURE_POLICY_VERSION:
        action = _DIALOGUE_ADAPTER.validate_json(json.dumps(value, allow_nan=False), strict=True)
    elif policy == KNOWLEDGE_BODY_ERASURE_POLICY_VERSION:
        action = _KNOWLEDGE_ADAPTER.validate_json(json.dumps(value, allow_nan=False), strict=True)
    elif policy == REFLECTION_BODY_ERASURE_POLICY_VERSION:
        action = _REFLECTION_ADAPTER.validate_json(json.dumps(value, allow_nan=False), strict=True)
    elif policy == APPRAISAL_BODY_ERASURE_POLICY_VERSION:
        action = _APPRAISAL_ADAPTER.validate_json(json.dumps(value, allow_nan=False), strict=True)
    elif policy == CHECKPOINT_REASON_ERASURE_POLICY_VERSION:
        action = _CHECKPOINT_REASON_ADAPTER.validate_json(
            json.dumps(value, allow_nan=False), strict=True
        )
    else:
        raise ValueError("body_action_projection_corrupt")
    if encode_body_action(action) != value:
        raise ValueError("body_action_projection_corrupt")
    return action


def decode_body_projection_rows(rows: Iterable[Mapping[str, object]]) -> tuple[BodyAction, ...]:
    """Decode admitted main rows; this parser alone confers no floor/head authority."""
    ordered: list[tuple[int, BodyAction]] = []
    try:
        for row in rows:
            revision = row["body_revision"]
            if type(revision) is not int or revision < 1:
                raise ValueError("invalid_projection_revision")
            raw = row["action"]
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict) or any(type(key) is not str for key in value):
                raise ValueError("invalid_projection_action")
            ordered.append((revision, decode_body_action(value)))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("body_action_projection_corrupt") from None
    return tuple(action for _revision, action in sorted(ordered, key=lambda item: item[0]))
