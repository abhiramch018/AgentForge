"""Tests for role-to-agent registration."""

from collections.abc import Sequence

import pytest

from app.agent import Agent
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.orchestration import (
    AgentRegistry,
    ExecutionConfigurationError,
    SpecialistRole,
)


class FakeLLMClient(LLMClient):
    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="ok", model="fake-model")


def _agent(name: str) -> Agent:
    return Agent(
        name=name,
        system_instruction=f"You are {name}.",
        llm_client=FakeLLMClient(),
        allowed_tools=(),
    )


def test_registry_registers_and_requires_roles() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.RESEARCHER, _agent("researcher"))
    registry.register(SpecialistRole.WRITER, _agent("writer"))

    assert registry.get(SpecialistRole.RESEARCHER).name == "researcher"
    registry.require_roles({SpecialistRole.RESEARCHER, SpecialistRole.WRITER})


def test_registry_rejects_duplicate_and_missing_roles() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.GENERAL, _agent("general"))

    with pytest.raises(ExecutionConfigurationError, match="already registered"):
        registry.register(SpecialistRole.GENERAL, _agent("other"))

    with pytest.raises(ExecutionConfigurationError, match="Missing agents"):
        registry.require_roles({SpecialistRole.GENERAL, SpecialistRole.ANALYST})
