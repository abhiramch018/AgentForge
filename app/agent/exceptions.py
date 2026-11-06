"""Errors raised by the agent layer before a model call.

Provider failures stay as the LLM exceptions from ``app.core.exceptions``.
This module does not wrap those errors.
"""

from pydantic import ValidationError


class AgentError(Exception):
    """Base class for errors raised while preparing an agent request."""


class AgentConfigurationError(AgentError):
    """The agent name, system instruction, or client is unusable."""


class AgentRequestError(AgentError):
    """The task sent to an agent is missing or blank."""


class AgentMaxIterationsError(AgentError):
    """The run used every model call without producing a final answer."""


def format_validation_error(exc: ValidationError) -> str:
    """Turn a Pydantic error into a short message without echoing raw input."""

    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)
