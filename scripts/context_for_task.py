#!/usr/bin/env python3
"""Read-only context CLI. Supply one WCU context request as JSON on stdin."""
import argparse
import json
import os
from pathlib import Path
import sys

from cu.context_records import SnapshotStore
from cu.context_retrieval import context_for_task, render, validate_request, CONTRACT_VERSION, RENDERER
from cu.braid_client import BraidBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path(__file__).resolve().parents[1]/'.dev/keyboard-context/normalized')
    parser.add_argument('--diagnostics', type=Path, help='Optional local diagnostic file; excluded from model payload')
    args = parser.parse_args()
    backend = None
    if os.environ.get('WCU_BRAID_EXECUTABLE') and os.environ.get('WCU_BRAID_SHA256'):
        backend = BraidBackend(os.environ['WCU_BRAID_EXECUTABLE'], args.catalog.parent/'braid', os.environ['WCU_BRAID_SHA256'])
    try:
        request = json.loads(sys.stdin.buffer.read(16385))
        validate_request(request)
        try:
            snapshot = SnapshotStore(args.catalog).load()
        except (OSError, ValueError):
            payload = {'contract_version': CONTRACT_VERSION, 'status': 'catalog_unavailable',
                'output': {'unit':'utf8_bytes','renderer':RENDERER,'limit':request.get('max_bytes',8192),'bytes':0}}
            diagnostics = {'backend':'local','failures':['catalog_unavailable']}
        else:
            payload, diagnostics = context_for_task(snapshot, request, backend=backend)
        sys.stdout.buffer.write(render(payload)+b'\n')
        if args.diagnostics:
            args.diagnostics.write_text(json.dumps(diagnostics, indent=2)+'\n')
    finally:
        if backend:
            backend.close()


if __name__ == '__main__':
    main()
