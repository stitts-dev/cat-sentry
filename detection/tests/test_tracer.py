from pathlib import Path

import pytest

from catsentry.tracer import parse_source, track_cats

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_CLIP = FIXTURES_DIR / "cat_laser_pointer.webm"


def test_parse_source_coerces_webcam_index_to_int():
    assert parse_source("0") == 0
    assert parse_source("2") == 2


def test_parse_source_leaves_paths_and_urls_as_strings():
    assert parse_source("video.mp4") == "video.mp4"
    assert parse_source("http://localhost:8089/stream") == "http://localhost:8089/stream"
    assert parse_source("rtsp://cam.local/stream") == "rtsp://cam.local/stream"


# ponytail: no lightweight way to fake a YOLO model, so the actual
# detect+track path is exercised as a slow, opt-in integration test instead
# of being mocked out. Needs `uv run python scripts/download_fixtures.py`
# first (network) and downloads pretrained YOLO weights on first run
# (network) -- both reasons this is skipped in CI. Run locally with:
#   uv run pytest -m integration
@pytest.mark.integration
@pytest.mark.skipif(not FIXTURE_CLIP.exists(), reason="run scripts/download_fixtures.py first")
def test_track_cats_finds_a_cat_in_fixture_clip():
    frames_and_detections = list(track_cats(str(FIXTURE_CLIP)))
    all_detections = [d for _frame, detections in frames_and_detections for d in detections]

    assert all(frame.ndim == 3 for frame, _detections in frames_and_detections)
    assert len(all_detections) > 0
    assert all(d.track_id is not None for d in all_detections)
    assert all(0.0 <= d.confidence <= 1.0 for d in all_detections)
    assert all(len(d.bbox) == 4 for d in all_detections)
