"""Gemini TTS free tier: 10 requests a day. Hitting the cap must stop the retries."""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))

import gemini_tts  # noqa: E402

DAILY = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
     "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "74140s"}]}}
PER_MINUTE = {"error": {"code": 429, "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
     "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}


def http_429(body):
    return urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture
def requests(monkeypatch):
    """Replace the network: every request raises the configured error and is counted."""
    state = {"calls": 0, "error": http_429(DAILY)}

    def fake(req, timeout):
        state["calls"] += 1
        raise state["error"]

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)
    return state


SETTINGS = {"gemini_voice": "Kore", "gemini_stagger_s": 0}


def test_daily_cap_is_recognised_with_its_reset_time(tmp_path, requests):
    with pytest.raises(gemini_tts.DailyQuotaExceeded) as caught:
        gemini_tts.generate("k", "m", "Kore", "Hi.", "warm", tmp_path / "x.wav")
    assert caught.value.retry_after_s == 74140


def test_per_minute_limit_is_still_an_ordinary_failure(tmp_path, requests):
    requests["error"] = http_429(PER_MINUTE)
    assert gemini_tts.generate("k", "m", "Kore", "Hi.", "warm", tmp_path / "x.wav") is False


def test_pregeneration_stops_at_the_daily_cap_and_stays_quiet_until_it_resets(tmp_path, requests):
    gemini_tts.pregenerate(SETTINGS, tmp_path, "key")
    assert requests["calls"] == 1                     # one request, not 8 passes x every phrase
    assert gemini_tts.quota_wait_s(tmp_path) > 70000
    gemini_tts.pregenerate(SETTINGS, tmp_path, "key")  # a UI restart the same day
    assert requests["calls"] == 1                     # sends nothing at all


def test_generation_resumes_once_the_marker_has_expired(tmp_path, requests):
    (tmp_path / ".quota_until").write_text("1.0")      # long past
    assert gemini_tts.quota_wait_s(tmp_path) == 0
    gemini_tts.pregenerate(SETTINGS, tmp_path, "key")
    assert requests["calls"] == 1


def test_the_phrases_people_hear_most_are_generated_first():
    order = gemini_tts._priority(["train_fall", "calibrating", "checkin_say", "alert", "train_empty"])
    assert order == ["checkin_say", "alert", "calibrating", "train_empty", "train_fall"]
