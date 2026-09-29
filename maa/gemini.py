"""Gemini adapter for maa.agent.Model (google-genai, manual function calling).

We keep the model's own Content objects in the history so Gemini's thought
signatures survive between turns.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from maa.agent import ModelTurn, ToolCall


class GeminiModel:
    def __init__(self, api_key: str, model: str, temperature: float = 0.2):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.history: list[types.Content] = []
        self.config: types.GenerateContentConfig | None = None

    def start(self, system: str, user_text: str, tools: list[dict[str, Any]]) -> ModelTurn:
        decls = [
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["parameters"])
            for t in tools
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=self.temperature,
        )
        self.history = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
        return self._generate()

    def send_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> ModelTurn:
        parts = [types.Part.from_function_response(name=name, response=resp) for name, resp in results]
        self.history.append(types.Content(role="user", parts=parts))
        return self._generate()

    def _generate(self) -> ModelTurn:
        resp = self.client.models.generate_content(model=self.model, contents=self.history, config=self.config)
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate is None or candidate.content is None:
            return ModelTurn(calls=[], text=f"no candidate (finish={getattr(candidate, 'finish_reason', None)})")
        self.history.append(candidate.content)
        calls = [ToolCall(name=fc.name, args=dict(fc.args or {})) for fc in (resp.function_calls or [])]
        text = "".join(p.text for p in candidate.content.parts or [] if getattr(p, "text", None))
        return ModelTurn(calls=calls, text=text)
