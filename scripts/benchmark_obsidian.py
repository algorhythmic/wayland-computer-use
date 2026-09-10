#!/usr/bin/env python3
"""Real Obsidian cold/warm URI benchmark in an isolated profile and vault.

Creates disposable notes only. Measures fresh MCP calls, exact saved content,
repeated calls, and reconnect reconciliation. No user's vault/settings are edited.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

from benchmark_handoff_e2e import MCP
import server

ROOT = Path(__file__).resolve().parents[1]


def owned_processes(marker):
    matches = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == os.getuid() and ('WCU_OBSIDIAN_FIXTURE_ID='+marker).encode() in (entry/'environ').read_bytes().split(b'\0'):
                matches.append(int(entry.name))
        except (OSError, ProcessLookupError):
            pass
    return matches


def stop_owned(marker):
    for pid in owned_processes(marker):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.monotonic()+3
    while owned_processes(marker) and time.monotonic() < end:
        time.sleep(.05)
    for pid in owned_processes(marker):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--server', type=Path, default=ROOT/'scripts/server.py')
    args = parser.parse_args()
    if not 2 <= args.trials <= 20:
        parser.error('--trials must be 2-20 (one cold, remaining warm)')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'incomplete', 'scope': 'Real Obsidian in isolated profile/vault; excludes model inference and approval time', 'trials': [], 'failures': []}
    save = lambda: args.output.write_text(json.dumps(report, indent=2)+'\n')
    marker = uuid.uuid4().hex
    client = None
    control = server.Desktop()
    server.session_env()
    original = control.hypr('activewindow').get('address')
    setup_start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='wcu-obsidian-benchmark-') as temporary:
        fixture = Path(temporary)
        config, vault = fixture/'config', fixture/'WCU Test Vault'
        (config/'obsidian').mkdir(parents=True); (vault/'.obsidian').mkdir(parents=True)
        (vault/'Elsewhere').mkdir()
        (vault/'.obsidian/app.json').write_text(json.dumps({'newFileLocation': 'folder', 'newFileFolderPath': 'Elsewhere'}))
        (config/'obsidian/obsidian.json').write_text(json.dumps({'vaults': {'1234567890abcdef': {'path': str(vault), 'ts': int(time.time()*1000), 'open': True}}}))
        (config/'mimeapps.list').write_text('[Default Applications]\nx-scheme-handler/obsidian=obsidian.desktop\n')
        environment = {'XDG_CONFIG_HOME': str(config), 'XDG_DATA_HOME': str(fixture/'data'),
                       'XDG_CACHE_HOME': str(fixture/'cache'), 'XDG_STATE_HOME': str(fixture/'state'),
                       'WCU_NOTE_STATE_DIR': str(fixture/'receipts'), 'WCU_OBSIDIAN_FIXTURE_ID': marker,
                       'WAYLAND_CU_DEBUG_DIR': ''}
        command = [sys.executable, str(args.server.resolve())]
        try:
            client = MCP(command, environment)
            if 'obsidian_create_note' not in [t['name'] for t in client.tools]:
                raise ValueError('Selected MCP server lacks the structured note tools')
            _, _, metadata = client.call('desktop_state', {})
            report['runtime'] = metadata[0]['runtime']
            report['source'] = {'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                'server_sha256': hashlib.sha256(args.server.read_bytes()).hexdigest(),
                                'implementation_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scripts/cu/app_surfaces.py', ROOT/'scripts/cu/obsidian.py')}}
            report['setup_ms'] = (time.monotonic()-setup_start)*1000
            for trial in range(args.trials):
                request = {'vault': 'WCU Test Vault', 'name': f'WCU {trial} + café & 世界',
                           'content': f'# Fixture {trial}\n\nSpaces + plus & ampersand ? query = equal % percent # hash\nUnicode: café 世界 📝\n',
                           'operation_id': f'live-{marker}-{trial}', 'timeout_ms': 15000}
                start = time.monotonic()
                reply, _, metadata = client.call('obsidian_create_note', request)
                body = metadata[0]
                creation_measurement = client.rows[-1]
                if reply.get('isError') or body.get('status') != 'verified':
                    report['failed_result'] = metadata; save()
                    raise AssertionError('Real Obsidian note was not verified')
                note = Path(body['note_path'])
                assert note.parent == vault and note.name == request['name']+'.md'
                assert note.read_text() == request['content']
                elapsed_ms = (time.monotonic()-start)*1000
                count = len(list(vault.glob('WCU *.md')))
                again, _, repeated = client.call('obsidian_create_note', request)
                assert not again.get('isError') and repeated[0]['status'] == 'verified' and repeated[0]['action_performed'] is False
                assert len(list(vault.glob('WCU *.md'))) == count
                client.close(); client = MCP(command, environment)
                _, _, status = client.call('obsidian_note_status', {'operation_id': request['operation_id']})
                assert status[0]['status'] == 'verified' and status[0]['action_performed'] is False
                _, _, replay = client.call('obsidian_create_note', request)
                assert replay[0]['status'] == 'verified' and replay[0]['action_performed'] is False
                assert len(list(vault.glob('WCU *.md'))) == trial+1
                row = {'trial': trial, 'app_state': 'cold' if trial == 0 else 'warm', 'elapsed_ms': elapsed_ms,
                       'artifact_verified': True, 'launch': body['launch'], 'creation_tool_calls': 1, 'validation_tool_calls': 3,
                       'model_visible_image_pixels': creation_measurement['model_visible_image_pixels'],
                       'image_count': len(creation_measurement['images']),
                       'creation_result_bytes': len(json.dumps(reply, ensure_ascii=False, separators=(',', ':')).encode()),
                       'duplicate_count': 0, 'repeat_verified': True, 'reconnect_verified': True,
                       'root_destination_verified_despite_folder_preference': True}
                report['trials'].append(row); save(); print(json.dumps(row), flush=True)
            report['status'] = 'complete'
            report['cold_ms'] = report['trials'][0]['elapsed_ms']
            report['warm_median_ms'] = statistics.median(t['elapsed_ms'] for t in report['trials'][1:])
            save()
        except BaseException as exc:
            report['status'] = 'failed'; report['failures'].append(type(exc).__name__+': '+str(exc)); save()
            raise
        finally:
            if client:
                client.close()
            stop_owned(marker)
            if original and any(w['address'] == original for w in control.hypr('clients')):
                control.dispatch('focuswindow', 'address:'+original)
            control.close()
    print(json.dumps({'report': str(args.output), 'status': report['status'], 'cold_ms': report['cold_ms'], 'warm_median_ms': report['warm_median_ms']}))


if __name__ == '__main__':
    main()
