# Armageddon Recomp

Static x86 binary recompilation of **Worms Armageddon (`WA.exe`)** to **WebAssembly + WebGPU** (with a native headless verification harness) using [`veritr1x/recomp-kit`](https://github.com/veritr1x/recomp-kit).

> **Legal Notice:** This repository contains **zero** copyrighted *Worms Armageddon* binaries, assets, or lifted code. Users supply their own legally owned DRM-free **GOG.com** *Worms Armageddon* v3.8.1 installation in the git-ignored `game/WA_GOG/` (symlinked to `original/`) directory.

---

## Current Status

- **Static Recompilation:** Full `WA.exe` (GOG v3.8.1, `SHA-256: 10d8172b5761bb5a1cb4ec219fdaca5015c0b86b6638c8dbcc8f5d0618c0a8b9`) translated — **13,359 / 13,360 functions**, **537 / 537 jump tables**, **0 recovery errors**, **0 undeliverable calls**.
- **WebAssembly + WebGPU Build:** Boots cleanly in browser (`ArmageddonRecomp.wasm`), streams GOG assets from OPFS, plays the Team17 & Worms Armageddon intro sequence, and enters the full animated **Main Menu** at `640×480×32bpp`.
- See [STATUS.md](STATUS.md) for detailed progress, shim inventory, and next session TODOs.

---

## Architecture

- **`kit/`** — [`veritr1x/recomp-kit`](https://github.com/veritr1x/recomp-kit) git submodule providing x86-to-C translation (`tools/`), guest x86 runtime & Win32/MFC shims (`runtime/`), DirectDraw / DirectSound / DirectInput shims (`dx/`), SDL3 host (`host/`), and WebAssembly launcher/player (`web/`).
- **`game.toml` & `globals.toml`** — Identity, PE addresses (`image_base = 0x00400000`, `entry_point = 0x005d8b6c`), translator settings, and curated symbols for `WA.exe`.
- **`game/` & `original/`** *(git-ignored)* — User's local DRM-free GOG *Worms Armageddon* installation.
- **`analysis/` & `build/`** *(git-ignored)* — Ghidra disassembly listings, translated C output, and compiled headless / WebAssembly targets.

---

## Quick Start (Build & Run Web Version)

### Prerequisites
- **Ghidra** (`>= 12.2`) + **OpenJDK 21** (headless disassembly export)
- **CMake**, **Ninja**, **Clang**, **Python 3** (`.venv`)
- **Emscripten** (`emcc`) for WebAssembly builds

### Build WebAssembly & Serve
```bash
# 1. Build WebAssembly target
cmake --build build/cmake/web --target ArmageddonRecomp.js --parallel 8

# 2. Stage GOG assets for streaming and serve on http://localhost:8080/
.venv/bin/python kit/tools/web_launcher.py \
  --game-dir . \
  --out build/web/site \
  --web-build wa=build/web/recomp \
  --stream wa \
  --serve 8080
```

### Headless Verification (Native macOS)
```bash
cmake --build build/cmake/macos --target pop_headless --parallel 8
rm -f /tmp/wa_clean_reg.json
RECOMP_REGISTRY=/tmp/wa_clean_reg.json ./build/recomp/pop_headless
```
