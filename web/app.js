import { queryEnvironment, executablePath } from "./env.mjs?v=__RECOMP_BUILD__";
import { createCanvasDevice } from "./webgpu.mjs?v=__RECOMP_BUILD__";
import { initNetRelay } from "./net-relay.mjs?v=__RECOMP_BUILD__";

const worker = new Worker(new URL("./worker.js?v=__RECOMP_BUILD__", import.meta.url), { type: "module" });
let nextId = 1;
const pending = new Map();
let onProgress = null;
worker.onmessage = async ({ data }) => {
  if (data.progress) {
    onProgress && onProgress(data.progress);
    return;
  }
  if (data.decodeOgg) {
    const { id, samplesPerCh, sampleRate, channels, ogg } = data.decodeOgg;
    try {
      const ctx = new OfflineAudioContext(channels, Math.max(1, samplesPerCh), sampleRate);
      const audioBuf = await ctx.decodeAudioData(ogg.buffer.slice(ogg.byteOffset, ogg.byteOffset + ogg.byteLength));
      const chs = [];
      const transfers = [];
      for (let c = 0; c < channels; c++) {
        const f32 = audioBuf.getChannelData(Math.min(c, audioBuf.numberOfChannels - 1));
        const copy = new Float32Array(f32);
        chs.push(copy);
        transfers.push(copy.buffer);
      }
      worker.postMessage({ id, decodedOgg: { channels: chs, length: audioBuf.length } }, transfers);
    } catch (e) {
      worker.postMessage({ id, decodedError: e.message || String(e) });
    }
    return;
  }
  const p = pending.get(data.id);
  if (!p) return;
  pending.delete(data.id);
  data.error ? p.reject(new Error(data.error)) : p.resolve(data.result);
};
function call(cmd, args = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, cmd, ...args });
  });
}

const setupEl = document.getElementById("setup");
const stateEl = document.getElementById("state");
const detailEl = document.getElementById("detail");
const progressEl = document.getElementById("progress");
const progressBar = document.getElementById("progress-bar");
const progressText = document.getElementById("progress-text");
const messageEl = document.getElementById("message");
const playBtn = document.getElementById("play");
const canvas = document.getElementById("canvas");
const statusEl = document.getElementById("status");

let game = null;
const log = [];
window.recompLog = log;

function showMessage(text, bad = false) {
  messageEl.hidden = !text;
  messageEl.textContent = text || "";
  messageEl.classList.toggle("bad", bad);
}

function setStatus(text, backToSetup = false) {
  statusEl.textContent = text || "";
  if (backToSetup && text) {
    const btn = document.createElement("button");
    btn.textContent = "Back";
    btn.style.marginTop = "12px";
    btn.onclick = () => location.reload();
    statusEl.append(document.createElement("br"), btn);
  }
  statusEl.hidden = !text;
}

function humanBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function setupFullscreenBanner() {
  const banner = document.getElementById("fs-banner");
  const textEl = document.getElementById("fs-text");
  const toggleBtn = document.getElementById("fs-toggle");
  const dismissBtn = document.getElementById("fs-dismiss");
  if (!banner || !toggleBtn || !document.fullscreenEnabled) return;
  let compact = false;
  const nativeW = Math.round(window.screen.width * (window.devicePixelRatio || 1));
  const nativeH = Math.round(window.screen.height * (window.devicePixelRatio || 1));
  if (textEl && nativeW >= 640 && nativeH >= 480)
    textEl.textContent = `Tip: Play in Fullscreen for native ${nativeW}×${nativeH} match resolution (Options → Graphics).`;
  const sync = () => {
    const isFs = !!document.fullscreenElement;
    banner.hidden = isFs;
    banner.classList.toggle("compact", compact && !isFs);
    toggleBtn.textContent = isFs ? "Exit Fullscreen" : (compact ? "⛶ Fullscreen" : "Enter Fullscreen");
  };
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {}
    canvas.focus();
  };
  toggleBtn.addEventListener("click", toggleFullscreen);
  dismissBtn?.addEventListener("click", () => {
    compact = true;
    sync();
    canvas.focus();
  });
  document.addEventListener("fullscreenchange", () => {
    sync();
    canvas.focus();
  });
  window.addEventListener("keydown", (e) => {
    if ((e.altKey && e.key === "Enter") || e.key === "F11") {
      e.preventDefault();
      toggleFullscreen();
    }
  });
  sync();
}

async function launchGame() {
  if (!crossOriginIsolated) throw new Error("Page must be served with COOP/COEP headers.");
  if (!navigator.gpu) throw new Error("This browser does not support WebGPU.");

  setupEl.hidden = true;
  canvas.hidden = false;
  setStatus("Initializing WebGPU…");

  const device = await createCanvasDevice(canvas);
  device.lost.then((info) => setStatus(`Graphics device lost: ${info.message}`, true));
  device.addEventListener("uncapturederror", (e) => console.error("WebGPU:", e.error.message));

  const record = (text, warning) => {
    log.push(text);
    if (log.length > 2000) log.shift();
    (warning ? console.warn : console.log)(text);
  };

  const Module = {
    canvas,
    preinitializedWebGPUDevice: device,
    noInitialRun: true,
    locateFile: (path, prefix) => `${prefix}${path}?v=__RECOMP_BUILD__`,
    print: (t) => record(t, false),
    printErr: (t) => record(t, true),
    setStatus: (t) => setStatus(t || ""),
    onAbort: (what) => setStatus(`${game.title} has crashed: ${what}`, true),
    onGameExit(code) {
      if (code === 0) {
        location.reload();
        return;
      }
      const last = [...log].reverse().find((t) => /failed|could not|unavailable|missing|error|fatal/i.test(t) && !/run record/.test(t));
      setStatus(`${game.title} exited with code ${code}${last ? `: ${last}` : "."}`, true);
    },
    preRun: [() => {
      Module.ENV.RECOMP_PROFILE_DIR = `/opfs/${game.id}/profile`;
      Module.ENV.SDL_TOUCH_MOUSE_EVENTS = "0";
      Object.assign(Module.ENV, queryEnvironment(location.search));
    }],
    onRuntimeInitialized() {
      setStatus("");
      setupFullscreenBanner();
      canvas.addEventListener("pointerdown", () => {
        if (window._recompWantPointerLock && document.pointerLockElement !== canvas && canvas.requestPointerLock) {
          Promise.resolve(canvas.requestPointerLock()).catch(() => {});
        }
      });
      canvas.focus();
      initNetRelay(Module);
      Module.callMain([`--exe=/opfs/${game.id}/game/${executablePath(game)}`]);
    },
  };
  window.Module = Module;
  setStatus("Loading WebAssembly…");
  const script = document.createElement("script");
  script.src = `${game.appName}.js?v=__RECOMP_BUILD__`;
  script.onerror = () => setStatus("ArmageddonRecomp.js is missing.", true);
  document.body.append(script);
}

async function init() {
  game = await fetch("./game.json?v=__RECOMP_BUILD__").then((r) => r.json());
  playBtn.onclick = () => launchGame().catch((e) => setStatus(e.message, true));
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("click", () => canvas.focus());

  stateEl.textContent = "Downloading game files…";
  stateEl.className = "state warn";
  progressEl.hidden = false;
  progressBar.value = 0;
  onProgress = (p) => {
    progressBar.value = p.bytesTotal ? p.bytesDone / p.bytesTotal : 0;
    progressText.textContent = `${p.filesDone} / ${p.filesTotal} files — ${p.current}`;
  };

  try {
    const res = await call("ensure", { game });
    progressEl.hidden = true;
    stateEl.textContent = "Ready";
    stateEl.className = "state ready";
    detailEl.textContent = `${res.files} files (${humanBytes(res.bytes)}) ready.`;
    playBtn.disabled = false;
  } catch (e) {
    progressEl.hidden = true;
    stateEl.textContent = "Download failed";
    stateEl.className = "state bad";
    showMessage(e.message, true);
  }
}

init().catch((e) => showMessage(e.message, true));
