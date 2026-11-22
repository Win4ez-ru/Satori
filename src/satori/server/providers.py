"""Explicit inference placement declarations; neither provider construction nor a grant.

The report is a preparation gate only. Real dispatch still needs source-policy
admission, a live scoped disclosure grant, budget admission and target tests.
"""

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ProviderRole(StrEnum):
    CONVERSATION = "conversation"
    EPISODE = "episode"
    EMBEDDING = "embedding"
    SEMANTIC = "semantic"
    APPRAISAL = "appraisal"
    RELATIONSHIP = "relationship"
    MODELS = "models"
    POSITIONS = "positions"
    REFLECTION = "reflection"


class Capability(StrEnum):
    CONVERSATION = "conversation"
    STRUCTURED = "structured"
    EMBEDDING = "embedding"


class Placement(StrEnum):
    SERVER_LOCAL = "server_local"
    REMOTE = "remote"


class AdapterQualification(StrEnum):
    NOT_IMPLEMENTED = "not_implemented"
    OFFLINE_TESTED = "offline_tested"
    TARGET_QUALIFIED = "target_qualified"


class ModelQualification(StrEnum):
    NOT_RUN = "not_run"
    PROTOCOL_ONLY = "protocol_only"
    SEMANTIC_ACCEPTED = "semantic_accepted"


class BillingEligibility(StrEnum):
    UNKNOWN = "unknown"
    DOCUMENTED_ONLY = "documented_only"
    ACCOUNT_CONFIRMED = "account_confirmed"


class ContextClass(StrEnum):
    """Canonical context classes only; external-source cards are not admitted here."""

    CURRENT_INPUT = "current_input"
    DIALOGUE = "dialogue"
    TYPED_SELF = "typed_self"
    RETRIEVED_MEMORY = "retrieved_memory"
    OWNER_SELECTED_EVIDENCE = "owner_selected_evidence"


class PlanIssueCode(StrEnum):
    INVALID_PLAN = "invalid_plan"
    CAPABILITY_MISMATCH = "capability_mismatch"
    ADAPTER_UNQUALIFIED = "adapter_unqualified"
    MODEL_UNQUALIFIED = "model_unqualified"
    DISCLOSURE_UNAPPROVED = "disclosure_unapproved"
    BILLING_UNCONFIRMED = "billing_unconfirmed"


class ProviderPlanError(ValueError):
    """Closed diagnostics; never echo supplied identifiers or validation input."""

    def __init__(self, code: PlanIssueCode = PlanIssueCode.INVALID_PLAN) -> None:
        self.code = code
        super().__init__(code.value)


class _Document(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class ProviderRoute(_Document):
    role: ProviderRole
    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$", repr=False)
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$", repr=False)
    recipient_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$", repr=False)
    endpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    placement: Placement
    capabilities: frozenset[Capability] = Field(min_length=1, max_length=3)
    adapter_qualification: AdapterQualification = AdapterQualification.NOT_IMPLEMENTED
    model_qualification: ModelQualification = ModelQualification.NOT_RUN
    context_classes: frozenset[ContextClass] = Field(min_length=1, max_length=5)
    billable: bool = Field(strict=True)
    billing_eligibility: BillingEligibility = BillingEligibility.UNKNOWN
    payer_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$", repr=False)

    def disclosure_digest(self) -> str:
        """Bind a declaration to exact routing and the finite context schema, not text."""
        material = {
            "schema": "satori.provider_route_disclosure.v1",
            "role": self.role.value,
            "adapter": self.adapter_id,
            "model": self.model_id,
            "recipient": self.recipient_id,
            "endpoint_sha256": self.endpoint_sha256,
            "placement": self.placement.value,
            "context_classes": sorted(c.value for c in self.context_classes),
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


class DisclosureDeclaration(_Document):
    """Operator-reviewed planning declaration; cannot authorize runtime disclosure.

    This carries no credential, profile authority or provider API capability.
    Runtime must resolve a fresh actual grant before each dispatch.
    """

    role: ProviderRole
    route_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone_required")
        return value.astimezone(UTC)


class ProviderPlan(_Document):
    schema_version: Literal[1] = 1
    routes: tuple[ProviderRoute, ...] = Field(min_length=9, max_length=9)
    disclosures: tuple[DisclosureDeclaration, ...] = Field(default=(), max_length=9, repr=False)
    automatic_fallback: Literal[False] = False

    @field_validator("automatic_fallback", mode="before")
    @classmethod
    def no_fallback(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("fallback_forbidden")
        return False

    @model_validator(mode="after")
    def exact_role_coverage(self) -> Self:
        roles = [route.role for route in self.routes]
        declared = [declaration.role for declaration in self.disclosures]
        if set(roles) != set(ProviderRole) or len(set(roles)) != len(roles):
            raise ValueError("exact_role_coverage_required")
        if len(set(declared)) != len(declared):
            raise ValueError("duplicate_disclosure_role")
        return self

    @classmethod
    def parse_document(cls, value: object) -> "ProviderPlan":
        """The untrusted-input boundary exposes one closed failure code only."""
        try:
            return cls.model_validate(value)
        except (ValidationError, TypeError, ValueError):
            raise ProviderPlanError() from None


class PlanIssue(_Document):
    role: ProviderRole
    code: PlanIssueCode


class PlanStatus(StrEnum):
    BLOCKED = "blocked"
    READY_FOR_RUNTIME_QUALIFICATION = "ready_for_runtime_qualification"


class ProviderPlanReport(_Document):
    status: PlanStatus
    issues: tuple[PlanIssue, ...]
    deployment_authorized: Literal[False] = False
    runtime_dispatch_authorized: Literal[False] = False


def assess_provider_plan(plan: ProviderPlan, *, now: datetime) -> ProviderPlanReport:
    """Evaluate complete declarations without calls, profiles, environment or I/O.

    Catalog prices, API compatibility and offline fixtures do not qualify either
    adapter execution on the target or role-specific semantic model behavior.
    """
    plan = ProviderPlan.parse_document(plan)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ProviderPlanError()
    declarations = {item.role: item for item in plan.disclosures}
    issues: list[PlanIssue] = []
    for route in sorted(plan.routes, key=lambda item: item.role.value):
        required = {
            ProviderRole.CONVERSATION: Capability.CONVERSATION,
            ProviderRole.EMBEDDING: Capability.EMBEDDING,
        }.get(route.role, Capability.STRUCTURED)
        if required not in route.capabilities:
            issues.append(PlanIssue(role=route.role, code=PlanIssueCode.CAPABILITY_MISMATCH))
        if route.adapter_qualification != AdapterQualification.TARGET_QUALIFIED:
            issues.append(PlanIssue(role=route.role, code=PlanIssueCode.ADAPTER_UNQUALIFIED))
        if route.model_qualification != ModelQualification.SEMANTIC_ACCEPTED:
            issues.append(PlanIssue(role=route.role, code=PlanIssueCode.MODEL_UNQUALIFIED))
        if route.placement == Placement.REMOTE:
            declaration = declarations.get(route.role)
            if (
                declaration is None
                or declaration.route_sha256 != route.disclosure_digest()
                or declaration.expires_at <= now
            ):
                issues.append(PlanIssue(role=route.role, code=PlanIssueCode.DISCLOSURE_UNAPPROVED))
        if route.billable and (
            route.billing_eligibility != BillingEligibility.ACCOUNT_CONFIRMED
            or route.payer_country is None
        ):
            issues.append(PlanIssue(role=route.role, code=PlanIssueCode.BILLING_UNCONFIRMED))
    return ProviderPlanReport(
        status=PlanStatus.BLOCKED if issues else PlanStatus.READY_FOR_RUNTIME_QUALIFICATION,
        issues=tuple(issues),
    )
