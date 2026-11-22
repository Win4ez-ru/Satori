"""One bounded read census; prospective seals never replace exact owner proof."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from satori.domain.audit_metadata import (
    AUDIT_METADATA_FIELDS_BY_FAMILY,
    AuditMetadataClassification,
    AuditMetadataCoverage,
)
from satori.domain.audit_metadata import (
    AuditMetadataCoverageStatus as Status,
)
from satori.domain.audit_metadata import (
    AuditMetadataFamily as Family,
)
from satori.domain.audit_metadata import (
    AuditMetadataUnknownReason as Reason,
)
from satori.domain.erasure_appraisal import AppraisalBodyAction
from satori.infrastructure.persistence.appraisal_material import (
    AppraisalMaterialIndex,
    AppraisalReceiptIndex,
    appraisal_target_terminal_proven,
)
from satori.infrastructure.persistence.appraisal_projection import latest_appraisal
from satori.infrastructure.persistence.audit_metadata_schema import (
    document,
    event_digest,
    integer,
    owner_record_field,
    string_list,
    text,
    utc_time,
    validate_event_shape,
)
from satori.infrastructure.persistence.erasure_body_codec import decode_body_projection_rows
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners

if TYPE_CHECKING:
    from satori.infrastructure.persistence.canonical_provenance import HistoricalProvenance
    from satori.infrastructure.persistence.erasure_inventory import Row, Store

MAX_AUDIT_WORK = 1_000_000

_OWNER_TABLES = {
    "episode_formation": ("episode_formation_decisions", "decision_id"),
    "memory_lifecycle": ("memory_lifecycle_decisions", "decision_id"),
    "semantic_formation": ("semantic_formation_decisions", "decision_id"),
    "user_model_formation": ("model_formation_decisions", "decision_id"),
    "world_model_formation": ("model_formation_decisions", "decision_id"),
    "position_formation": ("position_formation_decisions", "decision_id"),
    "reflection_position": ("reflection_outcomes", "outcome_id"),
    "reflection_inclination": ("reflection_outcomes", "outcome_id"),
    "reflection_personality": ("reflection_outcomes", "outcome_id"),
    "reflection_disabled": ("reflection_outcomes", "outcome_id"),
    "relationship_transition": ("relationship_decisions", "decision_id"),
    "checkpoint_approval": ("personality_checkpoint_approvals", "approval_id"),
    "personality_restore": ("personality_restore_events", "restore_id"),
    "source_withdrawal": ("owner_source_withdrawals", "receipt_id"),
}
_COGNITIVE = frozenset(
    {
        "episode_formation",
        "semantic_formation",
        "user_model_formation",
        "world_model_formation",
        "position_formation",
        "relationship_transition",
        "reflection_position",
        "reflection_inclination",
        "reflection_personality",
    }
)
_GROUPS = (
    ("memory_evidence", "memory_id"),
    ("semantic_claim_revisions", "decision_id"),
    ("user_model_claim_revisions", "decision_id"),
    ("world_model_claim_revisions", "decision_id"),
    ("satori_position_revisions", "decision_id"),
    ("satori_position_revisions", "reflection_outcome_id"),
    ("satori_inclination_revisions", "reflection_outcome_id"),
    ("personality_revisions", "reflection_outcome_id"),
    ("personality_evidence", "revision_id"),
    ("satori_inclination_evidence", "inclination_id"),
    ("reflection_sources", "run_id"),
)


class _ProofError(ValueError):
    def __init__(self, reason: Reason = Reason.OWNER_PROOF_UNPROVEN) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(slots=True)
class _WorkBudget:
    used: int = 0

    def add(self, amount: int) -> None:
        if self.used + amount > MAX_AUDIT_WORK:
            raise _ProofError(Reason.CAPACITY_EXCEEDED)
        self.used += amount


class _OwnerIndex:
    def __init__(
        self, store: Store, historical: HistoricalProvenance | None, work: _WorkBudget
    ) -> None:
        self.store = store
        self.work = work
        self.row_count = sum(len(rows) for rows in store.values())
        self.historical = historical
        self.indexes: dict[tuple[str, str], dict[str, Row]] = {}
        self.groups: dict[tuple[str, str], dict[str, tuple[Row, ...]]] = {}
        self.owners: dict[str, ProvenOwners] = {}
        self.admitted_targets: set[tuple[str, str, str, int]] = set()
        self.receipts: AppraisalReceiptIndex | None = None
        self.input_scopes: dict[str, tuple[str, str, tuple[str, ...]]] = {}
        self.reflection_people: dict[str, str] = {}
        self.receipt_failure = False
        for table, column in _GROUPS:
            self.work.add(len(store.get(table, ())))
            grouped: dict[str, list[Row]] = {}
            for row in store.get(table, ()):
                grouped.setdefault(str(row.get(column)), []).append(row)
            self.groups[table, column] = {key: tuple(rows) for key, rows in grouped.items()}

    def get(self, table: str, column: str, key: object) -> Row:
        self.work.add(1)
        pair = table, column
        if pair not in self.indexes:
            rows = self.store.get(table, ())
            self.work.add(len(rows))
            index = {str(row[column]): row for row in rows}
            if len(index) != len(rows):
                raise _ProofError()
            self.indexes[pair] = index
        result = self.indexes[pair].get(text(key))
        if result is None:
            raise _ProofError()
        return result

    def group(self, table: str, column: str, key: object) -> tuple[Row, ...]:
        rows = self.groups[table, column].get(str(key), ())
        self.work.add(len(rows))
        return rows

    def owner(self, identity: str) -> ProvenOwners:
        if identity not in self.owners:
            # Pay a conservative fixed-index row bound before another identity
            # traverses the whole store. This is work admission, not a cache grant.
            self.work.add(8 * self.row_count)
            self.owners[identity] = ProvenOwners(self.store, identity, historical=self.historical)
        return self.owners[identity]

    def scope(
        self,
        identity: str,
        interaction_id: object,
        *,
        person: object | None = None,
        message_id: object | None = None,
    ) -> Row:
        owners = self.owner(identity)
        interaction = self.get("conversation_interactions", "interaction_id", interaction_id)
        session = self.get("conversation_sessions", "session_id", interaction["session_id"])
        if (
            interaction.get("schema_version") != 1
            or session.get("schema_version") != 1
            or (
                session.get("identity_id") != identity
                or session.get("scope_verified") != 1
                or interaction.get("status") != "completed"
                or (person is not None and session.get("counterparty_id") != person)
            )
        ):
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        canonical = (
            owners.canonical_users.get(str(interaction_id))
            if message_id is None
            else text(message_id)
        )
        if canonical is None or not owners.canonical(canonical):
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        message = owners.messages[canonical]
        if message.get("schema_version") != 1 or message.get("interaction_id") != interaction_id:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        return session

    @staticmethod
    def same(details: Row, source: Row, names: tuple[str, ...]) -> None:
        for name in names:
            if type(details.get(name)) is not type(source.get(name)) or details.get(
                name
            ) != source.get(name):
                raise _ProofError()

    def input(
        self, binding: Row, identity: str, family: Family, interaction_id: object | None
    ) -> None:
        key = binding.get("source_input_id")
        if family.value not in _COGNITIVE:
            if key is not None:
                raise _ProofError(Reason.SCOPE_UNPROVEN)
            return
        if key is None:
            # Closed accountability metadata does not claim absent cognitive ancestry.
            return
        source = self.get("source_inputs", "input_id", key)
        if (
            source.get("identity_id") != identity
            or source.get("proof_status") != "complete"
            or source.get("legacy") not in (False, 0)
        ):
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        person = (
            text(self.scope(identity, interaction_id)["counterparty_id"])
            if interaction_id is not None
            else self.reflection_people.get(str(binding.get("reflection_outcome_id")))
        )
        if person is None:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        # The exact actual input is retained explicitly; no chronology/citation reconstruction.
        if self.receipt_failure:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        try:
            if self.receipts is None:
                self.work.add(2 * self.row_count)
                self.receipts = AppraisalReceiptIndex.build(self.store)
            input_key = str(source["input_id"])
            if input_key not in self.input_scopes:
                # Every reachable root is checked, including uncited historical
                # context parents. Mutable eligibility does not undo metadata.
                self.work.add(4 * self.row_count)
                _refs, all_roots = self.receipts.reachable(source)
                if not all_roots:
                    raise _ProofError(Reason.SCOPE_UNPROVEN)
                for root in all_roots:
                    message = self.get("conversation_messages", "message_id", root)
                    self.scope(identity, message["interaction_id"], person=person, message_id=root)
                self.input_scopes[input_key] = identity, person, all_roots
            saved_identity, saved_person, all_roots = self.input_scopes[input_key]
            if (saved_identity, saved_person) != (identity, person):
                raise _ProofError(Reason.SCOPE_UNPROVEN)
            if interaction_id is not None and not any(
                self.get("conversation_messages", "message_id", root)["interaction_id"]
                == interaction_id
                for root in all_roots
            ):
                raise _ProofError(Reason.SCOPE_UNPROVEN)
            self.receipts.digest(source)
        except RuntimeError:
            self.receipt_failure = True
            raise _ProofError(Reason.SCOPE_UNPROVEN) from None

    def proof(self, event: Row, binding: Row, family: Family, details: Row) -> None:
        identity = text(binding.get("identity_id"))
        if family is Family.ACTIVATION:
            owner = self.get("satori_identities", "identity_id", identity)
            self.same(details, owner, ("seed_id", "seed_schema_version", "seed_content_hash"))
            if (
                event["aggregate_type"] != "identity"
                or event["aggregate_id"] != identity
                or utc_time(event["occurred_at"]) != utc_time(owner["activation_time"])
            ):
                raise _ProofError()
            self.input(binding, identity, family, None)
            return
        table, key = _OWNER_TABLES[family.value]
        slot = AUDIT_METADATA_FIELDS_BY_FAMILY[family]
        owner = self.get(table, key, binding.get(slot) if slot else identity)
        record_field = (
            "outcome_id"
            if family.value.startswith("reflection_")
            else "approval_id"
            if family is Family.CHECKPOINT_APPROVAL
            else "restore_id"
            if family is Family.PERSONALITY_RESTORE
            else "receipt_id"
            if family is Family.SOURCE_WITHDRAWAL
            else "decision_id"
        )
        if details.get(record_field) != owner[key]:
            raise _ProofError()
        time_field = (
            "approved_at"
            if family is Family.CHECKPOINT_APPROVAL
            else "restored_at"
            if family is Family.PERSONALITY_RESTORE
            else "recorded_at"
            if family is Family.SOURCE_WITHDRAWAL
            else "decided_at"
        )
        if utc_time(event["occurred_at"]) != utc_time(owner[time_field]):
            raise _ProofError()
        if "trace_id" in owner and event["trace_id"] != owner["trace_id"]:
            raise _ProofError()
        if "identity_id" in owner and owner["identity_id"] != identity:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        interaction: object | None = None
        if family is Family.EPISODE_FORMATION:
            self.same(
                details,
                owner,
                (
                    "source_interaction_id",
                    "formation_version",
                    "policy_version",
                    "decision_id",
                    "reason_code",
                ),
            )
            interaction = owner["source_interaction_id"]
            self.scope(identity, interaction)
            created = owner["kind"] == "created"
            if (
                event["event_type"] != f"memory.episode_{owner['kind']}"
                or event["aggregate_type"] != ("memory" if created else "interaction")
                or event["aggregate_id"] != (owner["memory_id"] if created else interaction)
            ):
                raise _ProofError()
            if created:
                memory = self.get("episodic_memories", "memory_id", owner["memory_id"])
                if memory["source_interaction_id"] != interaction:
                    raise _ProofError()
                edges = self.group("memory_evidence", "memory_id", memory["memory_id"])
                ids = string_list(details["evidence_ids"])
                sources = string_list(details["source_message_ids"], unique=False)
                if len(ids) != len(edges) or len(ids) != len(sources):
                    raise _ProofError()
                by_id = {str(edge["evidence_id"]): edge for edge in edges}
                for edge_id, root in zip(ids, sources, strict=True):
                    edge = by_id.get(edge_id)
                    if (
                        edge is None
                        or edge["source_message_id"] != root
                        or edge["provenance_kind"] != "explicit_user_statement"
                    ):
                        raise _ProofError()
                    self.scope(identity, interaction, message_id=root)
        elif family is Family.MEMORY_LIFECYCLE:
            self.same(details, owner, tuple(details))
            memory = self.get("episodic_memories", "memory_id", owner["memory_id"])
            self.scope(identity, memory["source_interaction_id"], person=owner["counterparty_id"])
            if (
                event["aggregate_type"] != "memory"
                or event["aggregate_id"] != owner["memory_id"]
                or event["event_type"] != f"memory.{owner['reason_code']}"
            ):
                raise _ProofError()
        elif family in {
            Family.SEMANTIC_FORMATION,
            Family.USER_MODEL_FORMATION,
            Family.WORLD_MODEL_FORMATION,
            Family.POSITION_FORMATION,
        }:
            if family is Family.SEMANTIC_FORMATION:
                names = tuple(name for name in details if name != "claim_ids")
                self.same(details, owner, names)
                self._ids(details, "claim_ids", owner["claim_ids"])
                memory = self.get("episodic_memories", "memory_id", owner["source_memory_id"])
                interaction = memory["source_interaction_id"]
                expected_type, expected_id, prefix = (
                    "semantic_memory",
                    owner["source_memory_id"],
                    "memory.semantic_",
                )
                revision_table, claim_table = "semantic_claim_revisions", "semantic_claims"
                claimed = string_list(details["claim_ids"])
            elif family is Family.POSITION_FORMATION:
                self.same(details, owner, tuple(name for name in details if name != "position_ids"))
                self._ids(details, "position_ids", owner["position_ids"])
                interaction = owner["source_interaction_id"]
                expected_type, expected_id, prefix = "satori_positions", identity, "positions."
                revision_table, claim_table = "satori_position_revisions", "satori_positions"
                claimed = string_list(details["position_ids"])
            else:
                prefix_owner = "user" if family is Family.USER_MODEL_FORMATION else "world"
                self.same(
                    details,
                    owner,
                    (
                        "decision_id",
                        "source_interaction_id",
                        "formation_version",
                        "policy_version",
                        "reason_code",
                    ),
                )
                for name in (
                    "created_count",
                    "merged_count",
                    "superseded_count",
                    "disputed_count",
                    "rejected_count",
                ):
                    if details[name] != owner[prefix_owner + "_" + name]:
                        raise _ProofError()
                self._ids(details, "claim_ids", owner[prefix_owner + "_claim_ids"])
                interaction = owner["source_interaction_id"]
                expected_type, expected_id, prefix = (
                    prefix_owner + "_model",
                    f"{identity}:{owner['counterparty_id']}",
                    "models." + prefix_owner + "_",
                )
                revision_table, claim_table = (
                    prefix_owner + "_model_claim_revisions",
                    prefix_owner + "_model_claims",
                )
                claimed = string_list(details["claim_ids"])
            scope = self.scope(
                identity,
                interaction,
                person=owner.get("counterparty_id"),
                message_id=owner.get("source_message_id"),
            )
            if (
                event["aggregate_type"] != expected_type
                or event["aggregate_id"] != expected_id
                or event["event_type"] != prefix + str(owner["kind"])
            ):
                raise _ProofError()
            revisions = self.group(revision_table, "decision_id", owner["decision_id"])
            kind_key = "position_id" if family is Family.POSITION_FORMATION else "claim_id"
            for claim_id in claimed:
                claim = self.get(claim_table, kind_key, claim_id)
                if (
                    claim.get("identity_id", identity) != identity
                    or claim.get("counterparty_id", scope["counterparty_id"])
                    != scope["counterparty_id"]
                ):
                    raise _ProofError(Reason.SCOPE_UNPROVEN)
            if not set(claimed) <= {str(row[kind_key]) for row in revisions}:
                raise _ProofError()
        elif family.value.startswith("reflection_"):
            self._reflection(event, owner, identity, family, details)
        elif family is Family.RELATIONSHIP_TRANSITION:
            self.same(details, owner, tuple(name for name in details if name != "categories"))
            self._ids(details, "categories", owner["categories"])
            relation = self.get("relationship_states", "relationship_id", owner["relationship_id"])
            interaction = owner["interaction_id"]
            self.scope(
                identity,
                interaction,
                person=relation["counterparty_id"],
                message_id=owner["source_user_message_id"],
            )
            if (
                relation["identity_id"] != identity
                or event["aggregate_type"] != "relationship"
                or event["aggregate_id"] != owner["relationship_id"]
                or event["event_type"] != "relationship.decision_" + str(owner["kind"])
            ):
                raise _ProofError()
            if owner["kind"] == "applied":
                transition = self.get(
                    "relationship_transitions", "transition_id", owner["transition_id"]
                )
                self.same(
                    owner,
                    transition,
                    (
                        "relationship_id",
                        "interaction_id",
                        "source_user_message_id",
                        "session_id",
                        "trace_id",
                        "policy_version",
                        "appraisal_schema_version",
                    ),
                )
            elif owner["transition_id"] is not None:
                raise _ProofError()
        elif family is Family.CHECKPOINT_APPROVAL:
            self.same(details, owner, ("approval_id", "checkpoint_id", "checkpoint_hash"))
            checkpoint = self.get(
                "personality_checkpoints", "checkpoint_id", owner["checkpoint_id"]
            )
            if (
                details["aggregate_version"] != owner["expected_aggregate_version"]
                or checkpoint["identity_id"] != identity
                or checkpoint["checkpoint_hash"] != owner["checkpoint_hash"]
                or checkpoint["source_aggregate_version"] != details["aggregate_version"]
            ):
                raise _ProofError()
            if event["aggregate_type"] != "personality" or event["aggregate_id"] != identity:
                raise _ProofError()
        elif family is Family.PERSONALITY_RESTORE:
            self.same(
                details, owner, tuple(name for name in details if name != "changed_trait_keys")
            )
            diffs = document(owner["trait_diffs"])
            if not isinstance(diffs, list) or details["changed_trait_keys"] != [
                item["trait_key"] for item in diffs if isinstance(item, dict)
            ]:
                raise _ProofError()
            revision = self.get("personality_revisions", "revision_id", owner["revision_id"])
            self.same(
                owner,
                revision,
                (
                    "identity_id",
                    "before_aggregate_version",
                    "after_aggregate_version",
                    "source_checkpoint_id",
                    "resulting_checkpoint_id",
                ),
            )
            for checkpoint_id in (owner["source_checkpoint_id"], owner["resulting_checkpoint_id"]):
                checkpoint = self.get("personality_checkpoints", "checkpoint_id", checkpoint_id)
                if checkpoint["identity_id"] != identity:
                    raise _ProofError(Reason.SCOPE_UNPROVEN)
            if event["aggregate_type"] != "personality" or event["aggregate_id"] != identity:
                raise _ProofError()
        elif family is Family.SOURCE_WITHDRAWAL:
            self.same(
                details,
                owner,
                (
                    "owner",
                    "aggregate_id",
                    "request_id",
                    "policy_generation",
                    "receipt_id",
                    "before_version",
                    "after_version",
                ),
            )
            if (
                event["aggregate_type"] != owner["owner"]
                or event["aggregate_id"] != owner["aggregate_id"]
                or event["trace_id"] != owner["request_id"]
            ):
                raise _ProofError()
            owner_tables = {
                "semantic_memory": ("semantic_claims", "claim_id"),
                "user_model_claim": ("user_model_claims", "claim_id"),
                "world_model_claim": ("world_model_claims", "claim_id"),
                "position": ("satori_positions", "position_id"),
                "inclination": ("satori_inclinations", "inclination_id"),
            }
            target_table, target_key = owner_tables[str(owner["owner"])]
            target = self.get(target_table, target_key, owner["aggregate_id"])
            if target.get("identity_id", identity) != identity or integer(
                target["aggregate_version"], minimum=1
            ) < integer(owner["after_version"], minimum=1):
                raise _ProofError(Reason.SCOPE_UNPROVEN)
            kind = {
                "semantic_memory": "semantic_claim",
                "user_model_claim": "user_model_claim",
                "world_model_claim": "world_model_claim",
                "position": "position",
                "inclination": "inclination",
            }[str(owner["owner"])]
            ref = kind, text(owner["aggregate_id"]), integer(owner["after_version"], minimum=1)
            admitted_key = identity, *ref
            if admitted_key not in self.admitted_targets:
                canonical_owner = self.owner(identity)
                # Includes closed evidence/root chains and terminal historical
                # proof. The sidecar identity is not itself target attribution.
                self.work.add(4 * self.row_count)
                if not canonical_owner.admits(ref):
                    raise _ProofError(Reason.SCOPE_UNPROVEN)
                self.admitted_targets.add(admitted_key)
            for root in string_list(owner["root_message_ids"]):
                message = self.get("conversation_messages", "message_id", root)
                self.scope(
                    identity,
                    message["interaction_id"],
                    person=target.get("counterparty_id"),
                    message_id=root,
                )
        else:
            raise _ProofError()
        self.input(binding, identity, family, interaction)

    @staticmethod
    def _ids(details: Row, key: str, source: object) -> None:
        if string_list(details[key]) != string_list(source):
            raise _ProofError()

    def _reflection(
        self, event: Row, outcome: Row, identity: str, family: Family, details: Row
    ) -> None:
        self.same(
            details,
            outcome,
            tuple(
                name
                for name in ("outcome_id", "proposal_id", "target_policy_version", "reason_code")
                if name in details
            ),
        )
        proposal = self.get("reflection_proposals", "proposal_id", outcome["proposal_id"])
        run = self.get("reflection_runs", "run_id", proposal["run_id"])
        if run["identity_id"] != identity:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        sources = self.group("reflection_sources", "run_id", run["run_id"])
        if not sources:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        people = set()
        for source in sources:
            message = self.get("conversation_messages", "message_id", source["root_message_id"])
            scoped = self.scope(
                identity, message["interaction_id"], message_id=source["root_message_id"]
            )
            if source.get("root_counterparty_id") != scoped["counterparty_id"]:
                raise _ProofError(Reason.SCOPE_UNPROVEN)
            people.add(text(scoped["counterparty_id"]))
        if len(people) != 1:
            raise _ProofError(Reason.SCOPE_UNPROVEN)
        self.reflection_people[str(outcome["outcome_id"])] = next(iter(people))
        accepted = outcome["decision"] == "accepted"
        if outcome["decision"] not in {"accepted", "rejected"}:
            raise _ProofError()
        if family is Family.REFLECTION_DISABLED:
            if (
                accepted
                or (run.get("schema_version"), run.get("policy_version"))
                not in {(1, 1), (2, 2), (3, 3)}
                or outcome["target_policy_version"] != run["policy_version"]
                or outcome["target_aggregate_type"] is not None
                or outcome["target_aggregate_id"] is not None
                or event["aggregate_type"] != "reflection_proposal"
                or event["aggregate_id"] != outcome["proposal_id"]
                or details["identity_id"] != identity
                or event["event_type"] != "reflection.rejected"
            ):
                raise _ProofError()
            return
        prefix = (
            "position"
            if family is Family.REFLECTION_POSITION
            else "inclination"
            if family is Family.REFLECTION_INCLINATION
            else "personality"
        )
        expected_type = (
            "satori_positions"
            if prefix == "position"
            else "satori_inclinations"
            if prefix == "inclination"
            else "personality"
        )
        expected_id = (
            outcome["target_aggregate_id"] if accepted and prefix == "inclination" else identity
        )
        if (
            event["event_type"] != f"reflection.{prefix}_{outcome['decision']}"
            or event["aggregate_type"] != expected_type
            or event["aggregate_id"] != expected_id
        ):
            raise _ProofError()
        if family is Family.REFLECTION_POSITION:
            revisions = self.group(
                "satori_position_revisions", "reflection_outcome_id", outcome["outcome_id"]
            )
            ids = string_list(details["position_ids"])
            if set(ids) != {str(row["position_id"]) for row in revisions}:
                raise _ProofError()
        elif family is Family.REFLECTION_INCLINATION:
            revisions = self.group(
                "satori_inclination_revisions", "reflection_outcome_id", outcome["outcome_id"]
            )
            if accepted:
                if len(revisions) != 1:
                    raise _ProofError()
                revision = revisions[0]
                names = (
                    "revision_id",
                    "prior_score",
                    "new_score",
                    "applied_delta",
                    "prior_confidence",
                    "new_confidence",
                    "prior_stability",
                    "new_stability",
                )
                self.same(details, revision, names)
                inclination = self.get(
                    "satori_inclinations", "inclination_id", revision["inclination_id"]
                )
                if (
                    inclination["identity_id"] != identity
                    or details["inclination_id"] != revision["inclination_id"]
                    or details["aggregate_version"] != revision["inclination_version"]
                    or details["inclination_kind"] != inclination["kind"]
                ):
                    raise _ProofError()
                evidence = [
                    row
                    for row in self.group(
                        "satori_inclination_evidence", "inclination_id", revision["inclination_id"]
                    )
                    if utc_time(row["accepted_at"]) == utc_time(outcome["decided_at"])
                ]
                if set(string_list(details["evidence_ids"])) != {
                    str(row["evidence_id"]) for row in evidence
                } or details["new_evidence_count"] != len(evidence):
                    raise _ProofError()
            elif (
                revisions
                or any(
                    details[name] is not None
                    for name in (
                        "inclination_id",
                        "inclination_kind",
                        "aggregate_version",
                        "revision_id",
                        "prior_score",
                        "new_score",
                        "applied_delta",
                        "prior_confidence",
                        "new_confidence",
                        "prior_stability",
                        "new_stability",
                    )
                )
                or details["evidence_ids"] != []
                or details["new_evidence_count"] != 0
            ):
                raise _ProofError()
        else:
            revisions = self.group(
                "personality_revisions", "reflection_outcome_id", outcome["outcome_id"]
            )
            if accepted:
                if len(revisions) != 1:
                    raise _ProofError()
                revision = revisions[0]
                if (
                    details["revision_id"] != revision["revision_id"]
                    or details["aggregate_version"] != revision["after_aggregate_version"]
                    or revision["identity_id"] != identity
                ):
                    raise _ProofError()
                personality_evidence = self.group(
                    "personality_evidence", "revision_id", revision["revision_id"]
                )
                if set(string_list(details["evidence_ids"])) != {
                    str(row["evidence_id"]) for row in personality_evidence
                } or details["evidence_count"] != len(personality_evidence):
                    raise _ProofError()
            elif (
                revisions
                or details["revision_id"] is not None
                or details["aggregate_version"] is not None
                or details["evidence_ids"] != []
                or details["evidence_count"] != 0
            ):
                raise _ProofError()


@dataclass(frozen=True, slots=True)
class AuditMetadataIndex:
    classifications: tuple[AuditMetadataClassification, ...]
    coverage: AuditMetadataCoverage
    total_binding_rows: int
    unknown_binding_rows: int
    binding_unknown_reasons: tuple[Reason, ...]

    @classmethod
    def build(
        cls, store: Store, *, historical: HistoricalProvenance | None = None
    ) -> AuditMetadataIndex:
        events = store.get("audit_events", ())
        if len(events) > 100_000:
            raise ValueError("audit_metadata_capacity_exceeded")
        bindings = {str(row["event_id"]): row for row in store.get("audit_metadata_bindings", ())}
        work = _WorkBudget()
        work.add(sum(len(rows) for rows in store.values()))
        proof = _OwnerIndex(store, historical, work)
        duplicate_counts: Counter[tuple[str, str]] = Counter()
        for event in events:
            candidates: list[Family] = []
            event_type = str(event.get("event_type"))
            for prefix, candidate_family in (
                ("memory.episode_", Family.EPISODE_FORMATION),
                ("memory.semantic_", Family.SEMANTIC_FORMATION),
                ("models.user_", Family.USER_MODEL_FORMATION),
                ("models.world_", Family.WORLD_MODEL_FORMATION),
                ("positions.", Family.POSITION_FORMATION),
                ("reflection.position_", Family.REFLECTION_POSITION),
                ("reflection.inclination_", Family.REFLECTION_INCLINATION),
                ("reflection.personality_", Family.REFLECTION_PERSONALITY),
                ("relationship.decision_", Family.RELATIONSHIP_TRANSITION),
            ):
                if event_type.startswith(prefix):
                    candidates.append(candidate_family)
            for exact, candidate_family in (
                ("satori.activation", Family.ACTIVATION),
                ("source_support_withdrawn", Family.SOURCE_WITHDRAWAL),
                ("personality.checkpoint_approved", Family.CHECKPOINT_APPROVAL),
                ("personality.checkpoint_restored", Family.PERSONALITY_RESTORE),
                ("reflection.rejected", Family.REFLECTION_DISABLED),
            ):
                if event_type == exact:
                    candidates.append(candidate_family)
            if event_type in {
                "memory.episode_archived",
                "memory.episode_reactivated",
                "memory.episode_lifecycle_noop",
            }:
                candidates = [Family.MEMORY_LIFECYCLE]
            try:
                parsed = document(event.get("details"))
                if isinstance(parsed, dict):
                    for candidate_family in candidates:
                        field = owner_record_field(candidate_family)
                        value = event.get("aggregate_id") if field is None else parsed.get(field)
                        if type(value) is str:
                            duplicate_counts[candidate_family.value, value] += 1
            except ValueError:
                pass
        try:
            work.add(2 * proof.row_count)
            appraisal = AppraisalMaterialIndex.build(store)
        except _ProofError:
            raise
        except (ValueError, TypeError, KeyError, RuntimeError):
            appraisal = None
        try:
            actions = decode_body_projection_rows(store.get("erasure_body_applications", ()))
            app_targets = latest_appraisal(actions)
            approved_appraisal = {
                (
                    action.action_id,
                    target.transition_id,
                    target.terminal_digest,
                    target.input_closure_digest,
                ): (action, target)
                for action in actions
                if isinstance(action, AppraisalBodyAction)
                for target in action.proposal.targets
            }
        except (ValueError, TypeError, KeyError, RecursionError):
            app_targets = {}
            approved_appraisal = {}
        classifications = []
        exhausted = False
        for event in events:
            key = str(event["event_id"])
            family: Family | None = None
            reason: Reason | None = None
            status = Status.UNKNOWN
            try:
                if exhausted:
                    raise _ProofError(Reason.CAPACITY_EXCEEDED)
                work.add(48)
                if event.get("event_type") == "emotion.transition_applied":
                    family = Family.APPRAISAL_TRANSITION
                    if appraisal is None:
                        raise _ProofError(Reason.APPRAISAL_UNPROVEN)
                    details = document(event.get("details"))
                    if not isinstance(details, dict):
                        raise _ProofError(Reason.APPRAISAL_UNPROVEN)
                    transition_id = text(details.get("transition_id"))
                    row = appraisal.transitions.get(transition_id)
                    if row is None or row.get("audit_event_id") != key:
                        raise _ProofError(Reason.APPRAISAL_UNPROVEN)
                    work.add(3)
                    source_key = str(row.get("source_input_id"))
                    if source_key not in appraisal.receipt_index.cached:
                        work.add(2 * proof.row_count)
                    appraisal.material(transition_id, terminal=False)
                    appraisal.input_closure_digest(transition_id)
                    if row["explanation_state"] == "available":
                        status, reason = Status.OWNED_CONTENT, Reason.CONTENT_PRESENT
                    elif historical is not None and transition_id in app_targets:
                        target = app_targets[transition_id][1]
                        effective = approved_appraisal.get(
                            (
                                str(row.get("explanation_action_id")),
                                transition_id,
                                target.terminal_digest,
                                target.input_closure_digest,
                            )
                        )
                        if effective is None or not appraisal_target_terminal_proven(
                            appraisal, *effective
                        ):
                            raise _ProofError(Reason.APPRAISAL_UNPROVEN)
                        status = Status.CERTIFIED_METADATA
                    else:
                        raise _ProofError(Reason.APPRAISAL_UNPROVEN)
                else:
                    binding = bindings.get(key)
                    if binding is None:
                        raise _ProofError(Reason.MISSING_BINDING)
                    if (
                        type(binding.get("contract_version")) is not int
                        or binding["contract_version"] != 1
                    ):
                        raise _ProofError(Reason.UNSUPPORTED_CONTRACT)
                    family = Family(text(binding["family"]))
                    if family not in AUDIT_METADATA_FIELDS_BY_FAMILY:
                        raise _ProofError(Reason.UNSUPPORTED_FAMILY)
                    if type(event.get("schema_version")) is not int or event["schema_version"] != 1:
                        raise _ProofError(Reason.UNSUPPORTED_EVENT_VERSION)
                    details = validate_event_shape(event, family)
                    work.add(
                        sum(len(value) for value in details.values() if isinstance(value, list))
                    )
                    slot = AUDIT_METADATA_FIELDS_BY_FAMILY[family]
                    if any(
                        binding.get(other) is not None
                        for other in set(AUDIT_METADATA_FIELDS_BY_FAMILY.values()) - {None, slot}
                        if other is not None
                    ):
                        raise _ProofError(Reason.OWNER_PROOF_UNPROVEN)
                    if event_digest(event) != binding.get("event_digest"):
                        raise _ProofError(Reason.TAMPERED_BINDING)
                    record = text(binding.get(slot)) if slot else text(binding.get("identity_id"))
                    if duplicate_counts[family.value, record] != 1:
                        raise _ProofError(Reason.DUPLICATE_EVENT)
                    proof.proof(event, binding, family, details)
                    status = Status.CERTIFIED_METADATA
            except _ProofError as error:
                reason = error.reason
                exhausted = reason is Reason.CAPACITY_EXCEEDED
            except (ValueError, TypeError, KeyError, RecursionError):
                reason = Reason.INVALID_SHAPE
            except RuntimeError:
                reason = Reason.APPRAISAL_UNPROVEN
            classifications.append(AuditMetadataClassification(key, status, family, reason))
        counts = Counter(item.status for item in classifications)
        classification_by_id = {item.event_id: item for item in classifications}
        binding_reasons: list[Reason] = []
        for binding in store.get("audit_metadata_bindings", ()):
            item = classification_by_id.get(str(binding["event_id"]))
            if (
                item is None
                or item.status is not Status.CERTIFIED_METADATA
                or item.family is Family.APPRAISAL_TRANSITION
            ):
                binding_reasons.append(
                    item.reason if item and item.reason else Reason.OWNER_PROOF_UNPROVEN
                )
        return cls(
            tuple(classifications),
            AuditMetadataCoverage(
                len(events),
                counts[Status.CERTIFIED_METADATA],
                counts[Status.OWNED_CONTENT],
                counts[Status.UNKNOWN],
            ),
            len(store.get("audit_metadata_bindings", ())),
            len(binding_reasons),
            tuple(sorted(set(binding_reasons))),
        )


def classify_audit_metadata(
    store: Store, *, historical: HistoricalProvenance | None = None
) -> AuditMetadataIndex:
    return AuditMetadataIndex.build(store, historical=historical)
