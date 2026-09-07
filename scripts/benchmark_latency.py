#!/usr/bin/env python3
"""Opt-in live latency check: disposable GTK label, no keyboard/mouse injection.

Reports metrics only. Captured pixels, UI text, and app metadata are not saved.
Runs against the checkout, independently of installed MCP connections.
The report is rewritten after every trial and on failure with an explicit status,
so an assertion late in the run never discards earlier measurements.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import threading
import time

from cu import observation as obs
from cu.capture import Capturer
from cu.pixels import png_rgb
import server

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'wayland-desktop-observer/scripts/benchmark.py'
TITLE = 'Wayland observer comparison — disposable status test'
SCHEDULED_DELAY_MS = 200


def provenance():
    info = {'python': sys.version.split()[0], 'runs': 'checkout source, not the published or installed bundle',
            'capture_helper_present': (ROOT/'scripts/cu/capture-helper').is_file(),
            'checkout_git_revision': None, 'published_revision': None,
            'clock': 'CLOCK_MONOTONIC; fixture timestamps use perf_counter in the same domain on Linux',
            'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [Path(__file__), ROOT/'scripts/server.py', *sorted((ROOT/'scripts/cu').glob('*.py')),
                                        ROOT/'native/capture.c', FIXTURE]}}
    try:
        info['checkout_git_revision'] = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], stdout=subprocess.PIPE,
                                                       stderr=subprocess.DEVNULL, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        info['published_revision'] = json.loads((ROOT/'.dev/current.json').read_text()).get('revision')
    except (OSError, ValueError):
        pass
    return info


def main():
    server.session_env()
    destination = ROOT/'benchmarks'/('latency-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
    if destination.exists():
        raise FileExistsError(destination)
    report = {'scope': 'Local capture/encode and deterministic wait timings; no model or approval latency.',
              'status': 'incomplete', 'error': None, 'started_at': time.time(), 'provenance': provenance(),
              'focus_fixture': '--focus-fixture' in sys.argv, 'capture': [], 'waits': []}

    def save(status=None, error=None):
        if status:
            report['status'] = status
        if error is not None:
            report['error'] = f'{type(error).__name__}: {str(error)[:400]}'
        report['saved_at'] = time.time()
        pending = destination.with_suffix('.json.tmp')
        pending.write_text(json.dumps(report, indent=2)+'\n')
        os.replace(pending, destination)

    save()
    gui = subprocess.Popen([sys.executable, str(FIXTURE), '--fixture'], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True, env=dict(os.environ, GDK_BACKEND='wayland'))
    events = queue.Queue()
    def reader():
        for line in gui.stdout:
            events.put(json.loads(line))
    threading.Thread(target=reader, daemon=True).start()
    desktop = server.Desktop()
    observer = obs.Observer()
    raw = Capturer(helper='/nonexistent')
    native = Capturer()
    try:
        assert events.get(timeout=10).get('ready')
        windows = json.loads(server.run(['hyprctl', '-j', 'clients']))
        window = next(w for w in windows if w['pid'] == gui.pid and w['title'] == TITLE)
        address = window['address']
        if report['focus_fixture']:
            # Explicit opt-in: focus only the exact PID/title we just created,
            # once at startup. Any subsequent focus loss aborts the benchmark.
            desktop.dispatch('focuswindow', 'address:'+address)
            desktop.wait_focus(address)
            time.sleep(.3)  # Fixture layout/animation setup, outside measured work.
        def check_focus():
            assert json.loads(server.run(['hyprctl', '-j', 'activewindow'])).get('address') == address, 'Fixture lost focus; stopping'
        check_focus()
        monitor = next(m for m in json.loads(server.run(['hyprctl', '-j', 'monitors'])) if m['id'] == window['monitor'])
        geometry = obs.capture_geometry(window, monitor)
        report['capture_dimensions'] = geometry[2:]
        previous_rgb = None
        for trial in range(6):
            methods = [('grim-ppm', raw), ('native', native)]
            if trial % 2:
                methods.reverse()
            for name, capturer in methods:
                check_focus()
                start = time.monotonic_ns()
                capture = capturer.capture(monitor, geometry)
                captured = time.monotonic_ns()
                encoded = png_rgb(capture.width, capture.height, capture.rgb)
                report['capture'].append({'trial': trial, 'method': name, 'backend': capture.backend,
                    'capture_ms': (captured-start)/1e6, 'capture_encode_ms': (time.monotonic_ns()-start)/1e6,
                    'lock_wait_ms': (capture.started_ns-capture.requested_ns)/1e6 if capture.requested_ns else None,
                    'png_bytes': len(encoded), 'fallback_reason': capture.fallback_reason,
                    'rgb_matches_previous': capture.rgb == previous_rgb if previous_rgb is not None else None})
                previous_rgb = capture.rgb
                save()
        check_focus()
        # Prove native damage waits are bounded on a static source.
        start = time.monotonic_ns()
        capture = native.capture(monitor, geometry, wait_damage_ms=50)
        report['damage_wait'] = {'backend': capture.backend, 'elapsed_ms': (time.monotonic_ns()-start)/1e6,
                                 'fallback_reason': capture.fallback_reason}
        save()
        for trial in range(3):
            check_focus()
            current = json.loads(observer.observe(address, channels=['accessibility'], images=False)[0]['text'])
            assert current['state']['accessibility']['status'] == 'available', current['state']['accessibility'].get('reason')
            expected = f'Ready {trial:03}'
            events_before = observer.collector.accessibility.event_count
            scheduled_ns = time.monotonic_ns()
            gui.stdin.write(json.dumps({'text': expected, 'delay_ms': SCHEDULED_DELAY_MS})+'\n')
            gui.stdin.flush()
            start = time.monotonic_ns()
            result = json.loads(observer.observe(address, channels=['accessibility'], images=False,
                condition={'kind': 'accessible', 'name': expected}, timeout_ms=3000)[0]['text'])
            finish = time.monotonic_ns()
            applied = events.get(timeout=3)
            applied_ns = int(applied['time']*1e9)  # Fixture perf_counter: same monotonic domain, not an estimate.
            entry = {'trial': trial, 'scheduled_delay_ms': SCHEDULED_DELAY_MS, 'scheduled_ns': scheduled_ns,
                     'applied_ns': applied_ns, 'request_started_ns': start, 'response_ns': finish,
                     'request_ms': (finish-start)/1e6, 'schedule_to_applied_ms': (applied_ns-scheduled_ns)/1e6,
                     'after_change_ms': (finish-applied_ns)/1e6, 'status': result.get('status'),
                     'condition_met': result.get('condition_met'), 'matched_expected_text': applied.get('applied') == expected,
                     'events_received': observer.collector.accessibility.event_count-events_before,
                     'timings_ms': result['timings_ms']}
            report['waits'].append(entry)  # Preserve the sample before judging it.
            save()
            assert 0 <= entry['schedule_to_applied_ms'] < 5000, 'Fixture clock domain mismatch'
            assert finish >= applied_ns, 'False detection before the scheduled change'
            assert result['condition_met'] and entry['matched_expected_text'], entry
        report['accessibility_events_received'] = observer.collector.accessibility.event_count
        observer.stop()
        check_focus()
        content = desktop.call('observe_window', {'window': address})
        actionable = json.loads(content[0]['text'])
        assert actionable['actionable'] and actionable['input_frame_id'] in desktop.frames
        check_focus()
        frame_meta = json.loads(content[1]['text'])
        report['shared_capture'] = {'actionable': True, 'image_count': sum(c['type'] == 'image' for c in content),
            'capture_backend': frame_meta['capture_backend'], 'fallback_reason': frame_meta.get('fallback_reason'),
            'timings_ms': actionable.get('timings_ms')}
        for name in ('grim-ppm', 'native'):
            rows = [r for r in report['capture'] if r['method'] == name]
            report.setdefault('summary', {})[name] = {k: round(statistics.median(r[k] for r in rows), 2)
                                                      for k in ('capture_ms', 'capture_encode_ms', 'png_bytes')}
        report['summary']['after_change_ms'] = [round(r['after_change_ms'], 2) for r in report['waits']]
        save('complete')
        print(json.dumps({'report': str(destination), 'status': report['status'], 'summary': report['summary'],
            'wait_ms': [round(r['request_ms'], 2) for r in report['waits']],
            'shared_capture': report['shared_capture'], 'damage_wait': report['damage_wait']}), flush=True)
    except BaseException as exc:
        save('failed', exc)
        print(json.dumps({'report': str(destination), 'status': 'failed', 'error': report['error']}), flush=True)
        raise
    finally:
        desktop.close()
        observer.close()
        raw.close()
        native.close()
        gui.terminate()
        gui.wait(timeout=5)
        gui.stdin.close()
        gui.stdout.close()


if __name__ == '__main__':
    main()
