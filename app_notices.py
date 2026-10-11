"""تنبيهات تصل جوال المشترك داخل التطبيق: منشورات المجتمع المهمة، تذكير التجديد، الملخص اليومي،
وتنبيهات تتبع المركبات.

التطبيق يسأل الخادم كل دقيقة عن التنبيهات الجديدة (بعد آخر رقم شافه) ويعرضها كإشعار على الجوال.
التنبيه العام (organization_id فاضي) يوصل كل المؤسسات المسجلة قبل إرساله.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

RIYADH = timezone(timedelta(hours=3))
RENEW_DAYS = (7, 3, 1, 0)
SUMMARY_HOUR = 21  # 9 الليل بتوقيت السعودية
SILENCE_MINUTES = 5
PROBLEM_STATES = {"location_disabled": "قفل الموقع في جواله", "permission_denied": "سحب إذن الموقع من التطبيق", "stopped": "أوقف التتبع وفك الربط"}


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS app_notices (
        id {identity}, organization_id BIGINT, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
        action TEXT NOT NULL DEFAULT '', dedupe_key TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_app_notices_org ON app_notices(organization_id,id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_app_notices_dedupe ON app_notices(dedupe_key)")
    # أعطال التطبيق اللي يرسلها جوال المشترك تلقائيًا.
    c.execute(f"""CREATE TABLE IF NOT EXISTS app_errors (
        id {identity}, organization_id BIGINT, user_id BIGINT, message TEXT NOT NULL, stack TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '', app_version TEXT NOT NULL DEFAULT '', device TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_app_errors_created ON app_errors(created_at)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def add(c, organization_id: Any, kind: str, title: str, body: str, action: str = "", dedupe: str = "") -> bool:
    """يضيف تنبيهًا. مع dedupe لا يتكرر نفس التنبيه مرتين."""
    if dedupe and c.execute("SELECT 1 FROM app_notices WHERE dedupe_key=? LIMIT 1", (dedupe,)).fetchone():
        return False
    c.execute("INSERT INTO app_notices(organization_id,kind,title,body,action,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?)",
              (organization_id, kind, str(title)[:120], str(body)[:600], action[:60], dedupe[:200], _now().isoformat()))
    return True


def for_organization(c, organization_id: int, after: Any) -> dict[str, Any]:
    try:
        after_id = max(0, int(after or 0))
    except (TypeError, ValueError):
        after_id = 0
    org = c.execute("SELECT created_at FROM organizations WHERE id=?", (organization_id,)).fetchone()
    joined = org["created_at"] if org else ""
    rows = c.execute("""SELECT id,kind,title,body,action,created_at FROM app_notices
        WHERE id>? AND (organization_id=? OR (organization_id IS NULL AND created_at>=?)) ORDER BY id LIMIT 30""",
                     (after_id, organization_id, joined)).fetchall()
    latest = c.execute("""SELECT MAX(id) n FROM app_notices WHERE kind='community'
        AND (organization_id=? OR (organization_id IS NULL AND created_at>=?))""", (organization_id, joined)).fetchone()
    newest = c.execute("SELECT MAX(id) n FROM app_notices").fetchone()
    return {"items": [dict(r) for r in rows], "latestCommunity": int(latest["n"] or 0), "latestId": int(newest["n"] or 0)}


# ---------- تذكير تجديد الاشتراك ----------
def renew_text(days: int, package: str) -> tuple[str, str]:
    name = {"basic": "الأساسية", "vip": "VIP"}.get(package, package)
    if days <= 0:
        return "اشتراكك ينتهي اليوم ⏰", f"باقة {name} تنتهي اليوم. جدد الحين عشان ما تتوقف خدماتك."
    if days == 1:
        return "اشتراكك ينتهي بكرة ⏰", f"باقي يوم واحد على انتهاء باقة {name}. جدد الحين عشان ما تتوقف خدماتك."
    return f"باقي {days} أيام على انتهاء اشتراكك", f"باقة {name} تنتهي بعد {days} أيام. جدد الحين بضغطة."


def renew_reminders(c, now: datetime | None = None) -> int:
    now = now or _now()
    today = now.astimezone(RIYADH).date()
    sent = 0
    for row in c.execute("SELECT organization_id,package,expires_at FROM subscriptions WHERE package IN ('basic','vip') AND expires_at IS NOT NULL").fetchall():
        ends = _parse(row["expires_at"])
        if ends is None:
            continue
        days = (ends.astimezone(RIYADH).date() - today).days
        if days not in RENEW_DAYS or ends < now:
            continue
        title, body = renew_text(days, row["package"])
        if add(c, row["organization_id"], "renew", title, body, "renew", f"renew:{row['organization_id']}:{str(row['expires_at'])[:10]}:{days}"):
            sent += 1
    return sent


def manual_renew_reminder(c, organization_id: int) -> dict[str, Any]:
    row = c.execute("SELECT package,expires_at FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()
    if row is None or row["package"] not in ("basic", "vip") or not row["expires_at"]:
        title, body = "اشترك وخذ مزايا أكثر", "باقات خدوم المدفوعة تعطيك موظفين ومركبات ومزايا أكثر. شوف الباقات الحين."
    else:
        ends = _parse(row["expires_at"])
        days = max(0, (ends.astimezone(RIYADH).date() - _now().astimezone(RIYADH).date()).days) if ends else 0
        title, body = renew_text(days, row["package"])
    add(c, organization_id, "renew", title, body, "renew")
    return {"sent": True, "title": title}


# ---------- الملخص اليومي ----------
def _count(c, sql: str, args: tuple) -> int:
    try:
        row = c.execute(sql, args).fetchone()
        return int((row["n"] if row else 0) or 0)
    except Exception:
        return 0


def summary_text(c, organization_id: int, now: datetime) -> str | None:
    local = now.astimezone(RIYADH)
    today, tomorrow = local.date().isoformat(), (local.date() + timedelta(days=1)).isoformat()
    lines = []
    today_appts = _count(c, "SELECT COUNT(*) n FROM appointment_requests WHERE organization_id=? AND scheduled_at LIKE ? AND status IN ('pending','accepted','completed')", (organization_id, today + "%"))
    tomorrow_appts = _count(c, "SELECT COUNT(*) n FROM appointment_requests WHERE organization_id=? AND scheduled_at LIKE ? AND status IN ('pending','accepted')", (organization_id, tomorrow + "%"))
    pending = _count(c, "SELECT COUNT(*) n FROM appointment_requests WHERE organization_id=? AND status='pending'", (organization_id,))
    complaints = _count(c, "SELECT COUNT(*) n FROM support_tickets WHERE organization_id=? AND status NOT IN ('resolved','closed')", (organization_id,))
    if today_appts or tomorrow_appts:
        lines.append(f"📅 مواعيد اليوم {today_appts}، وبكرة {tomorrow_appts}")
    if pending:
        lines.append(f"🕒 {pending} طلب ينتظر ردك")
    if complaints:
        lines.append(f"🛠️ {complaints} شكوى دعم مفتوحة")
    try:
        vehicles = c.execute("SELECT vehicle_name,phone_status FROM vehicle_tracking_schedules WHERE organization_id=? AND enabled=1", (organization_id,)).fetchall()
    except Exception:
        vehicles = []
    if vehicles:
        moved = _count(c, "SELECT COUNT(DISTINCT vehicle_key) n FROM vehicle_location_events WHERE organization_id=? AND created_at>=?", (organization_id, (now - timedelta(hours=14)).isoformat()))
        problems = [v["vehicle_name"] for v in vehicles if v["phone_status"] in PROBLEM_STATES]
        lines.append(f"🚚 المركبات اللي اشتغلت اليوم: {moved} من {len(vehicles)}" + (f" — تتبع متوقف: {'، '.join(problems[:3])}" if problems else ""))
    sub = c.execute("SELECT package,expires_at FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()
    if sub and sub["package"] in ("basic", "vip") and sub["expires_at"]:
        ends = _parse(sub["expires_at"])
        if ends:
            days = (ends.astimezone(RIYADH).date() - local.date()).days
            if 0 <= days <= 7:
                lines.append(f"⏰ اشتراكك ينتهي بعد {days} أيام" if days > 1 else "⏰ اشتراكك ينتهي قريب، جدد الحين")
    return "\n".join(lines) if lines else None


def daily_summaries(c, now: datetime | None = None) -> int:
    now = now or _now()
    local = now.astimezone(RIYADH)
    if local.hour < SUMMARY_HOUR:
        return 0
    day = local.date().isoformat()
    if c.execute("SELECT 1 FROM app_notices WHERE dedupe_key=? LIMIT 1", (f"daily-run:{day}",)).fetchone():
        return 0
    sent = 0
    for org in c.execute("SELECT id FROM organizations WHERE archived_at IS NULL").fetchall():
        text = summary_text(c, org["id"], now)
        if text and add(c, org["id"], "daily_summary", "ملخص يومك في خدوم 📋", text, "home", f"daily:{org['id']}:{day}"):
            sent += 1
    # علامة أن ملخص اليوم انرسل، حتى ما يعاد حساب كل المؤسسات مع كل طلب.
    c.execute("INSERT INTO app_notices(organization_id,kind,title,body,action,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?)",
              (-1, "system", "daily-run", str(sent), "", f"daily-run:{day}", now.isoformat()))
    return sent


# ---------- تتبع المركبات ----------
def tracking_status_changed(c, organization_id: int, vehicle_key: str, vehicle_name: str, driver: str, old: str, new: str) -> None:
    """السائق قفل الموقع أو سحب الإذن أو أوقف التتبع، أو رجع التتبع بعد مشكلة."""
    who = f"السائق «{driver}»" if driver else "السائق"
    name = vehicle_name or "المركبة"
    stamp = _now().isoformat()
    if new in PROBLEM_STATES and old != new:
        add(c, organization_id, "tracking", f"تتبع {name} متوقف 🚨", f"{who} {PROBLEM_STATES[new]}، وموقع {name} ما يوصلك الحين.", "vehicles",
            f"track:{organization_id}:{vehicle_key}:{new}:{stamp[:16]}")
    elif new == "tracking" and (old in PROBLEM_STATES or old == "silent"):
        add(c, organization_id, "tracking", f"رجع تتبع {name} ✅", f"موقع {name} يوصلك من جديد.", "vehicles",
            f"track:{organization_id}:{vehicle_key}:resumed:{stamp[:16]}")


def tracking_silence(c, in_schedule, now: datetime | None = None) -> int:
    """جوال السائق مربوط والدوام شغال، بس ما وصل منه شي أكثر من 5 دقائق."""
    now = now or _now()
    limit = (now - timedelta(minutes=SILENCE_MINUTES)).isoformat()
    sent = 0
    try:
        rows = c.execute("""SELECT organization_id,vehicle_key,vehicle_name,driver_name,weekdays,start_minute,end_minute,enabled,phone_status,last_heartbeat,device_hash
            FROM vehicle_tracking_schedules WHERE enabled=1 AND device_hash<>'' AND phone_status IN ('tracking','armed')""").fetchall()
    except Exception:
        return 0
    for row in rows:
        if not in_schedule(row, now) or not row["last_heartbeat"] or str(row["last_heartbeat"]) >= limit:
            continue
        last_point = c.execute("SELECT MAX(created_at) n FROM vehicle_location_events WHERE organization_id=? AND vehicle_key=?",
                               (row["organization_id"], row["vehicle_key"])).fetchone()
        latest = max(str(row["last_heartbeat"] or ""), str((last_point["n"] if last_point else "") or ""))
        if latest >= limit:
            continue
        name = row["vehicle_name"] or "المركبة"
        who = f"جوال السائق «{row['driver_name']}»" if row["driver_name"] else "جوال السائق"
        if add(c, row["organization_id"], "tracking", f"ما وصل موقع {name} من {SILENCE_MINUTES} دقائق ⚠️",
               f"{who} ما أرسل شي من {SILENCE_MINUTES} دقائق والدوام شغال. ممكن الجوال مقفل أو بدون إنترنت.", "vehicles",
               f"silent:{row['organization_id']}:{row['vehicle_key']}:{latest[:16]}"):
            c.execute("UPDATE vehicle_tracking_schedules SET phone_status='silent' WHERE organization_id=? AND vehicle_key=?", (row["organization_id"], row["vehicle_key"]))
            sent += 1
    return sent


def sweep(c, in_schedule) -> None:
    renew_reminders(c)
    daily_summaries(c)
    tracking_silence(c, in_schedule)


# ---------- أعطال التطبيق ----------
def record_error(c, organization_id: int, user_id: int, data: dict[str, Any]) -> bool:
    message = " ".join(str(data.get("message") or "").split())[:900]
    if not message:
        return False
    since = (_now() - timedelta(hours=1)).isoformat()
    # نفس الخطأ من نفس المؤسسة خلال ساعة يُحسب مرة وحدة، وحد أقصى 50 بالساعة لكل مؤسسة.
    if c.execute("SELECT 1 FROM app_errors WHERE organization_id=? AND message=? AND created_at>=? LIMIT 1", (organization_id, message, since)).fetchone():
        return False
    count = c.execute("SELECT COUNT(*) n FROM app_errors WHERE organization_id=? AND created_at>=?", (organization_id, since)).fetchone()
    if int(count["n"] or 0) >= 50:
        return False
    c.execute("INSERT INTO app_errors(organization_id,user_id,message,stack,location,app_version,device,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (organization_id, user_id, message, str(data.get("stack") or "")[:4000], str(data.get("where") or "")[:200],
               str(data.get("appVersion") or "")[:40], str(data.get("device") or "")[:120], _now().isoformat()))
    return True


def recent_errors(c, days: int = 7) -> dict[str, Any]:
    since = (_now() - timedelta(days=days)).isoformat()
    rows = c.execute("""SELECT e.message,COUNT(*) n,COUNT(DISTINCT e.organization_id) orgs,MAX(e.created_at) last_at,MAX(e.app_version) app_version,
        MAX(e.location) location,MAX(o.name) organization FROM app_errors e LEFT JOIN organizations o ON o.id=e.organization_id
        WHERE e.created_at>=? GROUP BY e.message ORDER BY MAX(e.created_at) DESC LIMIT 30""", (since,)).fetchall()
    total = c.execute("SELECT COUNT(*) n FROM app_errors WHERE created_at>=?", (since,)).fetchone()
    return {"days": days, "total": int(total["n"] or 0), "items": [dict(r) for r in rows]}
