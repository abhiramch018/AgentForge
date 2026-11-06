"""Telemetry redaction — keep secrets and content out of default logs/spans."""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "api-key",
        "apikey",
        "token",
        "password",
        "secret",
        "credential",
        "llm_api_key",
        "bearer",
    }
)

_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "prompts",
        "messages",
        "content",
        "output",
        "final_answer",
        "system_instruction",
        "raw",
        "body",
    }
)


def redact_mapping(
    data: dict[str, Any],
    *,
    capture_content: bool = False,
) -> dict[str, Any]:
    """Return a shallow-redacted copy safe for default telemetry."""

    out: dict[str, Any] = {}
    for key, value in data.items():
        lower = key.lower()
        if lower in _SENSITIVE_KEYS or any(s in lower for s in _SENSITIVE_KEYS):
            out[key] = "[redacted]"
            continue
        if not capture_content and lower in _CONTENT_KEYS:
            out[key] = "[content_omitted]"
            continue
        if isinstance(value, dict):
            out[key] = redact_mapping(value, capture_content=capture_content)
        elif isinstance(value, list):
            out[key] = [
                redact_mapping(v, capture_content=capture_content)
                if isinstance(v, dict)
                else ("[content_omitted]" if not capture_content and isinstance(v, str) and len(v) > 80 else v)
                for v in value
            ]
        else:
            out[key] = value
    return out
