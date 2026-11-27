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

.venv/bin/python -u tools/serve_web.py --serve 8088
# open http://localhost:8088/
```

The current milestone uses the offline WA page and its existing private asset
pack. It never initializes the WebSocket relay. Networking sources are preserved
for a later milestone. Game execution remains client-side WebAssembly.

`node --test tests/webgpu.test.cjs` covers presentation failure, alternate-adapter
selection, and cleanup. Actual browser gameplay verification is still required.

Use `tools/build.py --target web --jobs 3` for cached incremental builds.
Only request `--regenerate` when translation inputs changed.

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

### Offline frame-time profiling

Run from a graphical desktop shell on Linux, with the local offline server
running and your own `.WAgame` replay. Use an isolated profile under `build/`;
its asset installation and replay copy stay in private OPFS storage. Install the
optional collector dependency once:

```sh
npm install --prefix build/browser-tools playwright-core@1.56.1
node tools/profile_browser.cjs --replay="/absolute/path/to/owned.WAgame" \
  --out=build/evidence/wasm-baseline --x11 <<'JSON'
{"op":"play"}
{"op":"await-game","warmupMs":2000}
{"op":"measure","seconds":30}
{"op":"close"}
JSON
python3 tools/summarize_frame_timings.py build/evidence/wasm-baseline/frames.csv
```

`--chrome=` and `--python=` select executables. The default Chrome path is
`/usr/bin/google-chrome`; the Python helper uses the local venv or `python3`.
The X11 flag enables the common window-update collector used for the comparison;
profile the actual Wayland launch separately before attributing differences to
its compositor. The browser PID printed by the tool identifies the Chrome
process group, not the Node controller. A newly installed profile must finish
asset setup before Play. `await-game` rejects blank runs and waits for the
recorded-game HUD before the warmup.

For a separate diagnostic capture, add `--diagnostic` and send `trace-start`
before Play, then `trace-stop` after `measure`, before `close`. It instruments
WebGPU calls and saves a CDP CPU/GC trace. Do not mix its timing results with
unprofiled baselines. GPU byte counts are source-view sizes, not bus bandwidth;
resource weak references are not a VRAM census. Counter snapshots can include
the save tail; use their actual timestamp span.

For the original executable and native build, use the same recorded input,
resolution, adapter and visible-game interval in isolated copied game/profile
directories. On Linux the original executable requires Wine; report that layer.
A fresh registry fixture needs all parent keys for its Options values. Check
both the guest display-mode log and actual drawable dimensions: a missing parent
key can silently select desktop resolution. CPU/GPU and X11 collectors can also
be run independently:

```sh
python3 tools/profile_telemetry.py --pgid GAME_PROCESS_GROUP --seconds 30 \
  --out build/evidence/native-telemetry.jsonl
python3 tools/profile_window_frames.py --window X11_WINDOW_ID --seconds 30 \
  --out build/evidence/native-window.json
```

For Wine's GL drawable use `--child` when its client window owns a child
rendering window. `--pgid` can discover an X11 window instead of `--window`.
Telemetry can select `--amd-device=/sys/class/drm/cardN/device` or add `--nvidia`.
Window updates, new completions, sealed-to-completion delay, RAF and long tasks
are distinct metrics; none alone measures hardware scanout or input latency.
Keep raw captures, screenshots and game inputs under ignored `build/`, and record
only non-copyrighted aggregate results in milestone documentation. The current
baseline and remaining hypotheses are documented in [STATUS.md](STATUS.md).
