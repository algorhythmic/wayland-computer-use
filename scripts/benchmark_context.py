#!/usr/bin/env python3
"""Held-out context intents under identical eligibility, closure and byte rendering."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import statistics
import time

from cu.context_records import normalize, encode, SnapshotStore
from cu.context_retrieval import context_for_task, SessionEvidence, eligibility, render
from cu.braid_client import BraidBackend


def fixtures():
    bindings={
        'chromium': [{'command':'IDC_FOCUS_LOCATION','shortcut':'CTRL+l','modes':['normal']},
                     {'command':'forbidden address bar','shortcut':'CTRL+i','modes':['insert']},
                     {'command':'intercepted address bar','shortcut':'SUPER+l','compositor_matches':[{'command':'lock'}]}],
        'herdr': [{'command':'split_vertical','shortcut':'ctrl+a then v','strokes':['ctrl+a','v'],'modes':['terminal']},
                  {'command':'colliding split terminal','shortcut':'ctrl+a then v','same_sequence_candidates':['custom'],'modes':['terminal']}],
        'neovim':[{'command':'next search match','shortcut':'n','notation':'vim','modes':['n']},
                  {'command':'previous search match','shortcut':'N','notation':'vim','modes':['n']},
                  {'command':'reverse search','shortcut':'?','notation':'vim','modes':['n']},
                  {'command':'insert letter n','shortcut':'n','notation':'vim','modes':['i']}],
        'lazyvim':[{'command':'optional explorer','shortcut':'<leader>e','notation':'vim','modes':['n'],'extra':'explorer','extra_listed_in_manifest':False}],
        'obsidian':[{'command':'file-explorer:new-file','shortcut':'CTRL+m','evidence':'vault_override'},
                    {'command':'disabled new note','shortcut':'CTRL+n','assignment':'disabled'}],
        'obs':[],
        'stale':[{'command':'stale source action','shortcut':'F1'}]}
    profiles={name:{'environment':name,'version':'fixture-v1','installed_version':'fixture-v1','version_matches':name!='stale',
        'evidence':'sanitized_fixture','sources':[{'source_id':name}], 'collected_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'coverage':'Labeled fixture, not installed application availability','bindings':rows} for name,rows in bindings.items()}
    profiles['herdr']['prefix']='ctrl+a'
    profiles['obs']['configurable_commands']=['OBSBasic.StartRecording']
    snapshot=normalize(profiles)
    cases=[('chromium','normal','go to the browser address bar','IDC_FOCUS_LOCATION',None),
           ('chromium','normal','enter URL','IDC_FOCUS_LOCATION',None),
           ('herdr','terminal','arrange side by side terminal panes','split_vertical',None),
           ('herdr','terminal','split terminal vertically','split_vertical',None),
           ('neovim','n','repeat next search match','next search match',{'shortcut':'n'}),
           ('neovim','n','previous search match','previous search match',{'shortcut':'N'}),
           ('neovim','n','reverse search','reverse search',{'shortcut':'?'}),
           ('neovim','n','optional explorer',None,{'app':'neovim','command_id':'optional explorer'}),
           ('obsidian','normal','create a new note','file-explorer:new-file',None),
           ('obs','normal','start recording',None,None),
           ('stale','normal','stale source action',None,None)]
    labels=[]
    for app,mode,intent,expected,exact in cases:
        facts={'focused_app':app,'mode':mode,'input_path':['omarchy',app],'input_path_complete':True}
        for r in snapshot['records'].values():
            if r['record_type']=='action':
                facts['available:'+r['id']]=True
                facts['config:'+r['catalog_app']]=r['evidence']['config_fingerprint']
        evidence=SessionEvidence('fixture:1',facts)
        labels.append({'intent':intent,'expected_command':expected,'exact':exact,'evidence':evidence})
    return snapshot,labels


def percentile(values,p):
    return sorted(values)[min(len(values)-1,round((len(values)-1)*p))]


def catalog_benchmark(args):
    """Timing-only installed-catalog probe; never invent live action availability."""
    snapshot=SnapshotStore(args.catalog).load()
    backend=BraidBackend(args.braid,args.output.parent/'braid-installed',hashlib.sha256(args.braid.read_bytes()).hexdigest()) if args.braid else None
    report={'scope':'In-memory installed catalog retrieval; excludes loading, model inference and desktop input',
            'catalog_revision':snapshot['revision'],'record_count':len(snapshot['records']),
            'interactive_p95_target_ms':250,'backends':{},'status':'incomplete'}
    try:
        for name in ('local','braid') if backend else ('local',):
            samples=[];sizes=[];failures=[];actual=set();cold=[]
            for app,intent in [('chromium','enter URL'),('herdr','split terminal vertically'),('neovim','previous search match'),('obsidian','create new note'),('obs','start recording')]:
                request={'intent':intent,'observation_ref':'benchmark:1','backend':name,'timeout_ms':5000,'max_bytes':8192}
                evidence=SessionEvidence('benchmark:1',{'focused_app':app})
                start=time.perf_counter_ns();context_for_task(snapshot,request,evidence,backend)
                cold.append((time.perf_counter_ns()-start)/1e6)
                for _ in range(args.repeats):
                    start=time.perf_counter_ns();p,d=context_for_task(snapshot,request,evidence,backend)
                    samples.append((time.perf_counter_ns()-start)/1e6);sizes.append(len(render(p)))
                    failures.extend(d['failures']);actual.add(d['backend'])
                    assert not p.get('actions'),'Default availability must remain unresolved'
                    assert len(render(p))<=8192
                    assert all(set(a['requires'])<=set(p['context']) for a in p.get('exploration',[]))
            report['backends'][name]={'first_query_ms':cold[0],'warm_p50_ms':statistics.median(samples),
                'warm_p95_ms':percentile(samples,.95),'median_payload_bytes':statistics.median(sizes),
                'samples':len(samples),'actual_backends':sorted(actual),'failures':failures,
                'unsupported_executable_recommendations':0,'budget_overruns':0,'incomplete_bundles':0}
        report['status']='complete'
    finally:
        if backend:backend.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--braid',type=Path)
    parser.add_argument('--catalog',type=Path,help='Timing-only probe of an installed normalized catalog, with unknown availability retained')
    parser.add_argument('--repeats',type=int,default=10)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.catalog:
        return catalog_benchmark(args)
    snapshot,labels=fixtures()
    backend=BraidBackend(args.braid,args.output.parent/'braid-context',hashlib.sha256(args.braid.read_bytes()).hexdigest()) if args.braid else None
    report={'scope':'Controlled local retrieval pipeline; no model inference or desktop actions',
            'interactive_p95_target_ms':250,'catalog_revision':snapshot['revision'],'backends':{},'status':'incomplete'}
    try:
        for name in ('substring','local','braid') if backend else ('substring','local'):
            samples=[];rows=[];violations={'ineligible_actions':0,'incomplete_bundles':0,'mixed_revisions':0,'budget_overruns':0,'incorrect_no_answer':0}
            for case in labels:
                request={'intent':case['intent'],'observation_ref':'fixture:1','max_bytes':8192,'timeout_ms':5000,'backend':name}
                if case['exact']:request['exact']=case['exact']
                evidence=case['evidence']
                # Warm publication and code paths are separate from repeated latency.
                context_for_task(snapshot,request,evidence,backend)
                outputs=[]
                for _ in range(args.repeats):
                    start=time.perf_counter_ns();payload,diagnostics=context_for_task(snapshot,request,evidence,backend)
                    samples.append((time.perf_counter_ns()-start)/1e6);outputs.append(len(render(payload)))
                    violations['budget_overruns']+=len(render(payload))>request['max_bytes']
                    violations['mixed_revisions']+=payload.get('catalog',{}).get('revision')!=snapshot['revision']
                    for action in payload.get('actions',[]):
                        violations['ineligible_actions']+=eligibility(snapshot['records'][action['id']],evidence.facts)[0]!='eligible'
                        violations['incomplete_bundles']+=not set(action['requires'])<=set(payload['context'])
                commands=[r['command_id'] for r in payload.get('actions',[])]
                expected=case['expected_command']
                if expected is None:violations['incorrect_no_answer']+=bool(commands)
                rows.append({'intent':case['intent'],'expected_command':expected,'commands':commands,'recalled':expected in commands if expected else None,
                             'status':payload['status'],'bytes':statistics.median(outputs),'backend_failures':diagnostics['failures']})
            relevant=[r for r in rows if r['expected_command']]
            report['backends'][name]={'warm_p50_ms':statistics.median(samples),'warm_p95_ms':percentile(samples,.95),
                'relevant_action_recall':sum(r['recalled'] for r in relevant)/len(relevant),'violations':violations,'cases':rows,
                'binary_sha256':backend.expected_sha256 if name=='braid' else None,'ranking_policy':'lexical, lambda=1' if name=='braid' else name}
        report['status']='complete'
        report['default_backend']='local'
        report['rollout']='Braid opt-in; this small synthetic retrieval pilot does not establish general task gains.'
    finally:
        if backend:backend.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:{x:v[x] for x in ('warm_p50_ms','warm_p95_ms','relevant_action_recall','violations')} for k,v in report['backends'].items()},indent=2))
    if any(any(v['violations'].values()) for v in report['backends'].values()):raise SystemExit(1)


if __name__=='__main__':main()
