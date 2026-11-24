# Contributing to wa-web

Thanks for helping get Worms Armageddon running in the browser. This guide covers
the legal rules, setting up a local build, how the `kit/` submodule works, and what
a good pull request looks like.

## Ground rules (read first)

- **Never commit game content.** No `WA.exe`, DLLs, BMP/PAL/WAV/IMG files,
  screenshots or frame dumps of the game, Ghidra listings, or translated C from
  `build/recomp/gen/`. `game/`, `original/`, `analysis/` and `build/` are
  git-ignored. `python3 tools/check_publication.py` checks what you're about to
  commit, and CI runs it on every pull request.
- **Use your own legal copy.** The supported executable is the DRM-free GOG.com
  v3.8.1 `WA.exe`, SHA-256
  `10d8172b5761bb5a1cb4ec219fdaca5015c0b86b6638c8dbcc8f5d0618c0a8b9`. No
  pirated repacks, no DRM bypassing.
- **Don't attach game files to issues.** Logs, addresses and descriptions are fine.
  If a screenshot is really needed to show a rendering bug, keep it small and link
  it rather than committing it.

## Prerequisites

| Tool | Version | Used for |
|------|---------|----------|
| Python | 3.9+ | build scripts (`.venv`) |
| CMake + Ninja | recent | native and web builds |
| Clang (Xcode CLT on macOS) | recent | native headless build |
| Ghidra | **12.1.3** | exporting the `WA.exe` listings |
| OpenJDK | 21 | running Ghidra headless |
| Emscripten SDK | recent | WebAssembly build |

macOS is the main development platform. Linux should work for the headless and web
builds but gets less testing.

## First-time setup

```bash
# 1. Clone with the kit submodule
git clone --recurse-submodules https://github.com/dogukanduran/wa-web
cd wa-web

# 2. Python environment
python3 -m venv .venv
.venv/bin/pip install -r kit/requirements-dev.txt

# 3. Link your GOG install (checks the SHA-256, creates original/ -> your install)
.venv/bin/python kit/tools/setup.py --game-dir "$PWD" \
  --install "/path/to/GOG Games/Worms Armageddon" --link-only

# 4. Export Ghidra listings into analysis/WA.exe (takes a while: full auto-analysis)
GHIDRA_HOME=/path/to/ghidra_12.1.3_PUBLIC tools/export_listings.sh

# 5. Translate WA.exe and build the native headless harness
.venv/bin/python tools/build.py --target headless --regenerate
```

You only need steps 4 and 5 with `--regenerate` again after `game.toml` /
`globals.toml` changes, or when the translator in `kit/tools/recomp/` changes.

## Building and running

**Native headless** (fastest loop, deterministic frames):

```bash
.venv/bin/python tools/build.py --target headless
rm -f /tmp/wa_clean_reg.json
RECOMP_REGISTRY=/tmp/wa_clean_reg.json ./build/recomp/pop_headless
```

Useful environment variables:

- `RECOMP_LOG=2` – trace every Win32 API call.
- `RECOMP_WATCH=0xADDR` – log writes to a guest address with a 10-frame backtrace.
- `RECOMP_CLICK_AT="frame:x:y,..."` – click at given frames, to script trips
  through the menus.

Always start from a clean registry file, or settings from earlier runs will change
the behavior.

**WebAssembly + WebGPU** (the main target):

```bash
source /path/to/emsdk/emsdk_env.sh
.venv/bin/python tools/build.py --target web

.venv/bin/python kit/tools/web_launcher.py --game-dir . --out build/web/site \
  --web-build wa=build/web/recomp --stream wa --serve 8080
# open http://localhost:8080/
```

Once the build directory is configured, incremental rebuilds can run directly:
`cmake --build build/cmake/web --target ArmageddonRecomp.js --parallel 8`.

## Where changes go

| You're changing… | Where |
|------------------|-------|
| Addresses, symbols, translator settings for WA | `game.toml`, `globals.toml` |
| WA-specific behavior (hooks keyed to WA addresses) | `[hooks]` in `game.toml`; a `mods/` directory here if needed |
| Win32 / MFC / DirectX / host / web player shims | `kit/` (see below) |
| Build helpers, Ghidra scripts | `tools/` |
| Findings, verified addresses, progress | `STATUS.md` |

Kit code must stay game-agnostic. Nothing under `kit/runtime/`, `kit/dx/`,
`kit/host/` or `kit/platform/` should name Worms Armageddon or hard-code WA
addresses. Use `[hooks]` and the generated `RECOMP_HOOK_*` macros instead.

### Working on `kit/`

`kit/` is a submodule tracking the `wa-web` branch of
[`dogukanduran/recomp-kit`](https://github.com/dogukanduran/recomp-kit), a fork of
[`veritr1x/recomp-kit`](https://github.com/veritr1x/recomp-kit). A kit change takes
two pull requests:

1. **Kit PR:** fork `dogukanduran/recomp-kit`, branch off `wa-web`, commit there, and
   open a PR against `dogukanduran/recomp-kit:wa-web`.
2. **wa-web PR:** in this repo, point `kit` at your kit commit
   (`cd kit && git checkout <commit> && cd .. && git add kit`) and open a PR that
   links the kit PR. Once the kit PR is merged, the submodule pointer is updated to
   the merged commit.

Generic fixes that would help any game built with the kit (a missing Win32 API, a
DirectDraw bug) are also good candidates to send upstream to `veritr1x/recomp-kit`.
Follow its `CONTRIBUTING.md`.

Kit tests run without game files: `cd kit && ../.venv/bin/python tools/test.py`.

## Reverse-engineering notes

- Use Ghidra (or the `rea` MCP server) on your local listings, or read the
  generated `build/recomp/gen/chunk_*.c`, to find entry points, MFC message
  handlers and indirect call targets.
- Write verified addresses into `globals.toml` / `game.toml` and summarize them in
  `STATUS.md` §3. Addresses and short descriptions are fine to commit; pasted
  disassembly or decompiled code is not.

## Pull request checklist

- [ ] `python3 tools/check_publication.py` passes.
- [ ] The headless build boots to the state your change is about. Say how far you
      got (intro, main menu, a given dialog, in-game).
- [ ] For web-facing changes: tested in the browser. Name the browser and OS.
- [ ] `STATUS.md` updated if you fixed a listed TODO, found new addresses, or added
      shims.
- [ ] Kit changes have a matching kit PR (see above).

Small, focused PRs get reviewed fastest. One fix or one feature per PR.

## License

By contributing you agree that your contributions to this repository are licensed
under the [MIT License](LICENSE). Contributions to `kit/` fall under that
project's license. See [NOTICE](NOTICE).
