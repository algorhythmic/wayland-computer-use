#!/usr/bin/env python3
"""Paired live WCU task benchmark through fresh stdio MCP connections.

Creates disposable GTK and Chromium windows. Inputs only target those fixtures;
all final task artifacts are checked independently. This is a deterministic
pipeline benchmark, not a model-mediated speed or accuracy claim.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time

import server

ROOT=Path(__file__).resolve().parents[1]
SOURCE_TEXT='WCU paired transfer: verified local context and guarded execution.'


class Lines:
    def __init__(self,process):
        self.process=process;self.queue=queue.Queue()
        def receive():
            for line in process.stdout:
                try:self.queue.put(json.loads(line))
                except ValueError:self.queue.put({'transport_error':'invalid_json'})
        self.thread=threading.Thread(target=receive,daemon=True);self.thread.start()
    def send(self,value,timeout=10):
        self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()
        return self.queue.get(timeout=timeout)
    def close(self):
        if self.process.poll() is None:self.process.terminate()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        self.process.stdin.close();self.thread.join(timeout=3);self.process.stdout.close()


class MCP(Lines):
    def __init__(self,command,environment=None):
        super().__init__(subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','WAYLAND_CU_TRACE_DIR':'',**(environment or {})}))
        self.sequence=0;self.rows=[]
        self.identity=self.rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'wcu-handoff-benchmark','version':'1'}})
        self.tools=self.rpc('tools/list',{})['tools']
    def rpc(self,method,params):
        self.sequence+=1
        reply=self.send({'jsonrpc':'2.0','id':self.sequence,'method':method,'params':params},140)
        if reply.get('id')!=self.sequence or 'result' not in reply:raise RuntimeError('MCP transport outcome unknown; never replay input')
        return reply['result']
    def call(self,name,args):
        start=time.perf_counter_ns();result=self.rpc('tools/call',{'name':name,'arguments':args});elapsed=(time.perf_counter_ns()-start)/1e6
        metadata=[json.loads(c['text']) for c in result.get('content',[]) if c['type']=='text']
        images=[]
        for c in result.get('content',[]):
            if c['type']=='image':
                png=base64.b64decode(c['data']);width,height=struct.unpack('>II',png[16:24])
                images.append({'width':width,'height':height,'png_bytes':len(png),'detail_hint':c.get('_meta',{}).get('codex/imageDetail')})
        frame=next((m['frame_id'] for m in metadata if 'frame_id' in m),None)
        sequence=next((m['sequence'] for m in metadata if 'sequence' in m),None)
        self.rows.append({'tool':name,'elapsed_ms':elapsed,'response_bytes':len(json.dumps(result).encode()),'images':images,
                          'is_error':result.get('isError',False),'sequence':sequence})
        return result,frame,metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks',nargs='+',choices=['form','note','canvas'],default=['form','note','canvas']);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--trials',type=int,default=3)
    parser.add_argument('--fixture-only',action='store_true',help='Launch for initial model inspection; stdin ends fixture')
    parser.add_argument('--baseline',type=Path,help='Frozen old source server for interruption comparison')
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    server.session_env();control=server.Desktop()
    original=control.hypr('activewindow').get('address')
    fixture=Lines(subprocess.Popen([sys.executable,str(ROOT/'tests/handoff_fixture.py'),'--artifacts',str(args.output.parent/'artifacts')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,
                                  env=dict(os.environ,GDK_BACKEND='wayland')))
    browser=None;client=None;baseline=None
    report={'scope':'Deterministic desktop task E2E through fresh MCP; excludes model inference and approvals',
            'status':'incomplete','trials':[],'failures':[],'source':{},'started_at':time.time()}
    def save():args.output.write_text(json.dumps(report,indent=2)+'\n')
    try:
        ready=fixture.queue.get(timeout=10);assert ready['ready']
        if args.fixture_only:
            print(json.dumps(ready),flush=True)
            input();return
        client=MCP([sys.executable,str(ROOT/'scripts/dev_host.py')])
        result,_,meta=client.call('desktop_state',{})
        runtime=meta[0]['runtime'];published=json.loads((ROOT/'.dev/current.json').read_text())['revision']
        assert runtime['identity']['revision']==published
        skill_hash=hashlib.sha256((ROOT/'skills/wayland-computer-use/SKILL.md').read_bytes()).hexdigest()
        assert runtime['identity']['published_skill_sha256']==skill_hash
        skills={str(p.relative_to(ROOT/'skills/wayland-computer-use')):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT/'skills/wayland-computer-use').rglob('*') if p.is_file()}
        assert runtime['identity']['published_skill_files_sha256']==skills
        report['source']={'connected_runtime':runtime,'published_revision':published,'harness_skill_sha256':skill_hash,
            'skill_loading':'Harness records the current skill file; this is not evidence of rediscovery in the existing chat',
            'checkout':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'tools':[t['name'] for t in client.tools]}
        if args.baseline:
            baseline=MCP([sys.executable,str(args.baseline)])
            report['source']['baseline_server_sha256']=hashlib.sha256(args.baseline.read_bytes()).hexdigest()
        page=args.output.parent/'source.html'
        page.write_text('<!doctype html><title>WCU Handoff Source</title><style>body{font:24px sans-serif;padding:40px}textarea{width:90%;height:200px;font:22px monospace}</style><h1>Disposable browser-to-note source</h1><textarea id="source" autofocus>'+SOURCE_TEXT+'</textarea><script>source.focus();source.select()</script>')
        browser=subprocess.Popen(['chromium','--user-data-dir='+str((args.output.parent/'chromium-profile').resolve()),'--no-first-run','--no-default-browser-check','--disable-background-networking',page.resolve().as_uri()],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        end=time.monotonic()+15
        browser_window=None
        while time.monotonic()<end:
            browser_window=next((w for w in control.hypr('clients') if w.get('title','').startswith('WCU Handoff Source')),None)
            if browser_window:break
            time.sleep(.1)
        assert browser_window,'Disposable browser did not map'
        def fixture_window():
            return next(w for w in control.hypr('clients') if w['pid']==ready['pid'] and w['title']=='WCU Handoff Fixture')
        def focus(c,address):
            response,frame,_=c.call('focus_window',{'address':address})
            if response.get('isError'):raise RuntimeError('Fixture focus failed')
            return frame
        def execute(c,steps,frame,variant):
            if variant=='single_monitor':
                for step in steps:
                    action=step['action'];params={k:v for k,v in step.items() if k not in ('action','expect')}
                    if action!='focus_window':params['frame_id']=frame
                    else:params.pop('after',None)
                    response,frame,_=c.call(action,params)
                    if response.get('isError'):raise RuntimeError('Fixture input failed')
                    if action=='focus_window' and step.get('after'):
                        response,frame,checks=c.call('wait_for',{'window':step['address'],**step['after']})
                        assert not response.get('isError') and checks[0].get('condition_met'),'Focused control not ready'
            else:
                response,frame,_=c.call('run_steps',{'frame_id':frame,'duration_ms':20000,'result_view':{'kind':'target' if variant=='sequence_target' else 'monitor'},'steps':steps})
                if response.get('isError') or any(m.get('sequence',{}).get('stopped') for m in [json.loads(v['text']) for v in response['content'] if v['type']=='text']):
                    report['failed_calls']=c.rows[-1:]
                    report['failed_fixture_state']=fixture.send({'op':'read'})
                    for block in response['content']:
                        if block['type']=='image':
                            (args.output.parent/'failed-result.png').write_bytes(base64.b64decode(block['data']))
                    observation_result,_,observation_meta=c.call('observe_window',{'window':fixture_window()['address'],'channels':['metadata','accessibility']})
                    report['failed_observation']=observation_meta
                    report['failed_metadata']=[json.loads(v['text']) for v in response['content'] if v['type']=='text' and 'frame_id' not in json.loads(v['text'])]
                    save()
                    raise RuntimeError('Fixture sequence stopped')
            return response
        for task in args.tasks:
            for trial in range(args.trials):
                variants=['single_monitor','sequence_monitor','sequence_target']
                if trial%2:variants.reverse()
                for variant in variants:
                    assert fixture.send({'op':'reset','mode':task})['reset']==task
                    address=fixture_window()['address']
                    frame=focus(client,browser_window['address'] if task=='note' else address)
                    if task=='form':
                        steps=[{'action':'type_text','text':'Alpha 123'},{'action':'press_key','key':'Tab'},
                               {'action':'type_text','text':'B42'},{'action':'press_key','key':'CTRL+s',
                               'after':{'condition':{'kind':'accessible','name':'Saved form'},'timeout_ms':3000}}]
                    elif task=='note':
                        steps=[{'action':'press_key','key':'CTRL+a'},{'action':'press_key','key':'CTRL+c'},
                               {'action':'focus_window','address':address,'expect':{'kind':'window','address':address},
                                'after':{'condition':{'kind':'accessible','name':'Test note','state':'focused'},'timeout_ms':1000}},
                               {'action':'press_key','key':'CTRL+v'},{'action':'press_key','key':'CTRL+s',
                                'after':{'condition':{'kind':'accessible','name':'Saved note'},'timeout_ms':3000}}]
                    else:steps=[{'action':'press_key','key':'space'}]
                    offset=len(client.rows);start=time.perf_counter_ns()
                    if task=='note':
                        execute(client,steps[:-1],frame,variant)
                        # Clipboard paste is asynchronous. End this batch and use
                        # task-scoped readback before issuing the save action.
                        response,_,observed=client.call('observe_window',{'window':address,'channels':['metadata','accessibility']})
                        state_body=observed[0]
                        nodes=state_body['state']['accessibility'].get('nodes',[])
                        matching=[n for n in nodes if n['name']=='Test note' and n['role']=='text']
                        assert len(matching)==1,'Note readback requires a unique text control'
                        selector={k:matching[0][k] for k in ('ref','name','role')}
                        selector['revision']=state_body['revision']
                        response,frame,readback=client.call('wait_for',{'window':address,'read_text':selector,
                            'condition':{'kind':'text_equals','text':SOURCE_TEXT},'timeout_ms':3000})
                        if response.get('isError') or not readback[0].get('condition_met'):
                            report['failed_readback']=readback
                            raise AssertionError('Paste readback failed')
                        assert frame,'Readback did not return a fresh actionable frame'
                        response,_,_=client.call('press_key',{'frame_id':frame,'key':'CTRL+s',
                            'result_view':{'kind':'target' if variant=='sequence_target' else 'monitor'},
                            'after':steps[-1]['after']})
                        assert not response.get('isError'),'Save rejected after readback'
                    else:
                        execute(client,steps,frame,variant)
                    final=fixture.send({'op':'read'})
                    elapsed=(time.perf_counter_ns()-start)/1e6
                    valid=(final['name']=='Alpha 123' and final['code']=='B42') if task=='form' else final['note']==SOURCE_TEXT if task=='note' else final['canvas'] is True
                    assert valid,(task,final)
                    artifact=json.loads(Path(final['artifact']).read_text());assert artifact['mode']==task
                    assert (artifact['name']=='Alpha 123' and artifact['code']=='B42') if task=='form' else artifact['note']==SOURCE_TEXT if task=='note' else artifact['canvas'] is True
                    rows=client.rows[offset:]
                    report['trials'].append({'task':task,'trial':trial,'variant':variant,'elapsed_ms':elapsed,'artifact_verified':True,
                        'tool_calls':len(rows),'input_tool_calls':sum(r['tool'] in ('type_text','press_key','run_steps','focus_window') for r in rows),'image_count':sum(len(r['images']) for r in rows),
                        'delivered_png_bytes':sum(i['png_bytes'] for r in rows for i in r['images']),
                        'guard_rejections':sum(r['is_error'] for r in rows),'calls':rows})
                    save();print(json.dumps({'task':task,'trial':trial,'variant':variant,'ms':round(elapsed,1)}),flush=True)
        for c,name in [(client,'current')]+([(baseline,'baseline')] if baseline else []):
            for trial in range(args.trials):
                fixture.send({'op':'reset','mode':'interrupt'});frame=focus(c,fixture_window()['address'])
                offset=len(c.rows);response,_,meta=c.call('type_text',{'frame_id':frame,'text':'x'*81})
                time.sleep(.1);final=fixture.send({'op':'read'})
                assert final['interrupted']
                report['trials'].append({'task':'focus_interruption_natural','variant':name,'trial':trial,'target_characters':len(final['name']),
                    'decoy_characters':len(final['decoy']),'guard_rejected':response.get('isError',False),'calls':c.rows[offset:]})
                save();print(json.dumps({'task':'focus_interruption','variant':name,'decoy_characters':len(final['decoy'])}),flush=True)
        # Equal synchronization for both executors makes the tested ordering
        # explicit: real app-generated focus change finishes before the first
        # wtype invocation returns. This isolates the between-segment contract;
        # the ungated natural race above remains a separate measurement.
        gated=args.output.parent/'intersegment-gate';gated.mkdir(exist_ok=True)
        marker=(gated/'focus-changed').resolve();armed=(gated/'armed').resolve()
        wrapper=gated/'wtype'
        wrapper.write_text('#!'+sys.executable+'\n'+
            'import pathlib,subprocess,sys,time\n'+
            'rc=subprocess.run('+repr([shutil.which('wtype')])+'+sys.argv[1:]).returncode\n'+
            'marker=pathlib.Path('+repr(str(marker))+');armed=pathlib.Path('+repr(str(armed))+')\n'+
            'if armed.exists() and sys.argv[1:]==["-"]:\n'+
            ' end=time.monotonic()+2\n'+
            ' while not marker.exists() and time.monotonic()<end: time.sleep(.001)\n'+
            ' if not marker.exists():sys.exit(75)\n'+
            'sys.exit(rc)\n')
        wrapper.chmod(0o700)
        for command,name in [([sys.executable,str(ROOT/'scripts/dev_host.py')],'current')]+([( [sys.executable,str(args.baseline)],'baseline')] if args.baseline else []):
            gated_client=MCP(command,{'PATH':str(gated.resolve())+os.pathsep+os.environ['PATH']})
            try:
                for trial in range(args.trials):
                    marker.unlink(missing_ok=True);armed.write_text('armed')
                    fixture.send({'op':'reset','mode':'interrupt','focus_marker':str(marker)})
                    frame=focus(gated_client,fixture_window()['address']);offset=len(gated_client.rows)
                    response,_,_=gated_client.call('type_text',{'frame_id':frame,'text':'x'*81})
                    final=fixture.send({'op':'read'});assert marker.exists()
                    if name=='current':assert final['decoy']=='' and response.get('isError'),final
                    report['trials'].append({'task':'focus_interruption_between_segments','variant':name,'trial':trial,
                        'target_characters':len(final['name']),'decoy_characters':len(final['decoy']),
                        'guard_rejected':response.get('isError',False),'calls':gated_client.rows[offset:]})
                    save()
            finally:
                armed.unlink(missing_ok=True);gated_client.close()
        report['summary']={}
        for task in args.tasks:
            report['summary'][task]={}
            for variant in ('single_monitor','sequence_monitor','sequence_target'):
                rows=[r for r in report['trials'] if r['task']==task and r['variant']==variant]
                report['summary'][task][variant]={k:statistics.median(r[k] for r in rows) for k in ('elapsed_ms','tool_calls','input_tool_calls','delivered_png_bytes','image_count')}
        report['status']='complete';save()
        print(json.dumps({'report':str(args.output),'summary':report['summary']}),flush=True)
    except BaseException as exc:
        try:report['last_fixture_state']=fixture.send({'op':'read'},timeout=2)
        except Exception:report['last_fixture_state']={'status':'unavailable'}
        report['last_calls']=client.rows[-3:] if client else []
        report['status']='failed';report['failures'].append(type(exc).__name__+': '+str(exc)[:1000]);save();raise
    finally:
        if client:client.close()
        if baseline:baseline.close()
        fixture.close()
        if browser:
            browser.terminate()
            try:browser.wait(timeout=5)
            except subprocess.TimeoutExpired:browser.kill();browser.wait(timeout=3)
        if original and any(w['address']==original for w in control.hypr('clients')):
            control.dispatch('focuswindow','address:'+original)
        control.close()


if __name__=='__main__':main()
