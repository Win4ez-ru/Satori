"""Durably deliver an admitted busy-call review to its local profile."""

import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from satori.application.assistant.contracts import LocalPrincipal, require_utc
from satori.application.calls.intake import (
    MAX_TEXT_BYTES,
    BusyMessageGrant,
    CallIntakeError,
    IntakeAuthority,
)
from satori.infrastructure.artifacts import ArtifactExistsError, PrivateArtifactStore
from satori.infrastructure.calls.busy import BusyCallHandler, BusyCallReview, BusyCallSpeech


class CallSummary(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    version: Literal[1] = 1
    profile_id: UUID
    call_key: UUID
    grant_id: UUID
    receipt_id: UUID
    received_at: datetime
    text: str = Field(min_length=1, max_length=MAX_TEXT_BYTES + 8, repr=False)

    _utc = field_validator("received_at")(require_utc)


class CallInbox:
    """One profile's private sink. Only the call handler can offer a completed review."""

    def __init__(self, profile: Path) -> None:
        self._profile = profile.expanduser().resolve()
        self._directory = self._profile / "call-inbox"
        self.principal = LocalPrincipal(
            os_uid=os.getuid(),
            profile_id=uuid5(NAMESPACE_URL, str(self._profile)),
            session_id=uuid4(),
        )
        self._files = PrivateArtifactStore()

    def busy_handler(
        self, grant: BusyMessageGrant, *, authority: IntakeAuthority, speech: BusyCallSpeech
    ) -> BusyCallHandler:
        if grant.binding.principal != self.principal:
            raise CallIntakeError("review_owner_mismatch")
        return BusyCallHandler(grant, authority=authority, speech=speech, deliver=self._deliver)

    def _prepare(self, *, create: bool) -> bool:
        if create:
            self._profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (self._profile, self._directory):
            if create and path == self._directory:
                path.mkdir(mode=0o700, exist_ok=True)
            try:
                meta = path.lstat()
            except FileNotFoundError:
                return False
            forbidden = 0o077 if path == self._directory else 0o022
            if (
                not stat.S_ISDIR(meta.st_mode)
                or meta.st_uid != os.getuid()
                or meta.st_mode & forbidden
            ):
                raise CallIntakeError("inbox_directory_not_private")
        return True

    def _read(self, path: Path) -> CallSummary:
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
            raise CallIntakeError("inbox_file_not_private")
        item = CallSummary.model_validate_json(self._files.read(path, max_bytes=65536))
        if item.profile_id != self.principal.profile_id or path.stem != str(item.call_key):
            raise CallIntakeError("inbox_record_mismatch")
        return item

    def entries(self) -> list[CallSummary]:
        if not self._prepare(create=False):
            return []
        records = (self._read(path) for path in self._directory.iterdir() if path.suffix == ".json")
        return sorted(records, key=lambda item: (item.received_at, item.call_key), reverse=True)

    def _deliver(self, review: BusyCallReview) -> None:
        self._prepare(create=True)
        proposal = review.resolve(self.principal, now=datetime.now(UTC))
        binding = proposal.binding
        if proposal.kind != "owner_callback_review":
            raise CallIntakeError("inbox_review_kind")
        # Stable across receipts, call legs and application sessions. Text is never the identity.
        key = uuid5(binding.provider_account_ref, f"{binding.route_id}/{binding.call_id}")
        summary = CallSummary(
            profile_id=self.principal.profile_id,
            call_key=key,
            grant_id=proposal.grant_id,
            receipt_id=proposal.request_id,
            received_at=proposal.created_at,
            text="\n".join(proposal.untrusted_fragments),
        )
        path = self._directory / f"{key}.json"
        try:
            self._files.write(path, summary.model_dump_json().encode())
        except ArtifactExistsError:
            if self._read(path).text != summary.text:
                raise CallIntakeError("inbox_call_conflict") from None
