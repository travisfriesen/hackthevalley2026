"""Pi side: receive streamed audio and maintain the rolling WAV.

    python audio_receiver.py

This replaces rolling_audio.py on the Pi: it writes the same rolling WAV
(newest 300 s, atomically replaced every 2 s), so fall_detector.py runs
unchanged. If the laptop disconnects, silence is written so the time window
keeps moving in real time.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from pathlib import Path

import numpy as np

from audio_link import DEFAULT_PORT, HEADER, parse_header, recv_exact
from rolling_audio import (
    SECONDS_TO_KEEP,
    RollingBuffer,
    default_wav_path,
    publish_wav,
    publisher_loop,
)

log = logging.getLogger("audio_receiver")

GAP_TOLERANCE = 1.0       # seconds of unexplained gap before padding silence
IDLE_DROP_SECONDS = 5.0   # drop a sender that has sent nothing for this long


class Receiver:
    def __init__(self, output: Path, token: str | None):
        self.output = output
        self.token = token
        self.stop = threading.Event()
        self.rate: int | None = None
        self.buffer: RollingBuffer | None = None
        self.mark = 0.0           # monotonic time the buffer is current to
        self.publisher: threading.Thread | None = None

    def start_buffer(self, rate: int) -> None:
        self.rate = rate
        self.buffer = RollingBuffer(rate * SECONDS_TO_KEEP)
        self.mark = time.monotonic()
        publish_wav(self.buffer, self.output, rate)
        log.info("WAV available at %s (%d Hz)", self.output.resolve(), rate)
        self.publisher = threading.Thread(
            target=publisher_loop,
            args=(self.buffer, self.output, self.stop, rate),
            name="wav-publisher",
            daemon=True,
        )
        self.publisher.start()

    def catch_up(self) -> None:
        """Pad silence so the window follows wall-clock time during gaps."""
        if self.buffer is None:
            return
        now = time.monotonic()
        missing = round((now - self.mark) * self.rate)
        if missing > 0:
            self.buffer.append_silence(missing)
            self.mark = now

    def serve(self, client: socket.socket, addr) -> None:
        try:
            client.settimeout(3.0)
            rate = parse_header(recv_exact(client, HEADER.size), self.token)
            if self.rate is None:
                self.start_buffer(rate)
            elif rate != self.rate:
                raise ValueError(
                    f"sample rate {rate} Hz, receiver is using {self.rate} Hz"
                    " (restart the receiver to change it)"
                )
            client.sendall(b"K")
        except (ValueError, OSError) as exc:
            log.warning("Rejected %s: %s", addr[0], exc)
            try:
                client.sendall(b"N")
            except OSError:
                pass
            client.close()
            return

        log.info("Sender connected from %s", addr[0])
        client.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        client.settimeout(0.25)
        pending = b""
        last_data = time.monotonic()

        with client:
            while not self.stop.is_set():
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    self.catch_up()
                    if time.monotonic() - last_data > IDLE_DROP_SECONDS:
                        log.warning("No audio for %.0f s; dropping sender",
                                    IDLE_DROP_SECONDS)
                        break
                    continue
                except OSError:
                    break
                if not chunk:
                    break

                now = time.monotonic()
                if now - self.mark > GAP_TOLERANCE:
                    self.catch_up()
                last_data = now

                data = pending + chunk
                usable = len(data) - len(data) % 2
                pending = data[usable:]
                if usable:
                    samples = np.frombuffer(data[:usable], dtype="<i2")
                    self.buffer.append(samples)
                    self.mark += len(samples) / self.rate

        log.info("Sender disconnected; padding silence until it returns")

    def run(self, bind: str, port: int) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((bind, port))
        server.listen(1)
        server.settimeout(0.25)
        log.info("Listening on %s:%d - start audio_sender.py on the laptop",
                 bind, port)

        try:
            while not self.stop.is_set():
                try:
                    client, addr = server.accept()
                except socket.timeout:
                    self.catch_up()
                    continue
                self.serve(client, addr)
        except KeyboardInterrupt:
            log.info("Shutdown requested")
        finally:
            self.stop.set()
            server.close()
            if self.publisher is not None:
                self.publisher.join()
            log.info("Shutdown complete")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="Address to listen on (default: all)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--output", type=Path, default=default_wav_path(),
                        help="Rolling WAV path (default: %(default)s)")
    parser.add_argument("--token", help="Shared secret; must match the laptop")
    args = parser.parse_args()

    Receiver(args.output, args.token).run(args.bind, args.port)


if __name__ == "__main__":
    main()