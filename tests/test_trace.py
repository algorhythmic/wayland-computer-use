import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cu import trace


class TraceTests(unittest.TestCase):
    def test_spans_nest_close_on_error_and_report_inclusive_totals(self):
        t = trace.Trace('request', tool='pointer')
        with t.span('guard'):
            with t.span('visual_check'):
                pass
        with self.assertRaises(RuntimeError):
            with t.span('input'):
                raise RuntimeError('boom')
        t.close(RuntimeError('boom'))
        names = {s['name']: s for s in t.spans}
        self.assertEqual(names['visual_check']['parent'], names['guard']['id'])
        self.assertEqual(names['guard']['parent'], t.root['id'])
        self.assertEqual(names['input']['error'], 'RuntimeError')
        self.assertEqual(t.root['error'], 'RuntimeError')
        self.assertTrue(all(s['end_ns'] is not None for s in t.spans))
        durations = t.durations_ms()
        for key in ('guard_ms', 'visual_check_ms', 'input_ms', 'total_ms'):
            self.assertIn(key, durations)
        self.assertGreaterEqual(durations['total_ms'], durations['guard_ms'])

    def test_unfinished_spans_are_marked_incomplete_on_close(self):
        t = trace.Trace('request')
        t.begin('guard')
        t.close()
        self.assertTrue(t.spans[1]['incomplete'])
        self.assertIsNotNone(t.spans[1]['end_ns'])

    def test_exec_records_attach_to_active_thread_only(self):
        t = trace.Trace('request')
        previous = trace.activate(t)
        seen = []
        try:
            trace.record_exec('/usr/bin/hyprctl', time.monotonic_ns(), 0)
            thread = threading.Thread(target=lambda: seen.append(trace.current()))
            thread.start()
            thread.join()
        finally:
            trace.activate(previous)
        self.assertEqual(seen, [None])
        self.assertEqual(t.spans[-1]['attrs'], {'argv0': 'hyprctl', 'exit_code': 0})
        self.assertNotIn('exec_ms', t.durations_ms())

    def test_recorder_is_opt_in(self):
        with patch.dict(os.environ):
            os.environ.pop(trace.ENVIRONMENT, None)
            recorder = trace.Recorder()
        self.assertFalse(recorder.enabled)
        self.assertFalse(recorder.write({'kind': 'request'}))
        self.assertTrue(recorder.status()['complete'])

    def test_recorder_requires_private_absolute_directory(self):
        self.assertFalse(trace.Recorder(directory='relative/path').enabled)
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o755)
            recorder = trace.Recorder(directory=folder)
            self.assertFalse(recorder.enabled)
            self.assertIn('0700', recorder.error)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_recorder_scrubs_text_and_counts_dropped_records(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            recorder = trace.Recorder(directory=folder, limit_bytes=2000, provenance={'python': '3', 'wall_s': 1.0})
            self.assertTrue(recorder.enabled)
            self.assertEqual(stat.S_IMODE(Path(recorder.path).stat().st_mode), 0o600)
            self.assertTrue(recorder.write({'kind': 'request', 'tool': 'type_text', 'text': 'SECRET', 'reason': 'x'*500,
                                            'nested': {'title': 'SECRET TITLE', 'count': 3, 'ok': True, 'none': None}}))
            self.assertFalse(recorder.write({'kind': 'request', 'spans': [{'name': 'a', 'start_ns': 1, 'end_ns': 2}]*200}))
            recorder.close()
            text = Path(recorder.path).read_text()
            self.assertNotIn('SECRET', text)
            records = [json.loads(line) for line in text.splitlines()]
            self.assertEqual([r['kind'] for r in records], ['process', 'request'])
            self.assertEqual(records[0]['python'], '3')
            self.assertIn('monotonic_ns', records[0])
            request = records[1]
            self.assertIsNone(request['text'])
            self.assertEqual(request['nested'], {'title': None, 'count': 3, 'ok': True, 'none': None})
            self.assertEqual(len(request['reason']), trace.REASON_LIMIT)
            status = recorder.status()
            self.assertEqual((status['records'], status['dropped'], status['complete']), (2, 1, False))


if __name__ == '__main__':
    unittest.main()
