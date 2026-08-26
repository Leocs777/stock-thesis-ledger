#!/usr/bin/env python3
"""Validate checked-in App Store and TestFlight copy without external tools."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "ios" / "metadata"
LOCALES = ("en-US", "zh-Hans")
LIMITS = {
    "name.txt": 30,
    "subtitle.txt": 30,
    "promotional_text.txt": 170,
    "description.txt": 4000,
    "keywords.txt": 100,
    "whats_new.txt": 4000,
    "beta_description.txt": 4000,
    "beta_review_notes.txt": 4000,
}
URL_FILES = ("support_url.txt", "privacy_url.txt")
FORBIDDEN = re.compile(
    r"(?:ngrok-free|ALPACA_API_SECRET|ALPACA_API_KEY|BEGIN PRIVATE KEY|Bearer\s+[A-Za-z0-9])",
    re.IGNORECASE,
)


def main() -> int:
    failures: list[str] = []
    for locale in LOCALES:
        folder = METADATA / locale
        for filename, limit in LIMITS.items():
            path = folder / filename
            if not path.is_file():
                failures.append(f"missing {path.relative_to(ROOT)}")
                continue
            value = path.read_text(encoding="utf-8").strip()
            if not value:
                failures.append(f"empty {path.relative_to(ROOT)}")
            elif len(value) > limit:
                failures.append(
                    f"{path.relative_to(ROOT)} is {len(value)} characters; limit {limit}"
                )
            if FORBIDDEN.search(value):
                failures.append(f"sensitive-looking value in {path.relative_to(ROOT)}")
        for filename in URL_FILES:
            path = folder / filename
            if not path.is_file():
                failures.append(f"missing {path.relative_to(ROOT)}")
                continue
            value = path.read_text(encoding="utf-8").strip()
            if not value.startswith("https://"):
                failures.append(f"{path.relative_to(ROOT)} must use HTTPS")
            if FORBIDDEN.search(value):
                failures.append(f"private-looking URL in {path.relative_to(ROOT)}")
    if failures:
        print("App Store metadata check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("App Store metadata: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
