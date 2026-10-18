"""Understanding free-form answers: rules, Claude parsing, segmentation, CSV rows."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sound" / "speech"))

import reply  # noqa: E402
from segmenter import SR, Segmenter  # noqa: E402


@pytest.mark.parametrize("text,label", [
    ("Yes, I'm fine.", "ok"),
    ("I'm okay", "ok"),
    ("No.", "help"),
    ("I'm not okay", "help"),
    ("Help me", "help"),
    ("I'm fine but my hip hurts", "help"),
    ("I can't get up", "help"),
    ("I fell and I can't move", "help"),
    ("No, I'm fine", "unclear"),          # rules won't guess; Claude reads this one
    ("what time is dinner", "unclear"),
    ("", "unclear"),
])
def test_rule_interpret(text, label):
    assert reply.rule_interpret(text).label == label


def answers(text=None, error=None):
    """An `ask` function that returns a canned model answer (or raises)."""
    calls = []

    def ask(transcript):
        calls.append(transcript)
        if error:
            raise error
        return text
    ask.calls = calls
    return ask


def test_model_answer_is_used():
    ask = answers('Sure: {"label": "ok", "reason": "declines help"}')
    r = reply.interpret("no, I'm fine", ask)
    assert (r.label, r.source, r.reason) == ("ok", "llm", "declines help")
    assert ask.calls == ["no, I'm fine"]


def test_falls_back_to_rules_when_model_fails():
    r = reply.interpret("help me", answers(error=RuntimeError("down")))
    assert (r.label, r.source) == ("help", "rules")


def test_falls_back_to_rules_on_garbage_answer():
    r = reply.interpret("yes", answers("I think they are fine"))
    assert (r.label, r.source) == ("ok", "rules")


def test_no_model_uses_rules():
    assert reply.interpret("yes").label == "ok"


def test_ollama_ask_posts_the_transcript(monkeypatch):
    import io
    import json
    import urllib.request
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return io.BytesIO(json.dumps(
            {"message": {"content": '{"label": "help", "reason": "r"}'}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = reply.interpret("my leg hurts", reply.ollama_ask("m1", "http://h:1"))
    assert r.label == "help" and r.source == "llm"
    assert seen["url"] == "http://h:1/api/chat"
    assert seen["body"]["model"] == "m1" and seen["body"]["format"] == "json"
    assert "my leg hurts" in seen["body"]["messages"][1]["content"]


def test_row_roundtrip_with_commas_and_quotes():
    import csv
    r = reply.Reply("help", "mentions pain", 'my hip, it "hurts"', start=100.5,
                    end=103.25, source="llm")
    values = next(csv.reader([reply.reply_row(r)]))
    assert len(values) == 6 and values[5] == reply.STATUS_REPLY
    back = reply.parse_reply(values[4], float(values[0]))
    assert (back.label, back.text, back.start, back.end) == ("help", 'my hip, it "hurts"', 100.5, 103.25)


def test_parse_reply_rejects_junk():
    assert reply.parse_reply("AST glass 0.91", 1.0) is None
    assert reply.parse_reply('{"label": "maybe"}', 1.0) is None


def _tone(seconds, amp=0.2, hz=220):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _quiet(seconds):
    return (0.001 * np.random.default_rng(0).standard_normal(int(seconds * SR))).astype(np.float32)


def test_segmenter_finds_utterance_with_times():
    audio = np.concatenate([_quiet(2.0), _tone(1.5), _quiet(2.0)])
    seg = Segmenter()
    out = []
    for i in range(0, len(audio), SR):              # fed in 1 s chunks like the Pi link
        out += seg.feed(audio[i:i + SR], 1000.0 + (i + len(audio[i:i + SR])) / SR)
    assert len(out) == 1
    u = out[0]
    assert u.start == pytest.approx(1000.0 + 2.0 - 0.3, abs=0.1)     # 0.3 s pre-roll
    assert 1.5 < len(u.audio) / SR < 3.2


def test_segmenter_splits_two_answers_and_ignores_blips():
    audio = np.concatenate([_quiet(1), _tone(1.0), _quiet(1.5), _tone(0.1), _quiet(1.5),
                            _tone(1.0), _quiet(1.5)])
    seg = Segmenter()
    out = seg.feed(audio, 50.0)
    assert len(out) == 2                            # the 0.1 s blip is too short


@pytest.mark.parametrize("text,echo", [
    ("Are you okay? Say yes, no, or I need help.", True),
    ("Okay, say yes or no. Yes. Or I need help.", True),
    ("Are you okay? Tap the green button.", True),
    ("like 1024 but... Okay, say yes, no, or I need help.", True),
    ("No.", False), ("I need help", False), ("Yes, I'm fine", False),
    ("my hip hurts", False),
])
def test_the_question_itself_is_not_an_answer(text, echo):
    assert reply.looks_like_prompt(text) is echo


# -- the device's own voice -------------------------------------------------
# The audio timeline runs ~0.5 s behind the device clock (lag), so a word spoken
# at device time t is reported at t + 0.5.

LAG = 0.5
PROMPT = ["Are", "you", "okay?", "Say", "yes,", "no,", "or", "I", "need", "help."]


def _heard(prompt_start=100.0, answer=None, answer_at=104.6):
    words, t = [], prompt_start
    if prompt_start is not None:
        for w in PROMPT:
            words.append([w, t + LAG, t + 0.4 + LAG])
            t += 0.42
    if answer:
        for w in answer:
            words.append([w, answer_at + LAG, answer_at + 0.3 + LAG])
            answer_at += 0.35
    return reply.Reply("help", "model said", " ".join(w[0] for w in words), words=words,
                       start=words[0][1], end=words[-1][2])


SPOKE = [[100.0, 104.2]]


def test_answer_glued_to_the_question_is_kept_and_the_question_dropped():
    # the real case: "Are you okay? Say yes, no, or I need help. No." as one utterance
    got = reply.without_own_voice(_heard(answer=["No."]), SPOKE, lag=LAG)
    assert (got.label, got.text) == ("help", "No.")


def test_yes_after_the_question_cancels():
    got = reply.without_own_voice(_heard(answer=["Yes,", "I'm", "fine."]), SPOKE, lag=LAG)
    assert got.label == "unclear" or got.label == "ok"
    assert got.label == "ok"                       # "yes I'm fine"


def test_injury_after_the_question_is_help():
    got = reply.without_own_voice(_heard(answer=["Yes", "but", "my", "hip", "hurts"]), SPOKE, lag=LAG)
    assert got.label == "help"


def test_only_the_question_is_dropped_entirely():
    assert reply.without_own_voice(_heard(), SPOKE, lag=LAG) is None


def test_answer_in_silence_is_untouched():
    quiet = _heard(prompt_start=None, answer=["No."], answer_at=110.0)
    assert reply.without_own_voice(quiet, SPOKE, lag=LAG) is quiet


def test_words_before_the_check_in_began_are_dropped():
    old = _heard(prompt_start=None, answer=["Yes."], answer_at=90.0)
    assert reply.without_own_voice(old, [], floor=100.0, lag=LAG) is None


def test_old_style_rows_without_word_times_still_filter():
    echo = reply.Reply("help", "", "Are you okay? Say yes, no, or I need help.", start=100.5, end=104.7)
    assert reply.without_own_voice(echo, [], lag=LAG) is None
    late = reply.Reply("help", "", "No.", start=110.0, end=110.5)
    assert reply.without_own_voice(late, SPOKE, lag=LAG) is late


def test_word_times_survive_the_csv_row():
    import csv
    r = _heard(answer=["No."])
    r.end = 105.5
    values = next(csv.reader([reply.reply_row(r)]))
    back = reply.parse_reply(values[4], float(values[0]))
    assert [w[0] for w in back.words] == [w[0] for w in r.words]
