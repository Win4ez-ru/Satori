"""Closed version-one metadata and numerical retention, with indexed owner joins.

These classifications describe retained rows. They never issue source admission, upgrade
legacy receipts, run owner policies, or reinterpret historical body-action material.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from satori.core.conversation import ConversationProviderFailureReason
from satori.core.personality import CANONICAL_TRAIT_KEYS
from satori.domain.relationship import RelationshipEventCategory
from satori.domain.retention import RetentionClass, RetentionFacet, RetentionReason
from satori.infrastructure.persistence.audit_metadata_schema import REASONS
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget, RowRetention
from satori.infrastructure.persistence.retention_shapes import ENUM_FIELDS, FOREIGN_KEYS, ROW_FIELDS
from satori.infrastructure.persistence.retention_values import (
    UnprovedRetention,
    digest,
    document,
    embedding_vector,
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
    from satori.infrastructure.persistence.canonical_provenance import HistoricalProvenance
    from satori.infrastructure.persistence.erasure_inventory import Row, Store

from satori.infrastructure.persistence.retention_owner_proofs import RetentionOwnerProofs
from satori.infrastructure.persistence.retention_rules import (
    _BODY_FIELDS,
    _DECISION_FAMILIES,
    _HASH_FIELDS,
    _METADATA,
    _NUMERIC,
    _PAYLOAD_OWNERS,
    _REVISION_CODES,
    _SIGNED_FIELDS,
    _SOURCE_OWNERS,
    _UNIT_FIELDS,
    _V1_FIELDS,
)


def _unknown(reason: RetentionReason) -> RowRetention:
    return RowRetention(RetentionClass.UNKNOWN, reasons=(reason,))


@dataclass(slots=True)
class MetadataRetentionIndex(RetentionOwnerProofs):
    store: Store
    snapshot: SourceEligibilitySnapshot
    historical: HistoricalProvenance | None
    budget: RetentionWorkBudget
    indices: dict[tuple[str, str], dict[object, Row]] = field(default_factory=dict)
    groups: dict[tuple[str, str], dict[object, list[Row]]] = field(default_factory=dict)
    owners: ProvenOwners = field(init=False)
    _documents: dict[tuple[str, str, object], object] = field(default_factory=dict)
    _closures: dict[tuple[str, str, int], frozenset[str]] = field(default_factory=dict)
    _runs: set[str] = field(default_factory=set)
    _preparations: set[tuple[str, int]] = field(default_factory=set)
    _preparation_rows: dict[tuple[str, int], list[Row]] = field(default_factory=dict)
    _checkpoints: set[str] = field(default_factory=set)
    _payload_rows: dict[tuple[str, str, int], list[Row]] = field(default_factory=dict)
    _shape_rows: dict[tuple[str, int], Mapping[str, object]] = field(default_factory=dict)
    _shape_cache: dict[tuple[str, int], RowRetention] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        store: Store,
        *,
        snapshot: SourceEligibilitySnapshot,
        historical: HistoricalProvenance | None,
        budget: RetentionWorkBudget,
    ) -> MetadataRetentionIndex:
        result = cls(store, snapshot, historical, budget)
        keys: dict[str, set[str]] = {}
        for table, fields in ROW_FIELDS.items():
            keys.setdefault(table, set()).add(fields.split()[0].split(":")[0])
        for refs in FOREIGN_KEYS.values():
            for _column, table, target in refs:
                keys.setdefault(table, set()).add(target)
        for table, column in (
            ("reflection_sources", "run_id"),
            ("reflection_attempts", "run_id"),
            ("reflection_proposals", "run_id"),
            ("reflection_outcomes", "proposal_id"),
            ("personality_checkpoint_traits", "checkpoint_id"),
            ("satori_personality_traits", "identity_id"),
            ("source_payload_receipts", "payload_id"),
        ):
            keys.setdefault(table, set()).add(column)
        for table, columns in keys.items():
            for column in columns:
                index: dict[object, Row] = {}
                groups: dict[object, list[Row]] = {}
                for row in store.get(table, ()):
                    budget.charge()
                    value = row.get(column)
                    if isinstance(value, (str, int)):
                        index[value] = row
                        groups.setdefault(value, []).append(row)
                result.indices[table, column] = index
                result.groups[table, column] = groups
        for row in store.get("source_payload_receipts", ()):
            budget.charge()
            key = (str(row.get("kind")), str(row.get("payload_id")), row.get("payload_version"))
            if type(key[2]) is int:
                result._payload_rows.setdefault((key[0], key[1], key[2]), []).append(row)
        for row in store.get("erasure_preparations", ()):
            budget.charge()
            revision = row.get("preparation_revision")
            if isinstance(row.get("request_id"), str) and type(revision) is int:
                result._preparation_rows.setdefault((str(row["request_id"]), revision), []).append(
                    row
                )
        # ProvenOwners builds bounded reference joins once; account before construction.
        budget.charge(sum(len(rows) for rows in store.values()) * 3)
        complete_store = {table: store.get(table, ()) for table in ROW_FIELDS}
        result.owners = ProvenOwners(complete_store, snapshot.identity_id, historical=historical)
        return result

    def _row(self, table: str, column: str, value: object) -> Row:
        self.budget.charge()
        values = self.groups.get((table, column), {}).get(value, [])
        require(len(values) == 1, RetentionReason.OWNER_REFERENCE_UNPROVEN)
        return values[0]

    def _json(self, table: str, row: Mapping[str, object], column: str) -> object:
        key = (table, column, id(row))
        if key not in self._documents:
            self._documents[key] = (
                embedding_vector(row[column], row["dimensions"], self.budget)
                if table == "episodic_memory_embeddings" and column == "vector"
                else document(row[column], self.budget)
            )
        return self._documents[key]

    def validate_row(self, table: str, row: Mapping[str, object]) -> RowRetention:
        key = (table, id(row))
        if key in self._shape_cache:
            return self._shape_cache[key]
        self.budget.charge()
        try:
            self._shape(table, row)
        except UnprovedRetention as error:
            result = _unknown(error.reason)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            result = _unknown(RetentionReason.ROW_SHAPE_UNPROVEN)
        else:
            result = RowRetention(RetentionClass.SUPPORTED_RETAINED)
        self._shape_rows[key] = row
        self._shape_cache[key] = result
        return result

    def classify(self, table: str, row: Mapping[str, object]) -> RowRetention:
        shaped = self.validate_row(table, row)
        if shaped.category is RetentionClass.UNKNOWN:
            return shaped
        if table not in _METADATA | _NUMERIC:
            return _unknown(RetentionReason.ROW_SHAPE_UNPROVEN)
        try:
            self._references(table, row)
            self._owner_contract(table, row)
        except UnprovedRetention as error:
            return _unknown(error.reason)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return _unknown(RetentionReason.OWNER_REFERENCE_UNPROVEN)
        facets = {RetentionFacet.STRUCTURAL_METADATA}
        if table in _NUMERIC:
            facets.add(RetentionFacet.NUMERIC_HISTORY)
        category = (
            RetentionClass.OWNED_CONTENT
            if table in {"satori_identities", "satori_values"}
            else RetentionClass.SUPPORTED_RETAINED
        )
        return RowRetention(category, frozenset(facets))

    def _shape(self, table: str, row: Mapping[str, object]) -> None:
        require(table in ROW_FIELDS)
        fields = {item.split(":")[0]: item.split(":")[1] for item in ROW_FIELDS[table].split()}
        require(set(row) == set(fields))
        for column, specification in fields.items():
            self.budget.charge()
            value = row[column]
            nullable = specification.endswith("?")
            if value is None:
                require(nullable)
                continue
            kind = specification[0]
            if kind == "s":
                text(value, int(specification[1:].rstrip("?")))
            elif kind == "i":
                integer(value, 0)
            elif kind == "b":
                require(type(value) in (int, bool) and value in (0, 1))
            elif kind == "f":
                if column in _UNIT_FIELDS and not (column == "value" and table in _BODY_FIELDS):
                    number(value, 0, 1)
                elif column in _SIGNED_FIELDS:
                    number(value)
                elif column == "inclination_curiosity_influence":
                    number(value, 0, 0.20)
                else:
                    number(value, 0, 2**63 - 1)
            elif kind == "t":
                timestamp(value)
            elif kind == "j" and column not in _BODY_FIELDS.get(table, ()):
                self._structured(table, row, column, self._json(table, row, column))
            if column in _V1_FIELDS and kind == "i":
                supported = {1, 2, 3} if table == "reflection_runs" else {1}
                require(value in supported, RetentionReason.ROW_VERSION_UNSUPPORTED)
            elif column.endswith("version") and kind == "i":
                integer(value, 1)
            if column in _HASH_FIELDS:
                digest(value)
            allowed = ENUM_FIELDS.get((table, column))
            if allowed is not None:
                require(value in allowed, RetentionReason.SCALAR_CODE_UNSUPPORTED)
        self._scalar_contract(table, row)
        self._row_relations(table, row)

    def _structured(
        self, table: str, row: Mapping[str, object], column: str, value: object
    ) -> None:
        if column in {
            "root_message_ids",
            "claim_ids",
            "user_claim_ids",
            "world_claim_ids",
            "position_ids",
            "evidence_source_ids",
            "source_refs",
            "retrieved_memory_ids",
            "retrieved_semantic_claim_ids",
            "user_model_context_claim_ids",
            "world_model_context_claim_ids",
            "position_context_ids",
            "inclination_context_ids",
        }:
            id_list(value)
        elif column == "payload_refs":
            require(isinstance(value, list))
            assert isinstance(value, list)
            seen: set[tuple[str, str, int]] = set()
            for item in value:
                self.budget.charge()
                data = object_fields(item, {"kind", "payload_id", "version"})
                kind = text(data["kind"], 64)
                require(kind in _PAYLOAD_OWNERS, RetentionReason.INPUT_PROVENANCE_UNPROVEN)
                ref = (kind, text(data["payload_id"]), integer(data["version"], 1))
                require(ref not in seen)
                seen.add(ref)
        elif column == "metrics":
            keys = {
                "provider_total_ms",
                "provider_load_ms",
                "provider_prompt_eval_ms",
                "provider_eval_ms",
                "provider_prompt_tokens",
                "provider_output_tokens",
                "provider_prompt_tokens_per_second",
                "provider_output_tokens_per_second",
                "client_request_build_ms",
                "client_http_roundtrip_ms",
                "client_response_parse_ms",
                "requested_output_token_limit",
                "provider_output_token_limit",
                "reasoning_output_tokens",
                "visible_output_tokens",
            }
            require(isinstance(value, dict) and set(value).issubset(keys))
            assert isinstance(value, dict)
            for key, item in value.items():
                if item is None:
                    continue
                if key.endswith("tokens") or key.endswith("token_limit"):
                    integer(item, 1 if key.endswith("token_limit") else 0)
                else:
                    number(item, 0, 2**63 - 1)
        elif column == "categories":
            categories = id_list(value, maximum=12)
            require(set(categories).issubset({item.value for item in RelationshipEventCategory}))
        elif column == "trait_diffs":
            require(isinstance(value, list))
            assert isinstance(value, list)
            seen_traits: set[str] = set()
            for item in value:
                self.budget.charge()
                diff = object_fields(item, {"trait_key", "before_value", "after_value"})
                trait = text(diff["trait_key"], 64)
                require(trait in CANONICAL_TRAIT_KEYS and trait not in seen_traits)
                seen_traits.add(trait)
                number(diff["before_value"], 0, 1)
                number(diff["after_value"], 0, 1)
        elif column == "reason_codes" and table == "erasure_preparations":
            reasons = id_list(value, maximum=1, nonempty=True)
            require(
                set(reasons).issubset(
                    {
                        "owner_retained_metadata_only",
                        "owner_content_executor_not_implemented",
                        "owner_retention_policy_not_implemented",
                        "owner_content_provenance_incomplete",
                    }
                ),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        elif column == "counts" and table == "erasure_preparations":
            data = object_fields(
                value, {"content_rows", "numeric_rows", "metadata_rows", "uncertain_rows"}
            )
            for item in data.values():
                integer(item)
            require(integer(data["uncertain_rows"]) <= integer(data["content_rows"]))
        elif (
            column in {"state_before", "state_after", "applied_delta"}
            and table == "relationship_transitions"
        ):
            self._relationship_document(value, snapshot=column != "applied_delta")
        elif table == "affective_transitions":
            # The exact owner numerical record is checked once after all JSON is bounded.
            require(isinstance(value, (dict, list)))
        elif column == "action" and table == "erasure_body_applications":
            require(isinstance(value, dict))  # Whole-head authority is checked by RetentionContext.
        elif column == "personality_expression_cues":
            cues = id_list(value, maximum=15)
            codes = {
                "curious_analytical",
                "independent_position",
                "warm_perceptive",
                "light_irony",
                "considered_directness",
                "grounded_optimism",
            }
            require(
                all(
                    cue.partition(":")[0] in codes
                    and cue.partition(":")[2] in {"slightly_stronger", "slightly_softer"}
                    for cue in cues
                ),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
            require(len({cue.partition(":")[0] for cue in cues}) == len(cues))
        elif table == "episodic_memory_embeddings" and column == "vector":
            # _json supplies the fully proved vector role, cached for this exact row.
            require(isinstance(value, list) and len(value) == row["dimensions"])
        else:
            raise UnprovedRetention(RetentionReason.STRUCTURED_VALUE_UNPROVEN)

    def _scalar_contract(self, table: str, row: Mapping[str, object]) -> None:
        if table in _DECISION_FAMILIES:
            require(
                row["reason_code"] in REASONS[_DECISION_FAMILIES[table]],
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        elif table in _REVISION_CODES:
            require(
                row["reason_code"] in _REVISION_CODES[table].get(str(row["kind"]), set()),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        elif table == "reflection_attempts":
            choices = {
                "succeeded": {"proposals_ready", "zero_proposals_completed"},
                "failed": {"provider_invalid_or_unavailable"},
            }
            require(
                row["reason_code"] in choices.get(str(row["status"]), set()),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        elif table == "reflection_outcomes":
            allowed = set().union(
                *(
                    REASONS[family]
                    for family in (
                        "reflection_position",
                        "reflection_inclination",
                        "reflection_personality",
                        "reflection_disabled",
                    )
                )
            )
            require(row["reason_code"] in allowed, RetentionReason.SCALAR_CODE_UNSUPPORTED)
        elif table == "personality_revisions":
            expected = {
                "evolution": "personality_evolution_applied",
                "restore": "personality_checkpoint_restored",
            }
            require(
                row["reason_code"] == expected.get(str(row["revision_kind"])),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )

        matrices = {
            "semantic_formation_decisions": {
                "applied": {"claims_applied"},
                "skipped": {"no_claims", "no_material_change"},
                "rejected": {"all_claims_rejected"},
            },
            "model_formation_decisions": {
                "applied": {"owner_changes_applied"},
                "skipped": {"no_supported_current_claims"},
                "rejected": {"proposal_rejected"},
            },
            "position_formation_decisions": {
                "applied": {"owner_changes_applied"},
                "skipped": {"no_new_eligible_evidence"},
                "rejected": {"proposal_rejected"},
            },
        }
        if table in matrices:
            require(
                row["reason_code"] in matrices[table].get(str(row["kind"]), set()),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )

    def _row_relations(self, table: str, row: Mapping[str, object]) -> None:
        for earlier, later in (
            ("started_at", "ended_at"),
            ("started_at", "completed_at"),
            ("started_at", "finished_at"),
            ("created_at", "updated_at"),
            ("observed_at", "accepted_at"),
        ):
            if (
                earlier in row
                and later in row
                and row[earlier] is not None
                and row[later] is not None
            ):
                require(timestamp(row[later]) >= timestamp(row[earlier]))
        if table == "conversation_sessions":
            require((row["status"] == "open") == (row["ended_at"] is None))
        if table == "reflection_runs":
            require(row["schema_version"] == row["policy_version"])
            require((row["purpose"] == "personality_evolution") == (row["schema_version"] == 3))
            integer(row["attempt_count"], 0, 2)
            require(
                (row["status"] in {"completed", "exhausted"}) == (row["completed_at"] is not None)
            )
        if table == "memory_lifecycle_decisions":
            same = row["from_status"] == row["to_status"]
            require(
                row["result_version"] == integer(row["expected_version"], 1) + (0 if same else 1)
            )
            require(row["approved_by"] == "local_operator")
            reason = (
                "episode_lifecycle_noop"
                if same
                else (
                    "episode_archived" if row["to_status"] == "archived" else "episode_reactivated"
                )
            )
            require(row["reason_code"] == reason)
        if table == "episode_formation_decisions":
            require((row["kind"] == "created") == (row["memory_id"] is not None))
            require((row["kind"] == "created") == (row["reason_code"] == "source_grounded_episode"))
        if table == "satori_position_revisions":
            require((row["decision_id"] is None) != (row["reflection_outcome_id"] is None))
        if table == "personality_revisions":
            require(
                row["after_aggregate_version"] == integer(row["before_aggregate_version"], 1) + 1
            )
            evolution = row["revision_kind"] == "evolution"
            require(evolution == (row["reflection_outcome_id"] is not None))
            fields = (
                "trait_key",
                "direction",
                "before_value",
                "after_value",
                "applied_delta",
                "decision_confidence",
                "rolling_trait_path",
                "lifetime_trait_path",
            )
            require(all((row[key] is not None) == evolution for key in fields))
            if evolution:
                require(row["trait_key"] in CANONICAL_TRAIT_KEYS)
                require(row["direction"] in {"increase", "decrease"})
                require(
                    row["applied_delta"] == (0.005 if row["direction"] == "increase" else -0.005)
                )
        if table == "conversation_interactions":
            self._interaction_shape(row)
        if table == "affective_transitions":
            from satori.domain.affect_history import validate_affective_numeric_record

            validate_affective_numeric_record(row)
        if table in {"source_inputs", "source_payload_receipts"}:
            require(row["proof_status"] in {"complete", "unknown", "overflow"})
        if table in {
            "satori_personality_traits",
            "personality_checkpoint_traits",
            "personality_evidence",
        }:
            require(row["trait_key"] in CANONICAL_TRAIT_KEYS)
        if table in {"user_model_claims", "world_model_claims"}:
            require(
                row["status"] in {"current", "superseded", "disputed", "retracted", "expired"},
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
            require(
                row["epistemic_kind"] in {"explicit_fact", "inference", "hypothesis"},
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        if table == "user_model_claims":
            from satori.domain.models import USER_MODEL_PREDICATES_V1

            definition = USER_MODEL_PREDICATES_V1.get(str(row["predicate"]))
            require(definition is not None, RetentionReason.SCALAR_CODE_UNSUPPORTED)
            assert definition is not None
            require(
                row["value_kind"] in {item.value for item in definition.allowed_value_kinds},
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        if table == "semantic_claims":
            from satori.domain.semantic_memory import SEMANTIC_PREDICATES_V1

            semantic_definition = SEMANTIC_PREDICATES_V1.get(str(row["predicate"]))
            require(semantic_definition is not None, RetentionReason.SCALAR_CODE_UNSUPPORTED)
            assert semantic_definition is not None
            require(
                row["value_kind"]
                in {item.value for item in semantic_definition.allowed_value_kinds},
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        if table == "satori_positions":
            from satori.infrastructure.seeds.loader import CANONICAL_V1_VALUES

            require((row["kind"] == "opinion") == (row["value_key"] is not None))
            require(
                row["value_key"] is None or row["value_key"] in CANONICAL_V1_VALUES,
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )
        if table == "personality_evidence":
            digest(row["normalized_signature"])
        if "body_state" in row and table != "satori_position_evidence":
            state = row["body_state"]
            require(state in {"available", "erased"}, RetentionReason.SCALAR_CODE_UNSUPPORTED)
            require((row["body_action_id"] is not None) == (state == "erased"))
            columns = _BODY_FIELDS.get(table, frozenset())
            if state == "erased":
                require(all(row[column] is None for column in columns))
        if table in {"personality_checkpoint_approvals", "personality_restore_events"}:
            require(row["reason_state"] in {"available", "erased"})
            require((row["reason_action_id"] is not None) == (row["reason_state"] == "erased"))
            require((row["reason"] is None) == (row["reason_state"] == "erased"))
        if table == "affective_transitions":
            require(row["explanation_state"] in {"available", "erased"})
            require(
                (row["explanation_action_id"] is not None) == (row["explanation_state"] == "erased")
            )
            require((row["reason_codes"] is None) == (row["explanation_state"] == "erased"))
        if table == "satori_values":
            from satori.infrastructure.seeds.loader import CANONICAL_V1_VALUES

            require(row["origin"] == "initial_seed" and row["value_key"] in CANONICAL_V1_VALUES)
        if table == "source_owner_repairs":
            require(row["owner"] in _SOURCE_OWNERS)
        if table == "alembic_version":
            require(
                row["version_num"] == "0024_checkpoint_reasons",
                RetentionReason.ROW_VERSION_UNSUPPORTED,
            )

    def _interaction_shape(self, row: Mapping[str, object]) -> None:
        completed = row["status"] == "completed"
        for key in (
            "provider",
            "model",
            "finish_status",
            "context_schema_version",
            "context_manifest_schema_version",
            "policy_id",
            "policy_schema_version",
        ):
            require(
                (row[key] is not None) == completed
                or (row["status"] == "failed" and key in {"provider", "model"})
            )
        require((row["completed_at"] is not None) == completed)
        require((row["failure_kind"] is not None) == (row["status"] == "failed"))
        if row["failure_reason"] is not None:
            require(
                row["failure_reason"] in {item.value for item in ConversationProviderFailureReason}
            )
        for key in (
            "model_context_status",
            "position_context_status",
            "inclination_context_status",
        ):
            if row[key] is not None:
                require(
                    row[key] in {"not_requested", "empty", "available"},
                    RetentionReason.SCALAR_CODE_UNSUPPORTED,
                )
        for key, allowed in (
            (
                "retrieval_status",
                {"not_requested", "retrieved", "no_relevant_memory", "unavailable"},
            ),
            ("semantic_retrieval_status", {"not_requested", "retrieved", "no_result"}),
            (
                "emotion_appraisal_status",
                {"not_requested", "applied", "skipped", "rejected", "unavailable"},
            ),
        ):
            if row[key] is not None:
                require(row[key] in allowed, RetentionReason.SCALAR_CODE_UNSUPPORTED)
        failure = row["failure_kind"]
        if failure is not None:
            require(
                isinstance(failure, str) and failure.isidentifier(),
                RetentionReason.SCALAR_CODE_UNSUPPORTED,
            )

    def _relationship_document(self, value: object, *, snapshot: bool) -> None:
        vector = {
            "familiarity",
            "trust",
            "comfort",
            "closeness",
            "intellectual_respect",
            "affection",
        }
        if not snapshot:
            for item in object_fields(value, vector).values():
                number(item)
            return
        fields = self._relationship_snapshot_fields()
        data = object_fields(value, fields)
        for item in object_fields(data["vector"], vector).values():
            number(item, 0, 1)
        for key in fields - {"vector"}:
            if key.endswith("version"):
                integer(data[key], 1)
                require(
                    key == "state_version" or data[key] == 1,
                    RetentionReason.ROW_VERSION_UNSUPPORTED,
                )
            elif key.endswith("count"):
                integer(data[key])
            elif key == "updated_at":
                timestamp(data[key])
            else:
                text(data[key])
