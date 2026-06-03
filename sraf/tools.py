from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sraf.evaluator import Evaluator
from sraf.sandbox import PythonSandbox, RestrictedSubprocessSandbox


ToolFunction = Callable[..., Any]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    function: ToolFunction

    def as_gigachat_function(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def specs(self) -> list[dict[str, Any]]:
        return [tool.as_gigachat_function() for tool in self._tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name].function(**arguments)


def default_tool_registry(
    *,
    sandbox: PythonSandbox | None = None,
    evaluator: Evaluator | None = None,
    workspace_root: str | Path = ".",
) -> ToolRegistry:
    sandbox = sandbox or RestrictedSubprocessSandbox()
    workspace = Path(workspace_root).resolve()

    def run_python(code: str) -> str:
        return sandbox.run(code).as_text()

    def verify_solution(task: str, result: str) -> dict[str, str]:
        if evaluator:
            verdict = evaluator.evaluate(task, result)
            return {"status": verdict.status, "feedback": verdict.feedback}
        if not result.strip():
            return {"status": "failure", "feedback": "Result is empty."}
        return {"status": "success", "feedback": "Heuristic verification passed; no LLM evaluator configured."}

    def calculator(expression: str) -> str:
        return str(evaluate_arithmetic(expression))

    def write_file(path: str, content: str, overwrite: bool = False) -> dict[str, str]:
        target = resolve_workspace_path(workspace, path)
        if target.exists() and not overwrite:
            return {
                "status": "failure",
                "path": str(target),
                "feedback": "File already exists. Pass overwrite=true to replace it.",
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "success", "path": str(target), "feedback": "File written."}

    return ToolRegistry(
        [
            ToolSpec(
                name="run_python",
                description="Execute Python code in a restricted sandbox and return stdout/stderr/status.",
                parameters={
                    "type": "object",
                    "properties": {"code": {"type": "string", "description": "Python code to execute."}},
                    "required": ["code"],
                },
                function=run_python,
            ),
            ToolSpec(
                name="verify_solution",
                description="Verify whether the result solves the original task.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "result": {"type": "string"},
                    },
                    "required": ["task", "result"],
                },
                function=verify_solution,
            ),
            ToolSpec(
                name="calculator",
                description="Evaluate a safe arithmetic expression.",
                parameters={
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
                function=calculator,
            ),
            ToolSpec(
                name="write_file",
                description="Write UTF-8 text to a relative path inside the current workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path inside the workspace.",
                        },
                        "content": {"type": "string", "description": "File content to write."},
                        "overwrite": {
                            "type": "boolean",
                            "description": "Whether to replace an existing file.",
                            "default": False,
                        },
                    },
                    "required": ["path", "content"],
                },
                function=write_file,
            ),
        ]
    )


def resolve_workspace_path(workspace: Path, path: str) -> Path:
    if not path.strip():
        raise ValueError("Path must not be empty.")
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("Only relative paths are allowed.")
    resolved = (workspace / candidate).resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError("Path escapes the workspace.")
    return resolved


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate_arithmetic(expression: str) -> int | float:
    node = ast.parse(expression, mode="eval")
    return _eval_arithmetic_node(node.body)


def _eval_arithmetic_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_arithmetic_node(node.left), _eval_arithmetic_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_arithmetic_node(node.operand))
    raise ValueError("Only arithmetic expressions are allowed.")
