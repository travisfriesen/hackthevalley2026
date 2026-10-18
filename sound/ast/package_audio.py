"""Convert every clip in manifest.csv to 16 kHz mono, exactly 10 s, in sound/data/packed/ (for upload to a training server).

Writes sound/data/packed/manifest.csv with paths relative to that folder.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import torch
import torchaudio.functional as AF

HERE = Path(__file__).parent
OUT = HERE.parent / "data" / "packed"
SR, LEN = 16000, 160000

df = pd.read_csv(HERE / "manifest.csv")
OUT.mkdir(parents=True, exist_ok=True)
rel = []
for i, r in enumerate(df.itertuples()):
    a, sr = sf.read(r.path, dtype="float32")
    a = a if a.ndim == 1 else a.mean(1)
    if sr != SR:
        a = AF.resample(torch.from_numpy(a), sr, SR).numpy()
    a = np.pad(a, (0, max(0, LEN - len(a))))[:LEN]
    name = f"{r.source}_{i:05d}.wav"
    sf.write(OUT / name, a, SR, subtype="PCM_16")
    rel.append(name)
    if i % 500 == 0:
        print(f"{i}/{len(df)}", flush=True)
df.assign(path=rel).to_csv(OUT / "manifest.csv", index=False)
print("done", len(df))
