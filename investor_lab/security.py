"""Request throttling and privacy-preserving local security audit helpers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class RequestRateLimiter:
    """Small in-process limiter for a single local modular-monolith instance."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
        self, key: str, limit: int, window_seconds: int, *, current: float | None = None
    ) -> tuple[bool, int]:
        observed = time.monotonic() if current is None else current
        cutoff = observed - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (observed - events[0])) + 1)
                return False, retry_after
            events.append(observed)
        return True, 0


def client_address(
    peer_address: str, headers: Mapping[str, str], *, trust_proxy: bool
) -> str:
    candidate = peer_address
    if trust_proxy:
        candidate = (
            headers.get("CF-Connecting-IP")
            or headers.get("X-Forwarded-For", "").split(",", 1)[0]
            or peer_address
        ).strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "invalid"


def identity_hash(kind: str, value: str) -> str:
    normalized = value.strip().lower()
    return hashlib.sha256(f"investor-lab:{kind}:{normalized}".encode("utf-8")).hexdigest()


def _audit_file(database_path: Path) -> Path:
    return database_path.parent / "security-audit.jsonl"


_AUDIT_LOCK = threading.Lock()


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (details or {}).items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in ("password", "secret", "token", "api_key")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)[:80]] = value if not isinstance(value, str) else value[:500]
    return clean


def read_security_events(
    database_path: Path, *, user_id: str | None = None, limit: int = 50
) -> dict[str, Any]:
    path = _audit_file(database_path)
    if not path.exists():
        return {"events": [], "invalid_lines": 0, "path": path.name}
    selected: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 500)))
    invalid_lines = 0
    expected_user = identity_hash("user", user_id) if user_id else None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(event, dict):
                invalid_lines += 1
                continue
            if expected_user is None or event.get("user_hash") == expected_user:
                selected.append(event)
    return {
        "events": list(reversed(selected)),
        "invalid_lines": invalid_lines,
        "path": path.name,
    }


def append_security_event(
    database_path: Path,
    event_type: str,
    outcome: str,
    *,
    user_id: str | None = None,
    email: str | None = None,
    address: str = "",
    device_id: str = "",
    client_type: str = "",
    unusual: bool = False,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = _audit_file(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "event_type": event_type[:80],
        "outcome": outcome[:40],
        "user_hash": identity_hash("user", user_id) if user_id else None,
        "email_hash": identity_hash("email", email) if email else None,
        "address_hash": identity_hash("address", address) if address else None,
        "device_hash": identity_hash("device", device_id) if device_id else None,
        "client_type": client_type[:20],
        "unusual": bool(unusual),
        "details": _safe_details(details),
    }
    encoded = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
    with _AUDIT_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return event


def record_login_event(
    database_path: Path,
    *,
    successful: bool,
    user_id: str | None,
    email: str,
    address: str,
    device_id: str,
    client_type: str,
    rate_limited: bool = False,
) -> dict[str, Any]:
    unusual = False
    if successful and user_id:
        prior = read_security_events(database_path, user_id=user_id, limit=100)["events"]
        successful_logins = [
            item for item in prior
            if item.get("event_type") == "login" and item.get("outcome") == "success"
        ]
        address_digest = identity_hash("address", address)
        device_digest = identity_hash("device", device_id)
        unusual = bool(successful_logins) and not any(
            item.get("address_hash") == address_digest
            and item.get("device_hash") == device_digest
            for item in successful_logins
        )
    event = append_security_event(
        database_path,
        "login",
        "rate_limited" if rate_limited else "success" if successful else "failure",
        user_id=user_id,
        email=email,
        address=address,
        device_id=device_id,
        client_type=client_type,
        unusual=unusual,
    )
    return {
        "unusual_login": unusual,
        "security_notice": (
            "New network or device observed. Review connected devices and sign out all sessions if this was not you."
            if unusual else None
        ),
        "event": event,
    }
