"""Shared wire format for audio_sender.py (laptop) and audio_receiver.py (Pi).

A sender connects over TCP and sends a 25-byte header, then raw mono int16
little-endian samples forever. The receiver answers the header with one byte:
b"K" (accepted) or b"N" (rejected).

Header: magic "RAUD", version, sample rate (uint32), 16-byte token digest.
"""

from __future__ import annotations

import hashlib
import hmac
import socket
import struct

MAGIC = b"RAUD"
VERSION = 1
DEFAULT_PORT = 5005
HEADER = struct.Struct("!4sBI16s")


def token_digest(token: str | None) -> bytes:
    if not token:
        return bytes(16)
    return hashlib.sha256(token.encode("utf-8")).digest()[:16]


def pack_header(sample_rate: int, token: str | None) -> bytes:
    return HEADER.pack(MAGIC, VERSION, sample_rate, token_digest(token))


def parse_header(data: bytes, token: str | None) -> int:
    """Validate a header and return the sender's sample rate."""
    magic, version, rate, digest = HEADER.unpack(data)
    if magic != MAGIC or version != VERSION:
        raise ValueError("not an audio_sender connection")
    if not hmac.compare_digest(digest, token_digest(token)):
        raise ValueError("wrong or missing token")
    if not 8_000 <= rate <= 192_000:
        raise ValueError(f"unreasonable sample rate {rate}")
    return rate


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)