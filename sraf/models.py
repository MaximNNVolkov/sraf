from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Status = Literal["success", "failure"]


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str | None = None
    name: str | None = None
    function_call: dict[str, Any] | None = None


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: Any


@dataclass(slots=True)
class AttemptResult:
    final_answer: str
    execution_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class EvaluationResult:
    status: Status
    feedback: str


@dataclass(slots=True)
class PromptConflict:
    instruction_a: str
    instruction_b: str
    explanation: str


@dataclass(slots=True)
class SanitizerResult:
    clean_prompt: str | None = None
    conflicts: list[PromptConflict] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


@dataclass(slots=True)
class ValidationResult:
    compliant: bool
    missing_instructions: list[str] = field(default_factory=list)
    blocking_instructions: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass(slots=True)
class EscalationResolution:
    type: Literal["abort", "new_task", "modified_instr", "resolved_prompt"]
    value: Any = None


@dataclass(slots=True)
class MetaLoopResult:
    output: str | None
    final_prompt: str
    attempts: int
    original_instructions: list[str]
    status: Status
    feedback: str = ""
