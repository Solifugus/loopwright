"""The one and only module that talks to OpenAI.

Raw REST via stdlib urllib — no SDK dependency, nothing to mock but HTTP.
Everything else in Loopwright goes through :class:`OpenAIClient.chat`, so
swapping providers later means changing exactly this file.
"""

import json
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIError(Exception):
    """The API call failed; the message says how."""


class OpenAIClient:
    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = DEFAULT_BASE_URL):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def chat(self, messages: list[dict], timeout: int = 120) -> str:
        """Send a chat-completion request; returns the assistant's text."""
        payload = {"model": self.model, "messages": messages}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OpenAIError(f"OpenAI API error {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenAIError(f"could not reach OpenAI: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenAIError(f"unexpected OpenAI response shape: {str(data)[:300]}") from exc
