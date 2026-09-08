#!/usr/bin/env python3
"""Opt-in live text-entry acceptance test against a disposable GTK text view.

Types fixed corpora through the real type_text tool path, reads the widget's
buffer back through the fixture's stdin protocol, and reports dropped or
altered characters with their distinct-character rank. Focuses only the
fixture it created and aborts on any later focus change. Saves metrics only.

    python3 tests/live_text_entry.py --corpus probe --trials 3 --no-split
    python3 tests/live_text_entry.py --corpus proposal --trials 100
"""
import argparse
import difflib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
TITLE = 'Wayland text entry acceptance — disposable fixture'
CORPORA = {
    # 40 distinct characters: with a 36-keysym cap the last four never arrive.
    'probe': 'abcdefghijklmnopqrstuvwxyz0123456789ABCD',
    # Early characters repeated after the cap: they must still arrive.
    'probe_repeat': 'abcdefghijklmnopqrstuvwxyz0123456789ABCD abc ABCD xyz 0189',
    # Long keysym names only: if the cap is keymap size, this fails before 30 distinct.
    'punct': '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~',
    # Short names only, 62 distinct: if the cap is a count, this fails around 36.
    'alnum62': 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
    # New characters introduced only after a long run: a time/position-based cap drops them.
    'late_new': 'a'*120 + ' bcdefghij',
    # Newlines in a tiny text: a newline-triggered cap drops characters after them.
    'newline_small': 'ab\ncd ef\ngh ij',
    # The proposal corpus with newlines replaced by spaces.
    'proposal_flat': ('Reflections (draft): the "consent requirement" reads like liability transfer, not consent. '
                      'Source: [LG terms](https://www.lg.com/us/terms) via HN item 49592375; see also Q&A #2. '
                      "It's unclear whether ThinQ-only products are covered - the EDIT says no. Key risk: 'wiretapping' laws! "
                      'Next: compare EU/UK terms; email privacy@ before Friday, 09:30.'),
    # The proposal's mix: cases, punctuation, a Markdown link, parentheses, newlines.
    'proposal': ('Reflections (draft): the "consent requirement" reads like liability transfer, not consent.\n'
                 'Source: [LG terms](https://www.lg.com/us/terms) via HN item 49592375; see also Q&A #2.\n'
                 "It's unclear whether ThinQ-only products are covered - the EDIT says no. Key risk: 'wiretapping' laws!\n"
                 'Next: compare EU/UK terms; email privacy@ before Friday, 09:30.'),
}


def fixture():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib
    Gtk.init()
    settings = Gtk.Settings.get_default()
    settings.set_property('gtk-cursor-blink', False)  # No caret animation in the guard's crop.
    window = Gtk.Window(title=TITLE)
    window.set_default_size(900, 600)
    view = Gtk.TextView()
    view.set_monospace(True)
    view.set_margin_top(40); view.set_margin_start(40); view.set_margin_end(40); view.set_margin_bottom(40)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    window.set_child(view)
    # F5 retitles the window immediately, reproducing applications that change
    # their title right after an action (the result-capture race).
    controller = Gtk.EventControllerKey()
    def on_key(ctrl, keyval, keycode, state):
        if keyval == 0xFFC2:  # GDK_KEY_F5
            def retitle():
                window.set_title(TITLE + ' (retitled)')
                emit({'retitled': True})
                return False
            GLib.timeout_add(int(os.environ.get('RETITLE_DELAY_MS', '12')), retitle)  # Land inside the post-action capture, as Obsidian does.
            return True
        return False
    controller.connect('key-pressed', on_key)
    window.add_controller(controller)
    window.present()
    view.grab_focus()
    def emit(value):
        print(json.dumps(value), flush=True)
    def handle(source, condition):
        line = sys.stdin.readline()
        if not line:
            return False
        cmd = json.loads(line)
        buffer = view.get_buffer()
        if cmd.get('clear'):
            buffer.set_text('')
            view.grab_focus()
            emit({'cleared': True})
        if cmd.get('read'):
            start, end = buffer.get_bounds()
            emit({'text': buffer.get_text(start, end, True)})
        return True
    GLib.io_add_watch(sys.stdin, GLib.IO_IN, handle)
    GLib.timeout_add(1000, lambda: emit({'ready': True}) or False)
    GLib.MainLoop().run()


def rank_map(text):
    """Distinct-character rank (1-based) of each character's first appearance."""
    ranks, seen = {}, []
    for ch in text:
        if ch not in ranks:
            seen.append(ch)
            ranks[ch] = len(seen)
    return ranks


def diff_report(expected, actual):
    ranks = rank_map(expected)
    ops = [op for op in difflib.SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes() if op[0] != 'equal']
    dropped = [(i, expected[i], ranks[expected[i]]) for op in ops if op[0] in ('delete', 'replace') for i in range(op[1], op[2])]
    return {'exact': expected == actual, 'expected_len': len(expected), 'actual_len': len(actual),
            'dropped_count': len(dropped), 'inserted_count': sum(op[4]-op[3] for op in ops if op[0] in ('insert', 'replace')),
            'dropped_positions': [d[0] for d in dropped], 'dropped_ranks': sorted({d[2] for d in dropped}),
            'min_dropped_rank': min((d[2] for d in dropped), default=None), 'distinct_in_corpus': len(ranks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', default='probe', choices=sorted(CORPORA) + ['all'])
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--no-split', action='store_true', help='Disable keysym-limited splitting in the server (baseline)')
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--retitle-check', action='store_true', help='Press F5 so the fixture retitles mid-capture; report result_retried')
    options = parser.parse_args()
    sys.path.insert(0, str(ROOT/'scripts'))
    import server
    from cu.system import hypr_query
    server.session_env()
    if options.no_split:
        server.TEXT_KEYSYM_LIMIT = 0
    corpora = sorted(CORPORA) if options.corpus == 'all' else [options.corpus]
    gui = subprocess.Popen([sys.executable, __file__, '--fixture'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           text=True, env=dict(os.environ, GDK_BACKEND='wayland', PYTHONDONTWRITEBYTECODE='1'))
    events = queue.Queue()
    threading.Thread(target=lambda: [events.put(json.loads(l)) for l in gui.stdout], daemon=True).start()
    def ask(command, key):
        gui.stdin.write(json.dumps(command)+'\n'); gui.stdin.flush()
        while True:
            reply = events.get(timeout=5)
            if key in reply:
                return reply[key]
    report = {'scope': 'Live typed-text fidelity through the type_text tool path into a GTK text view; metrics only.',
              'split_limit': server.TEXT_KEYSYM_LIMIT, 'started_at': time.time(), 'trials': [], 'status': 'incomplete'}
    desktop = server.Desktop()
    try:
        assert events.get(timeout=10).get('ready')
        window = next(w for w in hypr_query('clients') if w['pid'] == gui.pid and w['title'] == TITLE)
        address = window['address']
        desktop.dispatch('focuswindow', 'address:'+address)
        desktop.wait_focus(address)
        time.sleep(.4)
        def check_focus():
            assert hypr_query('activewindow').get('address') == address, 'Fixture lost focus; stopping'
        if options.retitle_check:
            check_focus()
            frame = json.loads(desktop.call('screenshot', {})[0]['text'])
            result = json.loads(desktop.call('press_key', {'frame_id': frame['frame_id'], 'key': 'F5'})[0]['text'])
            assert ask({'read': True}, 'text') == ''  # F5 typed nothing into the view.
            report['retitle_check'] = {'result_retried': result.get('result_retried', False),
                                       'delivered_title': result['target_window']['title'], 'timings_ms': result['timings_ms']}
            print(json.dumps(report['retitle_check']), flush=True)
            corpora = []
        for name in corpora:
            corpus = CORPORA[name]
            for trial in range(options.trials):
                check_focus()
                ask({'clear': True}, 'cleared')
                frame = json.loads(desktop.call('screenshot', {})[0]['text'])
                assert frame['target_window'] and frame['target_window']['address'] == address, 'Fixture is not the screenshot target'
                started = time.monotonic_ns()
                result = json.loads(desktop.call('type_text', {'frame_id': frame['frame_id'], 'text': corpus})[0]['text'])
                elapsed_ms = (time.monotonic_ns()-started)/1e6
                time.sleep(.15)
                actual = ask({'read': True}, 'text')
                entry = {'corpus': name, 'trial': trial, 'input_ms': result['timings_ms'].get('input_ms'),
                         'call_ms': elapsed_ms, 'wtype_calls': result.get('text_segments'), **diff_report(corpus, actual)}
                report['trials'].append(entry)
                print(json.dumps({k: entry[k] for k in ('corpus', 'trial', 'exact', 'dropped_count', 'dropped_ranks', 'wtype_calls', 'input_ms')}), flush=True)
        report['status'] = 'complete'
    except BaseException as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
        raise
    finally:
        exact = [t for t in report['trials'] if t['exact']]
        report['summary'] = {'trials': len(report['trials']), 'exact': len(exact),
                             'min_dropped_rank': min((t['min_dropped_rank'] for t in report['trials'] if t['min_dropped_rank']), default=None)}
        output = options.output or ROOT/'benchmarks'/('text-entry-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
        output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'report': str(output), 'status': report['status'], 'summary': report['summary']}), flush=True)
        desktop.close()
        gui.terminate()
        try:
            gui.wait(timeout=5)
        except subprocess.TimeoutExpired:
            gui.kill()


if __name__ == '__main__':
    fixture() if '--fixture' in sys.argv else main()
