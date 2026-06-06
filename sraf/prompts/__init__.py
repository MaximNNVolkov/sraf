"""System prompts for the SRAF agent loop.

Each prompt lives in its own file so it can be edited without changing code.
Set SRAF_PROMPTS_DIR env var or pass --prompts-dir to CLI to use custom prompts.
"""

from sraf.prompts.base_system import BASE_SYSTEM_PROMPT
from sraf.prompts.evaluator import EVALUATOR_PROMPT
from sraf.prompts.instruction_extractor import INSTRUCTION_EXTRACTOR_PROMPT
from sraf.prompts.instruction_validator import INSTRUCTION_VALIDATOR_PROMPT
from sraf.prompts.refiner import PROMPT_REFINER_PROMPT
from sraf.prompts.sanitizer import PROMPT_SANITIZER_PROMPT

__all__ = [
    "BASE_SYSTEM_PROMPT",
    "EVALUATOR_PROMPT",
    "INSTRUCTION_EXTRACTOR_PROMPT",
    "INSTRUCTION_VALIDATOR_PROMPT",
    "PROMPT_REFINER_PROMPT",
    "PROMPT_SANITIZER_PROMPT",
]
