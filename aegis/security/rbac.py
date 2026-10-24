"""Role-Based Access Control (RBAC) and Scoped API Key Engine for Aegis AI 2.0.

Enforces least-privilege access using cryptographically hashed tokens, scoped
permission grants, and role hierarchies (ADMIN, OPERATOR, READONLY, NODE_AGENT).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from aegis.db import Database, utcnow

_LOG = logging.getLogger("aegis.security.rbac")

ROLE_PERMISSIONS = {
    "ADMIN": {"*"},
    "OPERATOR": {
        "telemetry:read",
        "incidents:read",
        "incidents:write",
        "remediation:execute",
        "policy:manage",
        "emergency_stop:manage",
        "canary:manage",
        "cluster:read",
    },
    "READONLY": {
        "telemetry:read",
        "incidents:read",
        "cluster:read",
    },
    "NODE_AGENT": {
        "cluster:register",
        "cluster:heartbeat",
        "telemetry:write",
    },
}


@dataclass
class APIKeyRecord:
    key_id: str
    name: str
    token_hash: str
    role: str
    scopes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    expires_at: str | None = None
    last_used_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "name": self.name,
            "role": self.role,
            "scopes": self.scopes,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
        }


class RBACManager:
    """Manages role-based access control tokens and permission verification."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Returns the SHA-256 hex digest of a token."""
        return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

    def create_api_key(
        self,
        name: str,
        role: str = "OPERATOR",
        custom_scopes: list[str] | None = None,
        key_id: str | None = None,
    ) -> tuple[str, APIKeyRecord]:
        """Generates a raw secret token, stores its hash, and returns (raw_token, record)."""
        if role not in ROLE_PERMISSIONS:
            raise ValueError(f"Invalid role '{role}'. Allowed: {list(ROLE_PERMISSIONS.keys())}")

        kid = key_id or f"KEY-{secrets.token_hex(4).upper()}"
        raw_token = f"aegis_sec_{secrets.token_urlsafe(32)}"
        token_hash = self.hash_token(raw_token)
        now = utcnow()

        granted_scopes = custom_scopes or sorted(list(ROLE_PERMISSIONS[role]))
        record = APIKeyRecord(
            key_id=kid,
            name=name,
            token_hash=token_hash,
            role=role,
            scopes=granted_scopes,
            created_at=now,
        )

        self.db.execute(
            "INSERT INTO api_keys(key_id, name, token_hash, role, scopes, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (
                record.key_id,
                record.name,
                record.token_hash,
                record.role,
                json.dumps(record.scopes),
                record.created_at,
            ),
        )
        return raw_token, record

    def authenticate_token(self, raw_token: str) -> APIKeyRecord | None:
        """Authenticates a raw token against stored hashes."""
        if not raw_token:
            return None
        # Handle "Bearer <token>" prefix if present
        clean_token = raw_token.replace("Bearer ", "").strip()
        thash = self.hash_token(clean_token)

        row = self.db.query_one("SELECT * FROM api_keys WHERE token_hash=?", (thash,))
        if not row:
            return None

        # Update last used timestamp
        self.db.execute("UPDATE api_keys SET last_used_at=? WHERE key_id=?", (utcnow(), row["key_id"]))

        scopes = json.loads(row["scopes"]) if row["scopes"] else []
        return APIKeyRecord(
            key_id=row["key_id"],
            name=row["name"],
            token_hash=row["token_hash"],
            role=row["role"],
            scopes=scopes,
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
        )

    def has_permission(self, record: APIKeyRecord, required_scope: str) -> bool:
        """Verifies if an APIKeyRecord holds the required permission."""
        if "*" in record.scopes:
            return True
        return required_scope in record.scopes

    def list_keys(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT key_id, name, role, scopes, created_at, last_used_at FROM api_keys ORDER BY created_at DESC")
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["scopes"] = json.loads(item["scopes"]) if item.get("scopes") else []
            except Exception:
                item["scopes"] = []
            out.append(item)
        return out
