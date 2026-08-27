"""Structured logging and trace-correlation tests."""

import json
import logging
from io import StringIO

from satori.observability.logging import bind_trace_id, configure_logging
from satori.observability.trace import current_trace_id


def test_structured_logging_carries_and_resets_trace_id() -> None:
    """All events in one trace context can be correlated without HTTP."""

    stream = StringIO()
    configure_logging("INFO", stream=stream)
    logger = logging.getLogger("satori.test")

    with bind_trace_id("trace-123"):
        assert current_trace_id() == "trace-123"
        logger.info(
            "foundation_event",
            extra={"satori_fields": {"identity_id": "identity-1", "seed_version": 1}},
        )

    assert current_trace_id() is None
    payload = json.loads(stream.getvalue())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "satori.test"
    assert payload["message"] == "foundation_event"
    assert payload["trace_id"] == "trace-123"
    assert payload["fields"] == {"identity_id": "identity-1", "seed_version": 1}
