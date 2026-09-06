#!/usr/bin/env python3
"""Build the optional read-only capture helper locally; no install or service."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='wayland-cu-build-') as temporary:
        build = Path(temporary)
        xml = ROOT/'native/wlr-screencopy-unstable-v1.xml'
        for mode, output in [('client-header', 'wlr-screencopy-client.h'), ('private-code', 'wlr-screencopy.c')]:
            subprocess.run(['wayland-scanner', mode, str(xml), str(build/output)], check=True)
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'wayland-client'], text=True))
        destination = ROOT/'scripts/cu/capture-helper'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I'+str(build), str(ROOT/'native/capture.c'), str(build/'wlr-screencopy.c'),
                        '-o', str(build/'capture-helper'), *flags], check=True)
        fd, pending = tempfile.mkstemp(prefix='.capture-helper-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write((build/'capture-helper').read_bytes())
            os.chmod(pending, 0o755)
            os.replace(pending, destination)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        print(destination)


if __name__ == '__main__':
    main()
