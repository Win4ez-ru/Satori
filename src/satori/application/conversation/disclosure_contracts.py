"""Typed request-local disclosure scope shared by classification and delivery."""

from dataclasses import dataclass
from enum import StrEnum


class ConversationalDisclosureMode(StrEnum):
    """Small deterministic depth selector; never a state or semantic intent model."""

    SOCIAL = "social"
    REGISTER_CORRECTION = "register_correction"
    PERSONAL_IDENTITY = "personal_identity"
    DIGITAL_NATURE = "digital_nature"
    MEMORY = "memory"
    EMOTION = "emotion"
    INTERESTS = "interests"
    INDEPENDENCE = "independence"
    STYLE_CALIBRATION = "style_calibration"
    TECHNICAL_IDENTITY = "technical_identity"
    CONSCIOUSNESS = "consciousness"
    RELATIONSHIP_CURRENT = "relationship_current"
    RELATIONSHIP_CAPABILITY = "relationship_capability"
    GENERAL = "general"


class DisclosureFacet(StrEnum):
    """Authoritative self fact that must survive the primary response mode."""

    IDENTITY = "identity"
    MEMORY = "memory"
    AFFECT = "affect"
    INTERESTS = "interests"
    RELATIONSHIP = "relationship"
    EMBODIMENT = "embodiment"
    PROVIDER_TECHNICAL = "provider_technical"
    CONSCIOUSNESS_BOUNDARY = "consciousness_boundary"
    ORIGIN = "origin"


class DisclosureRequestKind(StrEnum):
    """Closed subject boundary for whether the turn requests Satori's own state."""

    NONE = "none"
    SATORI_SELF = "satori_self"


_V25_REQUIRED_FACETS_BY_MODE: dict[
    ConversationalDisclosureMode,
    frozenset[DisclosureFacet],
] = {
    ConversationalDisclosureMode.PERSONAL_IDENTITY: frozenset({DisclosureFacet.IDENTITY}),
    ConversationalDisclosureMode.DIGITAL_NATURE: frozenset({DisclosureFacet.IDENTITY}),
    ConversationalDisclosureMode.MEMORY: frozenset({DisclosureFacet.MEMORY}),
    ConversationalDisclosureMode.EMOTION: frozenset({DisclosureFacet.AFFECT}),
    ConversationalDisclosureMode.INTERESTS: frozenset({DisclosureFacet.INTERESTS}),
    ConversationalDisclosureMode.TECHNICAL_IDENTITY: frozenset(
        {DisclosureFacet.IDENTITY, DisclosureFacet.PROVIDER_TECHNICAL}
    ),
    ConversationalDisclosureMode.CONSCIOUSNESS: frozenset({DisclosureFacet.CONSCIOUSNESS_BOUNDARY}),
    ConversationalDisclosureMode.RELATIONSHIP_CURRENT: frozenset(
        {DisclosureFacet.RELATIONSHIP, DisclosureFacet.AFFECT}
    ),
    ConversationalDisclosureMode.RELATIONSHIP_CAPABILITY: frozenset(
        {DisclosureFacet.RELATIONSHIP, DisclosureFacet.AFFECT}
    ),
}
_V32_ORTHOGONAL_SELF_FACETS = frozenset(
    {
        DisclosureFacet.IDENTITY,
        DisclosureFacet.MEMORY,
        DisclosureFacet.AFFECT,
        DisclosureFacet.INTERESTS,
        DisclosureFacet.RELATIONSHIP,
        DisclosureFacet.EMBODIMENT,
        DisclosureFacet.PROVIDER_TECHNICAL,
        DisclosureFacet.CONSCIOUSNESS_BOUNDARY,
    }
)


@dataclass(frozen=True, slots=True)
class ConversationalDisclosurePlan:
    """One primary conversational action plus all directly required self facts."""

    primary_mode: ConversationalDisclosureMode
    required_facets: tuple[DisclosureFacet, ...]
    policy_schema_version: int = 25
    request_kind: DisclosureRequestKind = DisclosureRequestKind.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.primary_mode, ConversationalDisclosureMode):
            raise ValueError("disclosure primary_mode must be typed")
        if type(self.policy_schema_version) is not int or self.policy_schema_version < 1:
            raise ValueError("disclosure policy_schema_version must be positive")
        if not isinstance(self.request_kind, DisclosureRequestKind):
            raise ValueError("disclosure request_kind must be typed")
        facets = tuple(self.required_facets)
        if len(facets) != len(set(facets)) or not all(
            isinstance(facet, DisclosureFacet) for facet in facets
        ):
            raise ValueError("disclosure facets must be unique typed values")
        facet_set = frozenset(facets)
        if self.policy_schema_version < 25 and DisclosureFacet.INTERESTS in facet_set:
            raise ValueError("interests disclosure facet requires behavior policy v25")
        if self.policy_schema_version < 25 and self.request_kind is not DisclosureRequestKind.NONE:
            raise ValueError("self-disclosure request kind requires behavior policy v25")
        if self.policy_schema_version >= 25:
            orthogonal_style_self = (
                self.policy_schema_version >= 32
                and self.primary_mode is ConversationalDisclosureMode.STYLE_CALIBRATION
                and bool(facet_set.intersection(_V32_ORTHOGONAL_SELF_FACETS))
            )
            if (
                self.policy_schema_version >= 32
                and self.primary_mode is ConversationalDisclosureMode.STYLE_CALIBRATION
                and self.request_kind is DisclosureRequestKind.SATORI_SELF
            ):
                if not orthogonal_style_self:
                    raise ValueError("style self scope requires authoritative facets")
                if (
                    DisclosureFacet.PROVIDER_TECHNICAL in facet_set
                    and DisclosureFacet.IDENTITY not in facet_set
                ) or (
                    DisclosureFacet.RELATIONSHIP in facet_set
                    and DisclosureFacet.AFFECT not in facet_set
                ):
                    raise ValueError("style self scope lacks coupled authoritative facets")
            required = _V25_REQUIRED_FACETS_BY_MODE.get(self.primary_mode, frozenset())
            if not required <= facet_set:
                raise ValueError("disclosure mode requires its authoritative facets")
            if (
                DisclosureFacet.INTERESTS in facet_set
                and self.request_kind is not DisclosureRequestKind.SATORI_SELF
            ):
                raise ValueError("interests facet requires a direct Satori self request")
            if self.primary_mode is ConversationalDisclosureMode.SOCIAL and facet_set not in {
                frozenset(),
                frozenset({DisclosureFacet.AFFECT}),
            }:
                raise ValueError("social disclosure accepts only an optional affect facet")
            unambiguous_self_modes = set(_V25_REQUIRED_FACETS_BY_MODE) - {
                ConversationalDisclosureMode.RELATIONSHIP_CURRENT
            }
            if (
                self.primary_mode in unambiguous_self_modes
                or (
                    self.primary_mode is ConversationalDisclosureMode.SOCIAL
                    and DisclosureFacet.AFFECT in facet_set
                )
            ) and self.request_kind is not DisclosureRequestKind.SATORI_SELF:
                raise ValueError("direct Satori disclosure mode requires a self request kind")
            if self.request_kind is DisclosureRequestKind.SATORI_SELF and not (
                self.primary_mode in _V25_REQUIRED_FACETS_BY_MODE
                or (
                    self.primary_mode is ConversationalDisclosureMode.SOCIAL
                    and DisclosureFacet.AFFECT in facet_set
                )
                or orthogonal_style_self
            ):
                raise ValueError("self request kind requires a direct Satori disclosure mode")
        object.__setattr__(self, "required_facets", facets)


def is_satori_self_disclosure_plan(plan: ConversationalDisclosurePlan) -> bool:
    """Select a primary self action, not every orthogonal direct-self request scope.

    V32 style correction can carry SATORI_SELF scope and factual facets while its
    primary action remains correction. It must not gain the cognition self-action
    signal which would displace that genuine correction.
    """

    if (
        plan.policy_schema_version < 25
        or plan.request_kind is not DisclosureRequestKind.SATORI_SELF
    ):
        return False
    return plan.primary_mode in _V25_REQUIRED_FACETS_BY_MODE or (
        plan.primary_mode is ConversationalDisclosureMode.SOCIAL
        and DisclosureFacet.AFFECT in plan.required_facets
    )


def uses_personal_self_disclosure_delivery(plan: ConversationalDisclosurePlan) -> bool:
    """Whether v25 should answer as one cohesive personal disclosure rather than a fact lookup."""

    if not is_satori_self_disclosure_plan(plan):
        return False
    if (
        plan.policy_schema_version >= 29
        and plan.primary_mode is ConversationalDisclosureMode.EMOTION
        and DisclosureFacet.RELATIONSHIP in plan.required_facets
    ):
        # The existing relational owned-response route carries both required facets.
        # SELF_DISCLOSE has a closed personal facet set that excludes relationship.
        return False
    if plan.primary_mode in {
        ConversationalDisclosureMode.PERSONAL_IDENTITY,
        ConversationalDisclosureMode.EMOTION,
        ConversationalDisclosureMode.INTERESTS,
    }:
        return True
    return (
        plan.primary_mode
        in {
            ConversationalDisclosureMode.DIGITAL_NATURE,
            ConversationalDisclosureMode.MEMORY,
            ConversationalDisclosureMode.CONSCIOUSNESS,
        }
        and len(plan.required_facets) >= 2
    )
