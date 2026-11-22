"""Historical attribution cannot become reconstructed text or fresh evidence authority."""

import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from satori.domain.erasure_bodies import (
    BodyAction,
    BodyErasureAction,
    BodyErasureProposal,
    BodyErasureTarget,
    body_actions_digest,
)
from satori.domain.erasure_dialogue import (
    DialogueBodyAction,
    DialogueBodyProposal,
    DialogueBodyTarget,
    HistoricalEvidenceTable,
    HistoricalEvidenceWitness,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.canonical_provenance import (
    Row,
    Store,
    canonical_root_available,
    canonical_row_digest,
    capture_historical_witnesses,
    dialogue_message_digest,
    historical_from_approved_actions,
    historical_from_store,
    validate_historical_witnesses,
)
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.erasure_inventory import PAYLOAD_TABLES, ProvenOwners
from satori.infrastructure.source_policy.installation import _available_canonical_message_ids

_ROOT = CanonicalRootRef("lineage", "satori", "alice", "session", "interaction", "message")
_QUOTE = "private project"
_CONTENT = "The private project belongs to Alice."


def _store() -> Store:
    store: Store = {table: () for table, _key, _version in PAYLOAD_TABLES.values()}
    store.update(
        {
            "affective_transitions": (),
            "reflection_runs": (),
            "semantic_claim_evidence": (),
            "reflection_sources": (),
            "erasure_body_applications": (),
            "conversation_sessions": (
                {
                    "session_id": "session",
                    "identity_id": "satori",
                    "counterparty_id": "alice",
                    "scope_verified": 1,
                },
            ),
            "conversation_interactions": (
                {"interaction_id": "interaction", "session_id": "session", "status": "completed"},
            ),
            "conversation_messages": (
                {
                    "message_id": "message",
                    "interaction_id": "interaction",
                    "session_id": "session",
                    "schema_version": 1,
                    "role": "user",
                    "sequence": 1,
                    "created_at": "2026-10-06 00:00:00.000000",
                    "content": _CONTENT,
                    "body_state": "available",
                    "body_action_id": None,
                },
            ),
            "episodic_memories": (
                {
                    "memory_id": "memory",
                    "source_interaction_id": "interaction",
                    "aggregate_version": 1,
                    "summary": "A project",
                    "body_state": "available",
                },
            ),
            "user_model_claims": (
                {
                    "claim_id": "user-claim",
                    "identity_id": "satori",
                    "counterparty_id": "alice",
                    "aggregate_version": 1,
                },
            ),
            "world_model_claims": (
                {
                    "claim_id": "world-claim",
                    "identity_id": "satori",
                    "counterparty_id": "alice",
                    "aggregate_version": 1,
                },
            ),
            "satori_positions": (
                {"position_id": "position", "identity_id": "satori", "aggregate_version": 1},
            ),
            "satori_inclinations": (
                {"inclination_id": "inclination", "identity_id": "satori", "aggregate_version": 1},
            ),
        }
    )
    common: Row = {"source_message_id": "message", "observed_at": "2026-10-06 00:00:00.000000"}
    store["memory_evidence"] = (
        dict(
            common,
            evidence_id="memory-edge",
            memory_id="memory",
            quote=_QUOTE,
            provenance_kind="explicit_user_statement",
            body_state="available",
            body_action_id=None,
        ),
    )
    for table, claim in (
        ("user_model_claim_evidence", "user-claim"),
        ("world_model_claim_evidence", "world-claim"),
    ):
        store[table] = (
            dict(common, evidence_id=table, claim_id=claim, source_interaction_id="interaction"),
        )
    store["satori_position_evidence"] = (
        dict(
            common,
            evidence_id="position-edge",
            position_id="position",
            quote=_QUOTE,
            source_interaction_id="interaction",
            source_counterparty_id="alice",
            normalized_signature="signature",
            role="argument",
        ),
    )
    content_hash = hashlib.sha256(_QUOTE.encode()).hexdigest()
    store["reflection_sources"] = (
        {
            "source_id": "reflection-source",
            "run_id": "reflection-run",
            "root_message_id": "message",
            "root_interaction_id": "interaction",
            "root_counterparty_id": "alice",
            "content_hash": content_hash,
            "kind": "position_evidence",
            "evidence_edge_id": "position-edge",
        },
    )
    store["satori_inclination_evidence"] = (
        dict(
            common,
            evidence_id="inclination-edge",
            inclination_id="inclination",
            source_interaction_id="interaction",
            source_session_id="session",
            source_counterparty_id="alice",
            reflection_source_id="reflection-source",
            content_hash=content_hash,
            content_signature="signature",
            signal=0.25,
        ),
    )
    return store


def _action(store: Store) -> DialogueBodyAction:
    message = store["conversation_messages"][0]
    target = DialogueBodyTarget(
        "message",
        1,
        dialogue_message_digest(message),
        dialogue_message_digest(message, terminal=True),
        capture_historical_witnesses(store, _ROOT),
    )
    return DialogueBodyAction(
        DialogueBodyProposal(
            _ROOT, "intent", "request", 1, 1, 0, "a" * 64, "b" * 64, "c" * 64, (target,)
        ),
        "action",
        "floor",
        "installation",
        1,
        datetime(2026, 10, 6, tzinfo=UTC),
        "trace",
    )


def _snapshot(action: DialogueBodyAction | None = None) -> SourceEligibilitySnapshot:
    actions: tuple[BodyAction, ...] = () if action is None else (action,)
    return SourceEligibilitySnapshot(
        "floor",
        "installation",
        "lineage",
        "satori",
        1,
        frozenset({"message"}),
        erasure_revision=1,
        body_revision=len(actions),
        body_head_digest=body_actions_digest(actions) if actions else "",
    )


def _terminal(store: Store, action: DialogueBodyAction) -> None:
    store["conversation_messages"] = (
        dict(
            store["conversation_messages"][0],
            content=None,
            body_state="erased",
            body_action_id="action",
        ),
    )
    store["erasure_body_applications"] = (
        {
            "action_id": "action",
            "request_id": "request",
            "identity_id": "satori",
            "root_message_id": "message",
            "floor_id": "floor",
            "lineage_id": "lineage",
            "body_revision": 1,
            "action": json.dumps(encode_body_action(action)),
        },
    )


def test_witnesses_capture_all_closed_routes_without_copied_content() -> None:
    witnesses = capture_historical_witnesses(_store(), _ROOT)
    assert {witness.table for witness in witnesses} == set(HistoricalEvidenceTable)
    encoded = json.dumps([asdict(witness) for witness in witnesses])
    assert _QUOTE not in encoded
    assert _CONTENT not in encoded
    assert witnesses == tuple(sorted(witnesses, key=lambda item: (item.table, item.evidence_id)))


def test_null_body_retains_attribution_only_with_exact_trusted_complete_head() -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    historical = historical_from_store(store, _snapshot(action))
    assert historical.admits_root("message")
    assert not ProvenOwners(store, "satori").canonical("message")
    assert ProvenOwners(store, "satori", historical=historical).admits(("position", "position", 1))
    assert store["conversation_messages"][0]["content"] is None


@pytest.mark.parametrize("table", list(HistoricalEvidenceTable))
def test_nonversioned_row_drift_or_new_evidence_does_not_inherit_historical_proof(
    table: HistoricalEvidenceTable,
) -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    historical = historical_from_store(store, _snapshot(action))
    original = store[table.value][0]
    assert historical.admits_evidence(table, original)
    assert not historical.admits_evidence(table, dict(original, observed_at="2026-10-07"))
    assert not historical.admits_evidence(table, dict(original, evidence_id="new-edge"))


def test_forged_receipt_cannot_change_witness_approval_under_the_surviving_floor_head() -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    target = action.proposal.targets[0]
    changed = replace(target, witnesses=(replace(target.witnesses[0], digest="f" * 64),))
    forged = replace(action, proposal=replace(action.proposal, targets=(changed,)))
    store["erasure_body_applications"][0]["action"] = json.dumps(encode_body_action(forged))
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        historical_from_store(store, _snapshot(action))


def test_deeply_nested_corrupt_receipt_is_sanitized_without_changing_retained_state() -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    depth = sys.getrecursionlimit() + 100
    private_marker = "PRIVATE_CORRUPT_RECEIPT_CONTENT"
    store["erasure_body_applications"][0]["action"] = (
        '{"malformed":' + "[" * depth + json.dumps(private_marker) + "]" * depth + "}"
    )
    before = deepcopy(store)

    with pytest.raises(SourcePolicyError, match=r"^dialogue_provenance_unproven$") as failure:
        historical_from_store(store, _snapshot(action))

    assert private_marker not in str(failure.value)
    assert failure.value.__cause__ is None
    assert store == before


def test_terminal_skeleton_drift_and_fake_zero_head_cannot_authorize_a_header() -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    store["conversation_messages"][0]["created_at"] = "2026-10-07 00:00:00.000000"
    assert not historical_from_store(store, _snapshot(action)).admits_root("message")
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        historical_from_store(store, _snapshot())
    assert not historical_from_store(_store(), _snapshot()).admits_root("message")


def _pending_episode_action() -> BodyErasureAction:
    root = replace(_ROOT, message_id="pending-root", interaction_id="pending-interaction")
    return BodyErasureAction(
        BodyErasureProposal(
            root,
            "pending-intent",
            "pending-request",
            1,
            1,
            1,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (BodyErasureTarget("pending-memory", 1, "d" * 64, "e" * 64),),
        ),
        "pending-action",
        "floor",
        "installation",
        2,
        datetime(2026, 10, 6, tzinfo=UTC),
        "trace",
    )


def test_available_roots_need_no_historical_grant_while_body_materialization_is_pending() -> None:
    store = _store()
    historical = historical_from_store(store, _snapshot(_action(store)))
    assert historical.roots == frozenset()
    assert historical.witnesses == {}
    assert not historical.admits_root("message")

    del store["conversation_messages"][0]["body_state"]
    del store["conversation_messages"][0]["body_action_id"]
    assert historical_from_store(store, _snapshot(_action(_store()))).roots == frozenset()


def test_operator_floor_head_proves_prior_terminal_root_with_different_pending_target() -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    actions: tuple[BodyAction, ...] = (action, _pending_episode_action())
    snapshot = replace(
        _snapshot(action),
        body_revision=2,
        body_head_digest=body_actions_digest(actions),
        excluded_message_ids=frozenset({"message", "pending-root"}),
    )
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        historical_from_store(store, snapshot)
    historical = historical_from_approved_actions(store, snapshot, actions)
    assert historical.admits_root("message")
    assert not historical.admits_root("pending-root")
    assert historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][0]
    )
    store["satori_position_evidence"][0]["observed_at"] = "2026-10-07 00:00:00.000000"
    assert not historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][0]
    )


@pytest.mark.parametrize(
    "corruption", ["partial", "reordered", "wrong_floor", "duplicate_action", "duplicate_request"]
)
def test_operator_provenance_requires_exact_complete_unique_approved_head(corruption: str) -> None:
    store = _store()
    action = _action(store)
    _terminal(store, action)
    pending = _pending_episode_action()
    actions: tuple[BodyAction, ...] = (action, pending)
    checked: tuple[BodyAction, ...]
    if corruption == "partial":
        checked = (action,)
    elif corruption == "reordered":
        checked = (pending, action)
    elif corruption == "wrong_floor":
        checked = (action, replace(pending, floor_id="wrong-floor"))
    elif corruption == "duplicate_action":
        checked = (action, replace(pending, action_id=action.action_id))
    else:
        checked = (
            action,
            replace(pending, proposal=replace(pending.proposal, request_id="request")),
        )
    snapshot = replace(
        _snapshot(action),
        body_revision=2,
        body_head_digest=body_actions_digest(actions),
        excluded_message_ids=frozenset({"message", "pending-root"}),
    )
    # Even a hash matching bad ordering/bindings cannot establish an authoritative head.
    if len(checked) == 2:
        snapshot = replace(snapshot, body_head_digest=body_actions_digest(checked))
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        historical_from_approved_actions(store, snapshot, checked)


def test_terminal_operator_root_resolution_uses_trusted_floor_without_acknowledging_pending() -> (
    None
):
    store = _store()
    action = _action(store)
    _terminal(store, action)
    actions: tuple[BodyAction, ...] = (action, _pending_episode_action())
    snapshot = replace(
        _snapshot(action),
        body_revision=2,
        body_head_digest=body_actions_digest(actions),
        excluded_message_ids=frozenset({"message", "pending-root"}),
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            for table in (
                "conversation_messages",
                "conversation_sessions",
                "conversation_interactions",
                "erasure_body_applications",
            ):
                row = store[table][0]
                columns = ",".join(
                    f'"{name}" {"INTEGER" if type(value) is int else "TEXT"}'
                    for name, value in row.items()
                )
                connection.exec_driver_sql(f'CREATE TABLE "{table}" ({columns})')
                marks = ",".join("?" for _ in row)
                connection.exec_driver_sql(
                    f'INSERT INTO "{table}" VALUES ({marks})', tuple(row.values())
                )
        with engine.connect() as connection:
            with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
                canonical_root_available(connection, "message", snapshot)
            assert canonical_root_available(
                connection, "message", snapshot, approved_actions=actions
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM erasure_body_applications"
                ).scalar()
                == 1
            )
            assert (
                connection.exec_driver_sql("SELECT content FROM conversation_messages").scalar()
                is None
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("corruption", ["foreign_quote", "foreign_scope", "foreign_upstream"])
def test_malformed_support_blocks_capture_instead_of_certifying_a_partial_witness_set(
    corruption: str,
) -> None:
    store = _store()
    if corruption == "foreign_quote":
        store["satori_position_evidence"][0]["quote"] = "not in the canonical body"
    elif corruption == "foreign_scope":
        store["user_model_claims"][0]["counterparty_id"] = "bob"
    else:
        store["reflection_sources"][0]["root_counterparty_id"] = "bob"
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        capture_historical_witnesses(store, _ROOT)


def test_witness_limit_rejects_before_a_candidate_subset_can_be_returned() -> None:
    store = _store()
    original = store["memory_evidence"][0]
    store["memory_evidence"] = tuple(
        dict(original, evidence_id=f"edge-{index}") for index in range(513)
    )
    with pytest.raises(SourcePolicyError, match="dialogue_witness_capacity_exceeded"):
        capture_historical_witnesses(store, _ROOT)


class _RejectReadBudget:
    def add(self, *, rows: int, logical_bytes: int = 0) -> None:
        raise SourcePolicyError("test_read_budget_exceeded")


def test_operator_witness_subset_keeps_full_floor_proof_and_cannot_admit_pending_root() -> None:
    store = _store()
    initial = _action(store)
    target = initial.proposal.targets[0]
    witness = next(w for w in target.witnesses if w.table is HistoricalEvidenceTable.POSITION)
    action = replace(
        initial,
        proposal=replace(initial.proposal, targets=(replace(target, witnesses=(witness,)),)),
    )
    _terminal(store, action)
    original = store["satori_position_evidence"][0]
    pending_row = dict(original, evidence_id="pending-edge", source_message_id="pending-root")
    pending_target = replace(
        target,
        message_id="pending-root",
        witnesses=(
            HistoricalEvidenceWitness(
                HistoricalEvidenceTable.POSITION, "pending-edge", canonical_row_digest(pending_row)
            ),
        ),
    )
    pending = replace(
        action,
        action_id="pending-action",
        result_body_revision=2,
        proposal=replace(
            action.proposal,
            root=replace(_ROOT, message_id="pending-root"),
            request_id="pending-request",
            expected_body_revision=1,
            targets=(pending_target,),
        ),
    )
    actions: tuple[BodyAction, ...] = (action, pending)
    snapshot = replace(
        _snapshot(action),
        body_revision=2,
        body_head_digest=body_actions_digest(actions),
        excluded_message_ids=frozenset({"message", "pending-root"}),
    )
    pending_row["observed_at"] = "changed while pending"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            columns = ",".join(f'"{name}" TEXT' for name in original)
            connection.exec_driver_sql(f"CREATE TABLE satori_position_evidence ({columns})")
            marks = ",".join("?" for _ in original)
            connection.exec_driver_sql(
                f"INSERT INTO satori_position_evidence VALUES ({marks})",
                [tuple(original.values()), tuple(pending_row.values())],
            )
        with engine.connect() as connection:
            validate_historical_witnesses(
                connection, actions, snapshot=snapshot, message_ids=frozenset({"message"})
            )
            with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
                validate_historical_witnesses(connection, actions, snapshot=snapshot)
            with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
                validate_historical_witnesses(
                    connection, (action,), snapshot=snapshot, message_ids=frozenset()
                )
        historical = historical_from_approved_actions(store, snapshot, actions)
        assert historical.admits_root("message")
        assert not historical.admits_root("pending-root")
    finally:
        engine.dispose()


def test_admission_checks_size_before_loading_witness_rows_and_rejects_exact_row_drift() -> None:
    store = _store()
    action = _action(store)
    target = action.proposal.targets[0]
    witness = next(
        item for item in target.witnesses if item.table is HistoricalEvidenceTable.POSITION
    )
    action = replace(
        action, proposal=replace(action.proposal, targets=(replace(target, witnesses=(witness,)),))
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def trace(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    try:
        with engine.begin() as connection:
            row = store["satori_position_evidence"][0]
            columns = tuple(row)
            declarations = ",".join(f'"{name}" TEXT' for name in columns)
            connection.exec_driver_sql(f"CREATE TABLE satori_position_evidence ({declarations})")
            marks = ",".join("?" for _ in columns)
            connection.exec_driver_sql(
                f"INSERT INTO satori_position_evidence VALUES ({marks})", tuple(row.values())
            )
        with engine.connect() as connection:
            validate_historical_witnesses(connection, (action,), snapshot=_snapshot(action))
            statements.clear()
            with pytest.raises(SourcePolicyError, match="test_read_budget_exceeded"):
                validate_historical_witnesses(
                    connection, (action,), snapshot=_snapshot(action), budget=_RejectReadBudget()
                )
            assert not any("SELECT *" in statement for statement in statements)
            connection.exec_driver_sql(
                "UPDATE satori_position_evidence SET observed_at='2026-10-07'"
            )
            with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
                validate_historical_witnesses(connection, (action,), snapshot=_snapshot(action))
    finally:
        engine.dispose()


def test_enrollment_compatibility_never_revives_a_null_or_terminal_message() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE conversation_messages (message_id TEXT,content TEXT,"
                "body_state TEXT,body_action_id TEXT)"
            )
            connection.exec_driver_sql(
                "INSERT INTO conversation_messages VALUES (?,?,?,?)",
                [
                    ("live", _CONTENT, "available", None),
                    ("null", None, "available", None),
                    ("terminal", None, "erased", "approved-action"),
                    ("forged-terminal", _CONTENT, "erased", "approved-action"),
                    ("attached", _CONTENT, "available", "approved-action"),
                    ("empty", "  ", "available", None),
                ],
            )
        with Session(engine) as session:
            assert _available_canonical_message_ids(session) == {"live"}
    finally:
        engine.dispose()


def test_live_canonical_proof_reads_a_boolean_header_instead_of_loading_revoked_text() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def trace(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE conversation_sessions (session_id TEXT,identity_id TEXT,"
                "counterparty_id TEXT,scope_verified INTEGER)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE conversation_interactions "
                "(interaction_id TEXT,session_id TEXT,status TEXT)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE conversation_messages (message_id TEXT,interaction_id TEXT,"
                "session_id TEXT,role TEXT,sequence INTEGER,content TEXT,body_state TEXT,"
                "body_action_id TEXT)"
            )
            connection.exec_driver_sql(
                "INSERT INTO conversation_sessions VALUES ('session','satori','alice',1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO conversation_interactions VALUES ('interaction','session','completed')"
            )
            connection.exec_driver_sql(
                "INSERT INTO conversation_messages VALUES "
                "('message','interaction','session','user',1,?,'available',NULL)",
                (_CONTENT,),
            )
        with engine.connect() as connection:
            statements.clear()
            assert canonical_root_available(connection, "message", _snapshot())
            assert not any(
                "SELECT m.*" in statement or "SELECT * FROM conversation_messages" in statement
                for statement in statements
            )
    finally:
        engine.dispose()
