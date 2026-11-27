#!/usr/bin/env python3
"""Read-only process-group and GPU telemetry. One logical CPU core = 100%."""
import argparse
import csv
import datetime
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time


def read_int(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def processes(pgid, page_size):
    found = {}
    try:
        entries = list(Path('/proc').iterdir())
    except OSError:
        return None
    for directory in entries:
        if not directory.name.isdigit():
            continue
        try:
            raw = (directory / 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if int(fields[2]) != pgid:
                continue
            pid = int(directory.name)
            start = int(fields[19])
            found[(pid, start)] = {
                'pid': pid,
                'start_ticks': start,
                'cpu_ticks': int(fields[11]) + int(fields[12]),
                'rss_bytes': max(0, int(fields[21])) * page_size,
                'threads': int(fields[17]),
            }
        except (OSError, ValueError, IndexError):
            # Processes may exit between enumeration and reading stat.
            continue
    return found


def nvidia():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,uuid,utilization.gpu,memory.used',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=0.8, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    devices = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) != 4:
            continue
        def number(value):
            try:
                return float(value.strip())
            except ValueError:
                return None
        used = number(row[3])
        devices.append({'index': row[0].strip(), 'uuid': row[1].strip(),
                        'gpu_percent': number(row[2]),
                        'vram_bytes': None if used is None else int(used * 1024 * 1024)})
    return devices or None


def distribution(values):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    return {'mean': statistics.mean(values) if values else None,
            'p95': values[max(0, math.ceil(len(values) * .95) - 1)] if values else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pgid', required=True, type=int)
    parser.add_argument('--seconds', required=True, type=float)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--nvidia', action='store_true', help='Query all NVIDIA GPUs at most once/second')
    parser.add_argument('--amd-device', type=Path, default=None, help='DRM device directory; otherwise detect AMD')
    args = parser.parse_args()
    if args.pgid <= 0 or args.seconds <= 0 or not math.isfinite(args.seconds):
        parser.error('pgid and seconds must be positive')
    interval = .2
    ticks = os.sysconf('SC_CLK_TCK')
    pages = os.sysconf('SC_PAGE_SIZE')
    amd_root = args.amd_device or next((p for p in sorted(Path('/sys/class/drm').glob('card*/device')) if (p / 'gpu_busy_percent').is_file()), Path('/nonexistent-drm-device'))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    clock = time.get_clock_info('monotonic')
    started = time.monotonic()
    previous_time = None
    previous_boot_ticks = None
    previous = {}
    observed_ticks = 0
    last_nv = None
    last_nv_time = None
    next_nv = started
    rows = []
    metadata = {
        'type': 'metadata', 'utc_started': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'pgid': args.pgid, 'sampler_pid': os.getpid(), 'interval_seconds': interval,
        'requested_seconds': args.seconds, 'host': os.uname().nodename,
        'monotonic': {'implementation': clock.implementation, 'resolution_seconds': clock.resolution,
                      'reference_seconds': started},
        'clock_ticks_per_second': ticks, 'page_size_bytes': pages,
        'process_start_clock': 'Linux /proc starttime; CLOCK_BOOTTIME ticks',
        'logical_cpus': os.cpu_count(),
        'units': {'cpu_percent': 'one logical CPU core = 100%', 'rss_bytes': 'bytes; process RSS sum, not PSS',
                  'gpu_percent': 'percent; GPU-wide, not process-specific', 'memory': 'bytes'},
        'amd_device': str(amd_root.resolve()), 'nvidia_enabled': args.nvidia,
        'limitations': ['Main /proc/PID/stat CPU totals include all threads; task entries are not summed.',
                         'PID plus start_ticks identifies a process; exits do not subtract observed CPU.',
                         'Final CPU between the last poll and process exit, and processes entirely between polls, are unobservable.',
                         'RSS sum can count shared mappings in multiple processes.',
                         'Optional NVIDIA samples are cached for one second; missing metrics remain null.'],
    }
    deadline = started
    with args.out.open('w') as output:
        output.write(json.dumps(metadata) + '\n')
        while True:
            now = time.monotonic()
            if now < deadline:
                time.sleep(deadline - now)
                now = time.monotonic()
            boot_ticks = time.clock_gettime(time.CLOCK_BOOTTIME) * ticks
            current = processes(args.pgid, pages)
            elapsed = None if previous_time is None else now - previous_time
            delta = 0
            if current is not None and previous_time is not None:
                for identity, process in current.items():
                    old = previous.get(identity)
                    if old is not None:
                        delta += max(0, process['cpu_ticks'] - old['cpu_ticks'])
                    elif process['start_ticks'] >= previous_boot_ticks:
                        # starttime includes suspended time, like CLOCK_BOOTTIME.
                        # This child began during this sampling interval.
                        delta += process['cpu_ticks']
                observed_ticks += delta
            if args.nvidia and now >= next_nv:
                last_nv = nvidia()
                last_nv_time = time.monotonic()
                next_nv = time.monotonic() + 1
            row = {
                'type': 'sample', 'elapsed_seconds': now - started, 'monotonic_seconds': now,
                'sample_interval_seconds': elapsed,
                'cpu_percent': None if current is None or elapsed is None else 100 * delta / ticks / elapsed,
                'observed_cpu_seconds': observed_ticks / ticks,
                'rss_bytes': None if current is None else sum(p['rss_bytes'] for p in current.values()),
                'threads': None if current is None else sum(p['threads'] for p in current.values()),
                'process_count': None if current is None else len(current),
                'processes': None if current is None else list(current.values()),
                'amd_gpu_percent': read_int(amd_root / 'gpu_busy_percent'),
                'amd_vram_bytes': read_int(amd_root / 'mem_info_vram_used'),
                'amd_gtt_bytes': read_int(amd_root / 'mem_info_gtt_used'),
                'nvidia': last_nv if args.nvidia else None,
                'nvidia_sample_monotonic_seconds': last_nv_time,
            }
            output.write(json.dumps(row) + '\n')
            output.flush()
            rows.append(row)
            if now - started >= args.seconds:
                break
            previous_time = now
            previous_boot_ticks = boot_ticks
            previous = current or {}
            deadline = min(started + args.seconds, deadline + interval)
            if deadline < time.monotonic():
                deadline = min(started + args.seconds, time.monotonic())
    nv_values = [max((p['gpu_percent'] for p in r['nvidia'] if p['gpu_percent'] is not None), default=None)
                 if r['nvidia'] else None for r in rows]
    cpu_summary = distribution(r['cpu_percent'] for r in rows)
    valid_cpu = [r for r in rows if r['cpu_percent'] is not None]
    cpu_interval = sum(r['sample_interval_seconds'] for r in valid_cpu)
    if cpu_interval:
        cpu_summary['mean'] = sum(r['cpu_percent'] * r['sample_interval_seconds'] for r in valid_cpu) / cpu_interval
    summary = {
        'samples': len(rows), 'elapsed_seconds': rows[-1]['elapsed_seconds'],
        'cpu_percent': cpu_summary,
        'amd_gpu_percent': distribution(r['amd_gpu_percent'] for r in rows),
        'nvidia_gpu_percent': distribution(nv_values),
        'peak_rss_bytes': max((r['rss_bytes'] for r in rows if r['rss_bytes'] is not None), default=None),
        'out': str(args.out.resolve()),
    }
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
