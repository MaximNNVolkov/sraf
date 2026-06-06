"""Prompt for validating that instructions are preserved in the system prompt."""

INSTRUCTION_VALIDATOR_PROMPT = """Первичные инструкции пользователя (обязательны к соблюдению):
{original_instructions}

Текущий системный промпт агента:
{current_prompt}

Задача:
{user_task}

Проверь:
1. Все ли первичные инструкции явно присутствуют или логически следуют из текущего промпта? Если какая-то отсутствует, укажи ее в missing_instructions.
2. Можно ли выполнить задачу, не нарушая эти инструкции? Если какая-то инструкция блокирует выполнение, помести ее в blocking_instructions и объясни причину.

Ответ строго JSON:
{{
  "compliant": true/false,
  "missing_instructions": ["инструкция 1", ...],
  "blocking_instructions": ["инструкция 2", ...],
  "explanation": "пояснение"
}}"""
