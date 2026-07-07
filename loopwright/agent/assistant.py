"""The Primary Agent: drafts and refines the design packet in conversation.

Boundary rule from the design doc, enforced structurally: this module returns
*proposed* file contents to the caller — it has no code path that writes
packet files or touches git. Only the human's Save/Approve actions persist
anything.

Protocol with the model: it replies conversationally, and when it proposes
file changes it appends complete replacements in ``===FILE: name===`` blocks,
which :func:`parse_response` extracts.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PACKET_FILES = ("DESIGN.md", "DEVPLAN.md", "TESTPLAN.md")
HISTORY_FILENAME = "chat.json"
HISTORY_LIMIT = 20  # messages kept and re-sent for continuity

SYSTEM_PROMPT = """\
You are the Primary Agent of Loopwright, a system where a human approves a \
design packet (DESIGN.md, DEVPLAN.md, TESTPLAN.md) and autonomous worker \
agents then implement it. You help the human draft and refine that packet.

Guidelines for the packet:
- DESIGN.md: purpose, requirements, and concrete acceptance criteria.
- DEVPLAN.md: small ordered tasks, each completable by a coding agent in one \
session, as '- [ ] N. description' checkboxes.
- TESTPLAN.md: how the product is verified, including what \
scripts/deploy.sh and scripts/acceptance.sh must do on a clean machine.

When you propose changes to packet files, first reply conversationally in a \
sentence or two, then append the COMPLETE new content of each changed file \
using exactly this format:

===FILE: DESIGN.md===
<entire file content>
===END===

Use one block per changed file and only include files you are changing. \
Never use that marker format for anything else. You cannot commit or write \
anything yourself — the human reviews your draft in the editor and decides.\
"""

_FILE_BLOCK_RE = re.compile(
    r"===FILE:\s*(?P<name>[A-Z]+\.md)\s*===\s*\n(?P<body>.*?)(?=\n?===FILE:|\n?===END===|\Z)",
    re.DOTALL,
)


@dataclass
class AssistantReply:
    message: str
    files: dict[str, str] = field(default_factory=dict)


def parse_response(text: str) -> AssistantReply:
    """Split the model's reply into chat text and proposed file contents."""
    files = {}
    for match in _FILE_BLOCK_RE.finditer(text):
        name = match.group("name")
        if name in PACKET_FILES:
            body = match.group("body").strip("\n")
            files[name] = body + "\n" if body else ""
    first_marker = text.find("===FILE:")
    message = (text if first_marker < 0 else text[:first_marker]).strip()
    return AssistantReply(message=message or "(updated the packet files)", files=files)


def _buffers_block(buffers: dict[str, str]) -> str:
    parts = []
    for name in PACKET_FILES:
        parts.append(f"--- {name} (current editor content) ---\n{buffers.get(name, '')}")
    return "\n\n".join(parts)


class PacketAssistant:
    """Wraps a chat client with the packet protocol. Client is injectable."""

    def __init__(self, client):
        self.client = client

    def chat(self, message: str, buffers: dict[str, str], history: list[dict]) -> AssistantReply:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history[-HISTORY_LIMIT:])
        messages.append(
            {
                "role": "user",
                "content": f"{_buffers_block(buffers)}\n\nRequest: {message}",
            }
        )
        return parse_response(self.client.chat(messages))


def load_history(packet_dir: Path) -> list[dict]:
    path = Path(packet_dir) / HISTORY_FILENAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save_history(packet_dir: Path, history: list[dict]) -> None:
    packet_dir = Path(packet_dir)
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / HISTORY_FILENAME).write_text(
        json.dumps(history[-HISTORY_LIMIT:], indent=2), encoding="utf-8"
    )


def assistant_from_config(config) -> PacketAssistant | None:
    """Build the real assistant, or None when no API key is available."""
    import os

    from loopwright.agent.openai_client import OpenAIClient

    api_key = os.environ.get(config.openai_api_key_env)
    if not api_key:
        return None
    return PacketAssistant(OpenAIClient(api_key, model=config.openai_model))
