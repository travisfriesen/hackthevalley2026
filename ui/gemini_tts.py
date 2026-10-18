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


class DailyQuotaExceeded(Exception):
    """The free tier's requests-per-day cap is used up; retrying is pointless."""

    def __init__(self, retry_after_s: float):
        super().__init__(f"daily quota used up, resets in {retry_after_s / 3600:.1f} h")
        self.retry_after_s = retry_after_s


def _quota_marker(cache_dir):
    return cache_dir / ".quota_until"


def quota_wait_s(cache_dir) -> float:
    """Seconds until generation is worth trying again (0 = go ahead)."""
    try:
        return max(0.0, float(_quota_marker(cache_dir).read_text()) - time.time())
    except (OSError, ValueError):
        return 0.0


def _daily_quota_error(exc) -> DailyQuotaExceeded | None:
    """A 429 that is the per-day cap (not the per-minute limit), else None."""
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
        return None
    try:
        body = exc.read().decode("utf-8", "replace")
    except OSError:
        return None
    if "PerDay" not in body:
        return None
    retry = 6 * 3600.0
    try:
        for detail in json.loads(body)["error"]["details"]:
            if detail.get("@type", "").endswith("RetryInfo"):
                retry = float(str(detail["retryDelay"]).rstrip("s"))
    except (ValueError, KeyError, TypeError):
        pass
    return DailyQuotaExceeded(retry)


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
        quota = _daily_quota_error(exc)
        if quota is not None:
            raise quota from exc
        log.warning("gemini tts %r failed: %s", text[:32], exc)
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    log.info("gemini tts ok: %s", out_path.name)
    return True


# what a person hears most often goes first when only a few requests a day are allowed
_FIRST = ("checkin_say", "checkin_reminder_say", "checkin", "checkin_reminder", "alert",
          "alert_reminder", "reprompt", "calibrating", "calibrated")


def _priority(keys: list) -> list:
    return sorted(keys, key=lambda k: (_FIRST.index(k) if k in _FIRST else len(_FIRST), k))


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
    wait = quota_wait_s(cache_dir)
    if wait > 0 and missing(cache_dir, voice):
        log.info("gemini tts: daily quota used up, next try in %.1f h", wait / 3600)
        if on_progress:
            on_progress("quota")
        return
    attempts = 8                       # per-minute 429s clear quickly: keep going
    for attempt in range(attempts):
        todo = missing(cache_dir, voice, keys=_priority(missing(cache_dir, voice)))
        if not todo:
            if on_progress:
                on_progress("done")
            return
        for i, key in enumerate(todo):
            if stop is not None and stop.is_set():
                return
            out = cache_path(cache_dir, f"{voice}_{key}")
            try:
                ok = generate(api_key, model, voice, PHRASES[key],
                              STYLES.get(key, "clear and neutral"), out)
            except DailyQuotaExceeded as quota:
                log.warning("gemini tts: %s; stopping until then (%d phrases still use the "
                            "fallback voice)", quota, len(missing(cache_dir, voice)))
                try:
                    _quota_marker(cache_dir).write_text(str(time.time() + quota.retry_after_s))
                except OSError:
                    pass
                if on_progress:
                    on_progress("quota")
                return
            if on_progress:
                on_progress(key, ok)
            if stop is not None and stop.is_set():
                return
            if i < len(todo) - 1:
                pause = stagger if ok else stagger * 3     # back off after a 429
                if stop is not None:
                    stop.wait(pause)
                else:
                    time.sleep(pause)
        if attempt < attempts - 1:
            if stop is not None:
                if stop.wait(60):      # wait out any rate limit
                    return
            else:
                time.sleep(60)


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
