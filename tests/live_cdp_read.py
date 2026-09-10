#!/usr/bin/env python3
"""Opt-in headless Chromium smoke test in a disposable profile; no user tabs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cu.app_surfaces import cdp_read


def main():
    with tempfile.TemporaryDirectory(prefix='wcu-cdp-fixture-') as folder:
        root = Path(folder)
        page = root/'fixture.html'
        page.write_text('<html><head><title>WCU CDP fixture</title></head><body><div class="comment">A bounded excerpt. <a href="https://example.com/item?id=1">source</a></div><script>const secret="not returned";</script></body></html>')
        profile = root/'profile'
        process = subprocess.Popen(['chromium', '--headless=new', '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking', '--force-renderer-accessibility', '--remote-debugging-address=127.0.0.1',
            '--remote-debugging-port=0', '--user-data-dir='+str(profile), page.as_uri()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        previous = os.environ.get('WCU_CDP_PORT')
        try:
            end = time.monotonic()+15
            active = profile/'DevToolsActivePort'
            while not active.exists() and time.monotonic() < end:
                if process.poll() is not None:
                    raise RuntimeError('Fixture Chromium exited')
                time.sleep(.05)
            os.environ['WCU_CDP_PORT'] = active.read_text().splitlines()[0]
            while True:
                pages = cdp_read()['pages']
                match = [p for p in pages if p['url'] == page.as_uri()]
                if match:
                    break
                if time.monotonic() >= end:
                    raise TimeoutError('Fixture page not ready')
                time.sleep(.05)
            result = cdp_read(match[0]['id'], page.as_uri(), '.comment')
            assert result['text'] == 'A bounded excerpt. source', result
            assert result['links'] == ['https://example.com/item?id=1'], result
            assert result['complete'] and result['read_only']
            print(json.dumps({'status': 'passed', 'text': result['text'], 'links': result['links'],
                              'scope': 'Disposable headless Chromium; no user browser changes'}))
        finally:
            if previous is None:
                os.environ.pop('WCU_CDP_PORT', None)
            else:
                os.environ['WCU_CDP_PORT'] = previous
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
