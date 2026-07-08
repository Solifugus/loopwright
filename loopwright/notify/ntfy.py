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
    def __init__(self, server: str, topic: str, timeout: int = 10):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.timeout = timeout

    def notify(self, event: Event, message: str, project: str | None = None) -> bool:
        title = f"[{project}] {event.title}" if project else event.title
        request = urllib.request.Request(
            self.url,
            data=message.encode(),
            headers={"Title": title, "Priority": event.priority, "Tags": event.tags},
            method="POST",
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

    def notify(self, event: Event, message: str, project: str | None = None) -> bool:
        self.events.append((event, message, project))
        return True


def from_config(config) -> NtfyNotifier | NullNotifier:
    if config.ntfy_topic:
        return NtfyNotifier(config.ntfy_server, config.ntfy_topic)
    return NullNotifier()
