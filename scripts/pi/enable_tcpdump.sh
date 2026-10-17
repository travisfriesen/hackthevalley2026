#!/usr/bin/env bash
# One-time: let tcpdump capture without sudo (so CSI can be recorded over SSH). Undo: sudo setcap -r /usr/bin/tcpdump
set -euo pipefail
sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/tcpdump
getcap /usr/bin/tcpdump
