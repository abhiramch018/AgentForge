"""Process-wide admission control for orchestration runs.

Atomic admission reserves each run's maximum worker count up front.
Capacity stays held until the entire synchronous run returns (success or
failure). These limits cover one application instance in one OS process,
not multiple replicas.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator


class CapacityExceeded(Exception):
    """No capacity remains for another run in this process."""


class CapacityGate:
    """Reserve run and task slots for the lifetime of a synchronous execution."""

    def __init__(self, *, max_runs: int, max_tasks: int) -> None:
        if max_runs < 1 or max_tasks < 1:
            raise ValueError("Capacity limits must be positive.")
        self.max_runs = max_runs
        self.max_tasks = max_tasks
        self._runs = 0
        self._tasks = 0
        self._lock = Lock()

    @contextmanager
    def reserve(self, slots: int) -> Iterator[None]:
        if not isinstance(slots, int) or isinstance(slots, bool) or slots < 1:
            raise ValueError("Invalid capacity reservation.")
        if slots > self.max_tasks:
            raise ValueError("Reservation exceeds process task capacity.")
        with self._lock:
            if self._runs >= self.max_runs or self._tasks + slots > self.max_tasks:
                raise CapacityExceeded()
            self._runs += 1
            self._tasks += slots
        try:
            yield
        finally:
            with self._lock:
                self._runs -= 1
                self._tasks -= slots

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_runs": self._runs,
                "reserved_task_slots": self._tasks,
                "max_inflight_runs": self.max_runs,
                "max_inflight_tasks": self.max_tasks,
            }
