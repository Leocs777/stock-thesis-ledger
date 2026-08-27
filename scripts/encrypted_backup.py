#!/usr/bin/env python3
"""Create or verify an InvestorLab encrypted offsite backup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from investor_lab.encrypted_backup import (  # noqa: E402
    BackupError,
    DEFAULT_KEYCHAIN_SERVICE,
    create_encrypted_backup,
    drill_encrypted_backup,
    print_result,
    resolve_passphrase,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create encrypted SQLite backups or run a read-only restore drill."
    )
    result.add_argument(
        "--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE,
        help="macOS Keychain generic-password service containing the passphrase.",
    )
    result.add_argument(
        "--interactive", action="store_true",
        help="Prompt when the Keychain item is unavailable.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--database", type=Path, default=ROOT / "data" / "investor-lab.sqlite3")
    create.add_argument("--destination", type=Path, required=True)
    drill = commands.add_parser("drill")
    drill.add_argument("encrypted_backup", type=Path)
    drill.add_argument("--database", type=Path, default=ROOT / "data" / "investor-lab.sqlite3")
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        passphrase = resolve_passphrase(
            keychain_service=arguments.keychain_service,
            interactive=arguments.interactive,
        )
        if arguments.command == "create":
            output = create_encrypted_backup(
                arguments.database.resolve(), arguments.destination.expanduser().resolve(), passphrase
            )
        else:
            output = drill_encrypted_backup(
                arguments.encrypted_backup.expanduser().resolve(),
                arguments.database.resolve(),
                passphrase,
            )
        print_result(output)
        return 0 if output.get("drill_passed", True) else 2
    except BackupError as error:
        print(f"Encrypted backup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
