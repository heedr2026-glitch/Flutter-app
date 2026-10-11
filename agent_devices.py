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
SCOPE_DAYS = {"agent": 30, "dashboard": 7}


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS owner_agent_devices (
        id {identity}, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_used_at TEXT NOT NULL DEFAULT '', revoked_at TEXT)""")
    # النطاق: agent = موظف الإدارة فقط (30 يوم)، dashboard = لوحة الإدارة كاملة عدا الأشياء الخطيرة (7 أيام).
    if postgres:
        c.execute("ALTER TABLE owner_agent_devices ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'agent'")
    elif "scope" not in {row[1] for row in c.execute("PRAGMA table_info(owner_agent_devices)").fetchall()}:
        c.execute("ALTER TABLE owner_agent_devices ADD COLUMN scope TEXT NOT NULL DEFAULT 'agent'")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(c, label: Any = "", scope: str = "agent") -> dict[str, Any]:
    scope = scope if scope in SCOPE_DAYS else "agent"
    token = ("agd_" if scope == "agent" else "dsd_") + secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(days=SCOPE_DAYS[scope])
    c.execute("INSERT INTO owner_agent_devices(token_hash,label,created_at,expires_at,scope) VALUES(?,?,?,?,?)",
              (_hash(token), str(label or "")[:80], now.isoformat(), expires.isoformat(), scope))
    return {"token": token, "expiresAt": expires.isoformat(), "days": SCOPE_DAYS[scope]}


def valid(c, token: Any, scopes: tuple[str, ...] = ("agent", "dashboard")) -> bool:
    text = str(token or "").strip()
    if not text.startswith(("agd_", "dsd_")) or len(text) > 200:
        return False
    now = _now().isoformat()
    row = c.execute("SELECT id,scope FROM owner_agent_devices WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
                    (_hash(text), now)).fetchone()
    if row is not None and row["scope"] not in scopes:
        return False
    if row is None:
        return False
    c.execute("UPDATE owner_agent_devices SET last_used_at=? WHERE id=?", (now, row["id"]))
    return True


def active_count(c) -> int:
    row = c.execute("SELECT COUNT(*) n FROM owner_agent_devices WHERE revoked_at IS NULL AND expires_at>?", (_now().isoformat(),)).fetchone()
    return int(row["n"] or 0)


def revoke_all(c) -> int:
    return c.execute("UPDATE owner_agent_devices SET revoked_at=? WHERE revoked_at IS NULL", (_now().isoformat(),)).rowcount
