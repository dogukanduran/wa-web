#!/usr/bin/env python3
"""Summarize presenter completions, never hardware scanout or input latency."""
import argparse
import csv
import json
import math
from pathlib import Path


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * fraction))] if values else None


def summarize(path, seconds=30):
    rows = []
    rejected = 0
    with Path(path).open(newline='') as source:
        for record in csv.DictReader(source):
            try:
                row = {key: float(record[key]) for key in
                       ('sealed_s', 'submitted_s', 'presented_s', 'gpu_ms', 'drops', 'repeat')}
                if not all(math.isfinite(v) for v in row.values()):
                    raise ValueError('non-finite timestamp')
                if row['repeat'] not in (0, 1) or row['presented_s'] < row['sealed_s']:
                    raise ValueError('invalid completion')
                if rows and row['presented_s'] < rows[-1]['presented_s']:
                    raise ValueError('clock went backwards')
            except (KeyError, TypeError, ValueError):
                rejected += 1  # a live buffered CSV may end in a partial record
                continue
            rows.append(row)
    if not rows:
        raise ValueError('No complete frame records')
    end = rows[-1]['presented_s']
    rows = [r for r in rows if r['presented_s'] >= end - seconds]
    new = [r for r in rows if not r['repeat']]
    intervals = [1000 * (b['presented_s'] - a['presented_s']) for a, b in zip(new, new[1:])]
    pipeline = [1000 * (r['presented_s'] - r['sealed_s']) for r in new]
    span = new[-1]['presented_s'] - new[0]['presented_s'] if len(new) > 1 else 0
    distribution = lambda values: {**{name: percentile(values, p) for name, p in
                                     [('p50', .5), ('p95', .95), ('p99', .99)]},
                                   'max': max(values) if values else None}
    return {'metric': 'software presenter completion; not hardware scanout or input latency',
            'window_seconds': seconds, 'new_frames': len(new), 'all_completions': len(rows),
            'new_completion_fps': (len(new) - 1) / span if span > 0 else None,
            'new_intervals_ms': distribution(intervals), 'sealed_to_completion_ms': distribution(pipeline),
            'repeat_percent': 100 * (len(rows) - len(new)) / len(rows),
            'drops_delta': rows[-1]['drops'] - rows[0]['drops'], 'rejected_records': rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()
    if args.seconds <= 0 or not math.isfinite(args.seconds):
        parser.error('--seconds must be positive and finite')
    print(json.dumps(summarize(args.csv, args.seconds), indent=2))


if __name__ == '__main__':
    main()
