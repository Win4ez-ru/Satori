"""Pure admission/freshness contracts; no claim of production whole-store proof."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from pydantic import ValidationError

from satori.application.erasure.retention_contracts import (
    RetentionCheckRequest,
    RetentionStatusRequest,
)
from satori.application.erasure.retention_use_cases import RetentionPlanner
from satori.domain.erasure_preparation import ErasurePreparationOwner
from satori.domain.retention import (
    RetentionInventory,
    RetentionReason,
    RetentionReasonCount,
    RetentionTableCounts,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    ProtectedContentFootprint,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS


class Admission:
    def __init__(self) -> None:
        self.current = SourceEligibilitySnapshot(
            "floor", "installation", "lineage", "identity", 0, frozenset()
        )
        self.shared_calls = 0

    @contextmanager
    def shared(self) -> Iterator[SourceEligibilitySnapshot]:
        self.shared_calls += 1
        yield self.current

    @contextmanager
    def exclusive(self) -> Iterator[SourceEligibilitySnapshot]:
        raise AssertionError("read_control_opened_writer")
        yield self.current

    def snapshot(
        self, identity_id: str | None = None, *, verify_mirror: bool = True
    ) -> SourceEligibilitySnapshot:
        return self.current

    def assert_binding(self, identity_id: str, floor_id: str, lineage_id: str) -> None:
        assert (identity_id, floor_id, lineage_id) == (
            self.current.identity_id,
            self.current.floor_id,
            self.current.lineage_id,
        )

    def check_root(self, root: CanonicalRootRef, purpose: str = "read") -> None:
        raise AssertionError("whole_store_invented_canonical_root")

    def check_footprint(self, footprint: ProtectedContentFootprint, purpose: str = "read") -> None:
        raise AssertionError("retention_granted_content_eligibility")


def inventory(
    snapshot: SourceEligibilitySnapshot, *, unknown: bool = False, content: bool = False
) -> RetentionInventory:
    tables = tuple(
        RetentionTableCounts(
            owner=ErasurePreparationOwner(spec.owner),
            table=table,
            total_rows=int(table == "alembic_version"),
            supported_retained_rows=int(table == "alembic_version" and not unknown and not content),
            owned_content_rows=int(table == "alembic_version" and content and not unknown),
            unknown_rows=int(table == "alembic_version" and unknown),
        )
        for table, spec in sorted(ROUTE_SPECS.items())
    )
    return RetentionInventory(
        identity_id=snapshot.identity_id,
        lineage_id=snapshot.lineage_id,
        source_generation=snapshot.generation,
        erasure_revision=snapshot.erasure_revision,
        body_revision=snapshot.body_revision,
        body_head_digest=snapshot.body_head_digest,
        schema_revision="0024_checkpoint_reasons",
        schema_digest="a" * 64,
        store_digest="b" * 64,
        classification_digest="c" * 64,
        table_counts=tables,
        reason_counts=(RetentionReasonCount(RetentionReason.SCALAR_CODE_UNSUPPORTED, 1),)
        if unknown
        else (),
        scanned_rows=1,
        work_units=60,
    )


class FixedInventory:
    def __init__(self, value: RetentionInventory) -> None:
        self.value = value
        self.calls = 0

    def inspect(
        self, request: RetentionStatusRequest, snapshot: SourceEligibilitySnapshot
    ) -> RetentionInventory:
        self.calls += 1
        return self.value


def check_request(planner: RetentionPlanner) -> RetentionCheckRequest:
    status = planner.status(RetentionStatusRequest(identity_id="identity"))
    return RetentionCheckRequest(
        identity_id=status.identity_id,
        expected_generation=status.generation,
        expected_erasure_revision=status.erasure_revision,
        expected_body_revision=status.body_revision,
        review_digest=status.review_digest,
    )


@pytest.mark.parametrize(
    ("unknown", "content", "coverage", "readiness"),
    [
        (False, False, "complete", "retained_only"),
        (False, True, "complete", "owned_content_remaining"),
        (True, False, "partial", "blocked_unknown"),
    ],
)
def test_classification_and_remaining_content_never_become_execution_permission(
    unknown: bool, content: bool, coverage: str, readiness: str
) -> None:
    runtime = Admission()
    port = FixedInventory(inventory(runtime.current, unknown=unknown, content=content))
    planner = RetentionPlanner(runtime, port)
    current = planner.status(RetentionStatusRequest(identity_id="identity"))
    checked = planner.check(check_request(planner))
    assert current.coverage == checked.coverage == coverage
    assert current.logical_readiness == checked.logical_readiness == readiness
    assert (
        current.total_rows
        == current.supported_retained_rows + current.owned_content_rows + current.unknown_rows
    )
    assert len({item.owner for item in current.table_counts}) == 15
    assert current.physical_erasure is checked.physical_erasure is False
    assert current.execution_authorized is checked.execution_authorized is False
    assert current.automatic_processing is False
    assert current.external_retention == "not_assessed"
    with pytest.raises(ValidationError):
        current.execution_authorized = True  # type: ignore[assignment]


def test_foreign_identity_denies_before_any_inventory_disclosure() -> None:
    runtime = Admission()
    port = FixedInventory(inventory(runtime.current))
    with pytest.raises(SourcePolicyError, match="retention_identity_unavailable"):
        RetentionPlanner(runtime, port).status(RetentionStatusRequest(identity_id="foreign"))
    assert port.calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_id", "foreign"),
        ("lineage_id", "other-lineage"),
        ("source_generation", 1),
        ("erasure_revision", 1),
    ],
)
def test_inventory_cannot_substitute_scope_or_heads(field: str, value: object) -> None:
    runtime = Admission()
    current = inventory(runtime.current)
    if field == "identity_id":
        assert isinstance(value, str)
        current = replace(current, identity_id=value)
    elif field == "lineage_id":
        assert isinstance(value, str)
        current = replace(current, lineage_id=value)
    elif field == "source_generation":
        assert type(value) is int
        current = replace(current, source_generation=value)
    else:
        assert field == "erasure_revision"
        assert type(value) is int
        current = replace(current, erasure_revision=value)
    port = FixedInventory(current)
    with pytest.raises(SourcePolicyError, match="retention_inventory_binding_mismatch"):
        RetentionPlanner(runtime, port).status(RetentionStatusRequest(identity_id="identity"))


@pytest.mark.parametrize("field", ["store_digest", "classification_digest", "schema_digest"])
def test_equal_counts_do_not_hide_same_generation_material_or_classification_drift(
    field: str,
) -> None:
    runtime = Admission()
    port = FixedInventory(inventory(runtime.current))
    planner = RetentionPlanner(runtime, port)
    old = check_request(planner)
    if field == "store_digest":
        port.value = replace(port.value, store_digest="d" * 64)
    elif field == "classification_digest":
        port.value = replace(port.value, classification_digest="d" * 64)
    else:
        assert field == "schema_digest"
        port.value = replace(port.value, schema_digest="d" * 64)
    with pytest.raises(SourcePolicyError, match="retention_review_stale"):
        planner.check(old)


def test_body_head_and_source_policy_version_are_separately_bound() -> None:
    runtime = Admission()
    runtime.current = replace(runtime.current, body_revision=1, body_head_digest="a" * 64)
    port = FixedInventory(inventory(runtime.current))
    planner = RetentionPlanner(runtime, port)
    old = check_request(planner)
    runtime.current = replace(runtime.current, body_head_digest="b" * 64)
    port.value = inventory(runtime.current)
    with pytest.raises(SourcePolicyError, match="retention_review_stale"):
        planner.check(old)
    old = check_request(planner)
    runtime.current = replace(runtime.current, policy_version="future_policy")
    with pytest.raises(SourcePolicyError, match="retention_review_stale"):
        planner.check(old)


@pytest.mark.parametrize(
    "field", ["expected_generation", "expected_erasure_revision", "expected_body_revision"]
)
def test_stale_expected_counter_is_not_a_current_review(field: str) -> None:
    runtime = Admission()
    planner = RetentionPlanner(runtime, FixedInventory(inventory(runtime.current)))
    old = check_request(planner)
    wrong = RetentionCheckRequest(**{**old.model_dump(), field: 1})
    with pytest.raises(SourcePolicyError, match="retention_head_conflict"):
        planner.check(wrong)


def test_numeric_facets_do_not_add_a_second_row_to_the_partition() -> None:
    row = RetentionTableCounts(
        ErasurePreparationOwner.AFFECT, "affective_transitions", 1, 0, 1, 0, numeric_history_rows=1
    )
    assert row.supported_retained_rows + row.owned_content_rows + row.unknown_rows == 1
    assert row.numeric_history_rows == 1
    with pytest.raises(ValueError, match="retention_row_partition_incomplete"):
        replace(row, supported_retained_rows=1)
    with pytest.raises(ValueError, match="retention_terminal_rows_not_proved"):
        replace(row, terminal_body_rows=1)
    with pytest.raises(ValueError, match="invalid_retention_count"):
        replace(row, total_rows=True)
