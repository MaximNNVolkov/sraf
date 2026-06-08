from __future__ import annotations

from dataclasses import dataclass

from sraf.meta_loop import MetaLoop
from sraf.models import MetaLoopResult
from sraf.prompts import BASE_SYSTEM_PROMPT


CONTEXT_START = "=== SRAF_CONVERSATION_CONTEXT_START ==="
CONTEXT_END = "=== SRAF_CONVERSATION_CONTEXT_END ==="


@dataclass(slots=True)
class ConversationTurn:
    user_query: str
    assistant_output: str
    status: str


class ConversationSession:
    def __init__(
        self,
        loop: MetaLoop,
        *,
        base_prompt: str = BASE_SYSTEM_PROMPT,
        max_history_turns: int = 6,
    ) -> None:
        self.loop = loop
        self.system_prompt = strip_conversation_context(base_prompt)
        self.max_history_turns = max_history_turns
        self.turns: list[ConversationTurn] = []

    def ask(self, user_query: str) -> MetaLoopResult:
        effective_prompt = build_prompt_with_context(self.system_prompt, self.turns, self.max_history_turns)
        result = self.loop.run(user_query, base_prompt=effective_prompt)
        self.system_prompt = strip_conversation_context(result.final_prompt)
        self.turns.append(
            ConversationTurn(
                user_query=user_query,
                assistant_output=result.output or result.feedback,
                status=result.status,
            )
        )
        return result


def build_prompt_with_context(
    base_prompt: str,
    turns: list[ConversationTurn],
    max_history_turns: int,
    max_context_chars: int = 2000,
) -> str:
    relevant_turns = turns[-max_history_turns:]
    if not relevant_turns:
        return strip_conversation_context(base_prompt)

    context_lines = [
        CONTEXT_START,
        "Используй этот контекст предыдущего диалога при выполнении текущего запроса.",
        "Если пользователь просит изменить, дополнить или продолжить результат, считай это ссылкой на последние ответы, созданные файлы и требования ниже.",
    ]
    for index, turn in enumerate(relevant_turns, start=1):
        context_lines.append(f"\nХод {index}.")
        context_lines.append(f"Пользователь: {turn.user_query}")
        output = turn.assistant_output
        if len(output) > max_context_chars:
            output = output[:max_context_chars] + f"\n... [вывод сокращён с {len(output)} до {max_context_chars} символов]"
        context_lines.append(f"Агент ({turn.status}): {output}")
    context_lines.append(CONTEXT_END)
    return f"{strip_conversation_context(base_prompt).rstrip()}\n\n" + "\n".join(context_lines)


def strip_conversation_context(prompt: str) -> str:
    while CONTEXT_START in prompt and CONTEXT_END in prompt:
        start = prompt.index(CONTEXT_START)
        end = prompt.index(CONTEXT_END, start) + len(CONTEXT_END)
        prompt = f"{prompt[:start]}{prompt[end:]}"
    return prompt.strip()
