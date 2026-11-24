#!/usr/bin/env python3
"""Fail if a tracked file looks like copyrighted game content or WA.exe-derived output.

Run before pushing (CI runs it too):

    python3 tools/check_publication.py

It checks what git tracks, not the working tree, so ignored local game files
are fine. Submodule entries (kit/) are skipped; the kit has its own checks.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that only ever hold the user's game install or derived output.
FORBIDDEN_DIRS = ("game/", "original/", "analysis/", "build/", "410/")

# Game executables/data, rendered frames, and disassembly/translation output.
FORBIDDEN_SUFFIXES = {
    ".exe", ".dll", ".drv", ".bmp", ".pal", ".wav", ".img", ".dir", ".spr", ".pcx",
    ".wad", ".lev", ".wgt", ".wsc", ".wam", ".wagame", ".ppm", ".pdf", ".rtf",
    ".asm", ".tsv", ".gpr", ".rep", ".hashdb",
}
FORBIDDEN_NAMES = {"wa.exe", "steam_api.dll", "goggame-1462173886.info"}
MAX_BYTES = 1_000_000


def tracked_files():
    """Return (path, mode) for every index entry, so submodule gitlinks can be skipped."""
    out = subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=ROOT, text=True)
    for entry in filter(None, out.split("\0")):
        meta, path = entry.split("\t", 1)
        yield path, meta.split()[0]


def problems():
    """Yield one message per tracked file that must not be published."""
    for path, mode in tracked_files():
        if mode == "160000":  # submodule
            continue
        lower = path.lower()
        name = lower.rsplit("/", 1)[-1]
        if lower.startswith(FORBIDDEN_DIRS):
            yield f"{path}: lives in a git-ignored game/analysis/build directory"
        elif Path(lower).suffix in FORBIDDEN_SUFFIXES:
            yield f"{path}: file type is game data, a rendered frame, or a disassembly listing"
        elif name in FORBIDDEN_NAMES:
            yield f"{path}: game installation file"
        elif name.startswith("chunk_") and name.endswith(".c"):
            yield f"{path}: looks like translated WA.exe code (build/recomp/gen)"
        elif (ROOT / path).is_file() and (ROOT / path).stat().st_size > MAX_BYTES:
            yield f"{path}: larger than {MAX_BYTES} bytes; game assets and outputs stay local"


def main():
    found = list(problems())
    for message in found:
        print("not publishable:", message, file=sys.stderr)
    if found:
        print("Remove these from the index (git rm --cached) and see CONTRIBUTING.md.", file=sys.stderr)
        return 1
    print("Publication check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
