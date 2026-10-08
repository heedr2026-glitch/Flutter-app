"""نسخة احتياطية يدوية لكل بيانات خدووم، ينزلها المالك من لوحة الإدارة ويحفظها عنده.

هذه نسخة إضافية فوق النسخ التلقائي لمزود قاعدة البيانات (Neon)، لا بديل عنه. الملف حساس
(فيه بيانات المشتركين) فيُسمح به للمالك فقط ويُسجل كل تنزيل.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from typing import Any

# جداول داخلية مؤقتة أو جلسات لا قيمة لها في الاسترجاع، وفيها رموز دخول.
SKIPPED_TABLES = {"sessions", "platform_sessions", "sqlite_sequence"}


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS platform_backups (
        id {identity}, actor TEXT NOT NULL DEFAULT '', tables INTEGER NOT NULL DEFAULT 0, rows INTEGER NOT NULL DEFAULT 0,
        bytes INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")


def table_names(c, postgres: bool) -> list[str]:
    if postgres:
        rows = c.execute("SELECT table_name AS name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()
    else:
        rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [row["name"] for row in rows if row["name"] not in SKIPPED_TABLES]


def _plain(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def export(c, postgres: bool, actor: str) -> tuple[bytes, dict[str, Any]]:
    """يرجع ملف JSON مضغوط بكل الجداول، وملخص (عدد الجداول والصفوف)."""
    created = datetime.now(timezone.utc).isoformat()
    tables: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for name in table_names(c, postgres):
        rows = [{k: _plain(row[k]) for k in row.keys()} for row in c.execute(f'SELECT * FROM "{name}"').fetchall()]
        tables[name] = rows
        total += len(rows)
    document = {"khadoum_backup": 1, "created_at": created, "created_by": actor, "tables": tables}
    data = gzip.compress(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    summary = {"tables": len(tables), "rows": total, "bytes": len(data), "created_at": created}
    c.execute("INSERT INTO platform_backups(actor,tables,rows,bytes,created_at) VALUES(?,?,?,?,?)", (actor[:120], len(tables), total, len(data), created))
    return data, summary


def last_backup(c) -> dict[str, Any] | None:
    row = c.execute("SELECT created_at,rows,bytes FROM platform_backups ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
