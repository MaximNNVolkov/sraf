# Project Structure & File Guide

## 📁 Directory Overview

```
/home/user1/lab_agent/
├── 📄 Documentation (Start here!)
│   ├── README.md                    ← Main README (current status)
│   ├── QUICKSTART.md                ← 5-minute quick start
│   ├── README_SRAF.md               ← Comprehensive guide
│   ├── SSL_TROUBLESHOOTING.md       ← SSL solutions
│   ├── SOLUTION_SUMMARY.md          ← Problem/solution details
│   └── IMPLEMENTATION_SUMMARY.md    ← This implementation
│
├── 🚀 Scripts (Use these!)
│   ├── sraf.sh                      ← Main launcher script
│   └── verify.sh                    ← Verification test suite
│
├── 🔧 Configuration
│   ├── .env                         ← Credentials (keep secret!)
│   ├── .gitignore                   ← Git ignore rules
│   ├── pyproject.toml               ← Python project config
│   └── setup.py                     ← Installation script
│
├── 📦 Source Code
│   ├── sraf/
│   │   ├── __init__.py              ← Package init
│   │   ├── cli.py                   ← CLI interface
│   │   ├── llm.py                   ← GigaChat client (FIXED!)
│   │   ├── meta_loop.py             ← Main agent loop
│   │   ├── conversation.py          ← Chat mode
│   │   ├── instructions.py          ← Instruction extraction
│   │   ├── evaluator.py             ← Result evaluation
│   │   ├── refiner.py               ← Result refinement
│   │   ├── runner.py                ← Tool execution
│   │   ├── models.py                ← Data models
│   │   ├── prompts.py               ← Prompt templates
│   │   ├── tools.py                 ← Tool registry
│   │   ├── json_utils.py            ← JSON utilities
│   │   ├── sanitizer.py             ← Input sanitization
│   │   ├── escalation.py            ← Escalation handling
│   │   └── sandbox.py               ← Code sandbox
│   │
│   └── tests/
│       ├── __init__.py              ← Test package
│       └── test_sraf.py             ← Test suite
│
├── 📝 Generated Content
│   └── sraf.egg-info/               ← Package metadata
│       ├── PKG-INFO
│       ├── SOURCES.txt
│       ├── requires.txt
│       ├── entry_points.txt
│       ├── dependency_links.txt
│       └── top_level.txt
│
└── .venv/                           ← Python virtual environment
    ├── bin/
    │   ├── python
    │   ├── pip
    │   ├── sraf                     ← CLI entry point
    │   └── ...
    ├── lib/
    │   └── python3.10/site-packages/
    │       ├── gigachat/            ← GigaChat SDK
    │       ├── httpx/               ← HTTP client
    │       └── ...
    └── ...
```

## 📖 Documentation Guide

### Start With These:

1. **[README.md](README.md)** (5 min read)
   - Current project status
   - Quick start commands
   - Troubleshooting
   - **👉 Start here for overview**

2. **[QUICKSTART.md](QUICKSTART.md)** (5 min read)
   - Copy-paste ready commands
   - Common use cases
   - Basic examples
   - **👉 Start here for quick usage**

3. **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** (10 min read)
   - What was fixed
   - How it works
   - Architecture overview
   - **👉 Start here for technical details**

### Deep Dives:

4. **[README_SRAF.md](README_SRAF.md)** (20 min read)
   - Comprehensive guide
   - All parameters explained
   - Advanced features
   - Best practices

5. **[SSL_TROUBLESHOOTING.md](SSL_TROUBLESHOOTING.md)** (10 min read)
   - SSL error solutions
   - Corporate proxy setup
   - Certificate handling

6. **[SOLUTION_SUMMARY.md](SOLUTION_SUMMARY.md)** (15 min read)
   - Detailed problem breakdown
   - Root cause analysis
   - Implementation details
   - Code explanations

## 🚀 Script Guide

### sraf.sh - Main Launcher
```bash
# What it does:
# 1. Activates virtual environment
# 2. Loads credentials from .env
# 3. Passes arguments to SRAF CLI

# Usage:
./sraf.sh run "task" --no-verify-ssl
./sraf.sh chat --no-verify-ssl
./sraf.sh run "task" --demo
```

### verify.sh - Test Suite
```bash
# What it does:
# 1. Checks virtual environment
# 2. Verifies credentials
# 3. Tests demo mode
# 4. Tests real API

# Usage:
./verify.sh
```

## 🔧 Key Source Files

### sraf/llm.py ⭐ FIXED
```python
# What was fixed:
# - SSL context handling
# - GigaChat client initialization
# - SSL verification logic

# Key fix:
ctx = ssl.create_default_context()
ctx.check_hostname = False     # Must be first!
ctx.verify_mode = ssl.CERT_NONE # Must be second!
```

### sraf/cli.py 
```python
# What it does:
# - Parses command-line arguments
# - Handles --demo, --no-verify-ssl flags
# - Initializes LLM client
# - Runs or chats
```

### sraf/meta_loop.py
```python
# The main agent loop:
# 1. Extract instructions
# 2. Validate instructions
# 3. Execute with tools
# 4. Evaluate results
# 5. Refine if needed
```

## 📋 Configuration Files

### .env
```bash
# Stores credentials
GIGACHAT_CREDENTIALS=NjYx...

# Optional settings
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat
```

### pyproject.toml
```toml
# Python project metadata
# Dependencies
# Build configuration
# Entry points
```

### .gitignore
```
# Protects:
.env              ← Credentials never committed
__pycache__/      ← Python cache files
.venv/            ← Virtual environment
*.pyc             ← Compiled Python
.pytest_cache/    ← Test cache
```

## 📦 Dependencies

### Main
- `gigachat` - GigaChat API SDK
- `httpx` - HTTP client library
- `pydantic` - Data validation

### Development
- `pytest` - Testing framework
- `pytest-cov` - Coverage measurement
- `black` - Code formatting
- `pylint` - Code linting

## 🧪 Test Files

### tests/test_sraf.py
```python
# Test suite for SRAF
# Tests various components
# Can be run with: python -m pytest tests/
```

## 📊 File Purpose Summary

| File | Purpose | Status |
|------|---------|--------|
| README.md | Main documentation | ✅ Updated |
| QUICKSTART.md | Quick start guide | ✅ Updated |
| README_SRAF.md | Comprehensive guide | ✅ Created |
| SSL_TROUBLESHOOTING.md | SSL solutions | ✅ Created |
| SOLUTION_SUMMARY.md | Problem/solution | ✅ Created |
| IMPLEMENTATION_SUMMARY.md | Implementation details | ✅ Created |
| sraf.sh | Launcher script | ✅ Created |
| verify.sh | Test suite | ✅ Created |
| .env | Credentials | ✅ Configured |
| sraf/llm.py | GigaChat client | ✅ Fixed |
| sraf/cli.py | CLI interface | ✅ Updated |

## 🎯 Which File To Read?

**"I just want to use it"**
→ Read: [QUICKSTART.md](QUICKSTART.md)
→ Run: `./sraf.sh run "task" --no-verify-ssl`

**"Tell me what was fixed"**
→ Read: [SOLUTION_SUMMARY.md](SOLUTION_SUMMARY.md)

**"I need technical details"**
→ Read: [README_SRAF.md](README_SRAF.md)

**"SSL keeps giving me errors"**
→ Read: [SSL_TROUBLESHOOTING.md](SSL_TROUBLESHOOTING.md)

**"How do I use different features?"**
→ Read: [README.md](README.md)

**"What are all the parameters?"**
→ Read: [README_SRAF.md](README_SRAF.md) Advanced section

**"Something doesn't work"**
→ Run: `./verify.sh`
→ Read: [SSL_TROUBLESHOOTING.md](SSL_TROUBLESHOOTING.md)

## 🚀 Getting Started Path

```
1. Read README.md              (5 min)
   ↓
2. Run verify.sh               (1 min)
   ↓
3. Try ./sraf.sh run "test" --demo  (30 sec)
   ↓
4. Try ./sraf.sh run "task" --no-verify-ssl  (1-5 min)
   ↓
5. Try ./sraf.sh chat --no-verify-ssl  (interactive)
   ↓
6. Read advanced guides as needed
```

## 📞 Troubleshooting Flowchart

```
Something not working?
  ↓
  ├─ SSL error?
  │  └─ Read: SSL_TROUBLESHOOTING.md
  │
  ├─ "How do I...?" questions?
  │  └─ Read: README_SRAF.md
  │
  ├─ Specific command help?
  │  └─ Run: ./sraf.sh [command] --help
  │
  └─ General issues?
     └─ Run: ./verify.sh
     └─ Read: SOLUTION_SUMMARY.md
```

---

## 💡 Pro Tips

1. **Always start with demo mode:**
   ```bash
   ./sraf.sh run "test" --demo
   ```

2. **Use the launcher script:**
   ```bash
   ./sraf.sh [command]  # Not: python -m sraf
   ```

3. **Check credentials are loaded:**
   ```bash
   ./sraf.sh run "test" --no-verify-ssl --max-steps 1
   ```

4. **Read the docs in order:**
   START → README.md → QUICKSTART.md → README_SRAF.md

5. **Use verify script for diagnostics:**
   ```bash
   ./verify.sh
   ```

---

## ✨ Summary

Everything is set up and documented. All files serve a specific purpose. Follow the "Getting Started Path" above to begin using SRAF with GigaChat immediately! 🚀
