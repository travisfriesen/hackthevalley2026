"""Run frozen AudioSet-finetuned AST over manifest.csv; save pooled embeddings + 527 AudioSet logits."""
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import torch
import torchaudio.functional as AF
from transformers import ASTFeatureExtractor, ASTForAudioClassification

HERE = Path(__file__).parent
CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
LEN = 10 * SR
BATCH = 16

device = "mps" if torch.backends.mps.is_available() else "cpu"
extractor = ASTFeatureExtractor.from_pretrained(CHECKPOINT)
model = ASTForAudioClassification.from_pretrained(CHECKPOINT).to(device).eval()


def load(path):
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if sr != SR:
        audio = AF.resample(torch.from_numpy(audio), sr, SR).numpy()
    return np.pad(audio, (0, max(0, LEN - len(audio))))[:LEN]


df = pd.read_csv(HERE / "manifest.csv")
emb, logits = [], []
# manifest rows are append-only, so reuse embeddings already computed for the first rows
done = 0
if (HERE / "embeddings.npz").exists():
    old = np.load(HERE / "embeddings.npz")
    done = len(old["emb"])
    emb, logits = [old["emb"]], [old["logits"]]
for i in range(done, len(df), BATCH):
    wavs = [load(p) for p in df.path[i:i + BATCH]]
    x = extractor(wavs, sampling_rate=SR, return_tensors="pt")["input_values"].to(device)
    with torch.no_grad():
        pooled = model.audio_spectrogram_transformer(x).pooler_output
        logits.append(model.classifier(pooled).cpu().numpy())
        emb.append(pooled.cpu().numpy())
    if (i // BATCH) % 10 == 0:
        print(f"{i}/{len(df)}", flush=True)

np.savez(HERE / "embeddings.npz", emb=np.concatenate(emb), logits=np.concatenate(logits))
print("done")
