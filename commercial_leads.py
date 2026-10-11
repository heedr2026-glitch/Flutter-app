"""دفتر فرص البيع للبحث التجاري: الفرص وحالاتها وتذكير المتابعة، والبحث التلقائي الأسبوعي
(عملاء أو مشاريع جديدة)، وملف كل جهة، ورابط عرض السعر الذكي.

الإرسال للعملاء يظل بيد صاحب المؤسسة (واتساب يدوي)؛ الخادم ما يرسل لأحد من نفسه.
"""
from __future__ import annotations

import html
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

STATUSES = {
    "new": "جديد", "contacted": "تواصلت", "replied": "رد", "wants_price": "يبي سعر",
    "offer_sent": "أرسلت العرض", "won": "فزت", "lost": "خسرت",
}
FOLLOW_UP_STATUSES = {"contacted", "offer_sent"}
FOLLOW_UP_DAYS = 3
WATCH_TYPES = {"clients": "عملاء محتملون", "projects": "مشاريع جديدة"}
WATCH_DAYS = 7
QUOTE_DAYS = 30


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS commercial_leads (
        id {identity}, organization_id BIGINT NOT NULL, name TEXT NOT NULL, activity TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new', origin TEXT NOT NULL DEFAULT 'manual', profile TEXT NOT NULL DEFAULT '',
        next_follow_up TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_commercial_leads_org ON commercial_leads(organization_id,id)")
    c.execute("""CREATE TABLE IF NOT EXISTS commercial_watch (
        organization_id BIGINT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, keywords TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '', watch_type TEXT NOT NULL DEFAULT 'clients', last_run_at TEXT, last_result TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL)""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS smart_quotes (
        id {identity}, organization_id BIGINT NOT NULL, lead_id BIGINT, token TEXT NOT NULL UNIQUE,
        customer_name TEXT NOT NULL DEFAULT '', items_json TEXT NOT NULL DEFAULT '[]', total REAL NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '', valid_until TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'sent',
        open_count INTEGER NOT NULL DEFAULT 0, first_opened_at TEXT, last_opened_at TEXT, response TEXT NOT NULL DEFAULT '',
        responded_at TEXT, created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_smart_quotes_org ON smart_quotes(organization_id,id)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _phone_key(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-9:] if len(digits) >= 9 else ""


def _notify(c, organization_id: int, kind: str, title: str, body: str, action: str, dedupe: str = "") -> None:
    import app_notices
    app_notices.add(c, organization_id, kind, title, body, action, dedupe)


# ---------- الفرص ----------
def list_leads(c, organization_id: int) -> list[dict[str, Any]]:
    rows = c.execute("""SELECT id,name,activity,location,phone,website,source,details,notes,status,origin,profile,next_follow_up,created_at,updated_at
        FROM commercial_leads WHERE organization_id=? ORDER BY CASE WHEN status IN ('won','lost') THEN 1 ELSE 0 END, id DESC LIMIT 300""",
                     (organization_id,)).fetchall()
    quotes = {}
    for q in c.execute("SELECT lead_id,token,status,open_count,total,created_at FROM smart_quotes WHERE organization_id=? AND lead_id IS NOT NULL ORDER BY id",
                       (organization_id,)).fetchall():
        quotes[q["lead_id"]] = dict(q)
    out = []
    for row in rows:
        item = dict(row)
        item["status_name"] = STATUSES.get(item["status"], item["status"])
        item["quote"] = quotes.get(item["id"])
        out.append(item)
    return out


def create_lead(c, organization_id: int, data: dict[str, Any], origin: str = "manual") -> dict[str, Any]:
    name = _clean(data.get("name"), 160)
    if len(name) < 2:
        raise ValueError("اكتب اسم الجهة")
    phone = _clean(data.get("phone"), 40)
    # نفس الجهة (بالاسم أو الرقم) ما تتكرر في الدفتر.
    for row in c.execute("SELECT id,name,phone FROM commercial_leads WHERE organization_id=?", (organization_id,)).fetchall():
        if row["name"].strip().lower() == name.lower() or (phone and _phone_key(phone) and _phone_key(phone) == _phone_key(row["phone"])):
            return {"id": row["id"], "duplicate": True}
    stamp = _now().isoformat()
    cursor = c.execute("""INSERT INTO commercial_leads(organization_id,name,activity,location,phone,website,source,details,notes,status,origin,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (organization_id, name, _clean(data.get("activity"), 160), _clean(data.get("location"), 160), phone,
                                               _clean(data.get("website"), 300), _clean(data.get("source"), 400), str(data.get("details") or "")[:3000],
                                               str(data.get("notes") or "")[:2000], "new", origin, stamp, stamp))
    return {"id": int(cursor.lastrowid), "duplicate": False}


def update_lead(c, organization_id: int, lead_id: int, data: dict[str, Any]) -> dict[str, Any]:
    row = c.execute("SELECT * FROM commercial_leads WHERE id=? AND organization_id=?", (lead_id, organization_id)).fetchone()
    if row is None:
        raise LookupError("الفرصة غير موجودة")
    status = data.get("status", row["status"])
    if status not in STATUSES:
        raise ValueError("حالة الفرصة غير صحيحة")
    follow = row["next_follow_up"]
    if status != row["status"]:
        # تواصلت أو أرسلت العرض: نذكّرك تتابع بعد 3 أيام إذا ما رد.
        follow = (_now() + timedelta(days=FOLLOW_UP_DAYS)).isoformat() if status in FOLLOW_UP_STATUSES else None
    if "followUpDays" in data:
        try:
            days = int(data.get("followUpDays"))
        except (TypeError, ValueError):
            raise ValueError("عدد أيام المتابعة غير صحيح")
        follow = (_now() + timedelta(days=max(1, min(days, 60)))).isoformat() if days > 0 else None
    notes = str(data.get("notes", row["notes"]) or "")[:2000]
    phone = _clean(data.get("phone", row["phone"]), 40)
    c.execute("UPDATE commercial_leads SET status=?,notes=?,phone=?,next_follow_up=?,updated_at=? WHERE id=?",
              (status, notes, phone, follow, _now().isoformat(), lead_id))
    return {"saved": True, "status": status, "next_follow_up": follow}


def delete_lead(c, organization_id: int, lead_id: int) -> bool:
    return c.execute("DELETE FROM commercial_leads WHERE id=? AND organization_id=?", (lead_id, organization_id)).rowcount > 0


def follow_up_reminders(c, now: datetime | None = None) -> int:
    now = now or _now()
    sent = 0
    for row in c.execute("""SELECT id,organization_id,name,status,next_follow_up FROM commercial_leads
        WHERE next_follow_up IS NOT NULL AND next_follow_up<=? AND status IN ('contacted','offer_sent')""", (now.isoformat(),)).fetchall():
        what = "أرسلت له العرض" if row["status"] == "offer_sent" else "تواصلت معه"
        _notify(c, row["organization_id"], "lead_followup", f"تابع مع «{row['name']}» 🔔",
                f"{what} من {FOLLOW_UP_DAYS} أيام وما تغيرت حالته. أرسل له رسالة متابعة من دفتر الفرص.", "leads",
                f"lead-follow:{row['id']}:{str(row['next_follow_up'])[:10]}")
        c.execute("UPDATE commercial_leads SET next_follow_up=NULL WHERE id=?", (row["id"],))
        sent += 1
    return sent


# ---------- ملف الجهة ----------
def save_profile(c, organization_id: int, lead_id: int, profile: str) -> None:
    c.execute("UPDATE commercial_leads SET profile=?,updated_at=? WHERE id=? AND organization_id=?",
              (str(profile or "")[:4000], _now().isoformat(), lead_id, organization_id))


def profile_prompt(lead: Any) -> str:
    return (f"جهّز نبذة قصيرة (5 أسطر بالكثير) عن الجهة «{lead['name']}»"
            + (f" في {lead['location']}" if lead["location"] else "")
            + (f"، نشاطها: {lead['activity']}" if lead["activity"] else "")
            + ". اذكر: وش يشتغلون، ومشاريعهم الحالية أو الأخيرة إن وُجدت، وموقعهم، وأفضل مدخل للتواصل معهم. "
            "اعتمد على مصادر علنية واذكر المصدر، وإذا ما لقيت معلومة قل غير متوفر ولا تخمن.")


# ---------- البحث الأسبوعي ----------
def get_watch(c, organization_id: int) -> dict[str, Any]:
    row = c.execute("SELECT * FROM commercial_watch WHERE organization_id=?", (organization_id,)).fetchone()
    if row is None:
        return {"enabled": False, "keywords": "", "city": "", "watch_type": "clients", "last_run_at": None, "last_result": ""}
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    return out


def save_watch(c, organization_id: int, data: dict[str, Any]) -> dict[str, Any]:
    enabled = data.get("enabled") is True
    keywords, city = _clean(data.get("keywords"), 300), _clean(data.get("city"), 120)
    kind = data.get("watch_type") if data.get("watch_type") in WATCH_TYPES else "clients"
    if enabled and (not keywords or not city):
        raise ValueError("اكتب وش تدور والمدينة")
    c.execute("DELETE FROM commercial_watch WHERE organization_id=?", (organization_id,))
    c.execute("INSERT INTO commercial_watch(organization_id,enabled,keywords,city,watch_type,updated_at) VALUES(?,?,?,?,?,?)",
              (organization_id, int(enabled), keywords, city, kind, _now().isoformat()))
    return get_watch(c, organization_id)


def due_watches(c, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or _now()
    limit = (now - timedelta(days=WATCH_DAYS)).isoformat()
    return [dict(r) for r in c.execute("""SELECT w.* FROM commercial_watch w JOIN subscriptions s ON s.organization_id=w.organization_id
        WHERE w.enabled=1 AND s.package='vip' AND (w.last_run_at IS NULL OR w.last_run_at<?) LIMIT 20""", (limit,)).fetchall()]


def watch_prompt(watch: dict[str, Any], known: list[str]) -> str:
    what = "مشاريع جديدة (بدأت أو أُعلنت حديثًا أو مناقصات منشورة)" if watch["watch_type"] == "projects" else "جهات ممكن تكون عملاء"
    skip = ("، وتجاهل هذي الجهات لأنها معروفة عندي: " + "، ".join(known[:60])) if known else ""
    return (f"ابحث في المصادر العلنية عن {what} لها علاقة بـ «{watch['keywords']}» في {watch['city']} بالسعودية{skip}. "
            "رجّع JSON فقط بدون أي كلام ثاني: مصفوفة حتى 8 عناصر، كل عنصر فيه name و activity و location و phone و website و source و reason. "
            "اكتب \"\" لأي معلومة ما لقيتها ولا تخترع أرقام.")


def parse_watch_results(text: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", str(text or "").strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(cleaned[start:end + 1])
    except ValueError:
        return []
    return [item for item in items if isinstance(item, dict) and _clean(item.get("name"), 160)][:8]


def run_watch(c, watch: dict[str, Any], search: Callable[[str], str]) -> int:
    """يشغّل بحث أسبوعي وحد ويضيف الجهات الجديدة للدفتر، ثم ينبّه صاحب المؤسسة."""
    org = watch["organization_id"]
    known = [r["name"] for r in c.execute("SELECT name FROM commercial_leads WHERE organization_id=? ORDER BY id DESC LIMIT 60", (org,)).fetchall()]
    stamp = _now().isoformat()
    try:
        results = parse_watch_results(search(watch_prompt(watch, known)))
    except Exception as error:
        c.execute("UPDATE commercial_watch SET last_run_at=?,last_result=? WHERE organization_id=?", (stamp, "تعذر البحث: " + type(error).__name__, org))
        return 0
    added = 0
    for item in results:
        details = _clean(item.get("reason"), 600)
        out = create_lead(c, org, {**item, "details": details}, origin="weekly_projects" if watch["watch_type"] == "projects" else "weekly")
        if not out["duplicate"]:
            added += 1
    c.execute("UPDATE commercial_watch SET last_run_at=?,last_result=? WHERE organization_id=?", (stamp, f"لقيت {added} جديدة", org))
    if added:
        label = "مشاريع جديدة" if watch["watch_type"] == "projects" else "فرص جديدة"
        _notify(c, org, "leads_found", f"لقيت لك {added} {label} 🔎",
                f"البحث الأسبوعي عن «{watch['keywords']}» في {watch['city']} أضاف {added} جهة لدفتر الفرص.", "leads")
    return added


# ---------- رابط عرض السعر الذكي ----------
def create_quote(c, organization_id: int, data: dict[str, Any], base_url: str) -> dict[str, Any]:
    items = []
    for raw in (data.get("items") or [])[:30]:
        if not isinstance(raw, dict):
            continue
        title = _clean(raw.get("title"), 200)
        try:
            qty, price = float(raw.get("qty") or 1), float(raw.get("price") or 0)
        except (TypeError, ValueError):
            raise ValueError("الكمية والسعر لازم أرقام")
        if not title or qty <= 0 or price < 0:
            continue
        items.append({"title": title, "qty": qty, "price": price})
    if not items:
        raise ValueError("أضف بند واحد على الأقل بسعره")
    lead_id = data.get("leadId")
    customer = _clean(data.get("customerName"), 160)
    if lead_id is not None:
        lead = c.execute("SELECT id,name FROM commercial_leads WHERE id=? AND organization_id=?", (int(lead_id), organization_id)).fetchone()
        if lead is None:
            raise LookupError("الفرصة غير موجودة")
        customer = customer or lead["name"]
        c.execute("UPDATE commercial_leads SET status='offer_sent',next_follow_up=?,updated_at=? WHERE id=?",
                  ((_now() + timedelta(days=FOLLOW_UP_DAYS)).isoformat(), _now().isoformat(), lead["id"]))
    try:
        days = max(1, min(int(data.get("validDays") or QUOTE_DAYS), 90))
    except (TypeError, ValueError):
        days = QUOTE_DAYS
    total = round(sum(i["qty"] * i["price"] for i in items), 2)
    token = secrets.token_urlsafe(18)
    stamp = _now()
    c.execute("""INSERT INTO smart_quotes(organization_id,lead_id,token,customer_name,items_json,total,notes,valid_until,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""", (organization_id, int(lead_id) if lead_id is not None else None, token, customer,
                                       json.dumps(items, ensure_ascii=False), total, str(data.get("notes") or "")[:1500],
                                       (stamp + timedelta(days=days)).isoformat(), stamp.isoformat()))
    return {"token": token, "url": base_url.rstrip("/") + "/q/" + token, "total": total, "validDays": days}


def list_quotes(c, organization_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute("""SELECT id,lead_id,token,customer_name,total,status,open_count,first_opened_at,response,responded_at,valid_until,created_at
        FROM smart_quotes WHERE organization_id=? ORDER BY id DESC LIMIT 100""", (organization_id,)).fetchall()]


def _quote(c, token: str):
    return c.execute("""SELECT q.*,o.name organization_name,o.phone organization_phone FROM smart_quotes q
        JOIN organizations o ON o.id=q.organization_id WHERE q.token=?""", (token,)).fetchone()


def open_quote(c, token: str) -> dict[str, Any] | None:
    quote = _quote(c, token)
    if quote is None:
        return None
    stamp = _now().isoformat()
    first = quote["first_opened_at"] is None
    c.execute("UPDATE smart_quotes SET open_count=open_count+1,first_opened_at=COALESCE(first_opened_at,?),last_opened_at=? WHERE id=?",
              (stamp, stamp, quote["id"]))
    if first and quote["status"] == "sent":
        _notify(c, quote["organization_id"], "quote", f"👀 «{quote['customer_name'] or 'العميل'}» فتح عرض السعر",
                "العميل فتح الرابط الحين. وقت مناسب تتابع معه.", "leads", f"quote-open:{quote['id']}")
    elif quote["open_count"] + 1 == 3 and quote["status"] == "sent":
        _notify(c, quote["organization_id"], "quote", f"«{quote['customer_name'] or 'العميل'}» فتح العرض 3 مرات 🔥",
                "شكله مهتم وما رد للحين. اتصل عليه أو أرسل له متابعة.", "leads", f"quote-hot:{quote['id']}")
    return dict(quote)


def respond_quote(c, token: str, action: Any, text: Any) -> dict[str, Any]:
    quote = _quote(c, token)
    if quote is None:
        raise LookupError("العرض غير موجود")
    if quote["status"] in ("accepted",):
        return {"saved": True, "status": quote["status"]}
    if str(quote["valid_until"]) < _now().isoformat():
        raise ValueError("انتهت صلاحية هذا العرض، تواصل مع المؤسسة")
    message = _clean(text, 800)
    name = quote["customer_name"] or "العميل"
    if action == "accept":
        c.execute("UPDATE smart_quotes SET status='accepted',response=?,responded_at=? WHERE id=?", (message, _now().isoformat(), quote["id"]))
        if quote["lead_id"]:
            c.execute("UPDATE commercial_leads SET status='won',next_follow_up=NULL,updated_at=? WHERE id=?", (_now().isoformat(), quote["lead_id"]))
        _notify(c, quote["organization_id"], "quote", f"✅ «{name}» وافق على عرض السعر", f"إجمالي العرض {quote['total']:g} ريال." + (f" ملاحظته: {message}" if message else ""), "leads")
        return {"saved": True, "status": "accepted"}
    if action == "question":
        if len(message) < 2:
            raise ValueError("اكتب سؤالك")
        c.execute("UPDATE smart_quotes SET status='question',response=?,responded_at=? WHERE id=?", (message, _now().isoformat(), quote["id"]))
        if quote["lead_id"]:
            c.execute("UPDATE commercial_leads SET status='replied',updated_at=? WHERE id=?", (_now().isoformat(), quote["lead_id"]))
        _notify(c, quote["organization_id"], "quote", f"❓ «{name}» عنده سؤال عن العرض", message, "leads")
        return {"saved": True, "status": "question"}
    raise ValueError("اختر موافقة أو سؤال")


def quote_page(quote: dict[str, Any] | None) -> str:
    style = """body{margin:0;background:#f4f6fa;color:#172436;font-family:Tahoma,Arial,sans-serif;line-height:1.8}
main{max-width:640px;margin:auto;padding:20px}.card{background:#fff;border:1px solid #dce3ed;border-radius:16px;padding:20px;margin-bottom:14px}
h1{font-size:22px;margin:0 0 4px;color:#0b3b66}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #eef1f7;text-align:right}
.total{font-size:20px;font-weight:bold;color:#15803d}button{width:100%;padding:14px;border:0;border-radius:12px;font:inherit;font-weight:bold;cursor:pointer;margin-top:8px}
.ok{background:#16a34a;color:#fff}.ask{background:#e0f2fe;color:#0b3b66}textarea{width:100%;box-sizing:border-box;min-height:80px;padding:10px;border-radius:10px;border:1px solid #b9c5d5;font:inherit}
.muted{color:#64748b;font-size:13px}#msg{font-weight:bold;color:#15803d}"""
    if quote is None:
        body = '<div class="card"><h1>العرض غير موجود</h1><p class="muted">ممكن الرابط غلط أو انلغى. تواصل مع المؤسسة.</p></div>'
        return f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>عرض سعر</title><style>{style}</style></head><body><main>{body}</main></body></html>'
    items = json.loads(quote["items_json"] or "[]")
    rows = "".join(f"<tr><td>{html.escape(i['title'])}</td><td>{i['qty']:g}</td><td>{i['price']:g}</td><td>{i['qty'] * i['price']:g}</td></tr>" for i in items)
    expired = str(quote["valid_until"]) < _now().isoformat()
    accepted = quote["status"] == "accepted"
    until = str(quote["valid_until"])[:10].replace("-", "/")
    notes = f'<p>{html.escape(quote["notes"])}</p>' if quote["notes"] else ""
    actions = ('<p id="msg">✅ وافقت على هذا العرض. المؤسسة بتتواصل معك.</p>' if accepted else
               '<p class="muted">انتهت صلاحية هذا العرض، تواصل مع المؤسسة.</p>' if expired else
               '<button class="ok" onclick="send(\'accept\')">✅ موافق على العرض</button>'
               '<textarea id="q" placeholder="عندك سؤال؟ اكتبه هنا"></textarea><button class="ask" onclick="send(\'question\')">❓ أرسل سؤالي</button><p id="msg"></p>')
    token = html.escape(quote["token"])
    script = ("<script>async function send(a){const t=(document.getElementById('q')||{}).value||'';"
              f"const r=await fetch('/q/{token}/respond',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:a,text:t}})}});"
              "const d=await r.json().catch(()=>({}));const m=document.getElementById('msg');"
              "if(!r.ok){m.style.color='#b91c1c';m.textContent=d.error||'تعذر الإرسال';return}"
              "m.style.color='#15803d';m.textContent=a==='accept'?'✅ تم، وصلت موافقتك للمؤسسة.':'✓ وصل سؤالك للمؤسسة، بيردون عليك قريب.';"
              "if(a==='accept')document.querySelectorAll('button,textarea').forEach(e=>e.remove())}</script>")
    body = (f'<div class="card"><h1>عرض سعر من {html.escape(quote["organization_name"])}</h1>'
            f'<p class="muted">إلى: {html.escape(quote["customer_name"] or "العميل")} · صالح لين {until}</p></div>'
            f'<div class="card"><table><tr><th>البند</th><th>الكمية</th><th>السعر</th><th>المجموع</th></tr>{rows}</table>'
            f'<p class="total">الإجمالي: {quote["total"]:g} ريال</p>{notes}</div>'
            f'<div class="card">{actions}</div><p class="muted" style="text-align:center">عبر خدوم</p>')
    return (f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>عرض سعر — {html.escape(quote["organization_name"])}</title><style>{style}</style></head><body><main>{body}</main>{script}</body></html>')


def sweep(c) -> None:
    follow_up_reminders(c)
