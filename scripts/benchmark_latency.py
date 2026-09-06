#!/usr/bin/env python3
"""Opt-in live latency check: disposable GTK label, no keyboard/mouse injection.

Reports metrics only. Captured pixels, UI text, and app metadata are not saved.
Runs against the checkout, independently of installed MCP connections.
"""
import importlib.util
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


def main():
    server.session_env()
    gui=subprocess.Popen([sys.executable,str(FIXTURE),'--fixture'],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,text=True,env=dict(os.environ,GDK_BACKEND='wayland'))
    events=queue.Queue()
    def reader():
        for line in gui.stdout:
            events.put(json.loads(line))
    thread=threading.Thread(target=reader,daemon=True);thread.start()
    desktop=server.Desktop();observer=obs.Observer()
    raw=Capturer(helper='/nonexistent');native=Capturer()
    report={'scope':'Local capture/encode and deterministic wait timings; no model or approval latency.', 'capture':[], 'waits':[]}
    try:
        assert events.get(timeout=10).get('ready')
        windows=json.loads(server.run(['hyprctl','-j','clients']))
        window=next(w for w in windows if w['pid']==gui.pid and w['title']==TITLE)
        address=window['address']
        if '--focus-fixture' in sys.argv:
            # Explicit opt-in: focus only the exact PID/title we just created,
            # once at startup. Any subsequent focus loss aborts the benchmark.
            desktop.dispatch('focuswindow','address:'+address)
            desktop.wait_focus(address)
            time.sleep(.3)  # Fixture layout/animation setup, outside measured work.
        def check_focus():
            assert json.loads(server.run(['hyprctl','-j','activewindow'])).get('address')==address, 'Fixture lost focus; stopping'
        check_focus()
        monitor=next(m for m in json.loads(server.run(['hyprctl','-j','monitors'])) if m['id']==window['monitor'])
        geometry=obs.capture_geometry(window,monitor)
        report['capture_dimensions'] = geometry[2:]
        previous_rgb = None
        for trial in range(6):
            methods=[('grim-ppm',raw),('native',native)]
            if trial%2: methods.reverse()
            for name,capturer in methods:
                check_focus();start=time.monotonic_ns()
                capture=capturer.capture(monitor,geometry)
                captured=time.monotonic_ns();encoded=png_rgb(capture.width,capture.height,capture.rgb)
                report['capture'].append({'trial':trial,'method':name,'backend':capture.backend,
                    'capture_ms':(captured-start)/1e6,'capture_encode_ms':(time.monotonic_ns()-start)/1e6,
                    'png_bytes':len(encoded),'fallback_reason':capture.fallback_reason,
                    'rgb_matches_previous': capture.rgb == previous_rgb if previous_rgb is not None else None})
                previous_rgb = capture.rgb
        check_focus()
        # Prove native damage waits are bounded on a static source.
        start=time.monotonic_ns();capture=native.capture(monitor,geometry,wait_damage_ms=50)
        report['damage_wait']={'backend':capture.backend,'elapsed_ms':(time.monotonic_ns()-start)/1e6}
        for trial in range(3):
            check_focus()
            current=json.loads(observer.observe(address,channels=['accessibility'],images=False)[0]['text'])
            assert current['state']['accessibility']['status']=='available',current['state']['accessibility'].get('reason')
            expected=f'Ready {trial:03}'
            gui.stdin.write(json.dumps({'text':expected,'delay_ms':200})+'\n');gui.stdin.flush()
            start=time.monotonic_ns()
            result=json.loads(observer.observe(address,channels=['accessibility'],images=False,
                condition={'kind':'accessible','name':expected},timeout_ms=3000)[0]['text'])
            applied=events.get(timeout=3)
            assert result['condition_met'] and applied['applied']==expected
            elapsed=(time.monotonic_ns()-start)/1e6
            report['waits'].append({'trial':trial,'scheduled_delay_ms':200,'request_ms':elapsed,
                                   'timings_ms':result['timings_ms']})
        report['accessibility_events_received'] = observer.collector.accessibility.event_count
        observer.stop()
        check_focus()
        content=desktop.call('observe_window',{'window':address})
        actionable=json.loads(content[0]['text'])
        assert actionable['actionable'] and actionable['input_frame_id'] in desktop.frames
        check_focus()
        report['shared_capture']={'actionable':True,'image_count':sum(c['type']=='image' for c in content),
            'capture_backend':json.loads(content[1]['text'])['capture_backend']}
        for name in ('grim-ppm','native'):
            rows=[r for r in report['capture'] if r['method']==name]
            report.setdefault('summary',{})[name]={k:round(statistics.median(r[k] for r in rows),2)
                for k in ('capture_ms','capture_encode_ms','png_bytes')}
        destination=ROOT/'benchmarks'/('latency-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
        report['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__), ROOT/'scripts/server.py', *sorted((ROOT/'scripts/cu').glob('*.py')), ROOT/'native/capture.c']}
        with destination.open('x') as output:json.dump(report,output,indent=2);output.write('\n')
        print(json.dumps({'report':str(destination),'summary':report['summary'],
            'wait_ms':[round(r['request_ms'],2) for r in report['waits']],
            'shared_capture':report['shared_capture'],'damage_wait':report['damage_wait']}),flush=True)
    finally:
        desktop.close();observer.close();raw.close();native.close()
        gui.terminate();gui.wait(timeout=5)
        gui.stdin.close();gui.stdout.close()


if __name__ == '__main__':main()
