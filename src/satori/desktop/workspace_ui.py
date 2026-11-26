"""Chat-first presentation shell; no provider, source or persistence access."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QResizeEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from satori.desktop.brand_ui import portrait

_STYLE = """
QWidget#SatoriWorkspace { background: #fbfcfa; color: #26352e; font-size: 13px; }
QWidget#SatoriWorkspace QLabel { color: #334239; background: transparent; }
QFrame#WorkspaceSidebar { background: #f0f3ed; border-right: 1px solid #e1e6dd; }
QLabel#WorkspaceBrand { font-size: 21px; font-weight: 600; color: #284f3d; }
QLabel#SidebarCaption { color: #7a857c; font-size: 11px; }
QLabel#WorkspaceTitle { font-size: 19px; font-weight: 600; color: #273b2e; }
QLabel#WorkspaceStatus { color: #778279; font-size: 12px; }
QLabel#WelcomeTitle { font-size: 31px; font-weight: 500; color: #304b3a; }
QLabel#WelcomeHint { font-size: 14px; color: #7b857d; }
QLabel#VoiceStatus { font-size: 11px; color: #748078; }
QLabel#SettingsTitle { font-size: 25px; font-weight: 600; color: #304b3a; }
QLabel#SettingsHint { color: #7b857d; font-size: 13px; }
QPushButton { padding: 7px 12px; min-height: 20px; border: 1px solid #dce2d8;
    border-radius: 9px; background: #ffffff; color: #34483a; }
QPushButton:hover { background: #edf3e9; border-color: #c4d2c0; }
QPushButton:pressed, QPushButton:checked { background: #dfe9da; }
QPushButton:disabled { color: #909a91; background: #f4f6f1; border-color: #e5e9e1; }
QPushButton#NewConversation { background: #e0eadb; border: 0; color: #2e5038;
    text-align: left; font-weight: 600; padding: 10px 14px; }
QPushButton#SidebarSettings, QPushButton#SettingsNav, QPushButton#BackToChat {
    background: transparent; border: 0; text-align: left; padding: 8px 13px; }
QPushButton#SettingsNav:checked, QPushButton#SettingsNav:hover,
QPushButton#SidebarSettings:hover, QPushButton#BackToChat:hover { background: #e1e9dc; }
QPushButton#HeaderConnections { border: 0; background: transparent; color: #677b6b; }
QPushButton#CalendarInChat { border: 0; background: transparent; color: #6b7e6e;
    padding: 3px 8px; min-height: 16px; text-align: left; }
QPushButton#SendMessage { background: #315c46; color: #ffffff; border: 0;
    border-radius: 12px; padding: 8px 17px; font-weight: 600; }
QPushButton#SendMessage:hover { background: #244b37; }
QPushButton#SendMessage:disabled { background: #dce5d8; color: #71806f; }
QListWidget#ConversationList { background: transparent; border: 0; outline: 0; }
QListWidget#ConversationList::item { padding: 10px 10px; margin: 2px 0;
    border-radius: 8px; color: #60705f; }
QListWidget#ConversationList::item:selected { background: #dfe8d8; color: #294931; }
QListWidget#ConversationList::item:hover { background: #e7ede2; }
QTabWidget::pane { border: 0; background: transparent; }
QTextEdit#ConversationTranscript { border: 0; background: transparent; font-size: 15px;
    color: #344139; selection-background-color: #dfe9d7; }
QFrame#MessageComposer { border: 1px solid #dce4d5; border-radius: 18px;
    background: #ffffff; }
QTextEdit#MessageEditor { border: 0; background: transparent; font-size: 14px;
    padding: 3px; color: #2e4034; selection-background-color: #dfe9d7; }
QScrollArea#SettingsScroll { border: 0; background: transparent; }
QFrame#SourceConnectionCard { background: transparent; border: 0;
    border-bottom: 1px solid #e3e8de; padding: 8px 0; }
QFrame#AssistantTaskCard { background: #f1f5ed; border: 1px solid #dae4d2;
    border-radius: 14px; }
QLabel#TaskCardTitle { font-size: 16px; font-weight: 600; color: #315c46; }
QLabel#TaskCardSource { font-size: 11px; color: #697b69; }
QFrame#AssistantTaskCard QTextEdit { border: 0; background: transparent; font-size: 14px; }
QPushButton#QuickAction { border: 1px solid #dfe6d9; border-radius: 12px;
    padding: 5px 10px; font-size: 12px; color: #56705a; background: #f7f9f4; }
"""


def show_plain_history(transcript: QTextEdit, blocks: list[str]) -> None:
    """Format known speaker labels; every content byte is still inserted as plain text."""
    transcript.clear()
    cursor = QTextCursor(transcript.document())
    body = QTextCharFormat()
    body.setFontWeight(int(QFont.Weight.Normal))
    body.setForeground(QColor("#344139"))
    label = QTextCharFormat()
    label.setFontWeight(int(QFont.Weight.DemiBold))
    label.setForeground(QColor("#315c46"))
    for index, block in enumerate(blocks):
        if index:
            cursor.insertText("\n\n", body)
        if block.startswith(("Ты:\n", "Сатори:\n")):
            speaker, _, content = block.partition("\n")
            cursor.insertText(speaker, label)
            cursor.insertText("\n" + content, body)
        else:
            cursor.insertText(block, body)


class ConversationPage(QWidget):
    """Keep the reading column bounded when the desktop window is wide."""

    def resizeEvent(self, event: QResizeEvent) -> None:
        layout = self.layout()
        if layout is not None:
            margin = max(28, (event.size().width() - 780) // 2)
            layout.setContentsMargins(margin, 0, margin, 20)
        super().resizeEvent(event)


class DesktopWorkspace(QWidget):
    """One persistent page stack and sidebar; signals are navigation intents only."""

    conversation_requested = Signal()
    settings_requested = Signal(str)
    new_conversation_requested = Signal()
    start_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SatoriWorkspace")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        palette = self.palette()
        for role, value in (
            (QPalette.ColorRole.Window, "#fbfcfa"),
            (QPalette.ColorRole.WindowText, "#26352e"),
            (QPalette.ColorRole.Base, "#ffffff"),
            (QPalette.ColorRole.Text, "#26352e"),
            (QPalette.ColorRole.Button, "#ffffff"),
            (QPalette.ColorRole.ButtonText, "#34483a"),
            (QPalette.ColorRole.Highlight, "#dfe9d7"),
            (QPalette.ColorRole.HighlightedText, "#26352e"),
        ):
            palette.setColor(role, QColor(value))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_STYLE)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("WorkspaceSidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(15, 26, 15, 17)
        side.setSpacing(14)
        brand_row = QHBoxLayout()
        brand_row.addWidget(portrait(44))
        brand = QLabel("Сатори")
        brand.setObjectName("WorkspaceBrand")
        brand_row.addWidget(brand, 1)
        side.addLayout(brand_row)
        self.sidebar_pages = QStackedWidget()
        side.addWidget(self.sidebar_pages, 1)

        conversations = QWidget()
        chats = QVBoxLayout(conversations)
        chats.setContentsMargins(0, 8, 0, 0)
        chats.setSpacing(14)
        self.new_button = QPushButton("Новый разговор")
        self.new_button.setObjectName("NewConversation")
        self.new_button.clicked.connect(self.new_conversation_requested)
        chats.addWidget(self.new_button)
        caption = QLabel("РАЗГОВОРЫ")
        caption.setObjectName("SidebarCaption")
        chats.addWidget(caption)
        self.sessions = QListWidget()
        self.sessions.setObjectName("ConversationList")
        self.sessions.setAccessibleName("История разговоров")
        chats.addWidget(self.sessions, 1)
        self.call_inbox_button = QPushButton("Сводки звонков")
        chats.addWidget(self.call_inbox_button)
        self.sidebar_pages.addWidget(conversations)

        settings = QWidget()
        self.settings_nav = QVBoxLayout(settings)
        self.settings_nav.setContentsMargins(0, 0, 0, 0)
        self.settings_nav.setSpacing(3)
        self.back_button = QPushButton("Назад в чат")
        self.back_button.setObjectName("BackToChat")
        self.back_button.clicked.connect(self.conversation_requested)
        self.settings_nav.addWidget(self.back_button)
        self.navigation: dict[str, QPushButton] = {}
        self.sidebar_pages.addWidget(settings)
        self.settings_button = QPushButton("Настройки и подключения")
        self.settings_button.setObjectName("SidebarSettings")
        self.settings_button.clicked.connect(lambda: self.settings_requested.emit("general"))
        side.addWidget(self.settings_button)
        outer.addWidget(sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(31, 20, 28, 12)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        self.title = QLabel("Разговор")
        self.title.setObjectName("WorkspaceTitle")
        heading.addWidget(self.title)
        self.status = QLabel("Диалог выключен")
        self.status.setObjectName("WorkspaceStatus")
        self.status.setAccessibleName("Состояние Сатори")
        heading.addWidget(self.status)
        header.addLayout(heading, 1)
        self.start_button = QPushButton("Включить диалог")
        self.start_button.clicked.connect(self.start_requested)
        header.addWidget(self.start_button)
        self.connections_button = QPushButton("Подключения")
        self.connections_button.setObjectName("HeaderConnections")
        self.connections_button.clicked.connect(lambda: self.settings_requested.emit("connections"))
        header.addWidget(self.connections_button)
        content.addLayout(header)
        self.pages = QTabWidget()
        self.pages.setDocumentMode(True)
        content.addWidget(self.pages, 1)
        outer.addLayout(content, 1)

    def add_settings_route(self, key: str, title: str) -> None:
        button = QPushButton(title)
        button.setObjectName("SettingsNav")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False: self.settings_requested.emit(key))
        self.navigation[key] = button
        self.settings_nav.addWidget(button)

    def finish_navigation(self) -> None:
        self.settings_nav.addStretch()
        self.pages.tabBar().hide()

    def show_route(self, key: str, title: str) -> None:
        chat = key == "conversation"
        self.sidebar_pages.setCurrentIndex(0 if chat else 1)
        self.title.setText(title)
        self.connections_button.setVisible(chat)
        for name, button in self.navigation.items():
            button.setChecked(name == key)

    def set_core_caption(
        self, detail: str, *, can_start: bool, has_background_errors: bool = False
    ) -> None:
        # Only deterministic application status reaches this label, never generated text.
        caption = detail.partition(" · ")[0].removeprefix("Диалог: ")
        if has_background_errors:
            caption += " · Есть ошибки обработки"
        self.status.setText(caption)
        self.status.setToolTip("Подробности: Настройки → Общие и Диагностика")
        self.start_button.setVisible(can_start)
