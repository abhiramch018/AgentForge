"""M16 portfolio DemoLLM + smoke demos."""

from __future__ import annotations

from app.core.messages import ChatMessage, MessageRole
from app.llm.demo import PORTFOLIO_DEMO_MARKER, DemoLLM
from app.orchestration.models import ExecutionPlan


def test_demo_llm_portfolio_plan_is_multi_task() -> None:
    llm = DemoLLM()
    plan_raw = llm.generate(
        [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are the planning supervisor. Create a lean execution plan.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content=f"{PORTFOLIO_DEMO_MARKER}\nCompare NordicCloud and HarborStack.",
            ),
        ]
    ).content
    plan = ExecutionPlan.model_validate_json(plan_raw)
    assert len(plan.tasks) == 3
    ids = {t.id for t in plan.tasks}
    assert ids == {"analyze_nordic", "analyze_harbor", "recommend"}
    ready = [t for t in plan.tasks if not t.depends_on]
    assert len(ready) == 2


def test_demo_llm_forces_one_revision_then_accept() -> None:
    llm = DemoLLM()
    reject = llm.generate(
        [
            ChatMessage(role=MessageRole.SYSTEM, content="You are AgentForge's output reviewer."),
            ChatMessage(
                role=MessageRole.USER,
                content=f"{PORTFOLIO_DEMO_MARKER} task id=recommend draft",
            ),
        ]
    ).content
    assert "needs_revision" in reject
    accept = llm.generate(
        [
            ChatMessage(role=MessageRole.SYSTEM, content="You are AgentForge's output reviewer."),
            ChatMessage(
                role=MessageRole.USER,
                content=f"{PORTFOLIO_DEMO_MARKER} task id=recommend revised",
            ),
        ]
    ).content
    assert "accepted" in accept
