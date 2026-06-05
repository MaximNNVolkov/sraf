from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sraf.evaluator import Evaluator
from sraf.sandbox import PythonSandbox, RestrictedSubprocessSandbox
from sraf.skill_manager import (
    save_skill as _save_skill,
    list_skills as _list_skills,
    run_skill as _run_skill,
    delete_skill as _delete_skill,
)


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

    def install_package(package: str) -> dict[str, str]:
        """Install a pip package and return result."""
        import subprocess, sys, os
        # Unset SOCKS proxy for pip (can't reach PyPI through SOCKS)
        clean_env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                env=clean_env,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0:
                return {"status": "success", "feedback": f"Package '{package}' installed.", "output": proc.stdout}
            return {"status": "failure", "feedback": f"Failed to install '{package}': {proc.stderr[:300]}", "output": proc.stderr[:300]}
        except subprocess.TimeoutExpired:
            return {"status": "failure", "feedback": f"Installation of '{package}' timed out."}

    def list_files(path: str = ".") -> dict[str, Any]:
        """List files and directories at the given path."""
        import os
        target = Path(path)
        if not target.exists():
            return {"status": "failure", "feedback": f"Path '{path}' does not exist."}
        if not target.is_dir():
            return {"status": "failure", "feedback": f"Path '{path}' is not a directory."}
        entries = []
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            kind = "📁" if entry.is_dir() else "📄"
            entries.append(f"{kind} {entry.name}")
        return {"status": "success", "path": str(target.resolve()), "entries": entries, "count": len(entries)}

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
                name="save_skill",
                description="Save a working Python function as a reusable skill. Always call this after creating a working function so it can be reused later.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Unique skill name (e.g. 'add_numbers')."},
                        "code": {"type": "string", "description": "Complete Python code defining the function."},
                        "description": {"type": "string", "description": "What this skill does."},
                    },
                    "required": ["name", "code", "description"],
                },
                function=_save_skill,
            ),
            ToolSpec(
                name="list_skills",
                description="List all saved skills with their names, descriptions, and usage count.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
                function=lambda: _list_skills(),
            ),
            ToolSpec(
                name="run_skill",
                description="Execute a saved skill by name with the given arguments. Returns the result as JSON.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the saved skill to run."},
                        "arguments": {"type": "string", "description": "JSON object as string with keyword arguments (e.g. '{\"a\": 3, \"b\": 5}')."},
                    },
                    "required": ["name", "arguments"],
                },
                function=_run_skill,
            ),
            ToolSpec(
                name="delete_skill",
                description="Remove a skill from the registry and delete its file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the skill to delete."},
                    },
                    "required": ["name"],
                },
                function=_delete_skill,
            ),
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
            ToolSpec(
                name="install_package",
                description="Install a Python package via pip. BEFORE calling this, ALWAYS ask the user for permission.",
                parameters={
                    "type": "object",
                    "properties": {
                        "package": {
                            "type": "string",
                            "description": "Package name (e.g. 'requests', 'numpy').",
                        },
                    },
                    "required": ["package"],
                },
                function=install_package,
            ),
            ToolSpec(
                name="list_files",
                description="List files and directories at a given path. Returns entries with icons and count.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path (default: current directory).",
                            "default": ".",
                        },
                    },
                },
                function=list_files,
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
