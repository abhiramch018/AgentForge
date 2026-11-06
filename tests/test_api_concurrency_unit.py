"""Unit tests for process-wide capacity admission."""

import pytest

from app.api.concurrency import CapacityExceeded, CapacityGate


def test_reserve_holds_and_releases_slots() -> None:
    gate = CapacityGate(max_runs=2, max_tasks=4)
    with gate.reserve(2):
        assert gate.snapshot()["active_runs"] == 1
        assert gate.snapshot()["reserved_task_slots"] == 2
        with gate.reserve(2):
            assert gate.snapshot()["active_runs"] == 2
            with pytest.raises(CapacityExceeded):
                with gate.reserve(1):
                    pass
    assert gate.snapshot()["active_runs"] == 0
    assert gate.snapshot()["reserved_task_slots"] == 0


def test_reserve_releases_after_exception() -> None:
    gate = CapacityGate(max_runs=1, max_tasks=2)
    with pytest.raises(RuntimeError):
        with gate.reserve(2):
            raise RuntimeError("boom")
    assert gate.snapshot()["active_runs"] == 0
    with gate.reserve(2):
        assert gate.snapshot()["reserved_task_slots"] == 2
