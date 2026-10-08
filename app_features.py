"""مفاتيح إظهار خدمات التطبيق التي تتحكم بها إدارة خدووم، وصورة مرفقة بشكوى الدعم.

الواتساب والمكالمات مخفيان افتراضيًا؛ تظهر للمشتركين فقط إذا شغّلتها الإدارة من لوحتها،
فتعود للظهور في التطبيق بدون بناء نسخة جديدة.
"""
from __future__ import annotations

import re
from typing import Any

FEATURES = {"whatsapp": "موظف واتساب", "calls": "موظف الاتصالات"}
MAX_ATTACHMENT_CHARS = 900_000
ATTACHMENT_PATTERN = re.compile(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+")


def migrate(c, postgres: bool = False) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS app_feature_flags (
        name TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')""")
    # صورة اختيارية يرفقها المشترك مع شكوى الدعم.
    if postgres:
        c.execute("ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS attachment_data TEXT NOT NULL DEFAULT ''")
    elif "attachment_data" not in {row[1] for row in c.execute("PRAGMA table_info(support_tickets)").fetchall()}:
        c.execute("ALTER TABLE support_tickets ADD COLUMN attachment_data TEXT NOT NULL DEFAULT ''")


def flags(c) -> dict[str, bool]:
    found = {row["name"]: bool(row["enabled"]) for row in c.execute("SELECT name,enabled FROM app_feature_flags").fetchall()}
    return {name: found.get(name, False) for name in FEATURES}


def set_flag(c, name: Any, enabled: Any, actor: str, now: str, error) -> dict[str, bool]:
    name = str(name or "")
    if name not in FEATURES:
        raise error(400, "خدمة غير معروفة")
    c.execute("DELETE FROM app_feature_flags WHERE name=?", (name,))
    c.execute("INSERT INTO app_feature_flags(name,enabled,updated_by,updated_at) VALUES(?,?,?,?)",
              (name, 1 if enabled is True else 0, str(actor or "")[:120], now))
    return flags(c)


def clean_attachment(value: Any, error) -> str:
    """صورة الشكوى: PNG أو JPG أو WebP فقط وبحجم معقول، وإلا تُرفض الشكوى برسالة واضحة."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_ATTACHMENT_CHARS or not ATTACHMENT_PATTERN.fullmatch(text):
        raise error(400, "الصورة المرفقة يجب أن تكون PNG أو JPG وبحجم مناسب")
    return text
