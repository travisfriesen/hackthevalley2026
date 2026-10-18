"""Slide a 10 s window over an audio file and flag distress sounds.

Usage: python predict.py audio.wav [--hop 2] [--threshold 0.6]
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import soundfile as sf
import torch
import torchaudio.functional as AF
from transformers import ASTFeatureExtractor, ASTForAudioClassification

HERE = Path(__file__).parent
CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
WIN = 10 * SR

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--hop", type=float, default=2.0, help="seconds between windows")
ap.add_argument("--threshold", type=float, default=0.6, help="min probability to flag")
args = ap.parse_args()

audio, sr = sf.read(args.audio, dtype="float32")
if audio.ndim == 2:
    audio = audio.mean(axis=1)
if sr != SR:
    audio = AF.resample(torch.from_numpy(audio), sr, SR).numpy()
audio = np.pad(audio, (0, max(0, WIN - len(audio))))

device = "mps" if torch.backends.mps.is_available() else "cpu"
extractor = ASTFeatureExtractor.from_pretrained(CHECKPOINT)
model = ASTForAudioClassification.from_pretrained(CHECKPOINT).to(device).eval()
head = joblib.load(HERE / "head.joblib")

starts = list(range(0, len(audio) - WIN + 1, int(args.hop * SR)))
for s in starts:
    x = extractor(audio[s:s + WIN], sampling_rate=SR, return_tensors="pt")["input_values"].to(device)
    with torch.no_grad():
        pooled = model.audio_spectrogram_transformer(x).pooler_output.cpu().numpy()
    probs = head.predict_proba(pooled)[0]
    label, p = head.classes_[probs.argmax()], probs.max()
    if label != "not_distress" and p >= args.threshold:
        print(f"{s / SR:7.1f}s-{s / SR + 10:6.1f}s  {label:6s} {p:.2f}")
print(f"scanned {len(starts)} windows")
