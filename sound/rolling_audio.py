"""Cross-platform rolling audio capture.

Maintains rolling_audio.wav with up to the latest 300 seconds
of microphone audio. Tested design target: Windows first, Linux later.

Claps (short, broadband, non-speech sounds) are often removed by OS or
driver "voice" processing (noise suppression, AGC, gating) before audio
ever reaches this program. Use --meter to see what actually arrives, and
--exclusive (Windows) to bypass the Windows audio engine's effects.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except (ImportError, OSError):
    # Not installed, or the PortAudio library is missing. Only the parts that
    # touch a microphone need it (audio_receiver.py on the Pi doesn't).
    sd = None


DEFAULT_SAMPLE_RATE = 16_000
SECONDS_TO_KEEP = 300
PUBLISH_INTERVAL = 2.0
RETRY_INTERVAL = 2.0
BLOCK_SIZE = 1024
METER_INTERVAL = 0.5

# On Windows, os.replace() fails with PermissionError while a reader (such as
# fall_detector.py) has the WAV open, so retry briefly instead of giving up.
REPLACE_RETRIES = 10
REPLACE_RETRY_DELAY = 0.05

def default_wav_path() -> Path:
    """On Linux (e.g. a Raspberry Pi) keep the WAV in RAM (/dev/shm).

    The file is rewritten every 2 s (~10 MB each time), which would wear out
    an SD card within weeks. fall_detector.py uses the same default.
    """
    shm = Path("/dev/shm")
    if sys.platform.startswith("linux") and shm.is_dir() and os.access(shm, os.W_OK):
        return shm / "rolling_audio.wav"
    return Path("rolling_audio.wav")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("rolling_audio")


class RollingBuffer:
    """Fixed-size, thread-safe circular buffer of int16 audio samples."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=np.int16)
        self.position = 0
        self.count = 0
        self.lock = threading.Lock()

    def append(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.int16).reshape(-1)

        if samples.size == 0:
            return

        # If a block exceeds capacity, only its newest samples matter.
        if samples.size >= self.capacity:
            samples = samples[-self.capacity:]

        with self.lock:
            end = self.position + samples.size

            if end <= self.capacity:
                self.data[self.position:end] = samples
            else:
                first = self.capacity - self.position
                self.data[self.position:] = samples[:first]
                self.data[:end - self.capacity] = samples[first:]

            self.position = end % self.capacity
            self.count = min(self.capacity, self.count + samples.size)

    def append_silence(self, samples: int) -> None:
        """Advance the time window during microphone outages."""
        while samples > 0:
            n = min(samples, 8192)
            self.append(np.zeros(n, dtype=np.int16))
            samples -= n

    def snapshot(self) -> np.ndarray:
        """Return a chronological copy, safe to write outside the lock."""
        with self.lock:
            if self.count == 0:
                return np.empty(0, dtype=np.int16)

            if self.count < self.capacity:
                return self.data[:self.count].copy()

            return np.concatenate((
                self.data[self.position:],
                self.data[:self.position],
            ))


class Meter:
    """Tracks the peak level of incoming audio so you can see what the
    microphone stream really delivers (e.g. whether claps arrive at all)."""

    def __init__(self) -> None:
        self.peak = 0

    def update(self, samples: np.ndarray) -> None:
        # int32 so abs(-32768) doesn't overflow.
        peak = int(np.abs(samples.astype(np.int32)).max(initial=0))
        if peak > self.peak:
            self.peak = peak

    def take_dbfs(self) -> float:
        peak, self.peak = self.peak, 0
        return 20.0 * math.log10(max(peak, 1) / 32768.0)


def publish_wav(buffer: RollingBuffer, output: Path, sample_rate: int) -> None:
    """Publish a complete WAV by atomically replacing the previous file."""
    audio = buffer.snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None

    try:
        # The temporary file must be on the same filesystem as output.
        with tempfile.NamedTemporaryFile(
            prefix=output.stem + ".",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)

        with wave.open(str(temp_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # signed 16-bit PCM
            wav.setframerate(sample_rate)
            wav.writeframes(audio.astype("<i2", copy=False).tobytes())

        # On Windows, replacement can fail if another process has the
        # destination open without sharing permissions.
        for attempt in range(REPLACE_RETRIES):
            try:
                os.replace(temp_path, output)
                break
            except PermissionError:
                if attempt == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_RETRY_DELAY)
        temp_path = None

    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove temporary file %s", temp_path)


def publisher_loop(
    buffer: RollingBuffer,
    output: Path,
    stop: threading.Event,
    sample_rate: int,
) -> None:
    while not stop.wait(PUBLISH_INTERVAL):
        try:
            publish_wav(buffer, output, sample_rate)
        except OSError:
            log.exception("Could not publish WAV; will retry")

    # Publish the latest buffer during orderly shutdown.
    try:
        publish_wav(buffer, output, sample_rate)
    except OSError:
        log.exception("Final WAV publication failed")


def resolve_device(device: str | int | None, exclusive: bool):
    """In exclusive mode the device must belong to the WASAPI host API."""
    if not exclusive:
        return device

    wasapi = next(
        (h for h in sd.query_hostapis() if "WASAPI" in h["name"]), None
    )
    if wasapi is None:
        raise SystemExit("--exclusive requires the Windows WASAPI host API")

    if device is None:
        if wasapi["default_input_device"] < 0:
            raise SystemExit("No default WASAPI input device found")
        return wasapi["default_input_device"]

    if isinstance(device, int):
        return device  # caller is responsible for picking a WASAPI index

    for index in wasapi["devices"]:
        info = sd.query_devices(index)
        if (info["max_input_channels"] > 0
                and device.lower() in info["name"].lower()):
            return index

    raise SystemExit(f"No WASAPI input device matching {device!r}")


def pick_stream_format(device, rate_arg: int | None) -> tuple[int, int]:
    """Choose (sample_rate, channels) the input device actually supports.

    Prefers 16 kHz mono, then the device's native rate. Some Linux/ALSA
    devices (USB mics opened directly) refuse 16 kHz or mono.
    """
    try:
        info = sd.query_devices(device, "input")
    except (sd.PortAudioError, ValueError) as exc:
        raise SystemExit(
            f"No usable audio input device ({exc}). Plug in a microphone "
            "and run with --list-devices, then pass --device."
        )

    native = int(info["default_samplerate"])
    max_channels = int(info["max_input_channels"])
    rates = [rate_arg] if rate_arg else [DEFAULT_SAMPLE_RATE, native]

    for rate in dict.fromkeys(rates):
        for channels in (1, 2):
            if channels > max_channels:
                continue
            try:
                sd.check_input_settings(
                    device=device,
                    channels=channels,
                    dtype="int16",
                    samplerate=rate,
                )
                return rate, channels
            except sd.PortAudioError:
                continue

    raise SystemExit(
        f"{info['name']!r} does not support 16-bit capture at "
        f"{' or '.join(str(r) for r in rates)} Hz. Try --list-devices and "
        "a different --device, or set --rate."
    )


def capture_loop(
    buffer: RollingBuffer,
    stop: threading.Event,
    device: str | int | None,
    sample_rate: int,
    channels: int,
    extra_settings,
    meter: Meter | None,
) -> None:
    def callback(indata, frames, timing, status):
        if status:
            log.warning("Audio stream status: %s", status)

        if channels == 1:
            mono = indata[:, 0]
        else:
            mono = indata.mean(axis=1).astype(np.int16)

        if meter is not None:
            meter.update(mono)

        # append() copies the data before we return to PortAudio.
        buffer.append(mono)

    while not stop.is_set():
        try:
            log.info(
                "Opening audio input: %s",
                "system default" if device is None else device,
            )

            with sd.InputStream(
                device=device,
                samplerate=sample_rate,
                channels=channels,
                dtype="int16",
                blocksize=BLOCK_SIZE,
                callback=callback,
                extra_settings=extra_settings,
            ) as stream:
                log.info(
                    "Audio capture running at %d Hz, %d channel(s)",
                    sample_rate,
                    channels,
                )
                next_report = time.monotonic() + METER_INTERVAL

                while not stop.wait(0.25):
                    if not stream.active:
                        raise RuntimeError("Audio input stream stopped")

                    if meter is not None and time.monotonic() >= next_report:
                        log.info("Input peak: %6.1f dBFS", meter.take_dbfs())
                        next_report = time.monotonic() + METER_INTERVAL

            if stop.is_set():
                break

        except Exception:
            if stop.is_set():
                break

            log.exception(
                "Audio capture failed. Retrying in %.1f seconds.",
                RETRY_INTERVAL,
            )

            # Represent the disconnected period as silence so the
            # rolling window still advances in wall-clock time.
            deadline = time.monotonic() + RETRY_INTERVAL
            previous = time.monotonic()

            while not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                stop.wait(min(0.25, remaining))
                now = time.monotonic()
                missing = round((now - previous) * sample_rate)
                if missing > 0:
                    buffer.append_silence(missing)
                previous = now

    log.info("Audio capture stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_wav_path(),
        help="Output WAV path (default: %(default)s)",
    )
    parser.add_argument(
        "--device",
        help="Input device name or numeric device index",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices and exit",
    )
    parser.add_argument(
        "--rate",
        type=int,
        help="Sample rate in Hz (default: 16000, or the device's native "
             "rate with --exclusive)",
    )
    parser.add_argument(
        "--exclusive",
        action="store_true",
        help="Windows only: open the mic in WASAPI exclusive mode, which "
             "bypasses Windows audio enhancements (noise suppression, AGC) "
             "that can erase claps",
    )
    parser.add_argument(
        "--meter",
        action="store_true",
        help="Log the input peak level every 0.5 s (to check whether claps "
             "reach the program at all)",
    )
    args = parser.parse_args()

    if sd is None:
        raise SystemExit(
            "sounddevice/PortAudio is not available. Run: pip install "
            "sounddevice  (on Linux also: sudo apt install libportaudio2)"
        )

    if args.list_devices:
        print(sd.query_devices())
        return

    device = args.device
    if device is not None and device.isdigit():
        device = int(device)
    device = resolve_device(device, args.exclusive)

    channels = 1
    extra_settings = None

    if not args.exclusive:
        sample_rate, channels = pick_stream_format(device, args.rate)
    else:
        # Exclusive mode can't resample, so use the device's native format.
        info = sd.query_devices(device)
        channels = max(1, min(2, int(info["max_input_channels"])))
        sample_rate = args.rate or int(info["default_samplerate"])
        extra_settings = sd.WasapiSettings(exclusive=True)
        log.info("Exclusive mode on %s", info["name"])

    log.info("Input format: %d Hz, %d channel(s)", sample_rate, channels)

    meter = Meter() if args.meter else None
    buffer = RollingBuffer(sample_rate * SECONDS_TO_KEEP)
    stop = threading.Event()

    # Publish a valid, initially empty WAV before starting capture.
    publish_wav(buffer, args.output, sample_rate)
    log.info("WAV available at %s", args.output.resolve())

    publisher = threading.Thread(
        target=publisher_loop,
        args=(buffer, args.output, stop, sample_rate),
        name="wav-publisher",
        daemon=True,
    )
    publisher.start()

    try:
        capture_loop(
            buffer, stop, device, sample_rate, channels, extra_settings, meter
        )
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        stop.set()
        publisher.join()
        log.info("Shutdown complete")


if __name__ == "__main__":
    main()