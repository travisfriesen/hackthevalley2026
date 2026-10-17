"""Score a labelled CSI session. usage: analyze_session.py data/session1
Per 2 s window: var (std of gain-normalised amp), band (0.3-8 Hz power), shape (mean normalised amp per subcarrier).
Reports per-class stats, still-vs-empty separability, and leave-one-take-out classification (held-out take = unseen recording)."""
import sys, os, re, glob, collections
import numpy as np
from scipy.signal import butter, sosfiltfilt
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import confusion_matrix, roc_auc_score
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from csi import read_pcap

MAC, W = "2c:cf:67:c5:c4:14", 2.0
files = sorted(glob.glob(os.path.join(sys.argv[1], "*.pcap")))
raw = []
for f in files:
    t, rssi, seq, H = read_pcap(f, MAC)
    raw.append((os.path.basename(f), re.match(r"[a-z]+", os.path.basename(f)).group(), t - t[0], np.abs(H), rssi))
med = np.median(np.concatenate([r[3] for r in raw]), 0)
ok = (med > 0.4 * np.median(med)) & (med < 2 * np.median(med)); ok[0] = False
sos = butter(3, [0.3, 8], "band", fs=50, output="sos")

def windows(t, A):
    A = A[:, ok]; m = A.mean(1); g = np.abs(m - np.median(m)) < 0.25 * np.median(m)
    t, A = t[g], A[g]; A = A / A.mean(1, keepdims=True)
    grid = np.arange(0, t[-1], 0.02)
    I = np.array([np.interp(grid, t, A[:, k]) for k in range(A.shape[1])]).T
    Z = I - I.mean(0); B = sosfiltfilt(sos, Z, axis=0)
    out = []
    for k in range(int(grid[-1] // W)):
        s = (grid >= k * W) & (grid < (k + 1) * W)
        out.append([Z[s].std(0).mean(), np.log10((B[s] ** 2).mean() + 1e-9), *I[s].mean(0)])
    return np.array(out)

takes = []
for name, lab, t, A, rssi in raw:
    w = windows(t, A); takes.append((name, lab, w))
    print(f"{name:28s} {lab:9s} {len(t)/t[-1]:5.0f} Hz  rssi {rssi.mean():5.1f}  windows {len(w)}  var {w[:,0].mean():.4f}")

print("\n== per-class window variability (var), pooled over takes ==")
by = collections.defaultdict(list)
for _, lab, w in takes: by[lab].append(w)
for lab, ws in by.items():
    v = np.concatenate([w[:, 0] for w in ws]); print(f"{lab:9s} n={len(v):3d} median {np.median(v):.4f}  IQR {np.percentile(v,25):.4f}-{np.percentile(v,75):.4f}  per-take means {[round(w[:,0].mean(),4) for w in ws]}")

# --- is a STILL person distinguishable from empty? (static presence)
ref = {n: w[:, 2:].mean(0) for n, l, w in takes if l == "empty"}
E = [w for _, l, w in takes if l == "empty"]
print("\n== still person vs empty (AUC; 0.5 = no information) ==")
empty_var = np.concatenate([w[:, 0] for w in E]); empty_band = np.concatenate([w[:, 1] for w in E])
for lab in ("standing", "sitting", "lying"):
    ws = by[lab]; v = np.concatenate([w[:, 0] for w in ws]); b = np.concatenate([w[:, 1] for w in ws])
    auc = lambda x, e: roc_auc_score([0] * len(e) + [1] * len(x), np.concatenate([e, x]))
    # shape distance from the OTHER empty take's mean shape (score empty windows against the take they are not from)
    def dist(w, r): return np.linalg.norm(w[:, 2:] - r, axis=1)
    e_d = np.concatenate([dist(w, np.mean([ref[m] for m in ref if m != n], 0)) for n, l, w in takes if l == "empty"])
    p_d = np.concatenate([dist(w, np.mean(list(ref.values()), 0)) for w in ws])
    print(f"{lab:9s} var AUC {auc(v, empty_var):.2f}   band AUC {auc(b, empty_band):.2f}   shape-distance-from-empty AUC {auc(p_d, e_d):.2f}")

# --- leave-one-take-out classification
CL = {"empty": "empty", "walking": "walking", "standing": "still", "sitting": "still", "lying": "still", "fall": "fall"}
def evalset(name, feats, label_of, mk):
    labs = sorted(set(label_of(l) for _, l, _ in takes)); y_true, y_pred, per = [], [], []
    for i, (n, l, w) in enumerate(takes):
        tr = [(x[2], label_of(x[1])) for j, x in enumerate(takes) if j != i and label_of(x[1]) in labs]
        # a class with no other take left can't be learned; skip that test take
        if label_of(l) not in {c for _, c in tr}: continue
        Xtr = np.concatenate([feats(a) for a, _ in tr]); ytr = np.concatenate([[c] * len(a) for a, c in tr])
        clf = mk().fit(Xtr, ytr); p = clf.predict(feats(w)); y_true += [label_of(l)] * len(p); y_pred += list(p)
        per.append((n, label_of(l), np.mean(p == label_of(l))))
    acc = np.mean(np.array(y_true) == np.array(y_pred))
    print(f"\n[{name}] held-out-take accuracy {acc:.0%} over {len(y_true)} windows")
    cm = confusion_matrix(y_true, y_pred, labels=labs); print("rows=true, cols=pred:", labs)
    for l, r in zip(labs, cm): print(f"  {l:9s}", r, f"recall {r[labs.index(l)]/r.sum():.0%}")
    return per
f_motion = lambda a: a[:, :2]
f_all = lambda a: a
rf = lambda: RandomForestClassifier(200, random_state=0, min_samples_leaf=2)
lr = lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
print("\n== leave-one-take-out classification ==")
evalset("3 classes empty/walking/still, motion feats + LR", f_motion, lambda l: CL[l] if l != "fall" else "walking", lr)
evalset("4 classes + fall(=motion window), motion feats + LR", f_motion, lambda l: CL[l], lr)
evalset("6 classes all activities, motion feats + RF", f_motion, lambda l: l, rf)
evalset("6 classes all activities, motion+shape feats + RF", f_all, lambda l: l, rf)

# --- fall timeline: motion then stillness?
print("\n== fall takes: per-2s-window var over time (burst then quiet?) ==")
E_med = np.median(empty_var)
for n, l, w in takes:
    if l == "fall": print(n, " ".join(f"{x:.3f}" for x in w[:, 0]), f"| empty median {E_med:.3f}")
