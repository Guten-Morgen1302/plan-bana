"""Provider-neutral model types shared by the Gemini adapter and the planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class AgentOutputError(Exception):
    """The model refused, stopped without finishing, or produced invalid output."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    calls: list[ToolCall]
    text: str = ""


class Model(Protocol):
    def start(self, system: str, user_text: str, tools: list[dict[str, Any]]) -> ModelTurn: ...
    def send_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> ModelTurn: ...
