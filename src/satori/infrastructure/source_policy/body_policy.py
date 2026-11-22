"""Exact private body-action envelopes and bounded integrity validation."""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime

from satori.domain.erasure_appraisal import (
    APPRAISAL_BODY_ERASURE_POLICY_VERSION,
    MAX_APPRAISAL_TARGETS,
    AppraisalBodyAction,
    AppraisalBodyProposal,
    AppraisalBodyTarget,
)
from satori.domain.erasure_bodies import (
    BODY_ERASURE_POLICY_VERSION,
    MAX_BODY_TARGETS,
    BodyAction,
    BodyErasureAction,
    BodyErasureProposal,
    BodyErasureTarget,
    BodyProposal,
)
from satori.domain.erasure_dialogue import (
    DIALOGUE_BODY_ERASURE_POLICY_VERSION,
    MAX_DIALOGUE_TARGETS,
    DialogueBodyAction,
    DialogueBodyProposal,
    DialogueBodyTarget,
    HistoricalEvidenceTable,
    HistoricalEvidenceWitness,
)
from satori.domain.erasure_knowledge import (
    KNOWLEDGE_BODY_ERASURE_POLICY_VERSION,
    MAX_KNOWLEDGE_TARGETS,
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyProposal,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
    KnowledgeWitnessTransition,
)
from satori.domain.erasure_personality_reasons import (
    CHECKPOINT_REASON_ERASURE_POLICY_VERSION,
    MAX_CHECKPOINT_REASON_TARGETS,
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonProposal,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.erasure_reflection import (
    MAX_REFLECTION_TARGETS,
    REFLECTION_BODY_ERASURE_POLICY_VERSION,
    ReflectionBodyAction,
    ReflectionBodyProposal,
    ReflectionBodyTarget,
)
from satori.domain.source_policy import CanonicalRootRef, SourcePolicyError

MAX_BODY_ACTIONS = 4096
MAX_BODY_REFERENCES = 1_000_000
MAX_TARGET_JSON_BYTES = 131_072
MAX_BODY_LOGICAL_BYTES = 64 * 1024 * 1024


def proposal_references(proposal: BodyProposal) -> int:
    """Witnesses consume the same floor budget as exact owner selectors."""
    return len(proposal.targets) + (
        sum(len(target.witnesses) for target in proposal.targets)
        if isinstance(proposal, DialogueBodyProposal)
        else (
            sum(len(target.witness_transitions) for target in proposal.targets)
            if isinstance(proposal, KnowledgeBodyProposal)
            else (
                sum(target.metadata_reference_count for target in proposal.targets)
                if isinstance(
                    proposal,
                    (ReflectionBodyProposal, AppraisalBodyProposal, CheckpointReasonProposal),
                )
                else 0
            )
        )
    )


def encode_targets(
    targets: tuple[
        BodyErasureTarget
        | DialogueBodyTarget
        | KnowledgeBodyTarget
        | ReflectionBodyTarget
        | AppraisalBodyTarget
        | CheckpointReasonTarget,
        ...,
    ],
) -> str:
    try:
        encoded = json.dumps(
            [asdict(target) for target in targets], sort_keys=True, separators=(",", ":")
        )
    except RecursionError:
        raise SourcePolicyError("policy_integrity_invalid") from None
    if len(encoded.encode("utf-8")) > MAX_TARGET_JSON_BYTES:
        raise SourcePolicyError("policy_body_capacity_exceeded")
    return encoded


def _episode_targets(raw: list[object]) -> tuple[BodyErasureTarget, ...]:
    if not 1 <= len(raw) <= MAX_BODY_TARGETS:
        raise ValueError("invalid_body_targets")
    targets = []
    for target in raw:
        if (
            not isinstance(target, dict)
            or set(target) != {"memory_id", "aggregate_version", "body_digest", "terminal_digest"}
            or type(target["memory_id"]) is not str
            or type(target["aggregate_version"]) is not int
            or type(target["body_digest"]) is not str
            or type(target["terminal_digest"]) is not str
        ):
            raise ValueError("invalid_body_targets")
        targets.append(BodyErasureTarget(**target))
    return tuple(targets)


def _dialogue_targets(raw: list[object]) -> tuple[DialogueBodyTarget, ...]:
    if not 1 <= len(raw) <= MAX_DIALOGUE_TARGETS:
        raise ValueError("invalid_dialogue_targets")
    targets = []
    for target in raw:
        if (
            not isinstance(target, dict)
            or set(target)
            != {"message_id", "schema_version", "body_digest", "terminal_digest", "witnesses"}
            or type(target["message_id"]) is not str
            or type(target["schema_version"]) is not int
            or type(target["body_digest"]) is not str
            or type(target["terminal_digest"]) is not str
            or not isinstance(target["witnesses"], list)
        ):
            raise ValueError("invalid_dialogue_targets")
        witnesses = []
        for witness in target["witnesses"]:
            if (
                not isinstance(witness, dict)
                or set(witness) != {"table", "evidence_id", "digest"}
                or any(type(witness[key]) is not str for key in witness)
            ):
                raise ValueError("invalid_dialogue_witness")
            witnesses.append(
                HistoricalEvidenceWitness(
                    HistoricalEvidenceTable(witness["table"]),
                    witness["evidence_id"],
                    witness["digest"],
                )
            )
        targets.append(
            DialogueBodyTarget(
                target["message_id"],
                target["schema_version"],
                target["body_digest"],
                target["terminal_digest"],
                tuple(witnesses),
            )
        )
    return tuple(targets)


def _knowledge_targets(raw: list[object]) -> tuple[KnowledgeBodyTarget, ...]:
    if not 1 <= len(raw) <= MAX_KNOWLEDGE_TARGETS:
        raise ValueError("invalid_knowledge_targets")
    targets = []
    for target in raw:
        if (
            not isinstance(target, dict)
            or set(target)
            != {
                "kind",
                "aggregate_id",
                "aggregate_version",
                "body_digest",
                "terminal_digest",
                "witness_transitions",
            }
            or any(
                type(target[key]) is not str
                for key in (
                    "kind",
                    "aggregate_id",
                    "body_digest",
                    "terminal_digest",
                )
            )
            or type(target["aggregate_version"]) is not int
            or not isinstance(target["witness_transitions"], list)
        ):
            raise ValueError("invalid_knowledge_targets")
        transitions = []
        for witness in target["witness_transitions"]:
            if (
                not isinstance(witness, dict)
                or set(witness)
                != {
                    "table",
                    "evidence_id",
                    "root",
                    "before_digest",
                    "after_digest",
                    "origin",
                    "predecessor_action_id",
                }
                or any(
                    type(witness[key]) is not str
                    for key in (
                        "table",
                        "evidence_id",
                        "before_digest",
                        "after_digest",
                        "origin",
                    )
                )
                or (
                    witness["predecessor_action_id"] is not None
                    and type(witness["predecessor_action_id"]) is not str
                )
                or not isinstance(witness["root"], dict)
                or set(witness["root"])
                != {
                    "lineage_id",
                    "identity_id",
                    "counterparty_id",
                    "session_id",
                    "interaction_id",
                    "message_id",
                }
                or any(type(value) is not str for value in witness["root"].values())
            ):
                raise ValueError("invalid_knowledge_witness")
            transitions.append(
                KnowledgeWitnessTransition(
                    HistoricalEvidenceTable(witness["table"]),
                    witness["evidence_id"],
                    CanonicalRootRef(**witness["root"]),
                    witness["before_digest"],
                    witness["after_digest"],
                    KnowledgeWitnessOrigin(witness["origin"]),
                    witness["predecessor_action_id"],
                )
            )
        targets.append(
            KnowledgeBodyTarget(
                KnowledgeBodyKind(target["kind"]),
                target["aggregate_id"],
                target["aggregate_version"],
                target["body_digest"],
                target["terminal_digest"],
                tuple(transitions),
            )
        )
    return tuple(targets)


def _reflection_targets(raw: list[object]) -> tuple[ReflectionBodyTarget, ...]:
    if not 1 <= len(raw) <= MAX_REFLECTION_TARGETS:
        raise ValueError("invalid_reflection_targets")
    targets = []
    for target in raw:
        if (
            not isinstance(target, dict)
            or set(target)
            != {
                "proposal_id",
                "run_id",
                "run_version",
                "payload_version",
                "body_digest",
                "terminal_digest",
                "metadata_reference_count",
            }
            or any(
                type(target[key]) is not str
                for key in ("proposal_id", "run_id", "body_digest", "terminal_digest")
            )
            or any(
                type(target[key]) is not int
                for key in ("run_version", "payload_version", "metadata_reference_count")
            )
        ):
            raise ValueError("invalid_reflection_targets")
        targets.append(ReflectionBodyTarget(**target))
    return tuple(targets)


def _appraisal_targets(raw: list[object]) -> tuple[AppraisalBodyTarget, ...]:
    if not 1 <= len(raw) <= MAX_APPRAISAL_TARGETS:
        raise ValueError("invalid_appraisal_targets")
    targets = []
    for target in raw:
        if (
            not isinstance(target, dict)
            or set(target)
            != {
                "transition_id",
                "source_input_id",
                "audit_event_id",
                "payload_version",
                "body_digest",
                "terminal_digest",
                "metadata_reference_count",
                "input_closure_digest",
            }
            or any(
                type(target[key]) is not str
                for key in (
                    "transition_id",
                    "source_input_id",
                    "audit_event_id",
                    "body_digest",
                    "terminal_digest",
                    "input_closure_digest",
                )
            )
            or any(
                type(target[key]) is not int
                for key in ("payload_version", "metadata_reference_count")
            )
        ):
            raise ValueError("invalid_appraisal_targets")
        targets.append(AppraisalBodyTarget(**target))
    return tuple(targets)


def _checkpoint_reason_targets(raw: list[object]) -> tuple[CheckpointReasonTarget, ...]:
    if not 1 <= len(raw) <= MAX_CHECKPOINT_REASON_TARGETS:
        raise ValueError("invalid_checkpoint_reason_targets")
    targets = []
    for target in raw:
        if (
            not isinstance(target, dict)
            or set(target)
            != {
                "selector",
                "payload_version",
                "body_digest",
                "terminal_digest",
                "metadata_reference_count",
            }
            or not isinstance(target["selector"], dict)
            or set(target["selector"]) != {"kind", "record_id"}
            or any(type(target["selector"][key]) is not str for key in ("kind", "record_id"))
            or any(type(target[key]) is not str for key in ("body_digest", "terminal_digest"))
            or any(
                type(target[key]) is not int
                for key in ("payload_version", "metadata_reference_count")
            )
        ):
            raise ValueError("invalid_checkpoint_reason_targets")
        targets.append(
            CheckpointReasonTarget(
                CheckpointReasonSelector(
                    CheckpointReasonKind(target["selector"]["kind"]),
                    target["selector"]["record_id"],
                ),
                target["payload_version"],
                target["body_digest"],
                target["terminal_digest"],
                target["metadata_reference_count"],
            )
        )
    return tuple(targets)


def decode_action(row: sqlite3.Row, *, floor_id: str, installation_id: str) -> BodyAction:
    encoded = row["targets"]
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_TARGET_JSON_BYTES:
        raise ValueError("invalid_body_targets")
    try:
        raw = json.loads(encoded)
    except RecursionError:
        raise ValueError("invalid_body_targets") from None
    if not isinstance(raw, list):
        raise ValueError("invalid_body_targets")
    if row["policy_version"] == CHECKPOINT_REASON_ERASURE_POLICY_VERSION:
        if any(
            row[name] is not None
            for name in (
                "counterparty_id",
                "session_id",
                "interaction_id",
                "message_id",
                "intent_request_id",
            )
        ):
            raise ValueError("invalid_checkpoint_reason_scope")
        checkpoint_reason = CheckpointReasonProposal(
            CheckpointReasonScope(row["identity_id"], row["lineage_id"]),
            row["request_id"],
            row["expected_source_generation"],
            row["expected_erasure_revision"],
            row["expected_body_revision"],
            row["approval_digest"],
            row["store_digest"],
            row["schema_digest"],
            _checkpoint_reason_targets(raw),
            row["policy_version"],
        )
        return CheckpointReasonAction(
            checkpoint_reason,
            row["action_id"],
            floor_id,
            installation_id,
            row["result_body_revision"],
            datetime.fromisoformat(row["decided_at"]),
            row["trace_id"],
        )
    root = CanonicalRootRef(
        row["lineage_id"],
        row["identity_id"],
        row["counterparty_id"],
        row["session_id"],
        row["interaction_id"],
        row["message_id"],
    )
    proposal: BodyProposal
    if row["policy_version"] == APPRAISAL_BODY_ERASURE_POLICY_VERSION:
        appraisal = AppraisalBodyProposal(
            root,
            row["intent_request_id"],
            row["request_id"],
            row["expected_source_generation"],
            row["expected_erasure_revision"],
            row["expected_body_revision"],
            row["approval_digest"],
            row["store_digest"],
            row["schema_digest"],
            _appraisal_targets(raw),
            row["policy_version"],
        )
        return AppraisalBodyAction(
            appraisal,
            row["action_id"],
            floor_id,
            installation_id,
            row["result_body_revision"],
            datetime.fromisoformat(row["decided_at"]),
            row["trace_id"],
        )
    if row["policy_version"] == REFLECTION_BODY_ERASURE_POLICY_VERSION:
        reflection = ReflectionBodyProposal(
            root,
            row["intent_request_id"],
            row["request_id"],
            row["expected_source_generation"],
            row["expected_erasure_revision"],
            row["expected_body_revision"],
            row["approval_digest"],
            row["store_digest"],
            row["schema_digest"],
            _reflection_targets(raw),
            row["policy_version"],
        )
        return ReflectionBodyAction(
            reflection,
            row["action_id"],
            floor_id,
            installation_id,
            row["result_body_revision"],
            datetime.fromisoformat(row["decided_at"]),
            row["trace_id"],
        )
    if row["policy_version"] == BODY_ERASURE_POLICY_VERSION:
        proposal = BodyErasureProposal(
            root,
            row["intent_request_id"],
            row["request_id"],
            row["expected_source_generation"],
            row["expected_erasure_revision"],
            row["expected_body_revision"],
            row["approval_digest"],
            row["store_digest"],
            row["schema_digest"],
            _episode_targets(raw),
            row["policy_version"],
        )
        return BodyErasureAction(
            proposal,
            row["action_id"],
            floor_id,
            installation_id,
            row["result_body_revision"],
            datetime.fromisoformat(row["decided_at"]),
            row["trace_id"],
        )
    if row["policy_version"] == KNOWLEDGE_BODY_ERASURE_POLICY_VERSION:
        knowledge = KnowledgeBodyProposal(
            root,
            row["intent_request_id"],
            row["request_id"],
            row["expected_source_generation"],
            row["expected_erasure_revision"],
            row["expected_body_revision"],
            row["approval_digest"],
            row["store_digest"],
            row["schema_digest"],
            _knowledge_targets(raw),
            row["policy_version"],
        )
        return KnowledgeBodyAction(
            knowledge,
            row["action_id"],
            floor_id,
            installation_id,
            row["result_body_revision"],
            datetime.fromisoformat(row["decided_at"]),
            row["trace_id"],
        )
    if row["policy_version"] != DIALOGUE_BODY_ERASURE_POLICY_VERSION:
        raise ValueError("unsupported_body_policy")
    dialogue = DialogueBodyProposal(
        root,
        row["intent_request_id"],
        row["request_id"],
        row["expected_source_generation"],
        row["expected_erasure_revision"],
        row["expected_body_revision"],
        row["approval_digest"],
        row["store_digest"],
        row["schema_digest"],
        _dialogue_targets(raw),
        row["policy_version"],
    )
    return DialogueBodyAction(
        dialogue,
        row["action_id"],
        floor_id,
        installation_id,
        row["result_body_revision"],
        datetime.fromisoformat(row["decided_at"]),
        row["trace_id"],
    )


def body_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Bound material before fetching private JSON envelopes."""
    count, logical_bytes = connection.execute(
        "SELECT count(*),coalesce(sum(length(CAST(targets AS BLOB))),0) FROM policy_body_actions"
    ).fetchone()
    if count > MAX_BODY_ACTIONS or logical_bytes > MAX_BODY_LOGICAL_BYTES:
        raise SourcePolicyError("policy_body_capacity_exceeded")
    return connection.execute(
        "SELECT * FROM policy_body_actions LIMIT ?", (MAX_BODY_ACTIONS + 1,)
    ).fetchall()


def validate_body_actions(
    connection: sqlite3.Connection, *, floor_id: str, installation_id: str
) -> None:
    invalid = connection.execute(
        "SELECT 1 FROM policy_body_actions b LEFT JOIN policy_lineages l USING(identity_id) "
        "LEFT JOIN policy_erasure_intents i ON i.request_id=b.intent_request_id "
        "LEFT JOIN policy_exclusions e ON e.identity_id=b.identity_id "
        "AND e.message_id=b.message_id "
        "WHERE l.identity_id IS NULL OR b.lineage_id!=l.lineage_id "
        "OR b.expected_source_generation>l.generation "
        "OR b.expected_erasure_revision>l.erasure_revision "
        "OR b.result_body_revision>l.body_revision "
        "OR b.result_body_revision!=b.expected_body_revision+1 "
        "OR (b.policy_version='checkpoint_reason_erasure_v1' AND "
        "(b.counterparty_id IS NOT NULL OR b.session_id IS NOT NULL OR "
        "b.interaction_id IS NOT NULL OR b.message_id IS NOT NULL "
        "OR b.intent_request_id IS NOT NULL)) "
        "OR (b.policy_version!='checkpoint_reason_erasure_v1' AND "
        "(i.request_id IS NULL OR e.message_id IS NULL OR "
        "b.counterparty_id IS NULL OR b.session_id IS NULL OR b.interaction_id IS NULL OR "
        "b.message_id IS NULL OR b.intent_request_id IS NULL OR "
        "b.identity_id!=i.identity_id OR b.lineage_id!=i.lineage_id OR "
        "b.counterparty_id!=i.counterparty_id OR b.session_id!=i.session_id OR "
        "b.interaction_id!=i.interaction_id OR b.message_id!=i.message_id OR "
        "b.counterparty_id!=e.counterparty_id OR b.session_id!=e.session_id OR "
        "b.interaction_id!=e.interaction_id OR b.expected_source_generation<e.generation OR "
        "b.expected_source_generation<i.expected_source_generation OR "
        "b.expected_erasure_revision<i.result_erasure_revision)) LIMIT 1"
    ).fetchone()
    inconsistent = connection.execute(
        "SELECT 1 FROM policy_lineages l WHERE l.body_revision!=(SELECT count(*) "
        "FROM policy_body_actions b WHERE b.identity_id=l.identity_id) LIMIT 1"
    ).fetchone()
    conflicting = connection.execute(
        "SELECT 1 FROM policy_body_actions b LEFT JOIN policy_receipts r USING(request_id) "
        "LEFT JOIN policy_erasure_intents i USING(request_id) "
        "WHERE r.request_id IS NOT NULL OR i.request_id IS NOT NULL LIMIT 1"
    ).fetchone()
    if any(row is not None for row in (invalid, inconsistent, conflicting)):
        raise SourcePolicyError("policy_integrity_invalid")
    references = 0
    actions = []
    try:
        for row in body_rows(connection):
            action = decode_action(row, floor_id=floor_id, installation_id=installation_id)
            actions.append(action)
            references += proposal_references(action.proposal)
        validate_knowledge_origins(tuple(actions))
    except (ValueError, TypeError, KeyError):
        raise SourcePolicyError("policy_integrity_invalid") from None
    if references > MAX_BODY_REFERENCES:
        raise SourcePolicyError("policy_body_capacity_exceeded")


def validate_knowledge_origins(actions: tuple[BodyAction, ...]) -> None:
    """Floor metadata binds exact earlier witnesses; physical proof stays with the owners."""
    latest: dict[
        tuple[str, str, str, str],
        tuple[str, frozenset[str], KnowledgeWitnessOrigin, CanonicalRootRef],
    ] = {}
    historical: dict[tuple[str, str, str, str], set[str]] = {}
    for action in sorted(actions, key=lambda item: item.result_body_revision):
        if isinstance(action, DialogueBodyAction):
            for target in action.proposal.targets:
                for witness in target.witnesses:
                    key = (
                        action.proposal.root.identity_id,
                        witness.table.value,
                        witness.evidence_id,
                        action.proposal.root.message_id,
                    )
                    previous = latest.get(key)
                    if previous is not None and previous[2] is KnowledgeWitnessOrigin.KNOWLEDGE:
                        # A later canonical anchor does not rewind an earlier approved
                        # evidence transition, including its still-pending original state.
                        if witness.digest not in historical[key]:
                            raise ValueError("knowledge_witness_predecessor_conflict")
                        continue
                    historical.setdefault(key, set()).add(witness.digest)
                    latest[key] = (
                        action.action_id,
                        frozenset((witness.digest,)),
                        KnowledgeWitnessOrigin.DIALOGUE,
                        action.proposal.root,
                    )
        elif isinstance(action, KnowledgeBodyAction):
            for knowledge_target in action.proposal.targets:
                for transition in knowledge_target.witness_transitions:
                    key = (
                        transition.root.identity_id,
                        transition.table.value,
                        transition.evidence_id,
                        transition.root.message_id,
                    )
                    previous = latest.get(key)
                    if transition.origin is KnowledgeWitnessOrigin.LIVE:
                        if previous is not None:
                            raise ValueError("knowledge_witness_predecessor_conflict")
                    elif (
                        previous is None
                        or previous[0] != transition.predecessor_action_id
                        or previous[2] is not transition.origin
                        or previous[3] != transition.root
                        or transition.before_digest not in previous[1]
                    ):
                        raise ValueError("knowledge_witness_predecessor_conflict")
                    latest[key] = (
                        action.action_id,
                        frozenset((transition.before_digest, transition.after_digest)),
                        KnowledgeWitnessOrigin.KNOWLEDGE,
                        transition.root,
                    )
                    historical.setdefault(key, set()).update(
                        (transition.before_digest, transition.after_digest)
                    )
