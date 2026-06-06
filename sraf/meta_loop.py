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
        try:
            original_instructions = self.extractor.extract(user_query)
        except (ValueError, RuntimeError) as exc:
            return MetaLoopResult(
                output=None,
                final_prompt=base_prompt,
                attempts=0,
                original_instructions=[],
                status="failure",
                feedback=f"Instruction extraction failed: {exc}",
            )
        prompt = base_prompt
        feedback = ""

        for attempt in range(1, self.max_attempts + 1):
            try:
                validation = self.validator.validate(original_instructions, prompt, user_query)
            except (ValueError, RuntimeError) as exc:
                feedback = f"Validation failed (attempt {attempt}): {exc}"
                if attempt == self.max_attempts:
                    break
                continue

            if validation.missing_instructions:
                prompt = restore_missing_instructions(prompt, validation.missing_instructions)

            if validation.blocking_instructions:
                try:
                    resolution = self.escalation.resolve_blocking_instructions(
                        validation,
                        user_query,
                        original_instructions,
                    )
                except (ValueError, RuntimeError) as exc:
                    return MetaLoopResult(
                        output=None,
                        final_prompt=prompt,
                        attempts=attempt - 1,
                        original_instructions=original_instructions,
                        status="failure",
                        feedback=f"Escalation failed: {exc}",
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
                    try:
                        original_instructions = self.extractor.extract(user_query)
                    except (ValueError, RuntimeError) as exc:
                        return MetaLoopResult(
                            output=None,
                            final_prompt=prompt,
                            attempts=attempt - 1,
                            original_instructions=[],
                            status="failure",
                            feedback=f"Instruction extraction failed for new task: {exc}",
                        )
                    continue
                if resolution.type == "modified_instr":
                    original_instructions = [str(item) for item in resolution.value]
                    prompt = restore_missing_instructions(prompt, original_instructions)

            try:
                attempt_result = self.runner.run(prompt, user_query)
            except Exception as exc:
                feedback = f"Runner failed (attempt {attempt}): {exc}"
                if attempt == self.max_attempts:
                    break
                continue

            try:
                verdict = self.evaluator.evaluate(user_query, attempt_result.final_answer)
            except (ValueError, RuntimeError) as exc:
                verdict = None
                feedback = f"Evaluation failed (attempt {attempt}): {exc}"

            if verdict is not None:
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

            # Refine prompt for next attempt
            try:
                raw_prompt = self.refiner.refine(
                    prompt,
                    user_query,
                    attempt_result.execution_log,
                    feedback,
                )
            except (ValueError, RuntimeError) as exc:
                feedback = f"Refinement failed (attempt {attempt}): {exc}"
                if attempt == self.max_attempts:
                    break
                continue

            try:
                sanitized = self.sanitizer.sanitize(raw_prompt)
            except (ValueError, RuntimeError) as exc:
                feedback = f"Sanitization failed (attempt {attempt}): {exc}"
                if attempt == self.max_attempts:
                    break
                prompt = raw_prompt
                continue

            if sanitized.has_conflicts:
                try:
                    resolution = self.escalation.resolve_prompt_conflicts(sanitized.conflicts, raw_prompt)
                except (ValueError, RuntimeError) as exc:
                    return MetaLoopResult(
                        output=None,
                        final_prompt=prompt,
                        attempts=attempt,
                        original_instructions=original_instructions,
                        status="failure",
                        feedback=f"Escalation failed: {exc}",
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
                prompt = str(resolution.value)
            else:
                prompt = sanitized.clean_prompt or raw_prompt

            # Re-validate after refinement
            try:
                validation = self.validator.validate(original_instructions, prompt, user_query)
            except (ValueError, RuntimeError) as exc:
                feedback = f"Re-validation failed (attempt {attempt}): {exc}"
                continue

            if validation.missing_instructions:
                prompt = restore_missing_instructions(prompt, validation.missing_instructions)
            if validation.blocking_instructions:
                try:
                    resolution = self.escalation.resolve_blocking_instructions(
                        validation,
                        user_query,
                        original_instructions,
                    )
                except (ValueError, RuntimeError) as exc:
                    return MetaLoopResult(
                        output=None,
                        final_prompt=prompt,
                        attempts=attempt,
                        original_instructions=original_instructions,
                        status="failure",
                        feedback=f"Escalation failed: {exc}",
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

        # Build a user-friendly explanation of why the task was not completed
        reason = (
            f"Задача не решена после {self.max_attempts} попыток.\n\n"
            f"Последняя ошибка: {feedback or 'Неизвестная ошибка'}\n\n"
            f"Агент исчерпал лимит попыток ({self.max_attempts}), "
            f"но так и не смог выполнить задачу. "
            f"Возможные причины:\n"
            f"— Агент генерировал некорректный код или неправильные шаги\n"
            f"— Проверка (evaluator) каждый раз возвращала ошибку\n"
            f"— Не хватает контекста или инструкций для выполнения задачи\n"
            f"Попробуй уточнить задачу или разбить её на более мелкие шаги."
        )
        return MetaLoopResult(
            output=reason,
            final_prompt=prompt,
            attempts=self.max_attempts,
            original_instructions=original_instructions,
            status="failure",
            feedback=feedback,
        )
