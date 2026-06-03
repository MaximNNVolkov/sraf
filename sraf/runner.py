from __future__ import annotations

import json
from typing import Any

from sraf.llm import LLMClient
from sraf.models import AttemptResult, ChatMessage
from sraf.tools import ToolRegistry


class AgentRunner:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, *, max_steps: int = 5) -> None:
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps

    def run(self, system_prompt: str, user_query: str) -> AttemptResult:
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_query),
        ]
        execution_log: list[dict[str, Any]] = []

        for step in range(1, self.max_steps + 1):
            assistant_message = self.llm.complete(messages, functions=self.tools.specs())
            messages.append(assistant_message)

            if assistant_message.function_call:
                function_name, arguments = parse_function_call(assistant_message.function_call)
                try:
                    result = self.tools.call(function_name, arguments)
                except Exception as exc:
                    result = {"error": str(exc)}
                execution_log.append(
                    {
                        "step": step,
                        "tool": function_name,
                        "arguments": arguments,
                        "result": result,
                    }
                )
                messages.append(
                    ChatMessage(
                        role="function",
                        name=function_name,
                        content=json.dumps(result, ensure_ascii=False),
                    )
                )
                continue

            if assistant_message.content:
                return AttemptResult(final_answer=assistant_message.content, execution_log=execution_log)

        return AttemptResult(
            final_answer="Max steps reached before the agent produced a final answer.",
            execution_log=execution_log,
        )


def parse_function_call(function_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(function_call.get("name") or function_call.get("function_name") or "")
    if not name:
        raise ValueError("Function call has no name.")
    raw_arguments = function_call.get("arguments") or function_call.get("args") or {}
    if isinstance(raw_arguments, str):
        arguments = json.loads(raw_arguments or "{}")
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        raise ValueError("Function call arguments must be a JSON object or string.")
    return name, arguments
