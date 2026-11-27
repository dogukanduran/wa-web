#!/usr/bin/env python3
"""Pack WA assets into assets.pak (RCWV + RCDF), gzip static web assets, and serve with COOP/COEP."""

import argparse
import fnmatch
import functools
import gzip
import hashlib
import http.server
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit" / "tools"))
import game_config  # noqa: E402

BUILD_STAMP = "__RECOMP_BUILD__"
CSP = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; object-src 'none'; base-uri 'none'"
RECOMP_FILES = (
    "ArmageddonRecomp.js",
    "ArmageddonRecomp.wasm",
    "ArmageddonRecomp.data",
    "audio-worklet.js",
    "env.mjs",
    "net-relay.mjs",
)

IMA_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)
IMA_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)


def try_encode_rcwv(raw: bytes) -> bytes | None:
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    pos = 12
    fmt = None
    data_off = -1
    data_size = 0
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        csz = struct.unpack_from("<I", raw, pos + 4)[0]
        body = pos + 8
        if body + csz > len(raw):
            break
        if cid == b"fmt " and csz >= 16:
            afmt, ch, rate, _, _, bps = struct.unpack_from("<HHIIHH", raw, body)
            fmt = (afmt, ch, rate, bps)
        elif cid == b"data":
            data_off = body
            data_size = csz
            break
        pos = body + csz + (csz & 1)
    if not fmt or data_off < 0:
        return None
    afmt, ch, rate, bps = fmt
    if afmt != 1 or bps != 16 or ch not in (1, 2):
        return None
    samples_per_ch = data_size // (ch * 2)
    if samples_per_ch < 256:
        return None

    samples = memoryview(raw)[data_off:data_off + samples_per_ch * ch * 2].cast("h")
    idx_tbl = IMA_INDEX_TABLE
    stp_tbl = IMA_STEP_TABLE

    def enc_one(sample: int, pred: int, index: int) -> tuple[int, int, int]:
        step = stp_tbl[index]
        diff = sample - pred
        nib = 0
        if diff < 0:
            nib = 8
            diff = -diff
        delta = step >> 3
        if diff >= step:
            nib |= 4
            diff -= step
            delta += step
        step >>= 1
        if diff >= step:
            nib |= 2
            diff -= step
            delta += step
        step >>= 1
        if diff >= step:
            nib |= 1
            delta += step
        pred += -delta if (nib & 8) else delta
        if pred > 32767:
            pred = 32767
        elif pred < -32768:
            pred = -32768
        index += idx_tbl[nib]
        if index < 0:
            index = 0
        elif index > 88:
            index = 88
        return nib, pred, index

    header = bytearray(16 + ch * 4)
    struct.pack_into("<4sIIHH", header, 0, b"RCWV", samples_per_ch, rate, ch, 0)
    if ch == 1:
        p0, i0 = int(samples[0]), 0
        struct.pack_into("<hBB", header, 16, p0, 0, 0)
        out = bytearray(samples_per_ch // 2)
        dst = 0
        s = 1
        while s < samples_per_ch:
            n0, p0, i0 = enc_one(int(samples[s]), p0, i0)
            s += 1
            if s < samples_per_ch:
                n1, p0, i0 = enc_one(int(samples[s]), p0, i0)
                s += 1
            else:
                n1 = 0
            out[dst] = n0 | (n1 << 4)
            dst += 1
    else:
        p0, i0 = int(samples[0]), 0
        p1, i1 = int(samples[1]), 0
        struct.pack_into("<hBBhBB", header, 16, p0, 0, 0, p1, 0, 0)
        out = bytearray(samples_per_ch - 1)
        for s in range(1, samples_per_ch):
            n0, p0, i0 = enc_one(int(samples[s * 2]), p0, i0)
            n1, p1, i1 = enc_one(int(samples[s * 2 + 1]), p1, i1)
            out[s - 1] = n0 | (n1 << 4)

    packed = bytes(header) + bytes(out)
    return packed if len(packed) < len(raw) else None


def try_encode_rcog(raw: bytes) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or len(raw) < 512 * 1024 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    pos = 12
    fmt = None
    data_size = 0
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        csz = struct.unpack_from("<I", raw, pos + 4)[0]
        body = pos + 8
        if body + csz > len(raw):
            break
        if cid == b"fmt " and csz >= 16:
            afmt, ch, rate, _, _, bps = struct.unpack_from("<HHIIHH", raw, body)
            fmt = (afmt, ch, rate, bps)
        elif cid == b"data":
            data_size = csz
            break
        pos = body + csz + (csz & 1)
    if not fmt or data_size <= 0:
        return None
    afmt, ch, rate, bps = fmt
    if afmt != 1 or bps != 16 or ch not in (1, 2):
        return None
    samples_per_ch = data_size // (ch * 2)
    with tempfile.TemporaryDirectory() as td:
        in_p = Path(td) / "in.wav"
        out_p = Path(td) / "out.ogg"
        in_p.write_bytes(raw)
        rc = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(in_p), "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", str(out_p)],
            capture_output=True,
        )
        if rc.returncode != 0 or not out_p.exists():
            return None
        ogg = out_p.read_bytes()
    hdr = struct.pack("<4sIIHH", b"RCOG", samples_per_ch, rate, ch, 0)
    packed = hdr + ogg
    return packed if len(packed) < len(raw) else None


def pack_bytes(rel_lower: str, raw: bytes) -> bytes:
    if rel_lower.endswith(".exe") or rel_lower.endswith(".dll") or len(raw) < 256:
        return raw
    if rel_lower.endswith(".wav"):
        og_packed = try_encode_rcog(raw)
        if og_packed is not None:
            return og_packed
        wav_packed = try_encode_rcwv(raw)
        if wav_packed is not None:
            return wav_packed
    comp = zlib.compress(raw, 6)
    if len(comp) + 8 < len(raw):
        return struct.pack("<4sI", b"RCDF", len(raw)) + comp
    return raw


def is_excluded(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    top, name = parts[0].lower(), parts[-1].lower()
    if name in (".ds_store", "thumbs.db", ".stamp", ".manifest.json") or name.startswith("._"):
        return True
    return any(fnmatch.fnmatch(top, p.lower()) or fnmatch.fnmatch(name, p.lower()) for p in patterns)


def write_pak_from_entries(pak_path: Path, entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in entries:
        h.update(f"{rel}:{len(data)}\n".encode("utf-8"))
    stamp = h.hexdigest()[:16]
    with pak_path.open("wb") as fp:
        fp.write(struct.pack("<4sI16s", b"RPAK", len(entries), stamp.encode("ascii")))
        for rel, data in entries:
            pb = rel.encode("utf-8")
            fp.write(struct.pack("<H", len(pb)))
            fp.write(pb)
            fp.write(struct.pack("<I", len(data)))
            fp.write(data)
    return stamp


def pack_game_data(cfg: dict, out_dir: Path, force: bool) -> str:
    pak_path = out_dir / "assets.pak"
    old_data_dir = out_dir / "data"

    if pak_path.exists() and not force:
        with pak_path.open("rb") as fp:
            hdr = fp.read(24)
        if len(hdr) == 24 and hdr[:4] == b"RPAK":
            _, count, stamp_b = struct.unpack("<4sI16s", hdr)
            print(f"Using cached {pak_path.name} ({count} files, {pak_path.stat().st_size / 1e6:.1f} MB)")
            return stamp_b.decode("ascii")

    # Fast path: reuse already-packed data/ directory if present and not forcing repack
    manifest_path = old_data_dir / "manifest.json"
    if manifest_path.exists() and not force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = [(f["path"], (old_data_dir / f["path"]).read_bytes()) for f in manifest["files"]]
        stamp = write_pak_from_entries(pak_path, entries)
        shutil.rmtree(old_data_dir)
        print(f"Bundled {len(entries)} cached files into {pak_path.name} ({pak_path.stat().st_size / 1e6:.1f} MB)")
        return stamp

    src_root = cfg["developer_game_dir_path"]
    excludes = cfg["bundle"]["exclude"]
    entries = []
    orig_total = 0
    packed_total = 0

    for p in sorted(q for q in src_root.rglob("*") if q.is_file()):
        rel = p.relative_to(src_root).as_posix()
        if is_excluded(rel, excludes):
            continue
        raw = p.read_bytes()
        orig_total += len(raw)
        packed = pack_bytes(rel.lower(), raw)
        packed_total += len(packed)
        entries.append((rel, packed))

    stamp = write_pak_from_entries(pak_path, entries)
    if old_data_dir.exists():
        shutil.rmtree(old_data_dir)
    print(f"Packed {len(entries)} files into {pak_path.name}: {orig_total / 1e6:.1f} MB -> {pak_path.stat().st_size / 1e6:.1f} MB")
    return stamp


def stage(recomp_dir: Path, out_dir: Path, repack: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = game_config.load(ROOT)
    game = cfg["game"]

    data_stamp = pack_game_data(cfg, out_dir, force=repack)

    meta = {
        "id": game["id"],
        "title": game["name"],
        "appName": game["app_name"],
        "executable": game["executable"],
        "exeDir": game["exe_dir"],
        "dataStamp": data_stamp,
    }
    if cfg.get("registry"):
        reg_defaults = {}
        for key, vals in cfg["registry"].items():
            if not isinstance(vals, dict):
                continue
            reg_defaults[key] = {
                k: ({"type": 4, "data": int(v)} if isinstance(v, int) and not isinstance(v, bool)
                    else {"type": 1, "data": str(v)})
                for k, v in vals.items()
            }
        if reg_defaults:
            meta["registryDefaults"] = reg_defaults
    (out_dir / "game.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    for src in (ROOT / "web").iterdir():
        if src.is_file():
            shutil.copy2(src, out_dir / src.name)

    for name in RECOMP_FILES:
        src = recomp_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / name)

    res_dir = recomp_dir / "web-resources"
    dst_res = out_dir / "web-resources"
    if res_dir.is_dir():
        if dst_res.exists():
            shutil.rmtree(dst_res)
        shutil.copytree(res_dir, dst_res)

    h = hashlib.sha256()
    for p in (out_dir / "ArmageddonRecomp.wasm", out_dir / "game.json", out_dir / "app.js", out_dir / "worker.js", out_dir / "index.html"):
        if p.exists():
            h.update(p.read_bytes())
    stamp = h.hexdigest()[:12] if h.digest_size else "dev"
    for name in ("index.html", "app.js", "worker.js"):
        p = out_dir / name
        if p.exists():
            p.write_text(p.read_text(encoding="utf-8").replace(BUILD_STAMP, stamp), encoding="utf-8")

    # Pre-gzip .wasm and .js for fast wire transfer
    for name in ("ArmageddonRecomp.wasm", "ArmageddonRecomp.js"):
        src = out_dir / name
        if src.exists():
            gz = out_dir / (name + ".gz")
            if not gz.exists() or gz.stat().st_mtime < src.stat().st_mtime:
                gz.write_bytes(gzip.compress(src.read_bytes(), compresslevel=6))
                print(f"Gzipped {name}: {src.stat().st_size / 1e6:.1f} MB -> {gz.stat().st_size / 1e6:.1f} MB")

    print(f"Staged WA-only web build in {out_dir} (stamp={stamp})")


class IsolatedHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".mjs": "application/javascript",
        ".js": "application/javascript",
    }

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Content-Security-Policy", CSP)
        if self.path.split("?", 1)[0].endswith((".json", ".js", ".mjs", ".html", "/")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        accept = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept and not Path(path).is_dir():
            gz = Path(path + ".gz")
            if gz.is_file():
                ctype = self.guess_type(path)
                f = gz.open("rb")
                st = gz.stat()
                self.send_response(200)
                self.send_header("Content-type", ctype)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(st.st_size))
                self.send_header("Last-Modified", self.date_time_string(st.st_mtime))
                self.end_headers()
                return f
        return super().send_head()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recomp-dir", type=Path, default=ROOT / "build" / "web" / "recomp")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "web" / "site")
    ap.add_argument("--repack", action="store_true", help="Force re-packing game assets")
    ap.add_argument("--serve", type=int, nargs="?", const=8080, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    stage(args.recomp_dir, args.out, repack=args.repack)
    if args.serve is not None:
        handler = functools.partial(IsolatedHandler, directory=str(args.out))
        with http.server.ThreadingHTTPServer((args.host, args.serve), handler) as srv:
            print(f"Serving Worms Armageddon at http://{args.host}:{args.serve}/")
            srv.serve_forever()


if __name__ == "__main__":
    main()
