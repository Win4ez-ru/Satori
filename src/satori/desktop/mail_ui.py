"""Explicit Gmail read surface; external text never enters the conversation route."""

# ruff: noqa: RUF001 - intentional Russian user-facing text

import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.mail_contracts import (
    MAX_MAIL_MESSAGES,
    MailAccessDiagnostic,
    MailProjection,
    MailSnapshot,
    mail_access_diagnostic,
)
from satori.desktop.checklist_ui import CheckListWidget
from satori.desktop.mail_controller import MailActionReceipt, MailController
from satori.desktop.mail_lookup_handoff import MailLookupHandoff
from satori.desktop.source_status import SourceStatus
from satori.infrastructure.gmail import GmailMessageHeader
from satori.infrastructure.gmail.contracts import gmail_response_diagnostic

_ERROR = "Не удалось выполнить операцию. Данные не показаны; повтори действие явно."
_SEARCH_QUERY_LIMIT = 128
_SEARCH_HIT_LIMIT = 10
_SEARCH_EXCERPT_LIMIT = 160
_SEARCH_NO_SNAPSHOT = (
    "Поиск только по текущему снимку выбранных писем, не по всей почте. "
    "Сначала явно прочитай выбранные письма."
)
_STATUSES = {
    "disconnected": "Gmail отключён. Подключение действует только до закрытия Сатори.",
    "connecting": "Ожидаю вход в Google в системном браузере…",
    "connected": "Gmail подключён. Список входящих загружается отдельной кнопкой.",
    "listing": "Загружаю заголовки одной страницы входящих…",
    "searching": "Ищу по всей почте Gmail, включая спам и корзину…",
    "search_results": "Найденные письма показаны. Чтобы открыть текст, выбери нужное письмо ниже.",
    "search_empty": (
        "Gmail не нашёл писем по этому запросу. Попробуй другие слова или фильтры Gmail."
    ),
    "search_partial": "Результаты поиска частичные: часть заголовков недоступна.",
    "search_headers_unsupported": "Gmail нашёл письма, но их заголовки не прошли проверку.",
    "select_messages": "Выбери до 10 писем и нажми «Прочитать выбранные».",
    "reading": "Читаю только выбранные письма…",
    "ready": "Текст выбранных писем показан локально.",
    "ready_partial": (
        "Письма показаны в текстовом виде с ограничениями. Подробности — в служебных данных."
    ),
    "read_unsupported": (
        "В выбранных письмах нет поддерживаемой текстовой части. Ограничения указаны ниже."
    ),
    "select_none": "Выбор пуст. Отметь от 1 до 10 писем, чтобы прочитать их.",
    "selection_cleared": "Выбор снят, текст убран. Отметь письма для нового чтения.",
    "empty": "В этой странице входящих писем нет.",
    "listed_partial": (
        "Список входящих частичный: часть писем не допущена из-за заголовков. "
        "Выбери доступные письма для отдельного чтения."
    ),
    "headers_unsupported": (
        "В полученной странице есть письма, но их заголовки не допущены. "
        "Это не пустой INBOX. Чтение этих писем недоступно."
    ),
    "cancelled": "Операция отменена; текст писем убран.",
    "busy": "Дождись окончания операции или отмени её.",
    "shutting_down": "Отменяю операции Gmail и закрываю локальное подключение…",
    "revoking": (
        "Локальное подключение закрыто. Ожидаю ответа Google на отзыв доступа; "
        "результат пока неизвестен."
    ),
    "disconnected_remote_revoked": (
        "Локальное подключение закрыто. Google подтвердил отзыв доступа."
    ),
    "disconnected_remote_failed": (
        "Локальное подключение закрыто. Google не подтвердил отзыв; "
        "проверь доступ приложения в настройках аккаунта Google."
    ),
    "disconnected_remote_unknown": (
        "Локальное подключение закрыто. Результат отзыва у Google неизвестен; "
        "проверь доступ приложения в настройках аккаунта Google."
    ),
    "configuration_required": "Выбери JSON для Google OAuth Desktop client.",
    "configuration_invalid": "Этот файл не подходит для Google OAuth Desktop client.",
    "configuration_unavailable": "Выбранный файл недоступен. Выбери его заново.",
    "browser_unavailable": "Не удалось открыть системный браузер. Подключение отменено.",
    "authorization_invalid": "Адрес входа не прошёл проверку. Подключение отменено.",
    "login_in_progress": "Вход в Google уже ожидается.",
    "login_denied": "Вход или доступ отклонён в Google.",
    "callback_invalid": "Ответ входа Google не прошёл проверку. Подключись заново.",
    "state_mismatch": "Ответ входа относится к другой попытке. Подключись заново.",
    "timeout": "Время ожидания истекло. Повторное действие запускается вручную.",
    "not_connected": "Сначала подключи Gmail.",
    "remote_not_connected": "Для отзыва у Google сначала нужно действующее подключение.",
    "auth_expired": "Срок подключения истёк. Подключи Gmail заново.",
    "scope_rejected": "Google не выдал ожидаемый ограниченный набор прав.",
    "account_invalid": "Не удалось проверить аккаунт Google.",
    "account_mismatch": "Аккаунт изменился. Подключи Gmail заново.",
    "permission_denied": (
        "Google отклонил запрос: возможны ограничения частоты, проекта или аккаунта. "
        "Повтори нужное действие позже вручную."
    ),
    "http_unavailable": "Не удалось получить ответ Google. Повторное действие запускается вручную.",
    "http_error": "Google не выполнил запрос. Повторное действие запускается вручную.",
    "tls_unavailable": "Не удалось установить защищённое соединение с Google.",
    "response_invalid": "Ответ Google не прошёл проверку; данные не показаны.",
    "response_too_large": "Ответ Google превышает ограничение; данные не показаны.",
    "request_invalid": "Запрос не прошёл проверку; данные не прочитаны.",
    "clock_invalid": "Время изменилось. Разрешение на чтение сброшено.",
    "catalog_invalid": "Список писем не прошёл проверку. Загрузи входящие заново.",
    "catalog_expired": "Список писем устарел.",
    "selection_invalid": "Выбери от 1 до 10 писем из текущего списка.",
    "grant_required": "Подтверди выбранные письма кнопкой чтения.",
    "grant_expired": "Разрешение истекло. Подтверди выбранные письма заново.",
    "scope_mismatch": "Письма не соответствуют разрешённому выбору; данные не показаны.",
    "stale_request": "Результат относится к предыдущему запросу; данные не показаны.",
    "request_expired": "Время чтения истекло; повтори чтение вручную.",
    "result_invalid": "Результат чтения не прошёл проверку; данные не показаны.",
    "result_too_large": "Результат чтения превышает ограничение; данные не показаны.",
    "snapshot_expired": "Снимок писем устарел; текст убран.",
    "audit_unavailable": "Не удалось проверить разрешение; данные не показаны.",
    "invalid_message": "Формат письма не прошёл проверку; данные не показаны.",
    "message_too_large": "Письмо превышает ограничение; данные не показаны.",
    "batch_too_large": "Выбранные письма превышают ограничение; данные не показаны.",
    "unsupported_structure": "Структура письма не поддерживается; данные не показаны.",
    "invalid_header": "Заголовки письма не прошли проверку; данные не показаны.",
    "invalid_body": "Текст письма не прошёл проверку; данные не показаны.",
    "mime_limit": "Структура письма превышает ограничение; данные не показаны.",
    "operation_failed": _ERROR,
}
_DIAGNOSTICS: dict[MailAccessDiagnostic, str] = {
    "header_structure": "Некорректная структура заголовков.",
    "header_count_limit": "Превышено ограничение количества заголовков.",
    "header_name_type": "Имя заголовка имеет неподдерживаемый тип.",
    "header_name_limit": "Превышено ограничение длины имени заголовка.",
    "header_name_controls": "В имени заголовка есть управляющие символы.",
    "header_name_syntax": "Неподдерживаемая запись имени заголовка.",
    "header_value_type": "Значение заголовка имеет неподдерживаемый тип.",
    "header_value_limit": "Превышено ограничение длины заголовка.",
    "header_folding": "Неподдерживаемый перенос строки в заголовке.",
    "header_bare_cr": "В заголовке есть одиночный возврат каретки.",
    "header_bare_lf": "В заголовке есть одиночный перевод строки.",
    "header_format_controls": "В заголовке есть скрытые символы форматирования Unicode.",
    "header_surrogate_controls": "В заголовке есть недопустимые суррогатные символы Unicode.",
    "header_controls": "В заголовке есть управляющие символы.",
    "header_duplicate": "Повторён заголовок, который должен быть единственным.",
    "header_decoded_limit": "Превышено ограничение длины раскодированного заголовка.",
    "header_decoded_controls": "После раскодирования заголовка найдены управляющие символы.",
    "header_mime_mismatch": "Описание MIME не совпадает со структурой письма.",
    "header_mime_parameter": "Неподдерживаемая запись параметра MIME.",
    "header_mime_duplicate_parameter": "Повторён параметр MIME.",
    "header_mime_extended_charset": "Расширенная запись кодировки MIME не поддерживается.",
    "header_mime_charset": "В описании MIME не задана кодировка.",
    "body_structure": "Некорректная структура текстовой части.",
    "body_encoded_limit": "Превышено ограничение размера закодированной части.",
    "body_base64_alphabet": "В кодировке base64url есть неподдерживаемые символы.",
    "body_base64_length": "Некорректная длина кодировки base64url.",
    "body_base64_padding": "Некорректное заполнение кодировки base64url.",
    "body_base64_decode": "Не удалось раскодировать base64url.",
    "body_decoded_limit": "Превышено ограничение размера раскодированной части.",
    "body_base64_noncanonical": "Кодировка base64url не соответствует канонической записи.",
    "body_container_size": "Размер контейнера MIME не соответствует ожидаемой структуре.",
    "body_size_value": "Некорректно указан размер текстовой части.",
    "body_size_mismatch": "Указанный размер части не совпадает с раскодированными данными.",
    "body_charset_decode": "Байты текста не соответствуют заявленной кодировке.",
    "body_html_format_controls": (
        "В исходной HTML-части есть скрытые символы форматирования Unicode."
    ),
    "body_format_controls": "В тексте есть скрытые символы форматирования Unicode.",
    "body_surrogate_controls": "В тексте есть недопустимые суррогатные символы Unicode.",
    "body_controls": "В тексте есть неподдерживаемые управляющие символы.",
    "body_html_controls": "После извлечения текста HTML найдены управляющие символы.",
}
_RESPONSE_DIAGNOSTICS = {
    "http_status_type": "Неподдерживаемый формат HTTP-статуса.",
    "http_content_range": "Получен частичный HTTP-ответ вместо целого.",
    "http_content_encoding": "Неподдерживаемое кодирование HTTP-ответа.",
    "http_content_length_duplicate": "Повторено поле длины HTTP-ответа.",
    "http_transfer_encoding": "Некорректное описание передачи HTTP-ответа.",
    "http_content_length_syntax": "Некорректная запись длины HTTP-ответа.",
    "http_body_type": "Неподдерживаемый тип блока HTTP-ответа.",
    "http_body_length_mismatch": "Длина HTTP-ответа не совпала с объявленной.",
    "list_json": "Не удалось проверить JSON списка писем.",
    "list_rows_shape": "Неподдерживаемая структура списка писем.",
    "list_rows_count": "Превышено число записей одной страницы списка.",
    "list_row_shape": "Неподдерживаемая структура записи списка.",
    "list_identifier": "Некорректный формат идентификатора в метаданных списка.",
    "list_duplicate_identifier": "В списке повторён идентификатор письма.",
    "list_cursor": "Некорректный формат указателя следующей страницы.",
    "metadata_json": "Не удалось проверить JSON метаданных письма.",
    "metadata_identifier": "Некорректный формат идентификатора в метаданных письма.",
    "metadata_payload_shape": "Неподдерживаемая структура метаданных письма.",
    "metadata_headers_shape": "Неподдерживаемая структура списка заголовков.",
    "metadata_header_count": "Превышено число заголовков в метаданных.",
    "metadata_header_shape": "Неподдерживаемая структура одного заголовка.",
    "metadata_header_name": "Неподдерживаемое имя заголовка в метаданных.",
    "metadata_header_duplicate": "В метаданных повторён одиночный заголовок.",
    "metadata_header_value_type": "Неподдерживаемый тип значения заголовка.",
    "metadata_header_value_limit": "Значение заголовка превышает локальный предел длины.",
    "metadata_header_value_controls": "В значении заголовка найдены управляющие символы.",
    "metadata_header_decoding": "Не удалось проверить декодирование заголовка.",
    "metadata_header_decoded_limit": "Декодированный заголовок превышает локальный предел длины.",
    "metadata_header_decoded_controls": "В декодированном заголовке найдены управляющие символы.",
}
_OMISSIONS = {
    "attachment_omitted": "вложение пропущено",
    "nested_message_omitted": "вложенное письмо пропущено",
    "external_body_omitted": "внешнее содержимое пропущено",
    "unsupported_mime": "тип содержимого не поддерживается",
    "unsupported_charset": "кодировка не поддерживается",
    "html_lossy": "HTML преобразован в текст с потерей оформления",
    "html_discarded_format_controls": "символы форматирования в исключённом HTML пропущены",
    "empty_body": "доступного текста нет",
    "text_truncated": "текст сокращён из-за ограничения размера",
    "inline_resource_omitted": "встроенный ресурс пропущен",
    "alternative_omitted": "проверенное альтернативное представление пропущено",
    "alternative_not_decoded": "альтернативная часть не декодирована и не проверена",
    "header_decoding_unsupported": "кодировка заголовка не поддерживается",
}
_COMPLETENESS = {
    "complete": "полная",
    "partial": "неполная",
    "unsupported": "не поддерживается",
}
_HEADER_OMISSIONS = {
    "metadata_header_value_controls": "управляющие символы в значении заголовка",
    "metadata_header_decoded_controls": "управляющие символы после декодирования заголовка",
}
_CONNECTIONS = {
    "disconnected": "Подключение Gmail: нет активного входа.",
    "connecting": "Подключение Gmail: ожидается вход в Google.",
    "active": "Подключение Gmail: вход действует только в этой сессии Сатори.",
    "expired": "Подключение Gmail: срок входа истёк; нужен новый вход.",
    "closing": "Подключение Gmail: локальное подключение закрывается.",
}
_OPERATIONS = {
    "begin": "подготовка входа",
    "login": "вход в Google",
    "list": "загрузка входящих",
    "search": "поиск по всей почте Gmail",
    "read": "чтение выбранных писем",
    "disconnect": "локальное отключение",
    "revoke": "отзыв доступа у Google",
}
_RECEIPT_RESULTS = {
    "connected": "вход был выполнен",
    "listed": "список входящих был получен",
    "searched": "результаты поиска Gmail были получены",
    "searched_partial": "результаты поиска Gmail были получены частично",
    "search_headers_unsupported": "Gmail нашёл письма, но доступных заголовков нет",
    "search_empty": "Gmail вернул пустые результаты поиска",
    "listed_partial": "был получен частичный список входящих; часть писем не допущена",
    "headers_unsupported": "письма получены, но доступных заголовков в странице нет",
    "empty": "была получена пустая страница входящих",
    "ready": "выбранные письма были прочитаны",
    "ready_partial": "была получена текстовая версия писем с ограничениями",
    "read_unsupported": "поддерживаемый текст выбранных писем не был получен",
    "disconnected": "локальное подключение было закрыто",
    "disconnected_remote_revoked": "Google подтвердил отзыв доступа",
    "disconnected_remote_failed": (
        "Google не подтвердил отзыв доступа; проверь разрешения в аккаунте"
    ),
    "disconnected_remote_unknown": (
        "результат отзыва остался неизвестен; проверь разрешения в аккаунте"
    ),
}
_CURRENT_STATUSES = frozenset(
    {
        "disconnected",
        "connecting",
        "connected",
        "listing",
        "searching",
        "search_results",
        "search_partial",
        "search_headers_unsupported",
        "search_empty",
        "select_messages",
        "select_none",
        "selection_cleared",
        "reading",
        "ready",
        "ready_partial",
        "read_unsupported",
        "empty",
        "listed_partial",
        "headers_unsupported",
        "cancelled",
        "busy",
        "shutting_down",
        "revoking",
    }
)


def _time_text(value: datetime | None) -> str:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return "время не зафиксировано"
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M:%S UTC")


def _receipt_text(receipt: MailActionReceipt | None) -> str:
    if receipt is None:
        return "Последнее действие: в этой сессии ещё не выполнялось."
    operation = _OPERATIONS.get(receipt.operation, "операция")
    result = _RECEIPT_RESULTS.get(receipt.code, _STATUSES.get(receipt.code, _ERROR))
    diagnostic = mail_access_diagnostic(receipt.code, receipt.diagnostic)
    if diagnostic is not None:
        result += " Причина: " + _DIAGNOSTICS[diagnostic]
    response_diagnostic = gmail_response_diagnostic(
        receipt.code, getattr(receipt, "response_diagnostic", None)
    )
    if response_diagnostic is not None:
        result += " Причина: " + _RESPONSE_DIAGNOSTICS[response_diagnostic]
    result = result.rstrip(".")
    return (
        f"Последнее действие: {operation}; {_time_text(receipt.occurred_at)}. {result}. "
        "Это история действия, не состояние подключения или текста сейчас."
    )


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class MailPanel(QWidget):
    """Foreground account, metadata and exact selected-message controls."""

    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QWidget | None = None,
        *,
        controller: MailController | None = None,
        file_picker: Callable[[], Path | None] | None = None,
        confirm_revoke: Callable[[], bool] | None = None,
        client_configuration_path: Path | None = None,
        on_client_configuration_path_changed: Callable[[Path | None], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or MailController(data_directory, self)
        self._file_picker = file_picker or self._choose_configuration
        self._confirm_revoke = confirm_revoke or self._ask_revoke
        self._client_configuration_path = self._path_only(client_configuration_path)
        self._path_changed = on_client_configuration_path_changed
        self._preference_warning = (
            "Сохранённый выбор файла не подходит. Выбери файл заново."
            if client_configuration_path is not None and self._client_configuration_path is None
            else ""
        )
        self._headers: tuple[GmailMessageHeader, ...] = ()
        self._preview_snapshot_id: UUID | None = None
        self._search_snapshot_id: UUID | None = None
        self._search_revision = 0
        self._chat_lookup: MailLookupHandoff | None = None
        self._chat_lookup_guard: Callable[[MailLookupHandoff], bool] | None = None
        self._chat_lookup_bound_snapshot: UUID | None = None
        self._chat_lookup_requires_read = False
        self._chat_lookup_expected_read: UUID | None = None
        self._chat_lookup_search_started = False
        self._chat_lookup_timer = QTimer(self)
        self._chat_lookup_timer.setInterval(1000)
        self._chat_lookup_timer.timeout.connect(self._apply_chat_lookup)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        outer.addWidget(self.scroll_area)
        content = QWidget()
        self.scroll_area.setWidget(content)
        layout = QVBoxLayout(content)
        self.chat_lookup_summary = _plain_label()
        self.chat_lookup_summary.setObjectName("MailChatLookupSummary")
        self.chat_lookup_summary.setAccessibleName("Твой запрос поиска из чата")
        self.chat_lookup_summary.hide()
        layout.addWidget(self.chat_lookup_summary)
        self.mailbox_results = QPlainTextEdit()
        self.mailbox_results.setObjectName("MailMailboxSearchResults")
        self.mailbox_results.setReadOnly(True)
        self.mailbox_results.setMinimumHeight(150)
        self.mailbox_results.setMaximumHeight(230)
        self.mailbox_results.setAccessibleName("Найденные письма по всей почте Gmail")
        self.mailbox_results.hide()
        layout.addWidget(self.mailbox_results)
        self.disclosure = _plain_label(
            "Google выдаёт право чтения всей почты Gmail (gmail.readonly). Сатори ограничивает "
            "его локально: по твоему запросу ищет во всей почте, включая спам и корзину, "
            "и показывает до 20 найденных писем. Отдельное чтение текста — только выбранных "
            "тобой писем, до 10 за раз. Подключение хранится только в памяти до закрытия "
            "приложения; вход заново после перезапуска. Текст не отправляется модели, "
            "не озвучивается и не сохраняется в память или историю чата."
        )
        layout.addWidget(self.disclosure)
        connection = QHBoxLayout()
        self.connect_button = QPushButton("Подключить Gmail…")
        self.disconnect_button = QPushButton("Отключить локально")
        self.revoke_button = QPushButton("Отозвать доступ у Google…")
        self.disconnect_button.setToolTip(
            "Забыть подключение в Сатори. Разрешения аккаунта Google остаются."
        )
        self.revoke_button.setToolTip("Отдельно подтвердить отзыв разрешений этого Google-проекта")
        for button in (self.connect_button, self.disconnect_button, self.revoke_button):
            connection.addWidget(button)
        layout.addLayout(connection)
        configuration_actions = QHBoxLayout()
        self.choose_configuration_button = QPushButton("Выбрать другой файл…")
        self.forget_configuration_button = QPushButton("Не запоминать файл")
        self.choose_configuration_button.setToolTip(
            "Выбрать файл для следующего входа без обращения к Google"
        )
        self.forget_configuration_button.setToolTip(
            "Убрать только выбор файла; действующий вход и права Google сохраняются"
        )
        configuration_actions.addWidget(self.choose_configuration_button)
        configuration_actions.addWidget(self.forget_configuration_button)
        layout.addLayout(configuration_actions)
        self.configuration_choice = _plain_label()
        self.preference_warning = _plain_label()
        layout.addWidget(self.configuration_choice)
        layout.addWidget(self.preference_warning)
        self.account = _plain_label()
        layout.addWidget(self.account)
        self.connection_status = _plain_label()
        self.configuration_status = _plain_label()
        self.google_status = _plain_label()
        self.last_action = _plain_label()
        for label in (
            self.connection_status,
            self.configuration_status,
            self.google_status,
            self.last_action,
        ):
            layout.addWidget(label)
        actions = QHBoxLayout()
        self.list_button = QPushButton("Загрузить входящие (до 20)")
        self.cancel_button = QPushButton("Отменить")
        actions.addWidget(self.list_button)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.messages = CheckListWidget()
        self.messages.setAccessibleName("Заголовки входящих Gmail для выбора писем")
        self.messages.setMaximumHeight(150)
        layout.addWidget(self.messages)
        self.selection = _plain_label()
        layout.addWidget(self.selection)
        self.catalog_availability = _plain_label()
        layout.addWidget(self.catalog_availability)
        self.read_button = QPushButton("Прочитать выбранные")
        self.read_button.setToolTip(
            "Разрешить локальное чтение только отмеченных писем; вложения не загружаются"
        )
        reading = QHBoxLayout()
        self.clear_button = QPushButton("Снять выбор и убрать текст")
        self.clear_button.setToolTip("Убрать отметки и локальный предпросмотр без запроса к Google")
        reading.addWidget(self.read_button)
        reading.addWidget(self.clear_button)
        layout.addLayout(reading)
        self.status = _plain_label()
        self.source = _plain_label()
        self.freshness = _plain_label()
        for label in (self.status, self.source, self.freshness):
            layout.addWidget(label)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Локальный текст выбранных писем Gmail")
        layout.addWidget(self.preview)
        self.search_query = QLineEdit()
        self.search_query.setObjectName("MailSnapshotSearchQuery")
        self.search_query.setMaxLength(_SEARCH_QUERY_LIMIT)
        self.search_query.setAccessibleName("Точный поиск с учётом регистра в выбранных письмах")
        self.search_query.setPlaceholderText("Точный поиск с учётом регистра — до 128 символов")
        self.search_query.setToolTip(
            "Точный поиск с учётом регистра только в тексте текущего снимка выбранных писем"
        )
        layout.addWidget(self.search_query)
        self.search_status = _plain_label(_SEARCH_NO_SNAPSHOT)
        layout.addWidget(self.search_status)
        self.search_results = QPlainTextEdit()
        self.search_results.setObjectName("MailSnapshotSearchResults")
        self.search_results.setReadOnly(True)
        self.search_results.setMaximumHeight(170)
        self.search_results.setAccessibleName("Фрагменты совпадений в выбранных письмах")
        self.search_results.hide()
        layout.addWidget(self.search_results)
        self.search_query.textChanged.connect(self._search_query_changed)
        self.details_toggle = QPushButton("Показать служебные данные")
        self.details_toggle.setObjectName("MailDetailsToggle")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAccessibleName("Служебные заголовки и ограничения выбранных писем")
        self.details_toggle.setToolTip(
            "Локальные подробности уже прочитанных писем; без запроса к Google"
        )
        layout.addWidget(self.details_toggle)
        self.details_view = QPlainTextEdit()
        self.details_view.setReadOnly(True)
        self.details_view.setMaximumHeight(220)
        self.details_view.setAccessibleName("Служебные данные выбранных писем Gmail")
        self.details_view.hide()
        layout.addWidget(self.details_view)
        self.details_toggle.toggled.connect(self._toggle_details)
        self.connect_button.clicked.connect(self._connect)
        self.choose_configuration_button.clicked.connect(self._choose_other_configuration)
        self.forget_configuration_button.clicked.connect(self._forget_configuration)
        self.list_button.clicked.connect(self._list_inbox)
        self.read_button.clicked.connect(self._read)
        self.clear_button.clicked.connect(self._clear_selection)
        self.cancel_button.clicked.connect(self.cancel)
        self.disconnect_button.clicked.connect(self._disconnect_account)
        self.revoke_button.clicked.connect(self._revoke)
        self.messages.itemChanged.connect(self._selection_changed)
        self.controller.changed.connect(self._controller_changed)
        self.controller.shutdown_finished.connect(self.shutdown_finished.emit)
        self._render()

    @staticmethod
    def _path_only(value: Path | None) -> Path | None:
        if value is None or not isinstance(value, Path):
            return None
        text = str(value)
        if (
            not value.is_absolute()
            or ".." in value.parts
            or len(text) > 4096
            or any(unicodedata.category(char) in {"Cc", "Cs"} for char in text)
        ):
            return None
        return value

    @property
    def client_configuration_path(self) -> Path | None:
        return self._client_configuration_path

    @property
    def selection_count(self) -> int:
        return len(self._selected())

    @property
    def source_status(self) -> SourceStatus:
        status = self.controller.source_status
        # Checkbox count is presentation only; the existing owner still admits exact reads.
        return status.model_copy(
            update={
                "selected_count": (
                    self.selection_count
                    if status.connection == "connected"
                    and self.controller.catalog_observed is not None
                    else 0
                )
            }
        )

    @property
    def shutdown_complete(self) -> bool:
        return self.controller.shutdown_complete

    def set_profile(self, data_directory: Path) -> None:
        self.clear_chat_lookup()
        self._clear_search(clear_query=True)
        self.controller.set_profile(data_directory)

    def cancel(self) -> None:
        self.clear_chat_lookup()
        self._clear_search(clear_query=True)
        self.controller.cancel()

    def shutdown(self) -> None:
        self.clear_chat_lookup()
        self._clear_search(clear_query=True)
        self.controller.shutdown()

    def stage_chat_lookup(
        self,
        handoff: MailLookupHandoff,
        current: Callable[[MailLookupHandoff], bool],
        *,
        require_new_read: bool,
    ) -> bool:
        """Stage only user text; a hidden source never supplies search authority."""
        self.clear_chat_lookup()
        if not self.isVisible() or not current(handoff):
            return False
        snapshot = self.controller.snapshot if handoff.scope == "selected" else None
        self._chat_lookup = handoff
        self._chat_lookup_guard = current
        self._chat_lookup_requires_read = handoff.scope == "selected" and (
            require_new_read or snapshot is None
        )
        self._chat_lookup_timer.start()
        self._apply_chat_lookup()
        # Keep the own-query status and the connection controls at the start of the source view.
        # Do not jump to the search/results group below the explicit Connect/Read workflow.
        self.scroll_area.verticalScrollBar().setValue(0)
        return self._chat_lookup is handoff

    def clear_chat_lookup(self) -> None:
        handoff = self._chat_lookup
        active = handoff is not None
        self._chat_lookup = None
        self._chat_lookup_guard = None
        self._chat_lookup_bound_snapshot = None
        self._chat_lookup_requires_read = False
        self._chat_lookup_expected_read = None
        self._chat_lookup_search_started = False
        self._chat_lookup_timer.stop()
        self.chat_lookup_summary.clear()
        self.chat_lookup_summary.hide()
        self.mailbox_results.clear()
        self.mailbox_results.hide()
        if handoff is not None and handoff.scope == "mailbox":
            self.controller.cancel_search(handoff.request_id)
        if active:
            self._clear_search(clear_query=True)

    def _chat_lookup_current(self) -> bool:
        return (
            self._chat_lookup is not None
            and self._chat_lookup_guard is not None
            and self.isVisible()
            and self._chat_lookup_guard(self._chat_lookup)
        )

    def _stage_query_text(self, query: str) -> None:
        self.chat_lookup_summary.setText(
            "Запрос из чата: " + query + "\n"
            "Подключи Gmail при необходимости, выбери письма и нажми «Прочитать выбранные»."
        )
        self.chat_lookup_summary.show()
        previous = self.search_query.blockSignals(True)
        try:
            self.search_query.setText(query)
        finally:
            self.search_query.blockSignals(previous)
        self._clear_search(
            clear_query=False,
            status="Запрос из чата сохранён только здесь. Выбери письма и явно нажми "
            "«Прочитать выбранные»; поиск выполнится по новому доступному снимку. "
            "Весь аккаунт не просматривается.",
        )

    def _apply_chat_lookup(self) -> None:
        handoff = self._chat_lookup
        if handoff is None:
            return
        if not self._chat_lookup_current():
            self.clear_chat_lookup()
            return
        if handoff.scope == "mailbox":
            self._render_mailbox_lookup()
            if (
                not self._chat_lookup_search_started
                and not self.controller.busy
                and self.controller.account is not None
            ):
                # Set before invoking: the synchronous changed signal re-enters this method.
                self._chat_lookup_search_started = True
                self.controller.search_mailbox(handoff.query, handoff.request_id)
                self._render_mailbox_lookup()
            return
        snapshot = self.controller.snapshot
        if snapshot is None or (
            self._chat_lookup_requires_read
            and (
                self._chat_lookup_expected_read is None
                or snapshot.request.request_id != self._chat_lookup_expected_read
            )
        ):
            # No source body or old hidden snapshot is searched while awaiting explicit Read.
            self._stage_query_text(handoff.query)
            return
        if (
            self._chat_lookup_bound_snapshot is not None
            and snapshot.snapshot_id != self._chat_lookup_bound_snapshot
        ):
            self.clear_chat_lookup()
            return
        self._chat_lookup_requires_read = False
        self._chat_lookup_bound_snapshot = snapshot.snapshot_id
        if (
            self._search_snapshot_id == snapshot.snapshot_id
            and self.search_query.text() == handoff.query
        ):
            return
        self.set_search_query(handoff.query)

    def _render_mailbox_lookup(self) -> None:
        handoff = self._chat_lookup
        if handoff is None or handoff.scope != "mailbox":
            self.mailbox_results.clear()
            self.mailbox_results.hide()
            return
        if not self._chat_lookup_current():
            self.clear_chat_lookup()
            return
        rows: list[str] = []
        if self.controller.search_result_id == handoff.request_id:
            headers = self.controller.headers
            rows = [
                f"{index}. {header.subject or '(Без темы)'}\n"
                f"От: {header.sender or '(не указан)'} · {header.date or '(дата не указана)'}"
                for index, header in enumerate(headers, start=1)
            ]
            state = f"Показано найденных писем: {len(headers)} (до 20)."
            if self.controller.more_available:
                state += " Есть ещё результаты; уточни запрос, чтобы сузить поиск."
            if self.controller.header_omissions:
                state += " Часть заголовков недоступна; список неполный."
        elif self.controller.account is None:
            state = "Подключи Gmail, затем повтори запрос в чате. Письма выбирать не нужно."
        elif not self._chat_lookup_search_started:
            state = "Поиск начнётся после завершения текущей операции."
        else:
            state = _STATUSES.get(self.controller.status, _ERROR)
        self.chat_lookup_summary.setText(
            "Запрос из чата: " + handoff.query + "\n"
            "Область: вся почта Gmail, включая спам и корзину. " + state
        )
        self.chat_lookup_summary.show()
        text = "\n\n".join(rows)
        if self.mailbox_results.toPlainText() != text:
            self.mailbox_results.setPlainText(text)
        self.mailbox_results.setVisible(bool(rows))

    def _list_inbox(self) -> None:
        self.clear_chat_lookup()
        self.controller.list_inbox()

    def set_search_query(self, text: str) -> None:
        """Set a local query draft; never connect, list, read or renew source authority."""
        if type(text) is not str:
            self._clear_search(clear_query=True)
            raise TypeError("mail_search_query_invalid")
        bounded = text[:_SEARCH_QUERY_LIMIT]
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in bounded):
            self._clear_search(
                clear_query=True, status="Введите точный текст без управляющих символов."
            )
            return
        if bounded == self.search_query.text():
            self._render()
        else:
            self.search_query.setText(bounded)

    def _controller_changed(self) -> None:
        self._search_revision += 1
        self._render()
        self._apply_chat_lookup()

    def _search_query_changed(self, text: str) -> None:
        if self._chat_lookup is not None and text != self._chat_lookup.query:
            self.clear_chat_lookup()
            # Preserve the user's replacement as an ordinary source-local search draft.
            previous = self.search_query.blockSignals(True)
            try:
                self.search_query.setText(text)
            finally:
                self.search_query.blockSignals(previous)
        self._search_revision += 1
        self._render()

    def _clear_search(self, *, clear_query: bool, status: str = _SEARCH_NO_SNAPSHOT) -> None:
        self._search_revision += 1
        revision = self._search_revision
        self._search_snapshot_id = None
        if clear_query:
            previous = self.search_query.blockSignals(True)
            try:
                self.search_query.clear()
            finally:
                self.search_query.blockSignals(previous)
        self.search_status.setText(status)
        self.search_results.hide()
        if revision == self._search_revision and self.search_results.toPlainText():
            self.search_results.clear()

    def _disconnect_account(self) -> None:
        self.clear_chat_lookup()
        self._clear_search(clear_query=True)
        self.controller.disconnect_account()

    def _choose_configuration(self) -> Path | None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выбери JSON Google OAuth Desktop client",
            "",
            "Google OAuth JSON (*.json)",
        )
        return Path(filename) if filename else None

    def _set_configuration_path(self, path: Path | None) -> None:
        if self._chat_lookup is None or self._chat_lookup.scope != "mailbox":
            self.clear_chat_lookup()
        self._client_configuration_path = path
        self.controller.configuration_path_changed()
        self._preference_warning = ""
        if self._path_changed is not None:
            try:
                self._path_changed(path)
            except Exception:
                self._preference_warning = (
                    "Выбор применён в этом запуске; сохранение для следующего запуска "
                    "не подтверждено."
                )
        self._render()

    def _pick_configuration(self) -> Path | None:
        try:
            chosen = self._file_picker()
        except Exception:
            self._preference_warning = "Не удалось открыть выбор файла. Повтори действие вручную."
            self._render()
            return None
        if chosen is None:
            return None
        path = self._path_only(chosen)
        if path is None:
            self._preference_warning = "Выбор файла не подходит. Выбери файл заново."
            self._render()
            return None
        self._set_configuration_path(path)
        return path

    def _choose_other_configuration(self) -> None:
        if not self.controller.busy and not self.controller.shutting_down:
            self._pick_configuration()

    def _forget_configuration(self) -> None:
        if not self.controller.busy and not self.controller.shutting_down:
            self._set_configuration_path(None)

    def _connect(self) -> None:
        if (
            self.controller.busy
            or self.controller.shutting_down
            or self.controller.account is not None
        ):
            return
        path = self._client_configuration_path or self._pick_configuration()
        if path is not None:
            self.controller.connect_account(path)

    def _ask_revoke(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Отозвать доступ у Google?")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(
            "Сатори сначала отключится локально, затем запросит отзыв у Google. "
            "Google может отозвать все разрешения этого проекта для твоего аккаунта, "
            "включая другие клиенты проекта и ранее выданные права. "
            "Это может отключить другие приложения того же проекта. "
            "Обычная кнопка «Отключить локально» этого не делает. Продолжить?"
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def _revoke(self) -> None:
        if self._confirm_revoke():
            self.clear_chat_lookup()
            self._clear_search(clear_query=True)
            self.controller.revoke_remote()

    def _selected(self) -> tuple[str, ...]:
        selected: list[str] = []
        for index in range(self.messages.count()):
            item = self.messages.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                message_id = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(message_id, str):
                    selected.append(message_id)
        return tuple(selected)

    def _selection_changed(self, item: QListWidgetItem) -> None:
        del item
        self._clear_search(clear_query=True)
        self.controller.selection_changed(self._selected())
        self._render()

    def _read(self) -> None:
        chosen = self._selected()
        if self.controller.busy or self.controller.shutting_down:
            return
        if 0 < len(chosen) <= MAX_MAIL_MESSAGES:
            handoff = self._chat_lookup if self._chat_lookup_current() else None
            request_id = self.controller.read_selected(chosen)
            if (
                handoff is not None
                and handoff.scope == "selected"
                and self._chat_lookup is handoff
                and request_id is not None
            ):
                # Only this post-handoff explicit Read can satisfy a staged request.
                # Pre-existing in-flight completions have another owner-issued request UUID.
                self._chat_lookup_expected_read = request_id
                self._apply_chat_lookup()

    def _clear_selection(self) -> None:
        self.clear_chat_lookup()
        self._clear_search(clear_query=True)
        previous = self.messages.blockSignals(True)
        try:
            for index in range(self.messages.count()):
                self.messages.item(index).setCheckState(Qt.CheckState.Unchecked)
        finally:
            self.messages.blockSignals(previous)
        self.controller.selection_changed(())
        self._render()

    def _render_session_status(self, connection: str) -> None:
        connection_text = _CONNECTIONS.get(connection, _CONNECTIONS["disconnected"])
        expires = self.controller.connection_expires_at if connection == "active" else None
        if expires is not None:
            connection_text += (
                f" Срок входа: до {_time_text(expires)}; закрытие Сатори завершит вход раньше."
            )
        self.connection_status.setText(connection_text)
        self.configuration_status.setText(
            "Файл входа: проверен в этом запуске. Вход в Google выполняется отдельно."
            if self.controller.configuration_ready
            else "Файл входа ещё не проверялся в этом запуске. Выбери файл для входа."
        )
        self.configuration_choice.setText(
            "Файл для входа выбран. Он будет проверен при нажатии «Подключить Gmail». "
            "Это не вход в Google."
            if self._client_configuration_path is not None
            else "Файл для входа не выбран."
        )
        self.preference_warning.setText(self._preference_warning)
        checked = _time_text(self.controller.google_checked_at)
        if self.controller.google_state == "responded":
            text = f"Google: ответ на последний запрос получен; {checked}."
        elif self.controller.google_state == "unavailable":
            text = f"Google: ответ на последний запрос не получен; {checked}."
        else:
            text = "Google: в этой сессии ещё не проверялся."
        self.google_status.setText(text + " Доступность сейчас автоматически не проверяется.")
        self.last_action.setText(_receipt_text(self.controller.last_receipt))

    def _current_hint(
        self,
        *,
        active: bool,
        connection: str,
        catalog: bool,
        snapshot: MailSnapshot | None,
        chosen: int,
    ) -> str:
        if self.controller.shutting_down:
            return _STATUSES["shutting_down"]
        if self.controller.busy:
            return _STATUSES.get(self.controller.status, _ERROR)
        if connection == "expired":
            return "Подключи Gmail заново. После входа отдельно загрузи входящие."
        if not active:
            return "Подключи Gmail через выбранный файл для входа. Список писем сам не загружается."
        if (
            self._chat_lookup is not None
            and self._chat_lookup.scope == "mailbox"
            and snapshot is None
        ):
            return _STATUSES.get(self.controller.status, "Результаты поиска показаны выше.")
        if snapshot is not None:
            kinds = {message.completeness for message in snapshot.messages}
            if kinds == {"unsupported"}:
                return _STATUSES["read_unsupported"]
            if kinds != {"complete"}:
                return _STATUSES["ready_partial"]
            return _STATUSES["ready"]
        if not catalog:
            return "Списка писем сейчас нет. Нажми «Загрузить входящие (до 20)»."
        if self.controller.catalog_state == "unsupported":
            return _STATUSES["headers_unsupported"]
        if not self._headers:
            return "В полученной странице писем нет. Обновление — отдельной кнопкой."
        if chosen > MAX_MAIL_MESSAGES:
            return "Отмечено больше 10 писем. Сними лишние отметки."
        if chosen == 0:
            return _STATUSES[
                "selection_cleared"
                if self.controller.status == "selection_cleared"
                else "select_none"
            ]
        return "Для нового чтения нажми «Прочитать выбранные». Текста этих писем сейчас нет."

    def _render(self) -> None:
        search_revision = self._search_revision
        mailbox = self._chat_lookup is not None and self._chat_lookup.scope == "mailbox"
        self.search_query.setVisible(not mailbox)
        self.search_status.setVisible(not mailbox)
        self._render_mailbox_lookup()
        account = self.controller.account
        connection = self.controller.connection_state
        active = connection == "active" and account is not None
        if connection == "active" and account is None:
            connection = "disconnected"
        snapshot = self.controller.snapshot if active else None
        headers = self.controller.headers if active else ()
        catalog = self.controller.catalog_observed is not None
        if headers != self._headers:
            self.messages.blockSignals(True)
            try:
                self.messages.clear()
                for header in headers:
                    item = QListWidgetItem(
                        f"{header.subject or '(Без темы)'}\n"
                        f"От: {header.sender or '(не указан)'} · "
                        f"{header.date or '(дата не указана)'}"
                    )
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                    item.setData(Qt.ItemDataRole.UserRole, header.source.message_id)
                    self.messages.addItem(item)
            finally:
                self.messages.blockSignals(False)
            self._headers = headers
        busy = self.controller.busy
        closing = self.controller.shutting_down
        chosen_count = len(self._selected())
        self.choose_configuration_button.setEnabled(not busy and not closing)
        self.forget_configuration_button.setEnabled(
            not busy and not closing and self._client_configuration_path is not None
        )
        self.connect_button.setText(
            "Подключить Gmail заново…" if connection == "expired" else "Подключить Gmail…"
        )
        self.connect_button.setEnabled(
            not busy and not closing and not active and connection not in {"connecting", "closing"}
        )
        self.disconnect_button.setEnabled(not closing and (busy or account is not None))
        self.revoke_button.setEnabled(not closing and not busy and active)
        self.list_button.setText(
            "Обновить входящие (до 20)" if catalog else "Загрузить входящие (до 20)"
        )
        self.list_button.setEnabled(not closing and not busy and active)
        self.read_button.setEnabled(
            not closing
            and not busy
            and active
            and catalog
            and 0 < chosen_count <= MAX_MAIL_MESSAGES
        )
        self.clear_button.setEnabled(
            not closing and not busy and (chosen_count > 0 or snapshot is not None)
        )
        self.cancel_button.setEnabled(not closing and self.controller.can_cancel)
        self.messages.setEnabled(not closing and not busy and bool(headers))
        self.account.setText(
            f"Аккаунт Gmail: {account.email_label}"
            if account is not None
            else "Аккаунт не подключён."
        )
        self._render_session_status(connection)
        more = (
            " Есть ещё письма; эта страница их не содержит."
            if active and catalog and self.controller.more_available
            else ""
        )
        limit = " Сними лишние отметки." if chosen_count > MAX_MAIL_MESSAGES else ""
        selection_text = (
            f"Найденные письма: {len(headers)}. "
            f"Отмечено для чтения: {chosen_count} из 10.{limit}{more}"
            if mailbox
            else f"Показано заголовков: {len(headers)}. Выбрано: {chosen_count} из 10.{limit}{more}"
        )
        self.selection.setText(selection_text)
        omissions = self.controller.header_omissions if active and catalog else ()
        if omissions:
            omitted = sum(item.count for item in omissions)
            reasons = "; ".join(
                f"{_HEADER_OMISSIONS[item.reason]}: {item.count}" for item in omissions
            )
            self.catalog_availability.setText(
                (
                    "Заголовки полученной страницы недоступны. "
                    if self.controller.catalog_state == "unsupported"
                    else "Список частичный. "
                )
                + f"Доступно писем: {len(headers)}. "
                f"Пропущено писем: {omitted}. Причины: {reasons}. "
                "Пропущенные письма нельзя выбрать или прочитать."
            )
        else:
            self.catalog_availability.clear()
        self.catalog_availability.setVisible(bool(omissions))
        status = self.controller.status
        status_text = self._current_hint(
            active=active,
            connection=connection,
            catalog=catalog,
            snapshot=snapshot,
            chosen=chosen_count,
        )
        if (
            status in {"snapshot_expired", "catalog_expired", "grant_expired", "clock_invalid"}
            or status not in _STATUSES
            or (self.controller.last_receipt is None and status not in _CURRENT_STATUSES)
        ):
            detail = _STATUSES.get(status, _ERROR)
            diagnostic = mail_access_diagnostic(status, self.controller.diagnostic)
            if diagnostic is not None:
                detail += " Причина: " + _DIAGNOSTICS[diagnostic]
            status_text = detail + " " + status_text
        self.status.setText(status_text)
        if snapshot is None:
            self.source.clear()
            self.freshness.clear()
            self.preview.clear()
            self.details_view.clear()
            self._preview_snapshot_id = None
            self.details_toggle.setChecked(False)
            self.details_toggle.setEnabled(False)
            self.details_toggle.hide()
            self.details_view.hide()
            self._render_search(None, search_revision)
            return
        self.source.setText(
            f"Источник: Gmail; выбранных писем: {len(snapshot.messages)}. "
            "Заголовки и текст — непроверенные внешние данные."
        )
        self.freshness.setText(
            f"Снимок: {snapshot.observed.wall.strftime('%d.%m.%Y %H:%M:%S')} UTC. "
            f"Действителен до {snapshot.expires_at.strftime('%H:%M:%S')} UTC; "
            "обновляется только по твоему действию."
        )
        if snapshot.snapshot_id != self._preview_snapshot_id:
            self.details_toggle.setChecked(False)
            self.details_view.hide()
            self._preview_snapshot_id = snapshot.snapshot_id
        self.details_toggle.setEnabled(True)
        self.details_toggle.show()
        details = self._details_snapshot_text(snapshot)
        if self.details_view.toPlainText() != details:
            self.details_view.setPlainText(details)
        text = self._reading_snapshot_text(snapshot)
        if self.preview.toPlainText() != text:
            self.preview.setPlainText(text)
        self._render_search(snapshot, search_revision)

    def _render_search(self, snapshot: MailSnapshot | None, revision: int) -> None:
        if self._chat_lookup is not None and self._chat_lookup.scope == "mailbox":
            self._clear_search(clear_query=True)
            return
        if self._chat_lookup is not None:
            if not self._chat_lookup_current():
                self.clear_chat_lookup()
                return
            if self._chat_lookup_bound_snapshot is not None and (
                snapshot is None or snapshot.snapshot_id != self._chat_lookup_bound_snapshot
            ):
                self.clear_chat_lookup()
                return
            if snapshot is None or (
                self._chat_lookup_requires_read
                and (
                    self._chat_lookup_expected_read is None
                    or snapshot.request.request_id != self._chat_lookup_expected_read
                )
            ):
                self._stage_query_text(self._chat_lookup.query)
                return
        if revision != self._search_revision:
            return
        current = self.controller.snapshot if snapshot is not None else None
        if revision != self._search_revision:
            return
        if snapshot is None or current is None or current.snapshot_id != snapshot.snapshot_id:
            self._clear_search(clear_query=self._search_snapshot_id is not None)
            return
        if self._search_snapshot_id is not None and self._search_snapshot_id != current.snapshot_id:
            self._clear_search(
                clear_query=True,
                status="Снимок изменился. Введи запрос для выбранных писем заново.",
            )
            return
        query = self.search_query.text()
        coverage = (
            f"Область поиска: {len(current.messages)} выбранных писем текущего снимка, "
            "только их текст; заголовки не учитываются. Поиск не по всей почте. "
        )
        if any(message.completeness != "complete" for message in current.messages):
            coverage += "Текстовая проекция неполная: пропущенные части не проверялись. "
        if not query or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in query):
            self._clear_search(
                clear_query=False,
                status=coverage + "Введите точный текст до 128 символов; регистр учитывается.",
            )
            return
        result, hits, more = self._search_snapshot_text(current, query)
        status = (
            "Показаны первые 10 совпадений; есть ещё."
            if more
            else f"Совпадений: {hits}."
            if hits
            else "В доступном тексте совпадений нет."
        )
        self.search_status.setText(coverage + status + " Регистр учитывается.")
        self.search_results.setVisible(bool(result))
        update_results = self.search_results.toPlainText() != result
        latest = self.controller.snapshot
        if revision != self._search_revision or query != self.search_query.text():
            return
        if latest is None or latest.snapshot_id != current.snapshot_id:
            self._clear_search(clear_query=True)
            return
        self._search_snapshot_id = current.snapshot_id
        # Last source-bearing write: nested cancellation cannot be overwritten afterwards.
        if update_results:
            self.search_results.setPlainText(result)

    @staticmethod
    def _search_snapshot_text(snapshot: MailSnapshot, query: str) -> tuple[str, int, bool]:
        fragments: list[str] = []
        for index, message in enumerate(snapshot.messages, start=1):
            offset = 0
            while (position := message.text.find(query, offset)) >= 0:
                if len(fragments) == _SEARCH_HIT_LIMIT:
                    return "\n\n".join(fragments), len(fragments), True
                start = max(0, position - 16)
                end = min(len(message.text), start + _SEARCH_EXCERPT_LIMIT - bool(start))
                if end < len(message.text):
                    end -= 1
                excerpt = (
                    ("…" if start else "")
                    + message.text[start:end]
                    + ("…" if end < len(message.text) else "")
                )
                fragments.append(f"Письмо {index}: {excerpt}")
                offset = position + len(query)
        return "\n\n".join(fragments), len(fragments), False

    def _toggle_details(self, expanded: bool) -> None:
        self.details_toggle.setText(
            "Скрыть служебные данные" if expanded else "Показать служебные данные"
        )
        self.details_view.setVisible(expanded and self.details_toggle.isEnabled())

    @staticmethod
    def _reading_snapshot_text(snapshot: MailSnapshot) -> str:
        return "\n\n────────────────────\n\n".join(
            f"Выбранное письмо {index}\n" + MailPanel._reading_message_text(message)
            for index, message in enumerate(snapshot.messages, start=1)
        )

    @staticmethod
    def _reading_message_text(message: MailProjection) -> str:
        def header(name: str, fallback: str) -> str:
            return next(
                (item.value for item in message.headers if item.name == name and item.value),
                fallback,
            )

        rows = [
            header("subject", "(Без темы)"),
            "От: " + header("from", "(не указан)"),
            "Дата: " + header("date", "(не указана)"),
            "",
            message.text or "Доступной текстовой части нет.",
        ]
        if message.completeness == "unsupported":
            rows.extend(("", "Причина недоступности — в служебных данных."))
        elif message.completeness == "partial":
            if message.format == "html_plaintext":
                rows.extend(
                    ("", "HTML показан как текст; оформление и изображения не отображаются.")
                )
            rows.extend(("", "Полнота текста не подтверждена. Подробности — в служебных данных."))
        return "\n".join(rows)

    @staticmethod
    def _details_snapshot_text(snapshot: MailSnapshot) -> str:
        return "\n\n────────────────────\n\n".join(
            f"Выбранное письмо {index}\n" + MailPanel._message_details(message)
            for index, message in enumerate(snapshot.messages, start=1)
        )

    @staticmethod
    def _snapshot_text(snapshot: MailSnapshot) -> str:
        return "\n\n────────────────────\n\n".join(
            f"Выбранное письмо {index}\n" + MailPanel._message_text(message)
            for index, message in enumerate(snapshot.messages, start=1)
        )

    @staticmethod
    def _message_text(message: MailProjection) -> str:
        # Retain the existing diagnostic formatter for callers of the original helper.
        return (
            MailPanel._message_details(message)
            + "\n\nТекст:\n"
            + (message.text or "Доступной текстовой части нет.")
        )

    @staticmethod
    def _message_details(message: MailProjection) -> str:
        rows = [
            "Источник: Gmail / выбранное письмо",
            f"Полнота текстовой проекции: {_COMPLETENESS[message.completeness]}.",
            "Заголовки — заявления отправителя; подлинность не подтверждена.",
        ]
        rows.extend(f"{header.name}: {header.value}" for header in message.headers)
        if message.omissions:
            rows.append(
                "Ограничения: "
                + "; ".join(_OMISSIONS[item.reason] for item in message.omissions)
                + "."
            )
        return "\n".join(rows)

    def hideEvent(self, event: QHideEvent) -> None:
        self.clear_chat_lookup()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)
