"""Prompt for evaluating whether the agent's output solves the original task."""

EVALUATOR_PROMPT = """Задача пользователя:
{original_task}

Ответ агента:
{agent_output}

Оцени, полностью ли решена задача и соответствует ли ответ всем требованиям.
Обрати внимание на корректность, полноту и соблюдение ограничений.
Ответь строго в формате JSON:
{{
  "status": "success" или "failure",
  "feedback": "подробное описание ошибки или похвала"
}}"""
