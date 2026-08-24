from dataclasses import dataclass
from typing import Callable

from agentic_rag.contracts import AgenticState


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    handler: Callable[[AgenticState], dict]
    allowed_targets: tuple[str, ...] = ()


class AgentRegistry:
    def __init__(self, specs: list[AgentSpec]) -> None:
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("Agent names must be unique")
        known = set(names)
        for spec in specs:
            unknown = set(spec.allowed_targets) - known
            if unknown:
                raise ValueError(f"Unknown handoff target(s) for {spec.name}: {sorted(unknown)}")
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> AgentSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ValueError(f"Unknown agent: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def validate_target(self, source: str, target: str) -> None:
        spec = self.get(source)
        if target not in spec.allowed_targets:
            raise ValueError(f"Agent {source!r} cannot hand off to {target!r}")

