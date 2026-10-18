"""Cut a continuous 16 kHz mono stream into spoken utterances.

Energy-based, with a noise floor that adapts to the microphone (a phone mic
and a USB mic differ a lot in level). Whisper's own VAD cleans up afterwards;
this only decides where one answer ends so it can be transcribed promptly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SR = 16000
FRAME = int(0.03 * SR)


@dataclass
class Utterance:
    start: float          # stream clock of the first sample (seconds)
    end: float
    audio: np.ndarray     # float32 mono


class Segmenter:
    def __init__(self, min_speech=0.3, end_silence=0.8, max_len=12.0,
                 preroll=0.3, floor_gain=3.0, min_rms=0.006):
        self.min_speech = min_speech
        self.end_silence = end_silence
        self.max_len = max_len
        self.preroll = preroll
        self.floor_gain = floor_gain
        self.min_rms = min_rms
        self.noise = min_rms / floor_gain      # adaptive noise floor (rms)
        self.origin: float | None = None       # stream clock of sample 0
        self._frames = 0                       # frames consumed so far
        self._tail = np.zeros(0, np.float32)   # samples not yet a full frame
        self._pre: list[np.ndarray] = []       # recent silent frames
        self._speech: list[np.ndarray] = []    # frames of the open utterance
        self._speech_start = 0                 # frame index where it began
        self._voiced = 0                       # voiced frames in it
        self._silent = 0                       # trailing silent frames

    def feed(self, samples: np.ndarray, end_t: float) -> list[Utterance]:
        """Add contiguous audio whose last sample is at stream time end_t."""
        if self.origin is None:
            self.origin = end_t - len(samples) / SR
        data = np.concatenate([self._tail, samples.astype(np.float32)])
        out = []
        n = len(data) // FRAME
        for i in range(n):
            utt = self._frame(data[i * FRAME:(i + 1) * FRAME])
            if utt is not None:
                out.append(utt)
        self._tail = data[n * FRAME:]
        return out

    def _frame(self, frame: np.ndarray) -> Utterance | None:
        idx = self._frames
        self._frames += 1
        rms = float(np.sqrt(np.mean(frame ** 2)))
        voiced = rms > max(self.min_rms, self.noise * self.floor_gain)

        if not self._speech:
            if not voiced:
                self.noise = 0.98 * self.noise + 0.02 * rms
                self._pre.append(frame)
                self._pre = self._pre[-int(self.preroll * SR / FRAME):]
                return None
            self._speech = self._pre + [frame]
            self._speech_start = idx - len(self._pre)
            self._pre = []
            self._voiced, self._silent = 1, 0
            return None

        self._speech.append(frame)
        if voiced:
            self._voiced += 1
            self._silent = 0
        else:
            self._silent += 1
        length = len(self._speech) * FRAME / SR
        if (self._silent * FRAME / SR >= self.end_silence) or length >= self.max_len:
            return self._close()
        return None

    def _close(self) -> Utterance | None:
        frames, voiced = self._speech, self._voiced
        start = self._speech_start
        self._speech, self._voiced, self._silent = [], 0, 0
        if voiced * FRAME / SR < self.min_speech:
            return None
        t0 = self.origin + start * FRAME / SR
        audio = np.concatenate(frames)
        return Utterance(t0, t0 + len(audio) / SR, audio)
