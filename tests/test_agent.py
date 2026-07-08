import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from loopwright import service
from loopwright.agent import openai_client as oc_mod
from loopwright.agent.assistant import (
    HISTORY_LIMIT,
    PacketAssistant,
    load_history,
    parse_response,
    save_history,
)
from loopwright.agent.openai_client import OpenAIClient, OpenAIError
from loopwright.core.model import ProjectStore
from loopwright.web.app import create_app

# --- parsing the model's reply ---


def test_parse_plain_reply():
    reply = parse_response("Sure — what should the tool do?")
    assert reply.message == "Sure — what should the tool do?"
    assert reply.files == {}


def test_parse_reply_with_one_file():
    text = (
        "Here is a first draft.\n\n"
        "===FILE: DESIGN.md===\n# My Tool\n\nDoes things.\n===END===\n"
    )
    reply = parse_response(text)
    assert reply.message == "Here is a first draft."
    assert reply.files == {"DESIGN.md": "# My Tool\n\nDoes things.\n"}


def test_parse_reply_with_all_files():
    text = (
        "Drafted everything.\n"
        "===FILE: DESIGN.md===\ndesign body\n"
        "===FILE: DEVPLAN.md===\n- [ ] 1. first task\n"
        "===FILE: TESTPLAN.md===\ntest body\n===END===\n"
    )
    reply = parse_response(text)
    assert set(reply.files) == {"DESIGN.md", "DEVPLAN.md", "TESTPLAN.md"}
    assert reply.files["DEVPLAN.md"] == "- [ ] 1. first task\n"


def test_parse_ignores_unknown_filenames():
    text = "ok\n===FILE: SECRETS.md===\nnope\n===END===\n"
    assert parse_response(text).files == {}


def test_parse_files_only_gets_placeholder_message():
    text = "===FILE: DESIGN.md===\nx\n===END==="
    reply = parse_response(text)
    assert reply.files["DESIGN.md"] == "x\n"
    assert reply.message  # non-empty placeholder


# --- the assistant's request composition ---


class FakeClient:
    def __init__(self, response="hello"):
        self.response = response
        self.sent: list[list[dict]] = []

    def chat(self, messages, timeout=120):
        self.sent.append(messages)
        return self.response


def test_assistant_composes_messages():
    client = FakeClient("A draft.\n===FILE: DESIGN.md===\nnew design\n===END===")
    assistant = PacketAssistant(client)
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    buffers = {"DESIGN.md": "old design", "DEVPLAN.md": "plan", "TESTPLAN.md": "tests"}

    reply = assistant.chat("make it better", buffers, history)

    messages = client.sent[0]
    assert messages[0]["role"] == "system"
    assert "Primary Agent" in messages[0]["content"]
    assert messages[1:3] == history  # history rides along, bare
    assert "old design" in messages[-1]["content"]  # buffers embedded in final message
    assert "Request: make it better" in messages[-1]["content"]
    assert reply.files == {"DESIGN.md": "new design\n"}


def test_assistant_caps_history():
    client = FakeClient()
    long_history = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    PacketAssistant(client).chat("hi", {}, long_history)
    sent = client.sent[0]
    assert len(sent) == 1 + HISTORY_LIMIT + 1  # system + capped history + new message


def test_history_roundtrip_and_cap(tmp_path):
    history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    save_history(tmp_path, history)
    loaded = load_history(tmp_path)
    assert len(loaded) == HISTORY_LIMIT
    assert loaded[-1]["content"] == "m29"


def test_history_missing_or_corrupt_is_empty(tmp_path):
    assert load_history(tmp_path) == []
    (tmp_path / "chat.json").write_text("{not json")
    assert load_history(tmp_path) == []


# --- the OpenAI client (mocked HTTP) ---


class FakeHTTPResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_openai_client_success(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["auth"] = request.headers.get("Authorization")
        return FakeHTTPResponse(
            {"choices": [{"message": {"role": "assistant", "content": "drafted!"}}]}
        )

    monkeypatch.setattr(oc_mod.urllib.request, "urlopen", fake_urlopen)
    client = OpenAIClient("sk-test", model="gpt-4o")
    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "drafted!"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["payload"]["model"] == "gpt-4o"
    assert captured["auth"] == "Bearer sk-test"


def test_openai_client_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, None
        )

    monkeypatch.setattr(oc_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OpenAIError, match="401"):
        OpenAIClient("bad").chat([{"role": "user", "content": "hi"}])


def test_openai_client_network_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(oc_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OpenAIError, match="could not reach"):
        OpenAIClient("k").chat([])


# --- the chat panel in the web UI ---


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture(autouse=True)
def _doctrine(tmp_path, monkeypatch):
    """create_project requires doctrine (9.3); supply a valid one by default so
    these chat-panel tests can stay focused on the assistant."""
    base = tmp_path / "doctrine"
    base.mkdir()
    (base / "PRINCIPLES.md").write_text("# p\n")
    (base / "AGENT_RULES.md").write_text("# r\n")
    real = service.create_project
    monkeypatch.setattr(
        service,
        "create_project",
        lambda store, name, doctrine_dir=base: real(store, name, doctrine_dir=doctrine_dir),
    )
    return base


def make_client(store, assistant=None):
    return TestClient(create_app(store, assistant=assistant))


def test_packet_page_shows_unavailable_without_key(store):
    service.create_project(store, "demo")
    client = make_client(store, assistant=None)
    page = client.get("/projects/demo/packet")
    assert "Primary Agent" in page.text
    assert "Unavailable" in page.text


def test_chat_drafts_into_editor_buffers_only(store):
    service.create_project(store, "demo")
    fake = FakeClient(
        "Here you go.\n===FILE: DEVPLAN.md===\n- [ ] 1. build the thing\n===END==="
    )
    client = make_client(store, assistant=PacketAssistant(fake))
    before_on_disk = service.load_packet(store, "demo")

    response = client.post(
        "/projects/demo/packet/assistant",
        data={"message": "draft a plan", "design": "D", "devplan": "old", "testplan": "T"},
    )

    assert response.status_code == 200
    assert "- [ ] 1. build the thing" in response.text  # new buffer content
    assert "Here you go." in response.text  # chat reply shown
    # the cardinal rule: the assistant never wrote to disk
    assert service.load_packet(store, "demo") == before_on_disk


def test_chat_history_persists_across_requests(store):
    service.create_project(store, "demo")
    fake = FakeClient("Noted.")
    client = make_client(store, assistant=PacketAssistant(fake))
    client.post(
        "/projects/demo/packet/assistant",
        data={"message": "first question", "design": "", "devplan": "", "testplan": ""},
    )
    page = client.get("/projects/demo/packet")
    assert "first question" in page.text
    assert "Noted." in page.text


def test_chat_api_error_is_shown_not_fatal(store):
    service.create_project(store, "demo")

    class ErrorClient:
        def chat(self, messages, timeout=120):
            raise OpenAIError("OpenAI API error 429: slow down")

    client = make_client(store, assistant=PacketAssistant(ErrorClient()))
    response = client.post(
        "/projects/demo/packet/assistant",
        data={"message": "hi", "design": "keep me", "devplan": "", "testplan": ""},
    )
    assert response.status_code == 200
    assert "429" in response.text
    assert "keep me" in response.text  # buffers preserved on error


def test_chat_without_assistant_shows_setup_hint(store):
    service.create_project(store, "demo")
    client = make_client(store, assistant=None)
    response = client.post(
        "/projects/demo/packet/assistant",
        data={"message": "hi", "design": "", "devplan": "", "testplan": ""},
    )
    assert "unavailable" in response.text.lower()
