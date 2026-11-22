"""Indexed structural owner proofs; retained provenance is never live admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from satori.core.personality import CANONICAL_TRAIT_KEYS
from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.erasure_preparation import ErasurePreparationOwner
from satori.domain.retention import RetentionClass, RetentionReason
from satori.infrastructure.persistence.audit_metadata_schema import REASONS
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget, RowRetention
from satori.infrastructure.persistence.retention_rules import (
    _PAYLOAD_OWNERS,
    _REVISION_CODES,
    _SOURCE_OWNERS,
)
from satori.infrastructure.persistence.retention_shapes import FOREIGN_KEYS
from satori.infrastructure.persistence.retention_values import (
    UnprovedRetention,
    id_list,
    integer,
    number,
    object_fields,
    require,
    text,
    timestamp,
)

if TYPE_CHECKING:
    from satori.domain.source_policy import SourceEligibilitySnapshot
    from satori.infrastructure.persistence.erasure_inventory import Row, Store


class RetentionOwnerProofs:
    store: Store
    snapshot: SourceEligibilitySnapshot
    budget: RetentionWorkBudget
    owners: ProvenOwners
    groups: dict[tuple[str, str], dict[object, list[Row]]]
    _preparations: set[tuple[str, int]]
    _preparation_rows: dict[tuple[str, int], list[Row]]
    _runs: set[str]
    _checkpoints: set[str]
    _payload_rows: dict[tuple[str, str, int], list[Row]]
    _closures: dict[tuple[str, str, int], frozenset[str]]

    def _row(self, table: str, column: str, value: object) -> Row:
        raise NotImplementedError

    def _json(self, table: str, row: Mapping[str, object], column: str) -> object:
        raise NotImplementedError

    def validate_row(self, table: str, row: Mapping[str, object]) -> RowRetention:
        raise NotImplementedError

    def _references(self, table: str, row: Mapping[str, object]) -> None:
        if "identity_id" in row:
            require(
                row["identity_id"] == self.snapshot.identity_id,
                RetentionReason.OWNER_SCOPE_UNPROVEN,
            )
        for column, target_table, target_column in FOREIGN_KEYS.get(table, ()):
            value = row[column]
            if value is None:
                continue
            target = self._row(target_table, target_column, value)
            shaped = self.validate_row(target_table, target)
            require(
                shaped.category is not RetentionClass.UNKNOWN,
                RetentionReason.OWNER_REFERENCE_UNPROVEN,
            )
            if "identity_id" in target:
                require(
                    target["identity_id"] == self.snapshot.identity_id,
                    RetentionReason.OWNER_SCOPE_UNPROVEN,
                )
        if "source_interaction_id" in row:
            self._interaction_scope(row["source_interaction_id"])
        if "root_interaction_id" in row:
            self._interaction_scope(row["root_interaction_id"])
        if "interaction_id" in row and table != "conversation_interactions":
            self._interaction_scope(row["interaction_id"])

    def _interaction_scope(self, interaction_id: object) -> Row:
        interaction = self._row("conversation_interactions", "interaction_id", interaction_id)
        session = self._row("conversation_sessions", "session_id", interaction["session_id"])
        require(
            session["identity_id"] == self.snapshot.identity_id,
            RetentionReason.OWNER_SCOPE_UNPROVEN,
        )
        return session

    def _canonical(
        self,
        message_id: object,
        *,
        interaction_id: object | None = None,
        counterparty_id: object | None = None,
        session_id: object | None = None,
    ) -> Row:
        key = text(message_id)
        self.budget.charge()
        require(self.owners.canonical(key), RetentionReason.INPUT_PROVENANCE_UNPROVEN)
        message = self._row("conversation_messages", "message_id", key)
        session = self._interaction_scope(message["interaction_id"])
        for table, candidate in (
            ("conversation_messages", message),
            ("conversation_sessions", session),
        ):
            require(
                self.validate_row(table, candidate).category is not RetentionClass.UNKNOWN,
                RetentionReason.INPUT_PROVENANCE_UNPROVEN,
            )
        require(
            message["session_id"] == session["session_id"], RetentionReason.OWNER_SCOPE_UNPROVEN
        )
        if interaction_id is not None:
            require(
                message["interaction_id"] == interaction_id,
                RetentionReason.OWNER_REFERENCE_UNPROVEN,
            )
        if counterparty_id is not None:
            require(
                session["counterparty_id"] == counterparty_id, RetentionReason.OWNER_SCOPE_UNPROVEN
            )
        if session_id is not None:
            require(session["session_id"] == session_id, RetentionReason.OWNER_SCOPE_UNPROVEN)
        return session

    def _admit(self, kind: str, payload_id: object, version: object) -> None:
        require(kind in _PAYLOAD_OWNERS, RetentionReason.OWNER_REFERENCE_UNPROVEN)
        self.budget.charge()
        table, column, _version = _PAYLOAD_OWNERS[kind]
        target = self._row(table, column, payload_id)
        require(
            self.validate_row(table, target).category is not RetentionClass.UNKNOWN,
            RetentionReason.OWNER_REFERENCE_UNPROVEN,
        )
        require(
            self.owners.admits((kind, text(payload_id), integer(version, 1))),
            RetentionReason.OWNER_SCOPE_UNPROVEN,
        )

    def _owner_contract(self, table: str, row: Mapping[str, object]) -> None:
        if table in {
            "alembic_version",
            "satori_identities",
            "satori_value_sets",
            "satori_values",
            "affective_states",
        }:
            if table == "satori_identities":
                require(row["identity_id"] == self.snapshot.identity_id)
                require(row["installation_slot"] == 1)
            return
        if table == "conversation_sessions":
            require(row["identity_id"] == self.snapshot.identity_id)
            return  # A quarantined scope is retained metadata, never canonical authority.
        if table == "conversation_interactions":
            self._interaction_scope(row["interaction_id"])
            return
        if table in {
            "episode_formation_decisions",
            "model_formation_decisions",
            "position_formation_decisions",
        }:
            session = self._interaction_scope(row["source_interaction_id"])
            interaction = self._row(
                "conversation_interactions", "interaction_id", row["source_interaction_id"]
            )
            if row["reason_code"] != "interaction_not_completed":
                require(
                    interaction["status"] == "completed", RetentionReason.INPUT_PROVENANCE_UNPROVEN
                )
            if "source_message_id" in row:
                self._canonical(
                    row["source_message_id"],
                    interaction_id=row["source_interaction_id"],
                    counterparty_id=row.get("counterparty_id"),
                )
            if table == "episode_formation_decisions" and row["memory_id"] is not None:
                memory = self._row("episodic_memories", "memory_id", row["memory_id"])
                require(memory["source_interaction_id"] == row["source_interaction_id"])
                require(memory["formation_version"] == row["formation_version"])
                self._admit("episode", row["memory_id"], 1)
            elif table == "model_formation_decisions":
                require(session["counterparty_id"] == row["counterparty_id"])
                self._decision_targets(table, row, "user_claim_ids", "user_model_claim")
                self._decision_targets(table, row, "world_claim_ids", "world_model_claim")
            elif table == "position_formation_decisions":
                self._decision_targets(table, row, "position_ids", "position")
            return
        if table == "semantic_formation_decisions":
            self._admit("episode", row["source_memory_id"], 1)
            self._decision_targets(table, row, "claim_ids", "semantic_claim")
            return
        if table == "memory_lifecycle_decisions":
            self._admit("episode", row["memory_id"], row["result_version"])
            memory = self._row("episodic_memories", "memory_id", row["memory_id"])
            scoped = self._interaction_scope(memory["source_interaction_id"])
            require(
                scoped["counterparty_id"] == row["counterparty_id"],
                RetentionReason.OWNER_SCOPE_UNPROVEN,
            )
            require(integer(row["result_version"], 1) <= integer(memory["aggregate_version"], 1))
            return
        if table == "semantic_claim_evidence":
            claim = self._row("semantic_claims", "claim_id", row["claim_id"])
            self._admit("semantic_claim", row["claim_id"], claim["aggregate_version"])
            edge = self._row("memory_evidence", "evidence_id", row["memory_evidence_id"])
            require(
                edge["memory_id"] == row["memory_id"]
                and edge["source_message_id"] == row["root_message_id"]
            )
            self._canonical(row["root_message_id"], interaction_id=row["root_interaction_id"])
            return
        if table in {"user_model_claim_evidence", "world_model_claim_evidence"}:
            kind = "user_model_claim" if table.startswith("user_") else "world_model_claim"
            owner_table, key, _version = _PAYLOAD_OWNERS[kind]
            claim = self._row(owner_table, key, row["claim_id"])
            self._admit(kind, row["claim_id"], claim["aggregate_version"])
            self._canonical(
                row["source_message_id"],
                interaction_id=row["source_interaction_id"],
                counterparty_id=claim["counterparty_id"],
            )
            return
        if table in _REVISION_CODES:
            self._revision_owner(table, row)
            return
        if table in {
            "reflection_runs",
            "reflection_sources",
            "reflection_attempts",
            "reflection_outcomes",
        }:
            if table == "reflection_outcomes":
                proposal = self._row("reflection_proposals", "proposal_id", row["proposal_id"])
                self._run_contract(proposal["run_id"])
                self._outcome_contract(row, proposal)
            else:
                self._run_contract(row["run_id"])
                if table == "reflection_sources":
                    self._canonical(
                        row["root_message_id"],
                        interaction_id=row["root_interaction_id"],
                        counterparty_id=row["root_counterparty_id"],
                    )
            return
        if table == "satori_inclination_evidence":
            inclination = self._row("satori_inclinations", "inclination_id", row["inclination_id"])
            self._admit("inclination", row["inclination_id"], inclination["aggregate_version"])
            source = self._row("reflection_sources", "source_id", row["reflection_source_id"])
            require(source["root_message_id"] == row["source_message_id"])
            self._canonical(
                row["source_message_id"],
                interaction_id=row["source_interaction_id"],
                counterparty_id=row["source_counterparty_id"],
                session_id=row["source_session_id"],
            )
            return
        if table in {"satori_personality_states", "satori_personality_traits"}:
            self._traits("satori_personality_traits", "identity_id", self.snapshot.identity_id)
            return
        if table in {"personality_checkpoints", "personality_checkpoint_traits"}:
            self._checkpoint_contract(row["checkpoint_id"])
            return
        if table in {"personality_revisions", "personality_evidence"}:
            revision = (
                row
                if table == "personality_revisions"
                else self._row("personality_revisions", "revision_id", row["revision_id"])
            )
            source = self._checkpoint_contract(revision["source_checkpoint_id"])
            result = self._checkpoint_contract(revision["resulting_checkpoint_id"])
            require(result["source_aggregate_version"] == revision["after_aggregate_version"])
            require(
                integer(source["source_aggregate_version"], 1)
                <= integer(revision["before_aggregate_version"], 1)
            )
            if revision["reflection_outcome_id"] is not None:
                outcome = self._row(
                    "reflection_outcomes", "outcome_id", revision["reflection_outcome_id"]
                )
                require(
                    outcome["decision"] == "accepted"
                    and outcome["target_aggregate_id"] == self.snapshot.identity_id
                )
            if table == "personality_evidence":
                require(
                    row["trait_key"] == revision["trait_key"]
                    and row["direction"] == revision["direction"]
                )
                source_edge = self._row(
                    "reflection_sources", "source_id", row["reflection_source_id"]
                )
                require(
                    source_edge["run_id"] == row["reflection_run_id"]
                    and source_edge["root_message_id"] == row["root_message_id"]
                )
                self._canonical(
                    row["root_message_id"],
                    interaction_id=row["root_interaction_id"],
                    counterparty_id=row["root_counterparty_id"],
                    session_id=row["root_session_id"],
                )
            return
        if table in {"relationship_states", "relationship_decisions", "relationship_transitions"}:
            self._relationship_contract(table, row)
            return
        if table in {"source_inputs", "source_payload_receipts"}:
            if table == "source_payload_receipts":
                self._admit(str(row["kind"]), row["payload_id"], row["payload_version"])
            self._source_contract(table, row)
            return
        if table == "source_excluded_roots":
            require(row["message_id"] in self.snapshot.excluded_message_ids)
            self._canonical(row["message_id"])
            integer(row["policy_generation"], 1, self.snapshot.generation)
            return
        if table in {
            "source_policy_bindings",
            "erasure_policy_bindings",
            "erasure_body_bindings",
            "source_owner_repairs",
        }:
            self._binding_contract(table, row)
            return
        if table == "owner_source_withdrawals":
            self._withdrawal_contract(row)
            return
        if table == "erasure_body_applications":
            from satori.domain.erasure_bodies import action_identity, action_lineage
            from satori.infrastructure.persistence.erasure_body_codec import decode_body_action

            action = decode_body_action(cast(dict[str, object], self._json(table, row, "action")))
            require(
                action.action_id == row["action_id"]
                and action.proposal.request_id == row["request_id"]
            )
            require(
                action_identity(action) == self.snapshot.identity_id
                and action_lineage(action) == self.snapshot.lineage_id
            )
            require(
                action.result_body_revision
                == integer(row["body_revision"], 1)
                <= self.snapshot.body_revision
            )
            require(action.floor_id == row["floor_id"] == self.snapshot.floor_id)
            require(action.installation_id == self.snapshot.installation_id)
            return
        if table == "erasure_preparations":
            self._preparation_contract(row)
            return
        if table == "audit_metadata_bindings":
            require(
                row["contract_version"] == 1
                and row["family"]
                in {
                    item.value
                    for item in AuditMetadataFamily
                    if item is not AuditMetadataFamily.APPRAISAL_TRANSITION
                }
            )
            if row["source_input_id"] is not None:
                self._source_contract(
                    "source_inputs", self._row("source_inputs", "input_id", row["source_input_id"])
                )
            return  # Exact event/owner certification additionally belongs to AuditMetadataIndex.
        raise UnprovedRetention(RetentionReason.ROW_SHAPE_UNPROVEN)

    def _decision_targets(
        self, table: str, row: Mapping[str, object], field: str, kind: str
    ) -> None:
        targets = id_list(self._json(table, row, field))
        for payload_id in targets:
            self.budget.charge()
            owner_table, key, version = _PAYLOAD_OWNERS[kind]
            target = self._row(owner_table, key, payload_id)
            self._admit(kind, payload_id, target[version] if version else 1)
        prefix = (
            "user_" if field == "user_claim_ids" else "world_" if field == "world_claim_ids" else ""
        )
        changed = sum(
            integer(row[prefix + name])
            for name in ("created_count", "merged_count", "superseded_count")
            if prefix + name in row
        )
        if prefix + "disputed_count" in row:
            changed += integer(row[prefix + "disputed_count"])
        if prefix + "competing_count" in row:
            changed += integer(row[prefix + "competing_count"])
        require(bool(targets) == bool(changed), RetentionReason.OWNER_REFERENCE_UNPROVEN)
        require(len(targets) <= changed, RetentionReason.OWNER_REFERENCE_UNPROVEN)

    def _revision_owner(self, table: str, row: Mapping[str, object]) -> None:
        if table == "satori_inclination_revisions":
            kind, key, version_field = "inclination", "inclination_id", "inclination_version"
        elif table == "satori_position_revisions":
            kind, key, version_field = "position", "position_id", "position_version"
        else:
            kind = {
                "semantic_claim_revisions": "semantic_claim",
                "user_model_claim_revisions": "user_model_claim",
                "world_model_claim_revisions": "world_model_claim",
            }[table]
            key, version_field = "claim_id", "claim_version"
        self._admit(kind, row[key], row[version_field])
        if row.get("decision_id") is not None:
            decision_table = {
                "semantic_claim": "semantic_formation_decisions",
                "position": "position_formation_decisions",
            }.get(kind, "model_formation_decisions")
            decision = self._row(decision_table, "decision_id", row["decision_id"])
            require(timestamp(row["occurred_at"]) == timestamp(decision["decided_at"]))
            targets_field = {
                "semantic_claim": "claim_ids",
                "position": "position_ids",
                "user_model_claim": "user_claim_ids",
                "world_model_claim": "world_claim_ids",
            }[kind]
            require(row[key] in id_list(self._json(decision_table, decision, targets_field)))
        else:
            outcome = self._row("reflection_outcomes", "outcome_id", row["reflection_outcome_id"])
            require(
                outcome["decision"] == "accepted" and outcome["target_aggregate_id"] == row[key]
            )
            require(timestamp(row["occurred_at"]) == timestamp(outcome["decided_at"]))
            if table == "satori_inclination_revisions":
                require(outcome["reason_code"] == row["reason_code"])
        created = row["kind"] == "created"
        if "prior_status" in row:
            require(
                (row["prior_status"] is None) == created
                or (row["kind"] == "disputed" and row["prior_status"] is None)
            )
            require((row["prior_confidence"] is None) == (row["prior_status"] is None))
            statuses = {
                "semantic_claim": {"active", "superseded", "disputed", "retracted"},
                "position": {"active", "competing", "superseded", "retracted"},
            }.get(kind, {"current", "superseded", "disputed", "retracted", "expired"})
            require(
                row["new_status"] in statuses
                and (row["prior_status"] is None or row["prior_status"] in statuses)
            )
            if row["kind"] in {"superseded", "disputed", "retracted", "competing", "expired"}:
                require(row["new_status"] == row["kind"])
        if table == "satori_inclination_revisions":
            require((row["prior_score"] is None) == created)
            require((row["inclination_version"] == 1) == created)

    def _run_contract(self, run_id: object) -> None:
        from satori.core.reflection import (
            ReflectionLineageKind,
            ReflectionPurpose,
            ReflectionSourceKind,
        )
        from satori.domain.reflection import (
            ReflectionSourceRecord,
            reflection_run_key,
            source_set_hash,
        )

        key = text(run_id)
        if key in self._runs:
            return
        run = self._row("reflection_runs", "run_id", key)
        require(
            run["identity_id"] == self.snapshot.identity_id, RetentionReason.OWNER_SCOPE_UNPROVEN
        )
        require(self.validate_row("reflection_runs", run).category is not RetentionClass.UNKNOWN)
        sources = self.groups.get(("reflection_sources", "run_id"), {}).get(key, [])
        require(bool(sources) and len(sources) <= 12, RetentionReason.OWNER_REFERENCE_UNPROVEN)
        records = []
        for source in sources:
            self.budget.charge()
            require(
                self.validate_row("reflection_sources", source).category
                is not RetentionClass.UNKNOWN
            )
            self._canonical(
                source["root_message_id"],
                interaction_id=source["root_interaction_id"],
                counterparty_id=source["root_counterparty_id"],
            )
            edge_table = (
                "satori_position_evidence"
                if source["kind"] == "position_evidence"
                else "memory_evidence"
            )
            edge = self._row(edge_table, "evidence_id", source["evidence_edge_id"])
            require(edge["source_message_id"] == source["root_message_id"])
            parent_kind = "position" if edge_table == "satori_position_evidence" else "episode"
            parent_id = edge["position_id"] if parent_kind == "position" else edge["memory_id"]
            self._admit(parent_kind, parent_id, source["evidence_edge_version"])
            if source["upstream_lineage_kind"] is not None:
                expected = "position" if parent_kind == "position" else "episodic_memory"
                require(
                    source["upstream_lineage_kind"] == expected
                    and source["upstream_lineage_id"] == parent_id
                )
            records.append(
                ReflectionSourceRecord(
                    source_id=str(source["source_id"]),
                    run_id=key,
                    ordinal=integer(source["ordinal"]),
                    kind=ReflectionSourceKind(str(source["kind"])),
                    evidence_edge_id=str(source["evidence_edge_id"]),
                    evidence_edge_version=integer(source["evidence_edge_version"], 1),
                    root_interaction_id=str(source["root_interaction_id"]),
                    root_message_id=str(source["root_message_id"]),
                    root_counterparty_id=str(source["root_counterparty_id"]),
                    observed_at=timestamp(source["observed_at"]),
                    content_hash=str(source["content_hash"]),
                    upstream_lineage_kind=None
                    if source["upstream_lineage_kind"] is None
                    else ReflectionLineageKind(str(source["upstream_lineage_kind"])),
                    upstream_lineage_id=None
                    if source["upstream_lineage_id"] is None
                    else str(source["upstream_lineage_id"]),
                    affective_transition_id=None
                    if source["affective_transition_id"] is None
                    else str(source["affective_transition_id"]),
                    affective_state_version=None
                    if source["affective_state_version"] is None
                    else integer(source["affective_state_version"], 1),
                    affective_signal_hash=None
                    if source["affective_signal_hash"] is None
                    else str(source["affective_signal_hash"]),
                )
            )
        schema = integer(run["schema_version"], 1, 3)
        purpose = ReflectionPurpose(str(run["purpose"]))
        actual_hash = source_set_hash(tuple(records), schema_version=schema, purpose=purpose)
        require(actual_hash == run["source_set_hash"], RetentionReason.OWNER_REFERENCE_UNPROVEN)
        require(
            reflection_run_key(
                identity_id=self.snapshot.identity_id,
                source_hash=actual_hash,
                schema_version=schema,
                policy_version=integer(run["policy_version"], 1, 3),
                purpose=purpose,
            )
            == run["run_key"]
        )
        attempts = self.groups.get(("reflection_attempts", "run_id"), {}).get(key, [])
        require(len(attempts) == run["attempt_count"])
        require(
            sorted(integer(attempt["ordinal"], 1, 2) for attempt in attempts)
            == list(range(1, len(attempts) + 1))
        )
        for attempt in attempts:
            self.budget.charge()
            require(
                self.validate_row("reflection_attempts", attempt).category
                is not RetentionClass.UNKNOWN
            )
        proposals = self.groups.get(("reflection_proposals", "run_id"), {}).get(key, [])
        require(len(proposals) <= 3)
        source_ids = {str(source["source_id"]) for source in sources}
        for proposal in proposals:
            self.budget.charge()
            require(
                set(
                    id_list(
                        self._json("reflection_proposals", proposal, "evidence_source_ids"),
                        nonempty=True,
                    )
                ).issubset(source_ids)
            )
        self._runs.add(key)

    def _outcome_contract(self, row: Mapping[str, object], proposal: Mapping[str, object]) -> None:
        target_owner = str(proposal["target_owner"])
        family = {
            "satori_positions": "reflection_position",
            "satori_inclinations": "reflection_inclination",
            "personality": "reflection_personality",
            "values": "reflection_disabled",
        }[target_owner]
        require(row["reason_code"] in REASONS[family], RetentionReason.SCALAR_CODE_UNSUPPORTED)
        accepted_codes = {
            "satori_positions": {"position_owner_accepted"},
            "satori_inclinations": {
                "eligible_inclination_created",
                "inclination_strengthened",
                "inclination_weakened",
            },
            "personality": {"personality_evolution_applied"},
            "values": set(),
        }
        require(
            (row["reason_code"] in accepted_codes[target_owner]) == (row["decision"] == "accepted"),
            RetentionReason.SCALAR_CODE_UNSUPPORTED,
        )
        accepted = row["decision"] == "accepted"
        if target_owner == "values":
            require(
                not accepted
                and row["target_aggregate_id"] is None
                and row["target_aggregate_type"] is None
            )
        elif accepted:
            require(
                row["target_aggregate_id"] is not None and row["target_aggregate_type"] is not None
            )
            if target_owner == "personality":
                require(row["target_aggregate_id"] == self.snapshot.identity_id)
                require(row["reason_code"] == "personality_evolution_applied")
            else:
                kind = "position" if target_owner == "satori_positions" else "inclination"
                owner_table, column, version = _PAYLOAD_OWNERS[kind]
                target = self._row(owner_table, column, row["target_aggregate_id"])
                self._admit(kind, row["target_aggregate_id"], target[version] if version else 1)
        else:
            require(row["target_aggregate_id"] is None and row["target_aggregate_type"] is None)

    def _traits(self, table: str, column: str, value: object) -> tuple[Row, ...]:
        rows = tuple(self.groups.get((table, column), {}).get(value, []))
        self.budget.charge(len(rows))
        require(
            len(rows) == len(CANONICAL_TRAIT_KEYS)
            and {row["trait_key"] for row in rows} == set(CANONICAL_TRAIT_KEYS),
            RetentionReason.OWNER_REFERENCE_UNPROVEN,
        )
        for row in rows:
            require(self.validate_row(table, row).category is not RetentionClass.UNKNOWN)
        return rows

    def _checkpoint_contract(self, checkpoint_id: object) -> Row:
        from satori.domain.personality import Personality, PersonalityTrait
        from satori.domain.personality_evolution import PersonalityCheckpointKind, checkpoint_hash

        key = text(checkpoint_id)
        checkpoint = self._row("personality_checkpoints", "checkpoint_id", key)
        require(
            checkpoint["identity_id"] == self.snapshot.identity_id,
            RetentionReason.OWNER_SCOPE_UNPROVEN,
        )
        if key in self._checkpoints:
            return checkpoint
        require(
            self.validate_row("personality_checkpoints", checkpoint).category
            is not RetentionClass.UNKNOWN
        )
        traits = self._traits("personality_checkpoint_traits", "checkpoint_id", key)
        state = Personality(
            schema_version=1,
            aggregate_version=integer(checkpoint["source_aggregate_version"], 1),
            traits=tuple(
                PersonalityTrait(
                    str(row["trait_key"]),
                    number(row["value"], 0, 1),
                    number(row["baseline_value"], 0, 1),
                )
                for row in traits
            ),
        )
        require(
            checkpoint_hash(
                identity_id=self.snapshot.identity_id,
                checkpoint_kind=PersonalityCheckpointKind(str(checkpoint["checkpoint_kind"])),
                personality=state,
            )
            == checkpoint["checkpoint_hash"],
            RetentionReason.OWNER_REFERENCE_UNPROVEN,
        )
        self._checkpoints.add(key)
        return checkpoint

    def _relationship_contract(self, table: str, row: Mapping[str, object]) -> None:
        state = (
            row
            if table == "relationship_states"
            else self._row("relationship_states", "relationship_id", row["relationship_id"])
        )
        require(
            state["identity_id"] == self.snapshot.identity_id, RetentionReason.OWNER_SCOPE_UNPROVEN
        )
        if table == "relationship_states":
            require(
                integer(row["qualified_interaction_count"])
                <= integer(row["processed_interaction_count"])
            )
            require(
                integer(row["distinct_session_count"])
                <= integer(row["processed_interaction_count"])
            )
            return
        self._canonical(
            row["source_user_message_id"],
            interaction_id=row["interaction_id"],
            counterparty_id=state["counterparty_id"],
            session_id=row["session_id"],
        )
        if table == "relationship_decisions":
            expected = {
                "applied": "bounded_relationship_event_applied",
                "skipped": "bounded_no_effect",
                "rejected": "low_confidence",
            }
            require(row["reason_code"] == expected.get(str(row["kind"])))
            require((row["kind"] == "applied") == (row["transition_id"] is not None))
            if row["transition_id"] is not None:
                transition = self._row(
                    "relationship_transitions", "transition_id", row["transition_id"]
                )
                require(
                    transition["relationship_id"] == row["relationship_id"]
                    and transition["interaction_id"] == row["interaction_id"]
                )
        else:
            before = object_fields(
                self._json(table, row, "state_before"), self._relationship_snapshot_fields()
            )
            after = object_fields(
                self._json(table, row, "state_after"), self._relationship_snapshot_fields()
            )
            require(before["state_version"] == row["base_state_version"])
            require(
                after["state_version"]
                == row["resulting_state_version"]
                == integer(row["base_state_version"], 1) + 1
            )
            for snapshot in (before, after):
                require(
                    snapshot["identity_id"] == self.snapshot.identity_id
                    and snapshot["counterparty_id"] == state["counterparty_id"]
                    and snapshot["relationship_id"] == row["relationship_id"]
                )
            require(timestamp(after["updated_at"]) == timestamp(row["committed_at"]))

    @staticmethod
    def _relationship_snapshot_fields() -> set[str]:
        return {
            "relationship_id",
            "identity_id",
            "counterparty_id",
            "schema_version",
            "state_version",
            "policy_version",
            "vector",
            "processed_interaction_count",
            "qualified_interaction_count",
            "distinct_session_count",
            "positive_evidence_count",
            "negative_evidence_count",
            "updated_at",
        }

    def _source_contract(self, table: str, row: Mapping[str, object]) -> None:
        require(
            row["identity_id"] == self.snapshot.identity_id, RetentionReason.OWNER_SCOPE_UNPROVEN
        )
        integer(row["policy_generation"], 0, self.snapshot.generation)
        if row["proof_status"] != "complete":
            # Known status metadata; this does not grant closure or learning eligibility.
            if table == "source_payload_receipts":
                require(not row["eligible"], RetentionReason.INPUT_PROVENANCE_UNPROVEN)
            return
        require(not row["legacy"], RetentionReason.INPUT_PROVENANCE_UNPROVEN)
        roots = self._source_roots(table, row, set(), 0)
        require(bool(roots), RetentionReason.INPUT_PROVENANCE_UNPROVEN)
        # Generic footprints may contain identity-global self references across people.
        # The referenced payload owner enforces its own scope; this is not input admission.

    def _source_roots(
        self, table: str, row: Mapping[str, object], stack: set[tuple[str, str, int]], depth: int
    ) -> frozenset[str]:
        self.budget.charge()
        require(depth <= 32, RetentionReason.INPUT_PROVENANCE_UNPROVEN)
        require(
            self.validate_row(table, row).category is not RetentionClass.UNKNOWN,
            RetentionReason.INPUT_PROVENANCE_UNPROVEN,
        )
        integer(row["policy_generation"], 0, self.snapshot.generation)
        require(
            row["identity_id"] == self.snapshot.identity_id
            and row["proof_status"] == "complete"
            and not row["legacy"],
            RetentionReason.INPUT_PROVENANCE_UNPROVEN,
        )
        roots = set(id_list(self._json(table, row, "root_message_ids")))
        for root in roots:
            self._canonical(root)
        refs = self._json(table, row, "payload_refs")
        require(isinstance(refs, list))
        assert isinstance(refs, list)
        for item in refs:
            self.budget.charge()
            data = object_fields(item, {"kind", "payload_id", "version"})
            ref = (str(data["kind"]), str(data["payload_id"]), integer(data["version"], 1))
            require(ref not in stack, RetentionReason.INPUT_PROVENANCE_UNPROVEN)
            if ref not in self._closures:
                values = self._payload_rows.get(ref, [])
                require(len(values) == 1, RetentionReason.INPUT_PROVENANCE_UNPROVEN)
                child = values[0]
                self._admit(*ref)
                closure = self._source_roots(
                    "source_payload_receipts", child, stack | {ref}, depth + 1
                )
                require(bool(closure), RetentionReason.INPUT_PROVENANCE_UNPROVEN)
                self._closures[ref] = closure
            roots.update(self._closures[ref])
            require(len(roots) <= 512, RetentionReason.INPUT_PROVENANCE_UNPROVEN)
        return frozenset(roots)

    def _binding_contract(self, table: str, row: Mapping[str, object]) -> None:
        if table == "source_owner_repairs":
            require(
                row["owner"] in _SOURCE_OWNERS
                and row["policy_generation"] == self.snapshot.generation
            )
            return
        require(
            row["floor_id"] == self.snapshot.floor_id
            and row["lineage_id"] == self.snapshot.lineage_id
        )
        if table == "source_policy_bindings":
            require(
                row["installation_id"] == self.snapshot.installation_id
                and row["policy_generation"] == self.snapshot.generation
            )
        elif table == "erasure_policy_bindings":
            require(row["erasure_revision"] == self.snapshot.erasure_revision)
        else:
            require(row["body_revision"] == self.snapshot.body_revision)

    def _withdrawal_contract(self, row: Mapping[str, object]) -> None:
        mapping = {
            "semantic_memory": "semantic_claim",
            "user_model_claim": "user_model_claim",
            "world_model_claim": "world_model_claim",
            "position": "position",
            "inclination": "inclination",
            "reflection_run": "reflection_run",
            "relationship_backlog": "relationship_backlog",
        }
        require(row["owner"] in mapping, RetentionReason.SCALAR_CODE_UNSUPPORTED)
        kind = mapping[str(row["owner"])]
        if kind == "reflection_run":
            self._run_contract(row["aggregate_id"])
        elif kind == "relationship_backlog":
            self._interaction_scope(row["aggregate_id"])
        else:
            self._admit(kind, row["aggregate_id"], row["after_version"])
        require(integer(row["after_version"], 1) >= integer(row["before_version"], 1))
        roots = id_list(self._json("owner_source_withdrawals", row, "root_message_ids"))
        for root in roots:
            self._canonical(root)
            require(root in self.snapshot.excluded_message_ids)
        require(integer(row["policy_generation"]) <= self.snapshot.generation)
        status_pairs = {
            "semantic_memory": ({"active", "disputed", "superseded", "retracted"}, {"retracted"}),
            "user_model_claim": (
                {"current", "disputed", "superseded", "expired", "retracted"},
                {"retracted"},
            ),
            "world_model_claim": (
                {"current", "disputed", "superseded", "expired", "retracted"},
                {"retracted"},
            ),
            "position": ({"active", "competing", "superseded", "retracted"}, {"retracted"}),
            "inclination": ({"retained"}, {"disclosure_held"}),
            "reflection_run": (
                {
                    "pending_generation",
                    "proposals_ready",
                    "applying",
                    "completed",
                    "retryable_failure",
                    "exhausted",
                },
                {"completed", "exhausted"},
            ),
            "relationship_backlog": ({"undecided"}, {"excluded"}),
        }
        before, after = status_pairs[str(row["owner"])]
        require(row["before_status"] in before and row["after_status"] in after)

    def _preparation_contract(self, row: Mapping[str, object]) -> None:
        require(row["owner"] in {owner.value for owner in ErasurePreparationOwner})
        require(
            row["floor_id"] == self.snapshot.floor_id
            and row["installation_id"] == self.snapshot.installation_id
            and row["lineage_id"] == self.snapshot.lineage_id
        )
        self._canonical(
            row["root_message_id"],
            interaction_id=row["root_interaction_id"],
            counterparty_id=row["counterparty_id"],
            session_id=row["root_session_id"],
        )
        require(row["root_message_id"] in self.snapshot.excluded_message_ids)
        require(
            row["result_erasure_revision"]
            == integer(row["expected_erasure_revision"]) + 1
            <= self.snapshot.erasure_revision
        )
        require(integer(row["source_generation"]) >= integer(row["expected_source_generation"]))
        require(
            row["assessment_status"] in {"complete", "blocked"}
            and row["intent_assessment_status"] in {"complete", "blocked"}
        )
        readiness = str(row["body_readiness"])
        reason = {
            "retain_only": "owner_retained_metadata_only",
            "blocked_executor": "owner_content_executor_not_implemented",
            "blocked_owner_policy": "owner_retention_policy_not_implemented",
            "blocked_provenance": "owner_content_provenance_incomplete",
        }
        require(
            readiness in reason
            and id_list(self._json("erasure_preparations", row, "reason_codes"))
            == (reason[readiness],)
        )

        key = (text(row["request_id"]), integer(row["preparation_revision"], 1))
        if key not in self._preparations:
            from satori.infrastructure.persistence.erasure_preparation import _map
            from satori.infrastructure.persistence.models.erasure_intent import (
                ErasurePreparationRow,
            )

            group = self._preparation_rows.get(key, [])
            require(
                len(group) == len(ErasurePreparationOwner), RetentionReason.OWNER_REFERENCE_UNPROVEN
            )
            records = []
            for candidate in group:
                self.budget.charge()
                require(
                    self.validate_row("erasure_preparations", candidate).category
                    is not RetentionClass.UNKNOWN
                )
                values = dict(candidate)
                values["counts"] = self._json("erasure_preparations", candidate, "counts")
                values["reason_codes"] = self._json(
                    "erasure_preparations", candidate, "reason_codes"
                )
                for column in ("decided_at", "prepared_at"):
                    values[column] = timestamp(values[column])
                records.append(ErasurePreparationRow(**values))
            _map(records)
            self._preparations.add(key)
