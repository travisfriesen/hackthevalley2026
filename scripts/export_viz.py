"""Export compact JSON of the four Phase 0 runs for the visualisation page. usage: export_viz.py out.json f1.pcap ..."""
import sys, os, re, json
import numpy as np
from scipy.signal import butter, sosfiltfilt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from csi import read_pcap
MAC = "2c:cf:67:c5:c4:14"
raw = []
for f in sys.argv[2:]:
    t, rssi, seq, H = read_pcap(f, MAC)
    raw.append((os.path.basename(f), t - t[0], rssi, np.abs(H)))
med = np.median(np.concatenate([r[3] for r in raw]), 0)
ok = (med > 0.4 * np.median(med)) & (med < 2 * np.median(med)); ok[0] = False
out = {"runs": [], "nsub": int(ok.sum())}
for name, t, rssi, A in raw:
    A = A[:, ok]; m = A.mean(1)
    g = np.abs(m - np.median(m)) < 0.25 * np.median(m)
    tg, An = t[g], A[g] / A[g].mean(1, keepdims=True)
    grid = np.arange(0, tg[-1], 0.02)
    I = np.array([np.interp(grid, tg, An[:, k]) for k in range(An.shape[1])]).T
    I0 = I - I.mean(0)
    def feat(a, b):
        s = (grid >= a) & (grid < b); return float(I0[s].std(0).mean())
    hop = [(round(a + 1, 2), round(feat(a, a + 2), 4)) for a in np.arange(0, grid[-1] - 2, 0.5)]
    win = [round(feat(k * 2.0, (k + 1) * 2.0), 4) for k in range(int(grid[-1] // 2))]
    bins = np.arange(0, tg[-1], 0.25)
    hm = np.array([An[(tg >= b) & (tg < b + 0.25)].mean(0) if ((tg >= b) & (tg < b + 0.25)).any() else np.ones(An.shape[1]) for b in bins])
    pps = np.histogram(t, bins=np.arange(0, t[-1] + 1, 1.0))[0][:-1]
    out["runs"].append({"name": name, "label": re.match(r"[a-z]+", name).group(), "dur": float(t[-1]), "pkts": int(len(t)),
        "hz": round(len(t) / t[-1], 1), "rssi": [round(float(rssi.mean()), 1), round(float(rssi.std()), 2)],
        "series": hop, "win": win, "pps": pps.tolist(),
        "hm": np.round(hm.T * 100).astype(int).tolist(), "hmdt": 0.25})
json.dump(out, open(sys.argv[1], "w"), separators=(",", ":"))
print("bytes", os.path.getsize(sys.argv[1]))
