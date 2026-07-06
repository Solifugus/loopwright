import urllib.error

from loopwright.core.config import Config
from loopwright.notify import ntfy as ntfy_mod
from loopwright.notify.ntfy import Event, NtfyNotifier, NullNotifier, from_config


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_all_design_doc_events_exist():
    names = {e.name for e in Event}
    assert {
        "RUN_STARTED",
        "CHECKPOINT_PASSED",
        "DEPLOYMENT_PASSED",
        "REPEATED_FAILURE",
        "LIMIT_REACHED",
        "APPROVAL_NEEDED",
        "CANDIDATE_READY",
    } <= names


def test_ntfy_posts_message_with_headers(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["headers"] = dict(request.headers)
        captured["method"] = request.get_method()
        return FakeResponse()

    monkeypatch.setattr(ntfy_mod.urllib.request, "urlopen", fake_urlopen)
    notifier = NtfyNotifier("https://ntfy.sh/", "my-topic")
    assert notifier.notify(Event.CHECKPOINT_PASSED, "checkpoint 0003 tagged", project="demo")

    assert captured["url"] == "https://ntfy.sh/my-topic"
    assert captured["method"] == "POST"
    assert captured["data"] == b"checkpoint 0003 tagged"
    assert captured["headers"]["Title"] == "[demo] Checkpoint passed"
    assert captured["headers"]["Priority"] == "default"


def test_high_priority_events():
    for event in (Event.REPEATED_FAILURE, Event.LIMIT_REACHED, Event.APPROVAL_NEEDED):
        assert event.priority == "high"


def test_ntfy_failure_returns_false_not_raise(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ntfy_mod.urllib.request, "urlopen", fake_urlopen)
    notifier = NtfyNotifier("https://ntfy.sh", "t")
    assert notifier.notify(Event.RUN_STARTED, "hello") is False


def test_null_notifier_records():
    notifier = NullNotifier()
    assert notifier.notify(Event.RUN_STARTED, "go", project="p")
    assert notifier.events == [(Event.RUN_STARTED, "go", "p")]


def test_from_config_selects_by_topic():
    assert isinstance(from_config(Config(ntfy_topic=None)), NullNotifier)
    real = from_config(Config(ntfy_topic="loopwright-solifugus"))
    assert isinstance(real, NtfyNotifier)
    assert real.url == "https://ntfy.sh/loopwright-solifugus"
