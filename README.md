# Self-Refining Agent Framework

SRAF is a Python implementation of a self-refining LLM agent loop:

- extracts immutable user instructions once;
- runs an agent attempt with tools;
- evaluates the result;
- refines and sanitizes the system prompt after failures;
- validates that original instructions remain present;
- escalates prompt conflicts or blocking requirements to a human.

The package is GigaChat-first, but the core loop is built around a small `LLMClient`
interface so tests and alternative providers can be plugged in.

## Quick Start

✅ **Already set up and working!**

```bash
# Virtual environment is already created at .venv
source .venv/bin/activate

# Load credentials from .env
export $(cat .env | grep -v '#' | xargs)

# Test with demo mode (no internet needed)
sraf run "Напиши приветствие" --demo

# Run with real GigaChat API
sraf run "Напиши функцию быстрой сортировки. Ответ на русском." --no-verify-ssl
```

### Useful options:

```bash
# Control iteration limits
sraf run "..." --max-attempts 3 --max-steps 5

# Custom base prompt
sraf run "..." --base-prompt-file prompt.txt

# Disable SSL verification (for corporate proxy)
sraf run "..." --no-verify-ssl

# Demo mode (no API calls)
sraf run "..." --demo

# Interactive chat
sraf chat --no-verify-ssl
echo "Your task here" | sraf chat --demo
```

### Environment Setup

```bash
# The credentials are already in .env file
GIGACHAT_CREDENTIALS=NjYxZWRhZmUtZDQ3Ni00NDdlLWI0ODQtZmU1YzM0ZjBlMWQyOmQwZjFlNzgzLTE1NTEtNDQyNy1hM2IwLTZmMzc2ODg0MDVkNQ==

# Other optional settings
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat
```
python3 -m unittest
```

To ask the agent to create a file, include the target path explicitly:

```bash
sraf run "Создай файл generated/merge_sort.py с функцией сортировки слиянием на Python." --no-verify-ssl
```

The `write_file` tool only accepts relative paths inside the current workspace.

For multi-turn work, use chat mode. The session includes previous turns in the
next prompt, so short follow-ups can refer to earlier results:

```bash
sraf chat --no-verify-ssl
> Создай файл generated/merge_sort.py с функцией сортировки слиянием на Python.
> Добавь в эту функцию еще один диагностический print и перезапиши файл.
> exit
```

## Safety

`run_python` uses a sandbox abstraction:

- `DockerPythonSandbox` can run code inside Docker with disabled network, CPU,
  memory and timeout limits.
- `RestrictedSubprocessSandbox` is the default fallback. It blocks dangerous AST
  nodes and names, runs code in a temporary directory, uses isolated Python mode,
  and enforces a timeout.

For production, configure Docker and pass the Docker sandbox into the tool
registry; the local restricted subprocess is a development fallback, not a full
security boundary.

## Environment

The CLI expects GigaChat credentials in `GIGACHAT_CREDENTIALS`. Optional variables:

- `GIGACHAT_SCOPE`
- `GIGACHAT_MODEL`
- `GIGACHAT_VERIFY_SSL`

If your local proxy injects a self-signed certificate, use either:

```bash
export GIGACHAT_VERIFY_SSL=false
sraf run "Ответь коротко: привет"
```

or:

```bash
sraf run "Ответь коротко: привет" --no-verify-ssl
```

## Current Status

✅ **Fully working and tested**

- ✅ GigaChat API integration complete
- ✅ CLI interface (run, chat) operational
- ✅ Demo mode for development (no API calls)
- ✅ SSL verification handling (corporate proxy support)
- ✅ Credentials management via .env
- ✅ Error handling and helpful messages

### Test Results

```bash
# Demo mode test
sraf run "Напиши привет" --demo
# ✅ Returns: {"final_answer": "Hello! This is a demo response.", ...}

# Real API test
sraf run "Напиши краткое приветствие" --no-verify-ssl
# ✅ Returns: "Hello! How can I assist you today?"

# Chat mode test
echo "Напиши стихотворение" | sraf chat --demo
# ✅ Works interactively
```

## Troubleshooting

### SSL Certificate Verification Error

**Problem:** `FileNotFoundError: [Errno 2] No such file or directory`

**Solution:** Use `--no-verify-ssl` flag:
```bash
sraf run "task" --no-verify-ssl
```

**Note:** This is normal in corporate environments with local proxy servers.

### Missing GIGACHAT_CREDENTIALS

**Problem:** `RuntimeError: GIGACHAT_CREDENTIALS is required`

**Solution:** Load credentials from .env:
```bash
export $(cat .env | grep -v '#' | xargs)
```

### Connection Issues

**Problem:** `Connection timeout` or `Connection refused`

**Solution:** Test with demo mode first:
```bash
sraf run "test" --demo  # Should work instantly

# If demo works but API doesn't, check:
curl -I https://gigachat.devices.sberbank.ru/  # Check connectivity
```

## Additional Documentation

- [README_SRAF.md](README_SRAF.md) - Comprehensive guide
- [SSL_TROUBLESHOOTING.md](SSL_TROUBLESHOOTING.md) - SSL troubleshooting guide
