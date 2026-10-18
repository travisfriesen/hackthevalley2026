"""Full fine-tune of the AudioSet AST on the packed clips (see package_audio.py); meant for a GPU server.

    python finetune.py --data /root/data/packed --out /root/out/run1 [--epochs 8] [--lr 2e-5]

Classes match head.joblib: distress (cry + scream), fall, glass, siren, not_distress.
Keeps the epoch with the best validation macro-F1, then reports val/test/hf_test, so the numbers
are directly comparable with train_head.py. --smoke runs a few clips for a few steps to check the code.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import torch
from sklearn.metrics import classification_report, f1_score
from torch.utils.data import DataLoader, Dataset
from transformers import ASTFeatureExtractor, ASTForAudioClassification

CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
CLASSES = ["distress", "fall", "glass", "not_distress", "siren"]
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--data", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--epochs", type=int, default=8)
ap.add_argument("--bs", type=int, default=4)
ap.add_argument("--accum", type=int, default=2, help="gradient accumulation steps (effective batch = bs x accum)")
ap.add_argument("--lr", type=float, default=2e-5)
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--no-aug", action="store_true")
ap.add_argument("--ckpt", action="store_true", help="gradient checkpointing (less memory, slower)")
ap.add_argument("--smoke", action="store_true")
args = ap.parse_args()

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
data = Path(args.data)
df = pd.read_csv(data / "manifest.csv")
df["label"] = df.label.replace({"cry": "distress", "scream": "distress"})
df["y"] = df.label.map({c: i for i, c in enumerate(CLASSES)})
if args.smoke:
    df = pd.concat([g.head(4) for _, g in df.groupby(["split", "label"])])
train, val = df[df.split == "train"], df[df.split == "val"]
noise_pool = train[train.label == "not_distress"].path.tolist()
extractor = ASTFeatureExtractor.from_pretrained(CHECKPOINT)
rng = np.random.default_rng()


class Clips(Dataset):
    def __init__(self, frame, aug):
        self.f, self.aug = frame.reset_index(drop=True), aug

    def __len__(self):
        return len(self.f)

    def __getitem__(self, i):
        r = self.f.iloc[i]
        x, _ = sf.read(data / r.path, dtype="float32")
        if self.aug:
            x = np.roll(x, int(rng.uniform(-1, 1) * SR))                 # time shift up to 1 s
            x = x * 10 ** (rng.uniform(-6, 6) / 20)                       # gain
            if rng.random() < 0.3:                                        # household noise underneath
                n, _ = sf.read(data / noise_pool[rng.integers(len(noise_pool))], dtype="float32")
                x = x + n * (np.abs(x).std() / (np.abs(n).std() + 1e-8)) * 10 ** (-rng.uniform(5, 25) / 20)
            x = np.clip(x, -1, 1)
        v = extractor(x, sampling_rate=SR, return_tensors="pt")["input_values"][0]
        return v, int(r.y)


def loader(frame, aug, shuffle):
    return DataLoader(Clips(frame, aug), batch_size=args.bs if shuffle else 16, shuffle=shuffle,
                      num_workers=0 if args.smoke else args.workers, drop_last=shuffle)


model = ASTForAudioClassification.from_pretrained(
    CHECKPOINT, num_labels=len(CLASSES), id2label=dict(enumerate(CLASSES)),
    label2id={c: i for i, c in enumerate(CLASSES)}, ignore_mismatched_sizes=True).to(device)
if args.ckpt:
    model.gradient_checkpointing_enable()
counts = train.y.value_counts().reindex(range(len(CLASSES))).fillna(1).values
weights = torch.tensor((counts.sum() / (len(CLASSES) * counts)) ** 0.5, dtype=torch.float32, device=device)
loss_fn = torch.nn.CrossEntropyLoss(weight=weights, label_smoothing=0.1)
opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
train_dl = loader(train, not args.no_aug, True)
steps = max(1, args.epochs * len(train_dl) // args.accum)
sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps + 1, pct_start=0.1)
amp = torch.autocast(device_type="cuda", dtype=torch.bfloat16) if device == "cuda" else torch.autocast("cpu", enabled=False)


@torch.no_grad()
def predict(frame):
    model.eval()
    out = []
    for x, _ in loader(frame, False, False):
        with amp:
            out.append(model(x.to(device)).logits.float().cpu())
    return torch.cat(out).argmax(1).numpy()


def alert_metrics(y, p):
    """Anything other than not_distress counts as an alert, as in the UI."""
    nd = CLASSES.index("not_distress")
    yt, pt = y != nd, p != nd
    return dict(recall=float((pt & yt).sum() / max(1, yt.sum())), precision=float((pt & yt).sum() / max(1, pt.sum())),
                false_alarms=int((pt & ~yt).sum()), normal=int((~yt).sum()), missed=int((~pt & yt).sum()), events=int(yt.sum()))


out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
best = -1.0
for epoch in range(args.epochs):
    model.train()
    t0, running = time.time(), 0.0
    for i, (x, y) in enumerate(train_dl):
        with amp:
            loss = loss_fn(model(x.to(device)).logits.float(), y.to(device)) / args.accum
        loss.backward()
        running += loss.item() * args.accum
        if (i + 1) % args.accum == 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
        if args.smoke and i >= 3:
            break
    p = predict(val)
    f1 = f1_score(val.y, p, average="macro", zero_division=0)
    print(f"epoch {epoch + 1}/{args.epochs}  loss {running / (i + 1):.3f}  val macro-F1 {f1:.3f}  "
          f"alerts {alert_metrics(val.y.values, p)}  [{time.time() - t0:.0f}s]", flush=True)
    if f1 > best:
        best = f1
        model.save_pretrained(out / "ast_ft")
        extractor.save_pretrained(out / "ast_ft")

model = ASTForAudioClassification.from_pretrained(out / "ast_ft").to(device)
results = {}
for split in ("val", "test", "hf_test"):
    f = df[df.split == split]
    if len(f) == 0:
        continue
    p = predict(f)
    print(f"\n=== {split} ({len(f)} clips) ===")
    print(classification_report(f.y, p, labels=range(len(CLASSES)), target_names=CLASSES, zero_division=0))
    results[split] = dict(macro_f1=float(f1_score(f.y, p, average="macro", zero_division=0)), alerts=alert_metrics(f.y.values, p))
    print("alert-level:", results[split]["alerts"])
(out / "metrics.json").write_text(json.dumps(dict(args=vars(args), best_val_f1=best, **results), indent=2))
print("saved", out / "ast_ft")
