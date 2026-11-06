"""Correlation context for runs (propagated via contextvars + W3C headers)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_run_id: ContextVar[str | None] = ContextVar("af_run_id", default=None)
_task_id: ContextVar[str | None] = ContextVar("af_task_id", default=None)
_attempt: ContextVar[int | None] = ContextVar("af_attempt", default=None)
_approval_waiting: ContextVar[bool] = ContextVar("af_approval_waiting", default=False)


def get_run_id() -> str | None:
    return _run_id.get()


def get_task_id() -> str | None:
    return _task_id.get()


def get_attempt() -> int | None:
    return _attempt.get()


def is_approval_waiting() -> bool:
    return _approval_waiting.get()


@contextmanager
def bind_run(run_id: str) -> Iterator[None]:
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


@contextmanager
def bind_task(task_id: str, *, attempt: int | None = None) -> Iterator[None]:
    t1 = _task_id.set(task_id)
    t2 = _attempt.set(attempt)
    try:
        yield
    finally:
        _task_id.reset(t1)
        _attempt.reset(t2)


@contextmanager
def bind_approval_waiting(waiting: bool = True) -> Iterator[None]:
    token = _approval_waiting.set(waiting)
    try:
        yield
    finally:
        _approval_waiting.reset(token)


def correlation_fields() -> dict[str, object]:
    fields: dict[str, object] = {}
    if (rid := get_run_id()) is not None:
        fields["run_id"] = rid
    if (tid := get_task_id()) is not None:
        fields["task_id"] = tid
    if (att := get_attempt()) is not None:
        fields["attempt"] = att
    if is_approval_waiting():
        fields["approval_waiting"] = True
    return fields
