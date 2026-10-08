"""موظف الإدارة الصوتي والكتابي: يقرأ ويفحص وينفذ أوامر محددة لمدير خدووم.

القراءة والفحص تُنفذ مباشرة. أي تعديل (بيانات مؤسسة، اشتراك، عرض، قرار إعلان) يمر بمرحلتين:
الموظف يجهز الطلب ويقرأ ملخصه، ثم لا يُنفذ إلا بتأكيد صريح من المدير («أكد») أو بزر التأكيد
في الصفحة. الطلبات المجهزة تبقى في الذاكرة عشر دقائق فقط، وكل تنفيذ يُسجل في سجل الإدارة.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import ai_core
import calls_trial
import owner_admin

MAX_TOOL_ROUNDS = 6
PENDING_SECONDS = 600
CONFIRM_WORDS = re.compile(r"(أ|ا|إ)?ك[ّ]?د|أؤكد|اوكد|أوكد|confirm", re.IGNORECASE)

INSTRUCTIONS = """أنت «موظف الإدارة» في منصة خدووم، تكلم مدير المنصة (المالك أو أحد موظفي الإدارة).
تكلم بلهجة سعودية واضحة ومهنية، وبجمل قصيرة: ابدأ بالخلاصة ثم التفاصيل المهمة فقط.
لا تذكر معلومة لم ترجع من أداة. إذا ما قدرت تعرف شي قل وش الناقص.
الأرقام: رقم الشكوى مثل KHD-2026-000022 أو 22، ورقم الإعلان، ورقم المؤسسة.
إذا ذكر المدير مؤسسة بالاسم ابحث عنها بـ find_organization، وإذا طلع أكثر من نتيجة اسأله أي وحدة.
أسئلة الأرقام العامة (كم مؤسسة، كم مشترك، كم VIP، كم جديد) جاوبها من platform_stats ولا تبحث عن مؤسسة.
أسئلة مستخدمي مؤسسة من organization_users، وإعادة كلمة المرور من password_resets، والعروض والأكواد من current_offers.

التعديلات (بيانات مؤسسة، الباقة والاشتراك، العروض والخصومات، أكواد الخصم، قرار الإعلانات):
1) جهّز الطلب بأداة propose_… المناسبة.
2) اقرأ للمدير الملخص الراجع بالضبط واسأله: «أأكد؟».
3) لا تستدعي confirm_action إلا إذا قال المدير صراحة «أكد» في رسالته الأخيرة. أي رد ثاني يعني لا تنفذ.
4) بعد التنفيذ قل النتيجة باختصار.
لا تنفذ أي شي خارج هذي الأدوات، ولا تعد بشي ما تقدر عليه (مثل حذف مؤسسة أو تحويل فلوس).
"""

PERMISSION = {
    "find_organization": "organizations.view", "organization_status": "organizations.view",
    "complaint_details": "support", "recheck_complaint": "support", "platform_status": "security",
    "pending_ads": "ads", "current_offers": "offers", "platform_stats": "organizations.view",
    "organization_users": "organizations.view", "password_resets": "organizations.view", "propose_update_organization": "organizations.edit",
    "propose_subscription": "packages", "propose_offer": "offers", "propose_ad_decision": "ads", "propose_discount_code": "codes",
}

_obj = lambda props, required=(): {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}
TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "find_organization", "description": "البحث عن مؤسسة مشتركة بالاسم أو جزء منه",
     "parameters": _obj({"name": {"type": "string"}}, ["name"])},
    {"type": "function", "name": "organization_status", "description": "وضع مؤسسة: البيانات، الباقة وتاريخ الانتهاء، الإيقاف، المستخدمون، الشكاوى المفتوحة",
     "parameters": _obj({"organization_id": {"type": "integer"}}, ["organization_id"])},
    {"type": "function", "name": "complaint_details", "description": "تفاصيل شكوى برقمها: نصها وحالتها والرد الحالي وتشخيص الموظف التقني وسبب الرد وآخر الأحداث",
     "parameters": _obj({"reference": {"type": "string"}}, ["reference"])},
    {"type": "function", "name": "recheck_complaint", "description": "إعادة فحص شكوى الآن مع الموظف التقني وإرجاع النتيجة (قد يحدّث الرد الآلي للمشترك)",
     "parameters": _obj({"reference": {"type": "string"}}, ["reference"])},
    {"type": "function", "name": "platform_status", "description": "وضع المنصة الحالي: الخدمات والشكاوى المفتوحة والمتأخرة",
     "parameters": _obj({})},
    {"type": "function", "name": "pending_ads", "description": "طلبات الإعلانات الجديدة التي تنتظر الموافقة",
     "parameters": _obj({})},
    {"type": "function", "name": "platform_stats", "description": "أرقام المنصة: عدد المؤسسات كلها وحسب الباقة، الجديدة هالأسبوع وهالشهر، الموقوفة، وعدد المستخدمين",
     "parameters": _obj({})},
    {"type": "function", "name": "organization_users", "description": "مستخدمو مؤسسة: العدد والأسماء والأدوار وآخر دخول ومحاولات الدخول الفاشلة لكل واحد",
     "parameters": _obj({"organization_id": {"type": "integer"}}, ["organization_id"])},
    {"type": "function", "name": "password_resets", "description": "كم مرة انعادت كلمة المرور في مؤسسة (أو لمستخدم معين باسم المستخدم) ومتى آخر مرة، مع آخر العمليات",
     "parameters": _obj({"organization_id": {"type": "integer"}, "username": {"type": "string"}}, ["organization_id"])},
    {"type": "function", "name": "current_offers", "description": "العروض الحالية على الباقات وأكواد الخصم وأسعار الباقات العادية، مع حالة كل وحدة",
     "parameters": _obj({})},
    {"type": "function", "name": "propose_update_organization", "description": "تجهيز تعديل بيانات مؤسسة (الاسم أو النشاط أو رقم التواصل). لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"organization_id": {"type": "integer"}, "name": {"type": "string"}, "activity": {"type": "string"}, "phone": {"type": "string"}}, ["organization_id"])},
    {"type": "function", "name": "propose_subscription", "description": "تجهيز تغيير باقة مؤسسة (free أو basic أو vip) و/أو إضافة أيام لاشتراكها. لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"organization_id": {"type": "integer"}, "package": {"type": "string", "enum": ["free", "basic", "vip"]}, "add_days": {"type": "integer"}}, ["organization_id"])},
    {"type": "function", "name": "propose_offer", "description": "تجهيز عرض على باقة لمدة اشتراك (1 أو 3 أو 6 أو 12 شهر): bonus أشهر مجانية إضافية، أو percent نسبة خصم من السعر، أو price سعر خاص بالريال. لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"package": {"type": "string", "enum": ["basic", "vip", "basic,vip"]}, "paid_months": {"type": "integer"},
                         "offer_type": {"type": "string", "enum": ["bonus", "percent", "price"]}, "bonus_months": {"type": "integer"},
                         "discount_percent": {"type": "number"}, "price_sar": {"type": "number"}, "days_valid": {"type": "integer"}, "label": {"type": "string"}},
                        ["package", "paid_months", "offer_type"])},
    {"type": "function", "name": "propose_discount_code", "description": "تجهيز كود خصم يكتبه المشترك وقت الاشتراك: نسبة مئوية أو مبلغ بالريال، للباقات والمدد المحددة، بعدد استخدامات ومدة صلاحية. لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"code": {"type": "string"}, "discount_percent": {"type": "number"}, "discount_amount": {"type": "number"},
                         "packages": {"type": "string", "enum": ["basic", "vip", "basic,vip"]}, "durations": {"type": "string"},
                         "max_uses": {"type": "integer"}, "days_valid": {"type": "integer"}}, ["code"])},
    {"type": "function", "name": "propose_ad_decision", "description": "تجهيز الموافقة على طلب إعلان أو رفضه برقمه. لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"ad_id": {"type": "integer"}, "decision": {"type": "string", "enum": ["approve", "reject"]}, "note": {"type": "string"}}, ["ad_id", "decision"])},
    {"type": "function", "name": "confirm_action", "description": "تنفيذ طلب مجهز بعد أن قال المدير «أكد» صراحة",
     "parameters": _obj({"token": {"type": "string"}}, ["token"])},
]

_pending: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def confirmed_by(text: Any) -> bool:
    return bool(CONFIRM_WORDS.search(str(text or "")))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _reference_id(c, reference: Any) -> int | None:
    text = str(reference or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).strip().upper()
    row = c.execute("SELECT id FROM support_tickets WHERE reference_code=?", (text,)).fetchone() if text.startswith("KHD") else None
    if row:
        return int(row["id"])
    digits = re.findall(r"\d+", text)
    return int(digits[-1]) if digits else None


def _organization(c, organization_id: Any) -> dict[str, Any]:
    try:
        ident = int(organization_id)
    except (TypeError, ValueError):
        raise ValueError("رقم المؤسسة غير صحيح")
    row = c.execute("SELECT id,name,activity,phone FROM organizations WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise ValueError("ما لقيت مؤسسة بهذا الرقم")
    return dict(row)


def _subscription(c, organization_id: int) -> dict[str, Any] | None:
    row = c.execute("SELECT package,starts_at,expires_at FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()
    return dict(row) if row else None


PACKAGE_NAMES = {"free": "المجانية", "basic": "الأساسية", "vip": "VIP"}


class Agent:
    """أدوات موظف الإدارة. db: مصنع اتصالات، s: وحدة الخادم، actor: المدير الحالي."""

    def __init__(self, db: Callable[[], Any], s: Any, actor: dict[str, Any]):
        self.db, self.s, self.actor = db, s, actor

    # ---------- أدوات القراءة ----------
    def _dispatch(self, c, route: str, method: str, data: dict | None = None, query: dict | None = None):
        return owner_admin.dispatch(c, route, method, data or {}, query or {}, 1, self.actor, None, self.s)

    def find_organization(self, c, name: str = "", **_):
        term = " ".join(str(name).split())[:80]
        if not term:
            raise ValueError("اكتب اسم المؤسسة")
        items = owner_admin.rows(c, "SELECT o.id,o.name,o.activity,s.package FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE o.name LIKE ? ORDER BY o.id LIMIT 8", ("%" + term + "%",))
        return {"items": items, "count": len(items)}

    def organization_status(self, c, organization_id: Any = None, **_):
        org = _organization(c, organization_id)
        sub = _subscription(c, org["id"]) or {}
        suspended = owner_admin.scalar(c, "SELECT COUNT(*) n FROM platform_org_state WHERE organization_id=? AND suspended=1", (org["id"],)) if owner_admin.table_exists(c, "platform_org_state", self.s) else 0
        users = owner_admin.scalar(c, "SELECT COUNT(*) n FROM users WHERE organization_id=? AND archived_at IS NULL", (org["id"],))
        tickets = owner_admin.rows(c, "SELECT id,reference_code,category,status,created_at FROM support_tickets WHERE organization_id=? AND status NOT IN ('resolved','closed') ORDER BY id DESC LIMIT 5", (org["id"],))
        return {**org, "package": PACKAGE_NAMES.get(sub.get("package") or "free", sub.get("package")), "expires_at": sub.get("expires_at"),
                "suspended": bool(suspended), "users": users, "open_complaints": tickets}

    def complaint_details(self, c, reference: Any = None, **_):
        ident = _reference_id(c, reference)
        ticket = c.execute("SELECT t.id,t.reference_code,t.organization_id,o.name organization_name,t.category,t.title,t.message,t.status,t.owner_reply,t.owner_reply_by,t.created_at,t.updated_at FROM support_tickets t LEFT JOIN organizations o ON o.id=t.organization_id WHERE t.id=?", (ident,)).fetchone() if ident else None
        if ticket is None:
            raise ValueError("ما لقيت شكوى بهذا الرقم")
        task = c.execute("SELECT status,diagnosis,proposal,problem_type,knowledge,suggested_action,result FROM technical_tasks WHERE support_ticket_id=? ORDER BY id DESC LIMIT 1", (ident,)).fetchone() if owner_admin.table_exists(c, "technical_tasks", self.s) else None
        events = owner_admin.rows(c, "SELECT actor_name,event_type,body,created_at FROM support_ticket_events WHERE ticket_id=? ORDER BY id DESC LIMIT 8", (ident,)) if owner_admin.table_exists(c, "support_ticket_events", self.s) else []
        for event in events:
            event["body"] = str(event.get("body") or "")[:400]
        return {"ticket": dict(ticket), "technical": dict(task) if task else None, "recent_events": events,
                "reply_source": "الإدارة" if ticket["owner_reply_by"] == "admin" else "الموظف التقني الآلي"}

    def recheck_complaint(self, c, reference: Any = None, **_):
        ident = _reference_id(c, reference)
        if not ident or not c.execute("SELECT 1 FROM support_tickets WHERE id=?", (ident,)).fetchone():
            raise ValueError("ما لقيت شكوى بهذا الرقم")
        result = self._dispatch(c, "technical-ai/ask", "POST", {"question": f"فحص شكوى #{ident}", "_rule": True})
        keep = ("answer", "diagnosis", "proposal", "problemType", "suggestedAction", "limitations", "checks")
        return {k: result.get(k) for k in keep if isinstance(result, dict) and k in result} or {"result": result}

    def platform_status(self, c, **_):
        report = self._dispatch(c, "technical-ai/daily-report", "GET")
        return {k: report.get(k) for k in ("checkedAt", "summary", "serviceChecks", "counts", "overdueTickets", "recommendations") if isinstance(report, dict)}

    def pending_ads(self, c, **_):
        out = self._dispatch(c, "ads", "GET", query={"status": "pending"})
        items = [{"ad_id": ad.get("id"), "organization": ad.get("organization_name"), "title": ad.get("title"), "message": str(ad.get("message") or "")[:200],
                  "requested_days": ad.get("requested_days"), "created_at": ad.get("created_at")} for ad in (out.get("items") or [])]
        return {"items": items, "count": len(items)}

    def platform_stats(self, c, **_):
        now = _now()
        week, month = (now - timedelta(days=7)).isoformat(), (now - timedelta(days=30)).isoformat()
        by_package = {name: 0 for name in PACKAGE_NAMES.values()}
        for row in c.execute("SELECT COALESCE(s.package,'free') package,COUNT(*) n FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id GROUP BY COALESCE(s.package,'free')").fetchall():
            by_package[PACKAGE_NAMES.get(row["package"], row["package"])] = row["n"]
        suspended = owner_admin.scalar(c, "SELECT COUNT(*) n FROM platform_org_state WHERE suspended=1") if owner_admin.table_exists(c, "platform_org_state", self.s) else 0
        return {"organizations": owner_admin.scalar(c, "SELECT COUNT(*) n FROM organizations"), "by_package": by_package,
                "new_last_7_days": owner_admin.scalar(c, "SELECT COUNT(*) n FROM organizations WHERE created_at>=?", (week,)),
                "new_last_30_days": owner_admin.scalar(c, "SELECT COUNT(*) n FROM organizations WHERE created_at>=?", (month,)),
                "suspended": suspended, "users": owner_admin.scalar(c, "SELECT COUNT(*) n FROM users WHERE archived_at IS NULL")}

    def organization_users(self, c, organization_id: Any = None, **_):
        org = _organization(c, organization_id)
        month = (_now() - timedelta(days=30)).isoformat()
        users = owner_admin.rows(c, """SELECT u.id,u.name,u.username,u.role,u.active,
            (SELECT MAX(created_at) FROM audit_logs a WHERE a.actor_user_id=u.id AND a.action='login') last_login,
            (SELECT COUNT(*) FROM audit_logs a WHERE a.actor_user_id=u.id AND a.action='failed_login' AND a.created_at>=?) failed_logins_30_days
            FROM users u WHERE u.organization_id=? AND u.archived_at IS NULL ORDER BY u.id""", (month, org["id"]))
        for user in users:
            user["active"] = bool(user.get("active"))
        return {"organization": org["name"], "count": len(users), "users": users}

    def password_resets(self, c, organization_id: Any = None, username: Any = None, **_):
        org = _organization(c, organization_id)
        users = {row["id"]: row for row in owner_admin.rows(c, "SELECT id,name,username FROM users WHERE organization_id=?", (org["id"],))}
        wanted = None
        if username not in (None, ""):
            wanted = next((u for u in users.values() if str(u["username"]).lower() == str(username).strip().lower() or u["name"] == str(username).strip()), None)
            if wanted is None:
                raise ValueError("ما لقيت مستخدم بهذا الاسم في المؤسسة")
        events = []
        for row in owner_admin.rows(c, "SELECT actor_user_id,target_id,summary,created_at FROM audit_logs WHERE organization_id=? AND action='password_reset' ORDER BY id DESC LIMIT 100", (org["id"],)):
            target = users.get(int(row["target_id"])) if str(row.get("target_id") or "").isdigit() else None
            events.append({"for": (target or {}).get("username"), "by": (users.get(row["actor_user_id"]) or {}).get("name") or "المؤسسة", "summary": row["summary"],
                           "at": row["created_at"], "_target": target["id"] if target else None})
        if owner_admin.table_exists(c, "platform_audit", self.s) and users:
            for row in owner_admin.rows(c, "SELECT target,created_at,actor FROM platform_audit WHERE action='directory_password_reset' ORDER BY id DESC LIMIT 200"):
                found = re.search(r"user=(\d+)", str(row["target"]))
                if found and int(found.group(1)) in users:
                    target = users[int(found.group(1))]
                    events.append({"for": target["username"], "by": "إدارة خدووم: " + str(row["actor"]), "summary": "إعادة كلمة المرور من لوحة الإدارة",
                                   "at": row["created_at"], "_target": target["id"]})
        if wanted is not None:
            events = [e for e in events if e["_target"] == wanted["id"]]
        events.sort(key=lambda e: str(e["at"]), reverse=True)
        for event in events:
            event.pop("_target", None)
        return {"organization": org["name"], "user": wanted["username"] if wanted else None, "count": len(events),
                "last_at": events[0]["at"] if events else None, "recent": events[:10]}

    def current_offers(self, c, **_):
        now = _now().isoformat()
        prices = {}
        if owner_admin.table_exists(c, "package_prices", self.s):
            for row in c.execute("SELECT package,duration_months,price_sar FROM package_prices ORDER BY package,duration_months").fetchall():
                prices.setdefault(PACKAGE_NAMES.get(row["package"], row["package"]), {})[f"{row['duration_months']} شهر"] = row["price_sar"]
        offers = []
        if owner_admin.table_exists(c, "package_offers", self.s):
            for row in owner_admin.rows(c, "SELECT * FROM package_offers ORDER BY id DESC LIMIT 30"):
                running = bool(row.get("active")) and owner_admin.active_offer(row)
                ended = bool(row.get("ends_at")) and str(row["ends_at"]) <= now
                offers.append({"id": row.get("id"), "package": PACKAGE_NAMES.get(row.get("package"), row.get("package")), "paid_months": row.get("paid_months"),
                               "type": {"bonus": "أشهر مجانية", "percent": "نسبة خصم", "price": "سعر خاص"}.get(row.get("offer_type") or "price", row.get("offer_type")),
                               "bonus_months": row.get("bonus_months"), "discount_percent": row.get("discount_percent"),
                               "original_price": row.get("base_price_sar"), "offer_price": row.get("price_sar"), "label": row.get("label"),
                               "starts_at": row.get("starts_at"), "ends_at": row.get("ends_at"),
                               # عروض قديمة بلا سعر أساسي لا يعرضها التطبيق للمشتركين.
                               "status": "غير ظاهر في التطبيق" if row.get("base_price_sar") is None else "شغال" if running else "منتهي" if ended else "متوقف"})
        codes = []
        if owner_admin.table_exists(c, "activation_codes", self.s):
            for row in owner_admin.rows(c, "SELECT code_prefix,discount_percent,discount_amount,eligible_packages,eligible_durations,max_uses,used_count,starts_at,expires_at,active FROM activation_codes WHERE code_kind='discount' ORDER BY id DESC LIMIT 30"):
                expired = bool(row.get("expires_at")) and str(row["expires_at"]) <= now
                used_up = row.get("max_uses") is not None and (row.get("used_count") or 0) >= row["max_uses"]
                codes.append({"code": row.get("code_prefix"), "discount": f"{row['discount_percent']:g}%" if row.get("discount_percent") else f"{row.get('discount_amount') or 0:g} ريال",
                              "packages": " و".join(PACKAGE_NAMES.get(p, p) for p in str(row.get("eligible_packages") or "").split(",") if p),
                              "durations": row.get("eligible_durations"), "used": f"{row.get('used_count') or 0} من {row.get('max_uses')}",
                              "expires_at": row.get("expires_at"),
                              "status": "متوقف" if not row.get("active", 1) else "منتهي" if expired else "مستنفد" if used_up else "شغال"})
        return {"package_prices": prices, "offers": offers, "discount_codes": codes,
                "running_offers": sum(1 for o in offers if o["status"] == "شغال"), "running_codes": sum(1 for x in codes if x["status"] == "شغال")}

    # ---------- تجهيز التعديلات ----------
    def _propose(self, kind: str, args: dict[str, Any], summary: str) -> dict[str, Any]:
        token = secrets.token_hex(4)
        with _lock:
            now = time.time()
            for key in [k for k, v in _pending.items() if v["expires"] < now]:
                _pending.pop(key, None)
            _pending[token] = {"kind": kind, "args": args, "summary": summary, "actor": self.actor.get("name"), "expires": now + PENDING_SECONDS}
        return {"token": token, "summary": summary, "status": "بانتظار تأكيد المدير بكلمة «أكد»"}

    def propose_update_organization(self, c, organization_id: Any = None, name: Any = None, activity: Any = None, phone: Any = None, **_):
        org = _organization(c, organization_id)
        changes = {}
        if name not in (None, ""):
            clean = " ".join(str(name).split())[:120]
            if len(clean) < 2:
                raise ValueError("اسم المؤسسة قصير")
            changes["name"] = clean
        if activity not in (None, ""):
            changes["activity"] = " ".join(str(activity).split())[:120]
        if phone not in (None, ""):
            changes["phone"] = str(phone).strip()[:30]
        if not changes:
            raise ValueError("ما حددت وش يتعدل")
        labels = {"name": "الاسم", "activity": "النشاط", "phone": "رقم التواصل"}
        summary = f"تعديل بيانات «{org['name']}» (رقم {org['id']}): " + "، ".join(f"{labels[k]} من «{org.get(k) or '-'}» إلى «{v}»" for k, v in changes.items())
        return self._propose("organization", {"organization_id": org["id"], **changes}, summary)

    def propose_subscription(self, c, organization_id: Any = None, package: Any = None, add_days: Any = None, **_):
        org = _organization(c, organization_id)
        sub = _subscription(c, org["id"]) or {"package": "free", "expires_at": None}
        current = sub.get("package") or "free"
        target = package or current
        if target not in PACKAGE_NAMES:
            raise ValueError("الباقة لازم تكون المجانية أو الأساسية أو VIP")
        try:
            days = int(add_days or 0)
        except (TypeError, ValueError):
            raise ValueError("عدد الأيام غير صحيح")
        if days < 0 or days > 3650:
            raise ValueError("عدد الأيام لازم يكون بين 1 و3650")
        if target != "free" and target != current and days == 0:
            days = 30
        if target == current and days == 0:
            raise ValueError("ما فيه تغيير: حدد باقة مختلفة أو عدد أيام")
        if target == "free":
            expires = None
        else:
            base = _now()
            old = _parse(sub.get("expires_at"))
            if target == current and old and old > base:
                base = old
            expires = (base + timedelta(days=days)).isoformat()
        when = expires[:10] if expires else "بدون انتهاء"
        summary = (f"اشتراك «{org['name']}» (رقم {org['id']}): من {PACKAGE_NAMES[current]} إلى {PACKAGE_NAMES[target]}" if target != current
                   else f"تمديد اشتراك «{org['name']}» (رقم {org['id']}) على {PACKAGE_NAMES[target]} بـ {days} يوم") + f"، ينتهي: {when}"
        return self._propose("subscription", {"organization_id": org["id"], "package": target, "expires_at": expires, "same": target == current}, summary)

    def propose_offer(self, c, package: Any = None, paid_months: Any = None, offer_type: Any = "bonus", bonus_months: Any = None,
                      discount_percent: Any = None, price_sar: Any = None, days_valid: Any = None, label: Any = None, **_):
        if package not in ("basic", "vip", "basic,vip"):
            raise ValueError("حدد الباقة: الأساسية أو VIP أو الاثنين")
        kind = offer_type or "bonus"
        if kind not in ("bonus", "percent", "price"):
            raise ValueError("نوع العرض لازم يكون أشهر مجانية أو نسبة خصم أو سعر خاص")
        try:
            months, valid = int(paid_months), int(days_valid or 30)
            bonus = int(bonus_months or 0)
            percent = float(discount_percent or 0)
            price = float(price_sar or 0)
        except (TypeError, ValueError):
            raise ValueError("الأرقام في العرض غير صحيحة")
        if months not in owner_admin.PACKAGE_DURATIONS:
            raise ValueError("مدة الاشتراك لازم تكون شهر أو 3 أو 6 أو 12")
        if not 1 <= valid <= 365:
            raise ValueError("مدة العرض لازم تكون من يوم إلى سنة")
        if not 0 <= bonus <= 60:
            raise ValueError("الأشهر المجانية لازم تكون من 0 إلى 60")
        if kind == "bonus" and bonus < 1:
            raise ValueError("حدد كم شهر مجاني")
        if kind == "percent" and not 1 <= percent <= 90:
            raise ValueError("نسبة الخصم لازم تكون من 1% إلى 90%")
        packages = package.split(",")
        if kind == "price" and len(packages) > 1:
            raise ValueError("السعر الخاص يكون لباقة وحدة؛ حدد الأساسية أو VIP")
        bases = {}
        for item in packages:
            base = owner_admin.package_price_map(c, item).get(months)
            if not base:
                raise ValueError("السعر الأساسي لهذه المدة غير مهيأ في الباقات")
            bases[item] = base
        if kind == "price" and not 0 < price < bases[packages[0]]:
            raise ValueError(f"السعر الخاص لازم يكون أقل من السعر الأصلي ({bases[packages[0]]:g} ريال)")
        names = " و".join(PACKAGE_NAMES[p] for p in packages)
        prices = "، ".join(f"{PACKAGE_NAMES[p]} من {bases[p]:g} إلى {round(bases[p] * (100 - percent) / 100, 2):g} ريال" for p in packages) if kind == "percent" else ""
        detail = {"bonus": f"ادفع {months} شهر واحصل على {bonus} شهر مجاني",
                  "percent": f"خصم {percent:g}% على اشتراك {months} شهر ({prices})",
                  "price": f"اشتراك {months} شهر بـ {price:g} ريال بدل {bases[packages[0]]:g}"}[kind]
        if kind != "bonus" and bonus:
            detail += f" + {bonus} شهر مجاني"
        text = str(label or "").strip()[:100] or detail[:100]
        start = _now()
        summary = f"عرض جديد للباقة {names}: {detail}، يبدأ اليوم ويستمر {valid} يوم، بعنوان «{text}»"
        args = {"package": package, "paid_months": months, "bonus_months": bonus, "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(days=valid)).isoformat(), "offer_type": kind, "label": text}
        if kind == "percent":
            args["discount_percent"] = percent
        if kind == "price":
            args["price_sar"] = price
        return self._propose("offer", args, summary)

    def propose_discount_code(self, c, code: Any = None, discount_percent: Any = None, discount_amount: Any = None, packages: Any = None,
                              durations: Any = None, max_uses: Any = None, days_valid: Any = None, **_):
        clean = str(code or "").upper().strip()
        if not re.fullmatch(r"[A-Z0-9_-]{2,40}", clean):
            raise ValueError("الكود لازم يكون حروف إنجليزية أو أرقام، من حرفين إلى 40")
        try:
            percent, amount = float(discount_percent or 0), float(discount_amount or 0)
            uses, valid = int(max_uses or 100), int(days_valid or 30)
        except (TypeError, ValueError):
            raise ValueError("الأرقام في الكود غير صحيحة")
        if bool(percent) == bool(amount):
            raise ValueError("حدد نسبة خصم أو مبلغ خصم، واحد منهم بس")
        if percent and not 1 <= percent <= 90:
            raise ValueError("نسبة الخصم لازم تكون من 1% إلى 90%")
        if amount and amount <= 0:
            raise ValueError("مبلغ الخصم غير صحيح")
        if not 1 <= uses <= 100000 or not 1 <= valid <= 365:
            raise ValueError("عدد الاستخدامات أو مدة الصلاحية غير صحيحة")
        eligible = packages or "basic,vip"
        if eligible not in ("basic", "vip", "basic,vip"):
            raise ValueError("الباقات لازم تكون الأساسية أو VIP أو الاثنين")
        months = str(durations or "1,3,6,12").replace(" ", "")
        if not months or any(not x.isdigit() or int(x) not in owner_admin.PACKAGE_DURATIONS for x in months.split(",")):
            raise ValueError("المدد لازم تكون من 1 و3 و6 و12")
        if c.execute("SELECT 1 FROM activation_codes WHERE code_hash=?", (hashlib.sha256(clean.encode()).hexdigest(),)).fetchone():
            raise ValueError("هذا الكود موجود من قبل؛ اختر كود ثاني")
        start = _now()
        names = " و".join(PACKAGE_NAMES[p] for p in eligible.split(","))
        value = f"خصم {percent:g}%" if percent else f"خصم {amount:g} ريال"
        summary = f"كود خصم «{clean}»: {value} على {names} لمدد {months} شهر، يستخدم {uses} مرة، صالح {valid} يوم"
        return self._propose("code", {"code": clean, "discount_percent": percent, "discount_amount": amount, "eligible_packages": eligible,
                                      "eligible_durations": months, "max_uses": uses, "starts_at": start.isoformat(),
                                      "expires_at": (start + timedelta(days=valid)).isoformat(), "recipient_name": "عرض عام"}, summary)

    def propose_ad_decision(self, c, ad_id: Any = None, decision: Any = None, note: Any = None, **_):
        if decision not in ("approve", "reject"):
            raise ValueError("القرار لازم يكون موافقة أو رفض")
        try:
            ident = int(ad_id)
        except (TypeError, ValueError):
            raise ValueError("رقم الإعلان غير صحيح")
        ad = c.execute("SELECT a.id,a.title,a.requested_days,a.approved,a.active,o.name organization_name FROM advertisements a JOIN organizations o ON o.id=a.organization_id WHERE a.id=? AND COALESCE(a.deleted,0)=0", (ident,)).fetchone()
        if ad is None:
            raise ValueError("ما لقيت إعلان بهذا الرقم")
        days = int(ad["requested_days"] or 10)
        args: dict[str, Any] = {"ad_id": ident, "status": "published" if decision == "approve" else "rejected", "review_note": str(note or "").strip()[:500]}
        if decision == "approve":
            args["expires_at"] = (_now() + timedelta(days=days)).isoformat()
        summary = (f"الموافقة على إعلان رقم {ident} «{ad['title']}» من {ad['organization_name']} ونشره {days} يوم" if decision == "approve"
                   else f"رفض إعلان رقم {ident} «{ad['title']}» من {ad['organization_name']}" + (f"، والسبب: {args['review_note']}" if args["review_note"] else ""))
        return self._propose("ad", args, summary)

    # ---------- التنفيذ بعد التأكيد ----------
    def confirm(self, c, token: Any) -> dict[str, Any]:
        with _lock:
            item = _pending.get(str(token or ""))
            if item is None or item["expires"] < time.time():
                _pending.pop(str(token or ""), None)
                raise ValueError("الطلب منتهي أو غير موجود؛ جهزه من جديد")
            if item["actor"] != self.actor.get("name"):
                raise ValueError("هذا الطلب جهزه مدير آخر")
            _pending.pop(str(token), None)
        kind, args = item["kind"], item["args"]
        if not self._allowed({"organization": "organizations.edit", "subscription": "packages", "offer": "offers", "ad": "ads", "code": "codes"}[kind]):
            raise PermissionError("ما عندك صلاحية لهذا التعديل")
        actor = self.actor.get("name") or "المدير"
        if kind == "organization":
            fields = [k for k in ("name", "activity", "phone") if k in args]
            c.execute("UPDATE organizations SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", tuple(args[k] for k in fields) + (args["organization_id"],))
            owner_admin.audit(c, actor, "agent_organization_updated", json.dumps(args, ensure_ascii=False))
        elif kind == "subscription":
            starts = (_subscription(c, args["organization_id"]) or {}).get("starts_at") if args["same"] else None
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?) ON CONFLICT(organization_id) DO UPDATE SET package=excluded.package,starts_at=excluded.starts_at,expires_at=excluded.expires_at",
                      (args["organization_id"], args["package"], starts or _now().isoformat(), args["expires_at"]))
            owner_admin.clear_gift_fallback(c, args["organization_id"], self.s)
            owner_admin.audit(c, actor, "agent_subscription_changed", json.dumps(args, ensure_ascii=False))
        elif kind == "offer":
            self._dispatch(c, "offers", "POST", {k: v for k, v in args.items()})
            owner_admin.audit(c, actor, "agent_offer_created", json.dumps(args, ensure_ascii=False))
        elif kind == "code":
            self._dispatch(c, "codes", "POST", dict(args))
            owner_admin.audit(c, actor, "agent_discount_code_created", json.dumps({k: v for k, v in args.items() if k != "code"} | {"code_prefix": args["code"][:3]}, ensure_ascii=False))
        elif kind == "ad":
            data = {k: v for k, v in args.items() if k != "ad_id"}
            self._dispatch(c, "ads/%d" % args["ad_id"], "PUT", data)
        return {"done": True, "summary": item["summary"]}

    # ---------- التشغيل ----------
    def _allowed(self, permission: str) -> bool:
        return permission in (self.actor.get("permissions") or [])

    def call(self, name: str, args: dict[str, Any], user_text: str = "") -> dict[str, Any]:
        """ينفذ أداة واحدة ويرجع نتيجتها أو رسالة خطأ واضحة. التأكيد يحتاج كلمة «أكد» من المدير."""
        try:
            with self.db() as c:
                if name == "confirm_action":
                    if not confirmed_by(user_text):
                        return {"error": "ما أكد المدير. اسأله «أأكد؟» وانتظر يقول أكد."}
                    result = self.confirm(c, args.get("token"))
                else:
                    method = getattr(self, name, None) if name in PERMISSION else None
                    if method is None:
                        return {"error": "أداة غير متاحة"}
                    if not self._allowed(PERMISSION[name]):
                        return {"error": "ما عندك صلاحية لهذا الأمر"}
                    result = method(c, **{k: v for k, v in (args or {}).items() if isinstance(k, str)})
                c.commit()
                return result
        except (ValueError, PermissionError, TypeError, KeyError) as error:
            return {"error": str(error)[:300]}
        except Exception as error:  # خطأ غير متوقع لا يسقط المحادثة
            message = getattr(error, "message", "") or str(error)
            print(f"ADMIN AGENT TOOL ERROR {name}: {type(error).__name__} {message[:200]}")
            return {"error": str(message)[:300] or "تعذر التنفيذ"}

    def chat(self, message: str, history: Any, client: ai_core.ResponsesClient | None = None) -> dict[str, Any]:
        client = client or ai_core.ResponsesClient()
        clean = [{"role": h.get("role"), "text": str(h.get("text", ""))[:1500]} for h in (history or [])[-10:]
                 if isinstance(h, dict) and h.get("role") in ("user", "assistant")]
        items: list[dict[str, Any]] = [{"role": "user", "content": json.dumps({"conversation": clean, "current_message": message}, ensure_ascii=False)}]
        safety = hashlib.sha256(("khdoom-admin-agent:" + str(self.actor.get("name"))).encode()).hexdigest()[:64]
        actions: list[dict[str, Any]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = client.transport({"model": ai_core.PRIMARY_MODEL, "instructions": INSTRUCTIONS + "\nاسم المدير: " + str(self.actor.get("name")),
                                         "input": items, "tools": TOOLS, "parallel_tool_calls": False, "max_output_tokens": 1500, "store": False, "safety_identifier": safety})
            calls = [x for x in response.get("output", []) if isinstance(x, dict) and x.get("type") == "function_call"]
            if not calls:
                text = client.output_text(response)
                if not text:
                    raise ai_core.AIServiceError(502, "وصل رد فارغ من خدمة الذكاء الاصطناعي")
                return {"answer": text, "pending": [a for a in actions if a.get("token")]}
            items.extend(response.get("output", []))
            for call in calls:
                try:
                    args = json.loads(call.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = str(call.get("name", ""))
                result = self.call(name, args if isinstance(args, dict) else {}, user_text=message)
                if name.startswith("propose_") and isinstance(result, dict) and result.get("token"):
                    actions.append(result)
                items.append({"type": "function_call_output", "call_id": call.get("call_id"), "output": json.dumps(result, ensure_ascii=False, default=str)[:12000]})
        raise ai_core.AIServiceError(502, "تجاوز موظف الإدارة الحد الآمن لعدد الأدوات")


def voice_session(actor_name: str, voice: Any = "male") -> dict[str, Any]:
    """جلسة صوتية حية من المتصفح بنفس أدوات الكتابة؛ الأدوات تُنفذ عبر خادم خدووم لا من المتصفح."""
    instructions = INSTRUCTIONS + "\nأنت في مكالمة صوتية: رد بجمل قصيرة جدًا، واقرأ الأرقام بوضوح.\nاسم المدير: " + str(actor_name)
    return calls_trial.create_session(voice, instructions=instructions, tools=TOOLS)
