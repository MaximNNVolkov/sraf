# SRAF — Self-Refining Agent Framework

GigaChat-first LLM agent loop with tools, evaluation, refinement, and human escalation.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Set credentials in `.env`:

```
GIGACHAT_CREDENTIALS=your_client_id:your_secret
```

Run:

```bash
sraf run "твой запрос"
```

Demo mode (no API needed):

```bash
sraf run "привет" --demo
```

## Test

```bash
pip install -e ".[dev]"
pytest
```
