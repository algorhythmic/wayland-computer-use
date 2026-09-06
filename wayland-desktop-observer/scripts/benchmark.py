#!/usr/bin/env python3
"""Opt-in isolated live comparison. Opens/closes one disposable GTK window.
No keyboard or pointer injection; original plugin and observer remain unchanged.
Reports local MCP timing, not model inference latency or token counts.
"""
import base64
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

ROOT = Path(__file__).resolve().parents[1]
TITLE = 'Wayland observer comparison — disposable status test'


def fixture():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib
    Gtk.init()
    window=Gtk.Window(title=TITLE);window.set_default_size(800,500)
    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=24)
    box.set_margin_top(60);box.set_margin_start(60)
    label=Gtk.Label(label='Waiting 000');label.set_halign(Gtk.Align.START)
    label.set_size_request(300,60)
    box.append(Gtk.Label(label='Read the status; report when it changes to Ready.'))
    box.append(label);window.set_child(box);window.present()
    def emit(value): print(json.dumps(value),flush=True)
    def handle(source,condition):
        line=sys.stdin.readline()
        if not line:return False
        cmd=json.loads(line)
        def change():
            label.set_label(cmd['text'])
            emit({'applied':cmd['text'],'time':time.perf_counter()})
            return False
        GLib.timeout_add(cmd.get('delay_ms',1),change)
        return True
    GLib.io_add_watch(sys.stdin,GLib.IO_IN,handle)
    def ready():
        ok,rect=label.compute_bounds(window)
        emit({'ready':True,'label_bounds':[round(rect.get_x()),round(rect.get_y()),round(rect.get_width()),round(rect.get_height())]})
        return False
    GLib.timeout_add(1200,ready)
    GLib.MainLoop().run()


class Client:
    def __init__(self,command):
        self.p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,
                                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        self.q=queue.Queue();self.n=0
        def reader():
            for line in self.p.stdout:self.q.put(line)
        threading.Thread(target=reader,daemon=True).start()
        self.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'comparison','version':'1'}})
        self.p.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');self.p.stdin.flush()
    def request(self,method,params):
        self.n+=1;start=time.perf_counter()
        self.p.stdin.write(json.dumps({'jsonrpc':'2.0','id':self.n,'method':method,'params':params})+'\n');self.p.stdin.flush()
        line=self.q.get(timeout=35);r=json.loads(line)
        elapsed=(time.perf_counter()-start)*1000
        assert r['id']==self.n and 'error' not in r,r
        result=r['result'];assert not result.get('isError'),result
        return result,elapsed,len(line.encode())
    def call(self,name,**args):
        result,elapsed,size=self.request('tools/call',{'name':name,'arguments':args})
        blocks=result['content'];texts=[]
        for b in blocks:
            if b['type']=='text':
                try:texts.append(json.loads(b['text']))
                except ValueError:pass
        images=[b for b in blocks if b['type']=='image']
        pixels=0
        for im in images:
            import struct
            raw=base64.b64decode(im['data']);w,h=struct.unpack('>II',raw[16:24]);pixels+=w*h
        return texts[0],images,{'tool_ms':elapsed,'wire_bytes':size,'image_pixels':pixels,'image_count':len(images)}
    def close(self):
        self.p.stdin.close()
        try:self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:self.p.terminate();self.p.wait(timeout=5)


def main():
    import observer_server  # Locate the shared implementation in this checkout.
    from cu import observation as impl
    impl.baseline.session_env()
    original=json.loads(impl.command(['hyprctl','-j','activewindow']))
    gui=subprocess.Popen([sys.executable,__file__,'--fixture'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                         text=True,env=dict(os.environ,GDK_BACKEND='wayland'))
    events=queue.Queue()
    def read_gui():
        for line in gui.stdout:events.put(json.loads(line))
    threading.Thread(target=read_gui,daemon=True).start()
    old=new=None
    rows=[]
    try:
        ready=events.get(timeout=10);assert ready.get('ready'),ready
        windows=json.loads(impl.command(['hyprctl','-j','clients']))
        window=next(w for w in windows if w['pid']==gui.pid and w['title']==TITLE)
        address=window['address']
        assert json.loads(impl.command(['hyprctl','-j','activewindow']))['address']==address,'Fixture is not focused; stop instead of stealing focus.'
        config={'command':sys.executable,'args':[str(ROOT/'baseline/scripts/server.py')]}
        old=Client([config['command'],*config['args']]);new=Client([sys.executable,str(ROOT/'scripts/observer_server.py')])
        # Crop the exact status-label rectangle for the original pipeline's pixel predicate.
        # Conversion cost is recorded in task detection latency, outside tool latency.
        def status_hash(meta,images):
            x,y,w,h=ready['label_bounds'];target=meta['target_window']
            sx=target['at'][0]-meta['origin'][0]+x;sy=target['at'][1]-meta['origin'][1]+y
            data=impl.command(['magick','png:-','-crop',f'{w}x{h}+{sx}+{sy}','+repage','-alpha','off','-depth','8','rgb:-'],base64.b64decode(images[0]['data']))
            return hashlib.sha256(data).hexdigest()
        def send(text,delay=1):
            gui.stdin.write(json.dumps({'text':text,'delay_ms':delay})+'\n');gui.stdin.flush()
        for trial in range(6):
            for name in (['original','observer'] if trial%2==0 else ['observer','original']):
                new.call('stop_observing')
                send(f'Waiting {trial:03}')
                assert events.get(timeout=3)['applied']==f'Waiting {trial:03}'
                time.sleep(.5)
                if name=='original':
                    meta,images,metrics=old.call('screenshot');initial_hash=status_hash(meta,images)
                else:
                    meta,images,metrics=new.call('observe',window=address)
                    assert meta['state']['visual']['status']=='available',meta
                    assert meta['state']['accessibility']['status']=='available',meta['state']['accessibility']
                rows.append(dict(trial=trial,method=name,phase='initial',**metrics))
                rev=meta.get('revision')
                for repeat in range(3):
                    if name=='original':meta,images,metrics=old.call('screenshot')
                    else:
                        meta,images,metrics=new.call('observe',window=address,since_revision=rev);rev=meta['revision']
                    rows.append(dict(trial=trial,method=name,phase='unchanged',repeat=repeat,**metrics))
                expected=f'Ready {trial:03}'
                send(expected,500);start=time.perf_counter();calls=0;wire=0;pixels=0
                while time.perf_counter()-start<10:
                    if name=='original':
                        meta,images,metrics=old.call('screenshot');found=status_hash(meta,images)!=initial_hash
                    else:
                        meta,images,metrics=new.call('wait_for_change',window=address,since_revision=rev,timeout_ms=2000)
                        rev=meta['revision']
                        state=meta.get('state') or {}
                        access=state.get('accessibility') or meta['changes'].get('accessibility',{}).get('after',{})
                        found=any(n.get('name')==expected for n in access.get('nodes',[]))
                    calls+=1;wire+=metrics['wire_bytes'];pixels+=metrics['image_pixels']
                    if found:break
                assert found,f'{name} did not detect status'
                finish=time.perf_counter();applied=events.get(timeout=2)
                assert applied['applied']==expected and finish>=applied['time'],'False detection before scheduled change'
                rows.append({'trial':trial,'method':name,'phase':'detect_change','task_ms':(finish-start)*1000,
                             'after_change_ms':(finish-applied['time'])*1000,'calls':calls,'wire_bytes':wire,'image_pixels':pixels})
                print(json.dumps({'trial':trial,'method':name,'detection_ms':round((finish-applied['time'])*1000),'calls':calls}),flush=True)
        summary=[]
        for name in ['original','observer']:
            for phase in ['initial','unchanged','detect_change']:
                selected=[r for r in rows if r['method']==name and r['phase']==phase]
                keys=['tool_ms','wire_bytes','image_pixels'] if phase!='detect_change' else ['after_change_ms','task_ms','calls','wire_bytes','image_pixels']
                summary.append({'method':name,'phase':phase,'n':len(selected),
                                **{k:{'median':round(statistics.median(r[k] for r in selected),2),
                                      'min':round(min(r[k] for r in selected),2),'max':round(max(r[k] for r in selected),2)} for k in keys}})
        out=ROOT.parent/'benchmarks';out.mkdir(exist_ok=True)
        stamp=time.strftime('%Y%m%d-%H%M%S')
        path=out/f'comparison-{stamp}.json'
        report={'fixture':{'size':window['size'],'label_bounds':ready['label_bounds']},
                'scope':'Local MCP and deterministic predicate timing. No model inference. Original detects exact label pixel change; observer detects matching accessible label. Timed change occurs 500ms after start.',
                'original_config':config['args'],'observer_entry':str(ROOT/'scripts/observer_server.py'),
                'source_sha256':{str(p.relative_to(ROOT.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [ROOT.parent/'scripts/server.py',ROOT.parent/'scripts/dev_host.py',
                                           ROOT/'scripts/observer_server.py',ROOT/'scripts/accessibility_probe.py',
                                           ROOT/'baseline/scripts/server.py',Path(__file__).resolve()]},
                'summary':summary,'samples':rows}
        with path.open('x') as output: output.write(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'report':str(path),'summary':summary}),flush=True)
    finally:
        if new:
            try:new.call('stop_observing')
            finally:new.close()
        if old:old.close()
        gui.terminate();gui.wait(timeout=5)
        # Closing our fixture lets the compositor restore focus naturally.


if __name__=='__main__':
    fixture() if '--fixture' in sys.argv else main()
