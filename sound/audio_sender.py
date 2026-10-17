"""Laptop side: capture the microphone and stream it to the Raspberry Pi.

    python audio_sender.py --host raspberrypi.local

Run audio_receiver.py on the Pi first. The stream reconnects by itself if the
network or the microphone drops out.
"""

from __future__ import annotations

import argparse
import logging
import queue
import socket
import threading

import numpy as np

import rolling_audio
from audio_link import DEFAULT_PORT, pack_header, recv_exact
from rolling_audio import BLOCK_SIZE, pick_stream_format

sd = rolling_audio.sd
log = logging.getLogger("audio_sender")

RECONNECT_SECONDS = 2.0


def send_loop(host, port, rate, token, blocks, stop, mic_alive) -> None:
    while not stop.is_set() and mic_alive():
        try:
            sock = socket.create_connection((host, port), timeout=3.0)
        except OSError as exc:
            log.warning("Can't reach %s:%d (%s); retrying", host, port, exc)
            stop.wait(RECONNECT_SECONDS)
            continue

        with sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(5.0)
            try:
                sock.sendall(pack_header(rate, token))
                if recv_exact(sock, 1) != b"K":
                    log.error("The Pi rejected the connection (wrong "
                              "--token, or a different sample rate than "
                              "the receiver is already using)")
                    stop.wait(5.0)
                    continue

                # Audio captured while we were disconnected is stale; sending
                # it now would shift every timestamp on the Pi.
                while True:
                    try:
                        blocks.get_nowait()
                    except queue.Empty:
                        break

                log.info("Streaming to %s:%d at %d Hz", host, port, rate)
                while not stop.is_set() and mic_alive():
                    try:
                        data = blocks.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    sock.sendall(data)

            except OSError as exc:
                log.warning("Connection lost (%s); reconnecting", exc)
                stop.wait(RECONNECT_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="Pi hostname or IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device", help="Input device name or index")
    parser.add_argument("--rate", type=int,
                        help="Sample rate (default: 16000, else the mic's)")
    parser.add_argument("--token", help="Shared secret; must match the Pi")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    if sd is None:
        raise SystemExit("sounddevice/PortAudio is not available: "
                         "pip install sounddevice")
    if args.list_devices:
        print(sd.query_devices())
        return
    if not args.host:
        parser.error("--host is required")

    device = args.device
    if device is not None and device.isdigit():
        device = int(device)

    rate, channels = pick_stream_format(device, args.rate)
    blocks: queue.Queue = queue.Queue(maxsize=300)   # ~19 s of audio at most
    stop = threading.Event()

    def callback(indata, frames, timing, status):
        if status:
            log.warning("Audio stream status: %s", status)
        mono = (indata[:, 0] if channels == 1
                else indata.mean(axis=1).astype(np.int16))
        data = mono.astype("<i2").tobytes()
        try:
            blocks.put_nowait(data)
        except queue.Full:                 # keep the newest audio
            try:
                blocks.get_nowait()
                blocks.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    try:
        while not stop.is_set():
            try:
                with sd.InputStream(
                    device=device, samplerate=rate, channels=channels,
                    dtype="int16", blocksize=BLOCK_SIZE, callback=callback,
                ) as stream:
                    log.info("Microphone open: %d Hz, %d channel(s)",
                             rate, channels)
                    send_loop(args.host, args.port, rate, args.token,
                              blocks, stop, lambda: stream.active)
            except Exception:
                log.exception("Microphone failed; retrying")
                stop.wait(RECONNECT_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        stop.set()


if __name__ == "__main__":
    main()