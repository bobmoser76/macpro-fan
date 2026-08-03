<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Robert Moser -->

# macpro-fan

A Linux fan controller designed for the **2013 Mac Pro 6,1** thermal core.

It controls the single Apple blower using the temperature of the Xeon CPU and
both AMD FirePro GPUs. It supports automatic profiles, persistent statistics,
live monitoring, configuration hot-reload, and safe return to Apple SMC
control.

## Features

- CPU and dual-GPU temperature monitoring
- Automatic Silent, Balanced, and Performance profiles
- GPU utilization and CPU load awareness
- Configurable fan curves and GPU temperature bias
- Emergency maximum-fan and sensor-failure protection
- Persistent temperature and fan statistics
- Live terminal monitor
- systemd service
- Automatic return to Apple SMC control on exit

## Requirements

- Mac Pro 6,1
- Linux with `applesmc` fan interfaces
- Python 3
- systemd
- `coretemp` and `amdgpu` hwmon sensors

Expected Apple SMC path:

```text
/sys/devices/platform/applesmc.768/
```

## Installation

```bash
chmod +x install.sh
sudo ./install.sh
```

The installer backs up existing files under `/var/backups/`, validates the
hardware interfaces, and enables the service.

## Commands

```bash
macpro-fan status
macpro-fan monitor
macpro-fan history
macpro-fan reset-history
macpro-fan test

macpro-fan profile Auto
macpro-fan profile Silent
macpro-fan profile Balanced
macpro-fan profile Performance
```

## Logs

```bash
journalctl -fu macpro-fan-control.service
```

## Configuration

Edit:

```text
/etc/macpro-fan.conf
```

The running service reloads changes automatically.

## Safety

If the controller encounters a sensor or control error, it requests maximum
fan speed. When the service exits, it returns control to the Apple SMC.

This project is provided without warranty. Monitor temperatures carefully when
testing modified fan curves.

## License

This project is licensed under the GNU General Public License version 3, or
(at your option) any later version (`GPL-3.0-or-later`). See [LICENSE](LICENSE)
for the complete license text.
