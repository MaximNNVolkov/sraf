from __future__ import annotations

import sys
from abc import ABC, abstractmethod

from sraf.models import EscalationResolution, PromptConflict, ValidationResult


class HumanEscalationInterface(ABC):
    @abstractmethod
    def resolve_prompt_conflicts(
        self,
        conflicts: list[PromptConflict],
        raw_prompt: str,
    ) -> EscalationResolution:
        """Resolve sanitizer conflicts."""

    @abstractmethod
    def resolve_blocking_instructions(
        self,
        validation: ValidationResult,
        user_task: str,
        original_instructions: list[str],
    ) -> EscalationResolution:
        """Resolve task-blocking primary instructions."""


class CliEscalation(HumanEscalationInterface):
    def resolve_prompt_conflicts(
        self,
        conflicts: list[PromptConflict],
        raw_prompt: str,
    ) -> EscalationResolution:
        if not sys.stdin.isatty():
            return EscalationResolution(type="abort", value="Prompt conflict requires interactive resolution.")

        print("Обнаружены противоречия в промпте:")
        for index, conflict in enumerate(conflicts, start=1):
            print(f"{index}. A: {conflict.instruction_a}")
            print(f"   B: {conflict.instruction_b}")
            print(f"   Причина: {conflict.explanation}")

        print("Выберите действие: 1 - оставить A, 2 - оставить B, 3 - ввести новый промпт, 4 - отмена")
        choice = input("> ").strip()
        if choice == "1":
            prompt = _remove_lines(raw_prompt, [conflict.instruction_b for conflict in conflicts])
            return EscalationResolution(type="resolved_prompt", value=prompt)
        if choice == "2":
            prompt = _remove_lines(raw_prompt, [conflict.instruction_a for conflict in conflicts])
            return EscalationResolution(type="resolved_prompt", value=prompt)
        if choice == "3":
            print("Введите разрешенный системный промпт. Завершите пустой строкой.")
            lines: list[str] = []
            while True:
                line = input()
                if not line:
                    break
                lines.append(line)
            return EscalationResolution(type="resolved_prompt", value="\n".join(lines).strip())
        return EscalationResolution(type="abort", value="User aborted prompt conflict resolution.")

    def resolve_blocking_instructions(
        self,
        validation: ValidationResult,
        user_task: str,
        original_instructions: list[str],
    ) -> EscalationResolution:
        if not sys.stdin.isatty():
            return EscalationResolution(type="abort", value="Blocking instructions require interactive resolution.")

        print("Невозможно выполнить задачу с текущими требованиями.")
        print(f"Задача: {user_task}")
        print("Проблемные инструкции:")
        for instruction in validation.blocking_instructions:
            print(f"- {instruction}")
        print(f"Причина: {validation.explanation}")
        print("Выберите действие: 1 - изменить инструкции, 2 - изменить задачу, 3 - отмена")
        choice = input("> ").strip()
        if choice == "1":
            print("Введите новый JSON-массив инструкций или пустую строку для удаления блокирующих.")
            text = input("> ").strip()
            if not text:
                updated = [
                    instruction
                    for instruction in original_instructions
                    if instruction not in validation.blocking_instructions
                ]
                return EscalationResolution(type="modified_instr", value=updated)
            try:
                import json

                parsed = json.loads(text)
            except ValueError as exc:
                return EscalationResolution(type="abort", value=f"Invalid JSON: {exc}")
            return EscalationResolution(type="modified_instr", value=[str(item) for item in parsed])
        if choice == "2":
            print("Введите измененную задачу:")
            return EscalationResolution(type="new_task", value=input("> ").strip())
        return EscalationResolution(type="abort", value="User aborted blocking instruction resolution.")


def _remove_lines(prompt: str, lines_to_remove: list[str]) -> str:
    remove = {line.strip() for line in lines_to_remove}
    return "\n".join(line for line in prompt.splitlines() if line.strip() not in remove).strip()
