"""NtfyNotifier tests against a fake urllib opener -- no network calls."""

from __future__ import annotations

from catsentry.config import NtfyConfig
from catsentry.notify import NtfyNotifier

TOPIC = "catsentry-alerts"


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class FakeOpener:
    def __init__(self, status: int = 200, raise_error: Exception | None = None) -> None:
        self.status = status
        self.raise_error = raise_error
        self.requests: list = []

    def open(self, request, timeout: float | None = None):
        self.requests.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        return FakeResponse(self.status)


def make_notifier(opener: FakeOpener) -> NtfyNotifier:
    return NtfyNotifier(NtfyConfig(topic=TOPIC), opener=opener)


def test_ignores_event_types_not_worth_alerting_on():
    opener = FakeOpener()
    notifier = make_notifier(opener)

    sent = notifier.notify(
        {"type": "zone_enter", "zone": "floor_left", "ts": "2026-08-19T00:00:00Z"}
    )

    assert sent is False
    assert opener.requests == []


def test_squat_suspected_posts_to_topic_url_with_title_header():
    opener = FakeOpener()
    notifier = make_notifier(opener)

    sent = notifier.notify(
        {"type": "squat_suspected", "zone": "floor_left", "ts": "2026-08-19T00:00:00Z"}
    )

    assert sent is True
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == f"https://ntfy.sh/{TOPIC}"
    assert request.headers["Title"] == "Cat Sentry: squat suspected"


def test_deterrent_fired_without_snapshot_sends_plain_text_message_body():
    opener = FakeOpener()
    notifier = make_notifier(opener)

    notifier.notify({"type": "deterrent_fired", "zone": "floor_left", "ts": "2026-08-19T00:00:03Z"})

    request = opener.requests[0]
    assert request.data == b"zone=floor_left at 2026-08-19T00:00:03Z"
    assert request.headers["Title"] == "Cat Sentry: deterrent fired"
    assert "Filename" not in request.headers


def test_event_with_existing_snapshot_attaches_jpeg_bytes_with_filename_header(tmp_path):
    snapshot = tmp_path / "210400.jpg"
    snapshot.write_bytes(b"\xff\xd8fakejpegbytes")
    opener = FakeOpener()
    notifier = make_notifier(opener)

    notifier.notify(
        {
            "type": "squat_suspected",
            "zone": "floor_left",
            "ts": "2026-08-19T21:04:00Z",
            "snapshot_path": str(snapshot),
        }
    )

    request = opener.requests[0]
    assert request.data == b"\xff\xd8fakejpegbytes"
    assert request.headers["Filename"] == "210400.jpg"
    assert request.headers["Message"] == "zone=floor_left at 2026-08-19T21:04:00Z"


def test_missing_snapshot_file_falls_back_to_plain_text(tmp_path):
    opener = FakeOpener()
    notifier = make_notifier(opener)
    missing = tmp_path / "gone.jpg"

    notifier.notify(
        {
            "type": "squat_suspected",
            "zone": "floor_left",
            "ts": "2026-08-19T00:00:00Z",
            "snapshot_path": str(missing),
        }
    )

    request = opener.requests[0]
    assert request.data == b"zone=floor_left at 2026-08-19T00:00:00Z"
    assert "Filename" not in request.headers


def test_non_2xx_response_returns_false():
    opener = FakeOpener(status=500)
    notifier = make_notifier(opener)

    sent = notifier.notify({"type": "squat_suspected", "zone": "floor_left", "ts": "x"})

    assert sent is False


def test_network_error_is_swallowed_and_returns_false():
    opener = FakeOpener(raise_error=OSError("no network"))
    notifier = make_notifier(opener)

    sent = notifier.notify({"type": "squat_suspected", "zone": "floor_left", "ts": "x"})

    assert sent is False
