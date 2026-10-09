"""أرقام النمو للوحة إدارة خدوم: قمع التسجيل، والإلغاء، واستخدام المزايا.

كل الأرقام تُحسب من بيانات موجودة أصلًا؛ ميزة لا تسجل استخدامها قد يظهر رقمها أقل من الحقيقة.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import owner_admin

PACKAGES = {"basic": "الأساسية", "vip": "VIP", "free": "المجانية"}

# (الاسم، الجدول، استعلام المؤسسات المختلفة منذ تاريخ)
FEATURES = [
    ("المواعيد والطلبات", "appointment_requests", "SELECT COUNT(DISTINCT organization_id) n FROM appointment_requests WHERE created_at>=?"),
    ("مساعد اسألني", "ai_usage", "SELECT COUNT(DISTINCT organization_id) n FROM ai_usage WHERE employee_type='assistant' AND created_at>=?"),
    ("المحادثة الخارجية", "ai_usage", "SELECT COUNT(DISTINCT organization_id) n FROM ai_usage WHERE employee_type IN ('reception','chat') AND created_at>=?"),
    ("البحث التجاري", "ai_usage", "SELECT COUNT(DISTINCT organization_id) n FROM ai_usage WHERE employee_type IN ('commercial_research','commercial_report') AND created_at>=?"),
    ("الواتساب", "ai_usage", "SELECT COUNT(DISTINCT organization_id) n FROM ai_usage WHERE employee_type='whatsapp' AND created_at>=?"),
    ("المكالمات", "call_logs", "SELECT COUNT(DISTINCT organization_id) n FROM call_logs WHERE started_at>=?"),
    ("تتبع المركبات", "vehicle_location_events", "SELECT COUNT(DISTINCT organization_id) n FROM vehicle_location_events WHERE created_at>=?"),
    ("مجتمع خدوم", "community_posts", "SELECT COUNT(DISTINCT u.organization_id) n FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.created_at>=?"),
    ("الإعلانات", "advertisements", "SELECT COUNT(DISTINCT organization_id) n FROM advertisements WHERE created_at>=?"),
    ("الدعم", "support_tickets", "SELECT COUNT(DISTINCT organization_id) n FROM support_tickets WHERE created_at>=?"),
]


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _exists(c, name: str, s) -> bool:
    return owner_admin.table_exists(c, name, s)


def funnel(c, s, since: str) -> dict[str, Any]:
    orgs = owner_admin.rows(c, "SELECT id,name,activity,city,created_at FROM organizations WHERE created_at>=? ORDER BY id", (since,))
    ids = [o["id"] for o in orgs]
    completed = [o for o in orgs if str(o.get("activity") or "").strip() and str(o.get("city") or "").strip()]
    used_week = 0
    if ids and _exists(c, "audit_logs", s):
        for org in orgs:
            created = _parse(org["created_at"])
            if not created:
                continue
            later = (created + timedelta(days=7)).isoformat()
            if c.execute("SELECT 1 FROM audit_logs WHERE organization_id=? AND action='login' AND created_at>=? LIMIT 1", (org["id"], later)).fetchone():
                used_week += 1
    paid = 0
    if ids:
        marks = ",".join("?" * len(ids))
        paid_ids = {r["organization_id"] for r in owner_admin.rows(c, f"SELECT organization_id FROM subscriptions WHERE package IN ('basic','vip') AND organization_id IN ({marks})", tuple(ids))}
        if _exists(c, "subscription_requests", s):
            paid_ids |= {r["organization_id"] for r in owner_admin.rows(c, f"SELECT organization_id FROM subscription_requests WHERE status='approved' AND organization_id IN ({marks})", tuple(ids))}
        paid = len(paid_ids)
    steps = [
        {"label": "سجّل", "count": len(orgs)},
        {"label": "كمّل بيانات المؤسسة", "count": len(completed)},
        {"label": "رجع للتطبيق بعد أسبوع", "count": used_week},
        {"label": "اشترك بباقة مدفوعة", "count": paid},
    ]
    return {"steps": steps, "note": "«رجع بعد أسبوع» = سجل دخول بعد 7 أيام من التسجيل. المؤسسات الجديدة جدًا ما لحقت تكمل أسبوع."}


def churn(c, s, since: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if _exists(c, "audit_logs", s):
        for row in owner_admin.rows(c, """SELECT a.organization_id,o.name,a.summary,a.created_at FROM audit_logs a JOIN organizations o ON o.id=a.organization_id
                WHERE a.action='subscription_expired' AND a.created_at>=? ORDER BY a.created_at DESC""", (since,)):
            items.append({"organization_id": row["organization_id"], "organization": row["name"], "kind": "انتهى وما جدد", "detail": row["summary"], "at": row["created_at"]})
    if _exists(c, "support_tickets", s):
        for row in owner_admin.rows(c, """SELECT t.organization_id,o.name,t.message,t.created_at FROM support_tickets t JOIN organizations o ON o.id=t.organization_id
                WHERE t.category='تغيير الباقة' AND t.message LIKE '%المجانية%' AND t.created_at>=? ORDER BY t.created_at DESC""", (since,)):
            items.append({"organization_id": row["organization_id"], "organization": row["name"], "kind": "طلب الرجوع للمجانية", "detail": "", "at": row["created_at"]})
    if _exists(c, "account_deletion_requests", s):
        for row in owner_admin.rows(c, """SELECT d.organization_id,COALESCE(o.name,'') name,d.created_at FROM account_deletion_requests d LEFT JOIN organizations o ON o.id=d.organization_id
                WHERE d.created_at>=? ORDER BY d.created_at DESC""", (since,)):
            items.append({"organization_id": row["organization_id"], "organization": row["name"] or "حساب محذوف", "kind": "طلب حذف الحساب", "detail": "", "at": row["created_at"]})
    items.sort(key=lambda item: str(item["at"]), reverse=True)
    return {"count": len(items), "items": items[:50],
            "note": "طلب الرجوع للمجانية ما فيه خانة سبب حاليًا، فالسبب يظهر بس إذا ذكروه للدعم."}


def features(c, s, since: str) -> dict[str, Any]:
    total = owner_admin.scalar(c, "SELECT COUNT(*) n FROM organizations")
    items = []
    for label, table, sql in FEATURES:
        count = owner_admin.scalar(c, sql, (since,)) if _exists(c, table, s) else 0
        items.append({"label": label, "count": int(count or 0)})
    items.sort(key=lambda item: item["count"], reverse=True)
    return {"organizations": int(total or 0), "items": items,
            "note": "عدد المؤسسات اللي استخدمت الميزة. بعض المزايا (مثل الموظفين ومعلومات المؤسسة) ما تسجل كل استخدام، فرقمها ممكن أقل من الحقيقة."}


def summary(c, s, days: Any = 30) -> dict[str, Any]:
    try:
        span = max(7, min(int(days or 30), 365))
    except (TypeError, ValueError):
        span = 30
    since = _iso(datetime.now(timezone.utc) - timedelta(days=span))
    return {"days": span, "since": since, "funnel": funnel(c, s, since), "churn": churn(c, s, since), "features": features(c, s, since)}
