"""«الموظف يسألك»: إذا ما عرف الموظف الذكي جواب سؤال عميل، يسأل صاحب المؤسسة بدل ما يخمن.

صاحب المؤسسة يجاوب مرة وحدة من صفحة «تعليم الموظفين»: الجواب يوصل العميل في نفس المحادثة
(إذا كانت محادثة خارجية)، وينحفظ في التعليم عشان الموظفين يعرفونه المرة الجاية.
"""
from __future__ import annotations

from typing import Any

EMPLOYEE_NAMES = {
    "reception": "موظف المحادثة الخارجية", "chat": "موظف المحادثة الخارجية", "assistant": "اسألني",
    "whatsapp": "موظف الواتساب", "calls": "موظف المكالمات", "commercial_research": "موظف البحث التجاري",
}
SAVE_TYPES = {"shared", "assistant", "reception", "whatsapp", "calls", "commercial_research"}


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS ai_pending_questions (
        id {identity}, organization_id BIGINT NOT NULL, employee_type TEXT NOT NULL, session_id BIGINT,
        question TEXT NOT NULL, customer_question TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        answer TEXT NOT NULL DEFAULT '', saved_for TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, answered_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_pending_questions_org ON ai_pending_questions(organization_id,status,id)")


def ask(c, organization_id: int, employee_type: str, session_id: Any, question: str, customer_question: str, now: str) -> int:
    # نفس السؤال المعلق ما يتكرر (عميلين سألوا نفس الشي).
    same = c.execute("SELECT id FROM ai_pending_questions WHERE organization_id=? AND status='pending' AND question=? LIMIT 1",
                     (organization_id, question)).fetchone()
    if same:
        return int(same["id"])
    cursor = c.execute("""INSERT INTO ai_pending_questions(organization_id,employee_type,session_id,question,customer_question,created_at)
        VALUES(?,?,?,?,?,?)""", (organization_id, employee_type, session_id, question, str(customer_question or "")[:800], now))
    import app_notices
    app_notices.add(c, organization_id, "ai_question", f"❓ {EMPLOYEE_NAMES.get(employee_type, 'الموظف')} يسألك",
                    question, "ai_questions")
    return int(cursor.lastrowid)


def pending(c, organization_id: int) -> list[dict[str, Any]]:
    rows = c.execute("""SELECT id,employee_type,question,customer_question,created_at FROM ai_pending_questions
        WHERE organization_id=? AND status='pending' ORDER BY id DESC LIMIT 50""", (organization_id,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["employee_name"] = EMPLOYEE_NAMES.get(item["employee_type"], "الموظف")
        out.append(item)
    return out


def answer(c, organization_id: int, question_id: int, text: Any, save_for: Any, user_id: int, now: str, error, insert_message) -> dict[str, Any]:
    reply = " ".join(str(text or "").split())[:1500]
    if len(reply) < 2:
        raise error(400, "اكتب الجواب")
    row = c.execute("SELECT * FROM ai_pending_questions WHERE id=? AND organization_id=?", (question_id, organization_id)).fetchone()
    if row is None:
        raise error(404, "السؤال غير موجود")
    if row["status"] != "pending":
        raise error(409, "هذا السؤال انجاوب من قبل")
    target = str(save_for or "shared")
    if target not in SAVE_TYPES:
        target = "shared"
    c.execute("""INSERT INTO ai_training_instructions(organization_id,employee_type,content,status,created_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)""", (organization_id, target, f"سؤال: {row['question']}\nالجواب: {reply}", "approved", user_id, now, now))
    c.execute("UPDATE ai_pending_questions SET status='answered',answer=?,saved_for=?,answered_at=? WHERE id=?",
              (reply, target, now, question_id))
    delivered = False
    # محادثة خارجية: الجواب يوصل العميل في نفس المحادثة كرد من المؤسسة.
    if row["session_id"]:
        session = c.execute("SELECT id FROM chat_sessions WHERE id=? AND organization_id=?", (row["session_id"], organization_id)).fetchone()
        if session:
            insert_message(c, organization_id, row["session_id"], "human", reply, now)
            delivered = True
    return {"saved": True, "savedFor": target, "deliveredToCustomer": delivered}
