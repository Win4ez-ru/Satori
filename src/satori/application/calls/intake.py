"""Bounded, transient call intake producing owner-review proposals only.

Inputs are normalized assertions from a future qualified adapter, not authenticated
provider wire messages. Constructing these DTOs does not authenticate a caller or
prove notice audibility, participant consent, media or remote termination. There
is deliberately no provider implementation, default authority, parser, network,
recognizer, model call, canonical Send, persistence or action execution here.
"""

from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal, require_utc

MAX_CALL_LIFETIME = timedelta(seconds=120)
MAX_SEGMENTS = 8
MAX_TEXT_BYTES = 8192
FollowupKind = Literal["owner_callback_review", "owner_calendar_review"]
IntakePhase = Literal["awaiting_consent", "intake", "ended", "remote_end_unknown", "local_stopped"]


class CallIntakeError(Exception):
    """Finite body-free failure; never include text, raw errors or credentials."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _nonzero(value: UUID) -> UUID:
    if value.int == 0:
        raise ValueError("nonzero_uuid_required")
    return value


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class CallBinding(_DTO):
    """Opaque exact route/attempt binding, separate from caller-ID and identity."""

    principal: LocalPrincipal = Field(repr=False)
    route_id: UUID
    provider_account_ref: UUID = Field(repr=False)
    call_id: UUID
    leg_id: UUID
    generation: int = Field(ge=1)

    _ids = field_validator("route_id", "provider_account_ref", "call_id", "leg_id")(_nonzero)


class CallIntakeGrant(_DTO):
    """One operation's local intake authority; no calendar or messaging authority."""

    grant_id: UUID
    binding: CallBinding = Field(repr=False)
    notice_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    issued_at: datetime
    expires_at: datetime

    _id = field_validator("grant_id")(_nonzero)
    _utc = field_validator("issued_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def finite_window(self) -> "CallIntakeGrant":
        if not timedelta(0) < self.expires_at - self.issued_at <= MAX_CALL_LIFETIME:
            raise ValueError("bounded_call_window_required")
        return self


class ParticipantConsent(_DTO):
    """Correlated adapter assertion; not a self-authenticating permission token."""

    binding: CallBinding = Field(repr=False)
    grant_id: UUID
    notice_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    participant_ref: UUID = Field(repr=False)
    affirmation: Literal["affirmative"]
    method: Literal["synthetic_fixture", "qualified_peer_button", "qualified_provider_dtmf"]
    observed_at: datetime

    _ids = field_validator("grant_id", "participant_ref")(_nonzero)
    _utc = field_validator("observed_at")(require_utc)


class ExternalCallSegment(_DTO):
    """Final attributed external text; never the owner's canonical USER input."""

    binding: CallBinding = Field(repr=False)
    segment_id: UUID
    participant_ref: UUID = Field(repr=False)
    sequence: int = Field(ge=1, le=MAX_SEGMENTS)
    observed_at: datetime
    text: str = Field(min_length=1, max_length=500, repr=False)
    final: Literal[True] = True

    _ids = field_validator("segment_id", "participant_ref")(_nonzero)
    _utc = field_validator("observed_at")(require_utc)

    @field_validator("text")
    @classmethod
    def bounded_plain_text(cls, value: str) -> str:
        if not value.strip() or any(
            (ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value
        ):
            raise ValueError("plain_external_text_required")
        return value


class CallEndReceipt(_DTO):
    binding: CallBinding = Field(repr=False)
    receipt_id: UUID
    status: Literal["confirmed_ended", "unknown"]
    observed_at: datetime

    _id = field_validator("receipt_id")(_nonzero)
    _utc = field_validator("observed_at")(require_utc)


class CallFollowupProposal(_DTO):
    """Transient review data, intentionally not AssistantIntent or an executable action."""

    schema_version: Literal[1] = 1
    request_id: UUID
    binding: CallBinding = Field(repr=False)
    grant_id: UUID
    source: Literal["external_call"] = "external_call"
    kind: FollowupKind
    segment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_SEGMENTS)
    untrusted_fragments: tuple[str, ...] = Field(min_length=1, max_length=MAX_SEGMENTS, repr=False)
    created_at: datetime
    expires_at: datetime
    requires_owner_review: Literal[True] = True
    effect_authority: Literal[False] = False
    canonical_user_eligible: Literal[False] = False
    memory_self_relationship_eligible: Literal[False] = False
    provider_disclosure_authorized: Literal[False] = False
    tts_authorized: Literal[False] = False

    _ids = field_validator("request_id", "grant_id")(_nonzero)
    _utc = field_validator("created_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def bounded_review(self) -> "CallFollowupProposal":
        if (
            len(self.segment_ids) != len(self.untrusted_fragments)
            or len(set(self.segment_ids)) != len(self.segment_ids)
            or any(item.int == 0 for item in self.segment_ids)
            or any(not value.strip() or len(value) > 500 for value in self.untrusted_fragments)
            or sum(len(value.encode("utf-8")) for value in self.untrusted_fragments)
            > MAX_TEXT_BYTES
            or not timedelta(0) < self.expires_at - self.created_at <= MAX_CALL_LIFETIME
        ):
            raise ValueError("bounded_review_required")
        return self


class IntakeAuthority(Protocol):
    """Existing host/source owner seam; a concrete authority adapter is not supplied."""

    def is_current(self, binding: CallBinding, grant_id: UUID, now: datetime) -> bool: ...


class CallIntakeSession:
    """One ephemeral attempt; every content access rechecks its source authority.

    Callers must use resolve_proposal() at display time, not cache returned content.
    Clearing references is logical revocation, not a forensic RAM-erasure guarantee.
    Normalized notice/consent/terminal assertions require independent real-adapter
    qualification before any actual participant's data can enter this contract.
    """

    def __init__(
        self,
        grant: CallIntakeGrant,
        *,
        now: datetime,
        authority: IntakeAuthority | None = None,
    ) -> None:
        self._grant = grant
        self._authority = authority
        self._phase: IntakePhase = "awaiting_consent"
        self._last_now = grant.issued_at
        self._consent: ParticipantConsent | None = None
        self._segments: list[ExternalCallSegment] = []
        self._proposal: CallFollowupProposal | None = None
        self._end: CallEndReceipt | None = None
        self._guard(now)

    def _clear(self, phase: IntakePhase) -> None:
        self._segments.clear()
        self._consent = None
        self._proposal = None
        self._phase = phase

    def _guard(self, now: datetime) -> None:
        try:
            now = require_utc(now)
        except ValueError:
            self._clear("local_stopped")
            raise CallIntakeError("clock_invalid") from None
        if now < self._last_now:
            self._clear("local_stopped")
            raise CallIntakeError("clock_invalid")
        self._last_now = now
        if not self._grant.issued_at <= now < self._grant.expires_at:
            self._clear("local_stopped")
            raise CallIntakeError("grant_expired")
        if self._phase == "local_stopped":
            raise CallIntakeError("session_stopped")
        try:
            current = self._authority is not None and self._authority.is_current(
                self._grant.binding, self._grant.grant_id, now
            )
        except Exception:
            current = False
        if current is not True:
            self._clear("local_stopped")
            raise CallIntakeError("authority_unavailable")

    def _bound(self, binding: CallBinding, observed_at: datetime, now: datetime) -> None:
        self._guard(now)
        if binding != self._grant.binding:
            raise CallIntakeError("binding_mismatch")
        if not self._grant.issued_at <= observed_at <= now:
            raise CallIntakeError("event_time_invalid")

    def admit_consent(self, consent: ParticipantConsent, *, now: datetime) -> None:
        self._bound(consent.binding, consent.observed_at, now)
        if (
            consent.grant_id != self._grant.grant_id
            or consent.notice_digest != self._grant.notice_digest
        ):
            raise CallIntakeError("consent_mismatch")
        if self._consent == consent and self._phase == "intake":
            return
        if self._phase != "awaiting_consent":
            raise CallIntakeError("consent_state_invalid")
        self._consent = consent
        self._phase = "intake"

    def admit_segment(self, segment: ExternalCallSegment, *, now: datetime) -> None:
        self._bound(segment.binding, segment.observed_at, now)
        if self._phase != "intake" or self._consent is None:
            raise CallIntakeError("participant_consent_required")
        if (
            segment.participant_ref != self._consent.participant_ref
            or segment.observed_at < self._consent.observed_at
        ):
            raise CallIntakeError("participant_or_consent_mismatch")
        for previous in self._segments:
            if previous.segment_id == segment.segment_id:
                if previous == segment:
                    return
                self._clear("local_stopped")
                raise CallIntakeError("segment_conflict")
        if segment.sequence != len(self._segments) + 1:
            raise CallIntakeError("segment_sequence_invalid")
        if (
            len(self._segments) >= MAX_SEGMENTS
            or sum(len(item.text.encode("utf-8")) for item in self._segments)
            + len(segment.text.encode("utf-8"))
            > MAX_TEXT_BYTES
        ):
            self._clear("local_stopped")
            raise CallIntakeError("intake_limit_reached")
        self._segments.append(segment)

    def accept_end(self, receipt: CallEndReceipt, *, now: datetime) -> None:
        self._bound(receipt.binding, receipt.observed_at, now)
        if self._end == receipt:
            return
        if self._phase == "ended":
            raise CallIntakeError("terminal_receipt_conflict")
        latest = max(
            [self._grant.issued_at]
            + ([self._consent.observed_at] if self._consent is not None else [])
            + [item.observed_at for item in self._segments]
            + ([self._end.observed_at] if self._end is not None else [])
        )
        if receipt.observed_at < latest:
            raise CallIntakeError("terminal_time_invalid")
        if receipt.status == "unknown":
            self._clear("remote_end_unknown")
        else:
            self._phase = "ended"
        self._end = receipt

    def propose_followup(
        self,
        request_id: UUID,
        kind: FollowupKind,
        segment_ids: tuple[UUID, ...],
        *,
        now: datetime,
    ) -> UUID:
        """Select attributed material for local review, without semantic interpretation."""
        self._guard(now)
        if self._phase != "ended" or not self._segments:
            raise CallIntakeError("confirmed_end_and_content_required")
        if (
            not isinstance(request_id, UUID)
            or request_id.int == 0
            or kind not in {"owner_callback_review", "owner_calendar_review"}
            or type(segment_ids) is not tuple
            or not 1 <= len(segment_ids) <= MAX_SEGMENTS
            or any(not isinstance(item, UUID) or item.int == 0 for item in segment_ids)
            or len(set(segment_ids)) != len(segment_ids)
        ):
            raise CallIntakeError("followup_selection_invalid")
        selected = {item.segment_id: item for item in self._segments}
        if any(item not in selected for item in segment_ids):
            raise CallIntakeError("followup_selection_invalid")
        if self._proposal is not None:
            if self._proposal.request_id != request_id:
                raise CallIntakeError("proposal_limit_reached")
            if self._proposal.kind != kind or self._proposal.segment_ids != segment_ids:
                raise CallIntakeError("request_id_conflict")
            return request_id
        self._proposal = CallFollowupProposal(
            request_id=request_id,
            binding=self._grant.binding,
            grant_id=self._grant.grant_id,
            kind=kind,
            segment_ids=segment_ids,
            untrusted_fragments=tuple(selected[item].text for item in segment_ids),
            created_at=now,
            expires_at=self._grant.expires_at,
        )
        return request_id

    def resolve_proposal(self, request_id: UUID, *, now: datetime) -> CallFollowupProposal:
        self._guard(now)
        if self._proposal is None or self._proposal.request_id != request_id:
            raise CallIntakeError("proposal_unavailable")
        return self._proposal

    def metadata(self, *, now: datetime) -> dict[str, str | int | bool]:
        self._guard(now)
        return {
            "phase": self._phase,
            "segment_count": len(self._segments),
            "proposal_present": self._proposal is not None,
            "remote_termination_confirmed": self._phase == "ended",
            "external_effects_executed": 0,
        }

    def stop(self) -> None:
        """Stop local intake only; does not claim a remote hangup or dispatch any effect."""
        self._clear("local_stopped")
