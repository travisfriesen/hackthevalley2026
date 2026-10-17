"""CSI stream: pcap parsing and the motion scoring that drives escalation."""
import math
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from csi_stream import CsiProcessor, SimSource, iter_csi

MAC = "2c:cf:67:c5:c4:14"
ROOT = Path(__file__).resolve().parent.parent


def csi_payload(seq=1, nfft=64, rssi=-40):
    header = struct.pack("<Hbb", 0x1111, rssi, 0)          # magic, rssi, fctl
    header += bytes.fromhex(MAC.replace(":", ""))          # src mac
    header += struct.pack("<HHHH", seq, 0, 0x100B, 0x189)  # seq, css, chanspec
    iq = np.arange(nfft * 2, dtype="<i2").tobytes()
    return header + iq


def ip_udp_packet(payload, sport=12345, dport=5500):
    udp = struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 1, 0,
                     64, 17, 0, b"\x01\x01\x01\x01", b"\x02\x02\x02\x02")
    return b"\x00" * 12 + struct.pack(">H", 0x0800) + ip + udp


def pcap_bytes(packets):
    """packets: list of (ts_s, packet_bytes)."""
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, 1)
    for ts, data in packets:
        out += struct.pack("<IIII", int(ts), int((ts % 1) * 1e6),
                           len(data), len(data)) + data
    return out


def test_parse_synthetic_pcap():
    import io

    pkts = [(0.000 + i * 0.01, ip_udp_packet(csi_payload(seq=i)))
            for i in range(5)]
    pkts.append((0.06, ip_udp_packet(b"not-csi")))          # filtered out
    pkts.append((0.07, ip_udp_packet(csi_payload(), dport=5555)))  # wrong port
    stream = io.BytesIO(pcap_bytes(pkts))
    seen = list(iter_csi(stream, mac=MAC))
    assert len(seen) == 5
    t0, rssi, seq, csi = seen[0]
    assert t0 == pytest.approx(0.0)
    assert rssi == -40
    assert seq == 0
    assert csi.size == 64


def test_parse_real_session_pcap():
    path = ROOT / "data/session1/walking_20261016_235147.pcap"
    if not path.exists():
        pytest.skip("session1 pcap not present")
    with open(path, "rb") as f:
        seen = list(iter_csi(f, mac=MAC))
    assert len(seen) > 1000          # ~28 s at ~170 Hz
    assert all(c.size == 64 for _t, _r, _s, c in seen[:50])


def _drive(processor, mode, seconds, rate_hz=100.0, seed=1):
    """Feed synthetic activity through the processor at real pace."""
    rng = np.random.default_rng(seed)
    k = np.arange(64)
    shape_empty = 0.5 + 0.35 * np.abs(np.sin(k * 0.72))
    shape_person = 0.5 + 0.35 * np.abs(np.sin(k * 0.51 + 1.7))
    phase = rng.uniform(0, 2 * np.pi, 64)
    dt = 1.0 / rate_hz
    for i in range(int(seconds * rate_hz)):
        t = i * dt
        base = shape_person if mode in ("lying", "fall") else shape_empty
        amp = base * (1 + 0.015 * rng.standard_normal(64))
        if mode in ("walking", "fall"):
            amp = amp * (1 + 0.16 * np.sin(2 * math.pi * 0.9 * t + phase))
        processor.push(t, amp.astype(np.complex64))
    return processor.motion_score(seconds)


def test_motion_score_separates_walking_from_empty():
    walking = CsiProcessor(window_s=2.0)
    empty = CsiProcessor(window_s=2.0)
    s_walk = _drive(walking, "walking", 6.0)
    s_empty = _drive(empty, "empty", 6.0)
    assert s_walk is not None and s_empty is not None
    assert s_walk > s_empty * 1.8       # phase-0 effect: walking ~2x empty


def test_calibration_sets_threshold_and_moving_flag():
    p = CsiProcessor(window_s=2.0, factor=1.5)
    p.calibrate(6.0, seconds=1.0)      # collect during the empty drive below
    _drive(p, "empty", 6.0)
    p.finish_calibration()
    assert p.baseline is not None
    assert p.threshold == pytest.approx(p.baseline * 1.5)
    assert p.snapshot(6.0)["moving"] is False      # empty room: below threshold
    # now walk: per-subcarrier phases keep the variation after gain normalisation
    rng = np.random.default_rng(2)
    phase = rng.uniform(0, 2 * math.pi, 64)
    for i in range(400):
        t = 6.0 + i * 0.01
        k = np.arange(64)
        amp = (0.5 + 0.35 * np.abs(np.sin(k * 0.72))) * (
            1 + 0.16 * np.sin(2 * math.pi * 0.9 * t + phase))
        p.push(t, amp.astype(np.complex64))
    assert p.snapshot(10.0)["moving"] is True


def test_motion_score_on_real_pcaps():
    """The real validation: session1 walking must score above empty."""
    empty_path = ROOT / "data/session1/empty_20261016_235101.pcap"
    walk_path = ROOT / "data/session1/walking_20261016_235147.pcap"
    if not (empty_path.exists() and walk_path.exists()):
        pytest.skip("session1 pcaps not present")

    def score_file(path):
        p = CsiProcessor(window_s=2.0)
        with open(path, "rb") as f:
            base_t = None
            t_sim = 0.0
            last = None
            for ts, _rssi, _seq, csi in iter_csi(f, mac=MAC):
                if base_t is None:
                    base_t = ts
                if last is not None:
                    t_sim += min(ts - last, 0.1)
                last = ts
                p.push(t_sim, csi)
        return p.motion_score(t_sim)

    s_empty = score_file(empty_path)
    s_walk = score_file(walk_path)
    assert s_empty is not None and s_walk is not None
    assert s_walk > s_empty * 1.3


def test_sim_source_runs_and_feeds_processor():
    sim = SimSource(lambda: "walking", rate_hz=50)
    p = CsiProcessor(window_s=2.0)
    import threading

    stop = threading.Event()
    thread = threading.Thread(
        target=sim.run, args=(lambda t, csi: p.push(t, csi), stop),
        daemon=True)
    thread.start()
    time.sleep(1.5)
    stop.set()
    now = time.monotonic()
    assert p.hz(now) > 10
    score = p.motion_score(now)
    assert score is not None and score > 0.02
    snap = p.snapshot(now)
    assert snap["alive"] is True
    assert snap["column"] is not None and snap["column"].size >= 40
