"""Map specialist roles to concrete ``Agent`` instances.

Register agents before ``PlanExecutor.execute``. Do not mutate the registry
while a plan is running.
"""

from app.agent.agent import Agent
from app.orchestration.exceptions import ExecutionConfigurationError
from app.orchestration.models import SpecialistRole


class AgentRegistry:
    """Role-to-agent lookup used by the plan executor."""

    def __init__(self) -> None:
        self._agents: dict[SpecialistRole, Agent] = {}

    def register(self, role: SpecialistRole, agent: Agent) -> None:
        if not isinstance(role, SpecialistRole):
            raise ExecutionConfigurationError("role must be a SpecialistRole.")
        if not isinstance(agent, Agent):
            raise ExecutionConfigurationError("agent must be an Agent.")
        if role in self._agents:
            raise ExecutionConfigurationError(
                f"Role '{role.value}' is already registered."
            )
        self._agents[role] = agent

    def get(self, role: SpecialistRole) -> Agent:
        try:
            return self._agents[role]
        except KeyError:
            raise ExecutionConfigurationError(
                f"No agent is registered for role '{role.value}'."
            ) from None

    def require_roles(self, roles: set[SpecialistRole]) -> None:
        missing = sorted(
            (role.value for role in roles if role not in self._agents),
        )
        if missing:
            joined = ", ".join(missing)
            raise ExecutionConfigurationError(
                f"Missing agents for role(s): {joined}."
            )

    def list_roles(self) -> tuple[SpecialistRole, ...]:
        return tuple(self._agents.keys())
