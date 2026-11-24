#!/usr/bin/env python3
"""Thin wrapper delegating to kit/tools/build.py with --game-dir set to this repo."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_BUILD = ROOT / "kit" / "tools" / "build.py"

raise SystemExit(
    subprocess.call([sys.executable, str(KIT_BUILD), "--game-dir", str(ROOT), *sys.argv[1:]])
)
