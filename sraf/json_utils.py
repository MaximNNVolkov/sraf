from __future__ import annotations

import json
import re
from typing import Any


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def loads_jsonish(text: str) -> Any:
    """Parse strict JSON, fenced JSON, or the first JSON-looking fragment."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty JSON response")

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    block = _JSON_BLOCK_RE.search(stripped)
    if block:
        return json.loads(block.group(1).strip())

    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx != -1]
    if not starts:
        raise ValueError(f"no JSON object or array found: {text[:120]}")

    start = min(starts)
    closer = "}" if stripped[start] == "{" else "]"
    end = stripped.rfind(closer)
    if end < start:
        raise ValueError(f"unterminated JSON fragment: {text[:120]}")
    return json.loads(stripped[start : end + 1])


def dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
