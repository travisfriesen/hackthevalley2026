"""Whisper + a local LLM: hear how the person answers "Are you okay?" (runs on the GPU server).

    python reply_worker.py --pi travis@100.127.214.108
    python reply_worker.py --forced              # on the server, key forced to cloud_gate.sh
    python reply_worker.py --wav answer.wav      # offline: replay a 16 kHz wav, print the result
    python reply_worker.py --say "no I think I broke my hip"   # just test the interpretation

Every utterance heard in the Pi's rolling mic audio is transcribed with
faster-whisper, interpreted (a local Ollama model; keyword rules if it is unreachable)
and appended to the Pi's fall_events.csv as a SPEECH_REPLY row, which the
touchscreen UI reads during a check-in. A SPEECH_ALIVE row every 10 s tells
the UI the worker is up, so it knows to wait for the richer answer.

Same transport as sound/ast/remote_detector.py: the Pi's scripts/pi/cloud_gate.sh
(audio_tail / append_event over ssh); nothing new is opened on the Pi.
Needs: pip install faster-whisper numpy, and `ollama pull qwen2.5:7b` (--llm claude
needs the anthropic package and ANTHROPIC_API_KEY instead)
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import time
import wave

import numpy as np

from reply import alive_row, claude_ask, interpret, ollama_ask, reply_row
from segmenter import SR, Segmenter

log = logging.getLogger("reply_worker")

# Whisper hallucinates these on near-silence
HALLUCINATIONS = {"you", "thank you", "thanks for watching", "bye", "so"}
ALIVE_EVERY = 10.0


class PiLink:
    """New mic audio from the Pi, and events back, through cloud_gate.sh."""

    GATE = "~/hackthevalley2026/scripts/pi/cloud_gate.sh "

    def __init__(self, host: str, forced: bool = False):
        self.cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                    "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/cm-kaze-%C",
                    "-o", "ControlPersist=120", host]
        self.prefix = "" if forced else self.GATE
        self.prev = 0.0

    def now(self) -> float:
        return time.time()

    def fetch(self):
        """(time the newest sample was published, new float32 audio) or None.

        The first call only learns where the stream is: old audio is not replayed.
        """
        try:
            out = subprocess.run(self.cmd + [f"{self.prefix}audio_tail {self.prev:.6f}"],
                                 capture_output=True, timeout=20).stdout
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._unreachable(exc)
            return None
        head, _, pcm = out.partition(b"\n")
        try:
            mtime, nbytes = float(head.split()[0]), int(head.split()[1])
        except (IndexError, ValueError):
            return None
        if len(pcm) != nbytes:
            return None
        first = self.prev == 0.0
        self.prev = mtime
        if first or nbytes == 0:
            return None
        return mtime, np.frombuffer(pcm, "<i2").astype(np.float32) / 32768

    def append(self, line: str) -> None:
        try:
            subprocess.run(self.cmd + [f"{self.prefix}append_event"],
                           input=line.encode(), timeout=15)
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._unreachable(exc)
            log.error("could not deliver to the Pi, dropped: %s", line.strip())

    def _unreachable(self, exc) -> None:
        if time.time() - getattr(self, "_warned", 0) > 30:
            self._warned = time.time()
            log.warning("Pi unreachable (%s); retrying", type(exc).__name__)


class WavLink:
    """Replays a wav as the Pi's audio, one simulated second per fetch (offline tests)."""

    def __init__(self, path: str):
        with wave.open(path) as w:
            assert w.getframerate() == SR and w.getnchannels() == 1, "need 16 kHz mono"
            self.data = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        self.data = np.concatenate([self.data, np.zeros(2 * SR, np.float32)])  # let the last answer end
        self.pos, self.t0 = 0, 1_800_000_000.0

    def now(self) -> float:
        return self.t0 + self.pos / SR

    def fetch(self):
        if self.pos >= len(self.data):
            raise SystemExit
        chunk = self.data[self.pos:self.pos + SR]
        self.pos += len(chunk)
        return self.t0 + self.pos / SR, chunk

    def append(self, line: str) -> None:
        print(line, end="")


def load_whisper(name: str, device: str):
    from faster_whisper import WhisperModel
    compute = "float16" if device == "cuda" else "int8"
    return WhisperModel(name, device=device, compute_type=compute)


def transcribe(model, audio: np.ndarray) -> str:
    segments, _ = model.transcribe(
        audio, language="en", beam_size=1, vad_filter=True,
        condition_on_previous_text=False)
    kept = [s.text.strip() for s in segments
            if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
    text = " ".join(kept).strip()
    if re.sub(r"[^a-z ]", "", text.lower()).strip() in HALLUCINATIONS:
        return ""
    return text


def make_ask(args):
    """The function that reads a transcript, or None for keyword rules only."""
    if args.llm == "ollama":
        try:        # the first call loads the model into the GPU (can take a minute)
            ollama_ask(args.llm_model, args.ollama_host, timeout=180)("hello")
        except Exception as exc:         # noqa: BLE001
            log.warning("ollama warm-up failed (%s); answers fall back to rules until it is up", exc)
        return ollama_ask(args.llm_model, args.ollama_host)
    if args.llm == "claude":
        import anthropic
        return claude_ask(anthropic.Anthropic(), args.llm_model)
    return None


def run(link, args) -> None:
    ask = make_ask(args)
    model = load_whisper(args.whisper, args.device)
    seg = Segmenter()
    last_alive, last_end = 0.0, 0.0
    log.info("listening (whisper %s on %s, interpreter %s)", args.whisper, args.device,
             f"{args.llm}:{args.llm_model}" if ask else "rules")
    while True:
        started = time.time()
        if link.now() - last_alive >= ALIVE_EVERY:
            last_alive = link.now()
            link.append(alive_row(last_alive))
        got = link.fetch()
        if got is not None:
            end_t, samples = got
            if end_t != last_end:
                last_end = end_t
                for utt in seg.feed(samples, end_t):
                    text = transcribe(model, utt.audio)
                    if not text:
                        continue
                    reply = interpret(text, ask)
                    reply.start, reply.end = utt.start, utt.end
                    log.info("%r -> %s (%s: %s)", text, reply.label, reply.source, reply.reason)
                    link.append(reply_row(reply))
        if not isinstance(link, WavLink):
            time.sleep(max(0.0, args.hop - (time.time() - started)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pi", default="travis@100.127.214.108")
    ap.add_argument("--forced", action="store_true", help="the ssh key's forced command is cloud_gate.sh (cloud server)")
    ap.add_argument("--whisper", default="large-v3", help="faster-whisper model (small.en is fine on a CPU)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--llm", default="ollama", choices=["ollama", "claude", "none"],
                    help="what reads the transcript (none = keyword rules only)")
    ap.add_argument("--llm-model", default="qwen2.5:7b",
                    help="model name for --llm (ollama: any pulled model; claude: e.g. claude-haiku-5-5)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--hop", type=float, default=0.5, help="seconds between audio fetches")
    ap.add_argument("--wav", help="offline test: replay this 16 kHz mono wav")
    ap.add_argument("--say", help="offline test: interpret this text and exit")
    args = ap.parse_args()
    if args.say:
        print(interpret(args.say, make_ask(args)))
        return
    run(WavLink(args.wav) if args.wav else PiLink(args.pi, args.forced), args)


if __name__ == "__main__":
    main()
