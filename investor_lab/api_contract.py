"""Versioned API contract manifest for the Web and iOS clients."""

from __future__ import annotations

from typing import Any


API_CONTRACT_VERSION = "2026-08-26.phase2"

SHARED_CLIENT_CONTRACTS: tuple[dict[str, Any], ...] = (
    {"path": "/api/health", "methods": ["GET"], "auth": False, "clients": [], "required_response": ["status", "app_version", "schema_version", "api_contract_version"]},
    {"path": "/api/auth/login", "methods": ["POST"], "auth": False, "clients": ["web", "ios"], "required_response": ["user", "expires_at"]},
    {"path": "/api/auth/session", "methods": ["GET"], "auth": True, "clients": ["web", "ios"], "required_response": ["user", "revision"]},
    {"path": "/api/snapshot", "methods": ["GET"], "auth": True, "clients": [], "required_response": ["revision", "portfolio", "watchlist"]},
    {"path": "/api/sync", "methods": ["GET"], "auth": True, "clients": ["web", "ios"], "required_response": ["cursor", "has_more", "snapshot"]},
    {"path": "/api/watchlist", "methods": ["GET", "POST"], "auth": True, "clients": ["web", "ios"], "required_response": []},
    {"path": "/api/watchlist/{symbol}", "methods": ["DELETE"], "auth": True, "clients": ["web"], "required_response": []},
    {"path": "/api/market/research/{symbol}", "methods": ["GET"], "auth": True, "clients": ["web", "ios"], "required_response": ["symbol", "data_quality"]},
    {"path": "/api/market/refresh", "methods": ["POST"], "auth": True, "clients": ["web", "ios"], "required_response": ["symbol", "data_quality"]},
    {"path": "/api/decisions/{symbol}", "methods": ["GET"], "auth": True, "clients": ["web", "ios"], "required_response": ["symbol", "history"]},
    {"path": "/api/data-quality", "methods": ["GET"], "auth": True, "clients": ["web", "ios"], "required_response": ["generated_at", "summary", "symbols"]},
    {"path": "/api/system/health", "methods": ["GET"], "auth": True, "clients": ["web", "ios"], "required_response": ["status", "database", "market_cache"]},
    {"path": "/api/security/events", "methods": ["GET"], "auth": True, "clients": [], "required_response": ["events", "invalid_lines"]},
)


def contract_document() -> dict[str, Any]:
    return {
        "contract_version": API_CONTRACT_VERSION,
        "format": "investor-lab-client-contract-v1",
        "paper_only": True,
        "routes": list(SHARED_CLIENT_CONTRACTS),
        "compatibility": "Additive response fields are compatible. Removing or renaming a required field requires a new contract version and both client builds.",
    }
