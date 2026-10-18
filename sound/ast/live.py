"""Live mic classifier.

Two detectors run side by side:
  - AST + head.joblib looks at the last 10 s every second -> distress / glass / siren
  - spike.py watches for loud impulses; each one is cut to 3 s and scored by fall3s.py -> fall

Usage: python live.py [--threshold 0.6] [--fall-threshold 0.8] [--hop 1.0] [--save session.wav]
Ctrl+C to stop. The first macOS run will ask for microphone permission for your terminal.
"""
import argparse
import queue
import time
from pathlib import Path

import joblib
import numpy as np
import sounddevice as sd
import soundfile as sf
import torch
from transformers import ASTFeatureExtractor, ASTForAudioClassification

import fall3s
from spike import SpikeTrigger

HERE = Path(__file__).parent
CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
WIN = 10 * SR
AST_ALERTS = {"distress", "glass", "siren"}  # AST's own 'fall' output is shown but not alerted on

ap = argparse.ArgumentParser()
ap.add_argument("--threshold", type=float, default=0.6, help="min AST probability to raise an alert")
ap.add_argument("--fall-threshold", type=float, default=0.8, help="min fall3s probability to raise a fall alert")
ap.add_argument("--hop", type=float, default=1.0, help="seconds between AST classifications")
ap.add_argument("--save", help="also write the whole session to this wav file")
args = ap.parse_args()

device = "mps" if torch.backends.mps.is_available() else "cpu"
extractor = ASTFeatureExtractor.from_pretrained(CHECKPOINT)
model = ASTForAudioClassification.from_pretrained(CHECKPOINT).to(device).eval()
head = joblib.load(HERE / "head.joblib")
fall3s.load()
trigger = SpikeTrigger()

buf = np.zeros(WIN, dtype=np.float32)  # rolling 10 s window, newest samples at the end
session = []
chunks = queue.Queue()

print(f"Listening on: {sd.query_devices(kind='input')['name']}  (Ctrl+C to stop)")
print("Waiting for the first 10 s window to fill with real audio...\n")
try:
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                        callback=lambda indata, *_: chunks.put(indata[:, 0].copy())):
        started = time.time()
        while True:
            time.sleep(args.hop)
            while not chunks.empty():
                x = chunks.get()
                buf = np.concatenate([buf[len(x):], x])
                if args.save:
                    session.append(x)
                event = trigger.update(x)
                if event and event[0] >= 6:  # ignore the mic's start-up click (spike.py also needs time to learn the background)
                    t, clip = event
                    p = fall3s.predict_proba(clip)
                    verdict = "*** FALL ***" if p >= args.fall_threshold else "spike, not a fall"
                    print(f"{t:6.1f}s  {verdict:<22} fall3s P(fall)={p:.2f}")

            feats = extractor(buf, sampling_rate=SR, return_tensors="pt")["input_values"].to(device)
            with torch.no_grad():
                pooled = model.audio_spectrogram_transformer(feats).pooler_output.cpu().numpy()
            probs = head.predict_proba(pooled)[0]
            top = probs.argmax()
            label, p = head.classes_[top], probs[top]
            level = float(np.abs(buf[-SR:]).max())  # peak of the last second
            alert = label in AST_ALERTS and p >= args.threshold
            detail = "  ".join(f"{c}:{q:.2f}" for c, q in zip(head.classes_, probs))
            print(f"{time.time() - started:6.0f}s  {'*** ' + label.upper() + ' ***' if alert else 'ok':<22} "
                  f"level {level:.2f} | {detail}")
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    if args.save and session:
        sf.write(args.save, np.concatenate(session), SR)
        print(f"Saved session to {args.save}")
