# LOG

Running record of what was tried, what worked, and exact versions. Measurements only; assumptions go under "Unverified".

## 2026-10-16: project start

Nothing built or tested yet. Source: handoff notes (Wi-Fi CSI presence/fall sensing for Hack the Valley 11, Project KAZE challenge).

### Hardware
- Raspberry Pi 4B (bcm43455c0), Pico 2 W, 7" touchscreen, ultrasonic sensor + LEDs, MacBook, Steam Deck. No camera.

### Unverified (test before relying on)
- Pi Wi-Fi is single antenna; 2.4 GHz / 20 MHz gives few subcarriers.
- Nexmon only sees frames that are actually transmitted, so a steady traffic source is required.
- Pico 2 W can send fixed-rate UDP at 50-100 Hz and the Pi can capture it in monitor mode.
- Pico 2 W cannot capture CSI (cyw43 driver exposes no CSI/monitor mode).
- Resistors available for the 5 V -> 3.3 V ultrasonic echo divider.

### Phase 0 go/no-go (pending)
- [ ] Nexmon CSI capturing on Pi 4B (record Pi OS image, kernel, commands)
- [ ] Pico 2 W fixed-rate transmit; measure actual frame rate at the Pi
- [ ] 30 s empty vs 30 s walking; plot amplitude per subcarrier; pass = visibly and statistically different

### Answers (2026-10-16)
- Event starts now (36 h clock running). Team of 2.
- Pico 2 **W** confirmed (official board in the kit). USB Wi-Fi adapter / ESP32: none mentioned, assume no.
- Kit (from photo, not yet physically checked): resistors 220 x20, 1k x10, 10k x10; active + passive buzzer; speaker; LCD module; accelerometer module; PIR infrared motion sensor; ultrasonic module; vibration switch; RGB/8-RGB LEDs; LED bar; breadboard, jumpers.
- Ultrasonic divider: 1k from ECHO to Pico pin, 2 x 1k in series from that pin to GND -> ~3.3 V.
- PIR sensor is a second independent presence labeler (better than ultrasonic for "someone in the room").

### Open questions
4. Current Pi OS image and kernel on the Pi 4B?
5. Full KAZE brief read, and its judging criteria?

## 2026-10-16: Pi install scripts written (not yet run)
- Pi kernel: `Linux applepi 6.18.50+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.18.50-1+rpt1 (2026-09-11) aarch64`.
- Precompiled binaries (nexmonster/nexmon_csi_bin) only cover 4.19/5.4/5.10: not usable. Official README lists up to 5.10 / 5.15.
- Using `Makefile.rpi` path (seemoo-lab/nexmon_csi discussion #395): no patched brcmfmac needed. Commenters report 6.12.109+rpt-rpi-v8 and Pi 4B working. **Kernel 6.18 is untested by anyone I found: UNVERIFIED.**
- Known reports: nexutil hang on Pi 4B fixed by updating Makefile.rpi; one Bookworm hang fixed by an older Makefile.rpi version.
- Scripts: `scripts/pi/install_nexmon_csi.sh {build|activate|restore}`, `scripts/pi/csi_start.sh <chan/bw> <MAC>`. Output appended to `docs/pi-install.log`.

## 2026-10-16 22:03: Nexmon CSI build + activate succeeded on kernel 6.18
- `install_nexmon_csi.sh build` -> BUILD OK; `activate` -> ACTIVATE OK (log: docs/pi-install.log, contains ANSI codes: use `grep -a`).
- Patched firmware registered via update-alternatives (/usr/lib/firmware/nexmon/brcmfmac43455-sdio.bin); restore-wifi returns to stock.
- **Not yet verified: that CSI frames are actually produced.** Build success does not prove capture works on 6.18.
- Pi has no Ethernet: CSI mode kills wlan0/Tailscale. Workflow: record locally with scripts/pi/csi_record.sh, restore-wifi, scp captures to Mac.

## 2026-10-16 ~22:45: Pico 2 W up
- Pico connected to Pi USB (Mac has no USB-A); flashed MicroPython RPI_PICO2_W v1.29.0 (blank board in BOOTSEL), appears as /dev/ttyACM0. mpremote in ~/venv-pico on the Pi.
- Pico MAC: 2c:cf:67:c5:c4:14. Hotspot "Mojo Dojo Casa Hotspot": **channel 11, 2.4 GHz**, RSSI -43 dBm at scan time.
- Sender: scripts/pico/sender.py (needs on-board config.py with SSID/PASSWORD/RATE_HZ; gitignored).
- Next: CSI capture with `csi_start.sh 11/20 2c:cf:67:c5:c4:14`.
- 22:50: Pico joined hotspot (ip 172.28.228.1, gw 172.28.228.59, channel 11) and sent UDP at measured **99 Hz** avg (Pico-side count, not yet confirmed at the Pi). main.py = `import sender`, so the Pico transmits whenever powered. Stop with Ctrl-C via mpremote.

## 2026-10-16 ~22:53: CSI capture VERIFIED on kernel 6.18
- diag.sh (Pico 1 USB cable from Pi, i.e. adjacent): 15 s unfiltered capture on wlan0 = 21655 frames, **1301 CSI UDP packets (port 5500)**, all from Pico MAC 2c:cf:67:c5:c4:14, chanspec 0x100b (ch 11, 20 MHz), 274-byte payload (18 B header + 64 subcarriers x 4 B), **93.1 Hz** at the Pi, RSSI -39.
- Firmware banner: `7.45.189 (nexmon.org/csi: 1232-3)`; firmware via update-alternatives; nexutil -m -> monitor: 1.
- First phase0.sh run recorded 0 frames in both pcaps (live `dst port 5500` BPF filter). Cause NOT established; diag's unfiltered capture worked. csi_record.sh now records unfiltered and counts offline.
- restore-wifi ends with a nmcli "wireless setting is required" error but Wi-Fi comes back (takes a while).
- Still to do for Phase 0: Pico powered separately (power bank), 2-3 m apart, record empty vs walking, plot/compare.

## 2026-10-16 ~23:00: second phase0 run = no CSI (Pico not heard)
- Captures empty_20261016_225605 / walking_20261016_225645 (in data/phase0): 0 UDP:5500 packets; only 393-byte non-IP frames. Pico (moved to power bank) most likely not connected/transmitting. Not a CSI failure: diag.sh earlier proved 93 Hz capture.
- Added pre-flight to phase0.sh (needs >=100 CSI frames in 6 s before recording). Analysis tooling: src/csi.py (reader), scripts/phase0_analyze.py (stats + docs/phase0.png), .venv.

## 2026-10-16 ~23:05: Phase 0 first real result: MARGINAL, not a clean pass
- Pico powered from a laptop (not Pi, not power bank). Captures data/phase0/empty_20261016_230237.pcap, walking_20261016_230317.pcap (29 s each). Pico stopping after ~20 s on the power bank: cause still unknown (power bank cutoff vs sender crash); sender.py rewritten to log errors + reconnect, NOT yet deployed (Pico not plugged into Pi).
- Effective CSI rate 71 Hz (empty) / 68 Hz (walking) vs ~100 Hz sent; inter-packet median 10 ms but p99 73 ms, max 109 ms (bursty loss). seq-gap loss estimate unreliable.
- Decoding verified: 64 subcarriers/pkt, smooth amplitude (~500-900); DC/guard region (idx 0, ~28-35) is junk (up to 35000) and is masked (58 of 64 usable).
- Metric: per-1 s-window mean CV of amplitude over usable subcarriers. empty 0.148+-0.047, walking 0.188+-0.057, ratio **1.27x**, Mann-Whitney p=**0.0049** (n~29 windows each). Passes the p<0.01 rule but the effect is small; mean-amplitude traces overlap and the empty run also fades. See docs/phase0.png.
- Not yet known: room geometry, Pico-Pi distance, whether the "empty" run was truly empty.

## 2026-10-16 ~23:15: better features on the same two captures
- Layout: Pi mid-table, Pico on the edge of the next table, ~3 m apart (user).
- Features: drop glitch packets (mean amp >25% off median, ~1% dropped), divide each packet by its mean amplitude (AGC/gain normalisation), resample to 50 Hz, 2 s windows. Tried 3 metrics on the same data (n=14 windows per class, one recording each):
  - std of normalised amplitude: empty 0.076, walking 0.135, **1.78x, AUC 0.92, p=1.8e-4**
  - 0.3-8 Hz band power: 2.38x, AUC 0.87, p=1e-3
  - subcarrier decorrelation: 1.07x, AUC 0.60, p=0.37 (no signal)
- Caveats: small n, one pair of recordings, metrics chosen after looking at this data. Needs the repeat (empty/walk x2) to confirm. Not yet merged into scripts/phase0_analyze.py.

## 2026-10-16 ~23:25: Pico at 200 Hz
- Pi USB enumeration of the Pico failed (error -71) until the Pi was restarted; no under-voltage (throttled=0x0). Cause not established.
- Deployed resilient sender.py; config.py RATE_HZ=200. Ran 30+ s on the Pi: avg 200 Hz Pico-side, no errors, no errors.txt on the board. So the earlier 20 s stop on the power bank was probably the bank (not confirmed: the old sender did not log).
- phase0.sh now supports ROUNDS (default 2: empty,walking,empty,walking). Captured rate at the Pi at 200 Hz: not yet measured.

## 2026-10-16 ~23:30: PHASE 0 PASS (200 Hz, 2 rounds x 29 s, Pico on laptop power, ~3 m)
- Captured 210-230 Hz at the Pi (above the 200 Hz sent: likely retries/duplicate frames), p99 gap ~25-29 ms. Files: data/phase0/{empty_231533,walking_231613,empty_231659,walking_231739}.pcap. Scorer: scripts/analyze_runs.py (2 s windows, gain-normalised).
- Pooled (n=28 windows/class): std feature empty 0.0384 vs walking 0.0786 = **2.05x, AUC 0.96, p=1.4e-9**; 0.3-8 Hz band power 3.18x, AUC 0.89, p=2.8e-7. Both walking runs sit above both empty runs in the median.
- Held-out check (threshold fit on round 1, scored on round 2): walking correct 93%, **empty correct only 71%** (std) / 64% (band). Round-2 empty baseline (0.0436) is higher than round 1 (0.0331): the empty baseline drifts between runs -> a fixed threshold will not hold; needs per-session calibration on the empty room.
- Limits: 2 rounds, one room, one walker, one geometry, features chosen after seeing the first pair. Presence/motion only; no fall/lying test yet.

## 2026-10-16 ~23:55: USB tether + remote CSI capture works
- Pixel 9 USB tether: Pi gets usb0 (10.92.17.x), default route via usb0, internet 200 and SSH/Tailscale stay up while wlan0 is in CSI mode (monitor: 1). Solves "no network in CSI mode" without Ethernet.
- Pi USB ports over-current (dmesg `over-current change`), Pico failed to enumerate on the Pi hub with phone + touchscreen attached (error -22/-71). Keep the Pico off the Pi's USB (laptop/wall charger power).
- activate: `nmcli dev disconnect wlan0` failed ("not all devices disconnected") when wlan0 was already down; script now tolerates it. csi_on.sh = activate + csi_start (NOSNIFF).
- tcpdump given cap_net_raw,cap_net_admin (enable_tcpdump.sh) so CSI can be recorded over SSH without sudo.
- Remote 20 s capture over the tether: 50087 frames, **3616 CSI frames (~181 Hz)**. An earlier remote attempt got 0 CSI (62 frames) while the Pico was not sending/powered; cause was the Pico, not the tether.

## 2026-10-17 ~00:15: first labelled session (data/session1, 13 takes x 28 s, teammate performing, scripts/session.sh)
Captured 167-188 Hz, rssi ~-51 dBm. Scorer: scripts/analyze_session.py (2 s windows, 14/take).
- **Walking is detectable**: window var median 0.070 vs 0.042-0.050 for every other class; both walking takes (0.066, 0.076) above all non-walking takes (<=0.057).
- **A still person is NOT separable from empty by motion features**: var AUC vs empty: standing 0.55, sitting 0.45, lying 0.66; band power ~0.4 (no info).
- Static-channel shape (mean normalised amplitude per subcarrier) vs empty: lying AUC 0.99, sitting 0.75, standing 0.40. Promising only for lying; single pair of takes, not drift-controlled (empty takes at 23:51/23:56, lying at 23:54/23:57).
- Leave-one-take-out classification is poor: 3-class (empty/walking/still) 49%, empty recall 0%; 6-class 12% (motion feats) / 21% (+shape). Too little data (2-3 takes/class) and per-take baseline drift (take means vary ~25%); not a usable classifier.
- **Fall: "motion then quiet" is inconsistent.** Take 1: burst (0.075, 0.081) then 0.03-0.047 quiet. Take 2: no quiet phase (0.05-0.06 throughout). Take 3: burst then mixed, with another burst at windows 11-12. Not supported as a reliable rule yet.
- Design implication: CSI motion works; distinguishing "person lying still" from "nobody there" does not with these features. Use doorway sensors (ultrasonic/PIR) for entry/exit so the state machine is: present (entered, not left) AND no CSI motion for T seconds -> escalate.
