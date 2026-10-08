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

التعديلات (بيانات مؤسسة، الباقة والاشتراك، العروض، قرار الإعلانات):
1) جهّز الطلب بأداة propose_… المناسبة.
2) اقرأ للمدير الملخص الراجع بالضبط واسأله: «أأكد؟».
3) لا تستدعي confirm_action إلا إذا قال المدير صراحة «أكد» في رسالته الأخيرة. أي رد ثاني يعني لا تنفذ.
4) بعد التنفيذ قل النتيجة باختصار.
لا تنفذ أي شي خارج هذي الأدوات، ولا تعد بشي ما تقدر عليه (مثل حذف مؤسسة أو تحويل فلوس).
"""

PERMISSION = {
    "find_organization": "organizations.view", "organization_status": "organizations.view",
    "complaint_details": "support", "recheck_complaint": "support", "platform_status": "security",
    "pending_ads": "ads", "propose_update_organization": "organizations.edit",
    "propose_subscription": "packages", "propose_offer": "offers", "propose_ad_decision": "ads",
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
    {"type": "function", "name": "propose_update_organization", "description": "تجهيز تعديل بيانات مؤسسة (الاسم أو النشاط أو رقم التواصل). لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"organization_id": {"type": "integer"}, "name": {"type": "string"}, "activity": {"type": "string"}, "phone": {"type": "string"}}, ["organization_id"])},
    {"type": "function", "name": "propose_subscription", "description": "تجهيز تغيير باقة مؤسسة (free أو basic أو vip) و/أو إضافة أيام لاشتراكها. لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"organization_id": {"type": "integer"}, "package": {"type": "string", "enum": ["free", "basic", "vip"]}, "add_days": {"type": "integer"}}, ["organization_id"])},
    {"type": "function", "name": "propose_offer", "description": "تجهيز عرض باقات: اشتراك عدد أشهر مدفوعة (1 أو 3 أو 6 أو 12) مع أشهر مجانية إضافية، للأساسية أو VIP أو الاثنين. لا ينفذ قبل تأكيد المدير",
     "parameters": _obj({"package": {"type": "string", "enum": ["basic", "vip", "basic,vip"]}, "paid_months": {"type": "integer"}, "bonus_months": {"type": "integer"}, "days_valid": {"type": "integer"}, "label": {"type": "string"}}, ["package", "paid_months", "bonus_months"])},
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

    def propose_offer(self, c, package: Any = None, paid_months: Any = None, bonus_months: Any = None, days_valid: Any = None, label: Any = None, **_):
        if package not in ("basic", "vip", "basic,vip"):
            raise ValueError("حدد الباقة: الأساسية أو VIP أو الاثنين")
        try:
            months, bonus, valid = int(paid_months), int(bonus_months or 0), int(days_valid or 30)
        except (TypeError, ValueError):
            raise ValueError("المدد لازم تكون أرقام")
        if months not in owner_admin.PACKAGE_DURATIONS:
            raise ValueError("مدة الاشتراك المدفوعة لازم تكون شهر أو 3 أو 6 أو 12")
        if not 0 < bonus <= 60:
            raise ValueError("الأشهر المجانية لازم تكون من 1 إلى 60")
        if not 1 <= valid <= 365:
            raise ValueError("مدة العرض لازم تكون من يوم إلى سنة")
        for item in package.split(","):
            if not owner_admin.package_price_map(c, item).get(months):
                raise ValueError("السعر الأساسي لهذه المدة غير مهيأ في الباقات")
        names = " و".join(PACKAGE_NAMES[p] for p in package.split(","))
        text = str(label or "").strip()[:100] or f"اشتراك {months} أشهر + {bonus} مجانًا"
        start = _now()
        summary = f"عرض جديد للباقة {names}: ادفع {months} شهر واحصل على {bonus} شهر مجاني، يبدأ اليوم ويستمر {valid} يوم، بعنوان «{text}»"
        return self._propose("offer", {"package": package, "paid_months": months, "bonus_months": bonus, "starts_at": start.isoformat(),
                                       "ends_at": (start + timedelta(days=valid)).isoformat(), "offer_type": "bonus", "label": text}, summary)

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
        if not self._allowed({"organization": "organizations.edit", "subscription": "packages", "offer": "offers", "ad": "ads"}[kind]):
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
