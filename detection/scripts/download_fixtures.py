#!/usr/bin/env python3
"""Download a couple of short CC-licensed cat clips for local testing.

Used by the (opt-in, `-m integration`) YOLO smoke test in
tests/test_tracer.py -- not run in CI, both for network and file-size reasons.

Run manually:
    uv run python scripts/download_fixtures.py

Source: Wikimedia Commons, Category:Videos of cats, both CC BY-SA. See each
file's page for the individual author/attribution if you redistribute them.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Wikimedia rejects requests without a descriptive User-Agent (returns 403).
# https://meta.wikimedia.org/wiki/User-Agent_policy
USER_AGENT = "cat-sentry-fixture-downloader/1.0 (https://github.com/stitts-dev/cat-sentry)"

CLIPS = {
    # 14s, 1920x1080, CC BY-SA 3.0
    "cat_laser_pointer.webm": (
        "https://upload.wikimedia.org/wikipedia/commons/7/7a/"
        "Cat_playing_with_a_laser_pointer.webm"
    ),
    # 8.3s, 720x1280, CC BY-SA 4.0
    "cat_plays.webm": "https://upload.wikimedia.org/wikipedia/commons/f/f9/Cat_Plays.webm",
}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in CLIPS.items():
        dest = FIXTURES_DIR / name
        if dest.exists():
            print(f"skip (already downloaded): {dest}")
            continue
        print(f"downloading {url} -> {dest}")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        with urllib.request.urlopen(request) as response, open(dest, "wb") as f:  # noqa: S310
            shutil.copyfileobj(response, f)
    print("done.")


if __name__ == "__main__":
    main()
