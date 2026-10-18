"""Small 3 s / 16 kHz audio fall classifier (SAFE recipe: log-STFT -> linear models)."""
from pathlib import Path

import joblib
import librosa
import numpy as np
import pandas as pd
import soundfile as sf
from spike import clip_around, find_spikes
from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.metrics import precision_recall_fscore_support
from sklearn.naive_bayes import GaussianNB
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).parent
DATA = HERE.parent / "data" / "kaggle_fall"
OUT = HERE / "fall3s.joblib"
SR, N = 16000, 48000
CFG = dict(n_fft=512, hop=160, f_pool=4, t_pool=6)  # bins 0-191 (<6 kHz; resamplers/mics disagree near Nyquist) -> 48x50 = 2400 features
KEEP = 192  # STFT bins kept: 0 to 6 kHz
rng = np.random.default_rng(0)


def features(audio16k):
    x = np.zeros(N, np.float32)
    a = np.asarray(audio16k, np.float32)[:N]
    x[: len(a)] = a
    S = np.abs(librosa.stft(x, n_fft=CFG["n_fft"], hop_length=CFG["hop"])) + 1e-5
    S = np.log(S[:KEEP, :300])
    f, t = CFG["f_pool"], CFG["t_pool"]
    S = S.reshape(KEEP // f, f, 300 // t, t).mean((1, 3))
    return ((S - S.mean()) / (S.std() + 1e-6)).ravel()


def _load(path, start=None):
    info = sf.info(path)
    n = int(info.duration * info.samplerate)
    need = int(3 * info.samplerate)
    s = 0 if start is None else start
    if start is None and n > need:
        s = int(rng.integers(0, n - need))
    x, sr = sf.read(path, start=s, frames=need, dtype="float32", always_2d=True)
    return librosa.resample(x.mean(1), orig_sr=sr, target_sr=SR) if sr != SR else x.mean(1)


def shift(x, max_s=0.5):
    """Move content by a random offset, filling with faint noise (as a real cut would have audio)."""
    k = int(rng.uniform(-max_s, max_s) * SR)
    fill = rng.normal(0, x.std() * 0.03 + 1e-6, len(x)).astype(np.float32)
    y = fill.copy()
    if k >= 0:
        y[k:] = x[: len(x) - k]
    else:
        y[:k] = x[-k:]
    return y


def mix_bg(x, bg, snr_db):
    bg = bg[: len(x)]
    g = x.std() / (bg.std() + 1e-8) * 10 ** (-snr_db / 20)
    return x + g * bg


def _safe():
    d = {}
    for p in sorted(DATA.glob("*.wav")):
        a, _, _, _, ff = p.stem.split("-")
        d[p] = (int(a), int(ff) == 1)
    return d


def _foreign():
    """Non-fall clips from other datasets, grouped by kind. Even-indexed esc50 clips are kept for bg mixing."""
    m = pd.read_csv(HERE / "manifest.csv")
    m = m[m.source != "safe_fall"].reset_index(drop=True)
    kind = m.label.replace({"not_distress": "other"})
    kind[(m.source == "kaggle") & (m.label == "not_distress")] = "speech"
    kind[(m.source == "hf") & (m.label == "not_distress")] = "speech"
    m["kind"] = kind
    bg = m[(m.source == "esc50") & (m.label == "not_distress")].path.tolist()[::2]
    test = m[~m.path.isin(bg)]
    return test, bg


def live_cut(x, ctx):
    """Cut x the way live use does: put it between two context clips, run the spike detector,
    and take 3 s around the spike that lands inside x. None if no spike lands there."""
    w = np.concatenate([ctx[0], x, ctx[1]])
    for c in find_spikes(w):
        if N <= c < 2 * N:
            return clip_around(w, c)
    return None


def _live_set(x, y, pool, copies):
    out = [(live_cut(a, [pool[i] for i in rng.integers(len(pool), size=2)]), l)
           for _ in range(copies) for a, l in zip(x, y)]
    out = [(a, l) for a, l in out if a is not None]
    return [a for a, _ in out], np.array([l for _, l in out])


def _build(x, y, bgs, aug):
    X, Y = [features(a) for a in x], list(y)
    for _ in range(aug):
        for a, l in zip(x, y):
            b = shift(a)
            if rng.random() < 0.5:
                b = mix_bg(b, bgs[rng.integers(len(bgs))], rng.uniform(5, 25))
            X.append(features(b)); Y.append(l)
    return np.array(X), np.array(Y)


def _prf(y, p):
    pr, rc, f1, _ = precision_recall_fscore_support(y, p, average="binary", zero_division=0)
    return dict(acc=float((y == p).mean()), prec=pr, rec=rc, f1=f1)


def train(aug=3):
    safe = _safe()
    test_f, bg_paths = _foreign()
    bgs = [_load(p) for p in bg_paths]
    clips = {fo: ([], []) for fo in range(1, 11)}
    for p, (fo, lab) in safe.items():
        x = librosa.resample(sf.read(p, dtype="float32")[0], orig_sr=48000, target_sr=SR)
        clips[fo][0].append(x); clips[fo][1].append(lab)
    cat = lambda fs: ([a for f in fs for a in clips[f][0]], np.array([l for f in fs for l in clips[f][1]]))
    (xtr, ytr), (xva, yva), (xte, yte) = cat(range(1, 8)), cat([8]), cat([9, 10])
    Xtr, Ytr = _build(xtr, ytr, bgs, aug)
    # spike-cut copies: same alignment the live pipeline produces (event onset, not event middle)
    lx, ly = _live_set(xtr, ytr, [a for a, l in zip(xtr, ytr) if not l], 3)
    Xtr, Ytr = np.vstack([Xtr, [features(a) for a in lx]]), np.concatenate([Ytr, ly])
    Xva, Yva = _build(xva, yva, bgs, 1)  # val = clean + one shifted/mixed copy: picks shift-robust params
    Xte = np.array([features(a) for a in xte])
    Xte_s = np.array([features(shift(a)) for a in xte])

    # hyperparameters chosen on fold 8 only
    best = lambda cands, score: max(cands, key=score)
    lr = best([make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=3000)) for c in (1e-4, 1e-3, 1e-2, 1e-1)],
             lambda m: _prf(Yva, m.fit(Xtr, Ytr).predict(Xva))["f1"])
    rd = best([make_pipeline(StandardScaler(), RidgeClassifier(alpha=a)) for a in (1e2, 1e3, 1e4, 1e5)],
             lambda m: _prf(Yva, m.fit(Xtr, Ytr).predict(Xva))["f1"])
    nb = best([GaussianNB(var_smoothing=v) for v in (1e-3, 1e-2, 1e-1, 1, 3)],  # F2: favour recall
              lambda m: (lambda r: 5 * r["prec"] * r["rec"] / (4 * r["prec"] + r["rec"] + 1e-9))(_prf(Yva, m.fit(Xtr, Ytr).predict(Xva))))
    print("chosen:", lr[-1], rd[-1], nb)
    joblib.dump(dict(confirm=lr, ridge=rd, candidate=nb, cfg=CFG), OUT)

    # ---- report ----
    cand = lambda X: nb.predict_proba(X)[:, 1]
    conf = lambda X: lr.predict_proba(X)[:, 1]
    rows = {}
    for tag, X in (("centered", Xte), ("shifted +-0.5s", Xte_s)):
        print(f"\n== test folds 9-10 ({len(yte)} clips), {tag} ==")
        for name, p in (("ridge", rd.predict(X)), ("logreg", conf(X) >= 0.5), ("gaussNB", cand(X) >= 0.5),
                        ("two-stage NB>=.5 & LR>=.5", (cand(X) >= 0.5) & (conf(X) >= 0.5)),
                        ("two-stage NB>=.5 & LR>=.8", (cand(X) >= 0.5) & (conf(X) >= 0.8))):
            r = _prf(yte, np.asarray(p, bool)); print(f"{name:28s}" + " ".join(f"{k}={v:.3f}" for k, v in r.items()))

    # end to end: test clips cut by the spike detector, as live (several random contexts each)
    lx, ly = _live_set(xte, yte, [a for a, l in zip(xte, yte) if not l], 3)
    XL = np.array([features(a) for a in lx]); pl = conf(XL)
    nf, nn = int((yte == 1).sum()) * 3, int((yte == 0).sum()) * 3
    print(f"\n== spike-cut test clips ({len(ly)} of {len(yte) * 3} had a spike) ==")
    for th in (0.5, 0.8, 0.9):
        print(f"LR>={th}: falls caught {int(((pl >= th) & (ly == 1)).sum())}/{nf} (no-spike misses included), "
              f"non-fall spikes flagged {int(((pl >= th) & (ly == 0)).sum())}/{int((ly == 0).sum())}")

    # false alarms on foreign negatives (clean + shifted irrelevant: random crops)
    rows = []
    for kind, g in test_f.groupby("kind"):
        for p in g.path.sample(min(len(g), 70), random_state=0):
            rows.append((kind, features(_load(p))))
    kinds = np.array([k for k, _ in rows]); XF = np.array([f for _, f in rows])
    c, l = cand(XF) >= 0.5, conf(XF)
    print(f"\n== foreign negatives: {len(XF)} random 3 s crops (false-alarm rate) ==")
    print(f"{'kind':10s} n   NB    LR>=.5  LR>=.8  LR>=.9  two-stage(.5/.8)")
    for k in list(np.unique(kinds)) + ["ALL"]:
        m = np.ones(len(XF), bool) if k == "ALL" else kinds == k
        print(f"{k:10s} {m.sum():3d} {c[m].mean():.3f} {(l[m]>=.5).mean():.3f}   {(l[m]>=.8).mean():.3f}   "
              f"{(l[m]>=.9).mean():.3f}   {(c[m]&(l[m]>=.8)).mean():.3f}")


_M = None


def load():
    global _M
    if _M is None:
        _M = joblib.load(OUT)
    return _M


def predict_proba(audio16k):
    """P(fall) from the confirm model (logistic regression)."""
    return float(load()["confirm"].predict_proba(features(audio16k)[None])[0, 1])


def candidate_proba(audio16k):
    """P(fall) from the high-recall GaussianNB first stage (saturates near 0/1)."""
    return float(load()["candidate"].predict_proba(features(audio16k)[None])[0, 1])


if __name__ == "__main__":
    train()
