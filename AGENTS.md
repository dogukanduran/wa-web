# AGENTS.md

Instructions for AI coding agents working on **Armageddon Recomp** (`/Users/0xbeef/armageddon`).

---

## 1. Copyright & Git Hygiene (Strict)

- `game/`, `original/`, `analysis/`, and `build/` are **git-ignored**.
- **NEVER** commit copyrighted *Worms Armageddon* binaries, assets, Ghidra listings, or lifted C output derived from `WA.exe`.
- Never bypass DRM/access controls or use pirated repacks; only use the user's legally owned DRM-free GOG.com v3.8.1 copy (`game/WA_GOG/WA.exe`, symlinked at `original/`).
- Keep all WA-specific configuration in `game.toml`, `globals.toml`, and clean-room shims/hooks.

---

## 2. Architecture (`veritr1x/recomp-kit`)

- `kit/` is the `veritr1x/recomp-kit` git submodule. Local Win32/MFC/DirectDraw/SDL shims currently live in `kit/` (see `STATUS.md` for the full list).
- Target binary: 32-bit x86 PE `WA.exe` (GOG v3.8.1, `SHA-256: 10d8172b5761bb5a1cb4ec219fdaca5015c0b86b6638c8dbcc8f5d0618c0a8b9`).
  - `image_base = 0x00400000`, `entry_point = 0x005d8b6c`, `.text = 0x00401000..0x006194d3`.
  - Extra translator `entry_points` in `game.toml`: `[0x005d8b6c, 0x005ddccf, 0x006060f4, 0x005c4c43]` (`0x005c4c43` = `_AfxCbtFilterHook`).
- GOG v3.8.1 does **not** use LEADTOOLS; it loads BMPs directly via `CreateFileA`/`ReadFile` (front-end sprites) and `LoadImageA(..., LR_CREATEDIBSECTION | LR_LOADFROMFILE)` (fonts, cursor, icons).
- Primary delivery target is **WebAssembly + WebGPU** (`build/cmake/web`, target `ArmageddonRecomp.js`), served via `kit/tools/web_launcher.py`. Use native `pop_headless` (`build/cmake/macos`) for fast local debugging and frame verification.

---

## 3. Build & Debugging Tips

- Always run `pop_headless` with a clean registry: `rm -f /tmp/wa_clean_reg.json && RECOMP_REGISTRY=/tmp/wa_clean_reg.json ./build/recomp/pop_headless`.
- Use `RECOMP_LOG=2` for full Win32 API call traces, and `RECOMP_WATCH=0xADDR` (with 10-frame backtrace in `kit/runtime/memory.cpp`) to catch exact memory writes.
- Emscripten link step (`ArmageddonRecomp.js`) may require writing a lockfile in Homebrew's Emscripten cache (`/opt/homebrew/Cellar/emscripten/.../cache/symbol_lists/`). Compile object files sandboxed first, then link if needed.
- Always check `STATUS.md` at the start of a new session for current progress and open issues.

---

## 4. Reverse Engineering (REA + Ghidra)

- Use the `rea` MCP server (with Ghidra configured via `GHIDRA_INSTALL_DIR`) or `build/recomp/gen/chunk_*.c` to inspect `WA.exe` entry points, MFC message handlers, DirectDraw/DirectInput setup, and indirect call targets.
- Document verified addresses and symbol names in `globals.toml`, `game.toml`, and `STATUS.md`.
