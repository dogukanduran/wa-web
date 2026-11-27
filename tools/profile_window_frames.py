#!/usr/bin/env python3
"""Read-only X11 window-update timing; not hardware scanout."""
import ctypes as C, time, select, json, argparse, os, math
from pathlib import Path
from types import SimpleNamespace
p = argparse.ArgumentParser(description='XDamage window-update intervals, not scanout or input latency')
p.add_argument('--window', type=lambda s: int(s, 0))
p.add_argument('--pgid', type=int)
p.add_argument('--child', action='store_true', help='Use the first child drawable, e.g. Wine GL')
p.add_argument('--seconds', type=float, default=30)
p.add_argument('--out', required=True)
args = p.parse_args()
if not args.seconds > 0 or not math.isfinite(args.seconds):
    p.error('--seconds must be positive and finite')
Path(args.out).parent.mkdir(parents=True, exist_ok=True)
x = C.CDLL('libX11.so.6')
x.XOpenDisplay.restype = C.c_void_p
display = x.XOpenDisplay(None)
if not display:
    raise RuntimeError('Run from a graphical X11 desktop shell')
x.XDefaultRootWindow.argtypes = [C.c_void_p]
x.XDefaultRootWindow.restype = C.c_ulong
root = x.XDefaultRootWindow(display)
a = SimpleNamespace(x=x, d=display)

x.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
x.XInternAtom.restype = C.c_ulong
x.XGetWindowProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long, C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int), C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ubyte))]
x.XFree.argtypes = [C.c_void_p]

def prop(w, name):
    t = C.c_ulong()
    f = C.c_int()
    n = C.c_ulong()
    left = C.c_ulong()
    v = C.POINTER(C.c_ubyte)()
    x.XGetWindowProperty(display, w, x.XInternAtom(display, name.encode(), 0), 0, 65536, 0, 0, C.byref(t), C.byref(f), C.byref(n), C.byref(left), C.byref(v))
    if not v:
        return []
    values = list(C.cast(v, C.POINTER(C.c_ulong))[:n.value]) if f.value == 32 else []
    x.XFree(v)
    return values
if args.window is None and args.pgid:
    for w in prop(root, '_NET_CLIENT_LIST'):
        pid = prop(w, '_NET_WM_PID')
        try:
            if pid and os.getpgid(pid[0]) == args.pgid:
                args.window = w
                break
        except ProcessLookupError:
            pass
if args.window is None:
    p.error('No matching window; provide --window or an X11 --pgid')
if args.child:
    x.XQueryTree.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)]
    rr = C.c_ulong()
    parent = C.c_ulong()
    children = C.POINTER(C.c_ulong)()
    count = C.c_uint()
    x.XQueryTree(display, args.window, C.byref(rr), C.byref(parent), C.byref(children), C.byref(count))
    if count.value:
        args.window = children[0]
    if children:
        x.XFree(children)
l = C.CDLL('libXdamage.so.1')
l.XDamageQueryExtension.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
base = C.c_int()
err = C.c_int()
if not l.XDamageQueryExtension(a.d, C.byref(base), C.byref(err)):
    raise RuntimeError('XDamage unavailable')
l.XDamageCreate.argtypes = [C.c_void_p, C.c_ulong, C.c_int]
l.XDamageCreate.restype = C.c_ulong
damage = l.XDamageCreate(a.d, args.window, 3)
l.XDamageSubtract.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_ulong]
a.x.XPending.argtypes = [C.c_void_p]
a.x.XNextEvent.argtypes = [C.c_void_p, C.c_void_p]
a.x.XConnectionNumber.argtypes = [C.c_void_p]
a.x.XConnectionNumber.restype = C.c_int
a.x.XFlush.argtypes = [C.c_void_p]
a.x.XFlush(a.d)
fd = a.x.XConnectionNumber(a.d)
e = (C.c_long * 24)()
times = []
start = time.perf_counter()
while time.perf_counter() - start < args.seconds:
    select.select([fd], [], [], max(0, min(0.1, args.seconds - (time.perf_counter() - start))))
    while a.x.XPending(a.d):
        a.x.XNextEvent(a.d, C.byref(e))
        if C.cast(e, C.POINTER(C.c_int))[0] == base.value:
            times.append(time.perf_counter())
            l.XDamageSubtract(a.d, damage, 0, 0)
            a.x.XFlush(a.d)
dt = sorted((1000 * (b - a) for a, b in zip(times, times[1:])))
q = lambda f: dt[min(len(dt) - 1, int(len(dt) * f))] if dt else None
report = {'metric': 'XDamage nonempty window-update receive intervals; not hardware scanout or input latency', 'window': args.window, 'seconds': args.seconds, 'timestamps': times, 'intervals_ms': dt, 'fps': (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else None, 'p50_ms': q(0.5), 'p95_ms': q(0.95), 'p99_ms': q(0.99), 'max_ms': q(1)}
Path(args.out).write_text(json.dumps(report))
print(json.dumps({k: v for k, v in report.items() if k not in ['timestamps', 'intervals_ms']}))
