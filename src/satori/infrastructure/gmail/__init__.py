"""User-driven read-only Gmail adapter; no automatic connection or source admission."""

from satori.infrastructure.gmail.connector import GmailConnector as GmailConnector
from satori.infrastructure.gmail.contracts import (
    GmailAccount as GmailAccount,
)
from satori.infrastructure.gmail.contracts import (
    GmailClientConfiguration as GmailClientConfiguration,
)
from satori.infrastructure.gmail.contracts import (
    GmailDisconnectResult as GmailDisconnectResult,
)
from satori.infrastructure.gmail.contracts import (
    GmailError as GmailError,
)
from satori.infrastructure.gmail.contracts import (
    GmailInboxPage as GmailInboxPage,
)
from satori.infrastructure.gmail.contracts import (
    GmailLoginStart as GmailLoginStart,
)
from satori.infrastructure.gmail.contracts import (
    GmailMessageHeader as GmailMessageHeader,
)
from satori.infrastructure.gmail.contracts import (
    GmailSearchPage as GmailSearchPage,
)
from satori.infrastructure.gmail.contracts import (
    load_client_configuration as load_client_configuration,
)
