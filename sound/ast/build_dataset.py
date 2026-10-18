"""Build manifest.csv: path,label,source,split from ESC-50 + Kaggle + HF scream data."""
import io
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
ESC = HERE / "ESC-50-master" / "meta" / "esc50.csv"
ESC_AUDIO = HERE / "ESC-50-master" / "audio"
KAGGLE = HERE.parent / "data" / "kaggle_scream"
HF = HERE.parent / "data" / "hf_scream"

# ESC-50 classes kept; everything else (animals, weather, appliances...) is dropped.
ESC_LABELS = {
    "crying_baby": "cry", "glass_breaking": "glass", "siren": "siren",
    **{c: "not_distress" for c in [
        "laughing", "coughing", "sneezing", "clapping", "footsteps", "door_wood_knock",
        "dog", "vacuum_cleaner", "keyboard_typing", "snoring", "breathing"]},
}

rows = []
esc = pd.read_csv(ESC)
for r in esc[esc.category.isin(ESC_LABELS)].itertuples():
    rows.append((str(ESC_AUDIO / r.filename), ESC_LABELS[r.category], "esc50",
                 "test" if r.fold == 5 else "val" if r.fold == 4 else "train"))

# Kaggle: no official split, so hash-free deterministic 70/15/15 by shuffled order.
k = [(p, "scream" if d == "Screaming" else "not_distress")
     for d in ("Screaming", "NotScreaming") for p in sorted((KAGGLE / d).glob("*.wav"))]
kdf = pd.DataFrame(k, columns=["path", "label"]).sample(frac=1, random_state=0)
n = len(kdf)
a, b = int(.7 * n), int(.85 * n)
kdf["split"] = ["train"] * a + ["val"] * (b - a) + ["test"] * (n - b)
for r in kdf.itertuples():
    rows.append((str(r.path), r.label, "kaggle", r.split))

# HF clips live in a parquet; dump them to wav. Whole set = out-of-domain "hf" test.
import soundfile as sf
hf = pd.read_parquet(next((HF / "data").glob("*.parquet")))
(HF / "wav").mkdir(exist_ok=True)
for r in hf.itertuples():
    out = HF / "wav" / f"{r.file}.wav"
    if not out.exists():
        out.write_bytes(r.audio["bytes"])
    rows.append((str(out), "scream" if r.label == "Yes" else "not_distress", "hf", "hf_test"))

# AudioSet cry/scream clips: bal_train -> train, eval -> test. The AST backbone saw
# AudioSet's training clips, so only the eval split is an honest held-out test.
AUDIOSET = HERE.parent / "data" / "audioset"
for split, ours in (("bal_train", "train"), ("eval", "test")):
    for p in sorted((AUDIOSET / split).glob("*/*.wav")):
        rows.append((str(p), p.parent.name, "audioset", ours))

# SAFE fall dataset: 3 s clips -> 10 s windows (fall or non-fall clip placed among non-fall
# household noise from the same split), so they look like the live 10 s mic window.
import numpy as np
import torch
import torchaudio.functional as AF

FALL = HERE.parent / "data" / "kaggle_fall"
WIN_DIR = FALL / "windows"
WIN_DIR.mkdir(exist_ok=True)
rng = np.random.default_rng(0)


def read16k(p):
    a, sr = sf.read(p, dtype="float32")
    a = a if a.ndim == 1 else a.mean(1)
    return AF.resample(torch.from_numpy(a), sr, 16000).numpy()


clips = {}  # name -> (is_fall, split)
for p in sorted(FALL.glob("*.wav")):
    fold = int(p.name[:2])
    clips[p] = (p.stem.split("-")[-1] == "01", "train" if fold <= 7 else "val" if fold == 8 else "test")
for split in ("train", "val", "test"):
    bg = [p for p, (f, s) in clips.items() if s == split and not f]
    for p, (is_fall, s) in clips.items():
        if s != split:
            continue
        parts = [read16k(b) for b in rng.choice(bg, 3, replace=False)]
        parts.insert(int(rng.integers(0, 4)), read16k(p))  # main clip at a random slot
        w = np.concatenate(parts)[:160000]
        out = WIN_DIR / f"{p.stem}.wav"
        sf.write(out, w, 16000)
        rows.append((str(out), "fall" if is_fall else "not_distress", "safe_fall", split))

df = pd.DataFrame(rows, columns=["path", "label", "source", "split"])
df.to_csv(HERE / "manifest.csv", index=False)
print(df.groupby(["split", "label"]).size().unstack(fill_value=0))
