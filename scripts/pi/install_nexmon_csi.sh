#!/usr/bin/env bash
# Build Nexmon CSI on a Pi 4B (aarch64, recent kernel) via seemoo-lab's Makefile.rpi
# tutorial: https://github.com/seemoo-lab/nexmon_csi/discussions/395
# Source of truth for each step is that tutorial; UNVERIFIED on kernel 6.18 until Phase 0 passes.
#
#   ./install_nexmon_csi.sh build      steps 2-7 + makecsiparams (touches apt only; Wi-Fi keeps working)
#   ./install_nexmon_csi.sh activate   step 8: swaps in patched firmware, unmanages wlan0 (DROPS Wi-Fi SSH!)
#   ./install_nexmon_csi.sh restore    back to stock firmware + NetworkManager
set -euo pipefail

WORK="${WORK:-$HOME/nexmon-work}"
LOG="$(dirname "$0")/../../docs/pi-install.log"
mkdir -p "$WORK"
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date -Is) ${1:-} ==="; uname -a; grep PRETTY_NAME /etc/os-release; getconf PAGESIZE

build() {
  sudo apt update
  sudo apt full-upgrade -y   # may install a newer kernel: reboot and re-run if `uname -r` changed
  sudo apt install -y git libgmp3-dev gawk qpdf bison flex make autoconf libtool texinfo xxd \
    libnl-3-dev libnl-genl-3-dev bc libssl-dev tcpdump raspberrypi-kernel-headers 2>/dev/null \
    || sudo apt install -y git libgmp3-dev gawk qpdf bison flex make autoconf libtool texinfo xxd \
       libnl-3-dev libnl-genl-3-dev bc libssl-dev tcpdump

  if [ "$(uname -m)" = aarch64 ]; then   # step 3: nexmon's gcc toolchain is 32-bit ARM
    sudo dpkg --add-architecture armhf
    sudo apt update
    sudo apt install -y libc6:armhf libisl23:armhf libmpfr6:armhf libmpc3:armhf libstdc++6:armhf
    sudo ln -sfn /usr/lib/arm-linux-gnueabihf/libisl.so.23 /usr/lib/arm-linux-gnueabihf/libisl.so.10
    sudo ln -sfn /usr/lib/arm-linux-gnueabihf/libmpfr.so.6 /usr/lib/arm-linux-gnueabihf/libmpfr.so.4
  fi

  if ! command -v python2.7 >/dev/null; then   # step 4: temporary Debian stretch source
    sudo cp /etc/apt/sources.list /tmp/sources.list.bak
    echo 'deb http://archive.debian.org/debian/ stretch contrib main non-free' | sudo tee -a /etc/apt/sources.list
    sudo apt update || true
    sudo apt install -y python2.7
    sudo cp /tmp/sources.list.bak /etc/apt/sources.list
    sudo apt update
  fi

  cd "$WORK"   # step 5
  [ -d nexmon ] || git clone --depth=1 https://github.com/seemoo-lab/nexmon.git
  cd nexmon
  set +u; source setup_env.sh; set -u
  grep -q 'python2.7$' buildtools/b43-v3/debug/b43-beautifier || sed -i '1 s/$/2.7/' "$NEXMON_ROOT/buildtools/b43-v3/debug/b43-beautifier"
  make

  cd "$NEXMON_ROOT/utilities/nexutil"   # step 6
  sudo -E make install USE_VENDOR_CMD=1
  sudo setcap cap_net_admin+ep /usr/bin/nexutil

  cd "$NEXMON_ROOT/patches/bcm43455c0/7_45_189"   # step 7
  [ -d nexmon_csi ] || git clone --depth=1 https://github.com/seemoo-lab/nexmon_csi.git
  cd nexmon_csi/utils/makecsiparams && make   # CSI config generator (README: lives in utils/makecsiparams)
  echo "BUILD OK. Next: ./install_nexmon_csi.sh activate (use Ethernet/serial/monitor, not Wi-Fi SSH)"
}

activate() {
  if [ "${YES:-0}" != 1 ]; then
    [ -t 0 ] && read -rp "This replaces wlan0 firmware and disconnects Wi-Fi. Using Ethernet/keyboard? [y/N] " a && [ "$a" = y ] || exit 1
  fi
  cd "$WORK/nexmon"; set +u; source setup_env.sh; set -u
  cd "$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"
  make -f Makefile.rpi install-firmware
  # same steps as `make -f Makefile.rpi unmanage`, but tolerate wlan0 already being disconnected
  # (nmcli: "not all devices disconnected" aborted the make target)
  sudo nmcli dev disconnect wlan0 || true
  sudo nmcli dev set wlan0 managed no || true
  sudo nmcli radio wifi off || true
  sudo rfkill unblock wifi || true
  make -f Makefile.rpi reload-full
  echo "ACTIVATE OK. Next: scripts/pi/csi_start.sh"
}

restore() {
  cd "$WORK/nexmon"; set +u; source setup_env.sh; set -u
  cd "$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"
  make -f Makefile.rpi restore-wifi
}

"${1:?usage: $0 build|activate|restore}"
