#!/usr/bin/env python3
"""Print MCP config; optionally replace this checkout's plugin path templates.

Does not install plugins, modify client settings, publish builds, or start services.
"""
import argparse
import json
from pathlib import Path
import sys


def configurations(root):
    root = Path(root).resolve()
    result = {}
    for name, relative in (
        ('wayland', 'scripts/dev_host.py'),
        ('wayland_observer', 'wayland-desktop-observer/scripts/observer_server.py'),
    ):
        entry = root / relative
        if not entry.is_file():
            raise ValueError(f'Missing server entry point: {entry}')
        result[name] = {'command': sys.executable, 'args': [str(entry)],
                        'env': {'PYTHONDONTWRITEBYTECODE': '1'},
                        'startup_timeout_sec': 20}
    return {'mcpServers': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-plugin-configs', action='store_true',
                        help='Replace both .mcp.json path templates in this checkout')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = configurations(root)
    if args.write_plugin_configs:
        for name, directory in (('wayland', root),
                                ('wayland_observer', root / 'wayland-desktop-observer')):
            path = directory / '.mcp.json'
            path.write_text(json.dumps({'mcpServers': {name: config['mcpServers'][name]}},
                                       indent=2) + '\n')
            print(f'Configured {path}', file=sys.stderr)
    print(json.dumps(config, indent=2))


if __name__ == '__main__':
    main()
