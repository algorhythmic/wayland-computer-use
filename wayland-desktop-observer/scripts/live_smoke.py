#!/usr/bin/env python3
"""Opt-in read-only MCP smoke test. Prints metrics, never UI text or pixels."""
import json
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    process = subprocess.Popen([sys.executable, str(ROOT/'scripts/observer_server.py')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    counter = 0
    def request(method, params=None):
        nonlocal counter
        counter += 1
        process.stdin.write(json.dumps({'jsonrpc':'2.0','id':counter,'method':method,'params':params or {}})+'\n')
        process.stdin.flush()
        if not select.select([process.stdout], [], [], 35)[0]:
            raise TimeoutError('MCP response timeout')
        response = json.loads(process.stdout.readline())
        assert response['id'] == counter
        result = response['result']
        if result.get('isError'):
            raise RuntimeError(result['content'][0]['text'])
        return result
    def call(name, **args):
        result = request('tools/call', {'name':name, 'arguments':args})
        return json.loads(result['content'][0]['text']), result['content'][1:]
    try:
        request('initialize', {'protocolVersion':'2025-06-18', 'capabilities':{},
                               'clientInfo':{'name':'observer-smoke','version':'1'}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        tools = request('tools/list')['tools']
        assert {t['name'] for t in tools} == {'observe','wait_for_change','wait_for','stop_observing'}
        state, images = call('observe', images=False)
        assert not images and not state['actionable']
        assert 'windows' in state['state'], state['state']
        print(json.dumps({'check':'desktop','windows':len(state['state']['windows'])}), flush=True)
        active = state['state']['active_window']
        if active:
            start = time.monotonic()
            state, images = call('observe', window=active)
            visual = state['state'].get('visual', {})
            accessibility = state['state'].get('accessibility', {})
            print(json.dumps({'check':'window','elapsed_ms':round((time.monotonic()-start)*1000),
                              'visual_status':visual.get('status'),'visual_reason':visual.get('reason'),
                              'image_count':len(images),'accessibility_status':accessibility.get('status'),
                              'accessibility_reason':accessibility.get('reason'),
                              'accessible_nodes':len(accessibility.get('nodes',[])),
                              'event_source':state['event_source']}), flush=True)
            delta, images = call('observe',window=active,since_revision=state['revision'])
            assert delta['mode']=='delta'
            print(json.dumps({'check':'delta','changed_fields':list(delta['changes']),
                              'image_count':len(images),'fresh':delta['fresh_sample']}), flush=True)
            waited, _ = call('wait_for_change',window=active,since_revision=delta['revision'],images=False,timeout_ms=1000)
            print(json.dumps({'check':'wait','timed_out':waited['wait_timed_out']}), flush=True)
        stopped,_=call('stop_observing')
        assert stopped['history_cleared']
    finally:
        process.stdin.close()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(timeout=5)


if __name__=='__main__':main()
