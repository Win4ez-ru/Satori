"""Qt presentation and owned stdio transport; domain writes belong to the Core worker."""

# ruff: noqa: RUF001 - intentional Russian user-facing text.

import json
import sys
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from PySide6.QtCore import QEvent, QLockFile, QProcess, QProcessEnvironment, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.calendar_chat_intake import is_calendar_chat_request
from satori.application.assistant.mail_lookup import parse_mail_chat_read_intent
from satori.application.assistant.reminder_contracts import ReminderRecord
from satori.application.assistant.routing import AssistantReadRoute, route_intent
from satori.application.assistant.source_answer_contracts import SourceTaskRequest
from satori.application.assistant.source_answer_intake import (
    classify_source_answer_followup,
    is_explicit_mailbox_lookup,
    parse_source_answer_intent,
)
from satori.application.retrieval.adoption import (
    MemoryIndexAdoptionReport,
    MemoryIndexAdoptionRequest,
)
from satori.desktop.assistant_controller import AssistantController
from satori.desktop.assistant_home import AssistantHome
from satori.desktop.brand_ui import portrait
from satori.desktop.calendar_bridge import CalendarHostResult, packaged_calendar_action_helper
from satori.desktop.calendar_chat_controller import CalendarChatController
from satori.desktop.calendar_chat_ui import CalendarConversationPanel
from satori.desktop.calendar_dialogue_ui import CalendarDialoguePanel
from satori.desktop.calendar_ui import CalendarPanel
from satori.desktop.install import autostart_enabled, set_autostart
from satori.desktop.mail_lookup_handoff import MailLookupHandoff
from satori.desktop.mail_ui import MailPanel
from satori.desktop.mobile_bridge import MobileBridge
from satori.desktop.mobile_gateway.contracts import GatewayFault, ScopeBinding
from satori.desktop.mobile_settings_ui import MobileProfileChangeError, MobileSettingsPanel
from satori.desktop.openai_runtime import CONFIG_NAME
from satori.desktop.openai_settings_ui import OpenAISettingsPanel
from satori.desktop.places.contracts import Principal as PlacesPrincipal
from satori.desktop.places.panel import PlacesPanel
from satori.desktop.public_sources_ui import PublicSourcesPanel
from satori.desktop.reminder_delivery import NativeReminderDeliveryPort
from satori.desktop.reminder_ui import ReminderPanel
from satori.desktop.settings import (
    DesktopPreferences,
    default_repository_directory,
    load_preferences,
    preferences_path,
    save_preferences,
)
from satori.desktop.source_answer_controller import ConversationSourceController
from satori.desktop.source_answer_ui import ConversationSourcePanel
from satori.desktop.source_status import SourceKey
from satori.desktop.task_cards_ui import TaskCardView
from satori.desktop.telegram_reply_wire import reply_qualification_for
from satori.desktop.telegram_reply_worker import verified_reply_protocol
from satori.desktop.telegram_runtime import load_runtime_resources, qualification_for
from satori.desktop.telegram_ui import NativeTelegramPanel
from satori.desktop.voice.expression import VoiceDelivery
from satori.desktop.voice_ui import VoiceContext, VoicePanel
from satori.desktop.worker import WorkerResponse
from satori.desktop.workspace_ui import ConversationPage, DesktopWorkspace, show_plain_history
from satori.infrastructure.artifacts import PrivateArtifactStore


class DesktopApplication(QApplication):
    """Reopen the existing window when the macOS application becomes active."""

    desktop_window: "DesktopWindow | None" = None

    def event(self, event: QEvent) -> bool:
        window = self.desktop_window
        if event.type() == QEvent.Type.Quit and window is not None and not window._quit_issued:
            # Native Quit must keep the event loop alive for the owned drain.
            # The final, latched Quit is allowed through after every owner closes.
            event.ignore()
            window.quit_app()
            return True
        if (
            event.type() == QEvent.Type.ApplicationActivate
            and window is not None
            and not window.quitting
        ):
            QTimer.singleShot(0, window.reveal)
        return super().event(event)


class DesktopWindow(QMainWindow):
    """Keep text, activity and recovery honest across a separate Core process."""

    def __init__(
        self,
        preferences: DesktopPreferences,
        *,
        diagnostics_path: Path | None = None,
        calendar_foreground: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self.preferences = preferences
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._discard_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.started.connect(self._started_once)
        self.buffer = bytearray()
        self.pending: dict[str, str] = {}
        self.send_scopes: dict[str, tuple[Path, str | None]] = {}
        self.send_client_requests: dict[str, str] = {}
        self.calendar_requests: dict[str, tuple[str, str]] = {}
        self.source_answer_requests: dict[str, tuple[str, str]] = {}
        self.memory_adoption_requests: dict[str, tuple[MemoryIndexAdoptionRequest, Path]] = {}
        self.supports_source_answers = False
        self._calendar_identity: str | None = None
        self._calendar_provider = ""
        self._calendar_profile = preferences.data_directory
        self._mail_lookup_epoch = 0
        self._mail_lookup_wall_floor: datetime | None = None
        self._mail_lookup_mono_floor: float | None = None
        self.session_id: str | None = None
        self.session_ids: list[str] = []
        self.closed_sessions: set[str] = set()
        self.viewing_closed = False
        self.synchronize_session = False
        self.busy = False
        self._background_failures = 0
        self.activated = False
        self.stopping = False
        self.quitting = False
        self._quit_started = False
        self._quit_issued = False
        self._shutdown_requests_issued = False
        self.restart_requested = False
        self.crashes: deque[float] = deque()
        self.events: deque[str] = deque(maxlen=200)
        self.diagnostics_path = diagnostics_path
        if diagnostics_path is not None:
            try:
                stored = json.loads(PrivateArtifactStore().read(diagnostics_path, max_bytes=64_000))
                if isinstance(stored, list) and all(isinstance(item, str) for item in stored):
                    self.events.extend(stored[-200:])
            except (ValueError, OSError):
                pass
        self.setWindowTitle("Сатори")
        self.resize(1000, 720)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.workspace = DesktopWorkspace(self)
        self.setPalette(self.workspace.palette())
        self.state = QLabel("Диалог выключен")
        self.state.setWordWrap(True)
        self.tabs = self.workspace.pages
        self.sessions = self.workspace.sessions
        self.sessions.currentRowChanged.connect(self.select_session)
        self.assistant_home: AssistantHome | None = None
        self._telegram_configuration_pending = True
        self.calendar_panel = CalendarPanel(preferences.data_directory, parent=self)
        self.mail_panel = MailPanel(
            preferences.data_directory,
            parent=self,
            client_configuration_path=preferences.gmail_client_configuration_path,
            on_client_configuration_path_changed=self._set_mail_client_configuration_path,
        )
        self.telegram_panel = NativeTelegramPanel(preferences.data_directory, parent=self)
        self.public_sources = PublicSourcesPanel(preferences.data_directory, parent=self)
        self.public_sources.shutdown_finished.connect(self._try_finish_quit)
        bundled_resources = Path(sys.executable).parent.parent / "Resources"
        places_helper = bundled_resources / "places-native" / "SatoriPlaces"
        self.places_panel = PlacesPanel(
            self._places_principal(),
            helper_path=places_helper if getattr(sys, "frozen", False) else None,
            parent=self,
        )
        self._places_shutdown_complete = False
        self.places_panel.shutdown_finished.connect(self._places_finished)
        self.calendar_panel.shutdown_finished.connect(self._try_finish_quit)
        self.mail_panel.shutdown_finished.connect(self._try_finish_quit)
        self.telegram_panel.shutdown_finished.connect(self._try_finish_quit)
        self.telegram_panel.controller.changed.connect(self._telegram_runtime_changed)
        self._telegram_runtime_changed()
        self._calendar_editor_revision = 0
        self.calendar_panel.controller.bind_dialogue(uuid4())
        self.reminder_panel = ReminderPanel(preferences.data_directory, parent=self)
        self.reminder_panel.controller.restore_existing()
        principal = self.calendar_panel.controller.principal
        self._mobile_scope = ScopeBinding(
            principal_id=f"mac-uid:{principal.os_uid}",
            profile_id=str(principal.profile_id),
            generation=0,
        )
        self.mobile_bridge = MobileBridge(
            self.process,
            lambda: self._mobile_scope,
            self.reminder_panel.controller,
            self,
            timezone_provider=lambda: "Europe/Moscow",
            clock=lambda: datetime.now(UTC),
            monotonic=monotonic,
        )
        self.mobile_settings = MobileSettingsPanel(
            self.mobile_bridge, lambda: self._mobile_scope, preferences.data_directory, self
        )
        self.notification_adapter: NativeReminderDeliveryPort | None = None
        reminder_helper = bundled_resources / "reminder-native" / "SatoriReminderNotifications.app"
        if getattr(sys, "frozen", False) and reminder_helper.is_dir():
            self.notification_adapter = NativeReminderDeliveryPort(reminder_helper, self)
            self.notification_adapter.shutdown_finished.connect(self._try_finish_quit)
            self.reminder_panel.controller.set_delivery_port(self.notification_adapter)
            self.reminder_panel.set_notification_adapter(self.notification_adapter)
        self.assistant = AssistantController(
            self.calendar_panel.controller,
            self.reminder_panel.controller,
            self,
            telegram_reply_handoff=self._telegram_reply_handoff,
            mail_lookup_handoff=self._mail_lookup_handoff,
        )
        self.calendar_chat = CalendarChatController(
            self.calendar_panel.controller,
            preferences.data_directory,
            self,
            conversational=True,
            foreground=calendar_foreground or self._calendar_foreground,
        )
        self.calendar_chat.command_requested.connect(self._calendar_command_requested)
        self.calendar_chat.host_read_result.connect(self._calendar_host_result)
        self.calendar_chat.revocation_failed.connect(self._calendar_revoke_failed)
        self.source_answer = ConversationSourceController(
            self.mail_panel.controller,
            self.telegram_panel.controller,
            preferences.data_directory,
            self.calendar_panel.controller.principal.profile_id,
            self,
            foreground=calendar_foreground or self._calendar_foreground,
            context_current=lambda scope, provider: (
                str(scope.session_id) == self.session_id
                and str(scope.identity_id) == self._calendar_identity
                and scope.profile_id == self.calendar_panel.controller.principal.profile_id
                and provider == self._calendar_provider
            ),
            supported=lambda: self.supports_source_answers,
        )
        self.source_answer.command_requested.connect(self._source_answer_command_requested)
        self.source_answer.route_ready.connect(self._assistant_route_ready)
        self.source_answer.cancel_requested.connect(self._source_answer_cancel_requested)
        self.source_answer.revocation_failed.connect(self._source_answer_revoke_failed)
        self.mail_panel.controller.changed.connect(self.source_answer.tick)
        self.telegram_panel.controller.changed.connect(self.source_answer.tick)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.applicationStateChanged.connect(self._calendar_app_state)
        self._build_chat()
        self.reminder_panel.controller.due.connect(self._reminder_due)
        self.memory_view = QTextEdit()
        self.memory_view.setReadOnly(True)
        memory_page = QWidget()
        memory_layout = QVBoxLayout(memory_page)
        memory_layout.addWidget(
            QLabel("Эпизодическая память Core. Просмотр не создаёт новые воспоминания.")
        )
        refresh_memory = QPushButton("Обновить память")
        refresh_memory.clicked.connect(lambda: self.request("memories"))
        memory_layout.addWidget(refresh_memory)
        memory_layout.addWidget(self.memory_view)
        memory_layout.addWidget(
            QLabel("Удаление и архивирование через GUI — следующий отдельный срез.")
        )
        self.tabs.addTab(memory_page, "Память")
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.tabs.addTab(self.diagnostics, "Диагностика")
        self.tabs.addTab(self.calendar_panel, "Календарь")
        self.tabs.addTab(self.mail_panel, "Почта")
        self.tabs.addTab(self.telegram_panel, "Telegram")
        self.tabs.addTab(self.reminder_panel, "Напоминания")
        self.tabs.addTab(self.places_panel, "Места и карты")
        self.tabs.addTab(self.mobile_settings, "iPhone")
        self.tabs.addTab(self.public_sources, "Файлы и веб")
        self.assistant_home = AssistantHome(
            {
                "calendar": lambda: self.calendar_panel.source_status,
                "mail": lambda: self.mail_panel.source_status,
                "telegram": lambda: self.telegram_panel.source_status,
            },
            self._open_source,
            self._open_conversation,
            parent=self,
            settings_mode=True,
        )
        self.tabs.insertTab(0, self.assistant_home, "Подключения")
        self._build_settings_workspace(memory_page)
        self.tabs.currentChanged.connect(self._workspace_changed)
        self.workspace.conversation_requested.connect(self._open_conversation)
        self.workspace.settings_requested.connect(self._open_settings_section)
        self.workspace.new_conversation_requested.connect(self._new_conversation)
        self.workspace.start_requested.connect(self.start)
        self.setCentralWidget(self.workspace)
        # Hidden settings pages must not resize the conversation at first show.
        # Source panels own their bounded scrolling and responsive controls.
        window_layout = self.layout()
        if window_layout is not None:
            window_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(900, 720)
        self._open_conversation()
        native_menu = self.menuBar().addMenu("Сатори")
        reopen = QAction("Открыть Сатори", self)
        reopen.triggered.connect(self.reveal)
        native_menu.addAction(reopen)
        self.quit_action = QAction("Завершить Сатори", self)
        self.quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.quit_app)
        native_menu.addAction(self.quit_action)
        menu = QMenu(self)
        for title, callback in (
            ("Открыть Сатори", self.reveal),
            ("Запустить Core", self.start),
            ("Остановить Core", self.stop),
            ("Перезапустить Core", self.restart),
            ("Настройки", self.edit_settings),
            ("Завершить приложение", self.quit_app),
        ):
            action = QAction(title, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Сатори")
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda _reason: self.reveal())
        self.tray.show()
        self.poll = QTimer(self)
        self.poll.setInterval(2000)
        self.poll.timeout.connect(self._poll_status)
        self.poll.start()
        self.log("Приложение открыто; микрофон выключен, голос сам не запускается.")

    def _open_source(self, source: SourceKey) -> None:
        self._open_settings_section(source)

    def _telegram_reply_handoff(self, intent: AssistantIntent) -> bool:
        if intent.kind != "telegram_reply" or intent.reply_text is None or not self.session_id:
            return False
        try:
            conversation = UUID(self.session_id)
        except ValueError:
            return False
        profile = self.preferences.data_directory
        self._open_source("telegram")
        accepted = self.telegram_panel.reply_panel.stage_text(
            intent.request_id,
            intent.reply_text,
            conversation,
            context_current=lambda: (
                self.session_id == str(conversation)
                and self.preferences.data_directory == profile
                and not self.viewing_closed
                and not self.quitting
                and self.telegram_panel._foreground()
            ),
        )
        if accepted:
            QTimer.singleShot(0, lambda: self._reveal_telegram_reply(intent.request_id))
        return accepted

    def _reveal_telegram_reply(self, request_id: UUID) -> None:
        panel = self.telegram_panel.reply_panel
        if (
            panel.handoff is not None
            and panel.handoff.request_id == request_id
            and panel._handoff_current()
        ):
            self.telegram_panel.scroll_area.ensureWidgetVisible(panel.editor, 0, 0)

    def _places_principal(self) -> PlacesPrincipal:
        principal = self.calendar_panel.controller.principal
        return PlacesPrincipal(
            uid=principal.os_uid,
            profile_id=str(principal.profile_id),
            app_session_id=str(principal.session_id),
            device_id="desktop-" + str(principal.session_id),
        )

    def _places_finished(self) -> None:
        self._places_shutdown_complete = True
        self._try_finish_quit()

    def _reminder_due(self, record: object) -> None:
        if (
            not isinstance(record, ReminderRecord)
            or record not in self.reminder_panel.controller.records
        ):
            return
        if self.isActiveWindow() and self.tabs.currentWidget() is self.conversation_page:
            self.assistant.show_due(record)
        button = self.workspace.navigation.get("reminders")
        if button is not None:
            button.setText("Напоминания · пора")
        if record.delivery_state not in {"scheduled", "delivered"}:
            # Foreground-process fallback only; this is not a delivery receipt.
            self.tray.showMessage("Сатори", "Пришло время напоминания. Открой список в Сатори.")

    def _open_conversation(self) -> None:
        self.tabs.setCurrentWidget(self.conversation_page)
        self.workspace.show_route("conversation", "Разговор")
        self.editor.setFocus()

    def _open_settings_section(self, section: str) -> None:
        target = self._workspace_routes.get(section)
        if target is None:
            return
        self.voice_panel.cancel()
        self.tabs.setCurrentWidget(target)
        self.workspace.show_route(section, self._workspace_titles[section])

    def _clear_mail_lookup(self) -> None:
        self._mail_lookup_epoch += 1
        self._mail_lookup_wall_floor = None
        self._mail_lookup_mono_floor = None
        if hasattr(self, "mail_panel"):
            self.mail_panel.clear_chat_lookup()

    def _mail_lookup_current(self, handoff: MailLookupHandoff) -> bool:
        now, wall = monotonic(), datetime.now(UTC)
        current = (
            handoff.current(
                profile=self.preferences.data_directory,
                session_id=self.session_id,
                epoch=self._mail_lookup_epoch,
                now=now,
                wall_now=wall,
            )
            and self.activated
            and not self.busy
            and not self.viewing_closed
            and not self.stopping
            and not self.quitting
            and self.isVisible()
            and not self.isMinimized()
            and self.tabs.currentWidget() is self.mail_panel
        )
        if current and handoff.scope == "mailbox":
            if (
                self._mail_lookup_wall_floor is not None and wall < self._mail_lookup_wall_floor
            ) or (self._mail_lookup_mono_floor is not None and now < self._mail_lookup_mono_floor):
                return False
            self._mail_lookup_wall_floor = wall
            self._mail_lookup_mono_floor = now
        return current

    def _mail_lookup_handoff(self, intent: AssistantIntent) -> bool:
        if (
            intent.kind not in {"selected_lookup", "mailbox_search"}
            or intent.source != "mail"
            or intent.query is None
            or self.session_id is None
            or not self.activated
            or self.busy
            or self.viewing_closed
            or self.stopping
            or self.quitting
            or not self.isVisible()
            or self.isMinimized()
        ):
            return False
        was_visible = self.mail_panel.isVisible()
        self._clear_mail_lookup()
        now = monotonic()
        wall = datetime.now(UTC)
        try:
            handoff = MailLookupHandoff(
                request_id=intent.request_id,
                query=intent.query,
                profile=self.preferences.data_directory,
                session_id=self.session_id,
                epoch=self._mail_lookup_epoch,
                issued_at=now,
                deadline=now + 300,
                scope="mailbox" if intent.kind == "mailbox_search" else "selected",
                issued_wall=wall if intent.kind == "mailbox_search" else None,
                expires_wall=wall + timedelta(seconds=300)
                if intent.kind == "mailbox_search"
                else None,
            )
        except ValueError:
            return False
        self._open_settings_section("mail")
        return self.mail_panel.stage_chat_lookup(
            handoff, self._mail_lookup_current, require_new_read=not was_visible
        )

    def _workspace_changed(self, _index: int) -> None:
        current = self.tabs.currentWidget()
        if current is self.conversation_page:
            self.calendar_chat.set_foreground(True)
            self.workspace.show_route("conversation", "Разговор")
            return
        self.source_answer.clear()
        self.assistant.clear_calendar_read()
        self.voice_panel.cancel()
        self.calendar_chat.set_foreground(False)
        if self.calendar_action.isChecked():
            self.calendar_action.setChecked(False)
        for key, page in self._workspace_routes.items():
            if page is current:
                self.workspace.show_route(key, self._workspace_titles[key])
                break

    def _set_state(self, text: str) -> None:
        self.state.setText(text)
        self.workspace.set_core_caption(
            text,
            can_start=self.process.state() == QProcess.ProcessState.NotRunning,
            has_background_errors=bool(self._background_failures),
        )

    def _open_gpt_settings(self) -> None:
        card = self.source_answer.card
        if (
            card is not None
            and card.status == "gpt_required"
            and not self.editor.toPlainText().strip()
        ):
            self.editor.setPlainText(card.query)
        self._open_settings_section("general")
        self.gpt_settings.use_saved_button.setFocus()

    def _configure_gpt_preferences(self, path: Path) -> bool:
        if (
            self.busy
            or self.stopping
            or self.quitting
            or path != self.preferences.data_directory / CONFIG_NAME
        ):
            return False
        try:
            updated = DesktopPreferences.model_validate(
                {
                    **self.preferences.model_dump(),
                    "runtime_provider": "openai",
                    "openai_runtime_config": path,
                }
            )
            save_preferences(updated)
        except (ValueError, OSError):
            return False
        self.preferences = updated
        self.gpt_settings.set_preferences(updated)
        self.restart()
        return True

    def _configure_local_preferences(self) -> bool:
        if self.busy or self.stopping or self.quitting:
            return False
        try:
            updated = DesktopPreferences.model_validate(
                {**self.preferences.model_dump(), "runtime_provider": "local"}
            )
            save_preferences(updated)
        except (ValueError, OSError):
            return False
        self.preferences = updated
        self.gpt_settings.set_preferences(updated)
        self.restart()
        return True

    def _prepare_memory_index(self) -> None:
        if (
            self.busy
            or self.stopping
            or self.quitting
            or not self.activated
            or self.viewing_closed
            or not self.supports_source_answers
            or self.process.state() != QProcess.ProcessState.Running
            or self._calendar_identity is None
            or self.session_id is None
        ):
            self.gpt_settings.memory_preparation_finished(
                "Сначала дождись готовности GPT и разговора."
            )
            return
        try:
            request = MemoryIndexAdoptionRequest(
                operation_id=uuid4(),
                identity_id=UUID(self._calendar_identity),
                session_id=UUID(self.session_id),
                confirmed=True,
                max_memories=100,
            )
        except (ValueError, TypeError):
            self.gpt_settings.memory_preparation_finished(
                "Не получилось подтвердить текущий разговор."
            )
            return
        self._reset_calendar_dialogue()
        self.request(
            "memory_index_adopt", {"memory_index_adoption": request.model_dump(mode="json")}
        )

    def _memory_adoption_result(
        self,
        binding: tuple[MemoryIndexAdoptionRequest, Path],
        result: dict[str, object] | None,
    ) -> None:
        request, profile = binding
        if (
            profile != self.preferences.data_directory
            or str(request.identity_id) != self._calendar_identity
            or str(request.session_id) != self.session_id
            or self.viewing_closed
            or not self.supports_source_answers
            or self.stopping
            or self.quitting
        ):
            return
        try:
            report = MemoryIndexAdoptionReport.model_validate_json(json.dumps(result))
            if report.operation_id != request.operation_id:
                raise ValueError("memory_index_result_mismatch")
        except (ValueError, TypeError):
            self.gpt_settings.memory_preparation_finished(
                "Результат подготовки памяти не подтверждён. Автоповтора нет."
            )
            return
        text = f"Подготовлено записей: {report.indexed} из {report.considered}."
        if report.failed:
            text += f" Не получилось подготовить: {report.failed}."
        if report.more_available:
            text += " Ещё есть записи; следующий пакет запускается отдельным нажатием."
        self.gpt_settings.memory_preparation_finished(text)

    def _build_settings_workspace(self, memory_page: QWidget) -> None:
        assert self.assistant_home is not None
        general = QScrollArea()
        general.setObjectName("SettingsScroll")
        general.setWidgetResizable(True)
        general_content = QWidget()
        general.setWidget(general_content)
        layout = QVBoxLayout(general_content)
        layout.setContentsMargins(32, 22, 32, 28)
        title = QLabel("Настройки Сатори")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        hint = QLabel("Параметры приложения, голос и подключения — в одном месте.")
        hint.setObjectName("SettingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.gpt_settings = OpenAISettingsPanel(
            self.preferences,
            configured=self._configure_gpt_preferences,
            use_local=self._configure_local_preferences,
            can_configure=lambda: not self.busy and not self.stopping and not self.quitting,
            parent=general_content,
        )
        self.gpt_settings.memory_preparation_requested.connect(self._prepare_memory_index)
        layout.addWidget(self.gpt_settings)
        configure = QPushButton("Модель, профиль и параметры голоса…")
        configure.clicked.connect(self.edit_profile_preferences)
        layout.addWidget(configure)
        layout.addSpacing(22)
        layout.addWidget(QLabel("Состояние диалога"))
        layout.addWidget(self.state)
        controls = QHBoxLayout()
        for text, callback in (
            ("Включить диалог", self.start),
            ("Остановить", self.stop),
            ("Перезапустить", self.restart),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        layout.addStretch()
        self.tabs.addTab(general, "Общие")

        voice = QWidget()
        voice_layout = QVBoxLayout(voice)
        voice_layout.setContentsMargins(32, 22, 32, 28)
        heading = QLabel("Голос")
        heading.setObjectName("SettingsTitle")
        voice_layout.addWidget(heading)
        description = QLabel(
            "Удерживай в разговоре кнопку «Голос», затем проверь распознанный текст. "
            "Отправка остаётся за тобой."
        )
        description.setObjectName("SettingsHint")
        description.setWordWrap(True)
        voice_layout.addWidget(description)
        preferences = QPushButton("Выбрать голос, темп и API-ключ…")
        preferences.clicked.connect(self.edit_profile_preferences)
        voice_layout.addWidget(preferences)
        voice_layout.addSpacing(16)
        voice_layout.addWidget(self.voice_panel.settings_controls)
        self.voice_panel.settings_controls.show()
        voice_layout.addStretch()
        self.voice_settings_page = QScrollArea()
        self.voice_settings_page.setObjectName("SettingsScroll")
        self.voice_settings_page.setWidgetResizable(True)
        self.voice_settings_page.setWidget(voice)
        self.tabs.addTab(self.voice_settings_page, "Голос")
        self._workspace_routes: dict[str, QWidget] = {
            "general": general,
            "voice": self.voice_settings_page,
            "connections": self.assistant_home,
            "calendar": self.calendar_panel,
            "mail": self.mail_panel,
            "telegram": self.telegram_panel,
            "reminders": self.reminder_panel,
            "places": self.places_panel,
            "mobile": self.mobile_settings,
            "public": self.public_sources,
            "memory": memory_page,
            "diagnostics": self.diagnostics,
        }
        self._workspace_titles = {
            "general": "Настройки",
            "voice": "Голос",
            "connections": "Подключения",
            "calendar": "Календарь",
            "mail": "Gmail",
            "telegram": "Telegram",
            "reminders": "Напоминания",
            "places": "Места и карты",
            "mobile": "iPhone",
            "public": "Файлы и веб",
            "memory": "Память",
            "diagnostics": "Диагностика",
        }
        for key, name in self._workspace_titles.items():
            self.workspace.add_settings_route(key, name)
        self.workspace.finish_navigation()
        self._set_state(self.state.text())

    def _telegram_runtime_changed(self) -> None:
        controller = self.telegram_panel.controller
        if self._telegram_configuration_pending and controller.process is None:
            # Read only fixed software bytes. The separate explicit Connect
            # action owns process creation, authentication and account I/O.
            self._telegram_configuration_pending = False
            try:
                resources = load_runtime_resources()
                controller.configure_runtime(
                    qualification_for(controller.principal), resources.root
                )
                verified_reply_protocol(resources.root)
                controller.configure_reviewed_replies(reply_qualification_for(controller.principal))
            except (OSError, ValueError):
                # Default unavailable state is honest for source-only/test builds.
                # Package failures must not expose paths or exception contents.
                pass
        if self.assistant_home is not None:
            self.assistant_home.refresh()

    def _build_chat(self) -> None:
        page = ConversationPage()
        self.conversation_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 0, 30, 20)
        layout.setSpacing(9)
        self.history_stack = QStackedWidget()
        self.history_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        welcome = QWidget()
        welcome_layout = QVBoxLayout(welcome)
        welcome_layout.setSpacing(12)
        welcome_layout.addStretch()
        welcome_layout.addWidget(portrait(160), 0, Qt.AlignmentFlag.AlignCenter)
        greeting = QLabel("Я рядом.")
        greeting.setObjectName("WelcomeTitle")
        greeting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(greeting)
        hint = QLabel("Обсудим, разберёмся или спланируем день.")
        hint.setObjectName("WelcomeHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(hint)
        welcome_layout.addStretch()
        self.history_stack.addWidget(welcome)
        self.transcript = QTextEdit()
        self.transcript.setObjectName("ConversationTranscript")
        self.transcript.setReadOnly(True)
        self.transcript.setAcceptRichText(False)
        self.transcript.document().setDocumentMargin(12)
        self.history_stack.addWidget(self.transcript)
        self.transcript.textChanged.connect(self._refresh_chat_placeholder)
        layout.addWidget(self.history_stack, 1)
        self.task_card = TaskCardView(self.assistant)
        self.task_card.action_requested.connect(self._open_settings_section)
        self.assistant.changed.connect(self._refresh_chat_placeholder)
        layout.addWidget(self.task_card)
        self.calendar_chat_panel = CalendarConversationPanel(self.calendar_chat, parent=page)
        self.calendar_chat_panel.settings_requested.connect(
            lambda: self._open_settings_section("calendar")
        )
        self.calendar_chat.changed.connect(self._refresh_chat_placeholder)
        layout.addWidget(self.calendar_chat_panel)
        self.source_answer_panel = ConversationSourcePanel(self.source_answer, parent=page)
        self.source_answer_panel.settings_requested.connect(self._open_gpt_settings)
        self.source_answer.changed.connect(self._refresh_chat_placeholder)
        layout.addWidget(self.source_answer_panel)
        quick = QHBoxLayout()
        for title, draft in (
            ("Планы на сегодня", "Что в календаре сегодня?"),
            ("Напоминания", "Покажи напоминания"),
            ("Напомнить…", "Напомни через 15 минут "),
            ("Добавить событие", "Создай «Название» завтра в 15:00 на час"),
        ):
            button = QPushButton(title)
            button.setObjectName("QuickAction")
            button.clicked.connect(lambda _checked=False, value=draft: self._task_draft(value))
            quick.addWidget(button)
        quick.addStretch()
        files_button = QPushButton("Файл или веб")
        files_button.setObjectName("QuickAction")
        files_button.clicked.connect(lambda: self._open_settings_section("public"))
        quick.addWidget(files_button)
        places_button = QPushButton("Найти место")
        places_button.setObjectName("QuickAction")
        places_button.clicked.connect(lambda: self._open_settings_section("places"))
        quick.addWidget(places_button)
        layout.addLayout(quick)
        self.calendar_action = QPushButton("Мой день")
        self.calendar_action.setToolTip("Посмотреть выбранные события календаря")
        self.calendar_action.setObjectName("CalendarInChat")
        self.calendar_action.setCheckable(True)
        layout.addWidget(self.calendar_action)
        self.calendar_dialogue_panel = CalendarDialoguePanel(
            self.calendar_panel.controller, parent=page
        )
        self.calendar_dialogue_scroll = QScrollArea()
        self.calendar_dialogue_scroll.setWidgetResizable(True)
        self.calendar_dialogue_scroll.setMaximumHeight(260)
        self.calendar_dialogue_scroll.setWidget(self.calendar_dialogue_panel)
        self.calendar_dialogue_scroll.hide()
        self.calendar_action.toggled.connect(self._toggle_calendar_dialogue)
        layout.addWidget(self.calendar_dialogue_scroll)
        self.setup = QPushButton("Создать Сатори в выбранном профиле")
        self.setup.clicked.connect(self.activate)
        self.setup.hide()
        layout.addWidget(self.setup)
        composer = QFrame()
        composer.setObjectName("MessageComposer")
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(13, 10, 13, 10)
        compose.setSpacing(5)
        self.editor = QTextEdit()
        self.editor.setObjectName("MessageEditor")
        self.editor.setFixedHeight(72)
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText("Напиши Сатори…")
        compose.addWidget(self.editor)
        footer = QHBoxLayout()
        self.voice_panel = VoicePanel(
            self.preferences,
            context=self._voice_context,
            apply_draft=self.editor.setPlainText,
            toggle_speech=self._set_speech_enabled,
            parent=page,
        )
        self.voice_panel.set_compact_presentation(True)
        self.voice_panel.status.setObjectName("VoiceStatus")
        self.editor.textChanged.connect(self.voice_panel.editor_changed)
        self.editor.textChanged.connect(self._calendar_editor_changed)
        footer.addWidget(self.voice_panel, 1)
        self.send_button = QPushButton("Отправить")
        self.send_button.setObjectName("SendMessage")
        self.send_button.setEnabled(False)
        self.send_button.setToolTip("Отправить сообщение: ⌘Enter / Ctrl+Enter")
        self.send_button.clicked.connect(self.send)
        footer.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        compose.addLayout(footer)
        layout.addWidget(composer)
        QShortcut(QKeySequence("Ctrl+Return"), self.editor, self.send)
        QShortcut(QKeySequence("Meta+Return"), self.editor, self.send)
        self.tabs.addTab(page, "Разговор")

    def _toggle_calendar_dialogue(self, visible: bool) -> None:
        if visible:
            self.assistant.clear()
        self.calendar_dialogue_scroll.setVisible(visible)
        if not visible:
            self.calendar_panel.controller.clear_dialogue()
        self._refresh_chat_placeholder()

    def _refresh_chat_placeholder(self) -> None:
        empty = self.transcript.document().isEmpty()
        tool_open = (
            self.assistant.card is not None
            or self.calendar_chat.card is not None
            or self.source_answer.card is not None
            or (hasattr(self, "calendar_action") and self.calendar_action.isChecked())
        )
        self.history_stack.setCurrentIndex(0 if empty and not tool_open else 1)

    def _task_draft(self, text: str) -> None:
        # Never overwrite a user's unsent draft through a suggestion.
        if not self.editor.toPlainText().strip():
            self.editor.setPlainText(text)
        self.editor.setFocus()

    def _calendar_editor_changed(self) -> None:
        self.source_answer.composer_changed()
        self.telegram_panel.reply_panel.retire_handoff()
        self._clear_mail_lookup()
        self._calendar_editor_revision += 1
        # No draft text enters the source contract, and no source callback writes the editor.
        self.calendar_panel.controller.clear_dialogue("draft_changed")

    def _reset_calendar_dialogue(self, *, preserve_calendar_task: bool = False) -> None:
        if self.memory_adoption_requests:
            self.memory_adoption_requests.clear()
            self.gpt_settings.memory_preparation_finished(
                "Контекст сменился; результат подготовки памяти не подтверждён. Автоповтора нет."
            )
        self.source_answer.clear()
        self.telegram_panel.reply_panel.retire_handoff()
        self._clear_mail_lookup()
        if hasattr(self, "calendar_chat") and not preserve_calendar_task:
            self.calendar_chat.clear()
        if hasattr(self, "assistant"):
            self.assistant.clear()
        if hasattr(self, "public_sources"):
            self.public_sources.bind_session(uuid4())
        self.calendar_panel.controller.bind_dialogue(uuid4())

    def _new_conversation(self) -> None:
        self._reset_calendar_dialogue()
        self.request("new_session")

    def _voice_context(self) -> VoiceContext:
        return VoiceContext(
            profile=self.preferences.data_directory,
            session_id=self.session_id,
            draft=self.editor.toPlainText(),
            ready=(
                self.activated
                and self.tabs.currentWidget() is self.conversation_page
                and not self.busy
                and not self.stopping
                and not self.quitting
                and not self.viewing_closed
                and self.isVisible()
                and not self.isMinimized()
                and self.process.state() == QProcess.ProcessState.Running
            ),
        )

    def _set_mail_client_configuration_path(self, path: Path | None) -> None:
        updated = DesktopPreferences.model_validate(
            {**self.preferences.model_dump(), "gmail_client_configuration_path": path}
        )
        save_preferences(updated)
        self.preferences = updated

    def _set_speech_enabled(self, enabled: bool) -> None:
        updated = DesktopPreferences.model_validate(
            {**self.preferences.model_dump(), "speak_replies": enabled}
        )
        try:
            save_preferences(updated)
        except (ValueError, OSError):
            self.voice_panel.set_preferences(self.preferences)
            QMessageBox.warning(self, "Настройки", "Сохранение настройки озвучивания недоступно.")
            return
        self.preferences = updated
        self.voice_panel.set_preferences(updated)

    def event(self, event: QEvent) -> bool:
        if hasattr(self, "source_answer") and (
            event.type() in {QEvent.Type.Hide, QEvent.Type.WindowDeactivate}
            or (event.type() == QEvent.Type.WindowStateChange and self.isMinimized())
        ):
            self.source_answer.clear()
            self.assistant.clear_calendar_read()
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "assistant"):
            self._clear_mail_lookup()
            self.calendar_chat.set_foreground(False)
            self.assistant.clear()
            self.calendar_panel.controller.clear_dialogue("context_changed")
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "calendar_chat"):
            self.calendar_chat.set_foreground(True)
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and hasattr(self, "voice_panel")
            and self.voice_panel.controller.activity in {"capturing", "transcribing"}
        ):
            self.voice_panel.cancel()
        return super().event(event)

    def log(self, event: str) -> None:
        self.events.append(event)
        if self.diagnostics_path is not None:
            try:
                PrivateArtifactStore().write(
                    self.diagnostics_path,
                    json.dumps(list(self.events), ensure_ascii=False).encode(),
                    overwrite=True,
                )
            except OSError:
                self.events.append("Сохранение диагностики недоступно.")
        speech_disclosure = (
            "Озвучивание ElevenLabs: передаётся только текст для произнесения."
            if self.preferences.speech_backend == "elevenlabs"
            else "Озвучивание локальное; облачная передача отключена."
        )
        self.diagnostics.setPlainText(
            f"Профиль: {self.preferences.data_directory}\n"
            + (
                "Модель: GPT API / Luna\n"
                if self.preferences.runtime_provider == "openai"
                else f"Модель: Ollama / {self.preferences.model}\n"
            )
            + f"{speech_disclosure}\n\n"
            + "\n".join(self.events)
        )

    def reveal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.stopping = False
        self.supports_source_answers = False
        self._background_failures = 0
        self.buffer.clear()
        self.process.setProgram(sys.executable)
        environment = QProcessEnvironment.systemEnvironment()
        environment_names = environment.keys()
        for name in environment_names:
            key = name.upper()
            if key.startswith(("OPENAI_", "SATORI_OPENAI_", "ELEVENLABS_", "ANTHROPIC_")) or any(
                part in key
                for part in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
            ):
                environment.remove(name)
        self.process.setProcessEnvironment(environment)
        worker_entry = (
            ["--worker"] if getattr(sys, "frozen", False) else ["-m", "satori.desktop.worker"]
        )
        calendar_helper = (
            packaged_calendar_action_helper(Path(sys.executable))
            if getattr(sys, "frozen", False)
            else None
        )
        calendar_arguments = (
            ["--calendar-action-helper", str(calendar_helper)]
            if calendar_helper is not None
            else []
        )
        if self.preferences.runtime_provider == "openai":
            if self.preferences.openai_runtime_config is None:
                self._set_state("Нужно подключить GPT в настройках Сатори")
                return
            model_arguments = [
                "--runtime-provider",
                "openai",
                "--openai-runtime-config",
                str(self.preferences.openai_runtime_config),
            ]
        else:
            model_arguments = ["--runtime-provider", "local", "--model", self.preferences.model]
        self.process.setArguments(
            [
                *worker_entry,
                "--data-directory",
                str(self.preferences.data_directory),
                "--repository-directory",
                str(self.preferences.repository_directory),
                *model_arguments,
                *calendar_arguments,
            ]
        )
        self.process.setWorkingDirectory(str(self.preferences.repository_directory))
        self.process.start()
        self._set_state("Диалог: запускается…")

    def _started_once(self) -> None:
        self.request("initialize")

    def _bind_calendar_context(self) -> None:
        if self.preferences.data_directory != self._calendar_profile:
            self.calendar_chat.set_profile(self.preferences.data_directory)
            self._calendar_profile = self.preferences.data_directory
        if (
            self.activated
            and not self.viewing_closed
            and self.session_id
            and self._calendar_identity
            and self._calendar_provider
        ):
            self.calendar_chat.bind_context(
                self._calendar_identity, self.session_id, self._calendar_provider
            )
        else:
            self.calendar_chat.clear()
        self._bind_source_answer_context()

    def _bind_source_answer_context(self) -> None:
        self.source_answer.set_profile(
            self.preferences.data_directory,
            self.calendar_panel.controller.principal.profile_id,
        )
        if (
            self.activated
            and not self.viewing_closed
            and self.session_id
            and self._calendar_identity
            and self._calendar_provider
        ):
            self.source_answer.bind_context(
                self._calendar_identity, self.session_id, self._calendar_provider
            )
        else:
            self.source_answer.unbind_context()

    def _assistant_route_ready(
        self, request: SourceTaskRequest, route: AssistantReadRoute, deadline: float
    ) -> None:
        if (
            self.busy
            or self.stopping
            or not self._calendar_foreground()
            or self.editor.toPlainText().strip()
            or str(request.scope.session_id) != self.session_id
            or str(request.scope.identity_id) != self._calendar_identity
            or request.scope.profile_id != self.calendar_panel.controller.principal.profile_id
            or not request.created_at <= datetime.now(UTC) < request.expires_at
            or monotonic() >= deadline
        ):
            return
        intent = route_intent(request, route)
        if route.action == "chat":
            self.request("send", {"text": request.user_text, "request_id": str(request.request_id)})
        elif intent is not None and intent.kind == "source_answer":
            self._bind_source_answer_context()
            self.source_answer.try_dispatch_routed(request, route, deadline)
        elif intent is not None:
            self.assistant.dispatch(intent)

    def _source_answer_command_requested(self, data: dict[str, Any]) -> None:
        request_id = data.get("request_id")
        command = data.get("source_answer")
        operation = command.get("operation") if isinstance(command, dict) else None
        if not isinstance(request_id, str) or not isinstance(operation, str):
            return
        if (
            self.busy
            or self.stopping
            or not self.activated
            or self.viewing_closed
            or data.get("session_id") != self.session_id
            or self.process.state() != QProcess.ProcessState.Running
        ):
            self.source_answer.accept_error(request_id, operation)
            return
        self.request("source_answer", data)

    def _source_answer_revoke_failed(self) -> None:
        self.log("Отзыв доступа к сообщениям не подтверждён; процесс остановлен.")
        self.stopping = True
        self.restart_requested = False
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.send_button.setEnabled(False)

    def _source_answer_cancel_requested(self, data: dict[str, Any]) -> None:
        if self.process.state() != QProcess.ProcessState.Running:
            return
        operation = str(uuid4())
        # A cancellation is ordered on the same private pipe, but never owns or
        # resets the busy flag of a newer plan/answer request.
        self.pending[operation] = "source_answer_cancel"
        payload = {"id": operation, "command": "source_answer", "data": data}
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        if self.process.write(raw) != len(raw):
            self.pending.pop(operation, None)
            self._source_answer_revoke_failed()

    def _calendar_command_requested(self, data: dict[str, Any]) -> None:
        request_id = data.get("request_id")
        command = data.get("calendar")
        operation = command.get("operation") if isinstance(command, dict) else None
        if not isinstance(request_id, str) or not isinstance(operation, str):
            return
        if (
            self.busy
            or self.stopping
            or not self.activated
            or self.viewing_closed
            or data.get("session_id") != self.session_id
            or self.process.state() != QProcess.ProcessState.Running
        ):
            self.calendar_chat.accept_error(request_id, operation, "calendar_host_unavailable")
            return
        self.request("calendar", data)

    def _calendar_host_result(self, data: dict[str, Any]) -> None:
        if self.process.state() != QProcess.ProcessState.Running:
            return
        try:
            result = CalendarHostResult.model_validate_json(json.dumps(data))
        except (ValueError, TypeError):
            return
        self.process.write((result.model_dump_json() + "\n").encode("utf-8"))

    def _calendar_revoke_failed(self) -> None:
        # A failed lease removal cannot establish revocation. Retire its consumer.
        self.log("Отзыв календарного доступа не подтверждён; процесс остановлен.")
        self.stopping = True
        self.restart_requested = False
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.send_button.setEnabled(False)

    def _calendar_app_state(self, state: Qt.ApplicationState) -> None:
        self.calendar_chat.set_foreground(state == Qt.ApplicationState.ApplicationActive)
        if state != Qt.ApplicationState.ApplicationActive:
            self.source_answer.clear()
            self.assistant.clear_calendar_read()

    def _calendar_foreground(self) -> bool:
        return bool(
            self.isVisible()
            and not self.isMinimized()
            and QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
            and self.tabs.currentWidget() is self.conversation_page
            and self.activated
            and not self.viewing_closed
            and not self.stopping
            and not self.quitting
        )

    def request(self, command: str, data: dict[str, Any] | None = None) -> None:
        if self.process.state() != QProcess.ProcessState.Running or self.stopping:
            return
        if command != "status" and self.busy:
            return
        if command in {"new_session", "resume_session", "activate", "send"}:
            self.voice_panel.cancel()
            if command == "send":
                self.calendar_chat.pause_for_chat()
            preserve_task = (
                (
                    command == "send"
                    or (
                        command == "resume_session"
                        and (data or {}).get("session_id") == self.session_id
                    )
                )
                and self.calendar_chat.card is not None
                and (command == "send" or self.calendar_chat.card.status == "unknown")
            )
            self._reset_calendar_dialogue(preserve_calendar_task=preserve_task)
        operation = str(uuid4())
        self.pending[operation] = command
        if command == "calendar":
            calendar = (data or {}).get("calendar")
            request_id = (data or {}).get("request_id")
            if isinstance(calendar, dict) and isinstance(request_id, str):
                self.calendar_requests[operation] = (request_id, str(calendar.get("operation", "")))
        if command == "source_answer":
            source_answer = (data or {}).get("source_answer")
            request_id = (data or {}).get("request_id")
            if isinstance(source_answer, dict) and isinstance(request_id, str):
                self.source_answer_requests[operation] = (
                    request_id,
                    str(source_answer.get("operation", "")),
                )
        if command == "memory_index_adopt":
            try:
                adoption = MemoryIndexAdoptionRequest.model_validate_json(
                    json.dumps((data or {}).get("memory_index_adoption"))
                )
            except (TypeError, ValueError):
                self.pending.pop(operation, None)
                self.gpt_settings.memory_preparation_finished("Запрос подготовки памяти отклонён.")
                return
            self.memory_adoption_requests[operation] = (adoption, self.preferences.data_directory)
        if command == "send":
            self.send_scopes[operation] = (self.preferences.data_directory, self.session_id)
            client_request = (data or {}).get("request_id")
            if isinstance(client_request, str):
                self.send_client_requests[operation] = client_request
        if command != "status":
            self.busy = True
            self.send_button.setEnabled(False)
        if command == "send":
            self.editor.setReadOnly(True)
        payload = {"id": operation, "command": command, "data": data or {}}
        self.process.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        if command == "send":
            self._set_state("Сатори отвечает…")

    def _read_output(self) -> None:
        self.buffer.extend(self.process.readAllStandardOutput().data())
        if len(self.buffer) > 4_000_000:
            self.buffer.clear()
            self.log("Ошибка: превышен размер ответа транспорта.")
            return
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            try:
                message = WorkerResponse.model_validate_json(line).model_dump(exclude_none=True)
                self._receive(message)
            except (ValueError, TypeError, KeyError):
                self.log("Получен некорректный ответ Core; содержимое скрыто.")

    def _receive(self, message: dict[str, Any]) -> None:
        if message.get("event") == "calendar_host_request":
            if (
                self.pending.get(message.get("id", "")) == "calendar"
                and message.get("id") in self.calendar_requests
                and isinstance(message.get("data"), dict)
            ):
                self.calendar_chat.handle_host_read(message["data"])
            return
        if self.mobile_bridge.receive(message):
            return
        command = self.pending.pop(message.get("id", ""), None)
        send_scope = self.send_scopes.pop(message.get("id", ""), None)
        client_request = self.send_client_requests.pop(message.get("id", ""), None)
        calendar_request = self.calendar_requests.pop(message.get("id", ""), None)
        source_answer_request = self.source_answer_requests.pop(message.get("id", ""), None)
        memory_adoption_request = self.memory_adoption_requests.pop(message.get("id", ""), None)
        if command is None:
            return
        if command == "source_answer_cancel":
            return
        if command != "status":
            self.busy = False
        if command == "send":
            self.editor.setReadOnly(False)
        self.send_button.setEnabled(
            self.activated and not self.busy and not self.stopping and not self.viewing_closed
        )
        if command == "memory_index_adopt":
            if memory_adoption_request is not None:
                value = message.get("data") if message.get("event") == "result" else None
                self._memory_adoption_result(
                    memory_adoption_request, value if isinstance(value, dict) else None
                )
            self.voice_panel.refresh()
            return
        if command == "source_answer":
            if source_answer_request is not None:
                request_id, operation = source_answer_request
                if message.get("event") == "error":
                    self.source_answer.accept_error(request_id, operation)
                elif isinstance(message.get("data"), dict):
                    self.source_answer.accept_result(request_id, operation, message["data"])
            self.voice_panel.refresh()
            return
        if command == "calendar":
            if calendar_request is not None:
                request_id, operation = calendar_request
                if message.get("event") == "error":
                    self.calendar_chat.accept_error(
                        request_id, operation, "calendar_host_unavailable"
                    )
                elif isinstance(message.get("data"), dict):
                    self.calendar_chat.accept_result(request_id, operation, message["data"])
            self.voice_panel.refresh()
            return
        if message.get("event") == "error":
            error = message.get("error", {})
            self._set_state("Операция не завершена")
            self.log(f"Ошибка Core: {error.get('code', 'unknown')}")
            if command != "status":
                QMessageBox.warning(self, "Сатори", str(error.get("message", "Ошибка Core")))
            self.voice_panel.refresh()
            return
        data = message.get("data") or {}
        received_session = data.get("session_id")
        if isinstance(received_session, str) and received_session != self.session_id:
            self._reset_calendar_dialogue()
        if command in {"initialize", "activate", "status"}:
            identity = data.get("identity_id")
            self._calendar_identity = identity if isinstance(identity, str) else None
            provider, model = data.get("provider"), data.get("model")
            if isinstance(provider, str) and isinstance(model, str):
                self._calendar_provider = provider + ":" + model
            self.activated = bool(data.get("activated"))
            self.setup.setVisible(not self.activated)
            self.session_id = data.get("session_id") or self.session_id
            self.supports_source_answers = data.get("supports_source_answers") is True
            self.gpt_settings.set_runtime_status(data)
            self.source_answer.tick()
            if not self.busy and not self.stopping:
                pending = data.get("post_response_pending", 0)
                failures = len(data.get("post_response_failures", []))
                self._background_failures = failures
                self._set_state(
                    f"Диалог: {'готова' if self.activated else 'нужна первоначальная настройка'} · "
                    f"память в работе: {pending} · ошибок обработки: {failures}"
                )
            self.send_button.setEnabled(
                self.activated and not self.busy and not self.stopping and not self.viewing_closed
            )
            if command in {"initialize", "activate"} and self.activated:
                self.synchronize_session = True
                self.request("history")
        elif command == "history":
            self._show_history(data)
        elif command in {"new_session", "resume_session"}:
            self.viewing_closed = False
            self.session_id = data["session_id"]
            self.request("history", {"session_id": self.session_id})
        elif command == "send":
            current_receipt = (
                send_scope == (self.preferences.data_directory, self.session_id)
                and client_request is not None
                and data.get("client_request_id") == client_request
                and isinstance(data.get("session_id"), str)
                and bool(data["session_id"])
                and (self.session_id is None or data["session_id"] == self.session_id)
                and isinstance(data.get("interaction_id"), str)
                and isinstance(data.get("text"), str)
            )
            if not current_receipt:
                self.log(
                    "Ответ не относится к текущей отправке. Черновик и выбранный чат сохранены."
                )
                self.voice_panel.refresh()
                return
            self.editor.clear()
            speech_eligible = not self.stopping and not self.quitting
            self.session_id = data.get("session_id", self.session_id)
            self.log("Ответ сохранён Core; фоновая обработка памяти учитывается отдельно.")
            if (
                speech_eligible
                and isinstance(data.get("interaction_id"), str)
                and isinstance(data.get("text"), str)
            ):
                try:
                    delivery = VoiceDelivery.model_validate_json(
                        json.dumps(data.get("voice_delivery"))
                    )
                except (TypeError, ValueError):
                    delivery = VoiceDelivery()
                self.voice_panel.committed_reply(
                    data["interaction_id"],
                    data["text"],
                    replayed=data.get("replayed") is not False,
                    delivery=delivery,
                )
            self.request("history", {"session_id": self.session_id})
        elif command == "memories":
            self.memory_view.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        elif command == "shutdown":
            failures = data.get("post_response_failures", [])
            self.log(f"Остановка Core: ошибок фоновой обработки за запуск — {len(failures)}.")
        self._bind_calendar_context()
        self.voice_panel.refresh()

    def _show_history(self, data: dict[str, Any]) -> None:
        sessions = data.get("sessions", [])
        if self.synchronize_session:
            self.synchronize_session = False
            available = [item["session_id"] for item in sessions if item.get("status") == "open"]
            self.viewing_closed = False
            if available:
                selected = self.session_id if self.session_id in available else available[-1]
                self.request("resume_session", {"session_id": selected})
            else:
                self.session_id = None
                self.request("new_session")
            return
        self.session_ids = [item["session_id"] for item in sessions]
        self.closed_sessions = {
            item["session_id"] for item in sessions if item.get("status") == "closed"
        }
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for index, item in enumerate(sessions):
            stamp = item.get("started_at", "")[:16].replace("T", " · ")
            title = stamp or f"Разговор {index + 1}"
            if item.get("status") == "closed":
                title += " · архив"
            self.sessions.addItem(title)
        if self.session_id in self.session_ids:
            self.sessions.setCurrentRow(self.session_ids.index(self.session_id))
        self.sessions.blockSignals(False)
        interactions = data.get("interactions", [])
        blocks = []
        if data.get("truncated"):
            blocks.append("[Показана ограниченная часть истории]")
        for item in interactions:
            if self.session_id and item.get("session_id") != self.session_id:
                continue
            blocks.append(f"Ты:\n{item.get('user_text') or '[текст недоступен]'}")
            if item.get("assistant_text") is not None:
                blocks.append(f"Сатори:\n{item['assistant_text']}")
            elif item.get("status") != "completed":
                blocks.append(f"[{item.get('status', 'unknown')}: завершённого ответа нет]")
        show_plain_history(self.transcript, blocks)
        self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())
        self._bind_calendar_context()

    def select_session(self, row: int) -> None:
        if self.busy or row < 0 or row >= len(self.session_ids):
            return
        if self.session_ids[row] == self.session_id and self.session_id not in self.closed_sessions:
            return
        self.voice_panel.cancel()
        self._reset_calendar_dialogue()
        selected = self.session_ids[row]
        if selected in self.closed_sessions:
            self.viewing_closed = True
            self.session_id = selected
            self.request("history", {"session_id": selected})
            self.send_button.setEnabled(False)
        else:
            self.request("resume_session", {"session_id": selected})

    def send(self) -> None:
        text = self.editor.toPlainText()
        if len(text) > 8000:
            QMessageBox.warning(
                self,
                "Длинное сообщение",
                "Лимит сообщения — 8000 символов. Текст сохранён в поле ввода.",
            )
            return
        if text.strip() and not self.busy and self.activated and not self.viewing_closed:
            if self.source_answer.answer_source_choice(text):
                self.editor.clear()
                self.voice_panel.cancel()
                return
            if (
                self.source_answer.pending_route_clarification
                and classify_source_answer_followup(text) == "clarification"
            ):
                self.editor.clear()
                self.voice_panel.cancel()
                if not self.source_answer.answer_clarification(text):
                    self.editor.setPlainText(text)
                return
            intent = self.assistant.parse(text, timezone="Europe/Moscow")
            if intent is not None and intent.kind in {"calendar_agenda", "calendar_gaps"}:
                if self._calendar_foreground():
                    self.source_answer.clear()
                    self.calendar_chat.pause_for_chat()
                    self.editor.clear()
                    self.voice_panel.cancel()
                    self.assistant.dispatch(intent)
                return
            mail_read = parse_mail_chat_read_intent(text, timezone="Europe/Moscow")
            if mail_read is not None:
                self._reset_calendar_dialogue()
                self.editor.clear()
                self.voice_panel.cancel()
                self._bind_source_answer_context()
                if not self.source_answer.try_dispatch_mailbox(text, mail_read):
                    self.editor.setPlainText(text)
                return
            source_intent = (
                intent if intent is not None and intent.kind == "source_answer" else None
            )
            if source_intent is None and not is_explicit_mailbox_lookup(text, intent):
                source_intent = parse_source_answer_intent(
                    text, now=datetime.now(UTC), timezone="Europe/Moscow"
                )
            if source_intent is not None:
                self._reset_calendar_dialogue()
                self.editor.clear()
                self.voice_panel.cancel()
                self._bind_source_answer_context()
                if not self.source_answer.try_dispatch(source_intent):
                    self.editor.setPlainText(text)
                return
            if self.source_answer.answer_followup(text):
                self.editor.clear()
                self.voice_panel.cancel()
                return
            self.calendar_chat.set_foreground(True)
            # Explicit source tasks are routed before Calendar intake or a pending clarification.
            if intent is not None and (
                intent.kind == "telegram_reply"
                or (
                    intent.kind in {"selected_lookup", "mailbox_search"} and intent.source == "mail"
                )
                or (intent.kind == "clarification" and intent.source in {"mail", "telegram"})
            ):
                self.calendar_chat.pause_for_chat()
                self.editor.clear()
                self.voice_panel.cancel()
                self.assistant.dispatch(intent)
                if (
                    intent.kind in {"telegram_reply", "selected_lookup", "mailbox_search"}
                    and self.assistant.card is not None
                    and self.assistant.card.status == "context_changed"
                ):
                    self._open_conversation()
                    self.editor.setPlainText(text)
                return
            if is_calendar_chat_request(text):
                self._reset_calendar_dialogue(preserve_calendar_task=True)
                self.editor.clear()
                self.voice_panel.cancel()
                self._bind_calendar_context()
                self.calendar_chat.submit(text)
                return
            if self.calendar_chat.answer(text):
                self.editor.clear()
                self.voice_panel.cancel()
                return
            if self.source_answer.pending_clarification:
                followup = classify_source_answer_followup(text)
                if followup == "cancel":
                    self.source_answer.clear()
                    self.editor.clear()
                    self.voice_panel.cancel()
                    return
                if (
                    intent is None
                    and followup == "clarification"
                    and self.source_answer.answer_clarification(text)
                ):
                    self.editor.clear()
                    self.voice_panel.cancel()
                    return
                self.source_answer.clear()
            if intent is not None:
                self.calendar_chat.pause_for_chat()
                # Capture and clear first: textChanged invalidates the preceding source read.
                self.editor.clear()
                self.voice_panel.cancel()
                self.assistant.dispatch(intent)
                return
            if self.supports_source_answers:
                self._reset_calendar_dialogue()
                self.editor.clear()
                self.voice_panel.cancel()
                self._bind_source_answer_context()
                if not self.source_answer.try_route(text):
                    self.editor.setPlainText(text)
                return
            self.request("send", {"text": text, "request_id": str(uuid4())})

    def activate(self) -> None:
        answer = QMessageBox.question(
            self,
            "Первый запуск",
            f"Создать новую identity Сатори в {self.preferences.data_directory}?\n"
            "Если Сатори уже существует, сначала выбери её каталог в настройках. "
            "Существующая база не заменяется.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.request("activate")

    def _poll_status(self) -> None:
        self.voice_panel.refresh()
        if not self.busy and "status" not in self.pending.values():
            self.request("status")

    def _discard_stderr(self) -> None:
        self.process.readAllStandardError()
        self.log("Core записал диагностическое событие; исходный текст скрыт.")

    def stop(self) -> None:
        self.supports_source_answers = False
        self.gpt_settings.core_stopped()
        self.mobile_bridge.invalidate_scope()
        self.mobile_settings.core_stopped()
        self.voice_panel.cancel()
        self._reset_calendar_dialogue()
        if self.process.state() == QProcess.ProcessState.NotRunning or self.stopping:
            return
        operation = str(uuid4())
        self.pending[operation] = "shutdown"
        self.process.write(
            (json.dumps({"id": operation, "command": "shutdown", "data": {}}) + "\n").encode()
        )
        self.stopping = True
        self.send_button.setEnabled(False)
        self._set_state("Остановка: ждём текущий ответ и обработку памяти…")
        self.voice_panel.refresh()

    def restart(self) -> None:
        self.voice_panel.cancel()
        self._reset_calendar_dialogue()
        self.restart_requested = True
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.restart_requested = False
            self.start()
        else:
            self.stop()

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        self.supports_source_answers = False
        self.gpt_settings.core_stopped()
        self.mobile_bridge.invalidate_scope()
        self.mobile_settings.core_stopped()
        self.log("Ошибка процесса Core. Проверь доступность Python и каталога установки.")
        self._set_state("Диалог недоступен")
        if _error == QProcess.ProcessError.FailedToStart:
            self.voice_panel.cancel()
            self._reset_calendar_dialogue()
            self.pending.clear()
            self.send_scopes.clear()
            self.send_client_requests.clear()
            self.calendar_requests.clear()
            self.source_answer_requests.clear()
            self.memory_adoption_requests.clear()
            self.busy = False
            self.editor.setReadOnly(False)
            self.activated = False
            self.send_button.setEnabled(False)
            if self.quitting:
                self._try_finish_quit()

    def _finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self.supports_source_answers = False
        self.gpt_settings.core_stopped()
        self.mobile_bridge.invalidate_scope()
        self.mobile_settings.core_stopped()
        self.voice_panel.cancel()
        self._reset_calendar_dialogue()
        expected = self.stopping and exit_code == 0
        interrupted = "send" in self.pending.values()
        self.pending.clear()
        self.send_scopes.clear()
        self.send_client_requests.clear()
        self.calendar_requests.clear()
        self.source_answer_requests.clear()
        self.memory_adoption_requests.clear()
        self.busy = False
        self.editor.setReadOnly(False)
        self.activated = False
        self.send_button.setEnabled(False)
        self._set_state("Диалог выключен")
        self.log(f"Core завершён: код {exit_code}.")
        if interrupted:
            self.log("Исход отправки неизвестен. Проверь историю; автоматического повтора нет.")
        if self.quitting:
            self._try_finish_quit()
            return
        if self.restart_requested:
            self.restart_requested = False
            self.start()
        elif not expected:
            now = monotonic()
            while self.crashes and now - self.crashes[0] > 60:
                self.crashes.popleft()
            if len(self.crashes) < 3:
                self.crashes.append(now)
                self.log("Восстанавливаем Core после сбоя; сообщения не повторяются.")
                QTimer.singleShot(1000 * len(self.crashes), self.start)
            else:
                self.log(
                    "Автовосстановление приостановлено после трёх сбоев. Доступен ручной запуск."
                )

    def edit_settings(self) -> None:
        self._open_settings_section("general")

    def edit_profile_preferences(self) -> None:
        if self.busy or self.stopping:
            QMessageBox.information(self, "Настройки", "Дождись окончания текущей операции.")
            return
        self.voice_panel.cancel()
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки Сатори")
        form = QFormLayout(dialog)
        model = QLineEdit(self.preferences.model)
        directory = QLineEdit(str(self.preferences.data_directory))
        choose = QPushButton("Выбрать каталог существующего профиля…")

        def select_directory() -> None:
            selected = QFileDialog.getExistingDirectory(dialog, "Профиль Сатори", directory.text())
            if selected:
                directory.setText(selected)

        choose.clicked.connect(select_directory)
        form.addRow("Локальная модель Ollama", model)
        form.addRow("Каталог satori.db и source-policy", directory)
        form.addRow(choose)
        voice_directory = QLineEdit(str(self.preferences.voice_model_directory))
        speech_directory = QLineEdit(str(self.preferences.speech_model_directory))
        speech_backend = QComboBox()
        speech_backend.setObjectName("speech_backend")
        speech_backend.addItem("ElevenLabs · облачное озвучивание", "elevenlabs")
        speech_backend.addItem("Локальный экспериментальный голос", "neural")
        speech_backend.addItem("Системный голос macOS · ручной выбор", "system")
        speech_backend.setCurrentIndex(speech_backend.findData(self.preferences.speech_backend))
        speech_voice = QLineEdit(self.preferences.speech_voice)
        speech_rate = QSpinBox()
        speech_rate.setRange(100, 260)
        speech_rate.setValue(self.preferences.speech_rate)
        cloud_voice = QLineEdit(self.preferences.elevenlabs_voice_id)
        cloud_voice.setObjectName("elevenlabs_voice_id")
        cloud_voice.setMaxLength(128)
        cloud_voice.setPlaceholderText("Voice ID выбранного голоса ElevenLabs")
        cloud_key = QLineEdit()
        cloud_key.setObjectName("elevenlabs_api_key")
        cloud_key.setEchoMode(QLineEdit.EchoMode.Password)
        cloud_key.setMaxLength(512)
        cloud_key.setPlaceholderText(
            "Ключ уже задан в этом запуске; оставь поле пустым для сохранения"
            if self.voice_panel.controller.has_cloud_credentials
            else "API-ключ: только до закрытия приложения"
        )
        forget_cloud_key = QCheckBox("Забыть API-ключ в этом запуске")
        forget_cloud_key.setObjectName("forget_elevenlabs_key")
        cloud_disclosure = QLabel(
            "Тембр определяется сохранённым Voice ID. Можно указать другой голос. "
            "ElevenLabs получает только текст, подготовленный для произнесения, "
            "или публичную тестовую фразу по кнопке «Прослушать голос». "
            "Микрофон и история чата не передаются. "
            "Расходуется платная квота; хранение — по правилам ElevenLabs. "
            "API-ключ хранится только в памяти этого запуска."
        )
        cloud_disclosure.setWordWrap(True)
        form.addRow("Каталог локальной модели распознавания", voice_directory)
        form.addRow("Озвучивание", speech_backend)
        form.addRow("Каталог экспериментального голоса", speech_directory)
        local_voice_warning = QLabel(
            "Экспериментальный режим: ударения, темп и естественность речи "
            "не прошли проверку. Модель загружается только отдельной кнопкой."
        )
        local_voice_warning.setWordWrap(True)
        form.addRow(local_voice_warning)
        form.addRow("Системный голос macOS", speech_voice)
        form.addRow("Темп озвучивания, слов/мин", speech_rate)
        form.addRow("ElevenLabs Voice ID", cloud_voice)
        form.addRow("ElevenLabs API-ключ", cloud_key)
        form.addRow(forget_cloud_key)
        form.addRow(cloud_disclosure)

        def select_speech_backend(_index: int) -> None:
            system = speech_backend.currentData() == "system"
            speech_voice.setEnabled(system)
            speech_rate.setEnabled(system)
            form.setRowVisible(speech_voice, system)
            form.setRowVisible(speech_rate, system)
            form.setRowVisible(speech_directory, speech_backend.currentData() == "neural")
            form.setRowVisible(local_voice_warning, speech_backend.currentData() == "neural")
            cloud = speech_backend.currentData() == "elevenlabs"
            for field in (cloud_voice, cloud_key, forget_cloud_key, cloud_disclosure):
                form.setRowVisible(field, cloud)

        speech_backend.currentIndexChanged.connect(select_speech_backend)
        select_speech_backend(speech_backend.currentIndex())
        form.addRow(
            QLabel(
                "GPT подключается в общих настройках. Здесь задаётся имя локальной модели "
                "и настраивается голос. Облачный голос включается явным выбором ElevenLabs."
            )
        )
        login = QCheckBox("Запускать это приложение при входе в macOS")
        bundle_path = _bundle_path()
        login.setEnabled(bundle_path is not None and sys.platform == "darwin")
        initially_enabled = False
        if bundle_path is not None and sys.platform == "darwin":
            try:
                initially_enabled = autostart_enabled(bundle_path)
                login.setChecked(initially_enabled)
            except (ValueError, OSError):
                login.setEnabled(False)
        form.addRow(login)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            cloud_key.clear()
            dialog.deleteLater()
            return
        entered_key = "" if forget_cloud_key.isChecked() else cloud_key.text()
        cloud_key.clear()
        try:
            updated = DesktopPreferences(
                data_directory=Path(directory.text()).expanduser(),
                repository_directory=self.preferences.repository_directory,
                model=model.text(),
                runtime_provider=self.preferences.runtime_provider,
                openai_runtime_config=self.preferences.openai_runtime_config
                if Path(directory.text()).expanduser() == self.preferences.data_directory
                else None,
                start_hidden=self.preferences.start_hidden,
                gmail_client_configuration_path=self.preferences.gmail_client_configuration_path,
                voice_model_directory=Path(voice_directory.text()).expanduser(),
                speech_model_directory=Path(speech_directory.text()).expanduser(),
                speech_backend=speech_backend.currentData(),
                elevenlabs_voice_id=cloud_voice.text(),
                speak_replies=(
                    self.preferences.speak_replies
                    if speech_backend.currentData() == self.preferences.speech_backend
                    else False
                ),
                speech_voice=speech_voice.text(),
                speech_rate=speech_rate.value(),
            )
            if updated.data_directory != self.preferences.data_directory:
                # Revoke old authority durably before saving/changing the active profile.
                self.mobile_bridge.invalidate_scope()
                self.mobile_settings.set_profile(updated.data_directory)
            if (
                bundle_path is not None
                and login.isEnabled()
                and login.isChecked() != initially_enabled
            ):
                set_autostart(login.isChecked(), bundle_path)
            save_preferences(updated)
        except (ValueError, OSError, GatewayFault, MobileProfileChangeError) as error:
            QMessageBox.warning(self, "Настройки не сохранены", type(error).__name__)
            dialog.deleteLater()
            return
        if forget_cloud_key.isChecked():
            self.voice_panel.controller.clear_cloud_credentials()
        if updated.speech_backend == "elevenlabs":
            self.voice_panel.controller.set_cloud_credentials(
                entered_key, updated.elevenlabs_voice_id
            )
        entered_key = ""
        dialog.deleteLater()
        profile_changed = updated.data_directory != self.preferences.data_directory
        self.preferences = updated
        self.gpt_settings.set_preferences(updated)
        self.voice_panel.set_preferences(updated)
        self.calendar_panel.set_profile(updated.data_directory)
        principal = self.calendar_panel.controller.principal
        self._mobile_scope = ScopeBinding(
            principal_id=f"mac-uid:{principal.os_uid}",
            profile_id=str(principal.profile_id),
            generation=0,
        )
        self.mail_panel.set_profile(updated.data_directory)
        if profile_changed:
            # Conceal may emit synchronously for the previous principal. Do not
            # requalify that identity while the owner is still switching it.
            self._telegram_configuration_pending = False
            self.telegram_panel.set_profile(updated.data_directory)
            self.places_panel.set_principal(self._places_principal())
            self.public_sources.set_profile(updated.data_directory)
            self.reminder_panel.controller.set_profile(updated.data_directory)
            self.reminder_panel.controller.restore_existing()
            self._telegram_configuration_pending = True
            self._telegram_runtime_changed()
        self._reset_calendar_dialogue()
        if profile_changed:
            self.transcript.clear()
            self.memory_view.clear()
            self.sessions.clear()
            self.session_ids.clear()
            self.closed_sessions.clear()
            self.editor.clear()
            self.viewing_closed = False
        if profile_changed:
            self.session_id = None
        self.log("Настройки сохранены; перезапускаем Core.")
        self.restart()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quit_issued:
            event.accept()
            return
        self.voice_panel.cancel()
        self.source_answer.clear()
        if QSystemTrayIcon.isSystemTrayAvailable() and not self.quitting:
            self.mail_panel.cancel()
            self.telegram_panel.conceal()
            self.hide()
            event.ignore()
        else:
            self.quit_app()
            if self._quit_issued:
                event.accept()
            else:
                event.ignore()

    def quit_app(self) -> None:
        if self._quit_started:
            return
        self._quit_started = True
        self.quitting = True
        self.voice_panel.cancel()
        self.mobile_settings.shutdown()
        self.mobile_bridge.shutdown()
        self.assistant.clear()
        self.reminder_panel.controller.shutdown()
        if self.notification_adapter is not None:
            self.notification_adapter.shutdown()
        self.places_panel.shutdown()
        self.public_sources.shutdown()
        self.calendar_panel.shutdown()
        self.mail_panel.shutdown()
        self.telegram_panel.shutdown()
        self._shutdown_requests_issued = True
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._try_finish_quit()
        else:
            self.stop()

    def _try_finish_quit(self) -> None:
        if (
            self.quitting
            and not self._quit_issued
            and self._shutdown_requests_issued
            and self.process.state() == QProcess.ProcessState.NotRunning
            and self.mail_panel.shutdown_complete
            and self.calendar_panel.shutdown_complete
            and self.telegram_panel.shutdown_complete
            and self._places_shutdown_complete
            and self.public_sources.shutdown_complete
            and (self.notification_adapter is None or self.notification_adapter.shutdown_complete)
        ):
            self._quit_issued = True
            QApplication.quit()


def _bundle_path() -> Path | None:
    import os

    candidate = os.environ.get("SATORI_DESKTOP_BUNDLE")
    if candidate is None and getattr(sys, "frozen", False):
        candidate = str(Path(sys.executable).parent.parent.parent)
    return Path(candidate) if candidate else None


def run_desktop(repository_directory: Path | None = None, *, hidden: bool = False) -> int:
    application = DesktopApplication(sys.argv[:1])
    application.setApplicationName("Satori")
    application.setOrganizationName("Satori")
    application.setQuitOnLastWindowClosed(False)
    if getattr(sys, "frozen", False):
        repository_directory = default_repository_directory()
    try:
        preferences = load_preferences(repository_directory=repository_directory)
        config_path = preferences_path()
        first_launch = not config_path.exists()
        config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except (ValueError, OSError):
        QMessageBox.warning(
            None,
            "Сатори",
            "Настройки недоступны. Проверь каталог установки и права доступа.",
        )
        return 2
    lock = QLockFile(str(config_path.with_suffix(".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(
            None, "Сатори", "Приложение уже открыто. Используй значок в строке меню."
        )
        return 0
    if first_launch:
        selected = initial_setup(preferences)
        if selected is None:
            return 0
        preferences = selected
    window = DesktopWindow(
        preferences, diagnostics_path=config_path.with_name("desktop-events.json")
    )
    application.desktop_window = window
    application.aboutToQuit.connect(window.calendar_panel.shutdown)
    application.aboutToQuit.connect(window.mail_panel.shutdown)
    application.aboutToQuit.connect(window.telegram_panel.shutdown)
    if not hidden or not QSystemTrayIcon.isSystemTrayAvailable():
        window.show()
    window.start()
    return application.exec()


def initial_setup(preferences: DesktopPreferences) -> DesktopPreferences | None:
    """Review storage before migrations, state access or new-identity activation."""
    dialog = QDialog()
    dialog.setWindowTitle("Первый запуск Сатори")
    form = QFormLayout(dialog)
    form.addRow(
        QLabel(
            "Выбери профиль: каталог базы satori.db и source-policy.\n"
            "Существующая база будет открыта на месте.\n"
            "Для нового профиля создание identity подтверждается отдельно."
        )
    )
    directory = QLineEdit(str(preferences.data_directory))
    model = QLineEdit(preferences.model)
    form.addRow("Профиль", directory)
    choose = QPushButton("Выбрать каталог…")

    def select_directory() -> None:
        selected = QFileDialog.getExistingDirectory(dialog, "Профиль Сатори", directory.text())
        if selected:
            directory.setText(selected)

    choose.clicked.connect(select_directory)
    form.addRow(choose)
    form.addRow("Локальная модель Ollama", model)
    form.addRow(QLabel("Микрофон, автозапуск и облачные сервисы выключены."))
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    try:
        selected_preferences = DesktopPreferences(
            repository_directory=preferences.repository_directory,
            data_directory=Path(directory.text()).expanduser(),
            model=model.text(),
        )
        save_preferences(selected_preferences)
        return selected_preferences
    except (ValueError, OSError):
        QMessageBox.warning(
            None, "Сатори", "Настройки не сохранены. Проверь абсолютный путь и имя модели."
        )
        return None
