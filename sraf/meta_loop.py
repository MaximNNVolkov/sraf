from __future__ import annotations

from sraf.escalation import HumanEscalationInterface
from sraf.evaluator import Evaluator
from sraf.instructions import InstructionExtractor, InstructionValidator, restore_missing_instructions
from sraf.models import MetaLoopResult
from sraf.prompts import BASE_SYSTEM_PROMPT
from sraf.refiner import PromptRefiner
from sraf.runner import AgentRunner
from sraf.sanitizer import PromptSanitizer


class MetaLoop:
    def __init__(
        self,
        *,
        extractor: InstructionExtractor,
        runner: AgentRunner,
        evaluator: Evaluator,
        refiner: PromptRefiner,
        sanitizer: PromptSanitizer,
        validator: InstructionValidator,
        escalation: HumanEscalationInterface,
        max_attempts: int = 3,
    ) -> None:
        self.extractor = extractor
        self.runner = runner
        self.evaluator = evaluator
        self.refiner = refiner
        self.sanitizer = sanitizer
        self.validator = validator
        self.escalation = escalation
        self.max_attempts = max_attempts

    def run(self, user_query: str, base_prompt: str = BASE_SYSTEM_PROMPT) -> MetaLoopResult:
        original_instructions = self.extractor.extract(user_query)
        prompt = base_prompt
        feedback = ""

        for attempt in range(1, self.max_attempts + 1):
            validation = self.validator.validate(original_instructions, prompt, user_query)
            if validation.missing_instructions:
                prompt = restore_missing_instructions(prompt, validation.missing_instructions)

            if validation.blocking_instructions:
                resolution = self.escalation.resolve_blocking_instructions(
                    validation,
                    user_query,
                    original_instructions,
                )
                if resolution.type == "abort":
                    return MetaLoopResult(
                        output=None,
                        final_prompt=prompt,
                        attempts=attempt - 1,
                        original_instructions=original_instructions,
                        status="failure",
                        feedback=str(resolution.value),
                    )
                if resolution.type == "new_task":
                    user_query = str(resolution.value)
                    original_instructions = self.extractor.extract(user_query)
                    continue
                if resolution.type == "modified_instr":
                    original_instructions = [str(item) for item in resolution.value]
                    prompt = restore_missing_instructions(prompt, original_instructions)

            attempt_result = self.runner.run(prompt, user_query)
            verdict = self.evaluator.evaluate(user_query, attempt_result.final_answer)
            feedback = verdict.feedback
            if verdict.status == "success":
                return MetaLoopResult(
                    output=attempt_result.final_answer,
                    final_prompt=prompt,
                    attempts=attempt,
                    original_instructions=original_instructions,
                    status="success",
                    feedback=feedback,
                )

            raw_prompt = self.refiner.refine(
                prompt,
                user_query,
                attempt_result.execution_log,
                verdict.feedback,
            )
            sanitized = self.sanitizer.sanitize(raw_prompt)
            if sanitized.has_conflicts:
                resolution = self.escalation.resolve_prompt_conflicts(sanitized.conflicts, raw_prompt)
                if resolution.type == "abort":
                    return MetaLoopResult(
                        output=None,
                        final_prompt=prompt,
                        attempts=attempt,
                        original_instructions=original_instructions,
                        status="failure",
                        feedback=str(resolution.value),
                    )
                prompt = str(resolution.value)
            else:
                prompt = sanitized.clean_prompt or raw_prompt

            validation = self.validator.validate(original_instructions, prompt, user_query)
            if validation.missing_instructions:
                prompt = restore_missing_instructions(prompt, validation.missing_instructions)
            if validation.blocking_instructions:
                resolution = self.escalation.resolve_blocking_instructions(
                    validation,
                    user_query,
                    original_instructions,
                )
                if resolution.type == "abort":
                    return MetaLoopResult(
                        output=None,
                        final_prompt=prompt,
                        attempts=attempt,
                        original_instructions=original_instructions,
                        status="failure",
                        feedback=str(resolution.value),
                    )

        return MetaLoopResult(
            output="Задача не решена",
            final_prompt=prompt,
            attempts=self.max_attempts,
            original_instructions=original_instructions,
            status="failure",
            feedback=feedback,
        )
