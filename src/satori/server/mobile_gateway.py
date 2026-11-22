"""Explicit loopback-only composition of existing mobile owners and one Core."""

import asyncio
from collections.abc import Callable, Collection
from contextlib import suppress
from pathlib import Path

from satori.desktop.mobile_gateway.auth import AuthOwner
from satori.desktop.mobile_gateway.broker import Broker
from satori.desktop.mobile_gateway.contracts import GatewayFault, ScopeBinding
from satori.desktop.mobile_gateway.private_store import PrivateStore
from satori.desktop.mobile_gateway.server import GatewayConfig, GatewayServer
from satori.desktop.mobile_gateway.tls import prepare_tls
from satori.server.mobile_bridge import HeadlessMobileBridge
from satori.server.runtime import HeadlessRuntime


class LoopbackMobileGateway:
    """An explicit trusted host integration, not an unattended service entry point.

    The caller supplies an already started existing Core and an independently qualified
    scope. This class neither constructs another backend nor derives/adopts a binding.
    It takes lifecycle ownership of that runtime when start succeeds. Nothing listens
    at construction. No CLI, remote bind, iPhone host policy or data migration is added.
    """

    def __init__(
        self,
        runtime: HeadlessRuntime,
        scope_provider: Callable[[], ScopeBinding],
        namespace: Path,
        *,
        timezone: str,
        port: int = 8766,
    ) -> None:
        if type(port) is not int or not 0 <= port <= 65535:
            raise GatewayFault("invalid_request")
        self._runtime = runtime
        self._scope = scope_provider
        self._namespace = namespace
        self._timezone = timezone
        self._port = port
        self._store: PrivateStore | None = None
        self._auth: AuthOwner | None = None
        self._server: GatewayServer | None = None
        self._bridge: HeadlessMobileBridge | None = None
        self._used = False
        self._close_task: asyncio.Task[dict[str, object]] | None = None

    @property
    def port(self) -> int | None:
        return None if self._server is None else self._server.port

    def start(self) -> bool:
        """Explicitly enable a TLS listener on 127.0.0.1 only, once per instance."""
        if self._used or self._runtime.metadata().get("state") != "ready":
            raise GatewayFault("core_unavailable", 503)
        self._used = True
        store: PrivateStore | None = None
        server: GatewayServer | None = None
        try:
            bridge = HeadlessMobileBridge(self._runtime, self._scope, timezone=self._timezone)
            store = PrivateStore(self._namespace)
            identity = prepare_tls(store, "127.0.0.1")
            transport = identity.transport_id(self._port) if self._port else "loopback-starting"
            auth = AuthOwner(store, self._scope, transport)
            broker = Broker(store, bridge, auth.revalidate, self._scope)
            server = GatewayServer(
                GatewayConfig(True, "127.0.0.1", "127.0.0.1", self._port, False),
                auth,
                broker,
                identity,
            )
            if not server.start():
                raise GatewayFault("core_unavailable", 503)
            self._store, self._auth, self._server, self._bridge = store, auth, server, bridge
            return True
        except Exception:
            if server is not None:
                with suppress(Exception):
                    server.stop()
            if store is not None:
                store.close()
            raise GatewayFault("operation_failed", 503) from None

    def begin_pairing(self, capabilities: Collection[str]) -> str:
        """Trusted local owner action only; no code is logged or automatically displayed."""
        if self._auth is None or not set(capabilities) <= {"status", "history", "chat"}:
            raise GatewayFault("forbidden", 403)
        return self._auth.begin_pairing(capabilities)

    def revoke_all(self) -> None:
        if self._auth is None:
            raise GatewayFault("core_unavailable", 503)
        self._auth.revoke_all()

    async def close(self) -> dict[str, object]:
        """Drain once; caller cancellation does not release the underlying owner early."""
        self._used = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        return await asyncio.shield(self._close_task)

    async def _close(self) -> dict[str, object]:
        if self._server is None:
            return self._runtime.metadata()
        await asyncio.to_thread(self._server.stop)
        if self._bridge is not None:
            await self._bridge.close()
        try:
            return await self._runtime.close()
        finally:
            if self._store is not None:
                self._store.close()
            self._auth = None
