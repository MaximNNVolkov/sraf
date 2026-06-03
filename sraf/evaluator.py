from __future__ import annotations

from sraf.json_utils import loads_jsonish
from sraf.llm import LLMClient
from sraf.models import ChatMessage, EvaluationResult
from sraf.prompts import EVALUATOR_PROMPT


class Evaluator:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def evaluate(self, original_task: str, agent_output: str) -> EvaluationResult:
        response = self.llm.complete(
            [
                ChatMessage(
                    role="user",
                    content=EVALUATOR_PROMPT.format(
                        original_task=original_task,
                        agent_output=agent_output,
                    ),
                )
            ]
        )
        if not response.content:
            return EvaluationResult(status="failure", feedback="Evaluator returned an empty response.")

        data = loads_jsonish(response.content)
        status = str(data.get("status", "failure")).lower()
        return EvaluationResult(
            status="success" if status == "success" else "failure",
            feedback=str(data.get("feedback", "")),
        )
