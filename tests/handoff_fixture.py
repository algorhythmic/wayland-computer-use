#!/usr/bin/env python3
"""Disposable GTK task fixture. Its control pipe only resets/reads test state."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk, Gdk, GLib

parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path,required=True)
parser.add_argument('--steady-caret',action='store_true',help='Disable this fixture process cursor blinking to isolate focus-race tests')
args=parser.parse_args()
args.artifacts.mkdir(parents=True,exist_ok=True)
app=Gtk.Application(application_id='org.example.WCUHandoffFixture')
state={'mode':'form','canvas':False,'interrupt':False,'interrupted':False,'artifact':None}


def emit(value):print(json.dumps(value),flush=True)


def activate(app):
    if args.steady_caret:
        Gtk.Settings.get_default().set_property('gtk-cursor-blink',False)
    window=Gtk.ApplicationWindow(application=app,title='WCU Handoff Fixture')
    window.set_default_size(720,480)
    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
    for setter in ('set_margin_top','set_margin_bottom','set_margin_start','set_margin_end'):getattr(box,setter)(24)
    window.set_child(box)
    heading=Gtk.Label(label='Disposable WCU benchmark');box.append(heading)
    stack=Gtk.Stack();stack.set_vexpand(True);box.append(stack)
    form=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
    entries=[]
    for name in ('Name','Code'):
        form.append(Gtk.Label(label=name))
        entry=Gtk.Entry();entry.update_property([Gtk.AccessibleProperty.LABEL],[name]);form.append(entry);entries.append(entry)
    form.append(Gtk.Label(label='Ctrl+S saves this test form. Tab moves Name → Code.'))
    stack.add_named(form,'form')
    note=Gtk.TextView();note.set_wrap_mode(Gtk.WrapMode.WORD_CHAR);note.update_property([Gtk.AccessibleProperty.LABEL],['Test note'])
    stack.add_named(note,'note')
    canvas=Gtk.DrawingArea();canvas.set_focusable(True);canvas.update_property([Gtk.AccessibleProperty.LABEL],['Canvas'])
    def draw(widget,cr,width,height):
        cr.set_source_rgb(.07,.12,.18);cr.paint()
        cr.set_source_rgb(*((.15,.8,.4) if state['canvas'] else (.95,.45,.15)))
        cr.arc(width/2,height/2,65,0,6.2832);cr.fill()
    canvas.set_draw_func(draw);stack.add_named(canvas,'canvas')
    status=Gtk.Label(label='Ready');box.append(status)
    decoy=Gtk.ApplicationWindow(application=app,title='WCU Handoff Decoy')
    decoy.set_default_size(400,180);decoy_entry=Gtk.Entry();decoy_entry.update_property([Gtk.AccessibleProperty.LABEL],['Decoy']);decoy.set_child(decoy_entry)
    def save():
        buffer=note.get_buffer();text=buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)
        artifact={'mode':state['mode'],'name':entries[0].get_text(),'code':entries[1].get_text(),'note':text,'canvas':state['canvas'],'decoy':decoy_entry.get_text()}
        path=args.artifacts/(state['mode']+'.json');path.write_text(json.dumps(artifact)+'\n');state['artifact']=str(path)
        status.set_label('Saved '+state['mode'])
    def key(controller,keyval,keycode,mods):
        if keyval in (Gdk.KEY_s,Gdk.KEY_S) and mods&Gdk.ModifierType.CONTROL_MASK:
            save();return True
        if state['mode']=='canvas' and keyval==Gdk.KEY_space:
            state['canvas']=not state['canvas'];canvas.queue_draw();save();return True
        return False
    controller=Gtk.EventControllerKey();controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE);controller.connect('key-pressed',key);window.add_controller(controller)
    def changed(entry):
        if state['interrupt'] and not state['interrupted'] and len(entry.get_text())>=40:
            state['interrupted']=True
            # The decoy is a disposable window. The synchronous compositor focus
            # request occurs at the segment boundary being tested.
            clients=json.loads(subprocess.check_output(['hyprctl','-j','clients']))
            destination=next(w for w in clients if w['pid']==os.getpid() and w['title']=='WCU Handoff Decoy')
            subprocess.run(['hyprctl','dispatch','hl.dsp.focus({window="address:'+destination['address']+'"})'],stdout=subprocess.DEVNULL,check=True)
            if state.get('focus_marker'):
                assert json.loads(subprocess.check_output(['hyprctl','-j','activewindow']))['address']==destination['address']
                Path(state['focus_marker']).write_text('focus changed')
    entries[0].connect('changed',changed)
    buffer=bytearray();os.set_blocking(sys.stdin.fileno(),False)
    def incoming(fd,condition):
        data=os.read(fd,8192)
        if not data:app.quit();return False
        buffer.extend(data)
        while b'\n' in buffer:
            line,_,rest=buffer.partition(b'\n');buffer[:]=rest;request=json.loads(line)
            if request['op']=='reset':
                state.update(mode=request['mode'],canvas=False,interrupt=False,interrupted=False,artifact=None)
                state['focus_marker']=request.get('focus_marker')
                for entry in entries:entry.set_text('')
                note.get_buffer().set_text('');decoy_entry.set_text('');status.set_label('Ready')
                stack.set_visible_child_name('form' if request['mode']=='interrupt' else request['mode'])
                if request['mode']=='interrupt':decoy.present();decoy_entry.grab_focus()
                else:decoy.set_visible(False)
                window.present();(note if request['mode']=='note' else canvas if request['mode']=='canvas' else entries[0]).grab_focus()
                canvas.queue_draw();state['interrupt']=request['mode']=='interrupt'
                GLib.timeout_add(150,lambda:(emit({'reset':state['mode']}),False)[1])
            elif request['op']=='read':
                buf=note.get_buffer()
                emit({'name':entries[0].get_text(),'code':entries[1].get_text(),
                      'note':buf.get_text(buf.get_start_iter(),buf.get_end_iter(),True),'canvas':state['canvas'],
                      'decoy':decoy_entry.get_text(),'interrupted':state['interrupted'],'artifact':state['artifact']})
            elif request['op']=='quit':app.quit()
        return True
    GLib.io_add_watch(sys.stdin.fileno(),GLib.IO_IN|GLib.IO_HUP,incoming)
    window.present();entries[0].grab_focus()
    GLib.timeout_add(300,lambda:(emit({'ready':True,'pid':os.getpid()}),False)[1])


app.connect('activate',activate);app.run([])
