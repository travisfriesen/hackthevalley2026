"""Pre-generate the UI's voice phrases with the free Gemini TTS tier.

One call per phrase at startup (staggered: the free tier rate-limits), each
saved as a WAV in ui/voice_cache/. Playback later needs no network, so the
alert path never depends on Wi-Fi. If generation fails (no key, offline,
quota), voice.py keeps using espeak-ng / say - nothing breaks.

API: POST /v1beta/models/<model>:generateContent with responseModalities
AUDIO; unary requests return a complete WAV (24 kHz mono 16-bit) as base64
inlineData, which is written to disk untouched.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from voice import PHRASES

log = logging.getLogger("gemini_tts")

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

STYLES = {
    "ready": "warm and friendly",
    "checkin": "calm, clear, and reassuring",
    "checkin_reminder": "calm, clear, and reassuring",
    "im_ok": "warm and friendly",
    "alert": "firm and urgent",
    "alert_reminder": "firm and urgent",
    "helpsent": "reassuring and steady",
    "calibrated": "warm and friendly",
}


def cache_path(cache_dir, key: str):
    return cache_dir / f"{key}.wav"


def missing(cache_dir, voice: str, keys=None) -> list[str]:
    """Phrases with no WAV yet for this voice (cache key includes voice)."""
    out = []
    for key in (keys or PHRASES):
        if not cache_path(cache_dir, f"{voice}_{key}").exists():
            out.append(key)
    return out


def generate(api_key: str, model: str, voice: str, text: str,
             style: str, out_path) -> bool:
    """Generate one phrase; returns True when the WAV was written."""
    body = json.dumps({
        "contents": [{
            "role": "user",
            "parts": [{
                "text": text,
                "speech_metadata": {"style": style},
            }],
        }],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"voice": voice}},
        },
    }).encode("utf-8")
    url = f"{API_BASE}/{model}:generateContent"
    req = urllib.request.Request(
        url, data=body, headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        b64 = (data["candidates"][0]["content"]["parts"][0]
               ["inlineData"]["data"])
        audio = base64.b64decode(b64)
    except (urllib.error.URLError, KeyError, IndexError, ValueError,
            json.JSONDecodeError) as exc:
        log.warning("gemini tts %r failed: %s", text[:32], exc)
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    log.info("gemini tts ok: %s", out_path.name)
    return True


def pregenerate(settings: dict, cache_dir, api_key: str,
                 on_progress=None, stop: threading.Event | None = None):
    """Generate every missing phrase, staggered. Runs on a worker thread
    (call via start_pregeneration)."""
    voice = settings.get("gemini_voice", "Kore")
    model = settings.get("gemini_model", "gemini-3.8-flash-lite-tts")
    stagger = float(settings.get("gemini_stagger_s", 15))
    if not api_key:
        log.info("no Gemini API key; using espeak-ng/say fallback voice")
        if on_progress:
            on_progress("no key")
        return
    for attempt in range(3):
        todo = missing(cache_dir, voice)
        if not todo:
            if on_progress:
                on_progress("done")
            return
        for i, key in enumerate(todo):
            if stop is not None and stop.is_set():
                return
            out = cache_path(cache_dir, f"{voice}_{key}")
            ok = generate(api_key, model, voice, PHRASES[key],
                          STYLES.get(key, "clear and neutral"), out)
            if on_progress:
                on_progress(key, ok)
            if stop is not None and stop.is_set():
                return
            if i < len(todo) - 1:
                if stop is not None:
                    stop.wait(stagger)
                else:
                    time.sleep(stagger)
        if attempt < 2:
            time.sleep(60)             # wait out any rate limit


def start_pregeneration(settings: dict, cache_dir, api_key: str,
                        on_progress=None) -> tuple:
    """Fire-and-forget: returns (thread, stop_event)."""
    stop = threading.Event()
    thread = threading.Thread(
        target=pregenerate,
        args=(settings, cache_dir, api_key, on_progress, stop),
        name="gemini-tts", daemon=True)
    thread.start()
    return thread, stop
