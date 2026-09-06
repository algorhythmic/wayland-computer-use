#!/bin/bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test "$(id -u)" = 0
test -f /etc/systemd/system/wayland-computer-use-mouse.service
install -m 644 "$script_dir/wayland-computer-use-mouse.service" /etc/systemd/system/wayland-computer-use-mouse.service
systemctl daemon-reload
systemctl enable --now wayland-computer-use-mouse.service
