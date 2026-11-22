"""Untrusted inventory metadata cannot swap scope or authorize erasure."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import FrozenInstanceError, replace

import pytest
from pydantic import ValidationError

from satori.application.erasure.contracts import ErasureCheckRequest, ErasurePreviewRequest
from satori.application.erasure.use_cases import ErasurePlanner
from satori.domain.erasure import ErasureDisposition, ErasureInventory, ErasureRoute
from satori.domain.source_policy import (
    CanonicalRootRef,
    ProtectedContentFootprint,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)


def _root() -> CanonicalRootRef:
    return CanonicalRootRef("lineage", "satori", "alice", "session", "turn", "user-source")


def _route() -> ErasureRoute:
    return ErasureRoute(
        owner="memory",
        table="memory_evidence",
        disposition=ErasureDisposition.REDACT_CONTENT,
        rows=2,
        content_columns=("quote",),
        uncertain_rows=1,
    )


def _inventory() -> ErasureInventory:
    return ErasureInventory(
        root=_root(),
        schema_revision="0016_source_policy",
        schema_digest="a" * 64,
        store_digest="b" * 64,
        routing_digest="c" * 64,
        routes=(_route(),),
        scanned_rows=4,
        affected_payload_versions=1,
        uncertain_payload_versions=0,
    )


class FakeInventoryPort:
    def __init__(self, result: ErasureInventory) -> None:
        self.result = result

    def inspect(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
    ) -> ErasureInventory:
        return self.result


class FakeSourceAdmission:
    def __init__(self, snapshot: SourceEligibilitySnapshot) -> None:
        self.current = snapshot
        self.lease_held = False
        self.binding_checks = 0

    @contextmanager
    def shared(self) -> Iterator[SourceEligibilitySnapshot]:
        assert not self.lease_held
        self.lease_held = True
        try:
            yield self.current
        finally:
            self.lease_held = False

    def exclusive(self) -> AbstractContextManager[SourceEligibilitySnapshot]:
        raise AssertionError("read_only_planner_requested_exclusive_lease")

    def snapshot(
        self, identity_id: str | None = None, *, verify_mirror: bool = True
    ) -> SourceEligibilitySnapshot:
        assert self.lease_held
        return self.current

    def assert_binding(self, identity_id: str, floor_id: str, lineage_id: str) -> None:
        assert self.lease_held
        self.binding_checks += 1
        if (identity_id, floor_id, lineage_id) != (
            self.current.identity_id,
            self.current.floor_id,
            self.current.lineage_id,
        ):
            raise SourcePolicyError("source_installation_binding_mismatch")

    def check_root(self, root: CanonicalRootRef, purpose: str = "read") -> None:
        raise AssertionError("retention_preview_requested_source_disclosure")

    def check_footprint(self, footprint: ProtectedContentFootprint, purpose: str = "read") -> None:
        raise AssertionError("retention_preview_requested_payload_disclosure")


def _planner(
    inventory: ErasureInventory | None = None,
    *,
    excluded: bool = True,
) -> tuple[ErasurePlanner, FakeSourceAdmission, FakeInventoryPort]:
    snapshot = SourceEligibilitySnapshot(
        "floor",
        "installation",
        "lineage",
        "satori",
        1 if excluded else 0,
        frozenset({_root().message_id}) if excluded else frozenset(),
    )
    runtime = FakeSourceAdmission(snapshot)
    port = FakeInventoryPort(inventory or _inventory())
    return ErasurePlanner(runtime, port), runtime, port


def _request() -> ErasurePreviewRequest:
    return ErasurePreviewRequest(message_id=_root().message_id, counterparty_id="alice")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("message_id", "foreign-source"), ("counterparty_id", "bob")],
)
def test_inventory_cannot_swap_requested_target_before_digest(field: str, replacement: str) -> None:
    inventory = replace(_inventory(), root=replace(_root(), **{field: replacement}))
    planner, runtime, _ = _planner(inventory)

    with pytest.raises(SourcePolicyError, match=r"^erasure_inventory_target_mismatch$"):
        planner.preview(_request())

    assert runtime.binding_checks == 0
    assert runtime.lease_held is False


@pytest.mark.parametrize("field", ["identity_id", "lineage_id"])
def test_inventory_cannot_substitute_bound_identity_or_lineage(field: str) -> None:
    inventory = replace(_inventory(), root=replace(_root(), **{field: "foreign-binding"}))
    planner, runtime, _ = _planner(inventory)

    with pytest.raises(SourcePolicyError, match=r"^source_installation_binding_mismatch$"):
        planner.preview(_request())

    assert runtime.binding_checks == 1
    assert runtime.lease_held is False


def test_preview_does_not_silently_exclude_an_allowed_target() -> None:
    planner, runtime, _ = _planner(excluded=False)
    before = runtime.current

    with pytest.raises(SourcePolicyError, match=r"^erasure_requires_source_exclusion$"):
        planner.preview(_request())

    assert runtime.current == before
    assert runtime.lease_held is False


def test_same_store_and_counts_cannot_hide_changed_internal_routing() -> None:
    planner, runtime, port = _planner()
    preview = planner.preview(_request())
    port.result = replace(port.result, routing_digest="d" * 64)
    with pytest.raises(SourcePolicyError, match=r"^erasure_preview_stale$"):
        planner.check(
            ErasureCheckRequest(
                message_id=_root().message_id,
                counterparty_id="alice",
                expected_generation=preview.generation,
                preview_digest=preview.preview_digest,
            )
        )
    assert runtime.lease_held is False


def test_route_metadata_is_immutable_and_preserves_uncertainty() -> None:
    route = _route()
    assert route.rows == 2
    assert route.uncertain_rows == 1
    assert route.content_columns == ("quote",)
    with pytest.raises(FrozenInstanceError):
        route.__setattr__("rows", 0)


@pytest.mark.parametrize("field", ["rows", "uncertain_rows"])
@pytest.mark.parametrize("value", [True, False])
def test_boolean_route_counts_are_not_accepted_as_integers(field: str, value: bool) -> None:
    if field == "rows":
        with pytest.raises(ValueError, match=r"^invalid_erasure_row_count$"):
            replace(_route(), rows=value)
    else:
        with pytest.raises(ValueError, match=r"^invalid_erasure_uncertain_count$"):
            replace(_route(), uncertain_rows=value)


def test_duplicate_routes_cannot_double_count_one_retention_family() -> None:
    with pytest.raises(ValueError, match=r"^duplicate_erasure_route$"):
        replace(_inventory(), routes=(_route(), replace(_route(), rows=3)))


def test_duplicate_content_columns_are_not_a_second_erasure_route() -> None:
    with pytest.raises(ValueError, match=r"^duplicate_erasure_column$"):
        replace(_route(), content_columns=("quote", "quote"))


def test_route_order_does_not_change_the_reviewed_plan() -> None:
    index_route = ErasureRoute(
        owner="retrieval",
        table="episodic_memory_embeddings",
        disposition=ErasureDisposition.REMOVE_INDEX,
        rows=1,
        content_columns=("vector",),
    )
    planner, _, port = _planner(replace(_inventory(), routes=(_route(), index_route)))
    original = planner.preview(_request())
    port.result = replace(port.result, routes=(index_route, _route()))
    reordered = planner.preview(_request())

    assert reordered.preview_digest == original.preview_digest
    assert reordered.routes == original.routes


def test_matching_blocked_assessment_remains_blocked_without_execution_permission() -> None:
    planner, runtime, _ = _planner(
        replace(
            _inventory(),
            uncertain_payload_versions=1,
            blockers=("erasure_unknown_provenance",),
        )
    )
    preview = planner.preview(_request())
    result = planner.check(
        ErasureCheckRequest(
            message_id=preview.root.message_id,
            counterparty_id=preview.root.counterparty_id,
            expected_generation=preview.generation,
            preview_digest=preview.preview_digest,
        )
    )

    assert result.review_status == "current"
    assert result.assessment_status == "blocked"
    assert result.execution_authorized is False
    assert result.physical_erasure is False
    assert preview.physical_readiness == "blocked"
    assert preview.automatic_processing is False
    assert "physical_erasure_executor_not_implemented" in preview.execution_blockers
    assert runtime.lease_held is False


def test_changed_route_without_floor_advance_invalidates_review() -> None:
    planner, runtime, port = _planner()
    preview = planner.preview(_request())
    port.result = replace(port.result, routes=(replace(_route(), rows=3),))

    with pytest.raises(SourcePolicyError, match=r"^erasure_preview_stale$"):
        planner.check(
            ErasureCheckRequest(
                message_id=preview.root.message_id,
                counterparty_id=preview.root.counterparty_id,
                expected_generation=preview.generation,
                preview_digest=preview.preview_digest,
            )
        )

    assert runtime.current.generation == preview.generation
    assert runtime.lease_held is False


def test_operator_check_rejects_coercion_and_remains_immutable() -> None:
    payload: dict[str, object] = {
        "message_id": "user-source",
        "counterparty_id": "alice",
        "expected_generation": True,
        "preview_digest": "c" * 64,
    }
    with pytest.raises(ValidationError):
        ErasureCheckRequest.model_validate(payload)
    payload["expected_generation"] = 1
    request = ErasureCheckRequest.model_validate(payload)
    with pytest.raises(ValidationError, match="frozen_instance"):
        request.counterparty_id = "bob"
