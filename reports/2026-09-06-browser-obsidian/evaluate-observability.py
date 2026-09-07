#!/usr/bin/env python3
"""Offline evidence audit and mocked timing-contract probes; no desktop input.

Run from any directory. Prints JSON; redirect to preserve an evaluation.
Passing consistency checks do not independently validate the original raw logs.
"""
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
from collections import Counter
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
import server


def main():
    data = json.loads((HERE / 'metrics.json').read_text())
    metrics = data['metrics']
    with (HERE / 'tool-calls.csv').open(newline='') as source:
        rows = list(csv.DictReader(source))
    with (HERE / 'phases.csv').open(newline='') as source:
        phases = list(csv.DictReader(source))
    checks = []

    def check(name, actual, expected, tolerance=1e-5):
        ok = abs(actual-expected) <= tolerance if isinstance(actual, (int, float)) else actual == expected
        checks.append(dict(name=name, passed=ok, actual=actual, expected=expected))

    total = lambda field, selected=rows: sum(float(r[field]) for r in selected)
    rejected = [r for r in rows if r['rejected'] == 'True']
    counts = Counter(t.strip() for r in rows for t in r['nested_tools'].split(';'))
    check('outer_call_count', len(rows), metrics['calls'])
    check('unique_call_ids', len({r['call_id'] for r in rows}), len(rows))
    check('contiguous_call_numbers', [int(r['n']) for r in rows], list(range(1, len(rows)+1)))
    check('nested_operation_counts', dict(counts), metrics['nested_calls'])
    for field, key in [('tool_s','tool_s'), ('observed_s','observed_tool_intervals_s'),
                       ('approval_s','review_s'), ('image_png_bytes','image_bytes'),
                       ('image_base64_bytes','base64_bytes'), ('response_text_bytes','response_text_bytes'),
                       ('request_code_bytes','request_code_bytes')]:
        check('ledger_sum_'+field, total(field), metrics[key])
    check('image_bearing_calls', sum(int(r['image_png_bytes']) > 0 for r in rows), metrics['images'])
    check('rejections', len(rejected), metrics['rejects'])
    check('rejection_tool_s', total('tool_s', rejected), metrics['reject_tool_s'])
    check('rejection_review_s', total('approval_s', rejected), metrics['reject_approval_s'])
    check('rejection_image_bytes', total('image_png_bytes', rejected), metrics['reject_image_bytes'])
    check('review_count', sum(float(r['approval_s']) > 0 for r in rows), metrics['review_count'])
    check('review_entries_sum', sum(r['duration_s'] for r in data['reviews']), metrics['review_s'])
    check('review_call_links', all(abs(float(rows[r['call_n']-1]['approval_s'])-r['duration_s']) < 1e-5 for r in data['reviews']), True)
    check('valid_envelopes', all(0 <= float(r['approval_s']) <= float(r['tool_s']) <= float(r['observed_s'])+1e-5 for r in rows), True)
    check('serial_outer_intervals', all(float(b['start_s']) >= float(a['end_s']) for a,b in zip(rows, rows[1:])), True)
    check('call_boundary_durations', all(abs(float(r['end_s'])-float(r['start_s'])-float(r['observed_s'])) < 1e-5 for r in rows), True)
    check('gap_boundaries', all(abs(float(r['gap_before_s'])-(float(r['start_s'])-(float(rows[i-1]['end_s']) if i else 0))) < 1e-5 for i,r in enumerate(rows)), True)
    check('phases_elapsed', total('elapsed_s', phases), metrics['duration_s'])
    check('phase_continuity', all(abs(float(b['start_s'])-float(a['end_s'])) < 1e-5 for a,b in zip(phases, phases[1:])), True)
    for phase in phases:
        subset = [r for r in rows if r['phase'] == phase['phase']]
        check('phase_call_count:'+phase['phase'], len(subset), int(phase['outer_calls']))
        check('phase_tool_s:'+phase['phase'], total('tool_s', subset), float(phase['tool_s']))
    for field in ('input_tokens', 'output_tokens'):
        check('phase_usage:'+field, total(field, phases), metrics['root_usage'][field])
    check('top_level_partition', metrics['model_request_excluding_tool_overlap_s']+metrics['review_s']+metrics['nonapproval_tool_s']+metrics['other_host_client_gaps_s'], metrics['duration_s'])
    check('model_overlap_arithmetic', metrics['model_request_spans_s']-metrics['model_tool_overlap_s'], metrics['model_request_excluding_tool_overlap_s'])
    reject_loop = sum(float(rows[i+1]['start_s'])-float(r['start_s']) for i,r in enumerate(rows[:-1]) if r['rejected']=='True')
    check('reject_to_next_call_s', reject_loop, metrics['rejected_attempt_to_next_call_s'])

    overhead = [float(r['observed_s'])-float(r['tool_s']) for r in rows]
    image_overhead = [overhead[i] for i,r in enumerate(rows) if int(r['image_png_bytes'])]
    no_image_overhead = [overhead[i] for i,r in enumerate(rows) if not int(r['image_png_bytes'])]
    bench = json.loads((ROOT / 'benchmarks/latency-20260906-031607.json').read_text())
    residuals = []
    for wait in bench['waits']:
        t = wait['timings_ms']
        residual = t['collection_ms']-sum(t.get(k,0) for k in ('metadata_ms','capture_ms','accessibility_ms','validation_ms'))
        residuals.append(dict(unitemized_collection_ms=residual, collection_ms=t['collection_ms'], share=residual/t['collection_ms']))

    # Exercise real Desktop.call control flow, replacing external effects only.
    # These validate field availability, not real operation latency or correctness.
    probes = []
    for scenario in ('success', 'guard_rejection', 'partial_input_failure', 'post_input_capture_failure'):
        desktop = server.Desktop()
        frame = {'monitor': {'name': 'fixture'}}
        def prepare(*unused):
            if scenario == 'guard_rejection':
                desktop.reject('synthetic guard rejection', 'fixture')
            return frame
        def inject(*unused):
            if scenario == 'partial_input_failure':
                raise RuntimeError('synthetic backend failure')
        def screenshot(*unused):
            if scenario == 'post_input_capture_failure':
                raise RuntimeError('synthetic capture failure')
            return [server.text_content({'timings_ms': {'capture_ms': 0, 'encode_ms': 0}})]
        try:
            with patch.object(desktop, 'prepare', side_effect=prepare), patch.object(server, 'run', side_effect=inject), patch.object(desktop, 'screenshot', side_effect=screenshot):
                try:
                    blocks = desktop.call('type_text', {'frame_id': 'fixture', 'text': 'fixture'})
                except server.ActionRejected as exc:
                    blocks = exc.content
            bodies = [json.loads(b['text']) for b in blocks if b['type'] == 'text']
            timings = {k for b in bodies for k in b.get('timings_ms', {})}
            probes.append(dict(scenario=scenario, action_performed=bodies[0].get('action_performed'),
                               returned_timing_fields=sorted(timings),
                               has_operation_total='total_ms' in timings,
                               retained_internal_timing_fields=sorted(desktop.timings)))
        finally:
            desktop.close()

    paths = [HERE / name for name in ('retrospective.md','proposal.md','tool-calls.csv','metrics.json','phases.csv')]
    paths += [ROOT / 'scripts/server.py', ROOT / 'scripts/benchmark_latency.py',
              ROOT / 'scripts/dev_host.py', ROOT / 'benchmarks/latency-20260906-031607.json',
              *sorted((ROOT / 'scripts/cu').glob('*.py')), Path(__file__)]
    result = dict(scope='Offline artifact consistency audit plus mocked current-checkout timing-contract probes; no live task or raw private-log reconstruction.',
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        checks=checks, all_consistency_checks_passed=all(c['passed'] for c in checks),
        derived=dict(broad_envelope_coverage=1-metrics['other_host_client_gaps_s']/metrics['duration_s'],
            underlying_operations=sum(counts.values()), multi_operation_wrappers=sum(';' in r['nested_tools'] for r in rows),
            input_attempts=sum(counts.get('mcp__wayland__'+name,0) for name in ('press_key','type_text','pointer','scroll')),
            unitemized_nonapproval_tool_s=metrics['nonapproval_tool_s'], host_client_unattributed_s=metrics['other_host_client_gaps_s'],
            boundary_difference_s=sum(overhead), image_boundary_difference_s=sum(image_overhead),
            image_boundary_difference_median_s=statistics.median(image_overhead),
            nonimage_boundary_difference_median_s=statistics.median(no_image_overhead),
            benchmark_collection_residuals=residuals), current_contract_probes=probes,
        limits=['Model request intervals and overlap cannot be reconstructed from the aggregate model fields.',
                'Boundary differences do not isolate client resizing or networking.',
                'Synthetic probe durations are not performance measurements.',
                'Passing consistency checks does not mean observability is sufficient.'])
    print(json.dumps(result, indent=2))
    return 0 if result['all_consistency_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
