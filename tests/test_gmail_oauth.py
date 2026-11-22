"""Synthetic client configuration and owned loopback OAuth protocol checks."""

import base64
import hashlib
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from pydantic import SecretStr

from satori.infrastructure.gmail.contracts import GMAIL_SCOPES, GmailClientConfiguration, GmailError
from satori.infrastructure.gmail.oauth import OAuthLoopback, load_configuration

CLIENT_ID = "123456789-synthetic.apps.googleusercontent.com"
CLIENT_SECRET = "SYNTHETIC-CLIENT-SECRET"
ISSUER = "https://accounts.google.com"


def configuration() -> GmailClientConfiguration:
    return GmailClientConfiguration(client_id=CLIENT_ID, client_secret=SecretStr(CLIENT_SECRET))


def installed_client() -> dict[str, object]:
    return {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "project_id": "synthetic-test-project",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": ["http://localhost"],
    }


def write_configuration(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "synthetic-client.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_explicit_installed_configuration_is_redacted(tmp_path: Path) -> None:
    config = load_configuration(write_configuration(tmp_path, {"installed": installed_client()}))
    assert config.client_id == CLIENT_ID
    assert config.client_secret.get_secret_value() == CLIENT_SECRET
    assert CLIENT_SECRET not in repr(config)
    assert CLIENT_SECRET not in config.model_dump_json()


@pytest.mark.parametrize("outer", [{}, {"web": installed_client()}, [], None])
def test_configuration_requires_desktop_installed_client(tmp_path: Path, outer: object) -> None:
    with pytest.raises(GmailError, match="configuration_invalid"):
        load_configuration(write_configuration(tmp_path, outer))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_id", ""),
        ("client_id", "not-a-google-client"),
        ("client_id", "x" * 4096),
        ("client_secret", None),
        ("client_secret", ""),
        ("auth_uri", "https://attacker.example.test/auth"),
        ("token_uri", "https://attacker.example.test/token"),
        ("token_uri", "http://oauth2.googleapis.com/token"),
        ("redirect_uris", ["https://attacker.example.test/callback"]),
    ],
)
def test_configuration_rejects_wrong_registration_or_endpoints(
    tmp_path: Path, field: str, value: object
) -> None:
    client = installed_client()
    client[field] = value
    with pytest.raises(GmailError, match="configuration_invalid") as raised:
        load_configuration(write_configuration(tmp_path, {"installed": client}))
    assert CLIENT_SECRET not in str(raised.value)


def test_configuration_rejects_duplicate_keys_and_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-client.json"
    path.write_text('{"installed": {}, "installed": {}}', encoding="utf-8")
    with pytest.raises(GmailError, match="configuration_invalid"):
        load_configuration(path)
    path.write_bytes(b" " * (2 * 1024 * 1024))
    with pytest.raises(GmailError):
        load_configuration(path)


def test_configuration_rejects_symlinks_directories_and_missing_file(tmp_path: Path) -> None:
    target = write_configuration(tmp_path, {"installed": installed_client()})
    link = tmp_path / "linked-client.json"
    link.symlink_to(target)
    for path in (link, tmp_path, tmp_path / "missing-client.json"):
        with pytest.raises(GmailError):
            load_configuration(path)


def callback(
    attempt: OAuthLoopback,
    *,
    changes: dict[str, str] | None = None,
    path: str | None = None,
    host: str | None = None,
    duplicate: str | None = None,
    method: str = "GET",
    omit: tuple[str, ...] = (),
) -> int:
    query = parse_qs(urlsplit(attempt.authorization_url).query)
    redirect = urlsplit(query["redirect_uri"][0])
    assert redirect.hostname == "127.0.0.1"
    assert redirect.port is not None
    fields = {"code": "SYNTHETIC-ONE-USE-CODE", "state": query["state"][0], "iss": ISSUER}
    fields.update(changes or {})
    for key in omit:
        fields.pop(key, None)
    pairs = list(fields.items())
    if duplicate is not None:
        pairs.append((duplicate, fields[duplicate]))
    route = f"{path or redirect.path}?{urlencode(pairs)}"
    connection = http.client.HTTPConnection("127.0.0.1", redirect.port, timeout=2)
    try:
        connection.request(method, route, headers={"Host": host or redirect.netloc})
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_authorization_url_is_fixed_read_only_pkce_and_has_unique_state() -> None:
    first = OAuthLoopback(configuration())
    second = OAuthLoopback(configuration())
    try:
        parsed = urlsplit(first.authorization_url)
        fields = parse_qs(parsed.query)
        other = parse_qs(urlsplit(second.authorization_url).query)
        assert (parsed.scheme, parsed.netloc, parsed.path) == (
            "https",
            "accounts.google.com",
            "/o/oauth2/v2/auth",
        )
        assert fields["response_type"] == ["code"]
        assert fields["client_id"] == [CLIENT_ID]
        assert frozenset(fields["scope"][0].split()) == GMAIL_SCOPES
        assert fields["code_challenge_method"] == ["S256"]
        assert len(fields["code_challenge"][0]) == hashlib.sha256().digest_size * 4 // 3 + 1
        assert len(fields["state"][0]) >= 32
        assert fields["state"] != other["state"]
        assert fields["code_challenge"] != other["code_challenge"]
        assert first.attempt_id != second.attempt_id
        assert CLIENT_SECRET not in first.authorization_url
        assert "access_token" not in fields
        assert "client_secret" not in fields
    finally:
        first.close()
        second.close()


def test_loopback_accepts_only_matching_own_synthetic_callback() -> None:
    attempt = OAuthLoopback(configuration(), timeout=2)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt.wait, threading.Event())
            assert callback(attempt) == 200
            code = future.result(timeout=3)
        assert isinstance(code, SecretStr)
        assert code.get_secret_value() == "SYNTHETIC-ONE-USE-CODE"
        assert "SYNTHETIC-ONE-USE-CODE" not in repr(code)
        form = attempt.token_form(code)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode("ascii")).digest())
            .decode()
            .rstrip("=")
        )
        assert parse_qs(urlsplit(attempt.authorization_url).query)["code_challenge"] == [expected]
        assert (
            form["redirect_uri"]
            == parse_qs(urlsplit(attempt.authorization_url).query)["redirect_uri"][0]
        )
        assert form["grant_type"] == "authorization_code"
        with pytest.raises(GmailError, match="callback_invalid"):
            attempt.wait(threading.Event())
    finally:
        attempt.close()


@pytest.mark.parametrize(
    ("changes", "path", "host", "duplicate", "method"),
    [
        ({"state": "wrong"}, None, None, None, "GET"),
        ({"state": "не тот state"}, None, None, None, "GET"),
        ({"iss": "https://attacker.example.test"}, None, None, None, "GET"),
        ({"iss": ""}, None, None, None, "GET"),
        ({"code": ""}, None, None, None, "GET"),
        ({"error": "access_denied"}, None, None, None, "GET"),
        ({}, "/wrong-callback", None, None, "GET"),
        ({}, None, "attacker.example.test", None, "GET"),
        ({}, None, None, "state", "GET"),
        ({}, None, None, "code", "GET"),
        ({}, None, None, "iss", "GET"),
        ({}, None, None, None, "POST"),
    ],
)
def test_loopback_rejects_ambiguous_or_wrong_callback(
    changes: dict[str, str],
    path: str | None,
    host: str | None,
    duplicate: str | None,
    method: str,
) -> None:
    attempt = OAuthLoopback(configuration(), timeout=0.3)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt.wait, threading.Event())
            assert (
                callback(
                    attempt,
                    changes=changes,
                    path=path,
                    host=host,
                    duplicate=duplicate,
                    method=method,
                )
                >= 400
            )
            with pytest.raises(GmailError):
                future.result(timeout=2)
    finally:
        attempt.close()


@pytest.mark.parametrize("inactive", [False, True])
def test_loopback_cancellation_or_generation_change_stops_wait(inactive: bool) -> None:
    attempt = OAuthLoopback(configuration(), timeout=1)
    cancel = threading.Event()
    if not inactive:
        cancel.set()
    try:
        with pytest.raises(GmailError, match="cancelled"):
            attempt.wait(cancel, active=lambda: not inactive)
    finally:
        attempt.close()


def test_loopback_timeout_is_closed_and_bounded() -> None:
    attempt = OAuthLoopback(configuration(), timeout=0.03)
    try:
        with pytest.raises(GmailError, match="timeout"):
            attempt.wait(threading.Event())
    finally:
        attempt.close()


def test_loopback_distinguishes_provider_denial_from_success() -> None:
    attempt = OAuthLoopback(configuration(), timeout=1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt.wait, threading.Event())
            assert callback(attempt, changes={"error": "access_denied"}, omit=("code",)) == 400
            with pytest.raises(GmailError, match="login_denied"):
                future.result(timeout=2)
    finally:
        attempt.close()


def test_loopback_missing_issuer_is_not_accepted() -> None:
    attempt = OAuthLoopback(configuration(), timeout=1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt.wait, threading.Event())
            assert callback(attempt, omit=("iss",)) == 400
            with pytest.raises(GmailError, match="callback_invalid"):
                future.result(timeout=2)
    finally:
        attempt.close()


def test_closing_owned_listener_cancels_its_wait() -> None:
    attempt = OAuthLoopback(configuration(), timeout=1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt.wait, threading.Event())
            attempt.close()
            with pytest.raises(GmailError, match="cancelled"):
                future.result(timeout=2)
    finally:
        attempt.close()
