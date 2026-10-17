"""AST detector for the Pi touchscreen UI, run on this machine (the Pi is too slow for AST).

Every second it pulls the newest 14 s of the Pi's rolling audio over ssh, classifies it, and appends
events to the Pi's fall_events.csv, which the UI already watches:
  - AST (+ head.joblib, or a fine-tuned --model) on the last 10 s -> distress / glass / siren          (status DISTRESS_SOUND)
  - spike.py + fall3s.py on each loud impulse                                  (status REVIEW_CANDIDATE)

Usage: python remote_detector.py [--pi travis@100.127.214.108] [--threshold 0.6] [--fall-threshold 0.8]
Needs the Pi's audio_receiver.py running with a sender connected (scripts/pi/start_audio.sh + phone mic).
Offline test: --wav some.wav --out events.csv replays a file as if it were the Pi's rolling WAV.
"""
import argparse
import logging
import subprocess
import time
import wave
from pathlib import Path

import joblib
import numpy as np
import torch
from transformers import ASTFeatureExtractor, ASTForAudioClassification

import fall3s
from event_row import FIELDS, event_row
from spike import clip_around, find_spikes

HERE = Path(__file__).parent
CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
WIN, FETCH = 10 * SR, 14 * SR
AST_ALERTS = {"distress", "glass", "siren"}
log = logging.getLogger("remote_detector")


class PiLink:
    """The Pi over one multiplexed ssh connection, through scripts/pi/cloud_gate.sh.

    prefix: how to reach the gate. Direct ssh (from the Mac): run the script by path.
    With a restricted key whose forced command is the gate (the cloud server): prefix is empty.
    """

    GATE = "~/hackthevalley2026/scripts/pi/cloud_gate.sh "

    def __init__(self, host, forced=False):
        self.cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ControlMaster=auto",
                    "-o", "ControlPath=~/.ssh/cm-kaze-%C", "-o", "ControlPersist=120", host]
        self.prefix = "" if forced else self.GATE
        self.prev, self.buf = 0.0, np.zeros(0, np.float32)

    def now(self):
        return time.time()

    def fetch(self):
        """(time the newest sample was published, last 14 s as float32) or None. Only new audio is transferred."""
        try:
            out = subprocess.run(self.cmd + [f"{self.prefix}audio_tail {self.prev:.6f}"],
                                 capture_output=True, timeout=20).stdout
        except (subprocess.TimeoutExpired, OSError) as exc:  # Pi offline: wait for it, do not crash
            self._unreachable(exc)
            return None
        head, _, pcm = out.partition(b"\n")
        try:
            mtime, nbytes = float(head.split()[0]), int(head.split()[1])
        except (IndexError, ValueError):
            return None
        if len(pcm) != nbytes:
            return None
        new = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768
        self.buf = np.concatenate([self.buf, new])[-FETCH:]
        self.prev = mtime
        return (mtime, self.buf) if len(self.buf) == FETCH else None

    def append(self, line):
        try:
            subprocess.run(self.cmd + [f"{self.prefix}append_event"], input=line.encode(), timeout=15)
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._unreachable(exc)
            log.error("could not deliver event to the Pi, dropped: %s", line.strip())

    def _unreachable(self, exc):
        if time.time() - getattr(self, "_warned", 0) > 30:
            self._warned = time.time()
            log.warning("Pi unreachable (%s); retrying", type(exc).__name__)


class FileSim:
    """Replays a local WAV as a rolling window, one simulated second per fetch (for offline tests)."""

    def __init__(self, wav, out):
        with wave.open(wav) as w:
            assert w.getframerate() == SR, "wav must be 16 kHz"
            data = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        self.data, self.out, self.t0, self.pos = data, Path(out), 1_800_000_000.0, FETCH
        self.out.write_text(",".join(FIELDS) + "\n")

    def now(self):
        return self.t0 + self.pos / SR

    def fetch(self):
        if self.pos > len(self.data):
            raise SystemExit
        w = self.data[self.pos - FETCH:self.pos]
        self.pos += SR
        return self.t0 + (self.pos - SR) / SR, w

    def append(self, line):
        with open(self.out, "a") as f:
            f.write(line)


def run(link, args):
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    log.info("AST on %s", device)
    if args.model:  # fine-tuned classifier: the whole network, not backbone + logistic head
        extractor = ASTFeatureExtractor.from_pretrained(args.model)
        model = ASTForAudioClassification.from_pretrained(args.model).to(device).eval()
        names = [model.config.id2label[i] for i in range(len(model.config.id2label))]
    else:
        extractor = ASTFeatureExtractor.from_pretrained(CHECKPOINT)
        model = ASTForAudioClassification.from_pretrained(CHECKPOINT).to(device).eval()
        head = joblib.load(HERE / "head.joblib")
        names = list(head.classes_)
    fall3s.load()
    seen_spikes, last_alert, last_end = [], {}, 0.0
    thr = args.threshold if args.threshold is not None else (0.4 if args.model else 0.6)
    sim = isinstance(link, FileSim)
    while True:
        started = time.time()
        got = link.fetch()
        if got is None:
            time.sleep(1)
            continue
        end_t, x = got
        if end_t == last_end:  # nothing new from the Pi yet
            time.sleep(0.3)
            continue
        last_end = end_t
        if link.now() - end_t > 6:
            log.warning("Pi audio is stale (%.0f s old): is a sender connected?", link.now() - end_t)
            time.sleep(2)
            continue

        window = x[-WIN:]
        probs = None
        peak = float(np.abs(x[-2 * SR:]).max())
        ongoing = np.abs(x[-3 * SR:]).max() > 0.02  # the window keeps a sound for 10 s: don't re-alert once it has ended
        if np.abs(window).max() > 0.01:  # skip silence
            feats = extractor(window, sampling_rate=SR, return_tensors="pt")["input_values"].to(device)
            with torch.no_grad():
                if args.model:
                    probs = torch.softmax(model(feats).logits.float(), -1)[0].cpu().numpy()
                else:
                    probs = head.predict_proba(model.audio_spectrogram_transformer(feats).pooler_output.cpu().numpy())[0]
            label, p = names[int(probs.argmax())], float(probs.max())
            if label in AST_ALERTS and ongoing and p >= thr and end_t - last_alert.get(label, 0) > args.cooldown:
                last_alert[label] = end_t
                link.append(event_row(end_t, peak, label, p, "DISTRESS_SOUND"))
                log.info("%s %.2f -> DISTRESS_SOUND", label, p)

        # falls: each loud impulse, once its 1.5 s tail has arrived; skip the first 4 s (detector warm-up)
        for c in find_spikes(x):
            t = end_t - (len(x) - c) / SR
            if c < 4 * SR or c > len(x) - int(1.5 * SR) or any(abs(t - s) < 1.5 for s in seen_spikes):
                continue
            seen_spikes = (seen_spikes + [t])[-50:]
            pf = fall3s.predict_proba(clip_around(x, c))
            log.info("spike at %.1f: P(fall)=%.2f", t, pf)
            # the fine-tuned AST hears the same window: if it calls it an ordinary sound, a spike is a door/dropped object
            benign = probs is not None and args.model and probs[names.index("not_distress")] > args.veto
            if benign:
                log.info("  vetoed: AST says not_distress %.2f", probs[names.index("not_distress")])
            if pf >= args.fall_threshold and not benign:
                link.append(event_row(t, float(np.abs(x[max(0, c - SR):c + SR]).max()), "fall", pf, "REVIEW_CANDIDATE"))

        if not sim:
            time.sleep(max(0.0, args.hop - (time.time() - started)))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pi", default="travis@100.127.214.108")
    ap.add_argument("--forced", action="store_true", help="the ssh key's forced command is cloud_gate.sh (cloud server)")
    ap.add_argument("--model", help="directory of a fine-tuned AST (finetune.py output) instead of the frozen model + head")
    ap.add_argument("--threshold", type=float, help="min AST probability for distress/glass/siren (default 0.6 frozen, 0.4 for --model: its probabilities are flatter)")
    ap.add_argument("--fall-threshold", type=float, default=0.9, help="min fall3s probability for a fall")
    ap.add_argument("--veto", type=float, default=0.5, help="with --model: drop a fall when AST's not_distress probability is above this")
    ap.add_argument("--cooldown", type=float, default=10.0, help="seconds between repeat alerts of one class")
    ap.add_argument("--hop", type=float, default=1.0)
    ap.add_argument("--wav", help="offline test: replay this 16 kHz wav as the Pi's rolling audio")
    ap.add_argument("--out", default="events_test.csv", help="offline test: where to write events")
    args = ap.parse_args()
    run(FileSim(args.wav, args.out) if args.wav else PiLink(args.pi, args.forced), args)


if __name__ == "__main__":
    main()
