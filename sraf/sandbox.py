from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SandboxResult:
    status: str
    stdout: str
    stderr: str
    returncode: int | None

    def as_text(self) -> str:
        parts = [f"status={self.status}"]
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        if self.returncode is not None:
            parts.append(f"returncode={self.returncode}")
        return "\n".join(parts)


class PythonSandbox(ABC):
    @abstractmethod
    def run(self, code: str, timeout_seconds: int = 5) -> SandboxResult:
        """Execute Python code and return captured output."""


class RestrictedSubprocessSandbox(PythonSandbox):
    FORBIDDEN_NODES = (ast.Global, ast.Nonlocal)
    FORBIDDEN_MODULES = {
        "subprocess",
        "os",
        "socket",
        "shutil",
        "ctypes",
        "signal",
        "requests",
        "urllib",
        "http",
        "importlib",
    }
    FORBIDDEN_NAMES = {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "input",
        "locals",
        "open",
        "vars",
    }
    FORBIDDEN_ATTRS = {
        "__class__",
        "__dict__",
        "__globals__",
        "__subclasses__",
        "__mro__",
    }

    def run(self, code: str, timeout_seconds: int = 5) -> SandboxResult:
        try:
            self._validate_ast(code)
        except ValueError as exc:
            return SandboxResult(status="blocked", stdout="", stderr=str(exc), returncode=None)

        with tempfile.TemporaryDirectory(prefix="sraf-sandbox-") as tmp:
            script_path = Path(tmp) / "snippet.py"
            script_path.write_text(code, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", str(script_path)],
                    cwd=tmp,
                    env={"PYTHONIOENCODING": "utf-8"},
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return SandboxResult(
                    status="timeout",
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or f"Timed out after {timeout_seconds} seconds.",
                    returncode=None,
                )

        status = "success" if proc.returncode == 0 else "failure"
        return SandboxResult(status=status, stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)

    def _validate_ast(self, code: str) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(f"Syntax error: {exc}") from exc

        for node in ast.walk(tree):
            if isinstance(node, self.FORBIDDEN_NODES):
                raise ValueError(f"Forbidden syntax: {type(node).__name__}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_module = alias.name.split(".")[0]
                    if root_module in self.FORBIDDEN_MODULES:
                        raise ValueError(f"Forbidden module: {root_module}")
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    root_module = node.module.split(".")[0]
                    if root_module in self.FORBIDDEN_MODULES:
                        raise ValueError(f"Forbidden module: {root_module}")
            if isinstance(node, ast.Name) and node.id in self.FORBIDDEN_NAMES:
                raise ValueError(f"Forbidden name: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in self.FORBIDDEN_ATTRS:
                raise ValueError(f"Forbidden attribute: {node.attr}")


class DockerPythonSandbox(PythonSandbox):
    def __init__(
        self,
        image: str = "python:3.11-slim",
        *,
        memory: str = "128m",
        cpus: str = "0.5",
    ) -> None:
        self.image = image
        self.memory = memory
        self.cpus = cpus

    def run(self, code: str, timeout_seconds: int = 5) -> SandboxResult:
        if shutil.which("docker") is None:
            return SandboxResult(status="failure", stdout="", stderr="Docker is not installed.", returncode=None)

        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "-i",
            self.image,
            "python",
            "-I",
            "-",
        ]
        try:
            proc = subprocess.run(
                command,
                input=code,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                status="timeout",
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"Timed out after {timeout_seconds} seconds.",
                returncode=None,
            )
        status = "success" if proc.returncode == 0 else "failure"
        return SandboxResult(status=status, stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)
