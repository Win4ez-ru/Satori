"""Bounded dependency assessment; accepted roots never certify actual input closure."""

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from pydantic import TypeAdapter

from satori.core.reflection import (
    ReflectionCandidate,
    ReflectionInclinationCandidate,
    ReflectionLineageKind,
    ReflectionPersonalityCandidate,
    ReflectionPurpose,
    ReflectionSourceKind,
)
from satori.core.semantic import SemanticScalar, SemanticValueKind
from satori.domain.erasure_bodies import BodyErasureAction
from satori.domain.erasure_dialogue import HistoricalEvidenceTable
from satori.domain.memory_scope import MemoryScope, scope_semantic_key
from satori.domain.reflection import (
    ReflectionSourceRecord,
    affective_signal_hash,
    content_hash,
    proposal_payload,
    reflection_run_id,
    reflection_run_key,
    reflection_source_id,
    source_set_hash,
)
from satori.domain.semantic_memory import normalize_semantic_value
from satori.infrastructure.persistence.canonical_provenance import HistoricalProvenance
from satori.infrastructure.persistence.erasure_body_codec import decode_body_action

type Row = dict[str, object]
type Store = dict[str, tuple[Row, ...]]
type Ref = tuple[str, str, int]

_REFLECTION_CANDIDATE: TypeAdapter[ReflectionCandidate] = TypeAdapter(ReflectionCandidate)

PAYLOAD_TABLES = {
    "assistant_reply": ("conversation_interactions", "interaction_id", None),
    "episode": ("episodic_memories", "memory_id", "aggregate_version"),
    "semantic_claim": ("semantic_claims", "claim_id", "aggregate_version"),
    "user_model_claim": ("user_model_claims", "claim_id", "aggregate_version"),
    "world_model_claim": ("world_model_claims", "claim_id", "aggregate_version"),
    "position": ("satori_positions", "position_id", "aggregate_version"),
    "inclination": ("satori_inclinations", "inclination_id", "aggregate_version"),
    "reflection_proposal": ("reflection_proposals", "proposal_id", None),
}


def document(value: object) -> object:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError, RecursionError):
        return None


def identifiers(value: object) -> tuple[str, ...] | None:
    parsed = document(value)
    if not isinstance(parsed, list) or len(parsed) > 512:
        return None
    if any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in parsed):
        return None
    return tuple(parsed)


def references(value: object) -> tuple[Ref, ...] | None:
    parsed = document(value)
    if not isinstance(parsed, list) or len(parsed) > 128:
        return None
    result: list[Ref] = []
    for item in parsed:
        if not isinstance(item, dict) or set(item) != {"kind", "payload_id", "version"}:
            return None
        kind, payload_id, version = item["kind"], item["payload_id"], item["version"]
        if (
            not isinstance(kind, str)
            or kind not in PAYLOAD_TABLES
            or not isinstance(payload_id, str)
            or not payload_id.strip()
            or len(payload_id) > 128
            or type(version) is not int
            or version < 1
        ):
            return None
        result.append((kind, payload_id, version))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class DependencyAssessment:
    affected: frozenset[Ref]
    uncertain: frozenset[Ref]
    blockers: frozenset[str]


class ProvenOwners:
    """Cross-check receipt owner claims against physical canonical owner/root chains."""

    def __init__(
        self, store: Store, identity_id: str, *, historical: HistoricalProvenance | None = None
    ) -> None:
        self.store = store
        self.identity_id = identity_id
        self.historical = historical
        self.messages = self.index("conversation_messages", "message_id")
        self.interactions = self.index("conversation_interactions", "interaction_id")
        self.sessions = self.index("conversation_sessions", "session_id")
        self.runs = self.index("reflection_runs", "run_id")
        self.memory_edges = self.index("memory_evidence", "evidence_id")
        self.position_edges = self.index("satori_position_evidence", "evidence_id")
        self.affective_transitions = self.index("affective_transitions", "transition_id")
        self.canonical_users = {
            str(row["interaction_id"]): str(row["message_id"])
            for row in store["conversation_messages"]
            if self.canonical(str(row["message_id"]))
        }
        self._owner_cache: dict[tuple[str, str], bool] = {}
        self._strict_episode_cache: dict[str, bool] = {}
        self.targets = {
            kind: self.index(table, column)
            for kind, (table, column, _version) in PAYLOAD_TABLES.items()
        }
        reply_interactions = {
            str(row["interaction_id"])
            for row in store["conversation_messages"]
            if row["role"] == "assistant"
        }
        self.targets["assistant_reply"] = {
            key: value
            for key, value in self.targets["assistant_reply"].items()
            if key in reply_interactions
        }
        self.evidence: dict[tuple[str, str], list[Row]] = {}
        for kind, table, key in (
            ("episode", "memory_evidence", "memory_id"),
            ("semantic_claim", "semantic_claim_evidence", "claim_id"),
            ("user_model_claim", "user_model_claim_evidence", "claim_id"),
            ("world_model_claim", "world_model_claim_evidence", "claim_id"),
            ("position", "satori_position_evidence", "position_id"),
            ("inclination", "satori_inclination_evidence", "inclination_id"),
            ("reflection_proposal", "reflection_sources", "run_id"),
        ):
            for row in store[table]:
                self.evidence.setdefault((kind, str(row[key])), []).append(row)

    def index(self, table: str, column: str) -> dict[str, Row]:
        return {str(row[column]): row for row in self.store[table]}

    def canonical(self, message_id: str, *, completed: bool = True) -> bool:
        message = self.messages.get(message_id)
        if message is None or message["role"] != "user" or message["sequence"] != 1:
            return False
        if message.get("body_state") == "erased":
            if self.historical is None or not self.historical.admits_root(message_id):
                return False
        elif (
            message.get("body_state", "available") != "available"
            or message.get("body_action_id") is not None
            or not isinstance(message.get("content"), str)
            or not str(message["content"]).strip()
        ):
            return False
        interaction = self.interactions.get(str(message["interaction_id"]))
        scoped = self.sessions.get(str(message["session_id"]))
        return bool(
            interaction is not None
            and scoped is not None
            and interaction["session_id"] == scoped["session_id"]
            and scoped["identity_id"] == self.identity_id
            and scoped["scope_verified"] == 1
            and (not completed or interaction["status"] == "completed")
        )

    def root_person(self, message_id: str) -> object:
        message = self.messages[message_id]
        return self.sessions[str(message["session_id"])]["counterparty_id"]

    def admits(self, ref: Ref) -> bool:
        kind, payload_id, version = ref
        target = self.targets[kind].get(payload_id)
        if target is None:
            return False
        version_column = PAYLOAD_TABLES[kind][2]
        if version_column is None:
            if version != 1:
                return False
        else:
            head_version = target[version_column]
            if type(head_version) is not int or head_version < version:
                return False
        key = (kind, payload_id)
        if key not in self._owner_cache:
            self._owner_cache[key] = self._admits_owner(kind, payload_id, target)
        return self._owner_cache[key]

    def _admits_owner(self, kind: str, payload_id: str, target: Row) -> bool:
        source: Row | None = None
        terminal_knowledge = (
            kind
            in {
                "semantic_claim",
                "user_model_claim",
                "world_model_claim",
                "position",
                "inclination",
            }
            and target.get("body_state") == "erased"
        )
        if (
            kind == "reflection_proposal"
            and target.get("body_state") == "erased"
            and (self.historical is None or not self.historical.admits_terminal_owner(kind, target))
        ):
            return False
        if terminal_knowledge and (
            self.historical is None or not self.historical.admits_terminal_owner(kind, target)
        ):
            return False
        if "identity_id" in target and target["identity_id"] != self.identity_id:
            return False
        if kind == "assistant_reply":
            return target["status"] == "completed" and payload_id in self.canonical_users
        if kind == "episode":
            source = self.interactions.get(str(target["source_interaction_id"]))
            if source is None or str(source["interaction_id"]) not in self.canonical_users:
                return False
        key = str(target["run_id"]) if kind == "reflection_proposal" else payload_id
        edges = self.evidence.get((kind, key), ())
        if kind == "reflection_proposal":
            run = self.runs.get(key)
            if run is None or run["identity_id"] != self.identity_id:
                return False
        if not edges:
            return False
        people: set[object] = set()
        for edge in edges:
            root_key = (
                "root_message_id"
                if kind in {"semantic_claim", "reflection_proposal"}
                else "source_message_id"
            )
            root = str(edge[root_key])
            if not self.canonical(root):
                return False
            message = self.messages[root]
            person = self.root_person(root)
            people.add(person)
            interaction_key = (
                "root_interaction_id"
                if kind in {"semantic_claim", "reflection_proposal"}
                else "source_interaction_id"
            )
            if interaction_key in edge and edge[interaction_key] != message["interaction_id"]:
                return False
            if (
                kind in {"user_model_claim", "world_model_claim"}
                and person != target["counterparty_id"]
            ):
                return False
            if "source_counterparty_id" in edge and person != edge["source_counterparty_id"]:
                return False
            if "root_counterparty_id" in edge and person != edge["root_counterparty_id"]:
                return False
            if "source_session_id" in edge and message["session_id"] != edge["source_session_id"]:
                return False
            if kind == "episode":
                assert source is not None
                if (
                    edge["provenance_kind"] != "explicit_user_statement"
                    or message["session_id"] != source["session_id"]
                ):
                    return False
            if kind == "semantic_claim":
                memory = self.targets["episode"].get(str(edge["memory_id"]))
                evidence = self.memory_edges.get(str(edge["memory_evidence_id"]))
                if (
                    memory is None
                    or evidence is None
                    or evidence["memory_id"] != memory["memory_id"]
                    or evidence["source_message_id"] != root
                    or memory["source_interaction_id"] != message["interaction_id"]
                    or not self._strict_episode(str(memory["memory_id"]))
                ):
                    return False
            if message.get("body_state") == "erased":
                if kind == "episode" and edge.get("body_state") == "erased":
                    if not self._erased_episode_edge(payload_id, target, edge):
                        return False
                    continue
                witnessed_table = {
                    "episode": HistoricalEvidenceTable.MEMORY,
                    "user_model_claim": HistoricalEvidenceTable.USER_MODEL,
                    "world_model_claim": HistoricalEvidenceTable.WORLD_MODEL,
                    "position": HistoricalEvidenceTable.POSITION,
                    "inclination": HistoricalEvidenceTable.INCLINATION,
                }.get(kind)
                if witnessed_table is not None and (
                    self.historical is None
                    or not self.historical.admits_evidence(witnessed_table, edge)
                ):
                    return False
                # This admits retained attribution only. Ordinary source/payload
                # admission still withholds every affected text-bearing aggregate.
                continue
            if "quote" in edge:
                quote, content = edge["quote"], message["content"]
                if kind == "episode" and edge.get("body_state") == "erased":
                    # A terminal header proves retained attribution, never an available quote.
                    if not self._erased_episode_edge(payload_id, target, edge):
                        return False
                    continue
                if kind == "position" and quote is None:
                    if (
                        not terminal_knowledge
                        or self.historical is None
                        or not self.historical.admits_evidence(
                            HistoricalEvidenceTable.POSITION, edge
                        )
                    ):
                        return False
                    continue
                if (
                    not isinstance(quote, str)
                    or not quote
                    or not isinstance(content, str)
                    or quote not in content
                ):
                    return False
        if kind == "semantic_claim":
            if len(people) != 1:
                return False
            if terminal_knowledge:
                # The approved owner target binds the original namespace and full
                # retained skeleton. NULL content cannot undergo live normalization.
                return True
            try:
                value_kind = SemanticValueKind(str(target["value_kind"]))
                raw_value = target["value"]
                # SQLite's JSON column affinity can materialize numeric JSON as a
                # native INTEGER/REAL. Other logical documents still require JSON.
                value = raw_value if type(raw_value) in (int, float) else document(raw_value)
                normalized = normalize_semantic_value(value_kind, cast(SemanticScalar, value))
                material = {
                    "claim_kind": target["claim_kind"],
                    "normalization_version": target["normalization_version"],
                    "polarity": bool(target["polarity"]),
                    "predicate": target["predicate"],
                    "subject": target["subject"],
                    "value": normalized,
                    "value_kind": value_kind.value,
                }
                base = hashlib.sha256(
                    json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                return normalized == target["normalized_value"] and target[
                    "claim_key"
                ] == scope_semantic_key(
                    base, MemoryScope(self.identity_id, str(next(iter(people))))
                )
            except (ValueError, TypeError, AssertionError):
                return False
        return True

    def _erased_episode_edge(self, memory_id: str, memory: Row, edge: Row) -> bool:
        if (
            memory.get("body_state") != "erased"
            or memory["summary"] is not None
            or edge["quote"] is not None
            or memory.get("body_action_id") != edge.get("body_action_id")
        ):
            return False
        projection = next(
            (
                row
                for row in self.store.get("erasure_body_applications", ())
                if row["action_id"] == edge.get("body_action_id")
            ),
            None,
        )
        if projection is None:
            return False
        material = document(projection["action"])
        if not isinstance(material, dict):
            return False
        try:
            action = decode_body_action(material)
            return (
                isinstance(action, BodyErasureAction)
                and action.proposal.root.identity_id == self.identity_id
                and action.action_id == edge["body_action_id"]
                and any(
                    target.memory_id == memory_id
                    and target.aggregate_version == memory["aggregate_version"]
                    for target in action.proposal.targets
                )
            )
        except (ValueError, TypeError, KeyError):
            return False

    def reflection_proven(self, proposal_id: str) -> bool:
        """Stricter destructive-selection proof; old legacy attribution is unchanged."""
        target = self.targets["reflection_proposal"].get(proposal_id)
        if target is None or not self.admits(("reflection_proposal", proposal_id, 1)):
            return False
        run_id = str(target["run_id"])
        run = self.runs.get(run_id)
        if run is None or run.get("status") not in {"completed", "exhausted"}:
            return False
        edges = self.evidence.get(("reflection_proposal", run_id), ())
        try:
            records: list[ReflectionSourceRecord] = []
            for edge in edges:
                ordinal = int(str(edge["ordinal"]))
                if edge["source_id"] != reflection_source_id(run_id=run_id, ordinal=ordinal):
                    return False
                kind = ReflectionSourceKind(str(edge["kind"]))
                if edge.get("evidence_edge_version") != 1:
                    return False
                table_edges = (
                    self.memory_edges
                    if kind is ReflectionSourceKind.EPISODIC_MEMORY_EVIDENCE
                    else self.position_edges
                )
                upstream = table_edges.get(str(edge["evidence_edge_id"]))
                if upstream is None or upstream.get("source_message_id") != edge["root_message_id"]:
                    return False
                upstream_kind = (
                    "episode"
                    if kind is ReflectionSourceKind.EPISODIC_MEMORY_EVIDENCE
                    else "position"
                )
                owner_id = str(
                    upstream["memory_id" if upstream_kind == "episode" else "position_id"]
                )
                if not self.admits((upstream_kind, owner_id, 1)):
                    return False
                quote = upstream.get("quote")
                if isinstance(quote, str):
                    if content_hash(quote) != edge["content_hash"]:
                        return False
                elif not (upstream_kind == "episode" and self._strict_episode(owner_id)) and not (
                    upstream_kind == "position"
                    and self.historical is not None
                    and self.historical.admits_evidence(HistoricalEvidenceTable.POSITION, upstream)
                ):
                    return False
                if (
                    upstream_kind == "position"
                    and upstream.get("source_interaction_id") != edge["root_interaction_id"]
                ):
                    return False
                lineage = edge.get("upstream_lineage_kind")
                if lineage is not None and (
                    lineage != ("episodic_memory" if upstream_kind == "episode" else "position")
                    or edge.get("upstream_lineage_id") != owner_id
                ):
                    return False
                record = ReflectionSourceRecord(
                    source_id=str(edge["source_id"]),
                    run_id=run_id,
                    ordinal=ordinal,
                    kind=kind,
                    evidence_edge_id=str(edge["evidence_edge_id"]),
                    evidence_edge_version=1,
                    root_interaction_id=str(edge["root_interaction_id"]),
                    root_message_id=str(edge["root_message_id"]),
                    root_counterparty_id=str(edge["root_counterparty_id"]),
                    observed_at=datetime.fromisoformat(str(edge["observed_at"])),
                    content_hash=str(edge["content_hash"]),
                    affective_transition_id=cast(str | None, edge.get("affective_transition_id")),
                    affective_state_version=cast(int | None, edge.get("affective_state_version")),
                    affective_signal_hash=cast(str | None, edge.get("affective_signal_hash")),
                    upstream_lineage_kind=ReflectionLineageKind(str(lineage))
                    if lineage is not None
                    else None,
                    upstream_lineage_id=cast(str | None, edge.get("upstream_lineage_id")),
                )
                if run["schema_version"] == 1 and record.affective_transition_id is not None:
                    return False
                if record.affective_transition_id is not None:
                    transition = self.affective_transitions.get(record.affective_transition_id)
                    if (
                        transition is None
                        or transition.get("identity_id") != self.identity_id
                        or transition.get("interaction_id") != record.root_interaction_id
                        or transition.get("source_message_id") != record.root_message_id
                        or transition.get("resulting_state_version")
                        != record.affective_state_version
                    ):
                        return False
                    appraisal = document(transition.get("appraisal_payload"))
                    delta = document(transition.get("applied_delta"))
                    if not isinstance(appraisal, dict) or not isinstance(delta, dict):
                        return False
                    signal = affective_signal_hash(
                        transition_id=record.affective_transition_id,
                        identity_id=self.identity_id,
                        interaction_id=record.root_interaction_id,
                        source_message_id=record.root_message_id,
                        resulting_state_version=int(str(transition["resulting_state_version"])),
                        source=record,
                        appraisal_schema_version=int(str(transition["appraisal_schema_version"])),
                        appraisal_payload=appraisal,
                        appraisal_confidence=float(str(transition["appraisal_confidence"])),
                        applied_delta=delta,
                    )
                    if signal != record.affective_signal_hash:
                        return False
                records.append(record)
            purpose = ReflectionPurpose(str(run["purpose"]))
            source_hash = source_set_hash(
                tuple(records), schema_version=int(str(run["schema_version"])), purpose=purpose
            )
            key = reflection_run_key(
                identity_id=self.identity_id,
                source_hash=source_hash,
                schema_version=int(str(run["schema_version"])),
                policy_version=int(str(run["policy_version"])),
                purpose=purpose,
            )
            if (
                run["source_set_hash"] != source_hash
                or run["run_key"] != key
                or run_id != reflection_run_id(key)
            ):
                return False
            cited = identifiers(target["evidence_source_ids"])
            if (
                cited is None
                or not cited
                or len(set(cited)) != len(cited)
                or not set(cited) <= {r.source_id for r in records}
            ):
                return False
            if target.get("body_state") == "erased":
                return True  # Exact frozen terminal material was proved by the trusted action.
            payload = document(target["payload"])
            if (
                not isinstance(payload, dict)
                or payload.get("target_owner") != target["target_owner"]
            ):
                return False
            candidate = _REFLECTION_CANDIDATE.validate_json(str(target["payload"]))
            if proposal_payload(candidate) != payload:
                return False
            if (run["schema_version"] == 3) != isinstance(
                candidate, ReflectionPersonalityCandidate
            ):
                return False
            if run["schema_version"] == 1 and isinstance(candidate, ReflectionInclinationCandidate):
                return False
            payload_citations = payload.get("evidence", payload.get("citations"))
            if isinstance(payload_citations, list):
                payload_ids = tuple(
                    item.get("source_id") for item in payload_citations if isinstance(item, dict)
                )
                if len(payload_ids) != len(payload_citations):
                    return False
            else:
                raw_ids = payload.get("source_ids", payload.get("evidence_source_ids"))
                if not isinstance(raw_ids, list):
                    return False
                payload_ids = tuple(raw_ids)
            if len(payload_ids) != len(set(payload_ids)) or set(payload_ids) != set(cited):
                return False
            encoded = json.dumps(
                {"run_id": run_id, "ordinal": target["ordinal"], "payload": payload},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
            return proposal_id == "reflection-proposal-" + hashlib.sha256(encoded).hexdigest()[:40]
        except (ValueError, TypeError, KeyError, RecursionError):
            return False

    def _strict_episode(self, memory_id: str) -> bool:
        if memory_id not in self._strict_episode_cache:
            memory = self.targets["episode"].get(memory_id)
            self._strict_episode_cache[memory_id] = bool(
                memory is not None
                and self.admits(("episode", memory_id, 1))
                and all(
                    self.messages[str(edge["source_message_id"])]["interaction_id"]
                    == memory["source_interaction_id"]
                    for edge in self.evidence[("episode", memory_id)]
                )
            )
        return self._strict_episode_cache[memory_id]


def assess_dependencies(
    store: Store,
    identity_id: str,
    message_id: str,
    *,
    historical: HistoricalProvenance | None = None,
) -> DependencyAssessment:
    owners = ProvenOwners(store, identity_id, historical=historical)
    nodes: dict[Ref, Row] = {}
    affected: set[Ref] = set()
    uncertain: set[Ref] = set()
    blockers: set[str] = set()
    parents: dict[Ref, tuple[Ref, ...]] = {}
    children: dict[Ref, set[Ref]] = {}
    physical_support: set[tuple[str, str]] = set()
    for kind, table, root_key, owner_key in (
        ("episode", "memory_evidence", "source_message_id", "memory_id"),
        ("semantic_claim", "semantic_claim_evidence", "root_message_id", "claim_id"),
        ("user_model_claim", "user_model_claim_evidence", "source_message_id", "claim_id"),
        ("world_model_claim", "world_model_claim_evidence", "source_message_id", "claim_id"),
        ("position", "satori_position_evidence", "source_message_id", "position_id"),
        ("inclination", "satori_inclination_evidence", "source_message_id", "inclination_id"),
    ):
        physical_support.update(
            (kind, str(row[owner_key])) for row in store[table] if row[root_key] == message_id
        )
    interaction_id = str(owners.messages[message_id]["interaction_id"])
    physical_support.add(("assistant_reply", interaction_id))
    physical_support.update(
        ("episode", str(row["memory_id"]))
        for row in store["episodic_memories"]
        if row["source_interaction_id"] == interaction_id
    )
    runs = {
        str(row["run_id"])
        for row in store["reflection_sources"]
        if row["root_message_id"] == message_id
    }
    physical_support.update(
        ("reflection_proposal", str(row["proposal_id"]))
        for row in store["reflection_proposals"]
        if str(row["run_id"]) in runs
    )
    for row in store["source_payload_receipts"]:
        raw_kind, payload_id, version = row["kind"], row["payload_id"], row["payload_version"]
        if (
            not isinstance(raw_kind, str)
            or raw_kind not in PAYLOAD_TABLES
            or not isinstance(payload_id, str)
            or type(version) is not int
        ):
            blockers.add("unregistered_payload_owner")
            continue
        ref = (raw_kind, payload_id, version)
        nodes[ref] = row
        if row["identity_id"] != identity_id or not owners.admits(ref):
            uncertain.add(ref)
            blockers.add("payload_owner_unproven")
        roots, dependencies = identifiers(row["root_message_ids"]), references(row["payload_refs"])
        if roots is None or dependencies is None:
            uncertain.add(ref)
            blockers.add("dependency_shape_unproven")
            parents[ref] = ()
            continue
        if any(not owners.canonical(root, completed=False) for root in roots):
            uncertain.add(ref)
            blockers.add("dependency_root_unproven")
        if message_id in roots or (raw_kind, payload_id) in physical_support:
            affected.add(ref)
        if row["proof_status"] != "complete":
            uncertain.add(ref)
            blockers.add("unknown_or_overflow_dependency")
        parents[ref] = dependencies
        for parent in dependencies:
            children.setdefault(parent, set()).add(ref)
    for ref, dependencies in parents.items():
        if any(parent not in nodes for parent in dependencies):
            uncertain.add(ref)
            blockers.add("dependency_version_missing")
    remaining = {
        ref: len(set(parent for parent in parents[ref] if parent in nodes)) for ref in nodes
    }
    queue = deque(ref for ref, count in remaining.items() if count == 0)
    visited: set[Ref] = set()
    while queue:
        ref = queue.popleft()
        visited.add(ref)
        for child in children.get(ref, ()):
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    cycles = set(nodes) - visited
    if cycles:
        uncertain.update(cycles)
        blockers.add("dependency_cycle_unproven")
    for reached in (affected, uncertain):
        queue = deque(reached)
        while queue:
            for child in children.get(queue.popleft(), ()):
                if child not in reached:
                    reached.add(child)
                    queue.append(child)
    for kind, targets in owners.targets.items():
        for payload_id, target in targets.items():
            column = PAYLOAD_TABLES[kind][2]
            version = target[column] if column else 1
            if type(version) is not int or version < 1:
                blockers.add("payload_owner_unproven")
                continue
            ref = (kind, payload_id, version)
            if ref not in nodes:
                uncertain.add(ref)
                blockers.add("payload_receipt_missing")
                if (kind, payload_id) in physical_support:
                    affected.add(ref)
    for row in store["source_inputs"]:
        roots, dependencies = identifiers(row["root_message_ids"]), references(row["payload_refs"])
        if row["identity_id"] != identity_id or roots is None or dependencies is None:
            blockers.add("source_input_dependency_unproven")
            continue
        if row["proof_status"] != "complete":
            blockers.add("unknown_or_overflow_source_input")
        if any(not owners.canonical(root, completed=False) for root in roots):
            blockers.add("source_input_root_unproven")
        if any(ref not in nodes or ref in uncertain for ref in dependencies):
            blockers.add("source_input_dependency_unproven")
    return DependencyAssessment(frozenset(affected), frozenset(uncertain), frozenset(blockers))
