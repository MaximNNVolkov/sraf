from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sraf.conversation import ConversationSession, ConversationTurn, build_prompt_with_context
from sraf.escalation import HumanEscalationInterface
from sraf.instructions import InstructionValidator, restore_missing_instructions
from sraf.meta_loop import MetaLoop
from sraf.models import (
    AttemptResult,
    EscalationResolution,
    EvaluationResult,
    MetaLoopResult,
    PromptConflict,
    SanitizerResult,
    ValidationResult,
)
from sraf.sandbox import RestrictedSubprocessSandbox
from sraf.sanitizer import PromptSanitizer, parse_sanitizer_response
from sraf.tools import default_tool_registry, evaluate_arithmetic, resolve_workspace_path


class SanitizerTests(unittest.TestCase):
    def test_parse_clean_prompt(self) -> None:
        result = parse_sanitizer_response("CLEAN_PROMPT\nLine 1\nLine 2")
        self.assertEqual(result.clean_prompt, "Line 1\nLine 2")
        self.assertFalse(result.has_conflicts)

    def test_parse_conflict(self) -> None:
        result = parse_sanitizer_response(
            'CONFLICT\n[{"instruction_a":"always python","instruction_b":"never python","explanation":"conflict"}]'
        )
        self.assertTrue(result.has_conflicts)
        self.assertEqual(result.conflicts[0].instruction_b, "never python")

    def test_local_sanitizer_removes_duplicate_lines(self) -> None:
        result = PromptSanitizer().sanitize("A\nA\nB")
        self.assertEqual(result.clean_prompt, "A\nB")


class InstructionTests(unittest.TestCase):
    def test_restore_missing_instructions_appends_required_text(self) -> None:
        prompt = restore_missing_instructions("Base", ["Ответ на русском"])
        self.assertIn("Ответ на русском", prompt)

    def test_local_validator_detects_missing_instruction(self) -> None:
        result = InstructionValidator().validate(["без импортов"], "Base prompt", "task")
        self.assertFalse(result.compliant)
        self.assertEqual(result.missing_instructions, ["без импортов"])


class ToolTests(unittest.TestCase):
    def test_calculator_accepts_arithmetic(self) -> None:
        self.assertEqual(evaluate_arithmetic("2 + 3 * 4"), 14)

    def test_sandbox_blocks_imports(self) -> None:
        result = RestrictedSubprocessSandbox().run("import os\nprint(os.getcwd())")
        self.assertEqual(result.status, "blocked")
        self.assertIn("Forbidden module", result.stderr)

    def test_sandbox_runs_simple_code(self) -> None:
        result = RestrictedSubprocessSandbox().run("print(2 + 2)")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.stdout.strip(), "4")

    def test_write_file_writes_inside_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = default_tool_registry(workspace_root=tmp)
            result = registry.call(
                "write_file",
                {"path": "generated/example.py", "content": "print('ok')\n"},
            )
            self.assertEqual(result["status"], "success")
            with open(result["path"], encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "print('ok')\n")

    def test_write_file_refuses_existing_file_without_overwrite(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = default_tool_registry(workspace_root=tmp)
            registry.call("write_file", {"path": "example.txt", "content": "one"})
            result = registry.call("write_file", {"path": "example.txt", "content": "two"})
            self.assertEqual(result["status"], "failure")

    def test_write_file_rejects_workspace_escape(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                resolve_workspace_path(Path(tmp), "../x")


class FakeExtractor:
    def extract(self, user_query: str) -> list[str]:
        return ["Ответ на русском"]


class FakeValidator:
    def validate(self, original_instructions: list[str], current_prompt: str, user_task: str) -> ValidationResult:
        missing = [item for item in original_instructions if item not in current_prompt]
        return ValidationResult(compliant=not missing, missing_instructions=missing)


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, system_prompt: str, user_query: str) -> AttemptResult:
        self.calls += 1
        answer = "плохой ответ" if self.calls == 1 else "хороший ответ"
        return AttemptResult(final_answer=answer, execution_log=[{"call": self.calls}])


class RecordingLoop:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, user_query: str, base_prompt: str) -> MetaLoopResult:
        self.prompts.append(base_prompt)
        return MetaLoopResult(
            output=f"Ответ на: {user_query}",
            final_prompt=base_prompt,
            attempts=1,
            original_instructions=[],
            status="success",
        )


class FakeEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, original_task: str, agent_output: str) -> EvaluationResult:
        self.calls += 1
        if self.calls == 1:
            return EvaluationResult(status="failure", feedback="Нужно исправить ответ.")
        return EvaluationResult(status="success", feedback="OK")


class FakeRefiner:
    def refine(
        self,
        current_prompt: str,
        task: str,
        execution_log: list[dict],
        evaluation_feedback: str,
    ) -> str:
        return current_prompt + "\nПеред финальным ответом проверь результат."


class FakeEscalation(HumanEscalationInterface):
    def resolve_prompt_conflicts(
        self,
        conflicts: list[PromptConflict],
        raw_prompt: str,
    ) -> EscalationResolution:
        return EscalationResolution(type="resolved_prompt", value=raw_prompt)

    def resolve_blocking_instructions(
        self,
        validation: ValidationResult,
        user_task: str,
        original_instructions: list[str],
    ) -> EscalationResolution:
        return EscalationResolution(type="abort")


class MetaLoopTests(unittest.TestCase):
    def test_meta_loop_refines_after_failed_attempt(self) -> None:
        loop = MetaLoop(
            extractor=FakeExtractor(),
            runner=FakeRunner(),
            evaluator=FakeEvaluator(),
            refiner=FakeRefiner(),
            sanitizer=PromptSanitizer(),
            validator=FakeValidator(),
            escalation=FakeEscalation(),
            max_attempts=3,
        )
        result = loop.run("Сделай что-нибудь", base_prompt="Base")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.attempts, 2)
        self.assertIn("Ответ на русском", result.final_prompt)


class ConversationTests(unittest.TestCase):
    def test_session_adds_previous_turn_to_next_prompt(self) -> None:
        loop = RecordingLoop()
        session = ConversationSession(loop, base_prompt="Base")
        session.ask("Создай файл generated/a.py")
        session.ask("Добавь еще один print")
        self.assertEqual(loop.prompts[0], "Base")
        self.assertIn("Создай файл generated/a.py", loop.prompts[1])
        self.assertIn("Ответ на: Создай файл generated/a.py", loop.prompts[1])
        self.assertNotIn("Добавь еще один print", loop.prompts[1])

    def test_context_does_not_accumulate_in_system_prompt(self) -> None:
        loop = RecordingLoop()
        session = ConversationSession(loop, base_prompt="Base")
        session.ask("Первый ход")
        session.ask("Второй ход")
        session.ask("Третий ход")
        self.assertEqual(session.system_prompt, "Base")
        self.assertEqual(loop.prompts[2].count("SRAF_CONVERSATION_CONTEXT_START"), 1)

    def test_build_prompt_limits_history(self) -> None:
        prompt = build_prompt_with_context(
            "Base",
            [
                ConversationTurn("one", "a", "success"),
                ConversationTurn("two", "b", "success"),
            ],
            1,
        )
        self.assertNotIn("one", prompt)
        self.assertIn("two", prompt)


if __name__ == "__main__":
    unittest.main()
