"""Faithful private subprocess fixtures and actual Swift offline parsing only.

No fixture invokes EventKit or calendar_native's production entry point. The new
Swift binary is run only with --offline-probe, which does not construct a store.
"""

import asyncio
import base64
import errno
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionError,
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarCreateInput,
    calendar_action_digest,
)
from satori.application.assistant.contracts import (
    CalendarReadRequest,
    CalendarSourceRef,
    LocalPrincipal,
)
from satori.infrastructure.assistant import calendar_actions_eventkit as native
from satori.infrastructure.assistant.calendar_actions_eventkit import (
    EventKitCalendarActionProvider,
    EventKitCalendarReadProvider,
)
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)


def attempt(tmp_path: Path, *, title: str = "PUBLIC SYNTHETIC MEETING") -> CalendarCreateAttempt:
    bound = CalendarActionScope(
        identity_id=uuid4(),
        profile_id=uuid4(),
        session_id=uuid4(),
        generation=uuid4(),
        provider="macos_eventkit",
        account_id="public-account",
        calendar_id="public-calendar",
        timezone="Europe/Moscow",
    )
    observed = CalendarActionCatalog(
        scope=bound,
        revision=uuid4(),
        observed_at=NOW,
        full_access=True,
        writable=True,
        supports_events=True,
        calendar_title="PUBLIC CALENDAR",
        account_title="PUBLIC ACCOUNT",
    )
    command = CalendarCreateInput(
        request_id=uuid4(),
        context_digest="a" * 64,
        title=title,
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=1, hours=1),
        timezone="Europe/Moscow",
    )
    owner = CalendarActionOwner(
        SQLiteCalendarActionStore(tmp_path / (str(uuid4()) + ".sqlite3")),
        EventKitCalendarActionProvider(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    proposed = owner.propose(command, scope=bound, catalog=observed)
    approved = owner.approve(
        command.request_id, proposed.proposal.review_digest, scope=bound, catalog=observed
    )
    assert approved.approval is not None
    result = CalendarCreateAttempt(
        attempt_id=uuid4(),
        proposal=approved.proposal,
        approval=approved.approval,
        idempotency_token=calendar_action_digest(
            {
                "schema": "satori.calendar-create-attempt.v1",
                "request_id": str(command.request_id),
                "payload_digest": proposed.proposal.payload_digest,
            }
        ),
        claimed_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    owner.close()
    return result


_SCRIPT = r"""import json, os, sys, time
from pathlib import Path

mode = MODE
log = Path(LOG)
log.write_text(str(os.getpid()) + "\n")
first = sys.stdin.readline()
if not first:
    sys.exit(0)
r = json.loads(first)
with log.open("a") as f:
    f.write("prepare\n")


def out(status, operation, **extra):
    d = {
        "schema_version": 1,
        "attempt_id": r["attempt_id"],
        "idempotency_token": r["idempotency_token"],
        "payload_digest": r["payload_digest"],
        "status": status,
        "operation": operation,
        "observed_at": "2026-11-03T09:00:01Z",
    }
    d.update(extra)
    print(json.dumps(d), flush=True)


if r["operation"] == "lookup":
    with log.open("a") as f:
        f.write("lookup\n")
    if mode == "lookup_exact":
        out(
            "owned_exact",
            "lookup",
            event_id="public-event",
            exact_fields_verified=True,
            fresh_store_verified=True,
        )
    else:
        out(mode.removeprefix("lookup_"), "lookup", fresh_store_verified=mode != "lookup_unknown")
    sys.exit(0)
if mode == "sleep":
    time.sleep(10)
    sys.exit(0)
if mode == "pre_reject":
    out("rejected", "prepare", reason="calendar_action_permission_required")
    sys.exit(0)
if mode == "bad_ready":
    out("ready", "prepare", nonce="11111111-1111-4111-8111-111111111111", payload_digest="b" * 64)
    sys.exit(0)
if mode == "oversize":
    print("x" * 40000, flush=True)
    sys.exit(0)
out("ready", "prepare", nonce="11111111-1111-4111-8111-111111111111")
second = sys.stdin.readline()
if not second:
    sys.exit(0)
c = json.loads(second)
assert c["operation"] == "commit" and c["attempt_id"] == r["attempt_id"]
assert c["nonce"] == "11111111-1111-4111-8111-111111111111"
assert sys.stdin.read() == ""
with log.open("a") as f:
    f.write("commit\n")
if mode == "lost_after_commit":
    sys.exit(0)
out(
    "created",
    "commit",
    event_id="public-event",
    exact_fields_verified=mode != "unverified",
    fresh_store_verified=True,
)
if mode == "trailing":
    print("unexpected", flush=True)
"""


def helper(tmp_path: Path, mode: str) -> tuple[Path, Path]:
    directory = tmp_path / str(uuid4())
    directory.mkdir(mode=0o700)
    path, log = directory / "fixture", directory / "public-log"
    text = _SCRIPT.replace("MODE", repr(mode)).replace("LOG", repr(str(log)))
    path.write_text("#!" + sys.executable + "\n" + text)
    path.chmod(0o700)
    return path.resolve(), log


def invoked(log: Path) -> list[str]:
    return log.read_text().splitlines()[1:] if log.exists() else []


def test_create_performs_ready_guard_commit_then_verified_local_receipt(tmp_path: Path) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "normal")
    calls: list[str] = []

    def last_guard() -> None:
        assert invoked(log) == ["prepare"]
        calls.append("guard")

    receipt = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2)).create(
            value, before_effect=last_guard
        )
    )
    assert receipt.status == "created"
    assert receipt.exact_fields_verified
    assert receipt.fresh_store_verified
    assert receipt.remote_sync_verified is False
    assert calls == ["guard"]
    assert invoked(log) == ["prepare", "commit"]


@pytest.mark.parametrize("mode", ["pre_reject", "bad_ready", "oversize"])
def test_pre_effect_reject_or_bad_frame_never_calls_guard_or_commits(
    tmp_path: Path, mode: str
) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, mode)
    calls: list[int] = []
    result = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2)).create(
            value, before_effect=lambda: calls.append(1)
        )
    )
    assert result.status == "rejected"
    assert calls == []
    assert "commit" not in invoked(log)


def test_revoked_at_ready_is_a_policy_refusal_before_commit(tmp_path: Path) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "normal")

    def revoked() -> None:
        raise CalendarActionError("calendar_action_revoked")

    with pytest.raises(CalendarActionError, match="revoked"):
        asyncio.run(
            EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2)).create(
                value, before_effect=revoked
            )
        )
    assert "commit" not in invoked(log)


@pytest.mark.parametrize("mode", ["lost_after_commit", "trailing", "unverified"])
def test_uncertain_or_malformed_after_commit_stays_unknown_without_replay(
    tmp_path: Path, mode: str
) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, mode)
    calls: list[int] = []
    result = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2)).create(
            value, before_effect=lambda: calls.append(1)
        )
    )
    assert result.status == "unknown"
    assert calls == [1]
    assert invoked(log).count("commit") == 1


def test_closed_policy_error_after_commit_is_unknown_not_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "normal")
    provider = EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2))
    original = provider._reply

    def final_error(raw: bytes, received: CalendarCreateAttempt) -> native._ActionReply:
        result = original(raw, received)
        if result.operation == "commit":
            raise CalendarActionError("calendar_action_provider_unavailable")
        return result

    monkeypatch.setattr(provider, "_reply", final_error)
    result = asyncio.run(provider.create(value, before_effect=lambda: None))
    assert result.status == "unknown"
    assert result.reason == "calendar_action_unknown_no_replay"
    assert invoked(log).count("commit") == 1


def test_timeout_closes_only_owned_child_and_never_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "sleep")
    monkeypatch.setattr(native, "_MAX_TIMEOUT", 1.0)
    result = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2)).create(
            value, before_effect=lambda: pytest.fail("must not commit")
        )
    )
    assert result.status == "rejected"
    assert "commit" not in invoked(log)
    pid = int(log.read_text().splitlines()[0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancellation_disposes_owned_child(tmp_path: Path) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "sleep")

    async def run() -> None:
        task = asyncio.create_task(
            EventKitCalendarActionProvider(path, clock=lambda: NOW).create(
                value, before_effect=lambda: None
            )
        )
        for _ in range(100):
            if log.exists():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    pid = int(log.read_text().splitlines()[0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("unsafe", ["missing", "symlink", "writable"])
def test_unqualified_helper_is_unavailable_without_effect(tmp_path: Path, unsafe: str) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "normal")
    if unsafe == "missing":
        selected = path.with_name("missing")
    elif unsafe == "symlink":
        selected = path.with_name("link")
        selected.symlink_to(path)
    else:
        path.chmod(0o720)
        selected = path
    result = asyncio.run(
        EventKitCalendarActionProvider(selected, clock=lambda: NOW).create(
            value, before_effect=lambda: pytest.fail("unavailable")
        )
    )
    assert result.status == "rejected"
    assert result.reason == "calendar_action_provider_unavailable"
    assert not log.exists()


def test_linux_never_spawns_or_calls_effect_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "normal")
    monkeypatch.setattr(sys, "platform", "linux")
    result = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW).create(
            value, before_effect=lambda: pytest.fail("Linux unavailable")
        )
    )
    assert result.status == "rejected"
    assert not log.exists()


@pytest.mark.parametrize(
    "mode", ["lookup_exact", "lookup_absent", "lookup_conflict", "lookup_unknown"]
)
def test_lookup_is_read_only_and_does_not_rearm_old_attempt(tmp_path: Path, mode: str) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, mode)
    calls: list[int] = []
    result = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(days=1)).lookup(
            value, value.proposal.scope, before_read=lambda: calls.append(1)
        )
    )
    assert result.status == {"lookup_exact": "owned_exact"}.get(mode, mode.removeprefix("lookup_"))
    assert calls == [1]
    assert invoked(log) == ["prepare", "lookup"]
    assert result.fresh_store_verified is (mode != "lookup_unknown")


def test_lookup_refuses_another_calendar_without_process(tmp_path: Path) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "lookup_absent")
    other = value.proposal.scope.model_copy(update={"calendar_id": "another"})
    with pytest.raises(CalendarActionError, match="scope_mismatch"):
        asyncio.run(
            EventKitCalendarActionProvider(path, clock=lambda: NOW).lookup(
                value, other, before_read=lambda: pytest.fail("wrong calendar")
            )
        )
    assert not log.exists()


def read_request() -> CalendarReadRequest:
    return CalendarReadRequest(
        request_id=uuid4(),
        principal=LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4()),
        grant_id=uuid4(),
        generation=1,
        resources=(CalendarSourceRef(account_id="public-account", calendar_id="public-calendar"),),
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=2),
        timezone="Europe/Moscow",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )


def read_helper(tmp_path: Path, mode: str) -> Path:
    directory = tmp_path / str(uuid4())
    directory.mkdir(mode=0o700)
    path = directory / "read-fixture"
    script = r"""import json, sys

r = json.loads(sys.stdin.read())
assert r["operation"] == "read_events" and set(r) == {
    "schema_version",
    "request_id",
    "operation",
    "selected_calendars",
    "start",
    "end",
    "time_zone",
    "max_events",
}
s = r["selected_calendars"][0]
row = {
    "source_id": s["source_id"],
    "calendar_id": s["calendar_id"],
    "event_id": "public-event",
    "occurrence_start": "2026-11-04T09:00:00Z",
    "title": "PUBLIC READ",
    "start": "2026-11-04T09:00:00Z",
    "end": "2026-11-04T10:00:00Z",
    "all_day": False,
    "start_date": None,
    "end_date": None,
    "time_zone": "Europe/Moscow",
    "status": "confirmed",
}
mode = MODE
if mode == "wrong_source":
    row["calendar_id"] = "wrong"
if mode == "invalid_time":
    row["end"] = row["start"]
if mode == "invalid_all_day":
    row["all_day"] = True
reply = {
    "schema_version": 1,
    "request_id": r["request_id"],
    "operation": "read_events",
    "status": "ok",
    "access": "full_access",
    "error": None,
    "observed_at": "2026-11-03T09:00:01Z",
    "events": [row],
    "partial": mode == "partial",
}
if mode == "denied":
    reply.update(status="error", access="denied", error="permission_denied")
    reply.pop("events")
    reply.pop("partial")
if mode == "raw_error":
    reply.update(status="error", error="UNTRUSTED RAW PRIVATE ERROR")
    reply.pop("events")
    reply.pop("partial")
encoded = json.dumps(reply)
if mode == "duplicate":
    encoded = encoded.replace('"schema_version": 1', '"schema_version": 1,"schema_version": 1')
print(encoded)
"""
    path.write_text("#!" + sys.executable + "\n" + script.replace("MODE", repr(mode)))
    path.chmod(0o700)
    return path.resolve()


@pytest.mark.parametrize(
    "mode",
    [
        "normal",
        "partial",
        "wrong_source",
        "duplicate",
        "denied",
        "invalid_time",
        "invalid_all_day",
        "raw_error",
    ],
)
def test_noqt_read_port_only_uses_existing_read_wire_and_returns_typed_result(
    tmp_path: Path, mode: str
) -> None:
    request = read_request()
    calls: list[int] = []
    result = asyncio.run(
        EventKitCalendarReadProvider(
            read_helper(tmp_path, mode), clock=lambda: NOW + timedelta(seconds=2)
        ).read_events(request, before_read=lambda: calls.append(1))
    )
    expected = {"normal": "ok", "partial": "partial", "denied": "permission_denied"}.get(
        mode, "error"
    )
    assert result.status == expected
    assert calls == [1]
    if expected in {"ok", "partial"}:
        assert result.occurrences[0].source == request.resources[0]
        assert result.occurrences[0].starts_at == NOW + timedelta(days=1)
    else:
        assert result.occurrences == ()


@pytest.fixture(scope="module")
def swift_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if platform.system() != "Darwin" or shutil.which("xcrun") is None:
        pytest.skip("Actual account-free Swift parser requires macOS compiler")
    directory = tmp_path_factory.mktemp("generic-calendar-offline-probe")
    source = (
        Path(__file__).resolve().parents[1]
        / "src/satori/desktop/calendar_actions_native/main.swift"
    )
    binary = directory / "offline-probe"
    compiled = subprocess.run(
        [
            "/usr/bin/xcrun",
            "swiftc",
            "-swift-version",
            "5",
            "-module-cache-path",
            str(directory / "module-cache"),
            "-framework",
            "Foundation",
            "-framework",
            "EventKit",
            "-framework",
            "CryptoKit",
            str(source),
            "-o",
            str(binary),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr.decode()
    return binary


def run_probe(binary: Path, wire: dict[str, Any]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(binary), "--offline-probe"],
        input=json.dumps(wire).encode() + b"\n",
        capture_output=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("title", ["PUBLIC SYNTHETIC", "Синтетическая встреча 🧪"])
def test_actual_swift_offline_probe_checks_same_payload_and_token_without_eventstore(
    tmp_path: Path, swift_probe: Path, title: str
) -> None:
    value = attempt(tmp_path, title=title)
    wire = EventKitCalendarActionProvider(clock=lambda: NOW)._wire(value, "prepare")
    result = run_probe(swift_probe, wire)
    assert result.returncode == 0
    assert not result.stderr
    reply = json.loads(result.stdout)
    assert reply["status"] == "valid"
    assert reply["eventkit_opened"] is False
    assert reply["payload_digest"] == value.proposal.payload_digest


@pytest.mark.parametrize("change", ["hash", "token", "extra", "title", "guest", "timezone"])
def test_actual_swift_probe_rejects_changed_approval_or_unsupported_fields(
    tmp_path: Path, swift_probe: Path, change: str
) -> None:
    value = attempt(tmp_path)
    wire = EventKitCalendarActionProvider(clock=lambda: NOW)._wire(value, "prepare")
    if change == "hash":
        wire["payload_digest"] = "0" * 64
    elif change == "token":
        wire["idempotency_token"] = "0" * 64
    elif change == "extra":
        wire["delete"] = True
    else:
        encoded_material = wire["payload_json_base64"]
        assert isinstance(encoded_material, str)
        material = json.loads(base64.b64decode(encoded_material))
        if change == "title":
            material["command"]["title"] = "bad\nline"
        elif change == "guest":
            material["command"]["guests"] = ["synthetic@example.invalid"]
        else:
            material["command"]["timezone"] = "invalid/zone"
        body = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        wire["payload_json_base64"] = base64.b64encode(body).decode()
        wire["payload_digest"] = hashlib.sha256(body).hexdigest()
        wire["idempotency_token"] = calendar_action_digest(
            {
                "schema": "satori.calendar-create-attempt.v1",
                "request_id": str(value.proposal.command.request_id),
                "payload_digest": wire["payload_digest"],
            }
        )
    result = run_probe(swift_probe, wire)
    assert result.returncode != 0
    assert not result.stderr
    assert value.proposal.command.title.encode() not in result.stdout


class _CallbackLaggingOwnedProcess:
    """Keep a stale returncode until the real owned child has been awaited."""

    def __init__(self, owned: asyncio.subprocess.Process) -> None:
        self.owned = owned
        self.reaped = False

    @property
    def pid(self) -> int:
        return self.owned.pid

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        return self.owned.stdin

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self.owned.stdout

    @property
    def returncode(self) -> int | None:
        return self.owned.returncode if self.reaped else None

    async def wait(self) -> int:
        result = await self.owned.wait()
        self.reaped = True
        return result


@pytest.mark.parametrize("signal_error", [errno.EPERM, errno.ESRCH])
def test_unverified_commit_signal_race_reaps_owned_child_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signal_error: int
) -> None:
    value = attempt(tmp_path)
    path, log = helper(tmp_path, "unverified")
    original_spawn = native._checked_spawn
    owned: list[_CallbackLaggingOwnedProcess] = []
    signals: list[int] = []
    guards: list[int] = []

    async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        current = _CallbackLaggingOwnedProcess(await original_spawn(*args, **kwargs))
        owned.append(current)
        return cast(asyncio.subprocess.Process, current)

    def unavailable_group(pid: int, sig: int) -> None:
        assert pid == owned[0].pid
        signals.append(sig)
        raise OSError(signal_error, "synthetic owned group unavailable")

    monkeypatch.setattr(native, "_checked_spawn", spawn)
    monkeypatch.setattr(os, "killpg", unavailable_group)
    result = asyncio.run(
        EventKitCalendarActionProvider(path, clock=lambda: NOW + timedelta(seconds=2)).create(
            value, before_effect=lambda: guards.append(1)
        )
    )
    assert result.status == "unknown"
    assert guards == [1]
    assert invoked(log).count("commit") == 1
    assert len(owned) == 1
    assert owned[0].reaped
    assert owned[0].owned.returncode == 0
    assert signals == [signal.SIGTERM]


async def _release_controlled_cleanup_child(path: Path) -> asyncio.subprocess.Process:
    script = (
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "deadline=time.monotonic()+10; print('ready',flush=True)\n"
        "while not p.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert await asyncio.wait_for(process.stdout.readline(), timeout=3) == b"ready\n"
    return process


def test_term_denial_does_not_escalate_or_claim_unreaped_child_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals: list[int] = []

    async def scenario() -> None:
        release = tmp_path / "release-owned-child"
        process = await _release_controlled_cleanup_child(release)

        def denied(pid: int, sig: int) -> None:
            assert pid == process.pid
            signals.append(sig)
            raise PermissionError(errno.EPERM, "synthetic live child signal denial")

        monkeypatch.setattr(os, "killpg", denied)
        try:
            with pytest.raises(TimeoutError):
                await native._dispose(process)
            assert process.returncode is None
            assert signals == [signal.SIGTERM]
        finally:
            # Fixture-only natural completion; never another signal after denial.
            release.write_text("release")
            assert await asyncio.wait_for(process.wait(), timeout=3) == 0

    asyncio.run(scenario())


def test_kill_denial_still_requires_real_owned_child_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals: list[int] = []

    async def scenario() -> None:
        release = tmp_path / "release-owned-child"
        process = await _release_controlled_cleanup_child(release)

        def signals_for_owned_only(pid: int, sig: int) -> None:
            assert pid == process.pid
            signals.append(sig)
            if sig == signal.SIGKILL:
                release.write_text("release")
                raise PermissionError(errno.EPERM, "synthetic final signal race")

        monkeypatch.setattr(os, "killpg", signals_for_owned_only)
        try:
            await native._dispose(process)
            assert process.returncode == 0
            assert signals == [signal.SIGTERM, signal.SIGKILL]
        finally:
            release.write_text("release")
            assert await asyncio.wait_for(process.wait(), timeout=3) == 0

    asyncio.run(scenario())
