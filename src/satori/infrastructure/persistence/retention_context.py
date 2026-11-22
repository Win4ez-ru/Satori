"""One fresh, store-bound mixed-head proof and classifier per protected census."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from satori.domain.audit_metadata import AuditMetadataCoverageStatus
from satori.domain.erasure_appraisal import AppraisalBodyAction
from satori.domain.erasure_bodies import BodyAction, BodyErasureAction, source_root
from satori.domain.erasure_dialogue import DialogueBodyAction, HistoricalEvidenceTable
from satori.domain.erasure_knowledge import KnowledgeBodyAction
from satori.domain.erasure_personality_reasons import CheckpointReasonAction
from satori.domain.erasure_reflection import ReflectionBodyAction
from satori.domain.retention import RetentionClass as Category
from satori.domain.retention import RetentionFacet as Facet
from satori.domain.retention import RetentionReason as Reason
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.appraisal_material import (
    AppraisalMaterialIndex,
    appraisal_target_terminal_proven,
)
from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex
from satori.infrastructure.persistence.canonical_provenance import (
    HistoricalProvenance,
    _approved_evidence,
    _validate_action_head,
    canonical_row_digest,
    dialogue_message_digest,
    historical_from_approved_actions,
)
from satori.infrastructure.persistence.checkpoint_reason_material import (
    CheckpointReasonMaterialIndex,
    checkpoint_reason_target_terminal_proven,
)
from satori.infrastructure.persistence.erasure_body_codec import decode_body_projection_rows
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners, Row, Store
from satori.infrastructure.persistence.knowledge_material import (
    KNOWLEDGE_OWNER_SPECS,
    KnowledgeMaterialIndex,
    knowledge_target_terminal_proven,
)
from satori.infrastructure.persistence.reflection_material import (
    ReflectionMaterialIndex,
    reflection_target_terminal_proven,
)
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget, RowRetention

if TYPE_CHECKING:
    from satori.infrastructure.persistence.retention_metadata import MetadataRetentionIndex

_KEYS = {
    "conversation_messages": "message_id",
    "episodic_memories": "memory_id",
    "memory_evidence": "evidence_id",
    "episodic_memory_embeddings": "memory_id",
    "reflection_proposals": "proposal_id",
    "affective_transitions": "transition_id",
    "personality_checkpoint_approvals": "approval_id",
    "personality_restore_events": "restore_id",
}
_BODY_COLUMNS = {
    "conversation_messages": ("content",),
    "episodic_memories": ("summary",),
    "memory_evidence": ("quote",),
    "reflection_proposals": ("payload",),
    "affective_transitions": ("reason_codes",),
    "personality_checkpoint_approvals": ("reason",),
    "personality_restore_events": ("reason",),
    **{spec.table: spec.content_columns for spec in KNOWLEDGE_OWNER_SPECS.values()},
    "satori_position_evidence": ("quote", "normalized_signature"),
}
_KNOWLEDGE_TABLES = {spec.table: (kind, spec.key) for kind, spec in KNOWLEDGE_OWNER_SPECS.items()}
_STRUCTURAL = frozenset((Facet.STRUCTURAL_METADATA,))


def _unknown(reason: Reason) -> RowRetention:
    return RowRetention(Category.UNKNOWN, reasons=(reason,))


def _version(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return None
    return value


def _marker(table: str) -> tuple[str, str]:
    if table == "affective_transitions":
        return "explanation_state", "explanation_action_id"
    if table in {"personality_checkpoint_approvals", "personality_restore_events"}:
        return "reason_state", "reason_action_id"
    return "body_state", "body_action_id"


@dataclass(slots=True)
class RetentionContext:
    """Private authority is tied to actual rows; copying flags or ACKs is insufficient."""

    _store: Store
    _snapshot: SourceEligibilitySnapshot
    _primary_keys: dict[str, tuple[str, ...]]
    _schema_digest: str
    _store_digest: str
    budget: RetentionWorkBudget
    actions: tuple[BodyAction, ...]
    historical: HistoricalProvenance
    audit_index: AuditMetadataIndex
    metadata_index: MetadataRetentionIndex
    knowledge_index: KnowledgeMaterialIndex
    reflection_index: ReflectionMaterialIndex
    appraisal_index: AppraisalMaterialIndex | None
    checkpoint_reason_index: CheckpointReasonMaterialIndex
    owners: ProvenOwners
    _whole_head_proven: bool
    _terminal: dict[str, set[str]] = field(default_factory=dict)
    _rows: dict[tuple[str, tuple[object, ...]], Row] = field(default_factory=dict)
    _digests: dict[tuple[str, tuple[object, ...]], str] = field(default_factory=dict)
    _decisions: dict[tuple[str, tuple[object, ...]], RowRetention] = field(default_factory=dict)
    _audit_status: dict[str, AuditMetadataCoverageStatus] = field(default_factory=dict)
    _audit_identities: dict[str, object] = field(default_factory=dict)
    _audit_families: dict[str, str | None] = field(default_factory=dict)
    _terminal_audits: set[str] = field(default_factory=set)
    _message_pairs: dict[str, tuple[Row, ...]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        store: Store,
        *,
        snapshot: SourceEligibilitySnapshot,
        primary_keys: dict[str, tuple[str, ...]],
        schema_digest: str,
        store_digest: str,
        budget: RetentionWorkBudget | None = None,
    ) -> RetentionContext:
        return cls._assemble(
            store,
            snapshot=snapshot,
            primary_keys=primary_keys,
            schema_digest=schema_digest,
            store_digest=store_digest,
            budget=budget,
            owner_assessment=False,
            approved_actions=None,
        )

    @classmethod
    def for_owner_assessment(
        cls,
        store: Store,
        *,
        snapshot: SourceEligibilitySnapshot,
        primary_keys: dict[str, tuple[str, ...]],
        schema_digest: str,
        store_digest: str,
        budget: RetentionWorkBudget | None = None,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> RetentionContext:
        """Pending operator assessment grants no complete-head or terminal badges."""
        if approved_actions is not None:
            _validate_action_head(approved_actions, snapshot)
        return cls._assemble(
            store,
            snapshot=snapshot,
            primary_keys=primary_keys,
            schema_digest=schema_digest,
            store_digest=store_digest,
            budget=budget,
            owner_assessment=True,
            approved_actions=approved_actions,
        )

    @classmethod
    def _assemble(
        cls,
        store: Store,
        *,
        snapshot: SourceEligibilitySnapshot,
        primary_keys: dict[str, tuple[str, ...]],
        schema_digest: str,
        store_digest: str,
        budget: RetentionWorkBudget | None,
        owner_assessment: bool,
        approved_actions: tuple[BodyAction, ...] | None,
    ) -> RetentionContext:
        from satori.infrastructure.persistence.retention_metadata import MetadataRetentionIndex

        work = budget if budget is not None else RetentionWorkBudget()
        work.charge(sum(len(rows) for rows in store.values()))
        try:
            own = tuple(
                row
                for row in store.get("erasure_body_applications", ())
                if row.get("identity_id") == snapshot.identity_id
            )
            current = False
            actions: tuple[BodyAction, ...] = ()
            try:
                actions = decode_body_projection_rows(own)
                _validate_action_head(actions, snapshot)
                bindings = tuple(
                    row
                    for row in store.get("erasure_body_bindings", ())
                    if row.get("identity_id") == snapshot.identity_id
                )
                if snapshot.body_revision:
                    if len(bindings) != 1 or (
                        bindings[0].get("floor_id"),
                        bindings[0].get("lineage_id"),
                        bindings[0].get("body_revision"),
                    ) != (snapshot.floor_id, snapshot.lineage_id, snapshot.body_revision):
                        raise ValueError("body_binding_mismatch")
                elif bindings:
                    raise ValueError("unexpected_body_binding")
                ordered = sorted(own, key=lambda row: int(str(row["body_revision"])))
                for row, action in zip(ordered, actions, strict=True):
                    root = source_root(action)
                    if (
                        row.get("action_id"),
                        row.get("request_id"),
                        row.get("root_message_id"),
                        row.get("floor_id"),
                        row.get("lineage_id"),
                        row.get("body_revision"),
                    ) != (
                        action.action_id,
                        action.proposal.request_id,
                        root.message_id if root is not None else None,
                        snapshot.floor_id,
                        snapshot.lineage_id,
                        action.result_body_revision,
                    ):
                        raise ValueError("body_receipt_mismatch")
                current = True
            except (SourcePolicyError, ValueError, TypeError, KeyError, RecursionError):
                if not owner_assessment:
                    raise SourcePolicyError("retention_body_head_unproven") from None
            knowledge = KnowledgeMaterialIndex.build(store)
            reflection = ReflectionMaterialIndex.build(store)
            appraisal: AppraisalMaterialIndex | None
            try:
                appraisal = AppraisalMaterialIndex.build(store)
            except (SourcePolicyError, ValueError, TypeError, KeyError, RecursionError):
                appraisal = None
            reasons = CheckpointReasonMaterialIndex.build(store)
            attribution_actions = actions if current else approved_actions
            historical = (
                historical_from_approved_actions(store, snapshot, attribution_actions)
                if attribution_actions is not None
                else HistoricalProvenance(frozenset(), {})
            )
            owners = ProvenOwners(store, snapshot.identity_id, historical=historical)
            terminal: dict[str, set[str]] = {}
            if current:
                try:
                    terminal = cls._prove_bodies(
                        store, snapshot, actions, knowledge, reflection, appraisal, reasons, work
                    )
                except SourcePolicyError as error:
                    if (
                        not owner_assessment
                        or error.reason_code == "retention_assessment_capacity_exceeded"
                    ):
                        raise
                    current = False
            if not current:
                terminal = {}
            audit = AuditMetadataIndex.build(store, historical=historical if current else None)
            metadata = MetadataRetentionIndex.build(
                store, snapshot=snapshot, historical=historical, budget=work
            )
            result = cls(
                store,
                snapshot,
                dict(primary_keys),
                schema_digest,
                store_digest,
                work,
                actions,
                historical,
                audit,
                metadata,
                knowledge,
                reflection,
                appraisal,
                reasons,
                owners,
                current,
                terminal,
            )
            result._audit_status = {item.event_id: item.status for item in audit.classifications}
            result._audit_families = {
                item.event_id: item.family.value if item.family is not None else None
                for item in audit.classifications
            }
            result._audit_identities = {
                str(row["event_id"]): row.get("identity_id")
                for row in store.get("audit_metadata_bindings", ())
            }
            for row in store.get("affective_transitions", ()):
                event_id = row.get("audit_event_id")
                if event_id is not None:
                    result._audit_identities[str(event_id)] = row.get("identity_id")
                    if str(row.get("transition_id")) in terminal.get(
                        "affective_transitions", set()
                    ):
                        result._terminal_audits.add(str(event_id))
            grouped_messages: dict[str, list[Row]] = {}
            for row in store.get("conversation_messages", ()):
                grouped_messages.setdefault(str(row.get("interaction_id")), []).append(row)
            result._message_pairs = {key: tuple(rows) for key, rows in grouped_messages.items()}
            for table, rows in store.items():
                for row in rows:
                    key = result._key(table, row)
                    if key in result._rows:
                        raise ValueError("duplicate_logical_row")
                    result._rows[key] = row
                    result._digests[key] = canonical_row_digest(row)
                    result._decisions[key] = result._classify(table, row)
            return result
        except SourcePolicyError:
            raise
        except (ValueError, TypeError, KeyError, RecursionError, StopIteration):
            raise SourcePolicyError("retention_body_head_unproven") from None

    @staticmethod
    def _prove_bodies(
        store: Store,
        snapshot: SourceEligibilitySnapshot,
        actions: tuple[BodyAction, ...],
        knowledge: KnowledgeMaterialIndex,
        reflection: ReflectionMaterialIndex,
        appraisal: AppraisalMaterialIndex | None,
        reasons: CheckpointReasonMaterialIndex,
        budget: RetentionWorkBudget,
    ) -> dict[str, set[str]]:
        from satori.infrastructure.persistence.erasure_bodies import (
            MaterialIndex,
            _hash,
            _material,
        )

        episodes = MaterialIndex.build(store)
        result: dict[str, set[str]] = {}
        latest: dict[tuple[str, str], tuple[str, object]] = {}
        proven: dict[tuple[str, str], tuple[str, object]] = {}
        rows = {
            table: {
                str(row[_KEYS.get(table, _KNOWLEDGE_TABLES.get(table, (None, ""))[1])]): row
                for row in values
            }
            for table, values in store.items()
            if table in _KEYS or table in _KNOWLEDGE_TABLES
        }
        sessions = {str(row["session_id"]): row for row in store.get("conversation_sessions", ())}
        interactions = {
            str(row["interaction_id"]): row for row in store.get("conversation_interactions", ())
        }
        pairs: dict[str, list[Row]] = {}
        for message in store.get("conversation_messages", ()):
            pairs.setdefault(str(message["interaction_id"]), []).append(message)
        witnesses = _approved_evidence(actions)
        evidence_rows = {
            (table, str(row["evidence_id"])): row
            for table in HistoricalEvidenceTable
            for row in store.get(table.value, ())
        }

        def observe(table: str, key: str, digest: str, qualifier: object = None) -> Row | None:
            budget.charge()
            latest[table, key] = digest, qualifier
            return rows.get(table, {}).get(key)

        def admit(table: str, key: str, digest: str, qualifier: object = None) -> None:
            proven[table, key] = digest, qualifier

        for action in actions:
            if isinstance(action, BodyErasureAction):
                for episode in action.proposal.targets:
                    key = episode.memory_id
                    row = observe("episodic_memories", key, episode.terminal_digest)
                    if row is None or row.get("body_action_id") != action.action_id:
                        continue
                    budget.charge(1 + len(episodes.evidence.get(key, ())))
                    if (
                        row.get("body_state") == "erased"
                        and row.get("summary") is None
                        and _hash(_material(episodes, key, terminal=False))
                        == episode.terminal_digest
                        and all(
                            edge.get("body_state") == "erased"
                            and edge.get("quote") is None
                            and edge.get("body_action_id") == action.action_id
                            for edge in episodes.evidence.get(key, ())
                        )
                    ):
                        admit("episodic_memories", key, episode.terminal_digest)
                        result.setdefault("memory_evidence", set()).update(
                            str(edge["evidence_id"]) for edge in episodes.evidence.get(key, ())
                        )
            elif isinstance(action, DialogueBodyAction):
                for dialogue in action.proposal.targets:
                    key = dialogue.message_id
                    row = observe(
                        "conversation_messages", key, dialogue.terminal_digest, dialogue.witnesses
                    )
                    if row is None or row.get("body_action_id") != action.action_id:
                        continue
                    source = interactions.get(str(row.get("interaction_id")))
                    session = sessions.get(str(row.get("session_id")))
                    pair = pairs.get(str(row.get("interaction_id")), ())
                    root = action.proposal.root
                    budget.charge(1 + len(dialogue.witnesses))
                    if (
                        source is not None
                        and session is not None
                        and source.get("session_id") == session.get("session_id")
                        and source.get("status") == "completed"
                        and session.get("identity_id") == root.identity_id
                        and session.get("counterparty_id") == root.counterparty_id
                        and session.get("scope_verified") == 1
                        and len(pair) == 2
                        and {(item.get("role"), item.get("sequence")) for item in pair}
                        == {("user", 1), ("assistant", 2)}
                        and all(
                            item.get("session_id") == session.get("session_id") for item in pair
                        )
                        and (row.get("role") == "assistant" or key == root.message_id)
                        and row.get("body_state") == "erased"
                        and row.get("content") is None
                        and dialogue_message_digest(row) == dialogue.terminal_digest
                        and all(
                            (proof := witnesses.get((witness.table, witness.evidence_id)))
                            is not None
                            and (edge := evidence_rows.get((witness.table, witness.evidence_id)))
                            is not None
                            and proof.root == root
                            and canonical_row_digest(edge) == proof.after_digest
                            for witness in dialogue.witnesses
                        )
                    ):
                        admit(
                            "conversation_messages",
                            key,
                            dialogue.terminal_digest,
                            dialogue.witnesses,
                        )
            elif isinstance(action, KnowledgeBodyAction):
                for claim in action.proposal.targets:
                    table = KNOWLEDGE_OWNER_SPECS[claim.kind].table
                    row = observe(table, claim.aggregate_id, claim.terminal_digest)
                    if row is None or row.get("body_action_id") != action.action_id:
                        continue
                    budget.charge(
                        len(knowledge.evidence.get((claim.kind, claim.aggregate_id), ()))
                        + len(knowledge.revisions.get((claim.kind, claim.aggregate_id), ()))
                    )
                    if knowledge_target_terminal_proven(store, action, claim, index=knowledge):
                        admit(table, claim.aggregate_id, claim.terminal_digest)
                        if claim.kind.value == "position":
                            result.setdefault("satori_position_evidence", set()).update(
                                str(edge["evidence_id"])
                                for edge in knowledge.evidence.get(
                                    (claim.kind, claim.aggregate_id), ()
                                )
                            )
            elif isinstance(action, ReflectionBodyAction):
                for proposal in action.proposal.targets:
                    row = observe(
                        "reflection_proposals", proposal.proposal_id, proposal.terminal_digest
                    )
                    if row is None or row.get("body_action_id") != action.action_id:
                        continue
                    budget.charge(proposal.metadata_reference_count)
                    if reflection_target_terminal_proven(reflection, action, proposal):
                        admit(
                            "reflection_proposals", proposal.proposal_id, proposal.terminal_digest
                        )
            elif isinstance(action, AppraisalBodyAction):
                for transition in action.proposal.targets:
                    row = observe(
                        "affective_transitions",
                        transition.transition_id,
                        transition.terminal_digest,
                        transition.input_closure_digest,
                    )
                    if row is None or row.get("explanation_action_id") != action.action_id:
                        continue
                    budget.charge(transition.metadata_reference_count)
                    if appraisal is not None and appraisal_target_terminal_proven(
                        appraisal, action, transition
                    ):
                        admit(
                            "affective_transitions",
                            transition.transition_id,
                            transition.terminal_digest,
                            transition.input_closure_digest,
                        )
            elif isinstance(action, CheckpointReasonAction):
                for reason in action.proposal.targets:
                    table = (
                        "personality_checkpoint_approvals"
                        if reason.selector.kind.value == "checkpoint_approval"
                        else "personality_restore_events"
                    )
                    row = observe(table, reason.selector.record_id, reason.terminal_digest)
                    if row is None or row.get("reason_action_id") != action.action_id:
                        continue
                    budget.charge(reason.metadata_reference_count)
                    if checkpoint_reason_target_terminal_proven(reasons, action, reason):
                        admit(table, reason.selector.record_id, reason.terminal_digest)
        if any(proven.get(key) != expected for key, expected in latest.items()):
            raise SourcePolicyError("retention_body_head_unproven")
        for table, key in latest:
            result.setdefault(table, set()).add(key)
        for table, columns in _BODY_COLUMNS.items():
            state, marker = _marker(table)
            key_column = _KEYS.get(table, _KNOWLEDGE_TABLES.get(table, (None, "evidence_id"))[1])
            for row in store.get(table, ()):
                # Optional empty inclination alternatives are not terminal bodies.
                required = columns[:2] if table == "satori_inclinations" else columns
                terminal = (
                    row.get(state, "available") != "available"
                    or row.get(marker) is not None
                    or any(row.get(column) is None for column in required)
                )
                if table == "satori_position_evidence":
                    terminal = row.get("quote") is None or row.get("normalized_signature") is None
                if terminal and str(row.get(key_column)) not in result.get(table, set()):
                    raise SourcePolicyError("retention_body_head_unproven")
        return result

    def _key(self, table: str, row: Row) -> tuple[str, tuple[object, ...]]:
        return table, tuple(row[name] for name in self._primary_keys[table])

    def assert_bound(
        self,
        store: Store,
        snapshot: SourceEligibilitySnapshot,
        schema_digest: str,
        store_digest: str,
    ) -> None:
        if (
            store is not self._store
            or snapshot != self._snapshot
            or schema_digest != self._schema_digest
            or store_digest != self._store_digest
        ):
            raise SourcePolicyError("retention_context_binding_mismatch")

    def classify(self, table: str, row: Row) -> RowRetention:
        try:
            key = self._key(table, row)
            if self._rows.get(key) is not row or canonical_row_digest(row) != self._digests.get(
                key
            ):
                raise SourcePolicyError("retention_context_binding_mismatch")
            return self._decisions[key]
        except (KeyError, ValueError, TypeError, RecursionError):
            raise SourcePolicyError("retention_context_binding_mismatch") from None

    def _classify(self, table: str, row: Row) -> RowRetention:
        self.budget.charge()
        if table == "audit_events":
            key = str(row.get("event_id"))
            status = self._audit_status.get(key)
            if (
                status is not AuditMetadataCoverageStatus.UNKNOWN
                and self._audit_identities.get(key) != self._snapshot.identity_id
            ):
                return _unknown(Reason.OWNER_SCOPE_UNPROVEN)
            if status is AuditMetadataCoverageStatus.CERTIFIED_METADATA:
                facets = _STRUCTURAL | (
                    {Facet.TERMINAL_BODY} if key in self._terminal_audits else set()
                )
                return RowRetention(Category.SUPPORTED_RETAINED, frozenset(facets))
            if status is AuditMetadataCoverageStatus.OWNED_CONTENT:
                return RowRetention(Category.OWNED_CONTENT, _STRUCTURAL)
            return _unknown(Reason.AUDIT_CONTRACT_UNPROVEN)
        header = self.metadata_index.validate_row(table, row)
        if header.category is Category.UNKNOWN:
            return header
        if table == "audit_metadata_bindings":
            key = str(row.get("event_id"))
            if (
                self._audit_status.get(key) is not AuditMetadataCoverageStatus.CERTIFIED_METADATA
                or self._audit_families.get(key) != row.get("family")
                or row.get("identity_id") != self._snapshot.identity_id
            ):
                return _unknown(Reason.AUDIT_CONTRACT_UNPROVEN)
            return self.metadata_index.classify(table, row)
        facets = header.facets | _STRUCTURAL
        if table in _KNOWLEDGE_TABLES or table in {
            "affective_transitions",
            "personality_checkpoint_approvals",
            "personality_restore_events",
        }:
            facets |= {Facet.NUMERIC_HISTORY}
        if table in {"satori_identities", "satori_values"}:
            return self.metadata_index.classify(table, row)
        if (
            table in {"erasure_body_bindings", "erasure_body_applications"}
            and not self._whole_head_proven
        ):
            return _unknown(Reason.TERMINAL_MATERIAL_UNPROVEN)
        if table in _BODY_COLUMNS:
            state, marker = _marker(table)
            if not self._whole_head_proven and (
                row.get(state, "available") != "available"
                or row.get(marker) is not None
                or any(
                    row.get(column) is None
                    for column in _BODY_COLUMNS[table]
                    if column not in {"alternative_topic", "normalized_alternative_topic"}
                )
            ):
                return _unknown(Reason.TERMINAL_MATERIAL_UNPROVEN)
            key_column = _KEYS.get(table, _KNOWLEDGE_TABLES.get(table, (None, "evidence_id"))[1])
            if str(row.get(key_column)) in self._terminal.get(table, set()):
                return RowRetention(Category.SUPPORTED_RETAINED, facets | {Facet.TERMINAL_BODY})
            if table == "affective_transitions":
                if row.get("identity_id") != self._snapshot.identity_id:
                    return _unknown(Reason.OWNER_SCOPE_UNPROVEN)
                try:
                    if self.appraisal_index is None:
                        return _unknown(Reason.INPUT_PROVENANCE_UNPROVEN)
                    self.appraisal_index.material(str(row["transition_id"]), terminal=False)
                    self.appraisal_index.input_closure_digest(str(row["transition_id"]))
                except (SourcePolicyError, ValueError, TypeError, KeyError):
                    return _unknown(Reason.INPUT_PROVENANCE_UNPROVEN)
                return RowRetention(Category.OWNED_CONTENT, facets | {Facet.NUMERIC_HISTORY})
            if table in {"personality_checkpoint_approvals", "personality_restore_events"}:
                from satori.domain.erasure_personality_reasons import (
                    CheckpointReasonKind,
                    CheckpointReasonSelector,
                )

                reason_kind = (
                    CheckpointReasonKind.CHECKPOINT_APPROVAL
                    if table == "personality_checkpoint_approvals"
                    else CheckpointReasonKind.PERSONALITY_RESTORE
                )
                if row.get("identity_id") != self._snapshot.identity_id:
                    return _unknown(Reason.OWNER_SCOPE_UNPROVEN)
                try:
                    self.checkpoint_reason_index.material(
                        CheckpointReasonSelector(reason_kind, str(row[key_column]))
                    )
                except (SourcePolicyError, ValueError, TypeError, KeyError):
                    return _unknown(Reason.OWNER_REFERENCE_UNPROVEN)
                return RowRetention(Category.OWNED_CONTENT, facets | {Facet.NUMERIC_HISTORY})
            kind, aggregate = (
                ("episode", str(row["memory_id"]))
                if table == "memory_evidence"
                else ("position", str(row["position_id"]))
                if table == "satori_position_evidence"
                else ("assistant_reply", str(row["interaction_id"]))
                if table == "conversation_messages" and row.get("role") == "assistant"
                else ("episode", str(row["memory_id"]))
                if table == "episodic_memories"
                else ("reflection_proposal", str(row["proposal_id"]))
                if table == "reflection_proposals"
                else (table, "")
            )
            if table == "conversation_messages" and row.get("role") == "user":
                proved = self.owners.canonical(str(row["message_id"]), completed=False)
            elif table == "conversation_messages":
                interaction = self.owners.interactions.get(str(row.get("interaction_id")))
                session = self.owners.sessions.get(str(row.get("session_id")))
                pair = self._message_pairs.get(str(row.get("interaction_id")), ())
                self.budget.charge(1 + len(pair))
                proved = bool(
                    interaction is not None
                    and session is not None
                    and interaction.get("session_id") == session.get("session_id")
                    and session.get("identity_id") == self._snapshot.identity_id
                    and session.get("scope_verified") == 1
                    and len(pair) == 2
                    and {(item.get("role"), item.get("sequence")) for item in pair}
                    == {("user", 1), ("assistant", 2)}
                    and all(item.get("session_id") == session.get("session_id") for item in pair)
                    and self.owners.admits(("assistant_reply", str(row["interaction_id"]), 1))
                )
            elif table in _KNOWLEDGE_TABLES:
                body_kind, key_column = _KNOWLEDGE_TABLES[table]
                aggregate_version = _version(row.get("aggregate_version"))
                if aggregate_version is None:
                    return _unknown(Reason.ROW_VERSION_UNSUPPORTED)
                proved = self.owners.admits(
                    (body_kind.value, str(row[key_column]), aggregate_version)
                )
            elif table == "reflection_proposals":
                proved = self.owners.reflection_proven(str(row["proposal_id"]))
            else:
                version = _version(row.get("aggregate_version", 1))
                if version is None:
                    return _unknown(Reason.ROW_VERSION_UNSUPPORTED)
                proved = self.owners.admits((kind, aggregate, version))
            return (
                RowRetention(Category.OWNED_CONTENT, facets)
                if proved
                else _unknown(Reason.OWNER_SCOPE_UNPROVEN)
            )
        if table == "episodic_memory_embeddings":
            # The supported header already proves the dedicated bounded vector role
            # through MetadataRetentionIndex; do not apply generic JSON-list limits again.
            memory = self.owners.targets["episode"].get(str(row.get("memory_id")))
            memory_version = (
                _version(memory.get("aggregate_version")) if memory is not None else None
            )
            if memory_version is None or not self.owners.admits(
                ("episode", str(row["memory_id"]), memory_version)
            ):
                return _unknown(Reason.OWNER_REFERENCE_UNPROVEN)
            return RowRetention(Category.OWNED_CONTENT, facets)
        return self.metadata_index.classify(table, row)
