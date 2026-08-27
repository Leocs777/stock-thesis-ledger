#!/usr/bin/env python3
"""Verify the versioned API catalog and shared Web/iOS route references."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from investor_lab.api_contract import contract_document  # noqa: E402
from investor_lab.security import CONTENT_SECURITY_POLICY  # noqa: E402


def main() -> int:
    document = contract_document()
    routes = document["routes"]
    failures: list[str] = []
    paths = [item["path"] for item in routes]
    if len(paths) != len(set(paths)):
        failures.append("API contract contains duplicate paths.")
    sources = {
        "web": (ROOT / "web" / "app.js").read_text(encoding="utf-8"),
        "ios": (ROOT / "ios" / "InvestorLab" / "InvestorLabApp.swift").read_text(encoding="utf-8"),
    }
    for item in routes:
        if not item.get("methods") or not isinstance(item.get("required_response"), list):
            failures.append(f"Incomplete route contract: {item.get('path')}")
            continue
        prefix = item["path"].split("{")[0]
        for client in item.get("clients", []):
            if prefix not in sources[client]:
                failures.append(f"{client} does not reference contract route {item['path']}")
    html_files = [ROOT / "web" / "index.html", ROOT / "web" / "design-system.html"]
    for path in html_files:
        source = path.read_text(encoding="utf-8")
        if "<style" in source or re.search(r"<script(?![^>]*\bsrc=)[^>]*>", source):
            failures.append(f"Inline style or script remains in {path.relative_to(ROOT)}")
    if "unsafe-inline" in CONTENT_SECURITY_POLICY:
        failures.append("Content Security Policy still allows unsafe-inline.")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        f"PASS: {len(routes)} routes; Web/iOS references present; strict CSP static assets verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
