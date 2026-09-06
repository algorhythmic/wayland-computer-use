#!/usr/bin/env python3
"""Bounded, read-only AT-SPI probe. Parent kills this process on timeout.

Match an application PID and an unambiguous top-level title. Never fall back to
dumping the entire desktop or another window. Object paths are revision-local.
"""
import json
import sys


def probe(pid, title):
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    Atspi.set_timeout(250, 500)
    desktop = Atspi.get_desktop(0)
    candidates = []
    matched_apps = 0
    top_levels = 0
    for i in range(min(desktop.get_child_count(), 128)):
        app = desktop.get_child_at_index(i)
        if app.get_process_id() != pid:
            continue
        matched_apps += 1
        top_levels += app.get_child_count()
        for j in range(min(app.get_child_count(), 128)):
            child = app.get_child_at_index(j)
            if child.get_name() == title:
                candidates.append(child)
    if len(candidates) != 1:
        reason = ('application_not_exposed' if not matched_apps else
                  'application_exposes_no_windows' if not top_levels else
                  'no_unique_pid_and_title_match')
        return {'status': 'unavailable', 'reason': reason}
    nodes, queue = [], [(candidates[0], 'root', 0)]
    truncated = False
    while queue and len(nodes) < 200:
        obj, path, depth = queue.pop(0)
        states = obj.get_state_set()
        # Do not extract text contents or protected-entry names/values.
        protected = obj.get_role() == Atspi.Role.PASSWORD_TEXT
        node = {'ref': path, 'role': obj.get_role_name(),
                'name': '[protected]' if protected else (obj.get_name() or '')[:160],
                'states': [name.lower() for name in
                           ('VISIBLE', 'SHOWING', 'ENABLED', 'SENSITIVE', 'FOCUSED',
                            'CHECKED', 'SELECTED', 'EDITABLE')
                           if states.contains(getattr(Atspi.StateType, name))]}
        if 'visible' in node['states'] and 'showing' in node['states']:
            component = obj.get_component_iface()
            if component:
                rect = component.get_extents(Atspi.CoordType.WINDOW)
                node['reported_bounds'] = [rect.x, rect.y, rect.width, rect.height]
        value = None if protected else obj.get_value_iface()
        if value:
            node['value'] = value.get_current_value()
        nodes.append(node)
        count = obj.get_child_count()
        if depth >= 12:
            truncated |= count > 0
            continue
        truncated |= count > 200
        for i in range(min(count, 200)):
            queue.append((obj.get_child_at_index(i), f'{path}/{i}', depth + 1))
    return {'status': 'partial' if queue or truncated else 'available',
            'source': 'atspi', 'nodes': nodes, 'refs': 'revision-local tree paths',
            'coordinates': 'AT-SPI window-relative; not verified for input'}


if __name__ == '__main__':
    try:
        result = probe(int(sys.argv[1]), sys.argv[2])
    except Exception as exc:
        result = {'status': 'unavailable', 'reason': type(exc).__name__}
    print(json.dumps(result, allow_nan=False))
