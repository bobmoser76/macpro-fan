#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Robert Moser

set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo: sudo ./install.sh' >&2; exit 1; fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"; BACKUP="/var/backups/macpro-fan-v3-$STAMP"
systemctl disable --now mbpfan.service 2>/dev/null || true
systemctl stop macpro-fan-control.service 2>/dev/null || true
mkdir -p "$BACKUP"
for p in /usr/local/sbin/macpro-fan-control.py /usr/local/bin/macpro-fan /etc/macpro-fan.conf /etc/systemd/system/macpro-fan-control.service; do [[ -e "$p" ]] && cp -a "$p" "$BACKUP/"; done
install -Dm755 "$ROOT/macpro-fan-control.py" /usr/local/sbin/macpro-fan-control.py
install -Dm755 "$ROOT/macpro-fan" /usr/local/bin/macpro-fan
install -Dm644 "$ROOT/macpro-fan-control.service" /etc/systemd/system/macpro-fan-control.service
install -Dm644 "$ROOT/macpro-fan.conf" /etc/macpro-fan.conf
python3 -m py_compile /usr/local/sbin/macpro-fan-control.py
python3 -m py_compile /usr/local/bin/macpro-fan
systemctl daemon-reload
/usr/local/sbin/macpro-fan-control.py --check
systemctl enable --now macpro-fan-control.service
echo; echo 'Installed macpro-fan v3.'; echo "Backup: $BACKUP"; echo 'Run: macpro-fan status'
