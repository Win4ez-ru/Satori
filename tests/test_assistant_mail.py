"""Offline exact-mail capability, race, freshness and metadata-only audit checks."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant import mail as mail_module
from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import (
    MailAccessError,
    MailAccountRef,
    MailAuditEvent,
    MailCatalog,
    MailClock,
    MailReadRequest,
    MailReadResult,
    MailSelectionProof,
    MailSourceRef,
)
from satori.application.assistant.mail_projection import project_gmail_batch


class Clock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 10, 20, tzinfo=UTC)
        self.monotonic = 1000.0

    def __call__(self) -> MailClock:
        return MailClock(wall=self.wall, monotonic=self.monotonic)

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds


def principal() -> LocalPrincipal:
    return LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())


def account(subject: str = "account-A") -> MailAccountRef:
    return MailAccountRef(
        subject=subject, client_registration="satori-desktop.apps.googleusercontent.com"
    )


def raw_message(message_id: str = "m1", text: str = "Synthetic meeting at 10.") -> bytes:
    return json.dumps(
        {
            "id": message_id,
            "threadId": "shared-thread",
            "payload": {
                "partId": "0",
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Unverified Sender <synthetic@example.test>"},
                    {"name": "Subject", "value": "Synthetic subject"},
                ],
                "body": {
                    "data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("="),
                    "size": len(text.encode()),
                },
            },
        }
    ).encode()


def catalog(owner: MailAccessCoordinator, clock: Clock, subject: str = "account-A") -> MailCatalog:
    return MailCatalog(
        principal=owner.principal,
        account=account(subject),
        catalog_id=uuid4(),
        observed=clock(),
        message_ids=("m1", "m2"),
    )


def connected(
    *, lifetime: timedelta = timedelta(hours=1)
) -> tuple[MailAccessCoordinator, Clock, list[MailAuditEvent]]:
    clock = Clock()
    events: list[MailAuditEvent] = []
    owner = MailAccessCoordinator(principal(), clock=clock, audit=events.append)
    owner.admit_catalog(catalog(owner, clock))
    selection = owner.select(("m1",), confirmed=True)
    owner.issue(selection, confirmed=True, lifetime=lifetime)
    return owner, clock, events


def result_for(request: MailReadRequest, clock: Clock) -> MailReadResult:
    selection = request.grant.selection
    return MailReadResult(
        request_id=request.request_id,
        principal=selection.principal,
        account=selection.account,
        generation=selection.generation,
        selection_revision=selection.revision,
        observed=clock(),
        messages=(raw_message(),),
    )


def test_offline_owner_starts_empty_and_reuses_exact_calendar_principal() -> None:
    clock = Clock()
    identity = principal()
    owner = MailAccessCoordinator(identity, clock=clock)
    assert owner.principal is identity
    assert owner.grant is None
    assert owner.catalog is None
    assert owner.snapshot is None
    with pytest.raises(MailAccessError, match="grant_required"):
        owner.prepare_read()


def test_strict_frozen_boundaries_hide_sensitive_input() -> None:
    item = account()
    with pytest.raises(ValidationError):
        item.subject = "changed"
    with pytest.raises(ValidationError) as raised:
        MailAccountRef.model_validate(
            {"subject": "PRIVATE*VALUE", "client_registration": "client", "extra": "private"}
        )
    assert "PRIVATE" not in str(raised.value)
    with pytest.raises(ValidationError):
        MailAccountRef.model_validate(
            {"subject": "s", "client_registration": "c", "provider": "imap"}
        )
    assert "account-A" not in repr(item)


@pytest.mark.parametrize(
    "ids", [(), ("m1", "m1"), ("*",), ("unknown",), tuple(f"m{i}" for i in range(11))]
)
def test_selection_requires_exact_unique_ids_from_catalog(ids: tuple[str, ...]) -> None:
    owner, _, _ = connected()
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select(ids, confirmed=True)
    assert owner.grant is None


def test_selection_and_grant_require_explicit_confirmation() -> None:
    owner, clock, _ = connected()
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select(("m1",), confirmed=False)
    selection = owner.select(("m1",), confirmed=True)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.issue(selection, confirmed=False)
    owner.issue(selection, confirmed=True)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.issue(selection, confirmed=True)
    clock.advance(1)
    assert owner.grant is not None


def test_knowing_id_or_forging_selection_does_not_issue_grant() -> None:
    owner, _, _ = connected()
    selection = owner.select(("m1",), confirmed=True)
    forged = selection.model_copy(update={"selection_id": uuid4()})
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.issue(forged, confirmed=True)
    owner.issue(selection, confirmed=True)


def test_cross_account_selection_dto_rejected() -> None:
    with pytest.raises(ValidationError):
        MailSelectionProof(
            principal=principal(),
            account=account(),
            catalog_id=uuid4(),
            selection_id=uuid4(),
            generation=1,
            revision=1,
            resources=(MailSourceRef(account=account("B"), message_id="m1"),),
        )


@pytest.mark.parametrize("seconds", [0, -1, 3601])
def test_grant_lifetime_is_bounded(seconds: int) -> None:
    owner, _, _ = connected()
    selection = owner.select(("m1",), confirmed=True)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.issue(selection, confirmed=True, lifetime=timedelta(seconds=seconds))
    assert owner.grant is None


def test_catalog_expiry_and_catalog_refresh_fence_old_selection() -> None:
    owner, clock, _ = connected()
    selection = owner.select(("m1",), confirmed=True)
    clock.advance(300)
    with pytest.raises(MailAccessError, match="catalog_expired"):
        owner.issue(selection, confirmed=True)
    owner.admit_catalog(catalog(owner, clock))
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.issue(selection, confirmed=True)


@pytest.mark.parametrize("field", ["principal", "observed"])
def test_invalid_catalog_invalidates_existing_grant(field: str) -> None:
    owner, clock, _ = connected()
    original = catalog(owner, clock)
    update = (
        principal()
        if field == "principal"
        else MailClock(wall=clock.wall + timedelta(seconds=1), monotonic=clock.monotonic)
    )
    bad = original.model_copy(update={field: update})
    with pytest.raises(MailAccessError):
        owner.admit_catalog(bad)
    assert owner.grant is None
    assert owner.snapshot is None


def test_single_response_admission_has_exact_source_spans() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    result = result_for(request, clock)
    snapshot = owner.accept_result(request, result)
    assert snapshot.messages[0].source == request.grant.selection.resources[0]
    assert snapshot.messages[0].processing_eligible
    assert snapshot.messages[0].spans[0].start == 0
    assert snapshot.messages[0].spans[0].end == len(snapshot.messages[0].text)
    assert owner.snapshot == snapshot
    with pytest.raises(MailAccessError, match="stale_request"):
        owner.accept_result(request, result)


def test_one_pending_read_prevents_parallel_request() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    with pytest.raises(MailAccessError, match="stale_request"):
        owner.prepare_read()
    assert owner.accept_result(request, result_for(request, clock)).messages


@pytest.mark.parametrize(
    "field", ["request_id", "principal", "account", "generation", "selection_revision"]
)
def test_response_correlation_mismatch_is_consumed(field: str) -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    good = result_for(request, clock)
    changes: dict[str, object] = {
        "request_id": uuid4(),
        "principal": principal(),
        "account": account("B"),
        "generation": request.grant.selection.generation + 1,
        "selection_revision": request.grant.selection.revision + 1,
    }
    bad = good.model_copy(update={field: changes[field]})
    with pytest.raises(MailAccessError, match="result_invalid"):
        owner.accept_result(request, bad)
    assert owner.snapshot is None
    with pytest.raises(MailAccessError, match="stale_request"):
        owner.accept_result(request, good)


def test_colliding_message_ids_after_account_switch_cannot_restore_old_result() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    old_result = result_for(request, clock)
    owner.admit_catalog(catalog(owner, clock, "B"))
    selection = owner.select(("m1",), confirmed=True)
    owner.issue(selection, confirmed=True)
    with pytest.raises(MailAccessError, match="stale_request"):
        owner.accept_result(request, old_result)
    assert owner.snapshot is None


def test_revocation_and_restart_have_no_restored_grants_or_payloads() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    result = result_for(request, clock)
    owner.revoke()
    with pytest.raises(MailAccessError, match="grant_required"):
        owner.accept_result(request, result)
    restarted = MailAccessCoordinator(owner.principal, clock=clock)
    assert restarted.grant is None
    assert restarted.snapshot is None
    assert owner.catalog is None


@pytest.mark.parametrize("clock_kind", ["wall", "monotonic", "both"])
def test_grant_expiry_at_either_clock_boundary(clock_kind: str) -> None:
    owner, clock, _ = connected(lifetime=timedelta(seconds=20))
    if clock_kind in {"wall", "both"}:
        clock.wall += timedelta(seconds=20)
    if clock_kind in {"monotonic", "both"}:
        clock.monotonic += 20
    assert owner.grant is None
    assert owner.snapshot is None
    assert owner.catalog is None


@pytest.mark.parametrize("kind", ["wall", "monotonic"])
def test_clock_rollback_invalidates_all_capabilities(kind: str) -> None:
    owner, clock, _ = connected()
    if kind == "wall":
        clock.wall -= timedelta(seconds=1)
    else:
        clock.monotonic -= 1
    with pytest.raises(MailAccessError, match="clock_invalid"):
        owner.prepare_read()
    clock.advance(2)
    assert owner.grant is None
    assert owner.catalog is None


def test_read_request_expires_after_thirty_seconds() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    result = result_for(request, clock)
    clock.advance(30)
    with pytest.raises(MailAccessError, match="request_expired"):
        owner.accept_result(request, result)
    assert owner.snapshot is None


def test_preview_expiry_uses_observation_and_never_outlives_grant() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    result = result_for(request, clock)
    clock.advance(5)
    snapshot = owner.accept_result(request, result)
    assert snapshot.expires_at == result.observed.wall + timedelta(seconds=300)
    clock.advance(295)
    assert owner.snapshot is None
    assert owner.grant is not None


def test_short_grant_also_shortens_request_and_snapshot() -> None:
    owner, clock, _ = connected(lifetime=timedelta(seconds=10))
    request = owner.prepare_read()
    snapshot = owner.accept_result(request, result_for(request, clock))
    assert request.expires_at == request.grant.expires_at == snapshot.expires_at


@pytest.mark.parametrize("revoke", [True, False])
def test_permission_is_rechecked_after_parser(
    monkeypatch: pytest.MonkeyPatch, revoke: bool
) -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    result = result_for(request, clock)
    original = project_gmail_batch

    def changed(*args: object, **kwargs: object) -> object:
        value = original(result.messages, request.grant.selection.resources)
        if revoke:
            owner.revoke()
        else:
            clock.advance(30)
        return value

    monkeypatch.setattr(mail_module, "project_gmail_batch", changed)
    with pytest.raises(MailAccessError):
        owner.accept_result(request, result)
    assert owner.snapshot is None


def test_wrong_message_and_implicit_thread_expansion_are_rejected() -> None:
    owner, clock, _ = connected()
    request = owner.prepare_read()
    bad = result_for(request, clock).model_copy(update={"messages": (raw_message("m2"),)})
    with pytest.raises(MailAccessError):
        owner.accept_result(request, bad)
    assert owner.snapshot is None


def test_audit_contains_only_closed_metadata() -> None:
    owner, clock, events = connected()
    request = owner.prepare_read()
    owner.accept_result(request, result_for(request, clock))
    serialized = "\n".join(event.model_dump_json() for event in events)
    for secret in (
        "account-A",
        "example.test",
        "Synthetic",
        "satori-desktop",
        "m1",
        "shared-thread",
    ):
        assert secret not in serialized
    assert events[-1].event == "read_admitted"
    with pytest.raises(ValidationError):
        MailAuditEvent.model_validate(
            {
                "event": "read_admitted",
                "occurred_at": clock.wall,
                "generation": 1,
                "body": "private",
            }
        )


@pytest.mark.parametrize("event_name", ["grant_issued", "read_admitted"])
def test_audit_failure_clears_grant_and_payload(event_name: str) -> None:
    clock = Clock()

    def unavailable(event: MailAuditEvent) -> None:
        if event.event == event_name:
            raise RuntimeError("raw sensitive backend error")

    owner = MailAccessCoordinator(principal(), clock=clock, audit=unavailable)
    owner.admit_catalog(catalog(owner, clock))
    selection = owner.select(("m1",), confirmed=True)
    if event_name == "grant_issued":
        with pytest.raises(MailAccessError, match="audit_unavailable"):
            owner.issue(selection, confirmed=True)
    else:
        owner.issue(selection, confirmed=True)
        request = owner.prepare_read()
        with pytest.raises(MailAccessError, match="audit_unavailable"):
            owner.accept_result(request, result_for(request, clock))
    assert owner.grant is None
    assert owner.snapshot is None
    assert owner.catalog is None


def test_lost_timeout_can_start_new_read_without_admitting_late_old_result() -> None:
    owner, clock, _ = connected()
    old_request = owner.prepare_read()
    old_result = result_for(old_request, clock)
    clock.advance(30)
    current = owner.prepare_read()
    with pytest.raises(MailAccessError, match="stale_request"):
        owner.accept_result(old_request, old_result)
    snapshot = owner.accept_result(current, result_for(current, clock))
    assert snapshot.request.request_id == current.request_id


@pytest.mark.parametrize(
    "target",
    ["selection_confirmed", "grant_issued", "read_prepared", "read_admitted", "read_rejected"],
)
def test_reentrant_audit_revocation_cannot_return_or_dispatch_capability(target: str) -> None:
    clock = Clock()
    armed = False
    owner: MailAccessCoordinator

    def revoking(event: MailAuditEvent) -> None:
        nonlocal armed
        if armed and event.event == target:
            armed = False
            owner.revoke()

    owner = MailAccessCoordinator(principal(), clock=clock, audit=revoking)
    owner.admit_catalog(catalog(owner, clock))
    armed = target == "selection_confirmed"
    if armed:
        with pytest.raises(MailAccessError):
            owner.select(("m1",), confirmed=True)
    else:
        selection = owner.select(("m1",), confirmed=True)
        armed = target == "grant_issued"
        if armed:
            with pytest.raises(MailAccessError):
                owner.issue(selection, confirmed=True)
        else:
            owner.issue(selection, confirmed=True)
            armed = target == "read_prepared"
            if armed:
                with pytest.raises(MailAccessError):
                    owner.prepare_read()
            else:
                request = owner.prepare_read()
                if target == "read_rejected":
                    clock.advance(30)
                    armed = True
                    with pytest.raises(MailAccessError):
                        owner.prepare_read()
                else:
                    armed = True
                    with pytest.raises(MailAccessError):
                        owner.accept_result(request, result_for(request, clock))
    assert owner.grant is None
    assert owner.snapshot is None


def test_audit_clock_advance_cannot_return_expired_snapshot() -> None:
    clock = Clock()

    def advancing(event: MailAuditEvent) -> None:
        if event.event == "read_admitted":
            clock.advance(300)

    owner = MailAccessCoordinator(principal(), clock=clock, audit=advancing)
    owner.admit_catalog(catalog(owner, clock))
    owner.issue(owner.select(("m1",), confirmed=True), confirmed=True)
    request = owner.prepare_read()
    with pytest.raises(MailAccessError, match="snapshot_expired"):
        owner.accept_result(request, result_for(request, clock))
    assert owner.snapshot is None


def test_arbitrary_clock_exception_is_closed_and_invalidates_grant() -> None:
    clock = Clock()
    broken = False

    def failing_clock() -> MailClock:
        if broken:
            raise OSError("PRIVATE_BACKEND_ERROR")
        return clock()

    owner = MailAccessCoordinator(principal(), clock=failing_clock)
    owner.admit_catalog(catalog(owner, clock))
    owner.issue(owner.select(("m1",), confirmed=True), confirmed=True)
    broken = True
    with pytest.raises(MailAccessError, match="clock_invalid") as raised:
        owner.prepare_read()
    assert "PRIVATE" not in str(raised.value)
    broken = False
    assert owner.grant is None
    assert owner.catalog is None


def test_reentrant_timeout_retry_keeps_nested_request_as_current() -> None:
    clock = Clock()
    nested: list[MailReadRequest] = []
    armed = False
    owner: MailAccessCoordinator

    def retrying(event: MailAuditEvent) -> None:
        nonlocal armed
        if armed and event.event == "read_rejected" and event.reason == "request_expired":
            armed = False
            nested.append(owner.prepare_read())

    owner = MailAccessCoordinator(principal(), clock=clock, audit=retrying)
    owner.admit_catalog(catalog(owner, clock))
    owner.issue(owner.select(("m1",), confirmed=True), confirmed=True)
    owner.prepare_read()
    clock.advance(30)
    armed = True
    with pytest.raises(MailAccessError, match="stale_request"):
        owner.prepare_read()
    current = nested[0]
    assert owner.accept_result(current, result_for(current, clock)).request == current


@pytest.mark.parametrize("monotonic", [-1.0, float("nan"), float("inf")])
def test_clock_dto_rejects_invalid_monotonic_samples(monotonic: float) -> None:
    with pytest.raises(ValidationError):
        MailClock(wall=datetime(2026, 10, 20, tzinfo=UTC), monotonic=monotonic)


def test_catalog_uuid_and_exact_resource_tuple_are_strict() -> None:
    owner, clock, _ = connected()
    values = catalog(owner, clock).model_dump()
    values["catalog_id"] = UUID(int=0)
    with pytest.raises(ValidationError):
        MailCatalog.model_validate(values)
    values = catalog(owner, clock).model_dump()
    values["message_ids"] = ["m1"]
    with pytest.raises(ValidationError):
        MailCatalog.model_validate(values)
