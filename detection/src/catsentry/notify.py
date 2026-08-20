"""ntfy.sh push notifications: squat_suspected / deterrent_fired events get
a title + message + snapshot push, via stdlib urllib -- no `requests` dep.

# ponytail: ntfy's HTTP API takes the request body as either plain message
# text, or raw file bytes (with a `Filename` header) to attach -- so a
# snapshot attaches by PUTting the JPEG bytes as the body instead of
# hand-building multipart/form-data. See https://docs.ntfy.sh/publish/#attach-local-file.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from catsentry.config import NtfyConfig

NTFY_BASE_URL = "https://ntfy.sh"

# Event types worth interrupting a human for, each mapped to its ntfy title;
# see docs/contract-catsentry-v1.md for the full `catsentry/event` type list.
_TITLES = {
    "squat_suspected": "Cat Sentry: squat suspected",
    "deterrent_fired": "Cat Sentry: deterrent fired",
}

NOTIFY_EVENT_TYPES = frozenset(_TITLES)


class NtfyNotifier:
    def __init__(
        self,
        config: NtfyConfig,
        *,
        base_url: str = NTFY_BASE_URL,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._url = f"{base_url}/{config.topic}"
        self._opener = opener if opener is not None else urllib.request.build_opener()

    def notify(self, event: dict) -> bool:
        """Push `event` if its type is worth alerting on. Returns whether a
        push was attempted and accepted; never raises -- a dead network
        shouldn't take detection down.

        Expects the event dict returned by store.save(), so snapshot_path is
        already filled in (wiring order: policy -> store.save -> mqtt/notify).
        """
        event_type = event.get("type")
        if event_type not in NOTIFY_EVENT_TYPES:
            return False

        message = _build_message(event)
        headers = {"Title": _TITLES[event_type]}
        snapshot_path = event.get("snapshot_path")

        if snapshot_path and Path(snapshot_path).exists():
            data = Path(snapshot_path).read_bytes()
            headers["Message"] = message
            headers["Filename"] = Path(snapshot_path).name
        else:
            data = message.encode("utf-8")

        request = urllib.request.Request(self._url, data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=3) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError):
            return False


def _build_message(event: dict) -> str:
    return f"zone={event.get('zone', '?')} at {event.get('ts', '?')}"
