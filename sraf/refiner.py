from __future__ import annotations

from sraf.llm import LLMClient
from sraf.models import ChatMessage
from sraf.prompts import PROMPT_REFINER_PROMPT


class PromptRefiner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def refine(
        self,
        current_prompt: str,
        task: str,
        execution_log: list[dict],
        evaluation_feedback: str,
    ) -> str:
        response = self.llm.complete(
            [
                ChatMessage(
                    role="user",
                    content=PROMPT_REFINER_PROMPT.format(
                        current_prompt=current_prompt,
                        task=task,
                        execution_log=execution_log,
                        evaluation_feedback=evaluation_feedback,
                    ),
                )
            ]
        )
        if not response.content:
            raise ValueError("Prompt refiner returned an empty response.")
        return response.content.strip()
