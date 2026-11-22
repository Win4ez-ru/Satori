"""Exact appraisal explanation pair and retained numerical/input metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.erasure_inventory import identifiers, references

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from satori.domain.erasure_appraisal import AppraisalBodyAction, AppraisalBodyTarget
    from satori.infrastructure.persistence.erasure_inventory import Row, Store
    from satori.infrastructure.persistence.reflection_material import ReadBudget


@dataclass(slots=True)
class _LocalReadBudget:
    rows: int = 0
    logical_bytes: int = 0

    def add(self, *, rows: int, logical_bytes: int = 0) -> None:
        self.rows += rows
        self.logical_bytes += logical_bytes
        if self.rows > 100_000 or self.logical_bytes > 64 * 1024 * 1024:
            raise SourcePolicyError("body_application_capacity_exceeded")


_AUDIT_KEYS = frozenset(
    {
        "transition_id",
        "interaction_id",
        "source_message_id",
        "base_state_version",
        "resulting_state_version",
        "base_mood_version",
        "resulting_mood_version",
        "emotion_policy_version",
        "appraisal_schema_version",
        "mood_policy_version",
        "source_refs",
        "decision_reason_code",
        "appraisal_reason_codes",
    }
)


def _document(value: object) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=unique) if isinstance(value, str) else value
    except (ValueError, TypeError, RecursionError):
        raise SourcePolicyError("appraisal_body_target_unproven") from None


def _strings(value: object, *, maximum: int, limit: int) -> tuple[str, ...]:
    parsed = _document(value)
    if (
        not isinstance(parsed, list)
        or not 1 <= len(parsed) <= limit
        or any(type(item) is not str or not item.strip() or len(item) > maximum for item in parsed)
        or len(set(parsed)) != len(parsed)
    ):
        raise SourcePolicyError("appraisal_body_target_unproven")
    return tuple(parsed)


def appraisal_input_dependencies(
    source: Row,
) -> tuple[tuple[str, ...], tuple[tuple[str, str, int], ...]]:
    """Decode immutable input metadata strictly, including duplicate JSON keys."""
    roots = identifiers(json.dumps(_document(source.get("root_message_ids"))))
    parents = references(json.dumps(_document(source.get("payload_refs"))))
    if (
        roots is None
        or parents is None
        or len(set(roots)) != len(roots)
        or len(set(parents)) != len(parents)
    ):
        raise SourcePolicyError("appraisal_body_provenance_unproven")
    return roots, parents


@dataclass(slots=True)
class AppraisalReceiptIndex:
    """Immutable actual-input graph metadata; mutable mirrors are not authority."""

    receipts: dict[tuple[str, str, int], Row]
    headers: dict[str, Row]
    cached: dict[str, str] = field(default_factory=dict)
    work: int = 0

    @classmethod
    def build(cls, store: Store) -> AppraisalReceiptIndex:
        receipts: dict[tuple[str, str, int], Row] = {}
        for row in store.get("source_payload_receipts", ()):
            kind, key, version = row.get("kind"), row.get("payload_id"), row.get("payload_version")
            if (
                type(kind) is not str
                or type(key) is not str
                or type(version) is not int
                or version < 1
            ):
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            ref = kind, key, version
            if ref in receipts:
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            receipts[ref] = row
        sessions = {str(row["session_id"]): row for row in store.get("conversation_sessions", ())}
        interactions = {
            str(row["interaction_id"]): row for row in store.get("conversation_interactions", ())
        }
        headers: dict[str, Row] = {}
        for message in store.get("conversation_messages", ()):
            interaction = interactions.get(str(message.get("interaction_id")))
            session = sessions.get(str(message.get("session_id")))
            if (
                interaction is None
                or session is None
                or (
                    message.get("role") != "user"
                    or message.get("sequence") != 1
                    or interaction.get("status") != "completed"
                    or interaction.get("session_id") != session.get("session_id")
                    or session.get("scope_verified") != 1
                    or any(
                        type(row.get("schema_version")) is not int or row["schema_version"] != 1
                        for row in (message, interaction, session)
                    )
                )
            ):
                continue
            key = str(message["message_id"])
            if key in headers:
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            headers[key] = {
                "message_id": key,
                "interaction_id": message["interaction_id"],
                "session_id": message["session_id"],
                "identity_id": session["identity_id"],
                "counterparty_id": session["counterparty_id"],
                "message_schema_version": 1,
                "interaction_schema_version": 1,
                "session_schema_version": 1,
                "role": "user",
                "sequence": 1,
                "status": "completed",
                "scope_verified": 1,
            }
        return cls(receipts, headers)

    def reachable(self, source: Row) -> tuple[tuple[tuple[str, str, int], ...], tuple[str, ...]]:
        roots, parents = appraisal_input_dependencies(source)
        if source.get("proof_status") != "complete" or source.get("legacy") not in (False, 0):
            raise SourcePolicyError("appraisal_body_provenance_unproven")
        reached: set[tuple[str, str, int]] = set()
        active: set[tuple[str, str, int]] = set()
        all_roots = set(roots)
        stack = [(ref, False) for ref in parents]
        while stack:
            ref, leaving = stack.pop()
            if leaving:
                active.remove(ref)
                reached.add(ref)
                continue
            if ref in reached:
                continue
            if ref in active:
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            self.work += 1
            if self.work > 1_000_000:
                raise SourcePolicyError("body_application_capacity_exceeded")
            row = self.receipts.get(ref)
            if (
                row is None
                or row.get("identity_id") != source.get("identity_id")
                or (row.get("proof_status") != "complete" or row.get("legacy") not in (False, 0))
            ):
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            direct, prior = appraisal_input_dependencies(row)
            all_roots.update(direct)
            active.add(ref)
            stack.append((ref, True))
            stack.extend((parent, False) for parent in reversed(prior))
        return tuple(sorted(reached)), tuple(sorted(all_roots))

    def digest(self, source: Row) -> str:
        key = str(source["input_id"])
        if key in self.cached:
            return self.cached[key]
        refs, roots = self.reachable(source)
        headers: list[Row] = []
        for root in roots:
            header = self.headers.get(root)
            if header is None or header["identity_id"] != source.get("identity_id"):
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            headers.append(header)
        metadata = [
            {
                name: row[name]
                for name in (
                    "kind",
                    "payload_id",
                    "payload_version",
                    "identity_id",
                    "proof_status",
                    "legacy",
                )
            }
            | {
                "root_message_ids": list(appraisal_input_dependencies(row)[0]),
                "payload_refs": [
                    {"kind": kind, "payload_id": item, "version": version}
                    for kind, item, version in appraisal_input_dependencies(row)[1]
                ],
            }
            for ref in refs
            for row in (self.receipts[ref],)
        ]
        payload = {
            "input_id": key,
            "identity_id": source["identity_id"],
            "payloads": metadata,
            "canonical_headers": headers,
        }
        try:
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
        except (ValueError, TypeError, RecursionError):
            raise SourcePolicyError("appraisal_body_provenance_unproven") from None
        self.cached[key] = digest
        return digest


@dataclass(slots=True)
class AppraisalMaterialIndex:
    """One census index; each selected transition binds exactly three retained rows."""

    transitions: dict[str, Row]
    inputs: dict[str, Row]
    audits: dict[str, Row]
    audit_duplicates: dict[str, tuple[Row, ...]]
    ambiguous_audits: frozenset[tuple[object, object]]
    receipt_index: AppraisalReceiptIndex

    @classmethod
    def build(cls, store: Store) -> AppraisalMaterialIndex:
        def indexed(table: str, key: str) -> dict[str, Row]:
            rows = store.get(table, ())
            result = {str(row[key]): row for row in rows}
            if len(result) != len(rows):
                raise SourcePolicyError("appraisal_body_target_unproven")
            return result

        transitions = indexed("affective_transitions", "transition_id")
        owned_ids: dict[tuple[str, object], set[str]] = {}
        for key, transition in transitions.items():
            for field_name in ("transition_id", "interaction_id", "source_message_id"):
                owned_ids.setdefault((field_name, transition.get(field_name)), set()).add(key)
        duplicates: dict[str, list[Row]] = {}
        ambiguous: set[tuple[object, object]] = set()
        for row in store.get("audit_events", ()):
            if row.get("event_type") != "emotion.transition_applied":
                continue
            try:
                details = _document(row.get("details"))
            except SourcePolicyError:
                details = None
            if isinstance(details, dict) and type(details.get("transition_id")) is str:
                targets: set[str] = set()
                for field_name in ("transition_id", "interaction_id", "source_message_id"):
                    value = details.get(field_name)
                    if type(value) is str:
                        targets.update(owned_ids.get((field_name, value), ()))
                for key in targets:
                    duplicates.setdefault(key, []).append(row)
            else:
                ambiguous.add((row.get("aggregate_id"), row.get("trace_id")))
        return cls(
            transitions,
            indexed("source_inputs", "input_id"),
            indexed("audit_events", "event_id"),
            {key: tuple(rows) for key, rows in duplicates.items()},
            frozenset(ambiguous),
            AppraisalReceiptIndex.build(store),
        )

    def input_closure_digest(self, transition_id: str) -> str:
        transition = self.transitions.get(transition_id)
        source = self.inputs.get(str(transition.get("source_input_id"))) if transition else None
        if source is None:
            raise SourcePolicyError("appraisal_body_provenance_unproven")
        return self.receipt_index.digest(source)

    def material(self, transition_id: str, *, terminal: bool) -> dict[str, object]:
        from satori.domain.affect_history import validate_affective_numeric_record

        row = self.transitions.get(transition_id)
        if row is None:
            raise SourcePolicyError("appraisal_body_target_unproven")
        try:
            validate_affective_numeric_record(row)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise SourcePolicyError("appraisal_body_target_unproven") from None
        state = row.get("explanation_state")
        if state not in {"available", "erased"} or (
            (state == "available") != (row.get("explanation_action_id") is None)
        ):
            raise SourcePolicyError("appraisal_body_target_unproven")
        source = self.inputs.get(str(row.get("source_input_id")))
        audit = self.audits.get(str(row.get("audit_event_id")))
        if source is None or audit is None:
            raise SourcePolicyError("appraisal_body_provenance_unproven")
        roots, _parents = appraisal_input_dependencies(source)
        generation = source.get("policy_generation")
        if (
            source.get("identity_id") != row.get("identity_id")
            or source.get("proof_status") != "complete"
            or source.get("legacy") not in (False, 0)
            or row.get("source_message_id") not in roots
            or type(generation) is not int
            or generation < 0
        ):
            raise SourcePolicyError("appraisal_body_provenance_unproven")
        paired = self.audit_duplicates.get(transition_id, ())
        if (
            len(paired) != 1
            or paired[0].get("event_id") != audit.get("event_id")
            or (row.get("identity_id"), row.get("trace_id")) in self.ambiguous_audits
        ):
            raise SourcePolicyError("appraisal_audit_pair_unproven")
        details = _document(audit.get("details"))
        expected_keys = (
            _AUDIT_KEYS if state == "available" else _AUDIT_KEYS - {"appraisal_reason_codes"}
        )
        if not isinstance(details, dict) or set(details) != expected_keys:
            raise SourcePolicyError("appraisal_audit_pair_unproven")
        if (
            audit.get("schema_version") != 1
            or audit.get("event_type") != "emotion.transition_applied"
            or audit.get("aggregate_type") != "affective_state"
            or audit.get("aggregate_id") != row.get("identity_id")
            or audit.get("trace_id") != row.get("trace_id")
            or audit.get("occurred_at") != row.get("committed_at")
            or details.get("decision_reason_code") != "bounded_appraisal_applied"
        ):
            raise SourcePolicyError("appraisal_audit_pair_unproven")
        for name in _AUDIT_KEYS - {"decision_reason_code", "appraisal_reason_codes", "source_refs"}:
            if type(details[name]) is not type(row.get(name)) or details[name] != row.get(name):
                raise SourcePolicyError("appraisal_audit_pair_unproven")
        refs = _strings(row.get("source_refs"), maximum=128, limit=512)
        if details["source_refs"] != list(refs):
            raise SourcePolicyError("appraisal_audit_pair_unproven")
        if state == "available":
            reasons = _strings(row.get("reason_codes"), maximum=64, limit=8)
            if details["appraisal_reason_codes"] != list(reasons):
                raise SourcePolicyError("appraisal_audit_pair_unproven")
        elif row.get("reason_codes") is not None:
            raise SourcePolicyError("appraisal_body_target_unproven")
        transition = dict(row)
        transition["explanation_action_id"] = None
        audit_material = dict(audit)
        canonical_details = dict(details)
        audit_material["details"] = canonical_details
        if terminal:
            transition["explanation_state"] = "erased"
            transition["reason_codes"] = None
            canonical_details.pop("appraisal_reason_codes", None)
        return {"transition": transition, "source_input": dict(source), "audit": audit_material}

    def digest(self, transition_id: str, *, terminal: bool = False) -> str:
        try:
            payload = json.dumps(
                self.material(transition_id, terminal=terminal),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (ValueError, TypeError, RecursionError):
            raise SourcePolicyError("appraisal_body_target_unproven") from None
        return hashlib.sha256(payload).hexdigest()

    def metadata_reference_count(self, transition_id: str) -> int:
        row = self.transitions.get(transition_id)
        if (
            row is None
            or row.get("source_input_id") not in self.inputs
            or (row.get("audit_event_id") not in self.audits)
        ):
            raise SourcePolicyError("appraisal_body_target_unproven")
        return 3


def appraisal_target_terminal_proven(
    index: AppraisalMaterialIndex, action: AppraisalBodyAction, target: AppraisalBodyTarget
) -> bool:
    row = index.transitions.get(target.transition_id)
    if (
        row is None
        or row.get("identity_id") != action.proposal.root.identity_id
        or row.get("source_input_id") != target.source_input_id
        or row.get("audit_event_id") != target.audit_event_id
        or row.get("explanation_state") != "erased"
        or row.get("explanation_action_id") != action.action_id
        or row.get("reason_codes") is not None
        or target.payload_version != 1
    ):
        return False
    try:
        return (
            index.metadata_reference_count(target.transition_id) == target.metadata_reference_count
            and index.digest(target.transition_id) == target.terminal_digest
            and index.input_closure_digest(target.transition_id) == target.input_closure_digest
        )
    except (SourcePolicyError, ValueError, TypeError, KeyError):
        return False


def read_appraisal_material(
    session: Session,
    transition_ids: tuple[str, ...],
    *,
    require_terminal: bool = True,
    budget: ReadBudget | None = None,
    expected_input_closure_digests: Mapping[str, str] | None = None,
) -> AppraisalMaterialIndex:
    """NULL explanations and absent audit copies precede ordinary protected row reads."""
    ids = tuple(sorted(set(transition_ids)))
    if len(ids) > 100_000:
        raise SourcePolicyError("body_application_capacity_exceeded")
    budget = budget if budget is not None else _LocalReadBudget()
    connection = session.connection()
    input_ids: set[str] = set()
    header_inputs: dict[str, str] = {}
    audit_ids: set[str] = set()
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        marks = ",".join("?" for _ in chunk)
        predicate = "t.transition_id IN (" + marks + ")"
        if require_terminal:
            predicate += (
                " AND t.explanation_state='erased' AND t.explanation_action_id IS NOT NULL"
                " AND t.reason_codes IS NULL AND json_valid(a.details)"
                " AND json_type(a.details)='object'"
                " AND json_type(a.details,'$.appraisal_reason_codes') IS NULL"
            )
            allowed_keys = ",".join(
                "'" + key + "'" for key in sorted(_AUDIT_KEYS - {"appraisal_reason_codes"})
            )
            predicate += (
                " AND (SELECT count(*) FROM json_each(a.details))=12"
                " AND (SELECT count(DISTINCT key) FROM json_each(a.details))=12"
                " AND NOT EXISTS (SELECT 1 FROM json_each(a.details) WHERE key NOT IN ("
                + allowed_keys
                + "))"
            )
        headers = connection.exec_driver_sql(
            "SELECT t.transition_id,t.source_input_id,t.audit_event_id "
            "FROM affective_transitions t JOIN source_inputs i ON i.input_id=t.source_input_id "
            "JOIN audit_events a ON a.event_id=t.audit_event_id "
            "JOIN conversation_messages m ON m.message_id=t.source_message_id "
            "JOIN conversation_interactions c ON c.interaction_id=t.interaction_id "
            "JOIN conversation_sessions s ON s.session_id=c.session_id WHERE "
            + predicate
            + " AND m.interaction_id=c.interaction_id AND m.session_id=s.session_id"
            " AND m.role='user' AND m.sequence=1 AND c.status='completed'"
            " AND m.schema_version=1 AND c.schema_version=1 AND s.schema_version=1"
            " AND s.scope_verified=1 AND s.identity_id=t.identity_id"
            " AND i.identity_id=t.identity_id"
            " AND i.proof_status='complete' AND i.legacy=0",
            chunk,
        ).all()
        if len(headers) != len(chunk):
            raise SourcePolicyError("body_reconciliation_required")
        input_ids.update(str(row[1]) for row in headers)
        header_inputs.update((str(row[0]), str(row[1])) for row in headers)
        audit_ids.update(str(row[2]) for row in headers)
        duplicates = connection.exec_driver_sql(
            "SELECT t.transition_id,count(a.event_id) FROM affective_transitions t "
            "JOIN audit_events a ON a.event_type='emotion.transition_applied' AND "
            "(CASE WHEN json_valid(a.details) THEN json_extract(a.details,'$.transition_id') "
            "ELSE NULL END=t.transition_id OR "
            "CASE WHEN json_valid(a.details) THEN json_extract(a.details,'$.interaction_id') "
            "ELSE NULL END=t.interaction_id OR "
            "CASE WHEN json_valid(a.details) THEN json_extract(a.details,'$.source_message_id') "
            "ELSE NULL END=t.source_message_id) WHERE t.transition_id IN ("
            + marks
            + ") GROUP BY t.transition_id",
            chunk,
        ).all()
        if len(duplicates) != len(chunk) or any(count != 1 for _key, count in duplicates):
            raise SourcePolicyError("body_reconciliation_required")
        ambiguous = connection.exec_driver_sql(
            "SELECT count(*) FROM affective_transitions t JOIN audit_events a "
            "ON a.event_type='emotion.transition_applied' AND a.aggregate_id=t.identity_id "
            "AND a.trace_id=t.trace_id WHERE t.transition_id IN (" + marks + ") AND "
            "CASE WHEN json_valid(a.details) THEN json_type(a.details,'$.transition_id') "
            "ELSE NULL END IS NOT 'text'",
            chunk,
        ).scalar_one()
        if ambiguous:
            raise SourcePolicyError("body_reconciliation_required")
    store = _read_receipt_metadata(connection, tuple(sorted(input_ids)), budget=budget)
    receipt_index = AppraisalReceiptIndex.build(store)
    source_by_id = {str(row["input_id"]): row for row in store["source_inputs"]}
    for transition_id, input_id in header_inputs.items():
        closure_digest = receipt_index.digest(source_by_id[input_id])
        if (
            expected_input_closure_digests is not None
            and closure_digest != expected_input_closure_digests.get(transition_id)
        ):
            raise SourcePolicyError("body_reconciliation_required")
    total_rows = total_bytes = 0
    inspection = inspect(connection)
    for table, key, selected in (
        ("affective_transitions", "transition_id", ids),
        ("source_inputs", "input_id", tuple(sorted(input_ids))),
        ("audit_events", "event_id", tuple(sorted(audit_ids))),
    ):
        rows: list[Row] = []
        columns = tuple(str(column["name"]) for column in inspection.get_columns(table))
        lengths = "+".join(f'coalesce(length(CAST("{column}" AS BLOB)),0)' for column in columns)
        for offset in range(0, len(selected), 500):
            chunk = selected[offset : offset + 500]
            marks = ",".join("?" for _ in chunk)
            predicate = f'"{key}" IN ({marks})'
            if require_terminal and table == "affective_transitions":
                predicate += (
                    " AND explanation_state='erased' AND explanation_action_id IS NOT NULL"
                    " AND reason_codes IS NULL"
                )
            if require_terminal and table == "audit_events":
                predicate += (
                    " AND json_valid(details) AND json_type(details)='object'"
                    " AND json_type(details,'$.appraisal_reason_codes') IS NULL"
                )
            count, size = connection.exec_driver_sql(
                f'SELECT count(*),coalesce(sum({lengths}),0) FROM "{table}" WHERE {predicate}',
                chunk,
            ).one()
            total_rows += int(count)
            total_bytes += int(size)
            if budget is not None:
                budget.add(rows=int(count), logical_bytes=int(size))
            if total_rows > 100_000 or total_bytes > 64 * 1024 * 1024:
                raise SourcePolicyError("body_application_capacity_exceeded")
            if int(count) != len(chunk):
                raise SourcePolicyError("body_reconciliation_required")
            rows.extend(
                dict(row)
                for row in connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" WHERE {predicate}', chunk
                ).mappings()
            )
        store[table] = tuple(rows)
    index = AppraisalMaterialIndex.build(store)
    for transition_id in ids:
        index.material(transition_id, terminal=False)
    return index


def _read_receipt_metadata(
    connection: Connection, input_ids: tuple[str, ...], *, budget: ReadBudget | None
) -> Store:
    """Read only ancestry and canonical headers before any paired protected material."""
    input_rows: list[Row] = []
    for offset in range(0, len(input_ids), 500):
        chunk = input_ids[offset : offset + 500]
        lengths = "+".join(
            f'coalesce(length(CAST("{name}" AS BLOB)),0)'
            for name in (
                "input_id",
                "identity_id",
                "proof_status",
                "root_message_ids",
                "payload_refs",
            )
        )
        count, size = connection.exec_driver_sql(
            "SELECT count(*),coalesce(sum("
            + lengths
            + "),0) FROM source_inputs WHERE input_id IN ("
            + ",".join("?" for _ in chunk)
            + ")",
            chunk,
        ).one()
        if int(count) != len(chunk):
            raise SourcePolicyError("body_reconciliation_required")
        if budget is not None:
            budget.add(rows=int(count), logical_bytes=int(size))
        input_rows.extend(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT input_id,identity_id,policy_generation,proof_status,legacy,"
                "root_message_ids,payload_refs FROM source_inputs WHERE input_id IN ("
                + ",".join("?" for _ in chunk)
                + ")",
                chunk,
            ).mappings()
        )
    identities = {str(row["identity_id"]) for row in input_rows}
    if len(input_rows) != len(input_ids) or len(identities) != 1:
        raise SourcePolicyError("body_reconciliation_required")
    store: Store = {"source_inputs": tuple(input_rows)}
    columns = (
        "kind,payload_id,payload_version,identity_id,proof_status,legacy,"
        "root_message_ids,payload_refs"
    )
    lengths = "+".join(
        f'coalesce(length(CAST("{column}" AS BLOB)),0)' for column in columns.split(",")
    )
    # One identity census, not one store scan per target. Mutable mirror columns are absent.
    identity = next(iter(identities))
    present = "source_payload_receipts" in inspect(connection).get_table_names()
    if present:
        count, size = connection.exec_driver_sql(
            f"SELECT count(*),coalesce(sum({lengths}),0) FROM source_payload_receipts "
            "WHERE identity_id=?",
            (identity,),
        ).one()
        if int(count) > 100_000 or int(size) > 64 * 1024 * 1024:
            raise SourcePolicyError("body_application_capacity_exceeded")
        if budget is not None:
            budget.add(rows=int(count), logical_bytes=int(size))
        store["source_payload_receipts"] = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT " + columns + " FROM source_payload_receipts WHERE identity_id=?",
                (identity,),
            ).mappings()
        )
    else:
        store["source_payload_receipts"] = ()
    index = AppraisalReceiptIndex.build(store)
    roots = tuple(sorted({root for row in input_rows for root in index.reachable(row)[1]}))
    messages: dict[str, Row] = {}
    interactions: dict[str, Row] = {}
    sessions: dict[str, Row] = {}
    for offset in range(0, len(roots), 500):
        chunk = roots[offset : offset + 500]
        query = (
            "SELECT m.message_id,m.interaction_id,m.session_id,"
            "m.schema_version AS message_schema,m.role,m.sequence,"
            "i.schema_version AS interaction_schema,i.status,i.session_id AS interaction_session,"
            "s.schema_version AS session_schema,s.identity_id,s.counterparty_id,s.scope_verified "
            "FROM conversation_messages m JOIN conversation_interactions i "
            "ON i.interaction_id=m.interaction_id JOIN conversation_sessions s "
            "ON s.session_id=m.session_id WHERE m.message_id IN ("
            + ",".join("?" for _ in chunk)
            + ")"
        )
        for row in connection.exec_driver_sql(query, chunk).mappings():
            messages[str(row["message_id"])] = {
                "message_id": row["message_id"],
                "interaction_id": row["interaction_id"],
                "session_id": row["session_id"],
                "schema_version": row["message_schema"],
                "role": row["role"],
                "sequence": row["sequence"],
            }
            interactions[str(row["interaction_id"])] = {
                "interaction_id": row["interaction_id"],
                "session_id": row["interaction_session"],
                "schema_version": row["interaction_schema"],
                "status": row["status"],
            }
            sessions[str(row["session_id"])] = {
                "session_id": row["session_id"],
                "schema_version": row["session_schema"],
                "identity_id": row["identity_id"],
                "counterparty_id": row["counterparty_id"],
                "scope_verified": row["scope_verified"],
            }
    for table, rows in (
        ("conversation_messages", messages),
        ("conversation_interactions", interactions),
        ("conversation_sessions", sessions),
    ):
        store[table] = tuple(rows.values())
        size = sum(len(json.dumps(row, sort_keys=True).encode()) for row in rows.values())
        if len(rows) > 100_000 or size > 64 * 1024 * 1024:
            raise SourcePolicyError("body_application_capacity_exceeded")
        if budget is not None:
            budget.add(rows=len(rows), logical_bytes=size)
    return store
