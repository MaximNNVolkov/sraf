from __future__ import annotations

import re

from sraf.json_utils import loads_jsonish
from sraf.llm import LLMClient
from sraf.models import ChatMessage, PromptConflict, SanitizerResult
from sraf.prompts import PROMPT_SANITIZER_PROMPT


class PromptSanitizer:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def sanitize(self, raw_prompt: str) -> SanitizerResult:
        if self.llm is None:
            return self._local_sanitize(raw_prompt)

        response = self.llm.complete(
            [ChatMessage(role="user", content=PROMPT_SANITIZER_PROMPT.format(raw_prompt=raw_prompt))]
        )
        if not response.content:
            return self._local_sanitize(raw_prompt)
        return parse_sanitizer_response(response.content)

    @staticmethod
    def _local_sanitize(raw_prompt: str) -> SanitizerResult:
        lines = [line.strip() for line in raw_prompt.splitlines()]
        unique_lines: list[str] = []
        seen: set[str] = set()
        for line in lines:
            if not line:
                if unique_lines and unique_lines[-1]:
                    unique_lines.append("")
                continue
            key = re.sub(r"\s+", " ", line.lower())
            if key not in seen:
                unique_lines.append(line)
                seen.add(key)

        prompt = "\n".join(unique_lines).strip()
        conflicts = detect_simple_conflicts(unique_lines)
        if conflicts:
            return SanitizerResult(conflicts=conflicts)
        return SanitizerResult(clean_prompt=prompt)


def parse_sanitizer_response(text: str) -> SanitizerResult:
    stripped = text.strip()
    if stripped.startswith("CLEAN_PROMPT"):
        _, _, prompt = stripped.partition("\n")
        return SanitizerResult(clean_prompt=prompt.strip())
    if stripped.startswith("CONFLICT"):
        _, _, payload = stripped.partition("\n")
        data = loads_jsonish(payload)
        return SanitizerResult(
            conflicts=[
                PromptConflict(
                    instruction_a=str(item.get("instruction_a", "")),
                    instruction_b=str(item.get("instruction_b", "")),
                    explanation=str(item.get("explanation", "")),
                )
                for item in data
            ]
        )
    return PromptSanitizer._local_sanitize(stripped)


def detect_simple_conflicts(lines: list[str]) -> list[PromptConflict]:
    conflicts: list[PromptConflict] = []
    normalized = [(line, line.lower()) for line in lines if line.strip()]
    pairs = [
        (("всегда", "python"), ("никогда", "python")),
        (("обязательно", "код"), ("не", "писать", "код")),
        (("используй", "инструмент"), ("не", "используй", "инструмент")),
        (("ответ", "рус"), ("ответ", "англ")),
    ]
    for first_terms, second_terms in pairs:
        first = [line for line, lower in normalized if all(term in lower for term in first_terms)]
        second = [line for line, lower in normalized if all(term in lower for term in second_terms)]
        if first and second:
            conflicts.append(
                PromptConflict(
                    instruction_a=first[0],
                    instruction_b=second[0],
                    explanation="Instructions appear to require mutually exclusive behavior.",
                )
            )
    return conflicts
