#!/usr/bin/env python3
"""Verify historical hashes against the frozen tag, not the evolving checkout."""
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TAG = 'comparison-2026-09-06'


def main():
    for line in (ROOT/'benchmarks/SHA256SUMS').read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        frozen = subprocess.check_output(['git','show',f'{TAG}:{name}'],cwd=ROOT)
        if hashlib.sha256(frozen).hexdigest() != expected:
            raise ValueError('Historical checksum mismatch: '+name)
        # Frozen artifacts must also remain untouched in the working tree.
        if name.startswith(('benchmarks/', 'wayland-desktop-observer/baseline/')):
            if (ROOT/name).read_bytes() != frozen:
                raise ValueError('Preserved artifact modified: '+name)
        print(name+': OK (frozen tag)')


if __name__ == '__main__':main()
