"""Regression coverage for deferred pixels, readiness, and app acceptance."""
import base64
import copy
import json
import struct
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_server
from test_server import server
from cu.capture import Capture
from cu.observation import History
from cu.accessibility_worker import probe_tree
from test_readiness_contract import AT, Node


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        lock = patch.object(server, 'desktop_locked', return_value=False)
        lock.start()
        self.addCleanup(lock.stop)

    def desktop(self):
        d, f, state, args = test_server.Tests.approval_desktop(self)
        state['active'] = '0x123'
        original = d.hypr
        d.hypr = lambda cmd: {} if cmd == 'layers' else dict(f['target']) if cmd == 'activewindow' else original(cmd)
        d.result_target = d.identity(f['target'])
        now = time.monotonic_ns()
        sample = {'capture': Capture(800, 600, bytes(800*600*3), now, now+1, 'fixture'),
                  'monitor': f['monitor'], 'target': f['target'], 'geometry': [0, 0, 800, 600],
                  'state': {'monitors': [f['monitor']], 'active_window': '0x123',
                            'accessibility': {'status': 'unavailable', 'reason': 'fixture'}}, 'revision': 'obs:1'}
        observer = SimpleNamespace(observe=lambda *a, **kw: ([server.text_content({'freshness_satisfied': True})], sample))
        d.observation_service = lambda: observer
        self.addCleanup(d.close)
        return d, f, sample

    def test_deferred_frame_does_not_encode_or_deliver_pixels(self):
        d, f, sample = self.desktop()
        with patch.object(server, 'png_rgb', side_effect=AssertionError('unrequested encoding')):
            result = d.result_capture()
        self.assertEqual(len(result), 1)
        meta = json.loads(result[0]['text'])
        self.assertFalse(meta['image_delivered'])
        self.assertEqual((meta['width'], meta['height']), (400, 300))
        self.assertEqual(meta['accessibility']['status'], 'unavailable')
        self.assertIn(meta['frame_id'], d.frames)

    def test_view_scale_coordinate_guard_and_expiry(self):
        d, f, sample = self.desktop()
        meta = json.loads(d.result_capture()[0]['text'])
        token = meta['frame_id']
        with self.assertRaisesRegex(ValueError, 'requires viewing'):
            d.prepare('pointer', {'frame_id': token, 'x': 40, 'y': 50})
        result = d.call('view_frame', {'frame_id': token})
        png = base64.b64decode(result[1]['data'])
        self.assertEqual(struct.unpack('!II', png[16:24]), (400, 300))
        frame = d.frames[token]
        self.assertEqual(d.point(frame, 40, 50), (80, 100))
        self.assertEqual(d.action_region(frame, {'x': 40, 'y': 50}), (40, 60, 105, 125))
        original = d.call('view_frame', {'frame_id': token, 'detail': 'original'})
        new_meta = json.loads(original[0]['text'])
        self.assertNotEqual(new_meta['frame_id'], token)
        self.assertEqual(new_meta['capture_started_ns'], meta['capture_started_ns'])
        self.assertEqual(new_meta['expires_at_ns'], meta['expires_at_ns'])
        d.frames[new_meta['frame_id']]['time'] -= 121
        with self.assertRaisesRegex(ValueError, 'expired'):
            d.call('view_frame', {'frame_id': new_meta['frame_id']})

    def test_odd_sized_half_view_maps_sampled_pixels_exactly(self):
        d, f, sample = self.desktop()
        now = time.monotonic_ns()
        sample['capture'] = Capture(801, 601, bytes(801*601*3), now, now+1, 'fixture')
        sample['geometry'] = [0, 0, 801, 601]
        meta = json.loads(d.result_capture()[0]['text'])
        frame = d.frames[meta['frame_id']]
        self.assertEqual((meta['width'], meta['height']), (401, 301))
        self.assertEqual(d.point(frame, 400, 300), (800, 600))
        self.assertEqual(d.point(frame, 40, 50), (80, 100))

    def test_failure_delivery_policy_and_crop_recovery(self):
        for policy, count in (('none', 0), ('on_failure', 1), ('target', 1)):
            d, f, sample = self.desktop()
            d.images = policy
            success = d.result_capture()
            self.assertEqual(sum(b['type'] == 'image' for b in success), int(policy == 'target'))
            with patch.object(d, 'screenshot', side_effect=AssertionError('monitor fallback')):
                with self.assertRaises(server.ActionRejected) as error:
                    d.reject('changed', 'DP-1', {'visual_difference': {'changed_bbox_xyxy': [1, 2, 3, 4]}})
            self.assertEqual(sum(b['type'] == 'image' for b in error.exception.content), count)
            self.assertEqual(json.loads(error.exception.content[1]['text'])['source_size'], [800, 600])

    def test_missing_target_never_implicitly_reads_monitor(self):
        d, f, sample = self.desktop()
        d.result_target = {'address': '0x999'}
        with patch.object(d, 'screenshot', side_effect=AssertionError('monitor')):
            result = d.result_capture()
        self.assertTrue(json.loads(result[0]['text'])['overview_required'])
        self.assertEqual(len(result), 1)

    def test_verified_caret_only_and_other_changes_still_reject(self):
        d, f, sample = self.desktop()
        focus = {'ref': 'root/1', 'role': 'entry', 'name': 'Name', 'states': ['focused', 'editable'],
                 'protected': False, 'caret': {'offset': 1, 'box': [19, 20, 22, 48]}}
        evidence = {'focus_status': 'unique', 'focused_control': focus}
        f['accessibility'] = evidence
        d.observation_service = lambda: SimpleNamespace(collector=SimpleNamespace(
            accessibility=SimpleNamespace(probe=lambda *a: copy.deepcopy(evidence))))
        data = bytearray(f['visual'][2])
        for y in range(12, 40):
            data[(y*784+12)*3:(y*784+12)*3+3] = b'\xff'*3
        after = (784, 584, bytes(data))
        self.assertTrue(d.guarded_pixels(f, after)['accepted'])
        f['accessibility'] = {}
        self.assertFalse(d.guarded_pixels(f, after)['accepted'])
        f['accessibility'] = evidence
        data[0] = 255
        self.assertFalse(d.guarded_pixels(f, (784, 584, bytes(data)))['accepted'])
        f['accessibility'] = copy.deepcopy(evidence)
        f['accessibility']['focused_control']['caret']['offset'] = 2
        self.assertFalse(d.guarded_pixels(f, after)['accepted'])

    def test_focused_text_is_bounded_and_protected_text_never_leaks(self):
        for role, expected in (('entry', 'available'), ('password', 'protected')):
            node = Node('field', role=role, states=['FOCUSED'], text='s'*2000)
            evidence = probe_tree(Node('window', children=[node]), AT)
            text = evidence['text_readback']
            self.assertEqual(text['status'], expected)
            self.assertFalse(text['verifiable'])
            self.assertEqual(len(text.get('text', '')), 1024 if role == 'entry' else 0)

    def test_text_readback_length_race_is_not_verifiable(self):
        node = Node('field', role='entry', states=['FOCUSED'], text='full text')
        node.get_character_count = lambda: 20
        evidence = probe_tree(Node('window', children=[node]), AT)
        self.assertFalse(evidence['text_readback']['verifiable'])

    def test_accessibility_delta_contains_only_changed_nodes(self):
        history = History()
        initial = {'state': {'accessibility': {'status': 'available', 'nodes': [
            {'ref': 'root/1', 'name': 'before'}, {'ref': 'root/2', 'name': 'unchanged'}]}},
            'rgb': None, 'started_at': time.time(), 'observed_at': time.time()}
        first = history.add(initial)['revision']
        second = copy.deepcopy(initial)
        second['state']['accessibility']['nodes'][0]['name'] = 'after'
        history.add(second)
        body = json.loads(history.response(first, images=False)[0]['text'])
        delta = body['changes']['accessibility']
        self.assertEqual(delta['upsert_nodes'], [{'ref': 'root/1', 'name': 'after'}])
        self.assertNotIn('unchanged', json.dumps(delta))
        self.assertNotIn('state', body)

    def test_trace_counts_actual_delivered_pixels_and_utf8_bytes(self):
        d, f, sample = self.desktop()
        result = d.result_capture()
        hidden = server.result_summary(result)
        self.assertEqual(hidden['model_visible_image_pixels'], 0)
        token = json.loads(result[0]['text'])['frame_id']
        result = d.call('view_frame', {'frame_id': token})
        summary = server.result_summary(result)
        self.assertEqual(summary['model_visible_image_pixels'], 400*300)
        self.assertEqual(summary['model_visible_bytes'], len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()))
        self.assertEqual(server.result_summary([server.text_content({'text': 'é'})])['text_bytes'], len(server.text_content({'text': 'é'})['text'].encode()))

    def test_quiescence_waits_locally_with_backoff(self):
        d, f, sample = self.desktop()
        d.deadline = server.Deadline(1000)
        d.ledger = server.Ledger([{'action': 'press_key'}])
        entry = d.ledger.begin(0)
        # Real bounded sampler loop over a deterministic stable capture.
        self.assertTrue(d.settle(d.result_target, entry))
        self.assertEqual(entry['readiness'], 'quiescent')
        self.assertEqual(entry['application_accepted'], 'unverified')

    def test_quiescence_timeout_does_not_replay_or_run_next_input(self):
        d, f, sample = self.desktop()
        counter = 0
        def observe(*args, **kwargs):
            nonlocal counter
            counter += 1
            now = time.monotonic_ns()
            sample['capture'] = Capture(800, 600, bytes([counter % 255])*800*600*3, now, now+1, 'fixture')
            return [server.text_content({'freshness_satisfied': True})], sample
        d.observation_service = lambda: SimpleNamespace(observe=observe)
        with patch.object(server, 'run') as run, patch.object(d, 'result_capture', return_value=[server.text_content({})]):
            result = d.call('run_steps', {'frame_id': 'token', 'settle_timeout_ms': 70, 'steps': [
                {'action': 'press_key', 'key': 'Tab'}, {'action': 'type_text', 'text': 'never'}]})
        self.assertEqual(run.call_count, 1)
        sequence = json.loads(result[0]['text'])['sequence']
        self.assertEqual(sequence['stop_reason'], 'quiescence_timeout')
        self.assertEqual(sequence['steps'][1]['status'], 'unattempted')

    def test_paste_wait_readback_save_and_failed_acceptance(self):
        for accepted in (True, False):
            d, f, sample = self.desktop()
            sample['state']['accessibility']['text_readback'] = {'status': 'available', 'verifiable': True, 'text': 'excerpt'}
            def observation(args):
                return [server.text_content({'status': 'matched' if accepted else 'timeout', 'condition_met': accepted,
                    'condition_evidence': {'target': f['target'], 'revision': 'obs:1'}})]
            with patch.object(server, 'run') as run, patch.object(d, 'observation_content', side_effect=observation), \
                    patch.object(d, 'result_capture', return_value=[server.text_content({})]):
                result = d.call('run_steps', {'frame_id': 'token', 'settle_timeout_ms': 0, 'steps': [
                    {'action': 'press_key', 'key': 'CTRL+v'},
                    {'action': 'wait_for', 'condition': {'kind': 'text_equals', 'text': 'excerpt'}},
                    {'action': 'read_text'}, {'action': 'press_key', 'key': 'CTRL+s'}]})
            self.assertEqual(run.call_count, 2 if accepted else 1)
            seq = json.loads(result[0]['text'])['sequence']
            self.assertEqual(seq['stopped'], not accepted)
            if accepted:
                self.assertEqual(seq['steps'][2]['readback']['text'], 'excerpt')
                self.assertEqual(seq['steps'][1]['injection'], 'not_started')
            else:
                self.assertEqual(seq['steps'][3]['status'], 'unattempted')

    def test_delivery_validation_and_readonly_annotations(self):
        for tool in ('observe_window', 'wait_for', 'read_text', 'context_for_task', 'view_frame', 'cdp_read'):
            self.assertTrue(next(t for t in server.TOOLS if t['name'] == tool)['annotations']['readOnlyHint'])
        server.validate('observe_window', {'window': '0x123', 'images': 'none'})
        for request in ({'window': '0x123', 'images': False}, {'window': '0x123', 'detail': 'banana'}):
            with self.assertRaises(ValueError):
                server.validate('observe_window', request)
