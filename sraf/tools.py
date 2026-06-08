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


def core_tool_registry(
    *,
    sandbox: PythonSandbox | None = None,
    workspace_root: str | Path = ".",
) -> ToolRegistry:
    """Core tools — always available. Small footprint (~5 tools)."""
    sandbox = sandbox or RestrictedSubprocessSandbox()
    workspace = Path(workspace_root).resolve()

    def run_python(code: str) -> str:
        return sandbox.run(code).as_text()

    def list_files(path: str = ".", recursive: bool = False) -> dict[str, Any]:
        """List files and directories at the given path. Use recursive=True to show full project tree including nested folders."""
        import os
        target = Path(path)
        if not target.exists():
            return {"status": "failure", "feedback": f"Path '{path}' does not exist."}
        if not target.is_dir():
            return {"status": "failure", "feedback": f"Path '{path}' is not a directory."}
        entries = []
        if recursive:
            for root, dirs, files in os.walk(str(target)):
                root_path = Path(root)
                rel = root_path.relative_to(target)
                if rel == Path("."):
                    prefix = ""
                else:
                    prefix = str(rel) + "/"
                dirs.sort()
                files.sort()
                for d in sorted(dirs):
                    entries.append(f"📁 {prefix}{d}")
                for f in files:
                    entries.append(f"📄 {prefix}{f}")
        else:
            for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
                kind = "📁" if entry.is_dir() else "📄"
                entries.append(f"{kind} {entry.name}")
        return {"status": "success", "path": str(target.resolve()), "entries": entries, "count": len(entries)}

    def read_file(path: str, offset: int = 1, limit: int = 200) -> dict[str, Any]:
        """Read the contents of a text file. Shows line numbers. Use offset and limit to read specific sections."""
        target = resolve_workspace_path(workspace, path)
        if not target.exists():
            return {"status": "failure", "feedback": f"File '{path}' does not exist."}
        if target.is_dir():
            return {"status": "failure", "feedback": f"'{path}' is a directory, not a file."}
        try:
            lines = target.read_text(encoding="utf-8").splitlines(keepends=False)
            total = len(lines)
            start = max(0, offset - 1)
            end = min(total, offset - 1 + limit)
            shown = lines[start:end]
            content = "\n".join(f"{start+i+1:>4}|{line}" for i, line in enumerate(shown))
            return {
                "status": "success",
                "path": str(target),
                "total_lines": total,
                "offset": offset,
                "limit": limit,
                "content": content,
            }
        except Exception as exc:
            return {"status": "failure", "path": path, "feedback": f"Cannot read file: {exc}"}

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
                name="list_files",
                description="List files in a directory. recursive=True shows all nested files.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path (default: current dir).",
                            "default": ".",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Show files in subdirectories too?",
                            "default": False,
                        },
                    },
                },
                function=list_files,
            ),
            ToolSpec(
                name="read_file",
                description="Read a text file with line numbers. Use offset/limit for large files.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path.",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Start line (default: 1).",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Lines to read (default: 200).",
                            "default": 200,
                        },
                    },
                    "required": ["path"],
                },
                function=read_file,
            ),
            ToolSpec(
                name="run_python",
                description="Execute Python code in a sandbox. Returns stdout/stderr.",
                parameters={
                    "type": "object",
                    "properties": {"code": {"type": "string", "description": "Python code."}},
                    "required": ["code"],
                },
                function=run_python,
            ),
            ToolSpec(
                name="write_file",
                description="Write text to a file (relative path inside workspace).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path.",
                        },
                        "content": {"type": "string", "description": "File content."},
                        "overwrite": {
                            "type": "boolean",
                            "description": "Overwrite existing file?",
                            "default": False,
                        },
                    },
                    "required": ["path", "content"],
                },
                function=write_file,
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
        ]
    )


def extended_tool_registry(
    *,
    evaluator: Evaluator | None = None,
    sandbox: PythonSandbox | None = None,
    workspace_root: str | Path = ".",
) -> ToolRegistry:
    """Extended tools — added only when the agent needs them (skills, install, verify).
    Returns a ToolRegistry so you can merge specs with core:
      all_specs = core.specs() + extended.specs()
    """
    sandbox = sandbox or RestrictedSubprocessSandbox()
    workspace = Path(workspace_root).resolve()

    def install_package(package: str) -> dict[str, str]:
        """Install a pip package and return result."""
        import subprocess, sys, os
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

    def verify_solution(task: str, result: str) -> dict[str, str]:
        if evaluator:
            verdict = evaluator.evaluate(task, result)
            return {"status": verdict.status, "feedback": verdict.feedback}
        if not result.strip():
            return {"status": "failure", "feedback": "Result is empty."}
        return {"status": "success", "feedback": "Heuristic verification passed; no LLM evaluator configured."}

    return ToolRegistry(
        [
            ToolSpec(
                name="save_skill",
                description="Save a Python function as a reusable skill. Call after creating working code.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Unique skill name."},
                        "code": {"type": "string", "description": "Complete Python code."},
                        "description": {"type": "string", "description": "What this skill does and how to use it."},
                    },
                    "required": ["name", "code", "description"],
                },
                function=_save_skill,
            ),
            ToolSpec(
                name="list_skills",
                description="List all saved skills.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
                function=lambda: _list_skills(),
            ),
            ToolSpec(
                name="run_skill",
                description="Run a saved skill by name with JSON arguments.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name."},
                        "arguments": {"type": "string", "description": "JSON kwargs string (e.g. '{\"a\":3}')."},
                    },
                    "required": ["name", "arguments"],
                },
                function=_run_skill,
            ),
            ToolSpec(
                name="delete_skill",
                description="Delete a saved skill file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name."},
                    },
                    "required": ["name"],
                },
                function=_delete_skill,
            ),
            ToolSpec(
                name="install_package",
                description="Install pip package. Ask user first!",
                parameters={
                    "type": "object",
                    "properties": {
                        "package": {
                            "type": "string",
                            "description": "Package name (e.g. 'requests').",
                        },
                    },
                    "required": ["package"],
                },
                function=install_package,
            ),
            ToolSpec(
                name="verify_solution",
                description="Check whether the result solves the original task.",
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
        ]
    )


def default_tool_registry(
    *,
    sandbox: PythonSandbox | None = None,
    evaluator: Evaluator | None = None,
    workspace_root: str | Path = ".",
) -> ToolRegistry:
    """Full tool registry — all tools, for backward compatibility."""
    core = core_tool_registry(sandbox=sandbox, workspace_root=workspace_root)
    extended = extended_tool_registry(sandbox=sandbox, evaluator=evaluator, workspace_root=workspace_root)
    all_specs = core.specs() + extended.specs()
    merged = ToolRegistry([core._tools[name] for name in core._tools] + [extended._tools[name] for name in extended._tools])
    return merged


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
