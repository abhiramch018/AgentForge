"""Errors raised while creating or validating an execution plan."""

from pydantic import ValidationError


class PlanningError(Exception):
    """Base class for planning failures."""


class PlanRequestError(PlanningError):
    """The user goal is missing or blank."""


class PlanConfigurationError(PlanningError):
    """Supervisor settings are unusable."""


class PlanValidationError(PlanningError):
    """A plan failed structural validation."""


class PlanGenerationError(PlanningError):
    """The model never produced a valid plan within the repair budget."""


class ExecutionError(Exception):
    """Base class for plan-execution failures."""


class ExecutionConfigurationError(ExecutionError):
    """The executor or agent registry is misconfigured."""


class ReviewError(ExecutionError):
    """Base class for review failures."""


class ReviewConfigurationError(ReviewError):
    """Reviewer settings are unusable."""


class ReviewGenerationError(ReviewError):
    """The reviewer never produced a valid structured decision."""


def format_validation_error(exc: ValidationError) -> str:
    """Turn a Pydantic error into a short message without echoing raw input."""

    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)
