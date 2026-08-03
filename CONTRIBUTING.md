<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Robert Moser -->

# Contributing

Bug reports and tested improvements for the Mac Pro 6,1 are welcome.

Before submitting a change:

1. Run `python3 -m py_compile macpro-fan-control.py macpro-fan`.
2. Run `sudo ./install.sh` on a Mac Pro 6,1 test system.
3. Run `macpro-fan test`.
4. Confirm that stopping the service returns `fan1_manual` to `0`.

Please include hardware details, kernel version, and relevant journal output.
