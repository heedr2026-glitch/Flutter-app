"""طلبات اعتماد رقم المكالمات: المشترك يطلب رقم هاتف مؤسسته، وإدارة المنصة تعتمده أو ترفضه.

رقم المكالمات مستقل عن رقم المشترك وعن رقم التواصل في بيانات المؤسسة. الاعتماد نفسه يبقى في
owner_admin.service_number_grants؛ هذا الملف يدير الطلب فقط.
"""
from __future__ import annotations

import re
from typing import Any

import owner_admin

SERVICE = "calls"
MAX_OPEN_LIST = 200


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS service_number_requests (
        id {identity}, organization_id BIGINT NOT NULL, service TEXT NOT NULL, phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, decided_by TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '')""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_service_number_requests_org ON service_number_requests(organization_id,service,status)")
    # نوع الرقم كما اختاره المشترك: ثابت أو جوال أو موحد.
    if postgres:
        c.execute("ALTER TABLE service_number_requests ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT ''")
    elif "kind" not in {row[1] for row in c.execute("PRAGMA table_info(service_number_requests)").fetchall()}:
        c.execute("ALTER TABLE service_number_requests ADD COLUMN kind TEXT NOT NULL DEFAULT ''")


KINDS = {"landline": "هاتف ثابت", "mobile": "جوال", "unified": "رقم موحد"}


def normalize(value: Any) -> str:
    """أرقام بصيغة دولية بلا + ولا 00. الصيغ السعودية المحلية تُحوَّل تلقائيًا:

    جوال 05xxxxxxxx، ثابت 01xxxxxxxx، موحد 9200xxxxx، مجاني 800xxxxxxx.
    """
    digits = owner_admin.service_phone(value)
    if len(digits) == 10 and digits.startswith(("05", "01")):
        return "966" + digits[1:]
    if len(digits) == 9 and digits.startswith("5"):
        return "966" + digits
    if (len(digits) == 9 and digits.startswith("9200")) or (len(digits) == 10 and digits.startswith("800")):
        return "966" + digits
    return digits


def kind_of(phone: str, chosen: Any = "") -> str:
    """نوع الرقم من صيغته؛ اختيار المشترك يُعتمد فقط إذا لم تدل الصيغة على نوع آخر."""
    if phone.startswith("9665"):
        return "mobile"
    if phone.startswith("9661"):
        return "landline"
    if phone.startswith(("9669200", "966800")):
        return "unified"
    return chosen if chosen in KINDS else ""


def state(c, organization_id: int) -> dict[str, Any]:
    """ما يراه المشترك: الرقم المعتمد، وآخر طلب له وحالته."""
    approved = owner_admin.granted_number(c, organization_id, SERVICE)
    row = c.execute("""SELECT phone,status,note FROM service_number_requests
        WHERE organization_id=? AND service=? AND status IN ('pending','approved','rejected') ORDER BY id DESC LIMIT 1""",
                    (int(organization_id), SERVICE)).fetchone()
    result = {"approvedPhone": approved, "requestedPhone": "", "requestStatus": "none", "requestNote": ""}
    if row and not (row["status"] == "approved" and row["phone"] != approved):
        result.update({"requestedPhone": row["phone"], "requestStatus": row["status"], "requestNote": row["note"]})
    if approved and result["requestStatus"] == "none":
        result.update({"requestedPhone": approved, "requestStatus": "approved"})
    return result


def _used_elsewhere(c, organization_id: int, phone: str) -> bool:
    if c.execute("SELECT 1 FROM service_number_grants WHERE service=? AND phone=? AND organization_id<>?", (SERVICE, phone, organization_id)).fetchone():
        return True
    return bool(c.execute("SELECT 1 FROM call_connections WHERE phone_number=? AND organization_id<>?", (phone, organization_id)).fetchone())


def submit(c, organization_id: int, requested_by: str, value: Any, error, now: str, kind: Any = "") -> dict[str, Any]:
    phone = normalize(value)
    if not re.fullmatch(r"[1-9][0-9]{7,14}", phone):
        raise error(400, "رقم غير صالح. اكتب رقم المؤسسة كاملًا، مثل 0112345678 للثابت أو 0542027855 للجوال أو 920012345 للموحد")
    if _used_elsewhere(c, organization_id, phone):
        raise error(409, "هذا الرقم مستخدم لمؤسسة أخرى في خدوم")
    if phone == owner_admin.granted_number(c, organization_id, SERVICE):
        return state(c, organization_id)
    c.execute("UPDATE service_number_requests SET status='replaced' WHERE organization_id=? AND service=? AND status='pending'", (organization_id, SERVICE))
    c.execute("INSERT INTO service_number_requests(organization_id,service,phone,status,requested_by,created_at,kind) VALUES(?,?,?,?,?,?,?)",
              (organization_id, SERVICE, phone, "pending", str(requested_by or "")[:120], now, kind_of(phone, kind)))
    return state(c, organization_id)


def open_requests(c) -> list[dict[str, Any]]:
    rows = c.execute("""SELECT r.id,r.organization_id,o.name AS organization_name,r.phone,r.requested_by,r.created_at,r.kind
        FROM service_number_requests r JOIN organizations o ON o.id=r.organization_id
        WHERE r.service=? AND r.status='pending' ORDER BY r.id ASC LIMIT ?""", (SERVICE, MAX_OPEN_LIST)).fetchall()
    result = []
    for row in rows:
        current = owner_admin.granted_number(c, row["organization_id"], SERVICE)
        result.append({"id": row["id"], "organizationId": row["organization_id"], "organizationName": row["organization_name"],
                       "phone": row["phone"], "kind": row["kind"] or "", "kindLabel": KINDS.get(row["kind"] or "", ""), "currentApprovedPhone": current, "requestedBy": row["requested_by"], "createdAt": row["created_at"]})
    return result


def decide(c, request_id: Any, approve: bool, actor_name: str, note: Any, error, now: str) -> dict[str, Any]:
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        raise error(404, "الطلب غير موجود")
    row = c.execute("SELECT id,organization_id,phone,status FROM service_number_requests WHERE id=? AND service=?", (request_id, SERVICE)).fetchone()
    if row is None:
        raise error(404, "الطلب غير موجود")
    if row["status"] != "pending":
        raise error(409, "هذا الطلب تم البت فيه أو استُبدل بطلب أحدث")
    if approve:
        try:
            owner_admin.save_service_numbers(c, row["organization_id"], {SERVICE: row["phone"]}, actor_name)
        except ValueError as problem:
            raise error(409, str(problem))
    c.execute("UPDATE service_number_requests SET status=?,note=?,decided_by=?,decided_at=? WHERE id=?",
              ("approved" if approve else "rejected", " ".join(str(note or "").split())[:300], str(actor_name)[:120], now, request_id))
    return {"id": request_id, "organizationId": row["organization_id"], "phone": row["phone"], "status": "approved" if approve else "rejected"}
