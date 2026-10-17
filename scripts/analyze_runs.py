"""Score several empty/walking CSI recordings. usage: analyze_runs.py f1.pcap f2.pcap ...  (label = filename prefix)
Features per 2 s window: std of gain-normalised amplitude ("var") and 0.3-8 Hz band power ("band")."""
import sys, os, re
import numpy as np
from scipy.signal import butter, sosfiltfilt
from scipy.stats import mannwhitneyu
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from csi import read_pcap

MAC, W = "2c:cf:67:c5:c4:14", 2.0
files = sys.argv[1:]
runs = []
for f in files:
    t, rssi, seq, H = read_pcap(f, MAC)
    runs.append((os.path.basename(f), re.match(r"[a-z]+", os.path.basename(f)).group(), t - t[0], np.abs(H)))
    print(f"{runs[-1][0]}: {len(t)} pkts, {len(t)/(t[-1]-t[0]):.0f} Hz, p99 gap {np.percentile(np.diff(t),99)*1e3:.0f} ms, rssi {rssi.mean():.1f}+-{rssi.std():.2f}")

med = np.median(np.concatenate([r[3] for r in runs]), 0)
ok = (med > 0.4 * np.median(med)) & (med < 2 * np.median(med)); ok[0] = False
sos = butter(3, [0.3, 8], "band", fs=50, output="sos")

def windows(t, A):
    A = A[:, ok]; m = A.mean(1)
    g = np.abs(m - np.median(m)) < 0.25 * np.median(m)
    t, A = t[g], A[g]; A = A / A.mean(1, keepdims=True)
    grid = np.arange(0, t[-1], 0.02)
    I = np.array([np.interp(grid, t, A[:, k]) for k in range(A.shape[1])]).T
    I -= I.mean(0); B = sosfiltfilt(sos, I, axis=0)
    v, b = [], []
    for k in range(int(grid[-1] // W)):
        s = (grid >= k * W) & (grid < (k + 1) * W)
        v.append(I[s].std(0).mean()); b.append((B[s] ** 2).mean())
    return np.array(v), np.array(b)

feat = [(n, l, *windows(t, A)) for n, l, t, A in runs]
for i, name in enumerate(("var", "band")):
    E = np.concatenate([f[2 + i] for f in feat if f[1] == "empty"])
    Wk = np.concatenate([f[2 + i] for f in feat if f[1] == "walking"])
    u, p = mannwhitneyu(Wk, E, alternative="greater"); auc = u / (len(E) * len(Wk))
    print(f"\n[{name}] pooled: empty {E.mean():.4f} (n={len(E)}), walking {Wk.mean():.4f} (n={len(Wk)}), "
          f"{Wk.mean()/E.mean():.2f}x, AUC {auc:.2f}, p={p:.1e}")
    for n, l, v, b in feat:
        x = (v, b)[i]; print(f"   {n:34s} mean {x.mean():.4f} median {np.median(x):.4f} n={len(x)}")
    # held-out: threshold from the FIRST empty+walking pair, scored on the rest
    emp = [f for f in feat if f[1] == "empty"]; wlk = [f for f in feat if f[1] == "walking"]
    if len(emp) > 1 and len(wlk) > 1:
        e0, w0 = (emp[0][2 + i], wlk[0][2 + i])
        ths = np.sort(np.concatenate([e0, w0]))
        th = ths[np.argmax([((e0 < t).mean() + (w0 >= t).mean()) / 2 for t in ths])]
        e1 = np.concatenate([f[2 + i] for f in emp[1:]]); w1 = np.concatenate([f[2 + i] for f in wlk[1:]])
        print(f"   held-out: threshold {th:.4f} from round 1 -> later rounds: empty correct {(e1 < th).mean():.0%}, walking correct {(w1 >= th).mean():.0%}")
