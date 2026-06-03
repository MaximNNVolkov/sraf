from __future__ import annotations

from sraf.json_utils import loads_jsonish
from sraf.llm import LLMClient
from sraf.models import ChatMessage, ValidationResult
from sraf.prompts import INSTRUCTION_EXTRACTOR_PROMPT, INSTRUCTION_VALIDATOR_PROMPT


class InstructionExtractor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def extract(self, user_query: str) -> list[str]:
        response = self.llm.complete(
            [ChatMessage(role="user", content=INSTRUCTION_EXTRACTOR_PROMPT.format(user_query=user_query))]
        )
        if not response.content:
            return []
        parsed = loads_jsonish(response.content)
        if not isinstance(parsed, list):
            raise ValueError("instruction extractor must return a JSON array")
        return [str(item).strip() for item in parsed if str(item).strip()]


class InstructionValidator:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def validate(
        self,
        original_instructions: list[str],
        current_prompt: str,
        user_task: str,
    ) -> ValidationResult:
        if not original_instructions:
            return ValidationResult(compliant=True, explanation="No primary instructions.")

        if self.llm is None:
            return self._local_validate(original_instructions, current_prompt)

        response = self.llm.complete(
            [
                ChatMessage(
                    role="user",
                    content=INSTRUCTION_VALIDATOR_PROMPT.format(
                        original_instructions=original_instructions,
                        current_prompt=current_prompt,
                        user_task=user_task,
                    ),
                )
            ]
        )
        if not response.content:
            return self._local_validate(original_instructions, current_prompt)

        data = loads_jsonish(response.content)
        return ValidationResult(
            compliant=bool(data.get("compliant", False)),
            missing_instructions=[str(item) for item in data.get("missing_instructions", [])],
            blocking_instructions=[str(item) for item in data.get("blocking_instructions", [])],
            explanation=str(data.get("explanation", "")),
        )

    @staticmethod
    def _local_validate(original_instructions: list[str], current_prompt: str) -> ValidationResult:
        prompt_lower = current_prompt.lower()
        missing = [instruction for instruction in original_instructions if instruction.lower() not in prompt_lower]
        return ValidationResult(
            compliant=not missing,
            missing_instructions=missing,
            explanation="Local validation checks explicit instruction presence only.",
        )


def restore_missing_instructions(prompt: str, missing_instructions: list[str]) -> str:
    if not missing_instructions:
        return prompt
    suffix = "\n".join(
        f"Важно: соблюдай исходное требование — {instruction}" for instruction in missing_instructions
    )
    return f"{prompt.rstrip()}\n{suffix}"
