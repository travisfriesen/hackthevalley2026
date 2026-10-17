
import sys
import numpy as np
import soundfile as sf
import librosa
import torch

from transformers import (
    ASTFeatureExtractor,
    ASTForAudioClassification,
)

MODEL_DIR = "./esc50_ast_model"
SR = 16000

if len(sys.argv) != 2:
    print("Usage: python predict_audio.py path/to/audio.wav")
    raise SystemExit(1)

audio, sr = sf.read(sys.argv[1], dtype="float32")

if audio.ndim == 2:
    audio = audio.mean(axis=1)

if sr != SR:
    audio = librosa.resample(
        audio, orig_sr=sr, target_sr=SR
    )

length = 5 * SR
if len(audio) < length:
    audio = np.pad(audio, (0, length - len(audio)))
else:
    audio = audio[:length]

extractor = ASTFeatureExtractor.from_pretrained(MODEL_DIR)
model = ASTForAudioClassification.from_pretrained(MODEL_DIR)
model.eval()

device = torch.device(
    "cuda" if torch.cuda.is_available() else "cpu"
)
model.to(device)

inputs = extractor(
    audio,
    sampling_rate=SR,
    return_tensors="pt",
)
inputs = {k: v.to(device) for k, v in inputs.items()}

with torch.no_grad():
    logits = model(**inputs).logits
    probs = torch.softmax(logits, dim=-1)[0]

values, indices = torch.topk(probs, k=5)

for value, index in zip(values.tolist(), indices.tolist()):
    label = model.config.id2label[index]
    print(f"{label:24s} {value:.3f}")
