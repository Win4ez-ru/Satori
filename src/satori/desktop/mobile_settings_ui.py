"""Explicit desktop controls for the paired mobile transport, default off."""

from __future__ import annotations

import ipaddress
import math
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .mobile_gateway.auth import AuthOwner
    from .mobile_gateway.broker import Broker
    from .mobile_gateway.contracts import CoreBridge, ScopeBinding
    from .mobile_gateway.private_store import PrivateStore
    from .mobile_gateway.server import GatewayServer
    from .mobile_gateway.tls import TLSIdentity

# ruff: noqa: RUF001 - Russian user-facing copy


class MobileProfileChangeError(RuntimeError):
    """The old durable phone grant could not be revoked; keep the old profile selected."""

    def __init__(self) -> None:
        super().__init__("mobile_profile_revoke_failed")


@dataclass(slots=True, repr=False)
class _Runtime:
    store: PrivateStore
    auth: AuthOwner
    broker: Broker
    server: GatewayServer
    identity: TLSIdentity

    def close(self) -> None:
        try:
            self.server.stop()
        finally:
            try:
                self.broker.close()
            finally:
                self.store.close()


def _open_runtime(
    bridge: CoreBridge,
    scope_provider: Callable[[], ScopeBinding],
    namespace: Path,
    bind: str,
    advertised: str,
    port: int,
    lan: bool,
) -> _Runtime:
    # Importing this view and constructing its widgets never opens the transport owner.
    from .mobile_gateway.auth import AuthOwner
    from .mobile_gateway.broker import Broker
    from .mobile_gateway.private_store import PrivateStore
    from .mobile_gateway.server import GatewayConfig, GatewayServer
    from .mobile_gateway.tls import prepare_tls

    store = PrivateStore(namespace)
    broker: Broker | None = None
    server: GatewayServer | None = None
    try:
        identity = prepare_tls(store, advertised)
        auth = AuthOwner(store, scope_provider, identity.transport_id(port))
        broker = Broker(store, bridge, auth.revalidate, scope_provider)
        server = GatewayServer(
            GatewayConfig(True, bind, advertised, port, lan), auth, broker, identity
        )
        if not server.start():
            raise RuntimeError("mobile_start_failed")
        return _Runtime(store, auth, broker, server, identity)
    except Exception:
        if server is not None:
            with suppress(Exception):
                server.stop()
        if broker is not None:
            with suppress(Exception):
                broker.close()
        store.close()
        raise


def _revoke_namespace(namespace: Path, scope_provider: Callable[[], ScopeBinding]) -> None:
    """Explicit profile transitions revoke old grants even after an ordinary disabled restart."""
    try:
        namespace.lstat()
    except FileNotFoundError:
        return
    from .mobile_gateway.auth import AuthOwner
    from .mobile_gateway.private_store import PrivateStore

    store = PrivateStore(namespace)
    try:
        # A revocation-only identity cannot authenticate an old transport and never starts TLS.
        owner = AuthOwner(store, scope_provider, "profile-transition/" + str(uuid4()))
        owner.revoke_all()
    finally:
        store.close()


def _rotate_namespace(namespace: Path, host: str) -> str:
    from .mobile_gateway.private_store import PrivateStore
    from .mobile_gateway.tls import prepare_tls

    store = PrivateStore(namespace)
    try:
        return prepare_tls(store, host, rotate=True).sha256
    finally:
        store.close()


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setWordWrap(True)
    return label


class MobileSettingsPanel(QWidget):
    """One injected bridge, an explicit listener action, and local-only pairing controls."""

    def __init__(
        self,
        bridge: CoreBridge,
        scope_provider: Callable[[], ScopeBinding],
        data_directory: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._scope_provider = scope_provider
        self._data_directory = data_directory
        self._runtime: _Runtime | None = None
        self._revocation_required = False
        self._pair_deadline: float | None = None
        self._endpoint = ""
        self._pin = ""
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh_status)

        self.bind_host = QLineEdit("127.0.0.1")
        self.bind_host.setObjectName("mobile_bind_host")
        self.bind_host.setMaxLength(253)
        self.advertised_host = QLineEdit("127.0.0.1")
        self.advertised_host.setObjectName("mobile_advertised_host")
        self.advertised_host.setMaxLength(253)
        self.port = QSpinBox()
        self.port.setObjectName("mobile_port")
        self.port.setRange(1024, 65535)
        self.port.setValue(8766)
        self.allow_lan = QCheckBox("Разрешаю доступ по выбранному частному IP")
        self.allow_lan.setObjectName("mobile_allow_lan")
        self.enable_button = QPushButton("Включить подключение iPhone")
        self.disable_button = QPushButton("Выключить подключение")
        self.pair_button = QPushButton("Показать одноразовый код")
        self.revoke_button = QPushButton("Отозвать доступ телефона")
        self.rotate_button = QPushButton("Обновить сертификат и отвязать телефон")
        self.chat_capability = QCheckBox("Чат и новые разговоры")
        self.history_capability = QCheckBox("Список разговоров и история")
        self.reminders_capability = QCheckBox("Напоминания: просмотр, создание и отмена")
        self.capabilities_hint = _label()
        self.status_label = _label("Подключение выключено. Сервер сам не запускается.")
        self.endpoint_label = _label()
        self.fingerprint_label = _label()
        self.pair_code_label = _label()
        self.pair_code_label.setObjectName("mobile_pair_code")
        self.pair_timer_label = _label()
        for label in (self.endpoint_label, self.fingerprint_label):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        for field in (self.bind_host, self.advertised_host, self.port):
            field.setMinimumHeight(38)
        for button in (
            self.enable_button,
            self.disable_button,
            self.pair_button,
            self.revoke_button,
            self.rotate_button,
        ):
            button.setMinimumHeight(40)
        for checkbox in (
            self.allow_lan,
            self.chat_capability,
            self.history_capability,
            self.reminders_capability,
        ):
            checkbox.setMinimumHeight(32)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("MobileSettingsScroll")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self.scroll_content)
        layout.setContentsMargins(10, 10, 10, 16)
        layout.setSpacing(10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll_area.setWidget(self.scroll_content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll_area)
        layout.addWidget(
            _label(
                "iPhone подключается к этому Mac и использует ту же Сатори. "
                "Mac должен работать и быть доступен в локальной сети."
            )
        )
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Локальный IP для сервера", self.bind_host)
        form.addRow("Адрес для iPhone и TLS-сертификата", self.advertised_host)
        form.addRow("Порт", self.port)
        layout.addLayout(form)
        layout.addWidget(self.allow_lan)
        layout.addWidget(
            _label(
                "127.0.0.1 доступен только на этом Mac. Для телефона введи частный IP Mac "
                "или его точное имя .local. Адреса не определяются автоматически."
            )
        )
        layout.addWidget(
            _label(
                "Текущий iPhone-клиент поддерживает IPv4 и имена .local. "
                "IPv6 пока не поддерживается."
            )
        )
        controls = QHBoxLayout()
        controls.addWidget(self.enable_button)
        controls.addWidget(self.disable_button)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addWidget(self.endpoint_label)
        layout.addWidget(self.fingerprint_label)
        layout.addWidget(
            _label(
                "На iPhone вручную проверь адрес и SHA-256 сертификата перед связью. "
                "Разрешение локальной сети на телефоне не выдаётся автоматически."
            )
        )
        layout.addWidget(
            _label(
                "Права новой связи: состояние Сатори доступно всегда; остальные права выбери явно."
            )
        )
        layout.addWidget(
            _label(
                "Для изменения прав действующего телефона сначала отзови доступ "
                "и выполни новую связь."
            )
        )
        for checkbox in (self.chat_capability, self.history_capability, self.reminders_capability):
            layout.addWidget(checkbox)
            checkbox.toggled.connect(self._capabilities_edited)
        layout.addWidget(self.capabilities_hint)
        layout.addWidget(self.pair_button)
        layout.addWidget(self.pair_code_label)
        layout.addWidget(self.pair_timer_label)
        layout.addWidget(
            _label(
                "Код одноразовый: окно до 120 секунд, максимум 5 попыток. "
                "Он может быть уже использован; успешную связь проверь на телефоне."
            )
        )
        layout.addWidget(self.revoke_button)
        layout.addWidget(self.rotate_button)
        layout.addWidget(
            _label(
                "Выключение сервера сохраняет выданный доступ до его срока, максимум 30 дней. "
                "Отзыв удаляет доступ. Смена профиля отзывает старую связь."
            )
        )
        layout.addStretch(1)
        self.enable_button.clicked.connect(self._enable)
        self.disable_button.clicked.connect(self.shutdown)
        self.pair_button.clicked.connect(self._begin_pairing)
        self.revoke_button.clicked.connect(self._revoke)
        self.rotate_button.clicked.connect(self._rotate_certificate)
        self.bind_host.textChanged.connect(self._endpoint_edited)
        self.advertised_host.textChanged.connect(self._endpoint_edited)
        self.port.valueChanged.connect(self._endpoint_edited)
        self.allow_lan.toggled.connect(self._endpoint_edited)
        self._controls()

    @property
    def enabled(self) -> bool:
        return self._runtime is not None

    def _controls(self) -> None:
        self.enable_button.setEnabled(not self.enabled and not self._revocation_required)
        for button in (self.disable_button, self.pair_button):
            button.setEnabled(self.enabled)
        compatible = not self.chat_capability.isChecked() or self.history_capability.isChecked()
        self.pair_button.setEnabled(self.enabled and compatible)
        self.capabilities_hint.setText(
            "Для чата текущему iPhone-клиенту нужна история. Выбери её явно "
            "или сними доступ к чату."
            if not compatible
            else ""
        )
        self.revoke_button.setEnabled(True)
        self.rotate_button.setEnabled(True)

    def _configuration(self) -> tuple[str, str, int, bool]:
        from .mobile_gateway.tls import advertised_host, allowed_bind

        lan = self.allow_lan.isChecked()
        bind = allowed_bind(self.bind_host.text().strip(), lan_enabled=lan)
        if ipaddress.ip_address(bind).version != 4:
            raise ValueError("ipv4_required")
        advertised = advertised_host(self.advertised_host.text().strip())
        try:
            address = ipaddress.ip_address(advertised)
        except ValueError:
            if advertised != "localhost" and not advertised.endswith(".local"):
                raise ValueError("private_host_required") from None
        else:
            if address.version != 4:
                raise ValueError("ipv4_required")
            allowed_bind(advertised, lan_enabled=lan)
        return bind, advertised, self.port.value(), lan

    def _enable(self) -> None:
        if self.enabled or self._revocation_required:
            return
        try:
            bind, advertised, port, lan = self._configuration()
            runtime = _open_runtime(
                self._bridge,
                self._scope_provider,
                self._data_directory / "mobile-v1",
                bind,
                advertised,
                port,
                lan,
            )
            self._runtime = runtime
            host = f"[{advertised}]" if ":" in advertised else advertised
            self._endpoint = f"https://{host}:{runtime.server.port}"
            self._pin = runtime.identity.sha256
            self.endpoint_label.setText(self._endpoint)
            self.fingerprint_label.setText("TLS SHA-256: " + self._pin)
            self.status_label.setText("Сервер включён. Подключение проверь на телефоне.")
        except Exception:
            self.status_label.setText(
                "Подключение не включено. Проверь частный адрес, согласие на LAN, порт "
                "и прежний адрес сертификата. Используй IPv4 или имя .local. "
                "Небезопасное хранилище не исправляется автоматически."
            )
        self._controls()

    def _clear_pairing(self) -> None:
        self._timer.stop()
        self._pair_deadline = None
        self.pair_code_label.clear()
        self.pair_timer_label.clear()

    def _begin_pairing(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        if self.chat_capability.isChecked() and not self.history_capability.isChecked():
            self._controls()
            return
        self._clear_pairing()
        self._controls()
        capabilities = {"status"}
        for name, checkbox in (
            ("chat", self.chat_capability),
            ("history", self.history_capability),
            ("reminders", self.reminders_capability),
        ):
            if checkbox.isChecked():
                capabilities.add(name)
        try:
            code = runtime.auth.begin_pairing(capabilities)
            if len(code) != 6 or not code.isascii() or not code.isdigit():
                runtime.auth.cancel_pairing()
                raise ValueError("invalid_pairing_code")
            self.pair_code_label.setText(code)
            self._pair_deadline = time.monotonic() + 120
            self._timer.start()
            self.refresh_status()
        except Exception:
            self._clear_pairing()
            self.status_label.setText("Код не создан. Отключи подключение и проверь его настройки.")

    def refresh_status(self) -> None:
        if self._runtime is None or self._pair_deadline is None:
            return
        remaining = self._pair_deadline - time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            with suppress(Exception):
                self._runtime.auth.cancel_pairing()
            self._clear_pairing()
            return
        self.pair_timer_label.setText(f"До закрытия локального окна: {math.ceil(remaining)} с.")

    def _capabilities_edited(self) -> None:
        if self._runtime is not None:
            with suppress(Exception):
                self._runtime.auth.cancel_pairing()
        self._clear_pairing()
        self._controls()

    def _endpoint_edited(self) -> None:
        if self.enabled:
            self.shutdown()
            self.status_label.setText(
                "Адрес изменён. Подключение выключено; включи его заново явно."
            )

    def shutdown(self) -> None:
        self._clear_pairing()
        runtime, self._runtime = self._runtime, None
        self.endpoint_label.clear()
        self.fingerprint_label.clear()
        self._endpoint = ""
        self._pin = ""
        failed = False
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                failed = True
        self.status_label.setText(
            "Остановка подключения не подтверждена."
            if failed
            else "Подключение выключено. Если доступ был выдан, "
            "он сохранён до истечения или отзыва."
        )
        self._controls()

    def _revoke(self) -> None:
        try:
            if self._runtime is not None:
                self._runtime.auth.revoke_all()
            else:
                _revoke_namespace(self._data_directory / "mobile-v1", self._scope_provider)
        except Exception:
            self._revocation_required = True
            self.shutdown()
            self.status_label.setText(
                "Отзыв не подтверждён. Подключение остановлено; смена профиля заблокирована."
            )
            return
        self._revocation_required = False
        self.shutdown()
        self.status_label.setText(
            "Доступ телефона отозван. Новая связь требует включения и нового кода."
        )

    def set_profile(self, data_directory: Path) -> None:
        if data_directory == self._data_directory:
            return
        try:
            if self._runtime is not None:
                self._runtime.auth.revoke_all()
            else:
                _revoke_namespace(self._data_directory / "mobile-v1", self._scope_provider)
        except Exception:
            self._revocation_required = True
            self.shutdown()
            self.status_label.setText(
                "Старый доступ не удалось отозвать. Новый профиль не принят этой панелью."
            )
            raise MobileProfileChangeError from None
        self.shutdown()
        self._data_directory = data_directory
        self._revocation_required = False
        self.status_label.setText(
            "Профиль сменён, старая связь отозвана. Подключение остаётся выключенным."
        )
        self._controls()

    def _rotate_certificate(self) -> None:
        try:
            _, advertised, port, _ = self._configuration()
        except Exception:
            self.status_label.setText(
                "Сначала укажи допустимый частный IPv4 или имя .local и разрешение LAN."
            )
            return
        try:
            if self._runtime is not None:
                self._runtime.auth.revoke_all()
            else:
                _revoke_namespace(self._data_directory / "mobile-v1", self._scope_provider)
        except Exception:
            self._revocation_required = True
            self.shutdown()
            self.status_label.setText("Старый доступ не удалось отозвать. Сертификат не изменён.")
            return
        self.shutdown()
        self._revocation_required = False
        try:
            self._pin = _rotate_namespace(self._data_directory / "mobile-v1", advertised)
        except Exception:
            self.status_label.setText(
                "Доступ отозван, но обновление сертификата не подтверждено. Подключение выключено."
            )
            self._controls()
            return
        self._endpoint = f"https://{advertised}:{port}"
        self.endpoint_label.setText(self._endpoint)
        self.fingerprint_label.setText("TLS SHA-256: " + self._pin)
        self.status_label.setText(
            "Сертификат обновлён, прежний доступ отозван. Включи подключение и создай новую связь."
        )
        self._controls()

    def core_stopped(self) -> None:
        self.shutdown()
        self.status_label.setText(
            "Core остановлен. Подключение выключено; после запуска включи его вручную."
        )
