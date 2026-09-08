#!/usr/bin/env python3
"""Point the development host at an already published release for A/B trials.

Only revisions present under .dev/releases are accepted. A running host whose
tool contract differs from the selected release fails closed on its next
request and must be reconnected; that is deliberate. No tests are skipped by
selecting: every release here was published through dev_publish.py or copied
verbatim from an installation, and the host verifies its checksum on load.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def select(revision, root=ROOT):
    if not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise ValueError('Revision must be a SHA-256 hex digest')
    release = root/'.dev'/'releases'/revision
    if not (release/'server.py').is_file():
        raise FileNotFoundError(f'No published release {revision[:12]}')
    manifest = release/'bundle.json'
    digest = hashlib.sha256((manifest if manifest.exists() else release/'server.py').read_bytes()).hexdigest()
    if digest != revision:
        raise ValueError('Release contents do not match its revision')
    state = root/'.dev'
    fd, pending = tempfile.mkstemp(prefix='select-', dir=state)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump({'revision': revision}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, state/'current.json')
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return {'selected_revision': revision, 'format': 'bundle' if manifest.exists() else 'legacy single file',
            'note': 'Reconnect the MCP server if the tool contract differs from the running host.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision')
    parser.add_argument('--list', action='store_true', help='List published releases instead of selecting')
    options = parser.parse_args()
    if options.list:
        current = json.loads((ROOT/'.dev'/'current.json').read_text()).get('revision')
        for path in sorted((ROOT/'.dev'/'releases').iterdir(), key=lambda p: p.stat().st_mtime):
            kind = 'bundle' if (path/'bundle.json').exists() else 'legacy'
            print(f"{'*' if path.name == current else ' '} {path.name} {kind}")
    else:
        print(json.dumps(select(options.revision), indent=2))
