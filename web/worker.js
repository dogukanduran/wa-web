// worker.js - Streams single assets.pak into OPFS (/opfs/wa/game/...), decoding RCOG Opus tracks to RCWV.
const IMA_INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8];
const IMA_STEP_TABLE = [
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
  50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
  337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
  2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
  15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
];

function imaEncodeSample(sample, state) {
  let pred = state.pred;
  let index = state.index;
  let step = IMA_STEP_TABLE[index];
  let diff = sample - pred;
  let nibble = 0;
  if (diff < 0) { nibble = 8; diff = -diff; }
  let delta = step >> 3;
  if (diff >= step) { nibble |= 4; diff -= step; delta += step; }
  step >>= 1;
  if (diff >= step) { nibble |= 2; diff -= step; delta += step; }
  step >>= 1;
  if (diff >= step) { nibble |= 1; delta += step; }
  pred += (nibble & 8) ? -delta : delta;
  if (pred > 32767) pred = 32767;
  else if (pred < -32768) pred = -32768;
  index += IMA_INDEX_TABLE[nibble];
  if (index < 0) index = 0;
  else if (index > 88) index = 88;
  state.pred = pred;
  state.index = index;
  return nibble;
}

function encodeRcwvFromFloat(channelArrays, length, sampleRate) {
  const ch = channelArrays.length;
  const samplesPerCh = length;
  const nibbleBytes = ch === 1 ? Math.floor(samplesPerCh / 2) : (samplesPerCh - 1);
  const out = new Uint8Array(16 + ch * 4 + nibbleBytes);
  const ov = new DataView(out.buffer);
  out.set([0x52, 0x43, 0x57, 0x56], 0); // "RCWV"
  ov.setUint32(4, samplesPerCh, true);
  ov.setUint32(8, sampleRate, true);
  ov.setUint16(12, ch, true);
  ov.setUint16(14, 0, true);

  const toI16 = (v) => Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
  const states = [];
  for (let c = 0; c < ch; c++) {
    const s0 = toI16(channelArrays[c][0] || 0);
    states.push({ pred: s0, index: 0 });
    ov.setInt16(16 + c * 4, s0, true);
    out[16 + c * 4 + 2] = 0;
    out[16 + c * 4 + 3] = 0;
  }

  let dst = 16 + ch * 4;
  if (ch === 1) {
    const a = channelArrays[0];
    for (let s = 1; s < samplesPerCh; s += 2) {
      const n0 = imaEncodeSample(toI16(a[s]), states[0]);
      const n1 = (s + 1 < samplesPerCh) ? imaEncodeSample(toI16(a[s + 1]), states[0]) : 0;
      out[dst++] = n0 | (n1 << 4);
    }
  } else {
    const a = channelArrays[0], b = channelArrays[1];
    for (let s = 1; s < samplesPerCh; s++) {
      const n0 = imaEncodeSample(toI16(a[s]), states[0]);
      const n1 = imaEncodeSample(toI16(b[s]), states[1]);
      out[dst++] = n0 | (n1 << 4);
    }
  }
  return out;
}

const oggPending = new Map();
let nextOggId = 1;
function decodeOggOnMain(samplesPerCh, sampleRate, channels, ogg) {
  const id = nextOggId++;
  return new Promise((resolve, reject) => {
    oggPending.set(id, { resolve, reject });
    const copy = ogg.slice();
    self.postMessage({ decodeOgg: { id, samplesPerCh, sampleRate, channels, ogg: copy } }, [copy.buffer]);
  });
}

async function dirFor(path, create = true) {
  let dir = await navigator.storage.getDirectory();
  for (const part of path.split("/").filter(Boolean))
    dir = await dir.getDirectoryHandle(part, { create });
  return dir;
}

async function fileFor(root, rel, create) {
  const parts = rel.split("/");
  const name = parts.pop();
  let dir = root;
  for (const p of parts) dir = await dir.getDirectoryHandle(p, { create });
  return dir.getFileHandle(name, { create });
}

async function readText(root, rel) {
  try {
    return await (await (await fileFor(root, rel, false)).getFile()).text();
  } catch {
    return null;
  }
}

async function writeBytes(root, rel, bytes) {
  const handle = await fileFor(root, rel, true);
  const access = await handle.createSyncAccessHandle();
  access.truncate(0);
  const n = access.write(bytes, { at: 0 });
  access.flush();
  access.close();
  if (n !== bytes.length) throw new DOMException("short write", "QuotaExceededError");
}

function humanBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

class StreamBuffer {
  constructor(reader, totalBytes, onBytes) {
    this.reader = reader;
    this.totalBytes = totalBytes;
    this.onBytes = onBytes;
    this.buf = new Uint8Array(0);
    this.pos = 0;
    this.done = false;
    this.received = 0;
  }
  async nextChunk() {
    while (this.pos === this.buf.length) {
      if (this.done) throw new Error("Unexpected end of assets.pak");
      const { done, value } = await this.reader.read();
      if (done) { this.done = true; continue; }
      this.received += value.length;
      this.onBytes(this.received);
      this.buf = value;
      this.pos = 0;
    }
  }
  async readExact(n) {
    if (n === 0) return this.buf.subarray(this.pos, this.pos);
    await this.nextChunk();
    if (this.buf.length - this.pos >= n) {
      const slice = this.buf.subarray(this.pos, this.pos + n);
      this.pos += n;
      return slice;
    }

    // Assemble split records once. Growing a buffer for each network chunk
    // repeatedly copied the entire partial asset, making large reads quadratic.
    const result = new Uint8Array(n);
    let written = 0;
    while (written < n) {
      await this.nextChunk();
      const take = Math.min(n - written, this.buf.length - this.pos);
      result.set(this.buf.subarray(this.pos, this.pos + take), written);
      this.pos += take;
      written += take;
    }
    return result;
  }
}

async function seedRegistryDefaults(gameId, defaults) {
  if (!defaults || typeof defaults !== "object") return;
  const profile = await dirFor(`${gameId}/profile`);
  let registry = {};
  const raw = await readText(profile, "registry.json");
  if (raw) {
    try {
      registry = JSON.parse(raw);
    } catch {}
  }
  const sep = "\\";
  let dirty = false;
  for (const [fullKey, values] of Object.entries(defaults)) {
    const parts = fullKey.split(sep).filter(Boolean);
    for (let i = 1; i <= parts.length; i++) {
      const k = parts.slice(0, i).join(sep);
      if (!registry[k] || typeof registry[k] !== "object") {
        registry[k] = {};
        dirty = true;
      }
    }
    const target = registry[parts.join(sep)];
    for (const [name, entry] of Object.entries(values)) {
      if (!(name in target) || (entry.data !== "" && target[name]?.data === "")) {
        target[name] = entry;
        dirty = true;
      }
    }
  }
  if (dirty) {
    await writeBytes(profile, "registry.json", new TextEncoder().encode(JSON.stringify(registry, null, 2)));
  }
}

const handlers = {
  async ensure({ game }) {
    await seedRegistryDefaults(game.id, game.registryDefaults);
    const root = await dirFor(`${game.id}/game`);
    const currentStamp = await readText(root, ".stamp");
    if (currentStamp?.trim() === game.dataStamp) {
      const countRaw = await readText(root, ".count");
      const bytesRaw = await readText(root, ".bytes");
      return {
        files: Number(countRaw) || 4389,
        bytes: Number(bytesRaw) || 125000000,
        cached: true,
      };
    }

    const res = await fetch(`./assets.pak?v=${encodeURIComponent(game.dataStamp)}`);
    if (!res.ok) throw new Error(`Failed to fetch assets.pak (HTTP ${res.status})`);
    const totalBytes = Number(res.headers.get("Content-Length")) || 0;
    let filesDone = 0;
    let totalFiles = 0;
    let lastReport = 0;

    const sb = new StreamBuffer(res.body.getReader(), totalBytes, (received) => {
      const now = performance.now();
      if (now - lastReport > 100 || received === totalBytes) {
        lastReport = now;
        self.postMessage({
          progress: {
            filesDone,
            filesTotal: totalFiles,
            bytesDone: received,
            bytesTotal: totalBytes,
            current: `${humanBytes(received)} / ${humanBytes(totalBytes)}`,
          },
        });
      }
    });

    const hdr = await sb.readExact(24);
    const dv = new DataView(hdr.buffer, hdr.byteOffset, hdr.byteLength);
    const magic = String.fromCharCode(hdr[0], hdr[1], hdr[2], hdr[3]);
    if (magic !== "RPAK") throw new Error("Invalid assets.pak header");
    totalFiles = dv.getUint32(4, true);
    const stamp = new TextDecoder().decode(hdr.subarray(8, 24));
    const dec = new TextDecoder();

    for (let i = 0; i < totalFiles; i++) {
      const lenBytes = await sb.readExact(2);
      const pathLen = lenBytes[0] | (lenBytes[1] << 8);
      const rel = dec.decode(await sb.readExact(pathLen));
      const szBytes = await sb.readExact(4);
      const sz = new DataView(szBytes.buffer, szBytes.byteOffset, 4).getUint32(0, true);
      let data = await sb.readExact(sz);

      if (sz >= 16 && data[0] === 0x52 && data[1] === 0x43 && data[2] === 0x4f && data[3] === 0x47) { // "RCOG"
        const hv = new DataView(data.buffer, data.byteOffset, 16);
        const samplesPerCh = hv.getUint32(4, true);
        const sampleRate = hv.getUint32(8, true);
        const channels = hv.getUint16(12, true);
        const decoded = await decodeOggOnMain(samplesPerCh, sampleRate, channels, data.subarray(16));
        data = encodeRcwvFromFloat(decoded.channels, decoded.length, sampleRate);
      }

      await writeBytes(root, rel, data);
      filesDone++;
    }

    const enc = new TextEncoder();
    await writeBytes(root, ".count", enc.encode(String(totalFiles)));
    await writeBytes(root, ".bytes", enc.encode(String(totalBytes)));
    await writeBytes(root, ".stamp", enc.encode(stamp));
    return { files: totalFiles, bytes: totalBytes, cached: false };
  },
};

self.onmessage = async ({ data }) => {
  if (data.decodedOgg || data.decodedError) {
    const p = oggPending.get(data.id);
    if (!p) return;
    oggPending.delete(data.id);
    data.decodedError ? p.reject(new Error(data.decodedError)) : p.resolve(data.decodedOgg);
    return;
  }
  const { id, cmd, ...args } = data;
  try {
    self.postMessage({ id, result: await handlers[cmd](args) });
  } catch (e) {
    self.postMessage({ id, error: e.message || String(e) });
  }
};
