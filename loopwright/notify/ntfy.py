"""Notifications for major run events, delivered via ntfy.

Notification failure must never break a run: ``notify`` returns False on any
error instead of raising. ``NullNotifier`` records events for tests and for
hosts with no topic configured.
"""

import urllib.error
import urllib.request
from enum import Enum


class Event(Enum):
    RUN_STARTED = ("Run started", "default", "arrow_forward")
    CHECKPOINT_PASSED = ("Checkpoint passed", "default", "white_check_mark")
    DEPLOYMENT_PASSED = ("Deployment passed", "default", "rocket")
    PROVISIONAL_DECISION = ("Provisional decision logged", "high", "pushpin")
    RULE_VIOLATION = ("Rule violation rejected", "high", "no_entry")
    REPEATED_FAILURE = ("Repeated failure", "high", "rotating_light")
    LIMIT_REACHED = ("Usage limit reached", "high", "hourglass")
    APPROVAL_NEEDED = ("Human approval needed", "high", "raising_hand")
    CANDIDATE_READY = ("Final candidate ready", "high", "tada")
    TEST = ("Loopwright test notification", "min", "wrench")

    def __init__(self, title: str, priority: str, tags: str):
        self.title = title
        self.priority = priority
        self.tags = tags


class NtfyNotifier:
    def __init__(
        self, server: str, topic: str, web_base_url: str | None = None, timeout: int = 10
    ):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.web_base_url = web_base_url.rstrip("/") if web_base_url else None
        self.timeout = timeout

    def _provisional_actions(
        self, event: Event, project: str | None, decision_id: str | None
    ) -> str | None:
        """The ntfy X-Actions value giving a PROVISIONAL notification ACK/REVERT
        buttons that POST to the web UI, or None when not applicable."""
        if event is not Event.PROVISIONAL_DECISION:
            return None
        if not (self.web_base_url and project and decision_id):
            return None
        base = f"{self.web_base_url}/projects/{project}/provisional/{decision_id}"
        return (
            f"http, Ack, {base}/ack, method=POST, clear=true; "
            f"http, Revert, {base}/revert, method=POST, clear=true"
        )

    def notify(
        self,
        event: Event,
        message: str,
        project: str | None = None,
        decision_id: str | None = None,
    ) -> bool:
        title = f"[{project}] {event.title}" if project else event.title
        headers = {"Title": title, "Priority": event.priority, "Tags": event.tags}
        actions = self._provisional_actions(event, project, decision_id)
        if actions:
            headers["X-Actions"] = actions
        request = urllib.request.Request(
            self.url, data=message.encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, TimeoutError):
            return False


class NullNotifier:
    """Swallows notifications; records them so tests can assert on them."""

    def __init__(self):
        self.events: list[tuple[Event, str, str | None]] = []

    def notify(
        self,
        event: Event,
        message: str,
        project: str | None = None,
        decision_id: str | None = None,
    ) -> bool:
        self.events.append((event, message, project))
        return True


def from_config(config) -> NtfyNotifier | NullNotifier:
    if config.ntfy_topic:
        return NtfyNotifier(
            config.ntfy_server, config.ntfy_topic, web_base_url=config.web_base_url
        )
    return NullNotifier()
