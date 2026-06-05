from __future__ import annotations

import json
import os
import sys
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILLS_DIR = Path.home() / ".sraf" / "skills"
REGISTRY_PATH = SKILLS_DIR / "registry.json"


def _ensure_dir() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> dict[str, dict[str, Any]]:
    _ensure_dir()
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {}


def _save_registry(registry: dict[str, dict[str, Any]]) -> None:
    _ensure_dir()
    REGISTRY_PATH.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_skill(name: str, code: str, description: str = "") -> dict[str, str]:
    """Save a Python function as a reusable skill.

    Args:
        name: Unique skill name (e.g. 'add_numbers').
        code: Full Python code of the skill (def statement + any helpers).
        description: What this skill does.

    Returns:
        dict with status/path/feedback.
    """
    _ensure_dir()

    # Basic validation
    if not name.replace("_", "").isalnum():
        return {"status": "failure", "feedback": "Name must contain only letters, digits, and underscores."}

    file_path = SKILLS_DIR / f"{name}.py"
    file_path.write_text(code.strip() + "\n", encoding="utf-8")

    registry = _load_registry()
    registry[name] = {
        "description": description,
        "file": str(file_path),
        "created": datetime.now(timezone.utc).isoformat(),
        "calls": 0,
    }
    _save_registry(registry)

    return {
        "status": "success",
        "path": str(file_path),
        "feedback": f"Skill '{name}' saved with {len(code)} chars.",
    }


def list_skills() -> list[dict[str, Any]]:
    """Return all registered skills with metadata."""
    registry = _load_registry()
    return [
        {
            "name": name,
            "description": info.get("description", ""),
            "calls": info.get("calls", 0),
            "created": info.get("created", ""),
        }
        for name, info in registry.items()
    ]


def get_skill(name: str) -> dict[str, Any] | None:
    """Get skill metadata by name."""
    registry = _load_registry()
    return registry.get(name)


def run_skill(name: str, arguments: str = "{}") -> dict[str, Any]:
    """Execute a saved skill by name.

    The skill code is loaded and called with the given arguments.
    The skill must define a function with the same name as the skill.

    Args:
        name: Skill name (must match a registered skill).
        arguments: JSON string of keyword arguments to pass.

    Returns:
        dict with status/stdout/stderr/returncode.
    """
    registry = _load_registry()
    skill = registry.get(name)
    if not skill:
        return {"status": "failure", "feedback": f"Skill '{name}' not found.", "stdout": "", "stderr": ""}

    file_path = Path(skill["file"])
    if not file_path.exists():
        return {"status": "failure", "feedback": f"Skill file '{file_path}' not found.", "stdout": "", "stderr": ""}

    code = file_path.read_text(encoding="utf-8")

    # Build a wrapper that calls the skill function with the provided args
    wrapper = f"""
{code}

import json, sys
_args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {{}}
_result = {name}(**_args)
print(json.dumps({{"result": _result}}, ensure_ascii=False))
"""

    with tempfile.TemporaryDirectory(prefix="sraf-skill-") as tmp:
        script_path = Path(tmp) / "run_skill.py"
        script_path.write_text(wrapper, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script_path), arguments],
                cwd=tmp,
                env={"PYTHONIOENCODING": "utf-8"},
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "timeout",
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "Timed out after 10 seconds.",
                "returncode": None,
            }

    # Increment call counter
    registry[name]["calls"] = registry[name].get("calls", 0) + 1
    _save_registry(registry)

    return {
        "status": "success" if proc.returncode == 0 else "failure",
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "returncode": proc.returncode,
    }


def delete_skill(name: str) -> dict[str, str]:
    """Remove a skill from registry and delete its file."""
    registry = _load_registry()
    skill = registry.pop(name, None)
    if not skill:
        return {"status": "failure", "feedback": f"Skill '{name}' not found."}

    _save_registry(registry)
    file_path = Path(skill["file"])
    if file_path.exists():
        file_path.unlink()
    return {"status": "success", "feedback": f"Skill '{name}' deleted."}
