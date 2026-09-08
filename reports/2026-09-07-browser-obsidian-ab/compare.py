#!/usr/bin/env python3
"""Summarize host and runtime trace records per revision. Timings and counts only."""
import glob
import json
from pathlib import Path
import statistics
import sys


def load(directory, prefix):
    for path in sorted(glob.glob(str(Path(directory)/f'{prefix}-*.jsonl'))):
        for line in open(path):
            yield json.loads(line)


def percentile(values, share):
    values = sorted(values)
    return values[min(len(values)-1, int(round(share*(len(values)-1))))] if values else None


def main(directory):
    builds = {}
    for record in load(directory, 'host'):
        if record.get('kind') != 'host':
            continue
        build = builds.setdefault(record.get('revision') or 'unknown', {'calls': [], 'tools': {}})
        ms = (record['flushed_ns']-record['received_ns'])/1e6
        build['calls'].append(record)
        tool = build['tools'].setdefault(record['tool'], {'n': 0, 'errors': 0, 'images': 0, 'bytes': 0, 'ms': []})
        tool['n'] += 1
        tool['errors'] += bool(record.get('is_error'))
        tool['images'] += record.get('images', 0)
        tool['bytes'] += record.get('response_bytes', 0)
        tool['ms'].append(ms)
    runtime = {}
    for record in load(directory, 'trace'):
        if record.get('kind') != 'request':
            continue
        entry = runtime.setdefault(record['tool'], {'n': 0, 'rejected': 0, 'error': 0, 'total_ms': [], 'guard_ms': [], 'input_ms': []})
        entry['n'] += 1
        entry['rejected'] += record['outcome']['status'] == 'rejected'
        entry['error'] += record['outcome']['status'] == 'error'
        for key in ('total_ms', 'guard_ms', 'input_ms'):
            if key in record.get('timings_ms', {}):
                entry[key].append(record['timings_ms'][key])
    report = {'builds': {}, 'runtime_by_tool': {}}
    for revision, build in builds.items():
        calls = build['calls']
        wall = (calls[-1]['flushed_ns']-calls[0]['received_ns'])/1e9 if calls else 0
        report['builds'][revision[:12]] = {
            'calls': len(calls), 'errors': sum(bool(c.get('is_error')) for c in calls),
            'images': sum(c.get('images', 0) for c in calls), 'response_bytes': sum(c.get('response_bytes', 0) for c in calls),
            'first_to_last_call_s': round(wall, 3),
            'tools': {name: {'n': t['n'], 'errors': t['errors'], 'images': t['images'], 'bytes': t['bytes'],
                             'median_ms': round(statistics.median(t['ms']), 1), 'p95_ms': round(percentile(t['ms'], .95), 1)}
                      for name, t in sorted(build['tools'].items())}}
    for tool, entry in sorted(runtime.items()):
        report['runtime_by_tool'][tool] = {k: (v if not isinstance(v, list) else (round(statistics.median(v), 1) if v else None))
                                           for k, v in entry.items()}
    report['limits'] = ['Host envelope excludes model time, approval and client image handling.',
                        'Absolute times are for this session only; compare builds against each other.']
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/home/david/.local/state/wayland-computer-use-trace')
