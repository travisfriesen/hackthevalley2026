"""Spoken check-in replies: word classification and the listener's reporting."""
import pytest
from speech import SpeechListener, classify


@pytest.mark.parametrize("text,label", [
    ("yes", "ok"), ("yeah", "ok"), ("i am okay", "ok"), ("i'm fine", "ok"),
    ("no", "help"), ("nope", "help"), ("help", "help"),
    ("i need help", "help"), ("not okay", "help"), ("yes i need help", "help"),
    ("", None), ("[unk]", None), ("hello there", None),
])
def test_classify(text, label):
    assert classify(text) == label


class FakeRec:
    """Stands in for KaldiRecognizer: 'hears' a fixed phrase."""
    def __init__(self, phrase):
        self.phrase = phrase

    def AcceptWaveform(self, pcm):
        return True

    def Result(self):
        return '{"text": "%s"}' % self.phrase

    def PartialResult(self):
        return '{"partial": ""}'


def _listener():
    got = []
    lis = SpeechListener({"speech_enabled": False}, lambda: False,
                         lambda label, text: got.append((label, text)))
    return lis, got


def test_feed_reports_once_and_disarms():
    lis, got = _listener()
    lis.arm()
    lis._feed(FakeRec("i need help"), b"\0\0")
    lis._feed(FakeRec("i need help"), b"\0\0")
    assert got == [("help", "i need help")]


def test_feed_ignored_when_disarmed():
    lis, got = _listener()
    lis._feed(FakeRec("yes"), b"\0\0")
    assert got == []
