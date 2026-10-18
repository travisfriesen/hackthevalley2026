"""Compare the frozen model (backbone + head.joblib) with a fine-tuned one on events placed inside a real room recording.

The normal test split uses whole 10 s clips. In use, a 2-8 s event sits somewhere in a mostly quiet room, so here every held-out
test clip is cut to its loudest 2-8 s, mixed into a random 10 s of room noise at a random offset, and classified by both models.

    python stress_test.py --packed /root/data/packed --noise room.wav --ft models/ast_ft_lr1e-5 --head head.joblib
"""
import argparse
import wave

import joblib
import numpy as np
import pandas as pd
import soundfile as sf
import torch
from transformers import ASTFeatureExtractor, ASTForAudioClassification

SR, WIN = 16000, 160000
CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
ALERTS = {"distress", "glass", "siren", "fall"}
ap = argparse.ArgumentParser()
ap.add_argument("--packed", required=True)
ap.add_argument("--noise", required=True, help="16 kHz mono wav of an ordinary quiet room")
ap.add_argument("--ft", required=True)
ap.add_argument("--head", required=True)
ap.add_argument("--per-group", type=int, default=80)
args = ap.parse_args()
rng = np.random.default_rng(0)
dev = "cuda" if torch.cuda.is_available() else "cpu"

df = pd.read_csv(f"{args.packed}/manifest.csv")
df["label"] = df.label.replace({"cry": "distress", "scream": "distress"})
test = df[df.split == "test"]
rows = pd.concat([g.sample(min(len(g), args.per_group), random_state=0) for _, g in test.groupby("label")])
with wave.open(args.noise) as w:
    room = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def place(x):
    L = int(rng.uniform(2, 8) * SR)
    env = np.convolve(np.abs(x), np.ones(SR // 2) / (SR // 2), "same")  # loudest L seconds
    c = int(np.clip(env.argmax(), L // 2, len(x) - L // 2))
    seg = x[c - L // 2:c + L // 2]
    start = int(rng.integers(0, WIN - len(seg)))
    n0 = int(rng.integers(0, len(room) - WIN))
    out = room[n0:n0 + WIN].copy()
    out[start:start + len(seg)] += seg
    return np.clip(out, -1, 1)


samples = [(place(sf.read(f"{args.packed}/{r.path}", dtype="float32")[0]), r.label) for r in rows.itertuples()]
ft_ex = ASTFeatureExtractor.from_pretrained(args.ft)
ft = ASTForAudioClassification.from_pretrained(args.ft).to(dev).eval()
ft_names = [ft.config.id2label[i] for i in range(len(ft.config.id2label))]
fz = ASTForAudioClassification.from_pretrained(CHECKPOINT).to(dev).eval()
fz_ex = ASTFeatureExtractor.from_pretrained(CHECKPOINT)
head = joblib.load(args.head)


@torch.no_grad()
def run(x):
    a = ft(ft_ex(x, sampling_rate=SR, return_tensors="pt")["input_values"].to(dev)).logits.float().softmax(-1)[0].cpu().numpy()
    emb = fz.audio_spectrogram_transformer(fz_ex(x, sampling_rate=SR, return_tensors="pt")["input_values"].to(dev)).pooler_output
    b = head.predict_proba(emb.cpu().numpy())[0]
    return (ft_names[a.argmax()], a.max()), (head.classes_[b.argmax()], b.max())


res = [(true, *run(x)) for x, true in samples]
print(f"{len(res)} clips; room noise peak {np.abs(room).max():.2f}")
for name, k in (("frozen + head", 1), ("fine-tuned", 0)):
    print(f"\n=== {name} ===")
    for thr in (0.0, 0.3, 0.4, 0.5, 0.6):
        pred = [(t, r[k][0] if r[k][1] >= thr or r[k][0] == "not_distress" else "not_distress") for t, *r in res]
        print(f"  alert threshold {thr}:")
        for cls in ("distress", "fall", "glass", "siren"):
            g = [p for t, p in pred if t == cls]
            if g:
                print(f"    {cls:9s} caught as an alert {np.mean([p in ALERTS for p in g]):.0%}  (exactly right {np.mean([p == cls for p in g]):.0%}, n={len(g)})")
        g = [p for t, p in pred if t == "not_distress"]
        print(f"    normal    false alarms {np.mean([p in ALERTS for p in g]):.0%} (n={len(g)})")
