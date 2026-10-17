"""Read Nexmon CSI (bcm43455c0) from a pcap: Ethernet/IP/UDP:5500, 18 B header + NFFT x (int16 re, int16 im)."""
import struct
import numpy as np

HDR = 18  # magic(2) rssi(1) fctl(1) src mac(6) seq(2) css(2) chanspec(2) chipver(2)


def read_pcap(path, mac=None):
    """Return (t[s], rssi[dBm], seq, csi[n, nfft] complex) for CSI packets, optionally from one MAC."""
    d = open(path, "rb").read()
    assert struct.unpack("<I", d[:4])[0] == 0xA1B2C3D4, "expected little-endian microsecond pcap"
    o, ts, rssi, seq, rows = 24, [], [], [], []
    want = bytes.fromhex(mac.replace(":", "")) if mac else None
    while o + 16 <= len(d):
        s, us, cl, _ = struct.unpack("<IIII", d[o:o + 16])
        p = d[o + 16:o + 16 + cl]
        o += 16 + cl
        if len(p) < 42 or p[12:14] != b"\x08\x00":
            continue
        ihl = (p[14] & 15) * 4
        if p[23] != 17 or struct.unpack(">H", p[14 + ihl + 2:14 + ihl + 4])[0] != 5500:
            continue
        u = p[14 + ihl + 8:]
        if struct.unpack("<H", u[:2])[0] != 0x1111 or (want and u[4:10] != want):
            continue
        iq = np.frombuffer(u[HDR:], dtype="<i2").astype(np.float32)
        if iq.size % 2:
            continue
        rows.append(iq[0::2] + 1j * iq[1::2])
        ts.append(s + us / 1e6)
        rssi.append(struct.unpack("b", u[2:3])[0])
        seq.append(struct.unpack("<H", u[10:12])[0])
    n = min(len(r) for r in rows)  # drop any odd-sized packet
    return np.array(ts), np.array(rssi), np.array(seq), np.array([r[:n] for r in rows])
