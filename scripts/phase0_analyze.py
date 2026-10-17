"""Phase 0 go/no-go: is walking visibly + statistically different from empty? usage: phase0_analyze.py empty.pcap walking.pcap"""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import mannwhitneyu
sys.path.insert(0, __file__.rsplit("/", 2)[0] + "/src")
from csi import read_pcap

MAC = "2c:cf:67:c5:c4:14"
runs = {}
for name, path in zip(("empty", "walking"), sys.argv[1:3]):
    t, rssi, seq, H = read_pcap(path, MAC)
    runs[name] = (t - t[0], rssi, seq, np.abs(H))
    dur = t[-1] - t[0]
    sq = seq.astype(int) >> 4  # 802.11 seq number is the top 12 bits
    lost = int(((np.diff(sq[sq > 0]) % 4096) - 1).clip(0).sum())
    print(f"{name}: {len(t)} pkts, {dur:.1f}s, {len(t)/dur:.1f} Hz, seq-gap loss ~{lost}, rssi mean {rssi.mean():.1f} std {rssi.std():.2f}")

# usable subcarriers: drop DC/guard junk = those whose mean amplitude is far from the typical level
med = np.median(np.concatenate([A for _, _, _, A in runs.values()]), axis=0)
ok = (med > 0.4 * np.median(med)) & (med < 2.0 * np.median(med))
ok[0] = False
print("usable subcarriers:", ok.sum(), "of", ok.size)

def window_std(t, A, w=1.0):
    """Per 1 s window: mean over usable subcarriers of (std over time / mean amplitude)."""
    out = []
    for k in range(int(t[-1] // w)):
        m = (t >= k * w) & (t < (k + 1) * w)
        if m.sum() > 10:
            a = A[m][:, ok]
            out.append((a.std(0) / a.mean(0)).mean())
    return np.array(out)

ws = {n: window_std(r[0], r[3]) for n, r in runs.items()}
u, p = mannwhitneyu(ws["walking"], ws["empty"], alternative="greater")
print(f"1 s-window variability (CV): empty {ws['empty'].mean():.4f}+-{ws['empty'].std():.4f}, "
      f"walking {ws['walking'].mean():.4f}+-{ws['walking'].std():.4f}, ratio {ws['walking'].mean()/ws['empty'].mean():.2f}x")
print(f"Mann-Whitney walking > empty: U={u:.0f} p={p:.2e}  ->  {'PASS' if p < 0.01 else 'FAIL/INCONCLUSIVE'} (p<0.01)")

fig, ax = plt.subplots(2, 2, figsize=(13, 7), constrained_layout=True)
vmax = max(np.percentile(r[3][:, ok], 99) for r in runs.values())
for i, (n, (t, rssi, seq, A)) in enumerate(runs.items()):
    im = ax[0, i].imshow(A[:, ok].T, aspect="auto", origin="lower", vmin=0, vmax=vmax,
                         extent=[0, t[-1], 0, ok.sum()], cmap="viridis")
    ax[0, i].set(title=f"{n}: CSI amplitude", xlabel="time (s)", ylabel="usable subcarrier")
    ax[1, 0].plot(t, A[:, ok].mean(1), lw=0.7, label=n)
fig.colorbar(im, ax=ax[0, :], label="|CSI|")
ax[1, 0].set(title="mean amplitude over subcarriers", xlabel="time (s)"); ax[1, 0].legend()
ax[1, 1].boxplot([ws["empty"], ws["walking"]], tick_labels=["empty", "walking"])
ax[1, 1].set(title=f"1 s variability, Mann-Whitney p={p:.1e}")
out = "docs/phase0.png"; fig.savefig(out, dpi=110); print("saved", out)
