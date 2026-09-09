"""Stateful regression probes for the September execution handoff."""
import json
import unittest
from unittest.mock import patch

import test_server
from test_server import server


class ExecutionContract(unittest.TestCase):
    setUp = test_server.Tests.setUp
    sequence_desktop = test_server.Tests.sequence_desktop
    # Reuse desktop fixtures, not the inherited test methods.
    def test_focus_change_between_text_segments_stops_and_keeps_ledger(self):
        d, f, state = self.sequence_desktop()
        def inject(*args, **kwargs):
            state['active'] = '0x777'
        with patch.object(server, 'run', side_effect=inject) as run, \
                patch.object(d, 'screenshot', side_effect=RuntimeError('capture unavailable')):
            with self.assertRaises(server.ActionRejected) as raised:
                d.call('run_steps', {'frame_id': 'token', 'steps': [
                    {'action': 'type_text', 'text': 'x'*81}, {'action': 'press_key', 'key': 'Return'}]})
        self.assertEqual(run.call_count, 1)
        meta = json.loads(raised.exception.content[0]['text'])
        step = meta['sequence']['steps'][0]
        self.assertEqual(step['submitted_segments'], 1)
        self.assertFalse(step['in_flight_unknown'])
        self.assertEqual(meta['sequence']['steps'][1]['status'], 'unattempted')

    def test_matched_readiness_does_not_adopt_racing_focus(self):
        d, f, state = self.sequence_desktop()
        def observation(args):
            state['active'] = '0x777'
            return [server.text_content({'status': 'matched', 'condition_met': True,
                'condition_evidence': {'target': {'address': '0x123'}, 'revision': 'obs:1'}})]
        with patch.object(d, 'observation_content', side_effect=observation), \
                patch.object(server, 'run') as run, patch.object(d, 'screenshot', return_value=[server.text_content({})]):
            result = d.call('run_steps', {'frame_id': 'token', 'steps': [
                {'action': 'type_text', 'text': 'never',
                 'expect': {'kind': 'window', 'class': 'Test', 'focused': True}}]})
        run.assert_not_called()
        self.assertTrue(json.loads(result[0]['text'])['sequence']['stopped'])

    def test_second_backend_failure_does_not_inherit_completion(self):
        d, f, state = self.sequence_desktop()
        with patch.object(server, 'run', side_effect=[None, RuntimeError('backend down')]), \
                patch.object(d, 'screenshot', side_effect=RuntimeError('capture unavailable')):
            with self.assertRaises(server.ActionRejected) as raised:
                d.call('run_steps', {'frame_id': 'token', 'steps': [
                    {'action': 'press_key', 'key': 'Tab'}, {'action': 'type_text', 'text': 'x'},
                    {'action': 'press_key', 'key': 'Return'}]})
        meta = json.loads(raised.exception.content[0]['text'])
        self.assertEqual(meta['action_performed'], 'unknown')
        self.assertEqual(meta['sequence']['last_completed_action'], 0)
        self.assertTrue(meta['sequence']['steps'][1]['in_flight_unknown'])
        self.assertEqual(meta['sequence']['steps'][2]['status'], 'unattempted')

    def test_invalid_later_enum_and_top_level_after_rejected_before_input(self):
        for update in ({'steps': [{'action': 'press_key', 'key': 'Tab', 'axis': 'diagonal'}]},
                       {'after': {'condition': {'kind': 'window', 'class': 'Test'}}}):
            d, f, state = self.sequence_desktop()
            request = {'frame_id': 'token', 'steps': [{'action': 'press_key', 'key': 'Tab'}], **update}
            with patch.object(server, 'run') as run, self.assertRaises(ValueError):
                d.call('run_steps', request)
            run.assert_not_called()

    def test_lock_between_segments_and_ordinary_focus_loss(self):
        for name, change in [('run_steps', 'lock'), ('type_text', 'focus')]:
            d, f, state = self.sequence_desktop()
            def inject(*args, **kwargs):
                state['locked'] = change == 'lock'
                state['active'] = '0x777' if change == 'focus' else '0x123'
            request = {'frame_id': 'token', 'text': 'x'*81} if name == 'type_text' else {
                'frame_id': 'token', 'steps': [{'action': 'type_text', 'text': 'x'*81}]}
            with patch.object(server, 'desktop_locked', side_effect=lambda: state['locked']), \
                    patch.object(server, 'run', side_effect=inject) as run, \
                    patch.object(d, 'screenshot', return_value=[]):
                with self.assertRaises(server.ActionRejected) as raised:
                    d.call(name, request)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads(raised.exception.content[0]['text'])['sequence']['steps'][0]['submitted_segments'], 1)

    def test_coordinate_wait_revalidates_age_geometry_and_pixels(self):
        for change in ('age', 'geometry', 'pixels'):
            d, f, state, args = test_server.Tests.approval_desktop(self)
            state['active'] = '0x123'
            original_target = dict(f['target'])
            def observation(args):
                if change == 'age':
                    f['time'] -= 121
                elif change == 'geometry':
                    original = d.hypr
                    d.hypr = lambda command: [dict(original_target, size=[900, 600])] if command == 'clients' else original(command)
                return [server.text_content({'status': 'matched', 'condition_met': True,
                    'condition_evidence': {'target': original_target, 'revision': 'obs:1'}})]
            pixels = bytearray(f['visual'][2]); pixels[0] = 255
            with patch.object(d, 'observation_content', side_effect=observation), \
                    patch.object(d, 'target_pixels', return_value=(*f['visual'][:2], bytes(pixels))), \
                    patch.object(d, 'mouse') as mouse, patch.object(d, 'screenshot', return_value=[]):
                result = d.call('run_steps', {'frame_id': 'token', 'steps': [
                    {'action': 'pointer', 'x': 100, 'y': 100, 'expect': {'kind': 'window', 'address': '0x123'}}]})
            mouse.assert_not_called()
            self.assertTrue(json.loads(result[0]['text'])['sequence']['stopped'])

    def test_explicit_unique_transition_continues_without_adopting_other_focus(self):
        for race in (False, True):
            d, f, state = self.sequence_desktop()
            def inject(*args, **kwargs):
                state['active'] = '0x777'
            def observation(args):
                if race:
                    state['active'] = '0x999'
                return [server.text_content({'status': 'matched', 'condition_met': True,
                    'condition_evidence': {'target': {'address': '0x777'}, 'revision': 'obs:2'}})]
            with patch.object(server, 'run', side_effect=inject) as run, patch.object(d, 'observation_content', side_effect=observation), \
                    patch.object(d, 'screenshot', return_value=[]):
                d.call('run_steps', {'frame_id': 'token', 'steps': [
                    {'action': 'press_key', 'key': 'Tab', 'transition': 'matched_window',
                     'after': {'condition': {'kind': 'window', 'address': '0x777', 'focused': True}}},
                    {'action': 'type_text', 'text': 'safe'}]})
            self.assertEqual(run.call_count, 1 if race else 2)

    def test_shared_deadline_caps_waits_and_stops_remaining_steps(self):
        d, f, state = self.sequence_desktop()
        waits = []
        def observation(args):
            waits.append(args['timeout_ms'])
            if len(waits) == 2:
                d.deadline.end -= 121
            return [server.text_content({'status': 'matched', 'condition_met': True,
                'condition_evidence': {'target': {'address': '0x123'}, 'revision': 'obs:1'}})]
        condition = {'condition': {'kind': 'window', 'address': '0x123'}, 'timeout_ms': 30000}
        with patch.object(d, 'observation_content', side_effect=observation), patch.object(server, 'run') as run, \
                patch.object(d, 'screenshot', return_value=[]):
            result = d.call('run_steps', {'frame_id': 'token', 'duration_ms': 31000, 'steps': [
                {'action': 'wait', 'after': condition}, {'action': 'wait', 'after': condition},
                {'action': 'type_text', 'text': 'never'}]})
        self.assertGreater(waits[0], 29000)
        self.assertLessEqual(max(waits), 30000)
        run.assert_not_called()
        self.assertEqual(json.loads(result[0]['text'])['sequence']['steps'][2]['status'], 'unattempted')

    def test_context_tool_budget_includes_its_entire_text(self):
        d, f, state = self.sequence_desktop()
        from test_context_contract import fixture
        with patch.object(server.SnapshotStore, 'load', return_value=fixture()):
            result = d.call('context_for_task', {'intent': 'address bar', 'max_bytes': 2048})
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0]['text'])
        self.assertEqual(len(result[0]['text'].encode()), payload['output']['bytes'])
        self.assertLessEqual(payload['output']['bytes'], 2048)
