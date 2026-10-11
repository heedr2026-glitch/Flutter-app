"""«تذكّر هذا الجوال» لموظف الإدارة: رمز دخول خاص بالجهاز بدل حفظ مفتاح المالك نفسه.

الرمز صالح 30 يومًا، ويُحفظ في قاعدة البيانات مشفّرًا (بصمة SHA-256 فقط)، ويقدر المالك يلغي كل
الأجهزة المحفوظة بضغطة من لوحة الإدارة.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

DAYS = 30


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS owner_agent_devices (
        id {identity}, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_used_at TEXT NOT NULL DEFAULT '', revoked_at TEXT)""")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(c, label: Any = "") -> dict[str, Any]:
    token = "agd_" + secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(days=DAYS)
    c.execute("INSERT INTO owner_agent_devices(token_hash,label,created_at,expires_at) VALUES(?,?,?,?)",
              (_hash(token), str(label or "")[:80], now.isoformat(), expires.isoformat()))
    return {"token": token, "expiresAt": expires.isoformat(), "days": DAYS}


def valid(c, token: Any) -> bool:
    text = str(token or "").strip()
    if not text.startswith("agd_") or len(text) > 200:
        return False
    now = _now().isoformat()
    row = c.execute("SELECT id FROM owner_agent_devices WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
                    (_hash(text), now)).fetchone()
    if row is None:
        return False
    c.execute("UPDATE owner_agent_devices SET last_used_at=? WHERE id=?", (now, row["id"]))
    return True


def active_count(c) -> int:
    row = c.execute("SELECT COUNT(*) n FROM owner_agent_devices WHERE revoked_at IS NULL AND expires_at>?", (_now().isoformat(),)).fetchone()
    return int(row["n"] or 0)


def revoke_all(c) -> int:
    return c.execute("UPDATE owner_agent_devices SET revoked_at=? WHERE revoked_at IS NULL", (_now().isoformat(),)).rowcount
