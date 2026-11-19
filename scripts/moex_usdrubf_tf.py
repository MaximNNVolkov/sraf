"""Compact structured morning collector for USDRUBF."""
import os
import sys
from pathlib import Path

PROJECT_PYTHON = "/home/user1/moex/.venv/bin/python"
if __name__ == "__main__" and os.path.realpath(sys.executable) != os.path.realpath(PROJECT_PYTHON):
    os.execv(PROJECT_PYTHON, [PROJECT_PYTHON, __file__, *sys.argv[1:]])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moex_morning_structured import main

raise SystemExit(main("USDRUBF"))
