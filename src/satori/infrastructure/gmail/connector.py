"""Foreground Gmail reader with session-only credentials and exact connection fences."""

import hashlib
import json
import math
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.errors import HeaderParseError
from email.header import decode_header
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from pydantic import SecretStr, ValidationError

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail_contracts import (
    MAX_MAIL_BATCH_BYTES,
    MAX_MAIL_MESSAGE_BYTES,
    MailAccountRef,
    MailCatalog,
    MailClock,
    MailHeaderClaim,
    MailProjection,
    MailReadRequest,
    MailReadResult,
    MailSourceRef,
    mail_identifier,
)
from satori.application.assistant.mail_projection import project_gmail_batch
from satori.application.assistant.mail_search_contracts import MailSearchRequest
from satori.application.assistant.mail_task_contracts import MailQueryReadRequest
from satori.application.assistant.source_answer_contracts import (
    SourceObservation,
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import task_search_admission, task_snapshot
from satori.infrastructure.gmail.contracts import (
    GMAIL_SCOPES,
    GmailAccount,
    GmailClientConfiguration,
    GmailDisconnectResult,
    GmailError,
    GmailHeaderOmission,
    GmailHeaderOmissionReason,
    GmailInboxPage,
    GmailLoginStart,
    GmailMessageHeader,
    GmailResponseDiagnostic,
    GmailSearchPage,
    gmail_response_diagnostic,
)
from satori.infrastructure.gmail.http import (
    FULL_FIELDS,
    GMAIL_ORIGIN,
    LIST_FIELDS,
    METADATA_FIELDS,
    METADATA_HEADERS,
    PROFILE_URL,
    REVOKE_URL,
    TOKEN_URL,
    USERINFO_URL,
    GoogleHTTP,
)
from satori.infrastructure.gmail.oauth import GOOGLE_ISSUER, OAuthLoopback


def _clock() -> MailClock:
    return MailClock(wall=datetime.now(UTC), monotonic=time.monotonic())


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise GmailError("response_invalid")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise GmailError("response_invalid")


def _json(
    raw: bytes, *, response_diagnostic: GmailResponseDiagnostic | None = None
) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant
        )
        if type(value) is not dict:
            raise GmailError("response_invalid")
        return value
    except GmailError as error:
        if error.code == "response_invalid" and response_diagnostic is not None:
            raise GmailError("response_invalid", response_diagnostic=response_diagnostic) from None
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise GmailError("response_invalid", response_diagnostic=response_diagnostic) from None


def _text(value: object, maximum: int = 2048, *, empty: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or (not value and not empty):
        raise GmailError("response_invalid")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value):
        raise GmailError("response_invalid")
    return value


def _identifier(value: object) -> str:
    text = _text(value, 255)
    try:
        return mail_identifier(text)
    except ValueError:
        raise GmailError("response_invalid") from None


def _header_text(value: object, *, decoded: bool = False) -> str:
    # ADR-0088 permits HTAB in header values, including after RFC 2047 decoding.
    # Account/resource IDs, names and other single-line fields keep _text's rules.
    if type(value) is not str:
        raise GmailError("response_invalid", response_diagnostic="metadata_header_value_type")
    if len(value) > 2048:
        raise GmailError(
            "response_invalid",
            response_diagnostic="metadata_header_decoded_limit"
            if decoded
            else "metadata_header_value_limit",
        )
    if any(c != "\t" and unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value):
        raise GmailError(
            "response_invalid",
            response_diagnostic="metadata_header_decoded_controls"
            if decoded
            else "metadata_header_value_controls",
        )
    return value


def _secret(value: object) -> SecretStr:
    text = _text(value, 8192)
    if not all(33 <= ord(c) <= 126 for c in text):
        raise GmailError("response_invalid")
    return SecretStr(text)


@dataclass(frozen=True)
class _Session:
    account: GmailAccount = field(repr=False)
    access: SecretStr = field(repr=False)
    refresh: SecretStr | None = field(repr=False)
    expires_at: datetime
    deadline: float
    generation: int


class GmailConnector:
    """One principal owns foreground OAuth and an in-memory read-only connection.

    Methods performing HTTPS run on the host's worker. The host owns canonical mail
    grants and UI delivery fences; this adapter never grants them or stores mail.
    """

    def __init__(
        self,
        principal: LocalPrincipal,
        *,
        http: GoogleHTTP | None = None,
        clock: Callable[[], MailClock] = _clock,
    ) -> None:
        self._principal = principal
        self._http = http if http is not None else GoogleHTTP()
        self._clock = clock
        self._last_clock: MailClock | None = None
        self._lock = threading.RLock()
        self._generation = 0
        self._pending: OAuthLoopback | None = None
        self._pending_deadline: float | None = None
        self._session: _Session | None = None
        self._catalog: MailCatalog | None = None
        self._task_read_grant: MailQueryReadRequest | None = None
        self._listing_attempt: object | None = None
        self._revoking = False

    def _now(self) -> MailClock:
        with self._lock:
            try:
                now = MailClock.model_validate(self._clock().model_dump())
            except Exception:
                self._forget()
                raise GmailError("auth_expired") from None
            if self._last_clock is not None and (
                now.wall < self._last_clock.wall or now.monotonic < self._last_clock.monotonic
            ):
                self._forget()
                raise GmailError("auth_expired")
            self._last_clock = now
        return now

    def _forget(self) -> tuple[_Session | None, OAuthLoopback | None]:
        with self._lock:
            session, pending = self._session, self._pending
            self._generation += 1
            self._session = None
            self._pending = None
            self._pending_deadline = None
            self._catalog = None
            self._task_read_grant = None
            self._listing_attempt = None
        if pending is not None:
            pending.close()
        return session, pending

    @property
    def account(self) -> GmailAccount | None:
        with self._lock:
            session = self._session
        if session is None:
            return None
        try:
            self._check_session(session, threading.Event())
        except GmailError:
            return None
        return session.account

    @property
    def connection_expires_at(self) -> datetime | None:
        """Local session deadline only; inspecting it never probes Google."""
        with self._lock:
            session = self._session
        if session is None:
            return None
        try:
            self._check_session(session, threading.Event())
        except GmailError:
            return None
        return session.expires_at

    def begin_login(self, configuration: GmailClientConfiguration) -> GmailLoginStart:
        if type(configuration) is not GmailClientConfiguration:
            raise GmailError("configuration_invalid")
        with self._lock:
            if self._pending is not None or self._revoking:
                raise GmailError("login_in_progress")
            if self._session is not None:
                raise GmailError("request_invalid")
            self._generation += 1
            deadline = time.monotonic() + 120
            pending = OAuthLoopback(configuration, timeout=min(120, deadline - time.monotonic()))
            self._pending = pending
            self._pending_deadline = deadline
        return GmailLoginStart(
            attempt_id=pending.attempt_id, authorization_url=pending.authorization_url
        )

    def _pending_active(self, pending: OAuthLoopback, generation: int) -> bool:
        with self._lock:
            return self._pending is pending and self._generation == generation

    def cancel_login(self) -> None:
        self._cancel_pending(None)

    def _cancel_pending(self, attempt_id: UUID | None) -> None:
        with self._lock:
            pending = self._pending
            if pending is None or (attempt_id is not None and pending.attempt_id != attempt_id):
                return
            self._pending = None
            self._pending_deadline = None
            self._generation += 1
        pending.close()

    def finish_login(self, attempt_id: UUID, *, cancel: threading.Event) -> GmailAccount:
        with self._lock:
            pending = self._pending
            deadline = self._pending_deadline
            generation = self._generation
        if pending is None or deadline is None or pending.attempt_id != attempt_id:
            raise GmailError("cancelled")

        def active() -> bool:
            return self._pending_active(pending, generation)

        def guard() -> None:
            if cancel.is_set() or not active():
                raise GmailError("cancelled")
            if time.monotonic() >= deadline:
                raise GmailError("timeout")

        def remaining() -> float:
            guard()
            seconds = min(10.0, deadline - time.monotonic())
            if seconds <= 0:
                raise GmailError("timeout")
            return seconds

        try:
            guard()
            code = pending.wait(cancel, active)
            issued = self._now()
            guard()
            response = _json(
                self._http.request(
                    "POST",
                    TOKEN_URL,
                    form=pending.token_form(code),
                    max_bytes=32768,
                    cancel=cancel,
                    timeout=remaining(),
                )
            )
            guard()
            if response.get("token_type") != "Bearer":
                raise GmailError("scope_rejected")
            scopes = _text(response.get("scope"), 4096).split()
            normalized = {
                "email" if value == "https://www.googleapis.com/auth/userinfo.email" else value
                for value in scopes
            }
            if normalized != GMAIL_SCOPES:
                raise GmailError("scope_rejected")
            access = _secret(response.get("access_token"))
            refresh = _secret(response["refresh_token"]) if "refresh_token" in response else None
            seconds = response.get("expires_in")
            if type(seconds) is not int or not 0 < seconds <= 86400:
                raise GmailError("response_invalid")
            # Session read capability is at most one hour, even for a longer-lived token.
            seconds = min(seconds, 3600)
            user = _json(
                self._http.request(
                    "GET",
                    USERINFO_URL,
                    token=access,
                    max_bytes=16384,
                    cancel=cancel,
                    timeout=remaining(),
                )
            )
            guard()
            if "iss" in user and user["iss"] not in {GOOGLE_ISSUER, "accounts.google.com"}:
                raise GmailError("account_invalid")
            subject = _identifier(user.get("sub"))
            profile = _json(
                self._http.request(
                    "GET",
                    PROFILE_URL + "?" + urlencode({"fields": "emailAddress"}),
                    token=access,
                    max_bytes=16384,
                    cancel=cancel,
                    timeout=remaining(),
                )
            )
            guard()
            email = _text(profile.get("emailAddress"), 320)
            if re.fullmatch(r"[^\s@]+@[^\s@]+", email) is None:
                raise GmailError("account_invalid")
            account = GmailAccount(
                account=MailAccountRef(
                    subject=subject, client_registration=pending._configuration.client_id
                ),
                email_label=email,
            )
            session = _Session(
                account,
                access,
                refresh,
                issued.wall + timedelta(seconds=seconds),
                issued.monotonic + seconds,
                generation,
            )
            current = self._now()
            if current.wall >= session.expires_at or current.monotonic >= session.deadline:
                raise GmailError("auth_expired")
            with self._lock:
                guard()
                self._session = session
                self._pending = None
                self._pending_deadline = None
            if cancel.is_set():
                with self._lock:
                    if self._session is session:
                        self._forget()
                raise GmailError("cancelled")
            return account
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None
                    self._pending_deadline = None
                    self._generation += 1
            pending.close()

    def authenticate(
        self,
        configuration: GmailClientConfiguration,
        *,
        on_authorization_url: Callable[[str], object],
        cancel: threading.Event,
    ) -> GmailAccount:
        start = self.begin_login(configuration)
        try:
            on_authorization_url(start.authorization_url)
        except Exception:
            self._cancel_pending(start.attempt_id)
            raise GmailError("cancelled") from None
        return self.finish_login(start.attempt_id, cancel=cancel)

    def _check_session(self, session: _Session, cancel: threading.Event) -> MailClock:
        if cancel.is_set():
            raise GmailError("cancelled")
        now = self._now()
        with self._lock:
            if self._session is not session or session.generation != self._generation:
                raise GmailError("cancelled")
            if now.wall >= session.expires_at or now.monotonic >= session.deadline:
                self._forget()
                raise GmailError("auth_expired")
        return now

    def _current_session(self, cancel: threading.Event) -> _Session:
        if cancel.is_set():
            raise GmailError("cancelled")
        with self._lock:
            if self._revoking:
                raise GmailError("login_in_progress")
            session = self._session
        if session is None:
            raise GmailError("not_connected")
        self._check_session(session, cancel)
        return session

    def _get(
        self,
        session: _Session,
        url: str,
        *,
        cancel: threading.Event,
        max_bytes: int,
        deadline: float,
    ) -> bytes:
        now = self._check_session(session, cancel)
        remaining = min(deadline - now.monotonic, session.deadline - now.monotonic, 10.0)
        if remaining <= 0:
            raise GmailError("timeout")
        try:
            raw = self._http.request(
                "GET",
                url,
                token=session.access,
                max_bytes=max_bytes,
                cancel=cancel,
                timeout=remaining,
            )
        except GmailError as error:
            # A bare 403 also covers quota/domain policy failures. It does not
            # establish that the verified, still-live bearer needs replacement.
            if error.code == "auth_expired":
                with self._lock:
                    if self._session is session:
                        self._forget()
            raise
        now = self._check_session(session, cancel)
        if now.monotonic >= deadline:
            raise GmailError("timeout")
        if len(raw) > max_bytes:
            raise GmailError("response_too_large")
        return raw

    def list_headers(self, *, cancel: threading.Event) -> GmailInboxPage:
        return self._list_headers(cancel=cancel)

    def search_headers(
        self, request: MailSearchRequest, *, cancel: threading.Event
    ) -> GmailSearchPage:
        """Search the connected mailbox for one explicit query, fetching headers only."""
        if type(request) is not MailSearchRequest:
            raise GmailError("request_invalid")
        try:
            validated = MailSearchRequest.model_validate(request.model_dump())
        except (ValidationError, ValueError, TypeError):
            raise GmailError("request_invalid") from None
        page = self._list_headers(cancel=cancel, search=validated)
        return GmailSearchPage(request=validated, page=page)

    def _list_headers(
        self, *, cancel: threading.Event, search: MailSearchRequest | None = None
    ) -> GmailInboxPage:
        # An explicit refresh invalidates old read membership even if this attempt fails.
        attempt = object()
        with self._lock:
            self._catalog = None
            self._task_read_grant = None
            self._listing_attempt = attempt
        session = self._current_session(cancel)
        deadline = self._now().monotonic + 30
        if search is not None:
            deadline = min(deadline, search.monotonic_deadline)

        def guard() -> MailClock:
            now = self._check_session(session, cancel)
            if search is not None:
                if search.principal != self._principal or search.account != session.account.account:
                    raise GmailError("account_mismatch")
                if now.wall < search.created.wall or now.monotonic < search.created.monotonic:
                    raise GmailError("request_invalid")
                if now.wall >= search.expires_at or now.monotonic >= search.monotonic_deadline:
                    raise GmailError("timeout")
            with self._lock:
                if self._listing_attempt is not attempt:
                    raise GmailError("cancelled")
            if now.monotonic >= deadline:
                raise GmailError("timeout")
            return now

        list_parameters = (
            {
                "labelIds": "INBOX",
                "maxResults": "20",
                "includeSpamTrash": "false",
                "fields": LIST_FIELDS,
            }
            if search is None
            else {
                "q": search.query,
                "maxResults": "20",
                "includeSpamTrash": "true",
                "fields": LIST_FIELDS,
            }
        )
        query = urlencode(list_parameters)
        guard()
        raw = self._get(
            session,
            GMAIL_ORIGIN + "/gmail/v1/users/me/messages?" + query,
            cancel=cancel,
            max_bytes=32768,
            deadline=deadline,
        )
        guard()
        data = _json(raw, response_diagnostic="list_json")
        rows = data.get("messages", [])
        if type(rows) is not list:
            raise GmailError("response_invalid", response_diagnostic="list_rows_shape")
        if len(rows) > 20:
            raise GmailError("response_invalid", response_diagnostic="list_rows_count")
        ids: list[str] = []
        for row in rows:
            if type(row) is not dict:
                raise GmailError("response_invalid", response_diagnostic="list_row_shape")
            try:
                ids.append(_identifier(row.get("id")))
            except GmailError:
                raise GmailError(
                    "response_invalid", response_diagnostic="list_identifier"
                ) from None
        if len(set(ids)) != len(ids):
            raise GmailError("response_invalid", response_diagnostic="list_duplicate_identifier")
        cursor = data.get("nextPageToken", "")
        try:
            _text(cursor, 4096, empty=True)
        except GmailError:
            raise GmailError("response_invalid", response_diagnostic="list_cursor") from None
        headers: list[GmailMessageHeader] = []
        omitted: dict[GmailHeaderOmissionReason, int] = {}
        size = len(raw)
        for message_id in ids:
            query_items = [("format", "metadata"), ("fields", METADATA_FIELDS)] + [
                ("metadataHeaders", name) for name in METADATA_HEADERS
            ]
            url = (
                GMAIL_ORIGIN
                + "/gmail/v1/users/me/messages/"
                + quote(message_id, safe="")
                + "?"
                + urlencode(query_items)
            )
            guard()
            value = self._get(session, url, cancel=cancel, max_bytes=65536, deadline=deadline)
            guard()
            size += len(value)
            if size > MAX_MAIL_MESSAGE_BYTES:
                raise GmailError("response_too_large")
            message = _json(value, response_diagnostic="metadata_json")
            if message.get("id") != message_id:
                raise GmailError("account_mismatch")
            payload = message.get("payload", {})
            if type(payload) is not dict:
                raise GmailError("response_invalid", response_diagnostic="metadata_payload_shape")
            try:
                thread_id = _identifier(message["threadId"]) if "threadId" in message else None
            except GmailError:
                raise GmailError(
                    "response_invalid", response_diagnostic="metadata_identifier"
                ) from None
            try:
                claims = self._header_claims(payload.get("headers", []))
            except GmailError as error:
                reason = gmail_response_diagnostic(
                    error.code, getattr(error, "response_diagnostic", None)
                )
                if reason not in {
                    "metadata_header_value_controls",
                    "metadata_header_decoded_controls",
                }:
                    raise
                # Every field and codec/size failure is checked before this omission signal.
                omission: GmailHeaderOmissionReason = (
                    "metadata_header_value_controls"
                    if reason == "metadata_header_value_controls"
                    else "metadata_header_decoded_controls"
                )
                omitted[omission] = omitted.get(omission, 0) + 1
                continue
            headers.append(
                GmailMessageHeader(
                    source=MailSourceRef(account=session.account.account, message_id=message_id),
                    headers=claims,
                    thread_id=thread_id,
                    internal_date=self._internal_date(message.get("internalDate")),
                )
            )
        observed = guard()
        catalog = MailCatalog(
            principal=self._principal,
            account=session.account.account,
            catalog_id=uuid4(),
            observed=observed,
            message_ids=tuple(message.source.message_id for message in headers),
        )
        page = GmailInboxPage(
            catalog=catalog,
            messages=tuple(headers),
            more_available=bool(cursor),
            completeness="complete" if not omitted else "partial" if headers else "unsupported",
            omissions=tuple(
                GmailHeaderOmission(reason=reason, count=count) for reason, count in omitted.items()
            ),
        )
        with self._lock:
            guard()
            self._catalog = catalog
        return page

    @staticmethod
    def _internal_date(value: object) -> datetime | None:
        if value is None:
            return None
        if type(value) is not str or re.fullmatch(r"[0-9]{1,16}", value) is None:
            raise GmailError("response_invalid")
        try:
            return datetime.fromtimestamp(int(value) / 1000, UTC)
        except (OverflowError, ValueError, OSError):
            raise GmailError("response_invalid") from None

    def task_search(
        self, request: SourceTaskRequest, plan: SourceSearchPlan, *, cancel: threading.Event
    ) -> SourceTaskSnapshot:
        """One query-authorized search and at most three exact related-body reads.

        This path never fabricates a selected-resource proof or renews OAuth. The
        private identity-bound capability is minted only from this returned catalog.
        """
        now = self._now()
        query = task_search_admission(request, plan, "mail", now.wall)
        if request.scope.profile_id != self._principal.profile_id:
            raise GmailError("account_mismatch")
        session = self._current_session(cancel)
        # Epoch seconds avoid Gmail's local-midnight interpretation. Expand only the
        # transport's one-second boundaries; exact UTC filtering follows metadata.
        effective = (
            f"({query}) after:{math.floor(plan.window_start.timestamp()) - 1} "
            f"before:{math.ceil(plan.window_end.timestamp()) + 1}"
        )
        seconds = min(30.0, (request.expires_at - now.wall).total_seconds())
        search = MailSearchRequest(
            request_id=request.request_id,
            principal=self._principal,
            account=session.account.account,
            query=effective,
            created=now,
            expires_at=now.wall + timedelta(seconds=seconds),
            monotonic_deadline=now.monotonic + seconds,
        )
        found = self.search_headers(search, cancel=cancel)
        task_search_admission(request, plan, "mail", self._check_session(session, cancel).wall)
        supported = tuple(
            sorted(
                (
                    header
                    for header in found.page.messages
                    if header.internal_date is not None
                    and plan.window_start <= header.internal_date < plan.window_end
                ),
                key=lambda header: header.internal_date or plan.window_start,
                reverse=True,
            )
        )
        omitted = len(found.page.messages) - len(supported) + found.page.omitted_count
        selected = () if plan.mail_headers_only else supported[:3]
        projections: tuple[MailProjection, ...] = ()
        if selected:
            issued = self._check_session(session, cancel)
            capability = MailQueryReadRequest(
                task_request_id=request.request_id,
                search=search,
                catalog_id=found.page.catalog.catalog_id,
                message_ids=tuple(item.source.message_id for item in selected),
                issued=issued,
                expires_at=search.expires_at,
                monotonic_deadline=search.monotonic_deadline,
            )
            with self._lock:
                if self._catalog != found.page.catalog:
                    raise GmailError("cancelled")
                self._task_read_grant = capability
            raw = self._read_search_results(capability, session, cancel=cancel)
            projections = project_gmail_batch(raw, tuple(item.source for item in selected))
        observed = self._check_session(session, cancel)
        if observed.wall >= search.expires_at or observed.monotonic >= search.monotonic_deadline:
            raise GmailError("timeout")
        task_search_admission(request, plan, "mail", observed.wall)
        projected = {item.source.message_id: item for item in projections}
        items = []
        for header in supported:
            body = projected.get(header.source.message_id)
            if body is not None and body.internal_date != header.internal_date:
                raise GmailError("response_invalid")
            reference = (
                "mail:"
                + hashlib.sha256(
                    f"{request.request_id}:{header.source.message_id}".encode()
                ).hexdigest()[:32]
            )
            items.append(
                SourceObservation(
                    source="mail",
                    source_ref=reference,
                    observed_at=observed.wall,
                    event_at=header.internal_date,
                    title=header.subject[:500],
                    sender=header.sender[:500],
                    text=body.text[:4000] if body is not None else "",
                    completeness=(
                        "partial"
                        if body is not None and len(body.text) > 4000
                        else body.completeness
                        if body is not None and body.completeness != "unsupported"
                        else "metadata_only"
                    ),
                )
            )
        partial = (
            omitted > 0
            or (not plan.mail_headers_only and len(supported) > len(selected))
            or any(body.completeness != "complete" or bool(body.omissions) for body in projections)
        )
        body_omissions = ",".join(
            sorted({item.reason for body in projections for item in body.omissions})
        )
        return task_snapshot(
            request,
            plan,
            "mail",
            observed.wall,
            tuple(items),
            status="partial" if partial else "searched",
            more=found.page.more_available,
            detail=f"body_reads={len(selected)};metadata_omitted={omitted};"
            f"body_omissions={body_omissions}"[:160],
        )

    def _read_search_results(
        self, capability: MailQueryReadRequest, session: _Session, *, cancel: threading.Event
    ) -> tuple[bytes, ...]:
        def guard() -> None:
            now = self._check_session(session, cancel)
            with self._lock:
                catalog = self._catalog
                if (
                    self._task_read_grant is not capability
                    or catalog is None
                    or catalog.catalog_id != capability.catalog_id
                    or catalog.account != capability.search.account
                    or catalog.principal != capability.search.principal
                    or any(item not in catalog.message_ids for item in capability.message_ids)
                ):
                    raise GmailError("request_invalid")
            if now.wall >= capability.expires_at or now.monotonic >= capability.monotonic_deadline:
                raise GmailError("timeout")

        messages: list[bytes] = []
        try:
            for message_id in capability.message_ids:
                guard()
                raw = self._get(
                    session,
                    GMAIL_ORIGIN
                    + "/gmail/v1/users/me/messages/"
                    + quote(message_id, safe="")
                    + "?"
                    + urlencode({"format": "full", "fields": FULL_FIELDS}),
                    cancel=cancel,
                    max_bytes=MAX_MAIL_MESSAGE_BYTES,
                    deadline=capability.monotonic_deadline,
                )
                guard()
                if _json(raw).get("id") != message_id:
                    raise GmailError("account_mismatch")
                messages.append(raw)
                if sum(map(len, messages)) > MAX_MAIL_BATCH_BYTES:
                    raise GmailError("response_too_large")
            guard()
            return tuple(messages)
        finally:
            with self._lock:
                if self._task_read_grant is capability:
                    self._task_read_grant = None

    def list_inbox(self, *, cancel: threading.Event) -> GmailInboxPage:
        return self.list_headers(cancel=cancel)

    @staticmethod
    def _header_claims(value: object) -> tuple[MailHeaderClaim, ...]:
        if type(value) is not list:
            raise GmailError("response_invalid", response_diagnostic="metadata_headers_shape")
        if len(value) > len(METADATA_HEADERS):
            raise GmailError("response_invalid", response_diagnostic="metadata_header_count")
        rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        allowed = {name.lower() for name in METADATA_HEADERS}
        # Prevalidate all structure first. A control-only omission cannot conceal another failure.
        for row in value:
            if type(row) is not dict:
                raise GmailError("response_invalid", response_diagnostic="metadata_header_shape")
            try:
                name = _text(row.get("name"), 64).lower()
            except GmailError:
                raise GmailError(
                    "response_invalid", response_diagnostic="metadata_header_name"
                ) from None
            if name not in allowed:
                raise GmailError("response_invalid", response_diagnostic="metadata_header_name")
            if name in seen:
                raise GmailError(
                    "response_invalid", response_diagnostic="metadata_header_duplicate"
                )
            seen.add(name)
            raw = row.get("value")
            if type(raw) is not str:
                raise GmailError(
                    "response_invalid", response_diagnostic="metadata_header_value_type"
                )
            if len(raw) > 2048:
                raise GmailError(
                    "response_invalid", response_diagnostic="metadata_header_value_limit"
                )
            rows.append((name, re.sub(r"\r\n[ \t]+", " ", raw)))
        raw_controls = decoded_controls = False
        safe: list[tuple[str, str, int]] = []
        for index, (name, raw) in enumerate(rows):
            row_controls = False
            try:
                _header_text(raw)
            except GmailError as error:
                reason = gmail_response_diagnostic(
                    error.code, getattr(error, "response_diagnostic", None)
                )
                if reason != "metadata_header_value_controls":
                    raise
                raw_controls = row_controls = True
            # Even a control-bad field must pass decoding; codec failures remain atomic.
            try:
                chunks = decode_header(raw)
                decoded = "".join(
                    part.decode(charset or "ascii", errors="strict")
                    if isinstance(part, bytes)
                    else part
                    for part, charset in chunks
                )
            except (UnicodeError, LookupError, HeaderParseError):
                raise GmailError(
                    "response_invalid", response_diagnostic="metadata_header_decoding"
                ) from None
            try:
                _header_text(decoded, decoded=True)
            except GmailError as error:
                reason = gmail_response_diagnostic(
                    error.code, getattr(error, "response_diagnostic", None)
                )
                if reason != "metadata_header_decoded_controls":
                    raise
                decoded_controls = row_controls = True
            if not row_controls:
                safe.append((name, decoded, index))
        if raw_controls or decoded_controls:
            # One whole-message omission, with deterministic raw-control precedence.
            raise GmailError(
                "response_invalid",
                response_diagnostic="metadata_header_value_controls"
                if raw_controls
                else "metadata_header_decoded_controls",
            )
        try:
            return tuple(
                MailHeaderClaim(name=name, value=text, index=index) for name, text, index in safe
            )
        except ValidationError:
            raise GmailError(
                "response_invalid", response_diagnostic="metadata_header_decoding"
            ) from None

    def read_selected(self, request: MailReadRequest, *, cancel: threading.Event) -> MailReadResult:
        session = self._current_session(cancel)
        if type(request) is not MailReadRequest:
            raise GmailError("request_invalid")
        selection = request.grant.selection
        if (
            selection.principal != self._principal
            or selection.account != session.account.account
            or any(source.account != selection.account for source in selection.resources)
        ):
            raise GmailError("account_mismatch")
        if not 1 <= len(selection.resources) <= 10 or len(set(selection.resources)) != len(
            selection.resources
        ):
            raise GmailError("request_invalid")

        def guard() -> MailClock:
            now = self._check_session(session, cancel)
            with self._lock:
                catalog = self._catalog
                if (
                    catalog is None
                    or selection.catalog_id != catalog.catalog_id
                    or any(
                        source.message_id not in catalog.message_ids
                        for source in selection.resources
                    )
                ):
                    raise GmailError("request_invalid")
            if now.wall < request.created.wall or now.monotonic < request.created.monotonic:
                raise GmailError("request_invalid")
            if (
                now.wall >= request.expires_at
                or now.monotonic >= request.monotonic_deadline
                or now.wall >= request.grant.expires_at
                or now.monotonic >= request.grant.monotonic_deadline
            ):
                raise GmailError("timeout")
            return now

        guard()
        messages: list[bytes] = []
        size = 0
        for source in selection.resources:
            guard()
            url = (
                GMAIL_ORIGIN
                + "/gmail/v1/users/me/messages/"
                + quote(source.message_id, safe="")
                + "?"
                + urlencode({"format": "full", "fields": FULL_FIELDS})
            )
            raw = self._get(
                session,
                url,
                cancel=cancel,
                max_bytes=MAX_MAIL_MESSAGE_BYTES,
                deadline=request.monotonic_deadline,
            )
            guard()
            size += len(raw)
            if size > MAX_MAIL_BATCH_BYTES:
                raise GmailError("response_too_large")
            if _json(raw).get("id") != source.message_id:
                raise GmailError("account_mismatch")
            messages.append(raw)
        observed = guard()
        return MailReadResult(
            request_id=request.request_id,
            principal=self._principal,
            account=selection.account,
            generation=selection.generation,
            selection_revision=selection.revision,
            observed=observed,
            messages=tuple(messages),
        )

    def disconnect(self, *, revoke: bool = False) -> GmailDisconnectResult:
        """Forget locally; remote project-wide revocation requires explicit host confirmation."""
        with self._lock:
            if self._revoking:
                raise GmailError("login_in_progress")
            session, _ = self._forget()
            self._revoking = revoke and session is not None
        if session is None:
            return GmailDisconnectResult(remote="not_connected")
        if not revoke:
            return GmailDisconnectResult(remote="not_requested")
        try:
            self._http.request(
                "POST",
                REVOKE_URL,
                form={"token": (session.refresh or session.access).get_secret_value()},
                max_bytes=16384,
                cancel=threading.Event(),
            )
            return GmailDisconnectResult(remote="revoked")
        except GmailError as error:
            return GmailDisconnectResult(
                remote="unknown"
                if error.code in {"timeout", "http_unavailable", "cancelled"}
                else "failed"
            )
        finally:
            with self._lock:
                self._revoking = False
