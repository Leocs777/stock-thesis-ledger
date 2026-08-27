"""Verified AES-encrypted SQLite backups and non-destructive restore drills."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPENSSL_ITERATIONS = 200_000
DEFAULT_KEYCHAIN_SERVICE = "org.investorlab.encrypted-backup"


class BackupError(RuntimeError):
    pass


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _quick_check(path: Path) -> tuple[str, int]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as database:
            integrity = str(database.execute("PRAGMA quick_check").fetchone()[0])
            schema_version = int(database.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as error:
        raise BackupError(f"SQLite verification failed: {error}") from error
    if integrity != "ok":
        raise BackupError(f"SQLite quick_check returned {integrity}.")
    return integrity, schema_version


def _snapshot_database(database_path: Path, destination: Path) -> int:
    if not database_path.is_file():
        raise BackupError(f"Database does not exist: {database_path}")
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(destination) as target:
            source.backup(target)
    _, schema_version = _quick_check(destination)
    destination.chmod(0o600)
    return schema_version


def _openssl(
    source: Path, destination: Path, passphrase: str, *, decrypt: bool = False
) -> None:
    if len(passphrase) < 16:
        raise BackupError("Backup passphrase must contain at least 16 characters.")
    command = [
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter",
        str(OPENSSL_ITERATIONS), "-salt", "-in", str(source), "-out", str(destination),
        "-pass", "stdin",
    ]
    if decrypt:
        command.insert(2, "-d")
    try:
        result = subprocess.run(
            command,
            input=(passphrase + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as error:
        raise BackupError("OpenSSL is required for encrypted backups.") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise BackupError(f"OpenSSL encryption command failed: {detail or 'unknown error'}")


def create_encrypted_backup(
    database_path: Path, destination_dir: Path, passphrase: str
) -> dict[str, Any]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not destination_dir.is_dir():
        raise BackupError(f"Backup destination is not a directory: {destination_dir}")
    filename = f"investor-lab-{_utc_stamp()}.sqlite3.enc"
    output = destination_dir / filename
    if output.exists():
        raise BackupError(f"Encrypted backup already exists: {output}")
    with tempfile.TemporaryDirectory(prefix="investorlab-backup-") as temp_name:
        temp_dir = Path(temp_name)
        snapshot = temp_dir / "snapshot.sqlite3"
        encrypted = temp_dir / filename
        schema_version = _snapshot_database(database_path, snapshot)
        _openssl(snapshot, encrypted, passphrase)
        encrypted.replace(output)
    output.chmod(0o600)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "created": True,
        "filename": filename,
        "path": str(output),
        "size_bytes": output.stat().st_size,
        "sha256": digest,
        "schema_version": schema_version,
        "cipher": f"AES-256-CBC/PBKDF2-{OPENSSL_ITERATIONS}",
    }


def drill_encrypted_backup(
    encrypted_path: Path, active_database_path: Path, passphrase: str
) -> dict[str, Any]:
    if not encrypted_path.is_file():
        raise BackupError(f"Encrypted backup does not exist: {encrypted_path}")
    with tempfile.TemporaryDirectory(prefix="investorlab-drill-") as temp_name:
        decrypted = Path(temp_name) / "drill.sqlite3"
        _openssl(encrypted_path, decrypted, passphrase, decrypt=True)
        integrity, backup_schema = _quick_check(decrypted)
    _, active_schema = _quick_check(active_database_path)
    return {
        "drill_passed": integrity == "ok" and backup_schema == active_schema,
        "integrity": integrity,
        "backup_schema_version": backup_schema,
        "active_schema_version": active_schema,
        "schema_match": backup_schema == active_schema,
        "source": encrypted_path.name,
        "active_database_unchanged": True,
    }


def keychain_passphrase(service: str = DEFAULT_KEYCHAIN_SERVICE) -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise BackupError("macOS Keychain is unavailable; enter the passphrase interactively.") from error
    value = result.stdout.strip()
    if result.returncode or not value:
        raise BackupError(
            f"No backup passphrase was found in macOS Keychain service {service}."
        )
    return value


def resolve_passphrase(*, keychain_service: str, interactive: bool) -> str:
    try:
        return keychain_passphrase(keychain_service)
    except BackupError:
        if not interactive:
            raise
    return getpass.getpass("Encrypted backup passphrase: ")


def print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, sort_keys=True))
