#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Robert Moser

set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo: sudo ./uninstall.sh' >&2; exit 1; fi
systemctl disable --now macpro-fan-control.service 2>/dev/null || true
echo 0 > /sys/devices/platform/applesmc.768/fan1_manual 2>/dev/null || true
rm -f /etc/systemd/system/macpro-fan-control.service /usr/local/sbin/macpro-fan-control.py /usr/local/bin/macpro-fan
rm -rf /run/macpro-fan
systemctl daemon-reload; systemctl reset-failed
echo 'Removed. Configuration and history were retained.'
