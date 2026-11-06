"""Structured operational logging with correlation and redaction."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.observability.context import correlation_fields
from app.observability.redaction import redact_mapping

_log = logging.getLogger("agentforge.ops")


def ops_log(
    event: str,
    *,
    level: int = logging.INFO,
    capture_content: bool = False,
    **fields: Any,
) -> None:
    """Emit one structured ops line. Never raises into the caller."""

    try:
        payload = redact_mapping(
            {"event": event, **correlation_fields(), **fields},
            capture_content=capture_content,
        )
        _log.log(level, json.dumps(payload, default=str, sort_keys=True))
    except Exception:
        _log.exception("ops_log_failed event=%s", event)
