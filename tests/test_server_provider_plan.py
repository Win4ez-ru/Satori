"""Exhaustive preparation gates, without provider calls or real account declarations."""

from datetime import UTC, datetime, timedelta

import pytest

from satori.server.providers import (
    AdapterQualification,
    BillingEligibility,
    Capability,
    ContextClass,
    DisclosureDeclaration,
    ModelQualification,
    Placement,
    PlanIssueCode,
    PlanStatus,
    ProviderPlan,
    ProviderPlanError,
    ProviderRole,
    ProviderRoute,
    assess_provider_plan,
)

NOW = datetime(2026, 10, 31, 16, tzinfo=UTC)


def route(role: ProviderRole, *, remote: bool = False) -> ProviderRoute:
    return ProviderRoute(
        role=role,
        adapter_id="synthetic_adapter",
        model_id="synthetic/model:v1",
        recipient_id="synthetic_recipient",
        endpoint_sha256="a" * 64,
        placement=Placement.REMOTE if remote else Placement.SERVER_LOCAL,
        capabilities=frozenset(
            {
                {
                    ProviderRole.CONVERSATION: Capability.CONVERSATION,
                    ProviderRole.EMBEDDING: Capability.EMBEDDING,
                }.get(role, Capability.STRUCTURED)
            }
        ),
        adapter_qualification=AdapterQualification.TARGET_QUALIFIED,
        model_qualification=ModelQualification.SEMANTIC_ACCEPTED,
        context_classes=frozenset({ContextClass.OWNER_SELECTED_EVIDENCE}),
        billable=remote,
        billing_eligibility=BillingEligibility.ACCOUNT_CONFIRMED,
        payer_country="RU" if remote else None,
    )


def plan(*, remote: bool = False) -> ProviderPlan:
    routes = tuple(route(role, remote=remote) for role in ProviderRole)
    return ProviderPlan(
        routes=routes,
        disclosures=tuple(
            DisclosureDeclaration(
                role=item.role,
                route_sha256=item.disclosure_digest(),
                expires_at=NOW + timedelta(minutes=10),
            )
            for item in routes
            if remote
        ),
    )


def replace_route(source: ProviderPlan, replacement: ProviderRoute) -> ProviderPlan:
    return ProviderPlan(
        routes=tuple(
            replacement if item.role == replacement.role else item for item in source.routes
        ),
        disclosures=source.disclosures,
    )


def issue_codes(source: ProviderPlan, role: ProviderRole) -> set[PlanIssueCode]:
    return {item.code for item in assess_provider_plan(source, now=NOW).issues if item.role == role}


def test_complete_local_plan_is_only_ready_for_further_runtime_qualification() -> None:
    result = assess_provider_plan(plan(), now=NOW)
    assert result.status == PlanStatus.READY_FOR_RUNTIME_QUALIFICATION
    assert not result.issues
    assert not result.deployment_authorized
    assert not result.runtime_dispatch_authorized


@pytest.mark.parametrize("missing", list(ProviderRole))
def test_no_role_can_be_omitted_or_replaced_by_duplicate(missing: ProviderRole) -> None:
    source = plan().model_dump(mode="json")
    routes = source["routes"]
    assert isinstance(routes, list)
    source["routes"] = [item for item in routes if item["role"] != missing.value]
    with pytest.raises(ProviderPlanError, match=r"^invalid_plan$"):
        ProviderPlan.parse_document(source)
    other = next(item for item in routes if item["role"] != missing.value)
    source["routes"].append(other)
    with pytest.raises(ProviderPlanError, match=r"^invalid_plan$"):
        ProviderPlan.parse_document(source)


def test_untrusted_plan_rejection_contains_no_raw_input() -> None:
    supplied = {"routes": [], "secret": "PRIVATE_SENTINEL"}
    with pytest.raises(ProviderPlanError) as caught:
        ProviderPlan.parse_document(supplied)
    assert str(caught.value) == "invalid_plan"
    assert "PRIVATE_SENTINEL" not in repr(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("value", [True, 0, "false", None])
def test_automatic_fallback_is_rejected(value: object) -> None:
    supplied = plan().model_dump(mode="json")
    supplied["automatic_fallback"] = value
    with pytest.raises(ProviderPlanError):
        ProviderPlan.parse_document(supplied)


@pytest.mark.parametrize("role", list(ProviderRole))
def test_capability_is_checked_per_role(role: ProviderRole) -> None:
    wrong = Capability.STRUCTURED
    if role not in {ProviderRole.CONVERSATION, ProviderRole.EMBEDDING}:
        wrong = Capability.CONVERSATION
    changed = route(role).model_copy(update={"capabilities": frozenset({wrong})})
    assert issue_codes(replace_route(plan(), changed), role) == {PlanIssueCode.CAPABILITY_MISMATCH}


def test_foreground_protocol_pass_does_not_qualify_model_or_background_roles() -> None:
    candidate = ProviderPlan(
        routes=tuple(
            item.model_copy(
                update={
                    "adapter_qualification": AdapterQualification.OFFLINE_TESTED,
                    "model_qualification": ModelQualification.PROTOCOL_ONLY,
                }
            )
            for item in plan().routes
        )
    )
    report = assess_provider_plan(candidate, now=NOW)
    assert report.status == PlanStatus.BLOCKED
    assert {item.role for item in report.issues} == set(ProviderRole)
    assert {item.code for item in report.issues} == {
        PlanIssueCode.ADAPTER_UNQUALIFIED,
        PlanIssueCode.MODEL_UNQUALIFIED,
    }


def test_remote_routes_each_require_matching_unexpired_declaration() -> None:
    candidate = plan(remote=True)
    assert (
        assess_provider_plan(candidate, now=NOW).status
        == PlanStatus.READY_FOR_RUNTIME_QUALIFICATION
    )
    undeclared = ProviderPlan(routes=candidate.routes)
    report = assess_provider_plan(undeclared, now=NOW)
    assert len(report.issues) == 9
    assert {item.code for item in report.issues} == {PlanIssueCode.DISCLOSURE_UNAPPROVED}
    assert {item.role for item in report.issues} == set(ProviderRole)
    assert (
        assess_provider_plan(candidate, now=NOW + timedelta(minutes=10)).status
        == PlanStatus.BLOCKED
    )


@pytest.mark.parametrize(
    "change",
    [
        {"model_id": "different_model"},
        {"adapter_id": "different_adapter"},
        {"recipient_id": "different_recipient"},
        {"endpoint_sha256": "b" * 64},
        {"context_classes": frozenset({ContextClass.TYPED_SELF})},
    ],
)
def test_disclosure_cannot_follow_model_recipient_or_context_change(
    change: dict[str, object],
) -> None:
    source = plan(remote=True)
    changed = route(ProviderRole.CONVERSATION, remote=True).model_copy(update=change)
    assert issue_codes(replace_route(source, changed), changed.role) == {
        PlanIssueCode.DISCLOSURE_UNAPPROVED
    }


@pytest.mark.parametrize(
    ("eligibility", "country"),
    [
        (BillingEligibility.UNKNOWN, None),
        (BillingEligibility.DOCUMENTED_ONLY, "RU"),
        (BillingEligibility.ACCOUNT_CONFIRMED, None),
    ],
)
def test_unknown_residency_or_public_payment_docs_are_not_billing_admission(
    eligibility: BillingEligibility, country: str | None
) -> None:
    source = plan(remote=True)
    changed = route(ProviderRole.APPRAISAL, remote=True).model_copy(
        update={"billing_eligibility": eligibility, "payer_country": country}
    )
    assert issue_codes(replace_route(source, changed), changed.role) == {
        PlanIssueCode.BILLING_UNCONFIRMED
    }


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ProviderPlanError):
        assess_provider_plan(plan(), now=datetime(2026, 10, 31))


def test_document_round_trip_and_order_do_not_change_route_binding() -> None:
    source = plan(remote=True)
    restored = ProviderPlan.parse_document(source.model_dump(mode="json"))
    assert restored == source
    assert assess_provider_plan(restored, now=NOW) == assess_provider_plan(source, now=NOW)
    assert "synthetic_recipient" not in repr(source)


def test_duplicate_or_naive_disclosure_is_rejected_at_input_boundary() -> None:
    source = plan(remote=True).model_dump(mode="json")
    source["disclosures"][1] = source["disclosures"][0]
    with pytest.raises(ProviderPlanError):
        ProviderPlan.parse_document(source)
    source = plan(remote=True).model_dump(mode="json")
    source["disclosures"][0]["expires_at"] = "2026-10-31T16:10:00"
    with pytest.raises(ProviderPlanError):
        ProviderPlan.parse_document(source)


def test_constructed_or_copy_updated_models_cannot_bypass_assessment() -> None:
    source = plan()
    forged = source.model_copy(update={"automatic_fallback": True})
    with pytest.raises(ProviderPlanError):
        assess_provider_plan(forged, now=NOW)
    forged = source.model_copy(update={"routes": source.routes[:1]})
    with pytest.raises(ProviderPlanError):
        assess_provider_plan(forged, now=NOW)
