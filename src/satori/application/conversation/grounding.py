"""Deterministic response-evidence gate before durable delivery."""

from dataclasses import dataclass

from satori.application.conversation.errors import UnsupportedPastClaim
from satori.core.conversation import ConversationProviderResponse


@dataclass(frozen=True, slots=True)
class ResponseGroundingGate:
    """Require every provider-declared shared-past claim to cite available prior evidence."""

    policy_version: int = 1

    def validate(
        self,
        response: ConversationProviderResponse,
        *,
        available_past_evidence_ids: tuple[str, ...],
    ) -> None:
        """Reject claims whose references were absent from generation context."""

        available = set(available_past_evidence_ids)
        for claim in response.declared_past_claims:
            if not set(claim.evidence_ids) <= available:
                raise UnsupportedPastClaim(
                    "provider declared a shared-past claim without available prior evidence"
                )
