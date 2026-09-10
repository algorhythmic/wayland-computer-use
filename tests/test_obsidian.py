import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from test_server import server
from cu import obsidian, app_surfaces


class TransferTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = self.root/'My vault'; self.vault.mkdir()
        self.config = self.root/'obsidian.json'
        self.config.write_text(json.dumps({'vaults': {'1234567890abcdef': {'path': str(self.vault)}}}))
        self.launches = []
        self.request = {'vault': 'My vault', 'name': 'A + café & 世界', 'content': 'Text + space & ? = % #\nSecond line 📝\n', 'operation_id': 'note-test-0001', 'timeout_ms': 0}
        self.manager = self.make_manager(self.launch)

    def make_manager(self, launch):
        return obsidian.NoteTransfers(self.root/'receipts', self.config, launch)

    def launch(self, uri):
        self.launches.append(uri)
        query = parse_qs(urlsplit(uri).query, keep_blank_values=True)
        self.assertEqual(query['vault'], ['1234567890abcdef']); self.assertNotIn('name', query)
        (self.vault/query['file'][0]).write_text(query['content'][0])
        return {'spawned': True, 'status': 'acknowledged', 'exit_code': 0}

    def test_exact_encoding_and_content_free_receipts(self):
        result = self.manager.create(**self.request)
        self.assertEqual(result['status'], 'verified'); self.assertTrue(result['action_performed'])
        self.assertNotIn('+', self.launches[0]); self.assertIn('%20', self.launches[0]); self.assertIn('%2B', self.launches[0])
        self.assertEqual(Path(result['note_path']).read_text(), self.request['content'])
        receipt = '\n'.join(p.read_text() for p in (self.root/'receipts').glob('*.json'))
        self.assertNotIn(self.request['content'], receipt)

    def test_reconnect_and_repeat_only_verify(self):
        self.manager.create(**self.request)
        manager = self.make_manager(Mock(side_effect=AssertionError('duplicate')))
        result = manager.create(**self.request)
        self.assertEqual(result['status'], 'verified'); self.assertTrue(result['reused_operation'])
        self.assertFalse(result['action_performed'])
        self.assertEqual(manager.status(self.request['operation_id'])['status'], 'verified')
        self.assertEqual(len(self.launches), 1)

    def test_late_creation_and_new_id_cannot_duplicate(self):
        launch = Mock(return_value={'spawned': True, 'status': 'pending'})
        manager = self.make_manager(launch)
        self.assertEqual(manager.create(**self.request)['status'], 'unverified')
        manager.create(**self.request)
        with self.assertRaisesRegex(ValueError, 'reserved'):
            manager.create(**{**self.request, 'operation_id': 'note-test-0002'})
        launch.assert_called_once(); self.launch(launch.call_args.args[0])
        self.assertEqual(manager.status(self.request['operation_id'])['status'], 'verified')

    def test_timeout_can_still_have_created_note(self):
        def interrupted(uri):
            self.launch(uri); raise subprocess.TimeoutExpired('launcher', .1)
        result = self.make_manager(interrupted).create(**self.request)
        self.assertEqual(result['status'], 'verified'); self.assertEqual(result['action_performed'], 'unknown')
        self.assertEqual(self.make_manager(Mock()).create(**self.request)['status'], 'verified')

    def test_changed_request_and_existing_note_not_overwritten(self):
        result = self.manager.create(**self.request)
        for changed in ({'content': 'different'}, {'name': 'another'}):
            with self.assertRaisesRegex(ValueError, 'different'):
                self.manager.create(**{**self.request, **changed})
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.manager.create(**{**self.request, 'operation_id': 'note-test-0002'})
        self.assertEqual(Path(result['note_path']).read_text(), self.request['content'])

    def test_collision_suffix_excludes_preexisting_and_detects_ambiguity(self):
        filename = self.request['name']+'.md'
        (self.vault/(self.request['name']+' 1.md')).write_text(self.request['content'])
        def renamed(uri):
            self.launch(uri); (self.vault/filename).rename(self.vault/(self.request['name']+' 2.md'))
            return {'spawned': True, 'status': 'acknowledged'}
        result = self.make_manager(renamed).create(**self.request)
        self.assertEqual(result['status'], 'verified'); self.assertTrue(result['note_path'].endswith(' 2.md'))
        (self.vault/filename).write_text(self.request['content'])
        self.assertEqual(self.manager.status(self.request['operation_id'])['status'], 'ambiguous')

    def test_changed_content_and_symlinks_or_fifo_are_not_verified(self):
        result = self.manager.create(**self.request); target = Path(result['note_path'])
        target.write_text('partial')
        self.assertEqual(self.manager.status(self.request['operation_id'])['status'], 'unverified')
        self.manager.create(**self.request); self.assertEqual(len(self.launches), 1)
        target.unlink(); outside = self.root/'outside.md'; outside.write_text(self.request['content']); target.symlink_to(outside)
        self.assertEqual(self.manager.status(self.request['operation_id'])['status'], 'unverified')
        target.unlink(); os.mkfifo(target)
        self.assertEqual(self.manager.status(self.request['operation_id'])['status'], 'unverified')

    def test_concurrent_repeat_observes_reservation_before_dispatch_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(uri):
            entered.set(); release.wait(3); return self.launch(uri)
        with concurrent.futures.ThreadPoolExecutor() as executor:
            first = executor.submit(self.make_manager(delayed).create, **self.request)
            self.assertTrue(entered.wait(2))
            second = self.make_manager(Mock(side_effect=AssertionError('duplicate'))).create(**self.request)
            self.assertEqual(second['status'], 'unverified')
            release.set(); self.assertEqual(first.result(3)['status'], 'verified')
        self.assertEqual(len(self.launches), 1)

    def test_crash_between_reservation_and_receipt_stays_closed(self):
        original = self.manager.write
        def crash(name, value):
            if name.startswith('op-'): raise OSError('simulated interruption')
            original(name, value)
        with patch.object(self.manager, 'write', side_effect=crash), self.assertRaises(OSError):
            self.manager.create(**self.request)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.manager.create(**self.request)
        self.assertEqual(self.launches, [])

    def test_known_failed_spawn_has_no_submission(self):
        failed = Mock(return_value={'spawned': False, 'status': 'not_started', 'errno': 2})
        result = self.make_manager(failed).create(**self.request)
        self.assertEqual(result['status'], 'not_started'); self.assertFalse(result['action_performed'])
        self.make_manager(failed).create(**self.request); failed.assert_called_once()
        self.assertEqual(self.manager.create(**{**self.request, 'operation_id': 'note-test-0002'})['status'], 'verified')

    def test_invalid_requests_never_launch(self):
        for change in ({'name': '../outside'}, {'name': 'A\\B'}, {'name': 'Bad#heading'}, {'name': '.hidden'},
                       {'content': 'bad\0text'}, {'content': 'x'*32001}, {'operation_id': '../receipt'},
                       {'vault': str(self.vault)}, {'timeout_ms': -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.manager.create(**{**self.request, **change})
        self.assertEqual(self.launches, [])

    def test_registry_ambiguity_and_movement(self):
        other = self.root/'other'/'My vault'; other.mkdir(parents=True)
        self.config.write_text(json.dumps({'vaults': {'1234567890abcdef': {'path': str(self.vault)}, 'other-id': {'path': str(other)}}}))
        with self.assertRaisesRegex(ValueError, 'ambiguous'): self.manager.create(**self.request)
        self.manager.create(**{**self.request, 'vault': '1234567890abcdef'})
        self.config.write_text(json.dumps({'vaults': {'1234567890abcdef': {'path': str(other)}}}))
        with self.assertRaisesRegex(ValueError, 'moved'): self.manager.status(self.request['operation_id'])

    def test_receipt_lock_is_bounded_and_blocks_before_launch(self):
        self.manager.state_ready(create=True)
        fd = os.open(self.manager.root/'.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaisesRegex(TimeoutError, 'busy'):
                with self.manager.locked(timeout_s=.01):
                    self.fail('lock unexpectedly acquired')
        finally:
            os.close(fd)
        self.assertEqual(self.launches, [])

    def test_diagnostic_text_is_not_retained_in_receipts(self):
        def launch(uri):
            result = self.launch(uri)
            result['diagnostic'] = 'partial echoed note body'
            return result
        self.make_manager(launch).create(**self.request)
        self.assertNotIn('partial echoed', '\n'.join(p.read_text() for p in (self.root/'receipts').glob('*.json')))

    def test_mcp_validation_and_replay_action_metadata(self):
        server.validate('obsidian_create_note', self.request)
        for name, args in [('obsidian_note_status', {'operation_id': 'bad'}),
                           ('obsidian_create_note', {**self.request, 'timeout_ms': True})]:
            with self.assertRaises(ValueError): server.validate(name, args)
        self.assertTrue(next(t for t in server.TOOLS if t['name']=='obsidian_note_status')['annotations']['readOnlyHint'])
        desktop=server.Desktop(); self.addCleanup(desktop.close)
        with patch.object(server, 'NoteTransfers', return_value=self.manager), patch.object(server, 'desktop_locked', return_value=False):
            body=json.loads(desktop.call('obsidian_create_note', self.request)[0]['text'])
            self.assertTrue(body['action_performed']); self.assertEqual(body['status'], 'verified')
            self.assertFalse(json.loads(desktop.call('obsidian_create_note', self.request)[0]['text'])['action_performed'])


class LaunchTests(unittest.TestCase):
    def test_inherited_stderr_does_not_delay_launcher_acknowledgement(self):
        real_popen = subprocess.Popen
        def launch(argv, **kwargs):
            program='import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(.8)"])'
            return real_popen([sys.executable,'-c',program], **kwargs)
        with patch.object(app_surfaces.subprocess, 'Popen', side_effect=launch):
            start=time.monotonic(); result=app_surfaces.dispatch_uri('https://example.com/', wait_ms=300)
        self.assertLess(time.monotonic()-start,.6)
        self.assertTrue(result['spawned']); self.assertEqual(result['status'],'acknowledged')

    def test_pending_launcher_is_not_killed_or_replayed(self):
        real_popen = subprocess.Popen; processes=[]
        def launch(argv, **kwargs):
            p=real_popen([sys.executable,'-c','import time;time.sleep(.2)'],**kwargs); processes.append(p); return p
        with patch.object(app_surfaces.subprocess,'Popen',side_effect=launch) as spawn:
            result=app_surfaces.dispatch_uri('https://example.com/',wait_ms=10)
        self.assertEqual(result['status'],'pending'); spawn.assert_called_once()
        self.assertIsNone(processes[0].poll()); processes[0].wait()

    def test_missing_launcher_reports_not_started(self):
        with patch.object(app_surfaces.subprocess,'Popen',side_effect=FileNotFoundError(2,'missing')):
            result=app_surfaces.dispatch_uri('https://example.com/')
        self.assertFalse(result['spawned']); self.assertEqual(result['errno'],2)
