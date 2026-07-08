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


# --- provisional ACK/REVERT action buttons (task 9.5) ---


def test_provisional_notification_carries_ack_and_revert_actions():
    notifier = NtfyNotifier("https://ntfy.sh", "t", web_base_url="http://host:8000/")
    actions = notifier._provisional_actions(Event.PROVISIONAL_DECISION, "demo", "abc")
    assert "http://host:8000/projects/demo/provisional/abc/ack" in actions
    assert "http://host:8000/projects/demo/provisional/abc/revert" in actions
    assert actions.count("method=POST") == 2  # both buttons POST
    assert "Ack" in actions and "Revert" in actions


def test_provisional_actions_attached_as_x_actions_header(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.headers)
        return FakeResponse()

    monkeypatch.setattr(ntfy_mod.urllib.request, "urlopen", fake_urlopen)
    notifier = NtfyNotifier("https://ntfy.sh", "t", web_base_url="http://host:8000")
    notifier.notify(Event.PROVISIONAL_DECISION, "guessed schema", project="demo", decision_id="xy")
    # header keys are title-cased by urllib
    assert "/projects/demo/provisional/xy/ack" in captured["headers"]["X-actions"]


def test_non_provisional_events_carry_no_actions():
    notifier = NtfyNotifier("https://ntfy.sh", "t", web_base_url="http://host:8000")
    assert notifier._provisional_actions(Event.CHECKPOINT_PASSED, "demo", "abc") is None


def test_provisional_actions_need_web_base_url_and_id():
    notifier = NtfyNotifier("https://ntfy.sh", "t")  # no web_base_url
    assert notifier._provisional_actions(Event.PROVISIONAL_DECISION, "demo", "abc") is None
    with_url = NtfyNotifier("https://ntfy.sh", "t", web_base_url="http://h")
    assert with_url._provisional_actions(Event.PROVISIONAL_DECISION, "demo", None) is None
