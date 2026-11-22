"""Auth and namespace adversaries use only private synthetic temporary files."""

import ctypes
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import cast

import pytest

from satori.desktop.mobile_gateway.auth import (
    DEVICE_SECONDS,
    PAIRING_ATTEMPTS,
    AuthOwner,
)
from satori.desktop.mobile_gateway.contracts import GatewayFault, ScopeBinding
from satori.desktop.mobile_gateway.private_store import MAX_JSON_BYTES, PrivateStore


@dataclass
class Clock:
    wall: float = 1_800_000_000.0
    monotonic: float = 100.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.monotonic += seconds


@dataclass
class Scope:
    value: ScopeBinding = field(
        default_factory=lambda: ScopeBinding("principal-one", "profile-one", 1)
    )


@dataclass
class Fixture:
    directory: Path
    store: PrivateStore
    auth: AuthOwner
    clock: Clock
    scope: Scope

    def restart(self, transport: str = "certificate-one:443") -> None:
        self.store.close()
        self.store = PrivateStore(self.directory)
        self.auth = AuthOwner(
            self.store,
            lambda: self.scope.value,
            transport,
            clock=lambda: self.clock.wall,
            monotonic=lambda: self.clock.monotonic,
        )


@pytest.fixture
def owner(tmp_path: Path) -> Iterator[Fixture]:
    directory = tmp_path / "private"
    store = PrivateStore(directory)
    clock = Clock()
    scope = Scope()
    auth = AuthOwner(
        store,
        lambda: scope.value,
        "certificate-one:443",
        clock=lambda: clock.wall,
        monotonic=lambda: clock.monotonic,
    )
    fixture = Fixture(directory, store, auth, clock, scope)
    try:
        yield fixture
    finally:
        fixture.store.close()


def pair(owner: Fixture, capabilities: frozenset[str] = frozenset({"chat", "history"})) -> str:
    code = owner.auth.begin_pairing(capabilities)
    result = owner.auth.pair(code, "Synthetic phone")
    assert type(result["token"]) is str
    return result["token"]


def fault_code(call: Callable[[], object]) -> str:
    with pytest.raises(GatewayFault) as error:
        call()
    return error.value.code


def test_pair_wire_is_finite_and_only_explicit_capabilities(owner: Fixture) -> None:
    code = owner.auth.begin_pairing(frozenset({"chat"}))
    assert len(code) == 6
    assert code.isascii()
    assert code.isdecimal()
    result = owner.auth.pair(code, "Synthetic phone")
    assert set(result) == {"deviceID", "token", "expiresAt", "profileID", "capabilities"}
    assert result["capabilities"] == ["status", "chat"]
    assert str(result["expiresAt"]).endswith("Z")
    assert result["profileID"] == "profile-one"
    context = owner.auth.authenticate(cast(str, result["token"]))
    assert context.capabilities == frozenset({"status", "chat"})
    assert context.scope == owner.scope.value
    assert context.expires_at == owner.clock.wall + DEVICE_SECONDS
    assert context.transport_id == "certificate-one:443"


@pytest.mark.parametrize("name", ["x" * 80, "📱" * 80])
def test_device_name_matches_wire_character_bound(owner: Fixture, name: str) -> None:
    code = owner.auth.begin_pairing({"chat"})
    result = owner.auth.pair(code, name)
    owner.auth.authenticate(cast(str, result["token"]))


def test_pair_wire_cannot_mutate_the_issued_capabilities(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    result = owner.auth.pair(code, "Phone")
    capabilities = result["capabilities"]
    assert isinstance(capabilities, list)
    capabilities.append("calendar")
    assert owner.auth.authenticate(cast(str, result["token"])).capabilities == frozenset(
        {
            "status",
            "chat",
        }
    )


def test_capability_selection_has_no_default(owner: Fixture) -> None:
    with pytest.raises(TypeError):
        owner.auth.begin_pairing()  # type: ignore[call-arg]
    token = pair(owner, frozenset())
    assert owner.auth.authenticate(token).capabilities == frozenset({"status"})


@pytest.mark.parametrize("capability", ["calendar", "mail", "telegram", "files", "shell", "*"])
def test_external_or_general_capabilities_are_rejected(owner: Fixture, capability: str) -> None:
    assert fault_code(lambda: owner.auth.begin_pairing({capability})) == "forbidden"


def test_code_is_one_use(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"history"})
    owner.auth.pair(code, "Synthetic phone")
    assert fault_code(lambda: owner.auth.pair(code, "Other phone")) == "pairing_closed"


def test_invalid_label_is_rejected_before_consuming_correct_code(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    assert fault_code(lambda: owner.auth.pair(code, "bad\nlabel")) == "invalid_request"
    result = owner.auth.pair(code, "Synthetic phone")
    owner.auth.authenticate(cast(str, result["token"]))


def test_bad_labels_count_toward_global_window_budget(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    for _ in range(4):
        assert fault_code(lambda: owner.auth.pair(code, "bad\nlabel")) == "invalid_request"
    assert fault_code(lambda: owner.auth.pair(code, "bad\nlabel")) == "pairing_rate_limited"
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "pairing_closed"


def test_malformed_pair_bodies_share_the_same_budget(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    for _ in range(4):
        owner.auth.reject_pairing_attempt()
    result = owner.auth.pair(code, "Phone")
    owner.auth.authenticate(cast(str, result["token"]))
    owner.auth.begin_pairing({"chat"})
    for _ in range(4):
        owner.auth.reject_pairing_attempt()
    assert fault_code(owner.auth.reject_pairing_attempt) == "pairing_rate_limited"
    assert fault_code(owner.auth.reject_pairing_attempt) == "pairing_closed"


def test_malformed_attempt_has_no_code_oracle_and_obeys_expiry(owner: Fixture) -> None:
    assert fault_code(owner.auth.reject_pairing_attempt) == "pairing_closed"
    owner.auth.begin_pairing({"chat"})
    owner.clock.advance(120)
    assert fault_code(owner.auth.reject_pairing_attempt) == "pairing_expired"


def test_cancel_pairing_keeps_existing_grant_without_extending_it(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    code = owner.auth.begin_pairing({"chat"})
    owner.auth.cancel_pairing()
    assert fault_code(lambda: owner.auth.pair(code, "Synthetic phone")) == "pairing_closed"
    assert owner.auth.authenticate(token) == context


@pytest.mark.parametrize("clock_component", ["wall", "monotonic"])
def test_pairing_expires_at_120_seconds(owner: Fixture, clock_component: str) -> None:
    code = owner.auth.begin_pairing({"history"})
    setattr(owner.clock, clock_component, getattr(owner.clock, clock_component) + 120)
    assert fault_code(lambda: owner.auth.pair(code, "Synthetic phone")) == "pairing_expired"
    assert fault_code(lambda: owner.auth.pair(code, "Synthetic phone")) == "pairing_closed"


def test_every_bad_attempt_counts_toward_five(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    bad = "111111" if code != "111111" else "222222"
    for attempt in ["", "not-a-code", "١٢٣٤٥٦", bad]:
        assert fault_code(partial(owner.auth.pair, attempt, "Phone")) == "unauthorized"
    assert fault_code(lambda: owner.auth.pair(bad, "Phone")) == "pairing_rate_limited"
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "pairing_closed"


def test_concurrent_attempts_share_one_global_budget(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    bad = "111111" if code != "111111" else "222222"
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(lambda _: fault_code(lambda: owner.auth.pair(bad, "Phone")), range(8))
        )
    assert outcomes.count("unauthorized") == PAIRING_ATTEMPTS - 1
    assert outcomes.count("pairing_rate_limited") == 1
    assert outcomes.count("pairing_closed") == 3


def test_fifth_attempt_can_consume_correct_code(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    bad = "111111" if code != "111111" else "222222"
    for _ in range(4):
        assert fault_code(lambda: owner.auth.pair(bad, "Phone")) == "unauthorized"
    result = owner.auth.pair(code, "Synthetic phone")
    assert owner.auth.authenticate(cast(str, result["token"])).device_id == result["deviceID"]


def test_tokens_are_256_bit_random_and_only_digest_is_persisted(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    result = owner.auth.pair(code, "Synthetic phone")
    token = cast(str, result["token"])
    assert len(token) == 43
    stored = (owner.directory / "auth.json").read_bytes()
    assert token.encode() not in stored
    assert hashlib.sha256(token.encode()).hexdigest().encode() in stored
    assert "code" not in json.loads(stored)
    assert "pairing" not in json.loads(stored)
    assert fault_code(lambda: owner.auth.authenticate("x" * 43)) == "unauthorized"


def test_invalid_credentials_cannot_force_durable_writes(
    owner: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair(owner)
    owner.clock.advance(10)

    def unexpected_write(name: str, value: dict[str, object]) -> None:
        pytest.fail("Unauthenticated request attempted a durable write")

    monkeypatch.setattr(owner.store, "write_json", unexpected_write)
    assert fault_code(lambda: owner.auth.authenticate("wrong")) == "unauthorized"
    assert fault_code(lambda: owner.auth.authenticate("x" * 43)) == "unauthorized"


def test_closed_pairing_requests_cannot_force_durable_writes(
    owner: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner.clock.advance(10)

    def unexpected_write(name: str, value: dict[str, object]) -> None:
        pytest.fail("Closed pairing request attempted a durable write")

    monkeypatch.setattr(owner.store, "write_json", unexpected_write)
    assert fault_code(lambda: owner.auth.pair("123456", "Phone")) == "pairing_closed"
    assert fault_code(owner.auth.reject_pairing_attempt) == "pairing_closed"


def test_pair_replaces_all_previous_grants_regardless_of_label(owner: Fixture) -> None:
    old_token = pair(owner)
    old_context = owner.auth.authenticate(old_token)
    code = owner.auth.begin_pairing({"history"})
    new_result = owner.auth.pair(code, "Completely different label")
    new_context = owner.auth.authenticate(cast(str, new_result["token"]))
    assert new_context.device_id != old_context.device_id
    assert fault_code(lambda: owner.auth.authenticate(old_token)) == "unauthorized"
    assert fault_code(lambda: owner.auth.revalidate(old_context)) == "device_revoked"


def test_rename_cannot_supply_revocation_identity(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    assert fault_code(lambda: owner.auth.revoke("Synthetic phone")) == "invalid_request"
    owner.auth.revalidate(context)


def test_revoke_closes_token_context_and_pairing(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    code = owner.auth.begin_pairing({"chat"})
    owner.auth.revoke(context.device_id)
    assert fault_code(lambda: owner.auth.authenticate(token)) == "device_revoked"
    assert fault_code(lambda: owner.auth.revalidate(context)) == "device_revoked"
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "pairing_closed"
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "device_revoked"


def test_revoke_all_is_durable(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    owner.auth.revoke_all()
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"
    assert fault_code(lambda: owner.auth.revalidate(context)) == "device_revoked"


def test_same_binding_restart_keeps_grant_but_closes_pending_code(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    code = owner.auth.begin_pairing({"chat"})
    owner.restart()
    assert owner.auth.authenticate(token) == context
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "pairing_closed"


def test_device_expires_at_30_days_without_extension(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    owner.clock.advance(DEVICE_SECONDS - 1)
    assert owner.auth.authenticate(token) == context
    owner.clock.advance(1)
    assert fault_code(lambda: owner.auth.authenticate(token)) == "device_expired"
    assert fault_code(lambda: owner.auth.revalidate(context)) == "device_expired"
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "device_expired"


@pytest.mark.parametrize(
    "changed",
    [
        ScopeBinding("other", "profile-one", 1),
        ScopeBinding("principal-one", "other", 1),
        ScopeBinding("principal-one", "profile-one", 2),
    ],
)
def test_scope_change_durably_revokes_never_revives(
    owner: Fixture,
    changed: ScopeBinding,
) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    original = owner.scope.value
    owner.scope.value = changed
    assert fault_code(lambda: owner.auth.revalidate(context)) == "profile_changed"
    owner.scope.value = original
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"


def test_scope_change_closes_unconsumed_code(owner: Fixture) -> None:
    code = owner.auth.begin_pairing({"chat"})
    owner.scope.value = replace(owner.scope.value, generation=2)
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "pairing_closed"


def test_transport_rotation_never_revives_token_or_context(owner: Fixture) -> None:
    token = pair(owner)
    context = owner.auth.authenticate(token)
    code = owner.auth.begin_pairing({"chat"})
    owner.auth.set_transport("certificate-two:443")
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "pairing_closed"
    owner.auth.set_transport("certificate-one:443")
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"
    assert fault_code(lambda: owner.auth.revalidate(context)) == "device_revoked"
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"


def test_transport_change_at_restart_closes_previous_grant(owner: Fixture) -> None:
    token = pair(owner)
    owner.restart("certificate-two:443")
    owner.restart("certificate-one:443")
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"


@pytest.mark.parametrize("component", ["wall", "monotonic"])
def test_clock_rollback_closes_authority_and_survives_restart(
    owner: Fixture, component: str
) -> None:
    token = pair(owner)
    owner.clock.advance(10)
    owner.auth.authenticate(token)
    setattr(owner.clock, component, getattr(owner.clock, component) - 1)
    assert fault_code(lambda: owner.auth.authenticate(token)) == "operation_failed"
    owner.clock.advance(2)
    assert fault_code(lambda: owner.auth.authenticate(token)) == "operation_failed"
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_clock_closes_authority(owner: Fixture, value: float) -> None:
    token = pair(owner)
    original = owner.clock.wall
    owner.clock.wall = value
    assert fault_code(lambda: owner.auth.authenticate(token)) == "operation_failed"
    owner.clock.wall = original
    owner.restart()
    assert fault_code(lambda: owner.auth.authenticate(token)) == "unauthorized"


def test_context_cannot_expand_capabilities_or_expiry(owner: Fixture) -> None:
    token = pair(owner, frozenset())
    context = owner.auth.authenticate(token)
    assert (
        fault_code(
            lambda: owner.auth.revalidate(replace(context, capabilities=frozenset({"chat"})))
        )
        == "device_revoked"
    )
    assert (
        fault_code(
            lambda: owner.auth.revalidate(replace(context, expires_at=context.expires_at + 1))
        )
        == "device_revoked"
    )


def test_state_unknown_fields_and_duplicate_json_are_closed(owner: Fixture) -> None:
    pair(owner)
    raw = owner.store.read_json("auth.json")
    assert raw is not None
    raw["unexpected"] = "synthetic"
    owner.store.write_json("auth.json", raw)
    with pytest.raises(GatewayFault, match="operation_failed"):
        owner.restart()


def test_correct_code_consumed_before_persistence_failure(
    owner: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = owner.auth.begin_pairing({"chat"})

    def unavailable(name: str, value: dict[str, object]) -> None:
        raise GatewayFault("operation_failed", 503)

    monkeypatch.setattr(owner.store, "write_json", unavailable)
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "operation_failed"
    assert fault_code(lambda: owner.auth.pair(code, "Phone")) == "operation_failed"
    assert owner.auth._pairing is None


def test_private_namespace_and_regular_data_permissions(owner: Fixture) -> None:
    assert stat.S_IMODE(owner.directory.stat().st_mode) == 0o700
    owner.store.write_json("synthetic.json", {"schema_version": 1})
    assert stat.S_IMODE(owner.store.path("synthetic.json").stat().st_mode) == 0o600
    assert owner.store.read_json("synthetic.json") == {"schema_version": 1}
    assert owner.store.read_json("absent.json") is None


def test_second_store_cannot_share_namespace(owner: Fixture) -> None:
    with pytest.raises(GatewayFault, match="operation_failed"):
        PrivateStore(owner.directory)
    owner.store.write_json("synthetic.json", {"value": 1})
    owner.restart()
    assert owner.store.read_json("synthetic.json") == {"value": 1}


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "a/b", "a\\b", ".owner.lock", "", "x\x00"]
)
def test_unsafe_filenames_are_rejected(owner: Fixture, name: str) -> None:
    for action in [
        lambda: owner.store.path(name),
        lambda: owner.store.read_json(name),
        lambda: owner.store.write_json(name, {}),
    ]:
        assert fault_code(action) == "operation_failed"


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o777, 0o1700])
def test_existing_namespace_requires_exact_private_mode(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "namespace"
    path.mkdir(mode=0o700)
    path.chmod(mode)
    with pytest.raises(GatewayFault, match="operation_failed"):
        PrivateStore(path)
    assert stat.S_IMODE(path.stat().st_mode) == mode


def test_symlink_namespace_and_ancestor_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    for path in [link, link / "namespace"]:
        with pytest.raises(GatewayFault, match="operation_failed"):
            PrivateStore(path)
    assert list(real.iterdir()) == []


def test_shared_writable_non_os_parent_is_rejected(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    shared.chmod(0o1777)
    with pytest.raises(GatewayFault, match="operation_failed"):
        PrivateStore(shared / "namespace")


def test_parent_replacement_closes_all_path_operations(owner: Fixture) -> None:
    moved = owner.directory.with_name("original-moved")
    owner.directory.rename(moved)
    owner.directory.mkdir(mode=0o700)
    assert fault_code(lambda: owner.store.path("cert.pem")) == "operation_failed"
    assert fault_code(lambda: owner.store.read_json("auth.json")) == "operation_failed"
    assert fault_code(lambda: owner.store.write_json("auth.json", {})) == "operation_failed"
    assert list(owner.directory.iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "public_mode"])
def test_unsafe_existing_data_or_tls_entry_is_rejected(owner: Fixture, kind: str) -> None:
    path = owner.directory / "key.pem"
    if kind in {"symlink", "hardlink"}:
        outside = owner.directory.parent / "untouched"
        outside.write_bytes(b"synthetic-do-not-overwrite")
        outside.chmod(0o600)
        if kind == "symlink":
            path.symlink_to(outside)
        else:
            os.link(outside, path)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    elif kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_bytes(b"synthetic")
        path.chmod(0o644)
    assert fault_code(lambda: owner.store.path("key.pem")) == "operation_failed"
    assert fault_code(lambda: owner.store.read_json("key.pem")) == "operation_failed"
    assert fault_code(lambda: owner.store.write_json("key.pem", {})) == "operation_failed"
    if kind in {"symlink", "hardlink"}:
        assert outside.read_bytes() == b"synthetic-do-not-overwrite"


def test_foreign_owner_is_rejected(owner: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    owner.store.write_json("synthetic.json", {"value": 1})
    original = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: original + 1)
    assert fault_code(lambda: owner.store.read_json("synthetic.json")) == "operation_failed"


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}', b"[]", b'{"x":NaN}', b"{"])
def test_malformed_or_nonfinite_json_is_rejected(owner: Fixture, payload: bytes) -> None:
    path = owner.directory / "synthetic.json"
    path.write_bytes(payload)
    path.chmod(0o600)
    assert fault_code(lambda: owner.store.read_json("synthetic.json")) == "operation_failed"


def test_storage_read_and_write_are_bounded(owner: Fixture) -> None:
    path = owner.directory / "large.json"
    path.write_bytes(b"x" * (MAX_JSON_BYTES + 1))
    path.chmod(0o600)
    assert fault_code(lambda: owner.store.read_json("large.json")) == "operation_failed"
    assert (
        fault_code(lambda: owner.store.write_json("other.json", {"x": "y" * MAX_JSON_BYTES}))
        == "operation_failed"
    )
    assert not (owner.directory / "other.json").exists()


def test_atomic_failure_keeps_prior_file_and_removes_stage(
    owner: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner.store.write_json("synthetic.json", {"value": 1})

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic-private-error")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert (
        fault_code(lambda: owner.store.write_json("synthetic.json", {"value": 2}))
        == "operation_failed"
    )
    assert owner.store.read_json("synthetic.json") == {"value": 1}
    assert not any(path.name.startswith(".write-") for path in owner.directory.iterdir())


def test_storage_errors_do_not_include_paths_or_contents(owner: Fixture) -> None:
    private_name = "synthetic-secret.json"
    path = owner.directory / private_name
    path.write_text("SECRET_SENTINEL_NOT_DIAGNOSTIC")
    path.chmod(0o600)
    with pytest.raises(GatewayFault) as error:
        owner.store.read_json(private_name)
    assert str(error.value) == "operation_failed"
    assert private_name not in str(error.value)
    assert "SECRET_SENTINEL" not in str(error.value)


def test_closed_store_has_no_authority(owner: Fixture) -> None:
    token = pair(owner)
    owner.store.close()
    assert fault_code(lambda: owner.store.path("key.pem")) == "operation_failed"
    assert fault_code(lambda: owner.auth.authenticate(token)) == "operation_failed"


def _chmod_acl(path: Path, entry: str) -> None:
    subprocess.run(
        ["/bin/chmod", "+a", entry, str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )


def _remove_acl(path: Path) -> None:
    subprocess.run(
        ["/bin/chmod", "-N", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS extended-ACL fixture")
def test_native_absent_acl_on_live_private_directory_and_file(tmp_path: Path) -> None:
    store = PrivateStore(tmp_path / "no-acl")
    try:
        store.write_json("synthetic.json", {"value": 1})
        _remove_acl(store.path("synthetic.json"))
        _remove_acl(tmp_path / "no-acl")
        assert store.read_json("synthetic.json") == {"value": 1}
    finally:
        store.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS extended-ACL fixture")
@pytest.mark.parametrize("failure", [0, errno.EACCES, errno.ENOTSUP, errno.EIO])
def test_uninspectable_acl_is_never_treated_as_absent(
    owner: Fixture, monkeypatch: pytest.MonkeyPatch, failure: int
) -> None:
    library = owner.store._acl._library
    assert library is not None

    def unavailable(descriptor: int, acl_type: int) -> None:
        ctypes.set_errno(failure)

    monkeypatch.setattr(library, "acl_get_fd_np", unavailable)
    assert fault_code(lambda: owner.store.read_json("auth.json")) == "operation_failed"


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS extended-ACL fixture")
@pytest.mark.parametrize("target", ["file", "namespace", "ancestor"])
def test_allow_acl_is_rejected_even_with_private_posix_mode(tmp_path: Path, target: str) -> None:
    parent = tmp_path / "private-parent"
    parent.mkdir(mode=0o700)
    store = PrivateStore(parent / "state")
    try:
        store.write_json("synthetic.json", {"value": 1})
        path = {
            "file": store.path("synthetic.json"),
            "namespace": parent / "state",
            "ancestor": parent,
        }[target]
        before = stat.S_IMODE(path.stat().st_mode)
        _chmod_acl(path, "everyone allow read,readattr")
        try:
            assert stat.S_IMODE(path.stat().st_mode) == before
            assert fault_code(lambda: store.read_json("synthetic.json")) == "operation_failed"
            assert fault_code(lambda: store.path("synthetic.json")) == "operation_failed"
            assert fault_code(lambda: store.write_json("synthetic.json", {})) == "operation_failed"
        finally:
            _remove_acl(path)
    finally:
        store.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS extended-ACL fixture")
def test_deny_only_parent_acl_remains_acceptable(tmp_path: Path) -> None:
    parent = tmp_path / "private-parent"
    parent.mkdir(mode=0o700)
    _chmod_acl(parent, "everyone deny delete")
    try:
        store = PrivateStore(parent / "state")
        try:
            store.write_json("synthetic.json", {"value": 1})
            assert store.read_json("synthetic.json") == {"value": 1}
        finally:
            store.close()
    finally:
        _remove_acl(parent)
