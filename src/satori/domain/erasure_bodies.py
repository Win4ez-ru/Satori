"""Bounded owner body destruction, separate from intent and full physical erasure."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from satori.domain.erasure_appraisal import AppraisalBodyAction, AppraisalBodyProposal
from satori.domain.erasure_dialogue import DialogueBodyAction, DialogueBodyProposal
from satori.domain.erasure_knowledge import KnowledgeBodyAction, KnowledgeBodyProposal
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonProposal,
)
from satori.domain.erasure_reflection import ReflectionBodyAction, ReflectionBodyProposal
from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

BODY_ERASURE_POLICY_VERSION = "episode_body_erasure_v1"
MAX_BODY_TARGETS = 256


@dataclass(frozen=True, slots=True)
class BodyErasureTarget:
    """Private exact owner selector and fingerprint; never a public payload list."""

    memory_id: str
    aggregate_version: int
    body_digest: str
    terminal_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", non_blank(self.memory_id, "memory_id", maximum=128))
        positive_version(self.aggregate_version, "aggregate_version")
        sha256_hex(self.body_digest, "body_digest")
        sha256_hex(self.terminal_digest, "terminal_digest")


@dataclass(frozen=True, slots=True)
class BodyErasureProposal:
    root: CanonicalRootRef
    intent_request_id: str
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    expected_body_revision: int
    approval_digest: str
    store_digest: str
    schema_digest: str
    targets: tuple[BodyErasureTarget, ...]
    policy_version: str = BODY_ERASURE_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_body_root")
        for name in ("intent_request_id", "request_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        for name in (
            "expected_source_generation",
            "expected_erasure_revision",
            "expected_body_revision",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid_body_revision")
        for name in ("approval_digest", "store_digest", "schema_digest"):
            sha256_hex(getattr(self, name), name)
        targets = tuple(self.targets)
        if not 1 <= len(targets) <= MAX_BODY_TARGETS:
            raise ValueError("body_target_capacity_exceeded")
        if any(not isinstance(target, BodyErasureTarget) for target in targets):
            raise ValueError("invalid_body_target")
        if tuple(sorted(targets, key=lambda item: item.memory_id)) != targets or len(
            {t.memory_id for t in targets}
        ) != len(targets):
            raise ValueError("body_targets_must_be_unique_and_ordered")
        if self.policy_version != BODY_ERASURE_POLICY_VERSION:
            raise ValueError("unsupported_body_policy")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class BodyErasureAction:
    proposal: BodyErasureProposal
    action_id: str
    floor_id: str
    installation_id: str
    result_body_revision: int
    decided_at: datetime
    trace_id: str
    approved_by: str = "local_operator"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, BodyErasureProposal):
            raise ValueError("invalid_body_proposal")
        for name in ("action_id", "floor_id", "installation_id", "trace_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if (
            type(self.result_body_revision) is not int
            or self.result_body_revision != self.proposal.expected_body_revision + 1
        ):
            raise ValueError("invalid_body_transition")
        if self.approved_by != "local_operator":
            raise ValueError("invalid_body_approval")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))

    def matches(self, proposal: BodyErasureProposal) -> bool:
        return self.proposal == proposal


@dataclass(frozen=True, slots=True)
class ErasedEpisodeHeader:
    """Historical attribution only; does not assert an available summary or quotation."""

    memory_id: str
    aggregate_version: int
    action_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BodyPlan:
    root: CanonicalRootRef
    targets: tuple[BodyErasureTarget, ...]
    store_digest: str
    schema_digest: str
    scanned_rows: int
    quotes: int
    vectors: int
    already_erased: int
    blockers: tuple[str, ...]


type BodyAction = (
    BodyErasureAction
    | DialogueBodyAction
    | KnowledgeBodyAction
    | ReflectionBodyAction
    | AppraisalBodyAction
    | CheckpointReasonAction
)
type BodyProposal = (
    BodyErasureProposal
    | DialogueBodyProposal
    | KnowledgeBodyProposal
    | ReflectionBodyProposal
    | AppraisalBodyProposal
    | CheckpointReasonProposal
)


def proposal_identity(proposal: BodyProposal) -> str:
    if isinstance(proposal, CheckpointReasonProposal):
        return proposal.scope.identity_id
    root = source_proposal_root(proposal)
    if root is None:
        raise ValueError("unsupported_body_proposal")
    return root.identity_id


def proposal_lineage(proposal: BodyProposal) -> str:
    if isinstance(proposal, CheckpointReasonProposal):
        return proposal.scope.lineage_id
    root = source_proposal_root(proposal)
    if root is None:
        raise ValueError("unsupported_body_proposal")
    return root.lineage_id


def source_proposal_root(proposal: BodyProposal) -> CanonicalRootRef | None:
    """Identity-owned operator reasons never acquire canonical source attribution."""
    if isinstance(proposal, CheckpointReasonProposal):
        return None
    if isinstance(
        proposal,
        (
            BodyErasureProposal,
            DialogueBodyProposal,
            KnowledgeBodyProposal,
            ReflectionBodyProposal,
            AppraisalBodyProposal,
        ),
    ):
        return proposal.root
    raise ValueError("unsupported_body_proposal")


def _action_proposal(action: BodyAction) -> BodyProposal:
    if isinstance(
        action,
        (
            BodyErasureAction,
            DialogueBodyAction,
            KnowledgeBodyAction,
            ReflectionBodyAction,
            AppraisalBodyAction,
            CheckpointReasonAction,
        ),
    ):
        return action.proposal
    raise ValueError("unsupported_body_action")


def action_identity(action: BodyAction) -> str:
    return proposal_identity(_action_proposal(action))


def action_lineage(action: BodyAction) -> str:
    return proposal_lineage(_action_proposal(action))


def source_root(action: BodyAction) -> CanonicalRootRef | None:
    return source_proposal_root(_action_proposal(action))


def body_reference_count(actions: tuple[BodyAction, ...]) -> int:
    """Bound work over every retained approval, including dialogue attribution witnesses."""
    return sum(
        len(action.proposal.targets)
        + (
            sum(len(target.witnesses) for target in action.proposal.targets)
            if isinstance(action, DialogueBodyAction)
            else (
                sum(len(target.witness_transitions) for target in action.proposal.targets)
                if isinstance(action, KnowledgeBodyAction)
                else (
                    sum(target.metadata_reference_count for target in action.proposal.targets)
                    if isinstance(
                        action, (ReflectionBodyAction, AppraisalBodyAction, CheckpointReasonAction)
                    )
                    else 0
                )
            )
        )
        for action in actions
    )


def body_actions_digest(actions: tuple[BodyAction, ...]) -> str:
    """One opaque whole-head proof; private per-body fingerprints stay private."""
    material = [asdict(action) for action in actions]
    return hashlib.sha256(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat()
        ).encode()
    ).hexdigest()
