#!/usr/bin/env python3
"""Validate local Markdown links without third-party dependencies."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_TARGET_PATTERN = re.compile(r"(?:href|src)=\"([^\"]+)\"")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:")


def local_target(raw_target: str, source: Path) -> Path | None:
    target = raw_target.strip()
    if not target or target.startswith("#") or target.startswith(EXTERNAL_PREFIXES):
        return None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0].strip()
    if not target:
        return None
    return (source.parent / unquote(target)).resolve()


def main() -> int:
    missing: list[str] = []
    for markdown in sorted(ROOT.rglob("*.md")):
        relative_parts = markdown.relative_to(ROOT).parts
        if any(
            part.startswith(".") and part != ".github" for part in relative_parts
        ):
            continue
        text = markdown.read_text(encoding="utf-8")
        targets = [match.group(1) for match in LINK_PATTERN.finditer(text)]
        targets.extend(match.group(1) for match in HTML_TARGET_PATTERN.finditer(text))
        for raw_target in targets:
            target = local_target(raw_target, markdown)
            if target is not None and not target.exists():
                missing.append(
                    f"{markdown.relative_to(ROOT)} -> {raw_target.strip()}"
                )
    if missing:
        print("Missing local Markdown targets:", file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("Local Markdown links: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
