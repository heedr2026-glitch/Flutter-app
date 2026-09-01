"""Khdoom API server. Standard-library only: Python 3 + SQLite."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import re
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KHDOOM_DB", ROOT / "khdoom.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "" ).strip()
HOST = os.environ.get("KHDOOM_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("KHDOOM_PORT", "8080")))
OWNER_KEY_PATH = ROOT / "owner.key"
AI_URL = os.environ.get("KHDOOM_AI_URL", "https://api.openai.com/v1/responses")
AI_MODEL = os.environ.get("KHDOOM_AI_MODEL", "gpt-4.1-mini")
try:
    AI_ESTIMATED_COST_PER_REQUEST_USD = max(
        0.0, float(os.environ.get("KHDOOM_AI_ESTIMATED_COST_PER_REQUEST_USD", "0.002"))
    )
except ValueError:
    AI_ESTIMATED_COST_PER_REQUEST_USD = 0.002


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_arabic_datetime(value: datetime) -> str:
    local_value = value.astimezone(timezone(timedelta(hours=3)))
    hour = local_value.hour % 12 or 12
    period = "ص" if local_value.hour < 12 else "م"
    return f"{local_value:%Y-%m-%d} الساعة {hour}:{local_value:%M} {period}"


class PostgresCursor:
    def __init__(self, cursor: Any, lastrowid: int | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class PostgresConnection:
    _id_tables = {"organizations", "users", "vehicles", "advertisements", "activation_codes", "ai_usage", "subscription_requests", "appointment_requests", "chat_sessions", "chat_messages"}

    def __init__(self, connection: Any):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    def execute(self, sql: str, params: tuple | list = ()) -> PostgresCursor:
        statement = sql.replace("?", "%s").replace(" COLLATE NOCASE", "")
        match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)", statement, re.IGNORECASE)
        needs_id = bool(match and match.group(1).lower() in self._id_tables and " RETURNING " not in statement.upper())
        if needs_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"
        cursor = self._connection.execute(statement, params)
        lastrowid = None
        if needs_id:
            row = cursor.fetchone()
            lastrowid = int(row["id"])
        return PostgresCursor(cursor, lastrowid)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._connection.execute(statement)

    def commit(self) -> None:
        self._connection.commit()


def db():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is configured")
        return PostgresConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
if psycopg is not None:
    DB_INTEGRITY_ERRORS = DB_INTEGRITY_ERRORS + (psycopg.IntegrityError,)


def generate_ai_text(
    system_prompt: str, user_prompt: str, *, web_search: bool = False
) -> str:
    api_key = os.environ.get("KHDOOM_AI_API_KEY", "").strip()
    if not api_key:
        raise ApiError(503, "خدمة AI لم تُفعّل في إعدادات الخادم بعد")
    request_data = {
        "model": AI_MODEL,
        "instructions": system_prompt,
        "input": user_prompt,
        "max_output_tokens": 1200,
        "store": False,
    }
    if web_search:
        request_data["tools"] = [{"type": "web_search_preview"}]
    payload = json.dumps(request_data, ensure_ascii=False).encode("utf-8")
    request = Request(
        AI_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        print(f"AI HTTP ERROR: {error.code}")
        raise ApiError(502, "تعذر إنشاء الرد بالذكاء الاصطناعي الآن")
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"AI CONNECTION ERROR: {error}")
        raise ApiError(502, "تعذر الاتصال بخدمة الذكاء الاصطناعي")
    text = str(result.get("output_text", "")).strip()
    if not text:
        parts = []
        for item in result.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    parts.append(str(content.get("text", "")))
        text = "\n".join(parts).strip()
    if not text:
        raise ApiError(502, "وصل رد فارغ من خدمة الذكاء الاصطناعي")
    return text


def ai_allowance(
    connection: Any, organization_id: int
) -> tuple[str, int, int]:
    package_row = connection.execute(
        "SELECT package FROM subscriptions WHERE organization_id=?",
        (organization_id,),
    ).fetchone()
    package = package_row["package"] if package_row else "free"
    default_limit = {"free": 5, "basic": 30, "vip": 100}.get(package, 5)
    custom_limit = connection.execute("SELECT daily_limit FROM ai_limits WHERE organization_id=?", (organization_id,)).fetchone()
    daily_limit = custom_limit["daily_limit"] if custom_limit else default_limit
    day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    used = connection.execute(
        "SELECT COUNT(*) AS count FROM ai_usage WHERE organization_id=? AND created_at>=?",
        (organization_id, day_start),
    ).fetchone()["count"]
    if used >= daily_limit:
        raise ApiError(429, "تم بلوغ الحد اليومي لموظفي AI")
    return package, daily_limit, used

def ai_training_text(connection: Any, organization_id: int, employee_type: str) -> str:
    rows = connection.execute(
        "SELECT employee_type,content FROM ai_training WHERE organization_id=? AND employee_type IN (?,?)",
        (organization_id, "shared", employee_type),
    ).fetchall()
    unique_lines: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for line in str(row["content"]).splitlines():
            cleaned = line.strip()
            key = re.sub(r"^[•\-]\s*", "", cleaned).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique_lines.append(cleaned)
    return "\n".join(unique_lines)

def _next_arabic_weekday(text: str) -> datetime | None:
    weekdays = {"الاثنين": 0, "الثلاثاء": 1, "الأربعاء": 2, "الاربعاء": 2, "الخميس": 3, "الجمعة": 4, "السبت": 5, "الأحد": 6, "الاحد": 6}
    base = datetime.now(timezone(timedelta(hours=3)))
    target = next((value for name, value in weekdays.items() if name in text), None)
    if target is None:
        return None
    days = (target - base.weekday()) % 7
    if days == 0:
        days = 7
    hour = 9 if "صباح" in text else 18 if any(word in text for word in ("مساء", "عصر")) else 17
    match = re.search(r"(?:الساعة\s*)?(\d{1,2})(?::(\d{2}))?", text)
    if match:
        candidate = int(match.group(1))
        if 0 <= candidate <= 23:
            hour = candidate
            if any(word in text for word in ("مساء", "العصر")) and hour < 12:
                hour += 12
    minute = int(match.group(2)) if match and match.group(2) else 0
    result = (base + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return result


def _appointment_period(text: str) -> str | None:
    if any(word in text for word in ("صباح", "الصباح")):
        return "صباحًا"
    if any(word in text for word in ("مساء", "المساء", "عصر", "العصر")):
        return "مساءً"
    return None


def _format_appointment_slot(value: datetime, period: str) -> str:
    local_value = value.astimezone(timezone(timedelta(hours=3)))
    return f"{local_value:%Y-%m-%d} {period}"

def _appointment_chat_reply(connection: Any, organization_id: int, session: Any, message: str) -> str | None:
    state = session["state"]
    try:
        context = json.loads(session["context_json"] or "{}")
    except json.JSONDecodeError:
        context = {}
    lowered = message.strip().lower()
    booking_intent = any(
        phrase in lowered
        for phrase in (
            "موعد", "حجز", "احجز", "زيارة", "صيانة", "مقاس", "قياس",
            "كشف", "معاينة", "تركيب", "موظف يجي", "نبيكم تجون", "أبيكم تجون",
        )
    )
    if state == "closed":
        state = "idle"
        context = {}
    cancel_booking = any(
        phrase in lowered
        for phrase in ("إلغاء الحجز", "الغاء الحجز", "ألغي الحجز", "الغي الحجز", "إلغاء الموعد", "الغاء الموعد")
    )
    active_booking_states = {"await_name", "await_phone", "await_type", "await_datetime", "await_confirmation"}
    if cancel_booking and state in active_booking_states:
        connection.execute(
            "UPDATE chat_sessions SET state='idle',context_json='{}',updated_at=? WHERE id=?",
            (now(), session["id"]),
        )
        return "تم إلغاء الحجز غير المكتمل. كيف أقدر أخدمك؟"
    greeting = lowered.strip(" .،!؟") in {"السلام", "السلام عليكم", "هلا", "مرحبا", "مرحبًا", "صباح الخير", "مساء الخير"}
    if greeting and state in active_booking_states:
        return "عندك طلب غير مكتمل. اكتب «نكمل» للمتابعة أو «إلغاء الحجز» لإلغائه."
    if lowered.strip() == "نكمل" and state in active_booking_states:
        prompts = {
            "await_name": "ما اسمك الكامل لتسجيل طلب الموعد؟",
            "await_phone": "اكتب رقم التواصل من فضلك.",
            "await_type": "ما نوع الطلب: موعد مقاس، موعد صيانة، أم طلب عميل؟",
            "await_datetime": "اكتب اليوم والفترة المناسبة، مثل: السبت صباحًا أو السبت مساءً.",
            "await_confirmation": "هل أرسل طلب الموعد للموظف؟ اكتب نعم أو لا.",
        }
        return prompts[state]
    if state == "waiting_human":
        return "طلبك أُرسل للموظف البشري، وسأكتب لك هنا فور قبوله أو تعديله أو رفضه."
    if state == "idle":
        if not booking_intent:
            return None
        period = _appointment_period(lowered)
        desired = _next_arabic_weekday(lowered) if period else None
        context = {"original_request": message}
        if period:
            context["time_period"] = period
        if "صيانة" in lowered:
            context["request_type"] = "موعد صيانة"
        elif any(word in lowered for word in ("مقاس", "قياس", "معاينة")):
            context["request_type"] = "موعد مقاس"
        if desired:
            context["scheduled_at"] = desired.isoformat()
        state = "await_name"
        reply = "أكيد. ما اسمك الكامل لتسجيل طلب الموعد؟"
    elif state == "await_name":
        context["customer_name"] = message.strip()[:120]
        state = "await_phone"
        reply = "شكرًا. اكتب رقم التواصل من فضلك."
    elif state == "await_phone":
        phone = re.sub(r"[^0-9+]", "", message)
        if len(phone) < 6:
            return "رقم التواصل غير واضح. اكتبه بالأرقام من فضلك."
        context["phone"] = phone[:40]
        if context.get("request_type"):
            if context.get("scheduled_at"):
                state = "await_confirmation"
                scheduled = datetime.fromisoformat(context["scheduled_at"])
                reply = f"سأسجل {context['request_type']} يوم {_format_appointment_slot(scheduled, context.get("time_period") or ("صباحًا" if scheduled.hour < 12 else "مساءً"))}. هل أرسل الطلب للموظف؟ اكتب نعم أو لا."
            else:
                state = "await_datetime"
                reply = "اكتب اليوم والفترة المناسبة، مثل: السبت صباحًا أو السبت مساءً."
        else:
            state = "await_type"
            reply = "ما نوع الطلب: موعد مقاس، موعد صيانة، أم طلب عميل؟"
    elif state == "await_type":
        request_type = "موعد مقاس" if "مقاس" in lowered else "موعد صيانة" if "صيانة" in lowered else "طلب عميل"
        context["request_type"] = request_type
        if context.get("scheduled_at"):
            state = "await_confirmation"
            scheduled = datetime.fromisoformat(context["scheduled_at"])
            reply = f"سأسجل {request_type} يوم {_format_appointment_slot(scheduled, context.get("time_period") or ("صباحًا" if scheduled.hour < 12 else "مساءً"))}. هل أرسل الطلب للموظف؟ اكتب نعم أو لا."
        else:
            state = "await_datetime"
            reply = "اكتب اليوم والفترة المناسبة، مثل: السبت صباحًا أو السبت مساءً."
    elif state == "await_datetime":
        period = _appointment_period(lowered)
        desired = _next_arabic_weekday(lowered) if period else None
        if desired is None:
            return "لم أفهم الفترة. اكتب اليوم وحدد صباحًا أو مساءً، مثل: السبت صباحًا."
        context["scheduled_at"] = desired.isoformat()
        context["time_period"] = period
        state = "await_confirmation"
        reply = f"الموعد المقترح يوم {_format_appointment_slot(desired, period)}. هل أرسله للموظف؟ اكتب نعم أو لا."
    elif state == "await_confirmation":
        if any(word in lowered for word in ("لا", "غير", "غيّر", "غيره")):
            state = "await_datetime"
            reply = "حسنًا، اكتب يومًا ووقتًا آخر مناسبًا."
        elif any(word in lowered for word in ("نعم", "اي", "أيوه", "موافق", "تمام")):
            scheduled_at = context.get("scheduled_at")
            if not scheduled_at:
                state = "await_datetime"
                reply = "اكتب اليوم وحدد صباحًا أو مساءً أولًا."
            else:
                cursor = connection.execute(
                    """INSERT INTO appointment_requests(
                           organization_id,chat_session_id,request_type,title,customer_name,phone,notes,
                           scheduled_at,status,source,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (organization_id, session["id"], context.get("request_type", "طلب عميل"),
                     context.get("original_request", "طلب موعد")[:160], context.get("customer_name", ""),
                     context.get("phone", ""), context.get("original_request", "")[:1000],
                     scheduled_at, "pending", "public_chat", now(), now()),
                )
                context["appointment_id"] = cursor.lastrowid
                state = "waiting_human"
                reply = "تم إرسال طلب الموعد للموظف البشري ✓ سأبلغك هنا عند القبول أو التعديل أو الرفض."
        else:
            return "للتأكيد اكتب «نعم»، ولتغيير الموعد اكتب «لا»."
    else:
        state = "idle"
        context = {}
        reply = "كيف أقدر أخدمك؟"
    connection.execute(
        "UPDATE chat_sessions SET state=?,context_json=?,updated_at=? WHERE id=?",
        (state, json.dumps(context, ensure_ascii=False), now(), session["id"]),
    )
    return reply

def init_db() -> None:
    if DATABASE_URL:
        postgres_schema = """
        CREATE TABLE IF NOT EXISTS organizations (
          id BIGSERIAL PRIMARY KEY,
          name TEXT NOT NULL,
          activity TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '',
          public_chat_token TEXT UNIQUE,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          username TEXT NOT NULL UNIQUE,
          phone TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL DEFAULT '',
          password_hash TEXT NOT NULL,
          password_salt TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','employee')),
          job_title TEXT NOT NULL DEFAULT 'موظف',
          permissions TEXT NOT NULL DEFAULT '{}',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY,
          user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device_id TEXT NOT NULL DEFAULT '',
          device_name TEXT NOT NULL DEFAULT 'جهاز غير معروف',
          trusted INTEGER NOT NULL DEFAULT 0,
          last_seen_at TEXT NOT NULL DEFAULT '',
          expires_at TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blocked_devices (
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          device_id TEXT NOT NULL,
          device_name TEXT NOT NULL DEFAULT 'جهاز غير معروف',
          blocked_at TEXT NOT NULL,
          PRIMARY KEY(organization_id,device_id)
        );        CREATE TABLE IF NOT EXISTS vehicles (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          plate TEXT NOT NULL DEFAULT '',
          registration_expiry TEXT NOT NULL,
          inspection_expiry TEXT NOT NULL,
          insurance_expiry TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
          organization_id BIGINT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
          package TEXT NOT NULL DEFAULT 'free' CHECK(package IN ('free','basic','vip')),
          starts_at TEXT NOT NULL,
          expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS subscription_requests (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          requested_package TEXT NOT NULL CHECK(requested_package IN ('basic','vip')),
          discount_code TEXT NOT NULL DEFAULT '',
          discount_percent INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
          created_at TEXT NOT NULL,
          processed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_sessions (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          public_token TEXT NOT NULL UNIQUE,
          state TEXT NOT NULL DEFAULT 'idle',
          context_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
          id BIGSERIAL PRIMARY KEY,
          session_id BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
          sender TEXT NOT NULL CHECK(sender IN ('customer','bot','human')),
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS appointment_requests (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          chat_session_id BIGINT REFERENCES chat_sessions(id) ON DELETE SET NULL,
          request_type TEXT NOT NULL DEFAULT 'طلب عميل',
          title TEXT NOT NULL,
          customer_name TEXT NOT NULL,
          phone TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '',
          scheduled_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','completed','rejected')),
          source TEXT NOT NULL DEFAULT 'public_chat',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advertisements (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          title TEXT NOT NULL,
          message TEXT NOT NULL DEFAULT '',
          contact TEXT NOT NULL DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          approved INTEGER NOT NULL DEFAULT 0,
          approved_at TEXT,
          expires_at TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activation_codes (
          id BIGSERIAL PRIMARY KEY,
          code_hash TEXT NOT NULL UNIQUE,
          code_prefix TEXT NOT NULL,
          package TEXT NOT NULL CHECK(package IN ('basic','vip')),
          duration_days INTEGER NOT NULL,
          max_uses INTEGER NOT NULL DEFAULT 1,
          used_count INTEGER NOT NULL DEFAULT 0,
          expires_at TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          recipient_name TEXT NOT NULL DEFAULT '',
          assigned_username TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_usage (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          employee_type TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_training (
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          employee_type TEXT NOT NULL,
          content TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          PRIMARY KEY(organization_id, employee_type)
        );
        CREATE TABLE IF NOT EXISTS ai_limits (
          organization_id BIGINT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
          daily_limit INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS service_maintenance (
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          service TEXT NOT NULL,
          expires_at TEXT,
          message TEXT NOT NULL DEFAULT 'الخدمة متوقفة مؤقتًا',
          PRIMARY KEY(organization_id,service)
        );
        CREATE TABLE IF NOT EXISTS maintenance_modes (
          organization_id BIGINT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
          chat_until TEXT,
          appointments_until TEXT,
          message TEXT NOT NULL DEFAULT 'الخدمة تحت الصيانة مؤقتًا'
        );
        CREATE TABLE IF NOT EXISTS global_maintenance (
          service TEXT PRIMARY KEY,
          expires_at TEXT,
          message TEXT NOT NULL DEFAULT 'الخدمة متوقفة مؤقتًا'
        );        CREATE TABLE IF NOT EXISTS audit_logs (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
          action TEXT NOT NULL,
          target_type TEXT NOT NULL DEFAULT '',
          target_id TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_logs_org_date ON audit_logs(organization_id,created_at);
        CREATE TABLE IF NOT EXISTS ai_training_messages (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          employee_type TEXT NOT NULL,
          sender TEXT NOT NULL CHECK(sender IN ('owner','assistant')),
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id);
        CREATE INDEX IF NOT EXISTS idx_vehicles_org ON vehicles(organization_id);
        CREATE INDEX IF NOT EXISTS idx_ai_usage_org_date ON ai_usage(organization_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_ads_active ON advertisements(active,approved);
        """
        with db() as connection:
            connection.executescript(postgres_schema)
            connection.execute("ALTER TABLE appointment_requests ADD COLUMN IF NOT EXISTS chat_session_id BIGINT")
            connection.execute("ALTER TABLE advertisements ADD COLUMN IF NOT EXISTS expires_at TEXT")
            connection.execute("ALTER TABLE activation_codes ADD COLUMN IF NOT EXISTS code_kind TEXT NOT NULL DEFAULT 'activation'")
            connection.execute("ALTER TABLE activation_codes ADD COLUMN IF NOT EXISTS discount_percent INTEGER NOT NULL DEFAULT 0")
            connection.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_id TEXT NOT NULL DEFAULT ''")
            connection.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_name TEXT NOT NULL DEFAULT 'جهاز غير معروف'")
            connection.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS trusted INTEGER NOT NULL DEFAULT 0")
            connection.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_seen_at TEXT NOT NULL DEFAULT ''")
            organizations_without_chat = connection.execute(
                "SELECT id FROM organizations WHERE public_chat_token IS NULL OR public_chat_token=''"
            ).fetchall()
            for organization in organizations_without_chat:
                connection.execute(
                    "UPDATE organizations SET public_chat_token=? WHERE id=?",
                    (secrets.token_urlsafe(18), organization["id"]),
                )
        if not os.environ.get("KHDOOM_OWNER_KEY") and not OWNER_KEY_PATH.exists():
            OWNER_KEY_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        return
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              activity TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              phone TEXT NOT NULL DEFAULT '',
              email TEXT NOT NULL DEFAULT '',
              password_hash TEXT NOT NULL,
              password_salt TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('admin','employee')),
              permissions TEXT NOT NULL DEFAULT '{}',
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              device_id TEXT NOT NULL DEFAULT '',
              device_name TEXT NOT NULL DEFAULT 'جهاز غير معروف',
              trusted INTEGER NOT NULL DEFAULT 0,
              last_seen_at TEXT NOT NULL DEFAULT '',
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS blocked_devices (
          organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          device_id TEXT NOT NULL,
          device_name TEXT NOT NULL DEFAULT 'جهاز غير معروف',
          blocked_at TEXT NOT NULL,
          PRIMARY KEY(organization_id,device_id)
        );        CREATE TABLE IF NOT EXISTS vehicles (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              plate TEXT NOT NULL DEFAULT '',
              registration_expiry TEXT NOT NULL,
              inspection_expiry TEXT NOT NULL,
              insurance_expiry TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
              organization_id INTEGER PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
              package TEXT NOT NULL DEFAULT 'free' CHECK(package IN ('free','basic','vip')),
              starts_at TEXT NOT NULL,
              expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS subscription_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              requested_package TEXT NOT NULL CHECK(requested_package IN ('basic','vip')),
              discount_code TEXT NOT NULL DEFAULT '',
              discount_percent INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
              created_at TEXT NOT NULL,
              processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              public_token TEXT NOT NULL UNIQUE,
              state TEXT NOT NULL DEFAULT 'idle',
              context_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
              sender TEXT NOT NULL CHECK(sender IN ('customer','bot','human')),
              message TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS appointment_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              chat_session_id INTEGER REFERENCES chat_sessions(id) ON DELETE SET NULL,
              request_type TEXT NOT NULL DEFAULT 'طلب عميل',
              title TEXT NOT NULL,
              customer_name TEXT NOT NULL,
              phone TEXT NOT NULL,
              notes TEXT NOT NULL DEFAULT '',
              scheduled_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','completed','rejected')),
              source TEXT NOT NULL DEFAULT 'public_chat',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS advertisements (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              message TEXT NOT NULL DEFAULT '',
              contact TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              approved INTEGER NOT NULL DEFAULT 0,
              expires_at TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activation_codes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code_hash TEXT NOT NULL UNIQUE,
              code_prefix TEXT NOT NULL,
              package TEXT NOT NULL CHECK(package IN ('basic','vip')),
              duration_days INTEGER NOT NULL,
              max_uses INTEGER NOT NULL DEFAULT 1,
              used_count INTEGER NOT NULL DEFAULT 0,
              expires_at TEXT,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id);
            CREATE INDEX IF NOT EXISTS idx_vehicles_org ON vehicles(organization_id);
            CREATE TABLE IF NOT EXISTS ai_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              employee_type TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_training (
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              employee_type TEXT NOT NULL,
              content TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              PRIMARY KEY(organization_id, employee_type)
            );
            CREATE TABLE IF NOT EXISTS ai_limits (
              organization_id INTEGER PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
              daily_limit INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_maintenance (
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              service TEXT NOT NULL,
              expires_at TEXT,
              message TEXT NOT NULL DEFAULT 'الخدمة متوقفة مؤقتًا',
              PRIMARY KEY(organization_id,service)
            );
            CREATE TABLE IF NOT EXISTS maintenance_modes (
              organization_id INTEGER PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
              chat_until TEXT,
              appointments_until TEXT,
              message TEXT NOT NULL DEFAULT 'الخدمة تحت الصيانة مؤقتًا'
            );
            CREATE TABLE IF NOT EXISTS global_maintenance (
          service TEXT PRIMARY KEY,
          expires_at TEXT,
          message TEXT NOT NULL DEFAULT 'الخدمة متوقفة مؤقتًا'
        );        CREATE TABLE IF NOT EXISTS audit_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              action TEXT NOT NULL,
              target_type TEXT NOT NULL DEFAULT '',
              target_id TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_logs_org_date ON audit_logs(organization_id,created_at);
            CREATE TABLE IF NOT EXISTS ai_training_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              employee_type TEXT NOT NULL,
              sender TEXT NOT NULL CHECK(sender IN ('owner','assistant')),
              message TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_usage_org_date ON ai_usage(organization_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_ads_active ON advertisements(active, approved);
            """
        )
        user_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)")
        }
        if "job_title" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN job_title TEXT NOT NULL DEFAULT 'موظف'"
            )
        session_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(sessions)")
        }
        if "device_id" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN device_id TEXT NOT NULL DEFAULT ''")
        if "device_name" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN device_name TEXT NOT NULL DEFAULT 'جهاز غير معروف'")
        if "trusted" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN trusted INTEGER NOT NULL DEFAULT 0")
        if "last_seen_at" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN last_seen_at TEXT NOT NULL DEFAULT ''")
        organization_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(organizations)")
        }
        if "public_chat_token" not in organization_columns:
            connection.execute("ALTER TABLE organizations ADD COLUMN public_chat_token TEXT")
        organizations_without_chat = connection.execute(
            "SELECT id FROM organizations WHERE public_chat_token IS NULL OR public_chat_token=''"
        ).fetchall()
        for organization in organizations_without_chat:
            connection.execute(
                "UPDATE organizations SET public_chat_token=? WHERE id=?",
                (secrets.token_urlsafe(18), organization["id"]),
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_public_chat_token ON organizations(public_chat_token)"
        )
        appointment_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(appointment_requests)")
        }
        if "chat_session_id" not in appointment_columns:
            connection.execute("ALTER TABLE appointment_requests ADD COLUMN chat_session_id INTEGER")
        ad_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(advertisements)")
        }
        if "approved_at" not in ad_columns:
            connection.execute("ALTER TABLE advertisements ADD COLUMN approved_at TEXT")
        if "expires_at" not in ad_columns:
            connection.execute("ALTER TABLE advertisements ADD COLUMN expires_at TEXT")
        connection.execute(
            "UPDATE advertisements SET approved_at=? WHERE approved=1 AND approved_at IS NULL",
            (now(),),
        )
        code_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(activation_codes)")
        }
        if "recipient_name" not in code_columns:
            connection.execute(
                "ALTER TABLE activation_codes ADD COLUMN recipient_name TEXT NOT NULL DEFAULT ''"
            )
        if "assigned_username" not in code_columns:
            connection.execute(
                "ALTER TABLE activation_codes ADD COLUMN assigned_username TEXT NOT NULL DEFAULT ''"
            )
        if "code_kind" not in code_columns:
            connection.execute(
                "ALTER TABLE activation_codes ADD COLUMN code_kind TEXT NOT NULL DEFAULT 'activation'"
            )
        if "discount_percent" not in code_columns:
            connection.execute(
                "ALTER TABLE activation_codes ADD COLUMN discount_percent INTEGER NOT NULL DEFAULT 0"
            )
    if not os.environ.get("KHDOOM_OWNER_KEY") and not OWNER_KEY_PATH.exists():
        OWNER_KEY_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")


def _maintenance_active(value: object) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)
    except ValueError:
        return False


def global_maintenance_status(connection: Any, service: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT expires_at,message FROM global_maintenance WHERE service=?",
        (service,),
    ).fetchone()
    return {
        "active": _maintenance_active(row["expires_at"]) if row else False,
        "until": row["expires_at"] if row else None,
        "message": row["message"] if row else "الخدمة متوقفة مؤقتًا",
    }


def maintenance_status(connection: Any, organization_id: int) -> dict[str, Any]:
    row = connection.execute("SELECT chat_until,appointments_until,message FROM maintenance_modes WHERE organization_id=?", (organization_id,)).fetchone()
    global_chat = global_maintenance_status(connection, "chat")
    global_appointments = global_maintenance_status(connection, "appointments")
    chat_active = global_chat["active"] or (_maintenance_active(row["chat_until"]) if row else False)
    appointments_active = global_appointments["active"] or (_maintenance_active(row["appointments_until"]) if row else False)
    message = global_chat["message"] if global_chat["active"] else global_appointments["message"] if global_appointments["active"] else row["message"] if row else "الخدمة تحت الصيانة مؤقتًا"
    return {"chat": chat_active, "appointments": appointments_active, "chatUntil": global_chat["until"] if global_chat["active"] else row["chat_until"] if row else None, "appointmentsUntil": global_appointments["until"] if global_appointments["active"] else row["appointments_until"] if row else None, "message": message}


def service_maintenance_status(connection: Any, organization_id: int, service: str) -> dict[str, Any]:
    global_status = global_maintenance_status(connection, service)
    if global_status["active"]:
        return global_status
    row = connection.execute(
        "SELECT expires_at,message FROM service_maintenance WHERE organization_id=? AND service=?",
        (organization_id, service),
    ).fetchone()
    return {
        "active": _maintenance_active(row["expires_at"]) if row else False,
        "until": row["expires_at"] if row else None,
        "message": row["message"] if row else "الخدمة متوقفة مؤقتًا",
    }

def purge_expired_ads(connection: Any) -> int:
    """Deactivate advertisements after the owner-selected end time."""
    cursor = connection.execute(
        """UPDATE advertisements SET active=0
           WHERE approved=1 AND active=1 AND expires_at IS NOT NULL AND expires_at<=?""",
        (now(),),
    )
    return cursor.rowcount

def downgrade_expired_subscriptions(connection: Any) -> int:
    """Return expired paid subscriptions to the free package."""
    cursor = connection.execute(
        """UPDATE subscriptions
           SET package='free', starts_at=?, expires_at=NULL
           WHERE package IN ('basic','vip')
             AND expires_at IS NOT NULL
             AND expires_at<=?""",
        (now(), now()),
    )
    return cursor.rowcount

def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return digest.hex(), salt.hex()


def verify_password(password: str, expected: str, salt: str) -> bool:
    actual, _ = hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def issue_token(
    connection: Any,
    user_id: int,
    device_id: str = "",
    device_name: str = "جهاز غير معروف",
) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    connection.execute(
        """INSERT INTO sessions(
               token_hash,user_id,device_id,device_name,trusted,last_seen_at,
               expires_at,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (token_hash, user_id, device_id[:160], device_name[:160], 0, now(), expires, now()),
    )
    return token


def audit_log(
    connection: Any,
    organization_id: int,
    actor_user_id: int | None,
    action: str,
    summary: str,
    target_type: str = "",
    target_id: object = "",
) -> None:
    connection.execute(
        """INSERT INTO audit_logs(
               organization_id,actor_user_id,action,target_type,target_id,summary,created_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (organization_id, actor_user_id, action, target_type, str(target_id), summary[:500], now()),
    )

def require_permission(user: Any, *permission_names: str) -> None:
    if user["role"] == "admin":
        return
    try:
        permissions = json.loads(user["permissions"] or "{}")
    except (TypeError, json.JSONDecodeError):
        permissions = {}
    if any(permissions.get(name) is True for name in permission_names):
        return
    raise ApiError(403, "ليس لديك صلاحية لتنفيذ هذه العملية")

class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "KhdoomAPI/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{now()}] {self.address_string()} {fmt % args}")

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
            if "chunked" in transfer_encoding:
                chunks = bytearray()
                while True:
                    size_line = self.rfile.readline().strip().split(b";", 1)[0]
                    if not size_line:
                        continue
                    size = int(size_line, 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    if len(chunks) + size > 1_048_576:
                        raise ApiError(413, "حجم الطلب أكبر من المسموح")
                    chunks.extend(self.rfile.read(size))
                    self.rfile.read(2)
                raw = bytes(chunks)
            else:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1_048_576:
                    raise ApiError(413, "حجم الطلب أكبر من المسموح")
                raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw or b"{}")
        except ApiError:
            raise
        except (ValueError, json.JSONDecodeError):
            raise ApiError(400, "بيانات الطلب غير صحيحة")
    def _user(self, connection: Any) -> Any:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise ApiError(401, "يلزم تسجيل الدخول")
        token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
        row = connection.execute(
            """SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id
               WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.active=1""",
            (token_hash, now()),
        ).fetchone()
        if row is None:
            raise ApiError(401, "جلسة الدخول منتهية")
        connection.execute(
            "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
            (now(), token_hash),
        )
        return row

    def _owner(self) -> None:
        expected = os.environ.get("KHDOOM_OWNER_KEY", "").strip()
        if not expected:
            expected = OWNER_KEY_PATH.read_text(encoding="utf-8").strip()
        supplied = self.headers.get("X-Owner-Key", "")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise ApiError(401, "مفتاح المالك غير صحيح")

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        with db() as subscription_connection:
            downgrade_expired_subscriptions(subscription_connection)
        if method == "GET" and path == "/":
            self._send_html(
                """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>خادم خدووم</title><style>
body{margin:0;background:#071126;color:#fff;font-family:Tahoma,Arial;padding:28px}.wrap{max-width:850px;margin:auto}
.hero,.card{background:#111f42;border:1px solid #1d4f7a;border-radius:22px;padding:24px;margin-bottom:16px}
h1{color:#28c7ff;margin:0 0 8px;font-size:36px}.ok{color:#4ade80;font-weight:bold}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{margin:0}.tag{display:inline-block;background:#0c4a6e;color:#7dd3fc;border-radius:99px;padding:6px 12px;margin:4px}
a{color:#38bdf8}code{color:#fbbf24}</style></head><body><div class="wrap">
<section class="hero"><h1>خدووم</h1><p>الخادم وقاعدة البيانات يعملان بنجاح.</p><p class="ok">● متصل وجاهز</p></section>
<div class="grid"><section class="card"><h2>قاعدة البيانات</h2><p>SQLite محلية مع عزل بيانات المؤسسات وتشفير كلمات المرور.</p></section>
<section class="card"><h2>الخدمات</h2><span class="tag">المؤسسات</span><span class="tag">الموظفون</span><span class="tag">المركبات</span><span class="tag">الباقات</span><span class="tag">إعلانات VIP</span></section></div>
<section class="hero" style="margin-top:16px"><h2>فحص API</h2><p><a href="/health">فتح حالة الخادم</a></p><p>واجهة التسجيل: <code>POST /api/register</code></p><p>واجهة الدخول: <code>POST /api/login</code></p></section>
</div></body></html>"""
            )
            return
        if method == "GET" and path.startswith("/chat/"):
            chat_token = path.split("/", 2)[2]
            with db() as connection:
                organization = connection.execute(
                    """SELECT organizations.id,organizations.name,organizations.activity,organizations.phone,
                              subscriptions.package
                       FROM organizations JOIN subscriptions ON subscriptions.organization_id=organizations.id
                       WHERE organizations.public_chat_token=?""",
                    (chat_token,),
                ).fetchone()
            if organization is None:
                raise ApiError(404, "رابط المحادثة غير صحيح")
            with db() as connection:
                maintenance = maintenance_status(connection, organization["id"])
            if maintenance["chat"]:
                raise ApiError(503, maintenance["message"])
            if organization["package"] not in ("basic", "vip"):
                raise ApiError(403, "موظف الاستقبال غير متاح لهذه المؤسسة حاليًا")
            page = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>موظف استقبال __ORG_NAME__</title>
<style>body{margin:0;background:linear-gradient(160deg,#071126,#142454);color:#fff;font-family:Tahoma,Arial;min-height:100vh}.wrap{max-width:720px;margin:auto;padding:22px}.head,.chat{background:#111f42;border:1px solid #24618d;border-radius:22px;padding:20px;margin-bottom:14px}h1{color:#38d4ff;margin:0 0 8px}.messages{min-height:260px;max-height:52vh;overflow:auto;display:flex;flex-direction:column;gap:10px;margin-bottom:14px}.msg{padding:12px 15px;border-radius:16px;white-space:pre-wrap;line-height:1.65}.bot{background:#15345f;align-self:flex-start}.customer{background:#087cab;align-self:flex-end}textarea,input,select,button{box-sizing:border-box;width:100%;padding:14px;border-radius:13px;border:1px solid #2f6b99;color:#fff;font:inherit}textarea,input,select{background:#09152e}textarea{min-height:88px;resize:vertical}.appointment{display:none}.appointment.open{display:block}.appointment label{display:block;margin-top:10px;color:#9bdcf5}button{background:#7c3aed;font-weight:bold;margin-top:10px;cursor:pointer}.note{color:#9bdcf5;font-size:13px}.error{color:#fbbf24}</style></head><body><main class="wrap"><section class="head"><h1>__ORG_NAME__</h1><p>مرحبًا بك، أنا موظف الاستقبال الذكي. كيف أقدر أخدمك؟</p><p class="note">لا ترسل بيانات بنكية أو رموز تحقق. قد يتابع معك موظف بشري عند الحاجة.</p></section><section class="chat"><div id="messages" class="messages"><div class="msg bot">مرحبًا بك في __ORG_NAME__. اكتب استفسارك أو تفاصيل طلبك.</div></div><textarea id="message" maxlength="1500" placeholder="اكتب رسالتك هنا"></textarea><button id="send" onclick="sendMessage()">إرسال</button><div id="status" class="note"></div></section></main>
<script>const history=[];const sessionStorageKey='khdoom_chat___CHAT_TOKEN__';let sessionToken=localStorage.getItem(sessionStorageKey)||'';let lastMessageId=0;function addMessage(text,type){const item=document.createElement('div');item.className='msg '+type;item.textContent=text;const box=document.getElementById('messages');box.appendChild(item);box.scrollTop=box.scrollHeight}async function sendMessage(){const input=document.getElementById('message'),button=document.getElementById('send'),status=document.getElementById('status'),message=input.value.trim();if(!message)return;addMessage(message,'customer');history.push({role:'customer',text:message});input.value='';button.disabled=true;status.textContent='جاري تجهيز الرد...';try{const response=await fetch('/api/public-chat/__CHAT_TOKEN__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,history:history.slice(-8),sessionToken})});const data=await response.json();if(!response.ok)throw new Error(data.error||'تعذر الرد الآن');if(data.sessionToken){sessionToken=data.sessionToken;localStorage.setItem(sessionStorageKey,sessionToken)}if(data.lastMessageId)lastMessageId=Math.max(lastMessageId,data.lastMessageId);addMessage(data.text,'bot');history.push({role:'assistant',text:data.text});status.textContent=''}catch(error){status.textContent=error.message;status.className='note error'}finally{button.disabled=false;input.focus()}}async function pollMessages(){if(!sessionToken)return;try{const response=await fetch(`/api/public-chat/__CHAT_TOKEN__/sessions/${sessionToken}/messages?after=${lastMessageId}`);const data=await response.json();if(!response.ok||!Array.isArray(data))return;for(const message of data){lastMessageId=Math.max(lastMessageId,message.id);addMessage(message.message,message.sender==='customer'?'customer':'bot')}}catch(_){}}setInterval(pollMessages,5000);pollMessages();</script></body></html>"""
            page = page.replace("__ORG_NAME__", html.escape(organization["name"])).replace("__CHAT_TOKEN__", chat_token)
            self._send_html(page)
            return
        if method == "POST" and path.startswith("/api/public-chat/") and path.endswith("/appointments"):
            parts = path.split("/")
            if len(parts) != 5:
                raise ApiError(404, "رابط طلب الموعد غير صحيح")
            chat_token = parts[3]
            data = self._body()
            request_type = str(data.get("type", "طلب عميل")).strip()
            if request_type not in ("موعد مقاس", "موعد صيانة", "طلب عميل"):
                raise ApiError(400, "اختر نوع الطلب")
            customer_name = str(data.get("customer", "")).strip()[:120]
            phone = str(data.get("phone", "")).strip()[:40]
            title = str(data.get("title", "")).strip()[:160] or request_type
            notes = str(data.get("notes", "")).strip()[:1000]
            scheduled_at = str(data.get("scheduledAt", "")).strip()
            if not customer_name or not phone or not scheduled_at:
                raise ApiError(400, "الاسم ورقم التواصل ووقت الموعد مطلوبة")
            try:
                requested_time = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                if requested_time.tzinfo is None:
                    requested_time = requested_time.replace(tzinfo=timezone(timedelta(hours=3)))
                if requested_time.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                    raise ApiError(400, "اختر موعدًا في المستقبل")
            except ApiError:
                raise
            except ValueError:
                raise ApiError(400, "تاريخ الموعد غير صحيح")
            with db() as connection:
                organization = connection.execute(
                    """SELECT organizations.id,subscriptions.package FROM organizations
                       JOIN subscriptions ON subscriptions.organization_id=organizations.id
                       WHERE organizations.public_chat_token=?""",
                    (chat_token,),
                ).fetchone()
                if organization is None:
                    raise ApiError(404, "رابط المحادثة غير صحيح")
                if organization["package"] not in ("basic", "vip"):
                    raise ApiError(403, "استقبال المواعيد غير متاح لهذه المؤسسة حاليًا")
                maintenance = maintenance_status(connection, organization["id"])
                if maintenance["appointments"]:
                    raise ApiError(503, maintenance["message"])
                cursor = connection.execute(
                    """INSERT INTO appointment_requests(
                           organization_id,request_type,title,customer_name,phone,notes,
                           scheduled_at,status,source,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (organization["id"], request_type, title, customer_name, phone, notes,
                     scheduled_at, "pending", "public_chat", now(), now()),
                )
                connection.commit()
            self._send(201, {"id": cursor.lastrowid, "status": "pending", "message": "تم إرسال طلب الموعد للمؤسسة"})
            return
        if method == "GET" and path.startswith("/api/public-chat/") and "/sessions/" in path and path.endswith("/messages"):
            parts = path.split("/")
            if len(parts) != 7:
                raise ApiError(404, "رابط متابعة المحادثة غير صحيح")
            chat_token, session_token = parts[3], parts[5]
            after = int(urlparse(self.path).query.replace("after=", "") or "0")
            with db() as connection:
                session = connection.execute(
                    """SELECT chat_sessions.id FROM chat_sessions JOIN organizations
                       ON organizations.id=chat_sessions.organization_id
                       WHERE organizations.public_chat_token=? AND chat_sessions.public_token=?""",
                    (chat_token, session_token),
                ).fetchone()
                if session is None:
                    self._send(200, [])
                    return
                rows = connection.execute(
                    """SELECT id,sender,message,created_at FROM chat_messages
                       WHERE session_id=? AND id>? ORDER BY id ASC""",
                    (session["id"], after),
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
            return
        if method == "POST" and path.startswith("/api/public-chat/"):
            chat_token = path.rsplit("/", 1)[1]
            data = self._body()
            message = str(data.get("message", "")).strip()[:1500]
            if not message:
                raise ApiError(400, "اكتب رسالة العميل")
            supplied_session_token = str(data.get("sessionToken", "")).strip()
            history = data.get("history")
            if not isinstance(history, list):
                history = []
            safe_history = []
            for item in history[-8:]:
                if not isinstance(item, dict):
                    continue
                role = "موظف الاستقبال" if item.get("role") == "assistant" else "العميل"
                safe_history.append({"role": role, "text": str(item.get("text", ""))[:1500]})
            with db() as connection:
                organization = connection.execute(
                    """SELECT organizations.id,organizations.name,organizations.activity,organizations.phone,
                              subscriptions.package
                       FROM organizations JOIN subscriptions ON subscriptions.organization_id=organizations.id
                       WHERE organizations.public_chat_token=?""",
                    (chat_token,),
                ).fetchone()
                if organization is None:
                    raise ApiError(404, "رابط المحادثة غير صحيح")
                maintenance = maintenance_status(connection, organization["id"])
                if maintenance["chat"]:
                    raise ApiError(503, maintenance["message"])
                package, daily_limit, used = ai_allowance(connection, organization["id"])
                if package not in ("basic", "vip"):
                    raise ApiError(403, "موظف الاستقبال متاح من الباقة الأساسية")
                session = None
                if supplied_session_token:
                    session = connection.execute(
                        "SELECT * FROM chat_sessions WHERE organization_id=? AND public_token=?",
                        (organization["id"], supplied_session_token),
                    ).fetchone()
                if session is None:
                    supplied_session_token = secrets.token_urlsafe(24)
                    cursor = connection.execute(
                        """INSERT INTO chat_sessions(organization_id,public_token,state,context_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?)""",
                        (organization["id"], supplied_session_token, "idle", "{}", now(), now()),
                    )
                    session = connection.execute("SELECT * FROM chat_sessions WHERE id=?", (cursor.lastrowid,)).fetchone()
                customer_cursor = connection.execute(
                    "INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,?,?,?)",
                    (session["id"], "customer", message, now()),
                )
                reply = _appointment_chat_reply(connection, organization["id"], session, message)
                if reply is None:
                    admin = connection.execute(
                        "SELECT id FROM users WHERE organization_id=? AND role='admin' AND active=1 ORDER BY id LIMIT 1",
                        (organization["id"],),
                    ).fetchone()
                    if admin is None:
                        raise ApiError(503, "لا يوجد مسؤول نشط للمؤسسة")
                    system_prompt = (
                        "أنت موظف استقبال تابع للمؤسسة المذكورة. أجب بالعربية بوضوح وفق بيانات المؤسسة فقط. "
                        "لا تبدأ بالتحية إلا إذا بدأ العميل بتحية، ولا تكرر التحية داخل المحادثة. "
                        "لا تخترع أسعارًا أو خدمات أو مواعيد. ممنوع أن تقول تم تأكيد أو تسجيل أو حجز موعد. "
                        "تأكيد الموعد لا يتم إلا بواسطة نظام الحجز بعد جمع الاسم ورقم التواصل والنوع واليوم والوقت، "
                        "ثم موافقة الموظف البشري. تحدث بصورة طبيعية ولا تقل للعميل أكمل أسئلة النظام. "
                        "لا تطلب بيانات بنكية أو رموز تحقق."
                    )
                    organization_info = {"اسم المؤسسة": organization["name"], "نشاط المؤسسة": organization["activity"], "رقم المؤسسة للتواصل": organization["phone"]}
                    training = ai_training_text(connection, organization["id"], "chat")
                    user_prompt = "بيانات المؤسسة:\n" + json.dumps(organization_info, ensure_ascii=False) + "\nتدريب المؤسسة المعتمد:\n" + training + "\nالمحادثة السابقة:\n" + json.dumps(safe_history, ensure_ascii=False) + "\nرسالة العميل الحالية:\n" + message
                    reply = generate_ai_text(system_prompt, user_prompt)
                    connection.execute(
                        "INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)",
                        (organization["id"], admin["id"], "public_reception_chat", now()),
                    )
                bot_cursor = connection.execute(
                    "INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,?,?,?)",
                    (session["id"], "bot", reply, now()),
                )
                connection.commit()
            self._send(200, {"text": reply, "remaining": daily_limit - used - 1, "sessionToken": supplied_session_token, "lastMessageId": bot_cursor.lastrowid})
            return
        if method == "GET" and path == "/owner":
            self._send_html(
                """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>لوحة مالك خدووم</title>
<style>body{margin:0;background:#071126;color:#fff;font-family:Tahoma;padding:24px}.wrap{max-width:900px;margin:auto}.card{background:#111f42;border:1px solid #1d4f7a;border-radius:20px;padding:22px;margin-bottom:14px}h1{color:#28c7ff}input,select,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #285682;background:#09152e;color:#fff}button{background:#0284c7;font-weight:bold;cursor:pointer}.vip{background:#d97706}.result{color:#7dd3fc;white-space:pre-wrap}</style></head><body><div class="wrap"><h1>لوحة مالك خدووم</h1>
<div class="card"><h2>الدخول الآمن</h2><input id="key" type="password" placeholder="مفتاح المالك"><button onclick="ownerLogin()">دخول لوحة المالك</button><div id="loginStatus" class="result">أدخل المفتاح ثم اضغط دخول</div></div>
<div class="card"><h2>أكواد الخصم</h2><p>إنشاء أكواد خصم للعروض والإعلانات، مثل خصم 15%.</p><button onclick="location.href='/owner/codes'">فتح صفحة أكواد الخصم</button></div>
<div class="card"><h2>لوحة الأمن 🔐</h2><p>نظرة عامة على الحسابات والتنبيهات والأجهزة وحالة خدمات خدووم.</p><button onclick="location.href='/owner/security'">فتح لوحة الأمن</button></div>
<div class="card"><h2>طلبات ترقية الباقات</h2><button onclick="loadSubscriptionRequests()">تحميل طلبات الترقية</button><div id="subscriptionRequests" class="result"></div></div><div class="card"><h2>المؤسسات</h2><button onclick="loadOrganizations()">عرض المؤسسات</button><div id="organizations" class="result"></div></div><div class="card"><h2>مراجعة إعلانات VIP</h2><button class="vip" onclick="loadAds()">تحميل الإعلانات</button><div id="ads" class="result"></div></div></div>
<script>const headers=()=>({'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value.trim()});async function ownerLogin(){let r=await fetch('/owner/api/organizations',{headers:headers()});let d=await r.json();let s=document.getElementById('loginStatus');if(r.ok){s.textContent='تم الدخول بنجاح ✓';loadOrganizations();loadSubscriptionRequests();loadAds()}else{s.textContent=d.error||'تعذر الدخول'}}async function createCode(){let r=await fetch('/owner/api/codes',{method:'POST',headers:headers(),body:JSON.stringify({recipientName:document.getElementById('recipient').value,assignedUsername:document.getElementById('assignedUsername').value,customCode:document.getElementById('customCode').value,package:document.getElementById('package').value,durationDays:+document.getElementById('days').value,maxUses:+document.getElementById('uses').value})});let d=await r.json();document.getElementById('result').textContent=r.ok?'الكود: '+d.code+'\\nمخصص إلى: '+(d.recipientName||'غير محدد')+'\\nالباقة: '+d.package+'\\nالمدة: '+d.durationDays+' يوم':(d.error||'تعذر إنشاء الكود')}async function setPackage(id,pkg){let days=pkg==='free'?1:+document.getElementById('days-'+id).value;if(pkg!=='free'&&(!days||days<1)){alert('اكتب مدة صحيحة بالأيام');return}let r=await fetch('/owner/api/organizations/'+id+'/package',{method:'PUT',headers:headers(),body:JSON.stringify({package:pkg,durationDays:days})});let d=await r.json();if(r.ok&&d.saved){alert(pkg==='free'?'تم قفل الباقات وإعادة المؤسسة للمجانية':'تم فتح الباقة لمدة '+days+' يوم');await loadOrganizations()}else{alert(d.error||'تعذر تغيير الباقة')}}async function loadSubscriptionRequests(){let r=await fetch('/owner/api/subscription-requests',{headers:headers()});let data=await r.json();let box=document.getElementById('subscriptionRequests');if(!Array.isArray(data)){box.textContent=data.error||'تعذر تحميل طلبات الترقية';return}box.innerHTML=data.length?data.map(x=>`<div class="card"><b>${esc(x.organization_name)}</b><p>التواصل: ${esc(x.phone)}</p><p>الباقة الحالية: ${x.current_package}</p><p>الباقة المطلوبة: ${x.requested_package==='basic'?'الأساسية':'VIP'}</p><p>كود الخصم: ${x.discount_code?esc(x.discount_code)+' — خصم '+x.discount_percent+'%':'بدون كود'}</p><p>الحالة: ${x.status==='pending'?'بانتظار المراجعة':x.status==='approved'?'مقبول':'مرفوض'}</p>${x.status==='pending'?`<input id="request-days-${x.id}" type="number" min="1" value="30" placeholder="مدة فتح الباقة بالأيام"><button onclick="reviewSubscriptionRequest(${x.id},'approve')">قبول وفتح الباقة</button><button class="vip" onclick="reviewSubscriptionRequest(${x.id},'reject')">رفض الطلب</button>`:''}</div>`).join(''):'لا توجد طلبات ترقية بعد'}async function reviewSubscriptionRequest(id,action){let days=+document.getElementById('request-days-'+id)?.value||30;let r=await fetch('/owner/api/subscription-requests/'+id,{method:'PUT',headers:headers(),body:JSON.stringify({action:action,durationDays:days})});let d=await r.json();alert(r.ok?(action==='approve'?'تم قبول الطلب وفتح الباقة':'تم رفض الطلب'):(d.error||'تعذر معالجة الطلب'));if(r.ok){loadSubscriptionRequests();loadOrganizations()}}async function loadOrganizations(){let r=await fetch('/owner/api/organizations',{headers:headers()});let data=await r.json();let box=document.getElementById('organizations');if(!Array.isArray(data)){box.textContent=data.error||'تعذر عرض المؤسسات';return}if(data.length===0){box.textContent='لا توجد مؤسسات في قاعدة البيانات الجديدة بعد. يلزم ربط تسجيل حساب التطبيق بالخادم ثم ستظهر المؤسسات هنا.';return}box.innerHTML=data.map(o=>`<div class="card"><b>${esc(o.name)}</b><p>الباقة الحالية: ${o.package} | ${esc(o.phone)}</p><p>تنتهي: ${o.expires_at||'لا يوجد'}</p><a href="/chat/${o.public_chat_token}" target="_blank" style="display:block;color:#7dd3fc;margin:10px 0">فتح رابط محادثة الزبائن</a><details><summary style="cursor:pointer;background:#0284c7;padding:13px;border-radius:10px;font-weight:bold;margin:10px 0">⚙️ الإعدادات</summary><div style="padding:12px;border:1px solid #285682;border-radius:12px"><h3>استهلاك AI</h3><p>اليوم: ${o.ai_today||0} من ${o.ai_daily_limit||0} (${o.ai_usage_percent||0}%) — الشهر: ${o.ai_month||0} استخدام</p><p>التكلفة التقديرية للشهر: ${Number(o.ai_estimated_cost_sar||0).toFixed(2)} ر.س (${Number(o.ai_estimated_cost_usd||0).toFixed(4)} دولار)</p><p style="color:${o.ai_usage_status==='danger'?'#fb7185':o.ai_usage_status==='warning'?'#facc15':'#4ade80'}">${o.ai_usage_status==='danger'?'🚨 تم بلوغ الحد اليومي':o.ai_usage_status==='warning'?'⚠️ اقتربت المؤسسة من الحد اليومي':'✓ الاستهلاك طبيعي'}</p><small style="color:#94a3b8">التكلفة تقديرية وتختلف حسب طول الرسائل والردود.</small><input id="ai-limit-${o.id}" type="number" min="1" value="${o.custom_ai_limit||({free:5,basic:30,vip:100}[o.package]||5)}" placeholder="الحد اليومي"><button onclick="setAiLimit(${o.id})">حفظ الحد اليومي</button><h3>أقفال الصيانة</h3><p><b>مساعد المؤسسة:</b> ${o.assistant_until||'يعمل'}</p><button onclick="setMaintenance(${o.id},'assistant',true)">🔒 قفل مساعد المؤسسة</button><button onclick="setMaintenance(${o.id},'assistant',false)">🔓 فتح مساعد المؤسسة</button><p><b>الشات:</b> ${o.chat_until||'مفتوح'}</p><p><b>المواعيد:</b> ${o.appointments_until||'مفتوحة'}</p><input id="maintenance-hours-${o.id}" type="number" min="1" value="24" placeholder="مدة الصيانة بالساعات"><input id="maintenance-message-${o.id}" value="${esc(o.maintenance_message||'الخدمة تحت الصيانة مؤقتًا')}" placeholder="رسالة الصيانة"><button onclick="setMaintenance(${o.id},'chat',true)">🔒 قفل الشات</button><button onclick="setMaintenance(${o.id},'chat',false)">🔓 فتح الشات</button><button onclick="setMaintenance(${o.id},'appointments',true)">🔒 قفل المواعيد</button><button onclick="setMaintenance(${o.id},'appointments',false)">🔓 فتح المواعيد</button></div></details><input id="days-${o.id}" type="number" min="1" value="30" placeholder="المدة بالأيام"><button onclick="setPackage(${o.id},'free')">🔒 قفل وإرجاعها مجانية</button><button onclick="setPackage(${o.id},'basic')">${o.package==='basic'?'🔓':'🔒'} فتح الأساسية</button><button class="vip" onclick="setPackage(${o.id},'vip')">${o.package==='vip'?'🔓':'🔒'} فتح VIP</button></div>`).join('')}async function setAiLimit(id){let dailyLimit=+document.getElementById('ai-limit-'+id).value;if(!dailyLimit||dailyLimit<1){alert('اكتب حدًا يوميًا صحيحًا');return}let r=await fetch('/owner/api/organizations/'+id+'/ai-limit',{method:'PUT',headers:headers(),body:JSON.stringify({dailyLimit})});let d=await r.json();alert(r.ok?'تم حفظ الحد اليومي':(d.error||'تعذر حفظ الحد'));if(r.ok)loadOrganizations()}async function setMaintenance(id,service,enabled){let hours=+document.getElementById('maintenance-hours-'+id).value||24;let message=document.getElementById('maintenance-message-'+id).value;let serviceName=service==='chat'?'شات العملاء':service==='appointments'?'المواعيد':'مساعد المؤسسة';if(!confirm(enabled?'تأكيد إيقاف '+serviceName+' لهذه المؤسسة لمدة '+hours+' ساعة؟':'تأكيد تشغيل '+serviceName+' لهذه المؤسسة؟'))return;let r=await fetch('/owner/api/organizations/'+id+'/maintenance',{method:'PUT',headers:headers(),body:JSON.stringify({service,enabled,hours,message})});let d=await r.json();alert(r.ok?(enabled?'تم قفل الخدمة للمدة المحددة':'تم فتح الخدمة'):(d.error||'تعذر تغيير وضع الصيانة'));if(r.ok)loadOrganizations()}async function loadAds(){let r=await fetch('/owner/api/ads',{headers:headers()});let data=await r.json();let box=document.getElementById('ads');if(!Array.isArray(data)){box.textContent=data.error||'تعذر تحميل الإعلانات';return}box.innerHTML=data.length?data.map(a=>`<div class="card"><b>${esc(a.title)}</b><p>المؤسسة: ${esc(a.organization_name)}</p><p>${esc(a.message||'')}</p><p>التواصل: ${esc(a.contact||'')}</p><p>الحالة: ${a.approved?'مقبول':a.active?'بانتظار المراجعة':'مرفوض'}</p><p>ينتهي: ${a.expires_at||'لم تحدد المدة بعد'}</p><label for="ad-days-${a.id}" style="display:block;color:#bae6fd;font-weight:bold;margin-top:12px">مدة عرض الإعلان (بالأيام)</label><input id="ad-days-${a.id}" type="number" min="1" value="30" placeholder="مثال: 30 يومًا"><small style="display:block;color:#94a3b8;margin-bottom:8px">مثال: 30 تعني عرض الإعلان لمدة شهر من وقت القبول.</small><button onclick="reviewAd(${a.id},'approve')">قبول ونشر</button><button class="vip" onclick="reviewAd(${a.id},'reject')">رفض</button></div>`).join(''):'لا توجد إعلانات للمراجعة'}async function reviewAd(id,action){let r=await fetch('/owner/api/ads/'+id,{method:'PUT',headers:headers(),body:JSON.stringify({action,durationDays:+document.getElementById('ad-days-'+id).value||30})});let d=await r.json();alert(r.ok?(action==='approve'?'تم قبول الإعلان ونشره':'تم رفض الإعلان'):(d.error||'تعذر تحديث الإعلان'));if(r.ok)loadAds()}function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</script></body></html>"""
            )
            return
        if method == "GET" and path == "/owner/security":
            self._send_html(
                """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>لوحة أمن خدووم</title>
<style>body{margin:0;background:#071126;color:#fff;font-family:Tahoma;padding:24px}.wrap{max-width:1100px;margin:auto}h1{color:#28c7ff}.card{background:#111f42;border:1px solid #1d4f7a;border-radius:20px;padding:20px;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.metric{text-align:center}.metric strong{display:block;font-size:34px;color:#38bdf8;margin:8px}.ok{color:#4ade80}.warn{color:#facc15}.danger{color:#fb7185}input,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #285682;background:#09152e;color:#fff}button{background:#0284c7;font-weight:bold;cursor:pointer}.event{border-right:4px solid #f59e0b;padding:12px 14px;margin:9px 0;background:#0b1733;border-radius:10px}.muted{color:#94a3b8}a{color:#7dd3fc}</style></head><body><div class="wrap"><h1>لوحة أمن خدووم 🔐</h1><p><a href="/owner">العودة إلى لوحة المالك</a></p>
<div class="card"><h2>الدخول الآمن</h2><input id="key" type="password" placeholder="مفتاح المالك"><button onclick="loadSecurity()">دخول وتحديث لوحة الأمن</button><div id="status" class="muted">أدخل مفتاح المالك لعرض البيانات الأمنية.</div></div>
<div id="dashboard" style="display:none"><div class="grid"><div class="card metric"><span>الحسابات النشطة</span><strong id="activeUsers">0</strong></div><div class="card metric"><span>محاولات مشبوهة خلال 24 ساعة</span><strong id="failedLogins" class="danger">0</strong></div><div class="card metric"><span>الأجهزة غير الموثوقة</span><strong id="untrustedDevices" class="warn">0</strong></div><div class="card metric"><span>تنبيهات آخر 7 أيام</span><strong id="securityAlerts">0</strong></div><div class="card metric"><span>خدمات متوقفة</span><strong id="stoppedServices">0</strong></div></div>
<div class="card"><h2>حالة خدمات خدووم</h2><div id="services"></div></div><div class="card"><h2>وضع الطوارئ العام 🚨</h2><p class="muted">يوقف خدمة محددة لجميع المؤسسات حتى انتهاء المدة أو فتحها يدويًا.</p><input id="emergencyHours" type="number" min="1" value="2" placeholder="المدة بالساعات"><input id="emergencyMessage" value="الخدمة تحت الصيانة مؤقتًا" placeholder="الرسالة للمستخدمين"><div id="emergencyStatus"></div><button onclick="setEmergency('chat',true)">🔒 إيقاف شات العملاء للجميع</button><button onclick="setEmergency('chat',false)">🔓 تشغيل شات العملاء</button><button onclick="setEmergency('appointments',true)">🔒 إيقاف المواعيد للجميع</button><button onclick="setEmergency('appointments',false)">🔓 تشغيل المواعيد</button><button onclick="setEmergency('assistant',true)">🔒 إيقاف مساعد المؤسسة للجميع</button><button onclick="setEmergency('assistant',false)">🔓 تشغيل مساعد المؤسسة</button></div><div class="card"><h2>محاولات الدخول</h2><p class="muted">آخر المحاولات الفاشلة مع الجهاز والوقت وعدد التكرار.</p><div id="loginAttempts"></div></div><div class="card"><h2>الحسابات</h2><p class="muted">التجميد يمنع الدخول دون حذف بيانات الحساب.</p><div id="accounts"></div></div><div class="card"><h2>الأجهزة والجلسات</h2><p class="muted">كل جهاز موضح معه اسم المؤسسة والمستخدم.</p><div id="devices"></div></div><div class="card"><h2>الأجهزة المحظورة</h2><p class="muted">لا تستطيع هذه الأجهزة تسجيل الدخول حتى فك الحظر.</p><div id="blockedDevices"></div></div><div class="card"><h2>سجل العمليات الكامل</h2><select id="auditOrg"><option value="">جميع المؤسسات</option></select><select id="auditAction"><option value="">جميع العمليات</option><option value="security">العمليات الأمنية</option><option value="employee">الموظفون والصلاحيات</option><option value="appointment">المواعيد والطلبات</option><option value="organization">بيانات المؤسسة</option></select><input id="auditDate" type="date"><button onclick="loadAuditLogs()">تطبيق الفلاتر</button><div id="auditLogs"></div></div><div class="card"><h2>آخر التنبيهات الأمنية</h2><div id="events"></div></div></div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hdr=()=>({'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value.trim()});
async function loadSecurity(){let r=await fetch('/owner/api/security/overview',{headers:hdr()}),d=await r.json(),s=document.getElementById('status');if(!r.ok){s.textContent=d.error||'تعذر تحميل لوحة الأمن';document.getElementById('dashboard').style.display='none';return}s.textContent='تم تحديث البيانات الأمنية ✓';document.getElementById('dashboard').style.display='block';for(let k of ['activeUsers','failedLogins','untrustedDevices','securityAlerts','stoppedServices'])document.getElementById(k).textContent=d[k]||0;document.getElementById('services').innerHTML=d.services.map(x=>`<p><b>${esc(x.name)}</b> — <span class="${x.running?'ok':'danger'}">${x.running?'تعمل':'متوقفة مؤقتًا'}</span>${x.organization?' — '+esc(x.organization):''}</p>`).join('')||'<p class="ok">جميع الخدمات تعمل ✓</p>';document.getElementById('events').innerHTML=d.events.map(x=>`<div class="event"><b>${esc(x.organization_name||'مؤسسة')}</b><p>${esc(x.summary)}</p><small class="muted">${esc(x.created_at)}</small></div>`).join('')||'<p class="ok">لا توجد تنبيهات حديثة ✓</p>';renderEmergency(d.emergency||[]);renderLoginAttempts(d.loginAttempts||[]);renderAccounts(d.accounts||[]);renderDevices(d.devices||[]);renderBlockedDevices(d.blockedDevices||[]);setupAuditOrganizations(d.accounts||[]);loadAuditLogs()}
function renderEmergency(items){document.getElementById('emergencyStatus').innerHTML=items.map(x=>`<p><b>${x.service==='chat'?'شات العملاء':x.service==='appointments'?'المواعيد':'مساعد المؤسسة'}:</b> <span class="${x.active?'danger':'ok'}">${x.active?'متوقفة حتى '+esc(x.until):'تعمل'}</span></p>`).join('')}
async function setEmergency(service,enabled){let hours=+document.getElementById('emergencyHours').value||2,message=document.getElementById('emergencyMessage').value;if(!confirm(enabled?'تأكيد إيقاف الخدمة لجميع المؤسسات؟':'تأكيد تشغيل الخدمة لجميع المؤسسات؟'))return;await act('/owner/api/security/emergency','PUT',{service,enabled,hours,message})}function setupAuditOrganizations(accounts){let select=document.getElementById('auditOrg'),current=select.value,seen=new Map();for(let x of accounts)seen.set(x.organization_id||x.organization_name,x.organization_name);select.innerHTML='<option value="">جميع المؤسسات</option>'+[...seen].map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');select.value=current}
async function loadAuditLogs(){let q=new URLSearchParams(),org=document.getElementById('auditOrg').value,action=document.getElementById('auditAction').value,date=document.getElementById('auditDate').value;if(org)q.set('organization',org);if(action)q.set('type',action);if(date)q.set('date',date);let r=await fetch('/owner/api/security/audit-logs?'+q,{headers:hdr()}),d=await r.json(),box=document.getElementById('auditLogs');if(!r.ok){box.innerHTML=`<p class="danger">${esc(d.error||'تعذر تحميل السجل')}</p>`;return}box.innerHTML=d.map(x=>`<div class="event"><b>${esc(x.organization_name)} — ${esc(x.action)}</b><p>${esc(x.summary)}</p><small class="muted">بواسطة: ${esc(x.actor_name||x.actor_username||'النظام')} — ${esc(x.created_at)}</small></div>`).join('')||'<p>لا توجد عمليات مطابقة.</p>'}function renderLoginAttempts(a){document.getElementById('loginAttempts').innerHTML=a.map(x=>`<div class="event"><b>${esc(x.organization_name)} — ${esc(x.user_name||x.username)}</b><p>${esc(x.summary)}</p><p>التكرار لنفس الحساب والجهاز: ${x.attempt_count} | الوقت: ${esc(x.created_at)}</p></div>`).join('')||'<p class="ok">لا توجد محاولات دخول فاشلة ✓</p>'}function renderAccounts(a){document.getElementById('accounts').innerHTML=a.map(x=>`<div class="event"><b>${esc(x.organization_name)} — ${esc(x.name)}</b><p>المستخدم: ${esc(x.username)} | ${x.role==='admin'?'مالك':'موظف'} | ${x.active?'نشط':'مجمّد'}</p><button onclick="accountAction(${x.id},${!x.active})">${x.active?'تجميد الحساب':'إعادة تفعيل الحساب'}</button></div>`).join('')||'<p>لا توجد حسابات.</p>'}
function renderDevices(a){document.getElementById('devices').innerHTML=a.map(x=>`<div class="event"><b>${esc(x.organization_name)} — ${esc(x.user_name)}</b><p>الجهاز: ${esc(x.device_name||'جهاز غير معروف')} | المستخدم: ${esc(x.username)}</p><p>آخر استخدام: ${esc(x.last_seen_at||x.created_at)} | ${x.trusted?'موثوق':'غير موثوق'}</p><button onclick="deviceTrust('${x.id}',${!x.trusted})">${x.trusted?'إلغاء التوثيق':'توثيق الجهاز'}</button><button onclick="disconnectDevice('${x.id}')">فصل الجهاز</button><button onclick="blockDevice('${x.id}')">حظر الجهاز</button></div>`).join('')||'<p>لا توجد أجهزة نشطة.</p>'}
function renderBlockedDevices(a){document.getElementById('blockedDevices').innerHTML=a.map(x=>`<div class="event"><b>${esc(x.organization_name)} — ${esc(x.device_name||'جهاز غير معروف')}</b><p>معرّف الجهاز: ${esc(x.device_id)} | حُظر: ${esc(x.blocked_at)}</p><button onclick="unblockDevice(${x.organization_id},'${x.device_id}')">فك الحظر</button></div>`).join('')||'<p class="ok">لا توجد أجهزة محظورة ✓</p>'}
async function accountAction(id,active){if(!confirm(active?'تأكيد إعادة تفعيل الحساب؟':'تأكيد تجميد الحساب؟'))return;await act('/owner/api/security/accounts/'+id,'PUT',{active})}
async function deviceTrust(id,trusted){if(!confirm('تأكيد تغيير حالة توثيق الجهاز؟'))return;await act('/owner/api/security/devices/'+id,'PUT',{trusted})}
async function blockDevice(id){if(!confirm('تأكيد حظر هذا الجهاز ومنعه من الدخول؟'))return;await act('/owner/api/security/devices/'+id+'/block','POST')}
async function unblockDevice(organizationId,deviceId){if(!confirm('تأكيد فك حظر الجهاز؟'))return;await act('/owner/api/security/device-blocks/'+organizationId+'/'+encodeURIComponent(deviceId),'DELETE')}
async function disconnectDevice(id){if(!confirm('تأكيد فصل هذا الجهاز وإنهاء جلسته؟'))return;await act('/owner/api/security/devices/'+id,'DELETE')}
async function act(url,method,body){let r=await fetch(url,{method,headers:hdr(),body:body?JSON.stringify(body):null}),d=await r.json();alert(r.ok?'تم تنفيذ العملية ✓':(d.error||'تعذر تنفيذ العملية'));if(r.ok)loadSecurity()}
</script></body></html>"""
            )
            return
        if path == "/owner/api/security/overview" and method == "GET":
            self._owner()
            current_time = now()
            day_start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            week_start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            with db() as connection:
                active_users = connection.execute("SELECT COUNT(*) AS total FROM users WHERE active=1").fetchone()["total"]
                failed_logins = connection.execute("SELECT COUNT(*) AS total FROM audit_logs WHERE action='failed_login' AND created_at>=?", (day_start,)).fetchone()["total"]
                untrusted_devices = connection.execute("SELECT COUNT(*) AS total FROM sessions WHERE trusted=0 AND expires_at>?", (current_time,)).fetchone()["total"]
                security_alerts = connection.execute("SELECT COUNT(*) AS total FROM audit_logs WHERE target_type='security' AND created_at>=?", (week_start,)).fetchone()["total"]
                events = connection.execute("""SELECT audit_logs.summary,audit_logs.created_at,organizations.name AS organization_name FROM audit_logs JOIN organizations ON organizations.id=audit_logs.organization_id WHERE audit_logs.target_type='security' ORDER BY audit_logs.id DESC LIMIT 20""").fetchall()
                stopped = connection.execute("""SELECT organizations.name AS organization_name,'مساعد المؤسسة' AS service FROM service_maintenance JOIN organizations ON organizations.id=service_maintenance.organization_id WHERE service_maintenance.service='assistant' AND service_maintenance.expires_at>?
                    UNION ALL SELECT organizations.name,'شات العملاء' FROM maintenance_modes JOIN organizations ON organizations.id=maintenance_modes.organization_id WHERE maintenance_modes.chat_until>?
                    UNION ALL SELECT organizations.name,'المواعيد' FROM maintenance_modes JOIN organizations ON organizations.id=maintenance_modes.organization_id WHERE maintenance_modes.appointments_until>?""", (current_time,current_time,current_time)).fetchall()
            with db() as connection:
                login_attempts = connection.execute("""SELECT audit_logs.id,audit_logs.summary,audit_logs.created_at,users.name AS user_name,users.username,organizations.name AS organization_name,COUNT(1) OVER (PARTITION BY audit_logs.actor_user_id,audit_logs.summary) AS attempt_count FROM audit_logs JOIN organizations ON organizations.id=audit_logs.organization_id LEFT JOIN users ON users.id=audit_logs.actor_user_id WHERE audit_logs.action='failed_login' ORDER BY audit_logs.id DESC LIMIT 100""").fetchall()
                accounts = connection.execute("""SELECT users.id,users.name,users.username,users.role,users.active,organizations.id AS organization_id,organizations.name AS organization_name FROM users JOIN organizations ON organizations.id=users.organization_id ORDER BY organizations.name,users.id""").fetchall()
                devices = connection.execute("""SELECT sessions.token_hash AS id,sessions.device_id,sessions.device_name,sessions.trusted,sessions.last_seen_at,sessions.created_at,sessions.expires_at,users.name AS user_name,users.username,organizations.name AS organization_name FROM sessions JOIN users ON users.id=sessions.user_id JOIN organizations ON organizations.id=users.organization_id WHERE sessions.expires_at>? ORDER BY sessions.last_seen_at DESC,sessions.created_at DESC""", (current_time,)).fetchall()
                blocked_devices = connection.execute("""SELECT blocked_devices.organization_id,blocked_devices.device_id,blocked_devices.device_name,blocked_devices.blocked_at,organizations.name AS organization_name FROM blocked_devices JOIN organizations ON organizations.id=blocked_devices.organization_id ORDER BY blocked_devices.blocked_at DESC""").fetchall()
                emergency = [{"service": service, **global_maintenance_status(connection, service)} for service in ("chat", "appointments", "assistant")]
            services = [{"name": "خادم خدووم API", "running": True, "organization": ""}] + [{"name": row["service"], "running": False, "organization": row["organization_name"]} for row in stopped]
            account_items = [dict(row) for row in accounts]
            for item in account_items:
                item["active"] = bool(item["active"])
            device_items = [dict(row) for row in devices]
            for item in device_items:
                item["trusted"] = bool(item["trusted"])
            self._send(200, {"activeUsers": active_users,"failedLogins": failed_logins,"untrustedDevices": untrusted_devices,"securityAlerts": security_alerts,"stoppedServices": len(stopped) + sum(1 for item in emergency if item["active"]),"services": services,"events": [dict(row) for row in events],"loginAttempts": [dict(row) for row in login_attempts],"accounts": account_items,"devices": device_items,"blockedDevices": [dict(row) for row in blocked_devices]})
            return
        if path == "/owner/api/security/emergency" and method == "PUT":
            self._owner()
            data = self._body()
            service = str(data.get("service", "")).strip()
            if service not in ("chat", "appointments", "assistant"):
                raise ApiError(400, "اختر خدمة صحيحة")
            enabled = data.get("enabled") is True
            hours = max(1, min(int(data.get("hours", 2)), 24 * 30))
            until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat() if enabled else None
            message = str(data.get("message", "الخدمة تحت الصيانة مؤقتًا")).strip()[:300] or "الخدمة تحت الصيانة مؤقتًا"
            with db() as connection:
                connection.execute("""INSERT INTO global_maintenance(service,expires_at,message) VALUES(?,?,?) ON CONFLICT(service) DO UPDATE SET expires_at=excluded.expires_at,message=excluded.message""", (service, until, message))
                organizations = connection.execute("SELECT id FROM organizations").fetchall()
                service_name = {"chat": "شات العملاء", "appointments": "المواعيد", "assistant": "مساعد المؤسسة"}[service]
                for organization in organizations:
                    audit_log(connection, organization["id"], None, "global_emergency", f"تم {'إيقاف' if enabled else 'تشغيل'} {service_name} {'لجميع المؤسسات' if enabled else 'بعد الطوارئ'}", "security", service)
                connection.commit()
            self._send(200, {"saved": True, "service": service, "enabled": enabled, "until": until})
            return
        if path == "/owner/api/security/audit-logs" and method == "GET":
            self._owner()
            params = parse_qs(urlparse(self.path).query)
            clauses = []
            values: list[Any] = []
            raw_organization = str(params.get("organization", [""])[0]).strip()
            operation_type = str(params.get("type", [""])[0]).strip()
            operation_date = str(params.get("date", [""])[0]).strip()
            if raw_organization:
                try:
                    organization_id = int(raw_organization)
                except ValueError:
                    raise ApiError(400, "رقم المؤسسة غير صحيح")
                clauses.append("audit_logs.organization_id=?")
                values.append(organization_id)
            if operation_type:
                if operation_type not in ("security", "employee", "appointment", "organization"):
                    raise ApiError(400, "نوع العملية غير صحيح")
                clauses.append("audit_logs.target_type=?")
                values.append(operation_type)
            if operation_date:
                try:
                    datetime.strptime(operation_date, "%Y-%m-%d")
                except ValueError:
                    raise ApiError(400, "التاريخ غير صحيح")
                clauses.append("substr(audit_logs.created_at,1,10)=?")
                values.append(operation_date)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            with db() as connection:
                rows = connection.execute(
                    """SELECT audit_logs.id,audit_logs.action,audit_logs.target_type,audit_logs.target_id,audit_logs.summary,audit_logs.created_at,organizations.name AS organization_name,users.name AS actor_name,users.username AS actor_username FROM audit_logs JOIN organizations ON organizations.id=audit_logs.organization_id LEFT JOIN users ON users.id=audit_logs.actor_user_id""" + where + " ORDER BY audit_logs.id DESC LIMIT 500",
                    tuple(values),
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
            return
        if path.startswith("/owner/api/security/accounts/") and method == "PUT":
            self._owner()
            try:
                account_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                raise ApiError(400, "رقم الحساب غير صحيح")
            active = 1 if self._body().get("active") is True else 0
            with db() as connection:
                account = connection.execute("SELECT id,organization_id,name FROM users WHERE id=?", (account_id,)).fetchone()
                if account is None:
                    raise ApiError(404, "الحساب غير موجود")
                connection.execute("UPDATE users SET active=? WHERE id=?", (active, account_id))
                if not active:
                    connection.execute("DELETE FROM sessions WHERE user_id=?", (account_id,))
                audit_log(connection, account["organization_id"], None, "owner_account_status", f"تم {'تفعيل' if active else 'تجميد'} الحساب {account['name']} من لوحة الأمن", "security", account_id)
                connection.commit()
            self._send(200, {"saved": True, "active": bool(active)})
            return
        if path.startswith("/owner/api/security/devices/") and path.endswith("/block") and method == "POST":
            self._owner()
            session_id = path.split("/")[-2]
            with db() as connection:
                session = connection.execute("""SELECT sessions.device_id,sessions.device_name,users.organization_id,users.name FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=?""", (session_id,)).fetchone()
                if session is None or not session["device_id"]:
                    raise ApiError(404, "الجهاز غير موجود أو لا يملك معرّفًا صالحًا")
                connection.execute("""INSERT INTO blocked_devices(organization_id,device_id,device_name,blocked_at) VALUES(?,?,?,?) ON CONFLICT(organization_id,device_id) DO UPDATE SET device_name=excluded.device_name,blocked_at=excluded.blocked_at""", (session["organization_id"], session["device_id"], session["device_name"], now()))
                connection.execute("DELETE FROM sessions WHERE device_id=? AND user_id IN (SELECT id FROM users WHERE organization_id=?)", (session["device_id"], session["organization_id"]))
                audit_log(connection, session["organization_id"], None, "device_blocked", f"تم حظر جهاز {session['device_name']} للمستخدم {session['name']}", "security", session["device_id"][:40])
                connection.commit()
            self._send(200, {"blocked": True})
            return
        if path.startswith("/owner/api/security/device-blocks/") and method == "DELETE":
            self._owner()
            parts = path.split("/")
            try:
                organization_id = int(parts[-2])
            except ValueError:
                raise ApiError(400, "رقم المؤسسة غير صحيح")
            device_id = unquote(parts[-1])
            with db() as connection:
                blocked = connection.execute("SELECT device_name FROM blocked_devices WHERE organization_id=? AND device_id=?", (organization_id, device_id)).fetchone()
                if blocked is None:
                    raise ApiError(404, "الجهاز غير موجود في قائمة الحظر")
                connection.execute("DELETE FROM blocked_devices WHERE organization_id=? AND device_id=?", (organization_id, device_id))
                audit_log(connection, organization_id, None, "device_unblocked", f"تم فك حظر جهاز {blocked['device_name']}", "security", device_id[:40])
                connection.commit()
            self._send(200, {"unblocked": True})
            return
        if path.startswith("/owner/api/security/devices/") and method in ("PUT", "DELETE"):
            self._owner()
            session_id = path.rsplit("/", 1)[1]
            with db() as connection:
                session = connection.execute("""SELECT sessions.token_hash,users.organization_id,users.name,sessions.device_name FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=?""", (session_id,)).fetchone()
                if session is None:
                    raise ApiError(404, "الجهاز أو الجلسة غير موجودة")
                if method == "PUT":
                    trusted = 1 if self._body().get("trusted") is True else 0
                    connection.execute("UPDATE sessions SET trusted=? WHERE token_hash=?", (trusted, session_id))
                    summary = f"تم {'توثيق' if trusted else 'إلغاء توثيق'} جهاز {session['device_name']} للمستخدم {session['name']}"
                    result = {"saved": True, "trusted": bool(trusted)}
                else:
                    connection.execute("DELETE FROM sessions WHERE token_hash=?", (session_id,))
                    summary = f"تم فصل جهاز {session['device_name']} للمستخدم {session['name']}"
                    result = {"disconnected": True}
                audit_log(connection, session["organization_id"], None, "owner_device_action", summary, "security", session_id[:12])
                connection.commit()
            self._send(200, result)
            return
        if method == "GET" and path == "/owner/codes":
            self._send_html(
                """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>أكواد خصم خدووم</title>
<style>body{margin:0;background:#071126;color:#fff;font-family:Tahoma;padding:24px}.wrap{max-width:900px;margin:auto}.card{background:#111f42;border:1px solid #1d4f7a;border-radius:20px;padding:22px;margin-bottom:14px}h1{color:#28c7ff}input,select,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #285682;background:#09152e;color:#fff}button{background:#0284c7;font-weight:bold;cursor:pointer}.vip{background:#d97706}.result{color:#7dd3fc;white-space:pre-wrap}.code{border-right:4px solid #f59e0b}.field{margin:14px 0}.field label{display:block;font-weight:bold;color:#bae6fd;margin-bottom:3px}.hint{display:block;color:#94a3b8;font-size:13px;margin-top:2px}</style></head><body><div class="wrap"><h1>صفحة أكواد خصم خدووم</h1><p><a href="/owner" style="color:#7dd3fc">العودة إلى لوحة المالك</a></p>
<div class="card"><h2>مفتاح المالك</h2><input id="key" type="password" placeholder="مفتاح المالك"><button onclick="login()">دخول وتحميل الأكواد</button><div id="status" class="result"></div></div>
<div class="card"><h2>إنشاء كود خصم جديد</h2><div class="field"><label for="recipient">اسم الحملة أو المعلن</label><input id="recipient" placeholder="مثال: إعلان أحمد"><span class="hint">يظهر في سجل الأكواد لتعرف سبب إنشاء الكود.</span></div><div class="field"><label for="customCode">كود الخصم</label><input id="customCode" placeholder="مثال: AHMED15"><span class="hint">هذا هو الكود الذي ترسله للعميل.</span></div><div class="field"><label for="discount">نسبة الخصم (%)</label><input id="discount" type="number" min="1" max="90" value="15"><span class="hint">مثال: 15 تعني خصم 15% من سعر الباقة.</span></div><div class="field"><label for="validityDays">مدة صلاحية الكود بالأيام</label><input id="validityDays" type="number" min="1" value="30"><span class="hint">بعد انتهاء هذه المدة يتوقف قبول الكود.</span></div><div class="field"><label for="uses">عدد مرات الاستخدام</label><input id="uses" type="number" min="1" value="100"><span class="hint">أقصى عدد للعملاء الذين يمكنهم استخدام الكود.</span></div><button class="vip" onclick="createCode()">إنشاء كود الخصم</button><div id="result" class="result"></div></div>
<div class="card"><h2>سجل الأكواد</h2><button onclick="loadCodes()">تحديث السجل</button><div id="codes"></div></div></div>
<script>const headers=()=>({'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value.trim()});async function login(){let r=await fetch('/owner/api/codes',{headers:headers()});let d=await r.json();document.getElementById('status').textContent=r.ok?'تم الدخول بنجاح ✓':(d.error||'تعذر الدخول');if(r.ok)render(d)}async function createCode(){let r=await fetch('/owner/api/codes',{method:'POST',headers:headers(),body:JSON.stringify({recipientName:document.getElementById('recipient').value,customCode:document.getElementById('customCode').value,discountPercent:+document.getElementById('discount').value,validityDays:+document.getElementById('validityDays').value,maxUses:+document.getElementById('uses').value})});let d=await r.json();document.getElementById('result').textContent=r.ok?(d.renewed?'تم تجديد الكود: ':'تم إنشاء الكود: ')+d.code+'\\nالحملة: '+(d.recipientName||'غير محدد')+'\\nالخصم: '+d.discountPercent+'%':(d.error||'تعذر إنشاء الكود');if(r.ok)loadCodes()}async function loadCodes(){let r=await fetch('/owner/api/codes',{headers:headers()});let d=await r.json();if(r.ok)render(d);else document.getElementById('codes').textContent=d.error||'تعذر تحميل الأكواد'}function render(data){let box=document.getElementById('codes');box.innerHTML=data.length?data.map(c=>`<div class="card code"><b>${c.code_prefix}...</b><p><b>الحملة أو المعلن:</b> ${c.recipient_name||'غير محدد'}</p><p><b>نسبة الخصم:</b> ${c.discount_percent||0}%</p><p><b>مرات الاستخدام:</b> ${c.used_count} من ${c.max_uses}</p><p><b>تاريخ انتهاء الكود:</b> ${c.expires_at||'بدون انتهاء'}</p><p><b>النوع:</b> ${c.code_kind==='discount'?'كود خصم':'كود قديم'}</p></div>`).join(''):'لا توجد أكواد بعد'}</script></body></html>"""
            )
            return
        if method == "GET" and path == "/health":
            self._send(200, {"status": "ok", "service": "khdoom-api"})
            return
        if path == "/owner/api/ads" and method == "GET":
            self._owner()
            with db() as connection:
                purge_expired_ads(connection)
                rows = connection.execute(
                    """SELECT advertisements.id,advertisements.title,advertisements.message,
                              advertisements.contact,advertisements.active,advertisements.approved,
                              advertisements.created_at,advertisements.approved_at,advertisements.expires_at,organizations.name AS organization_name
                       FROM advertisements
                       JOIN organizations ON organizations.id=advertisements.organization_id
                       ORDER BY advertisements.id DESC"""
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
            return
        if path.startswith("/owner/api/ads/") and method == "PUT":
            self._owner()
            try:
                advertisement_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                raise ApiError(400, "رقم الإعلان غير صحيح")
            data = self._body()
            action = str(data.get("action", "")).strip()
            if action not in ("approve", "reject"):
                raise ApiError(400, "اختر قبول الإعلان أو رفضه")
            approved = 1 if action == "approve" else 0
            active = 1 if action == "approve" else 0
            duration_days = max(1, min(int(data.get("durationDays", 30)), 3650))
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=duration_days)
            ).isoformat() if approved else None
            with db() as connection:
                cursor = connection.execute(
                    "UPDATE advertisements SET approved=?,active=?,approved_at=?,expires_at=? WHERE id=?",
                    (approved, active, now() if approved else None, expires_at, advertisement_id),
                )
                connection.commit()
            if cursor.rowcount == 0:
                raise ApiError(404, "الإعلان غير موجود")
            self._send(200, {"saved": True, "approved": bool(approved), "expiresAt": expires_at})
            return
        if path == "/owner/api/codes" and method == "POST":
            self._owner()
            data = self._body()
            discount_percent = max(1, min(int(data.get("discountPercent", 15)), 90))
            max_uses = max(1, min(int(data.get("maxUses", 1)), 10000))
            validity_days = max(1, min(int(data.get("validityDays", 30)), 3650))
            custom_code = str(data.get("customCode", "")).strip().upper()
            if custom_code and (len(custom_code) < 2 or len(custom_code) > 40):
                raise ApiError(400, "كود الخصم يجب أن يكون من خانتين إلى 40 خانة")
            if custom_code and not all(c.isalnum() or c in "-_" for c in custom_code):
                raise ApiError(400, "استخدم في الكود حروفًا وأرقامًا وشرطة فقط")
            code = custom_code or f"SAVE{discount_percent}-{secrets.token_hex(3).upper()}"
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            expires_at = (datetime.now(timezone.utc) + timedelta(days=validity_days)).isoformat()
            recipient_name = str(data.get("recipientName", "")).strip()
            renewed = False
            with db() as connection:
                existing = connection.execute(
                    "SELECT id FROM activation_codes WHERE code_hash=?",
                    (code_hash,),
                ).fetchone()
                if existing:
                    renewed = True
                    connection.execute(
                        """UPDATE activation_codes
                           SET code_prefix=?,package='basic',duration_days=0,max_uses=?,used_count=0,
                               expires_at=?,active=1,recipient_name=?,assigned_username='',
                               code_kind='discount',discount_percent=?,created_at=?
                           WHERE id=?""",
                        (code[:10], max_uses, expires_at, recipient_name, discount_percent, now(), existing["id"]),
                    )
                else:
                    connection.execute(
                        """INSERT INTO activation_codes(code_hash,code_prefix,package,duration_days,max_uses,expires_at,recipient_name,assigned_username,code_kind,discount_percent,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (code_hash, code[:10], "basic", 0, max_uses, expires_at, recipient_name, "", "discount", discount_percent, now()),
                    )
            self._send(200 if renewed else 201, {"code": code, "renewed": renewed, "recipientName": recipient_name, "discountPercent": discount_percent, "maxUses": max_uses, "codeExpiresAt": expires_at})
            return
        if path == "/owner/api/codes" and method == "GET":
            self._owner()
            with db() as connection:
                rows = connection.execute(
                    """SELECT id,code_prefix,recipient_name,assigned_username,package,duration_days,max_uses,used_count,expires_at,active,code_kind,discount_percent,created_at
                       FROM activation_codes ORDER BY id DESC"""
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
            return
        if path == "/owner/api/subscription-requests" and method == "GET":
            self._owner()
            with db() as connection:
                rows = connection.execute(
                    """SELECT subscription_requests.id,subscription_requests.requested_package,
                              subscription_requests.discount_code,subscription_requests.discount_percent,
                              subscription_requests.status,subscription_requests.created_at,
                              subscription_requests.processed_at,organizations.name AS organization_name,
                              organizations.phone,subscriptions.package AS current_package
                       FROM subscription_requests
                       JOIN organizations ON organizations.id=subscription_requests.organization_id
                       JOIN subscriptions ON subscriptions.organization_id=organizations.id
                       ORDER BY CASE subscription_requests.status WHEN 'pending' THEN 0 ELSE 1 END,
                                subscription_requests.id DESC"""
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
            return
        if path.startswith("/owner/api/subscription-requests/") and method == "PUT":
            self._owner()
            try:
                request_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                raise ApiError(400, "رقم طلب الترقية غير صحيح")
            data = self._body()
            action = str(data.get("action", "")).strip()
            if action not in ("approve", "reject"):
                raise ApiError(400, "اختر قبول الطلب أو رفضه")
            with db() as connection:
                request_row = connection.execute(
                    "SELECT * FROM subscription_requests WHERE id=? AND status='pending'",
                    (request_id,),
                ).fetchone()
                if request_row is None:
                    raise ApiError(404, "طلب الترقية غير موجود أو تمت معالجته")
                if action == "approve":
                    days = max(1, min(int(data.get("durationDays", 30)), 3650))
                    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
                    connection.execute(
                        "UPDATE subscriptions SET package=?,starts_at=?,expires_at=? WHERE organization_id=?",
                        (request_row["requested_package"], now(), expires_at, request_row["organization_id"]),
                    )
                    if request_row["discount_code"]:
                        code_hash = hashlib.sha256(request_row["discount_code"].encode()).hexdigest()
                        connection.execute(
                            "UPDATE activation_codes SET used_count=used_count+1 WHERE code_hash=? AND used_count<max_uses",
                            (code_hash,),
                        )
                connection.execute(
                    "UPDATE subscription_requests SET status=?,processed_at=? WHERE id=?",
                    ("approved" if action == "approve" else "rejected", now(), request_id),
                )
                connection.commit()
            self._send(200, {"saved": True, "status": "approved" if action == "approve" else "rejected"})
            return
        if path == "/owner/api/organizations" and method == "GET":
            self._owner()
            with db() as connection:
                rows = connection.execute(
                    """SELECT organizations.id,organizations.name,organizations.phone,organizations.public_chat_token,subscriptions.package,subscriptions.expires_at,
                              maintenance_modes.chat_until,maintenance_modes.appointments_until,maintenance_modes.message AS maintenance_message, (SELECT expires_at FROM service_maintenance WHERE service_maintenance.organization_id=organizations.id AND service='assistant') AS assistant_until, (SELECT COUNT(*) FROM ai_usage WHERE ai_usage.organization_id=organizations.id AND substr(ai_usage.created_at,1,10)=substr(CAST(CURRENT_TIMESTAMP AS TEXT),1,10)) AS ai_today, (SELECT COUNT(*) FROM ai_usage WHERE ai_usage.organization_id=organizations.id AND substr(ai_usage.created_at,1,7)=substr(CAST(CURRENT_TIMESTAMP AS TEXT),1,7)) AS ai_month, (SELECT daily_limit FROM ai_limits WHERE ai_limits.organization_id=organizations.id) AS custom_ai_limit
                       FROM organizations JOIN subscriptions ON subscriptions.organization_id=organizations.id
                       LEFT JOIN maintenance_modes ON maintenance_modes.organization_id=organizations.id
                       ORDER BY organizations.id DESC"""
                ).fetchall()
                organization_items = []
                for row in rows:
                    item = dict(row)
                    default_limit = {"free": 5, "basic": 30, "vip": 100}.get(item["package"], 5)
                    daily_limit = int(item.get("custom_ai_limit") or default_limit)
                    ai_today = int(item.get("ai_today") or 0)
                    ai_month = int(item.get("ai_month") or 0)
                    usage_percent = round((ai_today / daily_limit) * 100) if daily_limit else 0
                    item["ai_daily_limit"] = daily_limit
                    item["ai_usage_percent"] = usage_percent
                    item["ai_usage_status"] = "danger" if usage_percent >= 100 else "warning" if usage_percent >= 80 else "normal"
                    item["ai_estimated_cost_usd"] = round(ai_month * AI_ESTIMATED_COST_PER_REQUEST_USD, 4)
                    item["ai_estimated_cost_sar"] = round(item["ai_estimated_cost_usd"] * 3.75, 2)
                    organization_items.append(item)
            self._send(200, organization_items)
            return
        if path.startswith("/owner/api/organizations/") and path.endswith("/ai-limit") and method == "PUT":
            self._owner()
            organization_id = int(path.split("/")[4])
            data = self._body()
            daily_limit = max(1, min(int(data.get("dailyLimit", 30)), 100000))
            with db() as connection:
                connection.execute("INSERT INTO ai_limits(organization_id,daily_limit) VALUES(?,?) ON CONFLICT(organization_id) DO UPDATE SET daily_limit=excluded.daily_limit", (organization_id, daily_limit))
                connection.commit()
            self._send(200, {"saved": True, "dailyLimit": daily_limit})
            return
        if path.startswith("/owner/api/organizations/") and path.endswith("/maintenance") and method == "PUT":
            self._owner()
            organization_id = int(path.split("/")[4])
            data = self._body()
            service = str(data.get("service", ""))
            if service not in ("chat", "appointments", "assistant"):
                raise ApiError(400, "اختر خدمة صحيحة")
            enabled = data.get("enabled") is True
            hours = max(1, min(int(data.get("hours", 24)), 24 * 365))
            until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat() if enabled else None
            message = str(data.get("message", "الخدمة تحت الصيانة مؤقتًا")).strip()[:300] or "الخدمة تحت الصيانة مؤقتًا"
            with db() as connection:
                if service == "assistant":
                    connection.execute(
                        """INSERT INTO service_maintenance(organization_id,service,expires_at,message) VALUES(?,?,?,?)
                           ON CONFLICT(organization_id,service) DO UPDATE SET expires_at=excluded.expires_at,message=excluded.message""",
                        (organization_id, service, until, message),
                    )
                else:
                    column = "chat_until" if service == "chat" else "appointments_until"
                    connection.execute("INSERT INTO maintenance_modes(organization_id,message) VALUES(?,?) ON CONFLICT(organization_id) DO UPDATE SET message=excluded.message", (organization_id, message))
                    connection.execute(f"UPDATE maintenance_modes SET {column}=?,message=? WHERE organization_id=?", (until, message, organization_id))
                service_name = {"chat": "شات العملاء", "appointments": "المواعيد", "assistant": "مساعد المؤسسة"}[service]
                audit_log(
                    connection,
                    organization_id,
                    None,
                    "organization_maintenance",
                    f"تم {'إيقاف' if enabled else 'تشغيل'} {service_name} {'حتى ' + until if enabled and until else ''}".strip(),
                    "security",
                    service,
                )
                connection.commit()
            self._send(200, {"saved": True, "service": service, "enabled": enabled, "until": until})
            return
        if path.startswith("/owner/api/organizations/") and path.endswith("/package") and method == "PUT":
            self._owner()
            organization_id = int(path.split("/")[4])
            data = self._body()
            package = str(data.get("package", ""))
            if package not in ("free", "basic", "vip"):
                raise ApiError(400, "اختر باقة صحيحة")
            days = max(1, min(int(data.get("durationDays", 30)), 3650))
            expires_at = None if package == "free" else (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            with db() as connection:
                connection.execute("UPDATE subscriptions SET package=?,starts_at=?,expires_at=? WHERE organization_id=?", (package, now(), expires_at, organization_id))
            self._send(200, {"saved": True, "package": package, "expiresAt": expires_at})
            return
        if method == "POST" and path == "/api/discount-code/preview":
            data = self._body()
            raw_code = str(data.get("code", "")).strip().upper()
            package = str(data.get("package", "")).strip().lower()
            if package not in ("basic", "vip"):
                raise ApiError(400, "اختر باقة صحيحة")
            code_hash = hashlib.sha256(raw_code.encode()).hexdigest()
            with db() as connection:
                code = connection.execute(
                    """SELECT * FROM activation_codes WHERE code_hash=? AND active=1
                       AND code_kind='discount' AND used_count<max_uses
                       AND (expires_at IS NULL OR expires_at>?)""",
                    (code_hash, now()),
                ).fetchone()
            if code is None:
                raise ApiError(400, "كود الخصم غير صحيح أو منتهي")
            original_price = 49 if package == "basic" else 99
            discount_percent = int(code["discount_percent"])
            discounted_price = round(original_price * (100 - discount_percent) / 100, 2)
            self._send(200, {"valid": True, "package": package, "discountPercent": discount_percent, "originalPrice": original_price, "discountedPrice": discounted_price})
            return
        if method == "POST" and path == "/api/register":
            data = self._body()
            required = ("name", "username", "phone", "password", "organizationName")
            missing = [key for key in required if not str(data.get(key, "")).strip()]
            if missing:
                raise ApiError(400, "حقول مطلوبة ناقصة: " + ", ".join(missing))
            if len(str(data["password"])) < 8:
                raise ApiError(400, "كلمة المرور يجب أن تكون 8 خانات على الأقل")
            password_hash, salt = hash_password(str(data["password"]))
            activation_code = str(data.get("activationCode", "")).strip().upper()
            try:
                with db() as connection:
                    code = None
                    if activation_code:
                        code_hash = hashlib.sha256(activation_code.encode()).hexdigest()
                        code = connection.execute(
                            """SELECT * FROM activation_codes WHERE code_hash=? AND active=1
                               AND code_kind='activation' AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)""",
                            (code_hash, now()),
                        ).fetchone()
                        if code is None:
                            raise ApiError(400, "كود التفعيل غير صحيح أو منتهي")
                        if code["assigned_username"] and code["assigned_username"].lower() != str(data["username"]).strip().lower():
                            raise ApiError(403, "هذا الكود مخصص لمستخدم آخر")
                    cursor = connection.execute(
                        "INSERT INTO organizations(name,activity,phone,public_chat_token,created_at) VALUES(?,?,?,?,?)",
                        (str(data["organizationName"]).strip(), str(data.get("activity", "")).strip(), str(data["phone"]).strip(), secrets.token_urlsafe(18), now()),
                    )
                    organization_id = cursor.lastrowid
                    cursor = connection.execute(
                        """INSERT INTO users(organization_id,name,username,phone,email,password_hash,password_salt,role,permissions,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (organization_id, str(data["name"]).strip(), str(data["username"]).strip().lower(), str(data["phone"]).strip(), str(data.get("email", "")).strip().lower(), password_hash, salt, "admin", "{}", now()),
                    )
                    package = code["package"] if code else "free"
                    package_expires = (datetime.now(timezone.utc) + timedelta(days=code["duration_days"])).isoformat() if code else None
                    connection.execute(
                        "INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?)",
                        (organization_id, package, now(), package_expires),
                    )
                    if code:
                        connection.execute("UPDATE activation_codes SET used_count=used_count+1 WHERE id=?", (code["id"],))
                    token = issue_token(
                        connection, cursor.lastrowid,
                        str(data.get("deviceId", "")).strip(),
                        str(data.get("deviceName", "جهاز غير معروف")).strip(),
                    )
                self._send(201, {"token": token, "organizationId": organization_id, "package": package, "expiresAt": package_expires})
            except DB_INTEGRITY_ERRORS:
                raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
            return
        if method == "POST" and path == "/api/login":
            data = self._body()
            with db() as connection:
                user = connection.execute(
                    "SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1",
                    (str(data.get("username", "")).strip().lower(),),
                ).fetchone()
                if user is None:
                    raise ApiError(401, "اسم المستخدم أو كلمة المرور غير صحيحة")
                if not verify_password(str(data.get("password", "")), user["password_hash"], user["password_salt"]):
                    client_ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
                    audit_log(connection, user["organization_id"], user["id"], "failed_login", f"محاولة دخول فاشلة من {str(data.get('deviceName', 'جهاز غير معروف')).strip()} — الاتصال: {client_ip}", "security")
                    ten_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
                    recent_failures = connection.execute(
                        "SELECT COUNT(*) AS count FROM audit_logs WHERE organization_id=? AND actor_user_id=? AND action='failed_login' AND created_at>=?",
                        (user["organization_id"], user["id"], ten_minutes_ago),
                    ).fetchone()["count"]
                    if recent_failures == 3:
                        audit_log(
                            connection,
                            user["organization_id"],
                            user["id"],
                            "suspicious_login",
                            f"تنبيه: 3 محاولات دخول فاشلة خلال 10 دقائق — الجهاز: {str(data.get('deviceName', 'جهاز غير معروف')).strip()}",
                            "security",
                        )
                    connection.commit()
                    raise ApiError(401, "اسم المستخدم أو كلمة المرور غير صحيحة")
                device_id = str(data.get("deviceId", "")).strip()
                blocked_device = connection.execute(
                    "SELECT 1 FROM blocked_devices WHERE organization_id=? AND device_id=?",
                    (user["organization_id"], device_id),
                ).fetchone() if device_id else None
                if blocked_device is not None:
                    audit_log(connection, user["organization_id"], user["id"], "blocked_device_login", f"محاولة دخول من جهاز محظور: {str(data.get('deviceName', 'جهاز غير معروف')).strip()}", "security", device_id[:40])
                    connection.commit()
                    raise ApiError(403, "هذا الجهاز محظور. تواصل مع مالك المؤسسة")
                known_device = connection.execute(
                    """SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                       WHERE users.organization_id=? AND sessions.device_id=? LIMIT 1""",
                    (user["organization_id"], device_id),
                ).fetchone() if device_id else None
                token = issue_token(
                    connection, user["id"],
                    str(data.get("deviceId", "")).strip(),
                    str(data.get("deviceName", "جهاز غير معروف")).strip(),
                )
                package = connection.execute("SELECT package FROM subscriptions WHERE organization_id=?", (user["organization_id"],)).fetchone()["package"]
                if known_device is None:
                    audit_log(connection, user["organization_id"], user["id"], "new_device", f"دخول من جهاز جديد: {str(data.get('deviceName', 'جهاز غير معروف')).strip()}", "security")
                else:
                    audit_log(connection, user["organization_id"], user["id"], "login", f"تسجيل دخول من {str(data.get('deviceName', 'جهاز غير معروف')).strip()}", "session")
                connection.commit()
                self._send(200, {"token": token, "user": {"id": user["id"], "name": user["name"], "role": user["role"], "permissions": json.loads(user["permissions"])}, "organizationId": user["organization_id"], "package": package})
            return
        if method == "POST" and path == "/api/activate-code-public":
            data = self._body()
            username = str(data.get("username", "")).strip().lower()
            raw_code = str(data.get("code", "")).strip().upper()
            if not username or not raw_code:
                raise ApiError(400, "اكتب اسم المستخدم وكود التفعيل")
            code_hash = hashlib.sha256(raw_code.encode()).hexdigest()
            with db() as connection:
                target_user = connection.execute(
                    "SELECT id,organization_id,username FROM users WHERE username=? COLLATE NOCASE AND active=1",
                    (username,),
                ).fetchone()
                if target_user is None:
                    raise ApiError(404, "اسم المستخدم غير موجود في الخادم")
                code = connection.execute(
                    """SELECT * FROM activation_codes WHERE code_hash=? AND active=1
                       AND code_kind='activation' AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)""",
                    (code_hash, now()),
                ).fetchone()
                if code is None:
                    raise ApiError(400, "كود التفعيل غير صحيح أو منتهي")
                if code["assigned_username"] and code["assigned_username"].lower() != username:
                    raise ApiError(403, "هذا الكود مخصص لمستخدم آخر")
                package_expires = (datetime.now(timezone.utc) + timedelta(days=code["duration_days"])).isoformat()
                connection.execute("UPDATE activation_codes SET used_count=used_count+1 WHERE id=?", (code["id"],))
                connection.execute("UPDATE subscriptions SET package=?,starts_at=?,expires_at=? WHERE organization_id=?", (code["package"], now(), package_expires, target_user["organization_id"]))
                connection.commit()
            self._send(200, {"activated": True, "package": code["package"], "expiresAt": package_expires})
            return

        with db() as connection:
            user = self._user(connection)
            organization_id = user["organization_id"]
            if path == "/api/maintenance-status" and method == "GET":
                self._send(200, {"assistant": service_maintenance_status(connection, organization_id, "assistant")})
                return
            if path == "/api/audit-logs" and method == "GET":
                require_permission(user, "viewAuditLog")
                rows = connection.execute(
                    """SELECT audit_logs.id,audit_logs.action,audit_logs.target_type,
                              audit_logs.target_id,audit_logs.summary,audit_logs.created_at,
                              users.name AS actor_name,users.username AS actor_username
                       FROM audit_logs LEFT JOIN users ON users.id=audit_logs.actor_user_id
                       WHERE audit_logs.organization_id=?
                       ORDER BY audit_logs.id DESC LIMIT 300""",
                    (organization_id,),
                ).fetchall()
                self._send(200, [dict(row) for row in rows])
                return
            if path == "/api/security/sessions" and method == "GET":
                if user["role"] != "admin":
                    raise ApiError(403, "قائمة الأجهزة متاحة للمالك فقط")
                current_hash = hashlib.sha256(
                    self.headers.get("Authorization", "")[7:].encode()
                ).hexdigest()
                rows = connection.execute(
                    """SELECT sessions.token_hash,sessions.device_id,sessions.device_name,
                              sessions.trusted,sessions.last_seen_at,sessions.created_at,
                              sessions.expires_at,users.name AS user_name,users.username
                       FROM sessions JOIN users ON users.id=sessions.user_id
                       WHERE users.organization_id=? AND sessions.expires_at>?
                       ORDER BY sessions.last_seen_at DESC,sessions.created_at DESC""",
                    (organization_id, now()),
                ).fetchall()
                result = []
                for row in rows:
                    item = dict(row)
                    item["id"] = item.pop("token_hash")
                    item["current"] = item["id"] == current_hash
                    item["trusted"] = bool(item["trusted"])
                    result.append(item)
                self._send(200, result)
                return
            if path.startswith("/api/security/sessions/") and method == "PUT":
                if user["role"] != "admin":
                    raise ApiError(403, "إدارة الأجهزة متاحة للمالك فقط")
                session_id = path.rsplit("/", 1)[1]
                data = self._body()
                trusted = 1 if data.get("trusted") is True else 0
                cursor = connection.execute(
                    """UPDATE sessions SET trusted=? WHERE token_hash=? AND user_id IN
                       (SELECT id FROM users WHERE organization_id=?)""",
                    (trusted, session_id, organization_id),
                )
                if cursor.rowcount == 0:
                    raise ApiError(404, "الجهاز غير موجود")
                audit_log(connection, organization_id, user["id"], "device_trust_updated", "تم توثيق جهاز" if trusted else "تم إلغاء توثيق جهاز", "security", session_id[:12])
                connection.commit()
                self._send(200, {"saved": True, "trusted": bool(trusted)})
                return
            if path.startswith("/api/security/sessions/") and method == "DELETE":
                if user["role"] != "admin":
                    raise ApiError(403, "إدارة الأجهزة متاحة للمالك فقط")
                session_id = path.rsplit("/", 1)[1]
                connection.execute(
                    """DELETE FROM sessions WHERE token_hash=? AND user_id IN
                       (SELECT id FROM users WHERE organization_id=?)""",
                    (session_id, organization_id),
                )
                audit_log(connection, organization_id, user["id"], "device_disconnected", "تم فصل جهاز من حساب المؤسسة", "security", session_id[:12])
                connection.commit()
                self._send(200, {"disconnected": True})
                return
            if path == "/api/security/logout-all" and method == "POST":
                if user["role"] != "admin":
                    raise ApiError(403, "إدارة الأجهزة متاحة للمالك فقط")
                current_hash = hashlib.sha256(
                    self.headers.get("Authorization", "")[7:].encode()
                ).hexdigest()
                data = self._body()
                keep_current = data.get("keepCurrent", True) is not False
                query = """DELETE FROM sessions WHERE user_id IN
                           (SELECT id FROM users WHERE organization_id=?)"""
                params: tuple[Any, ...] = (organization_id,)
                if keep_current:
                    query += " AND token_hash<>?"
                    params = (organization_id, current_hash)
                cursor = connection.execute(query, params)
                audit_log(connection, organization_id, user["id"], "sessions_disconnected", f"تم فصل {cursor.rowcount} جلسة من حساب المؤسسة", "security")
                connection.commit()
                self._send(200, {"disconnected": cursor.rowcount})
                return
            if path == "/api/ai-training" and method == "GET":
                rows = connection.execute(
                    "SELECT employee_type,content,updated_at FROM ai_training WHERE organization_id=?",
                    (organization_id,),
                ).fetchall()
                self._send(200, [dict(row) for row in rows])
                return
            if path == "/api/ai-training" and method == "PUT":
                data = self._body()
                employee_type = str(data.get("employeeType", "")).strip()
                allowed_types = {"shared", "assistant", "chat", "reception", "whatsapp", "calls", "commercial_research"}
                if employee_type not in allowed_types:
                    raise ApiError(400, "نوع موظف AI غير صحيح")
                content = str(data.get("content", "")).strip()[:12000]
                connection.execute(
                    """INSERT INTO ai_training(organization_id,employee_type,content,updated_at)
                       VALUES(?,?,?,?) ON CONFLICT(organization_id,employee_type)
                       DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at""",
                    (organization_id, employee_type, content, now()),
                )
                connection.commit()
                self._send(200, {"saved": True})
                return
            if path == "/api/ai-training/messages" and method == "GET":
                rows = connection.execute(
                    """SELECT id,employee_type,sender,message,created_at FROM ai_training_messages
                       WHERE organization_id=? ORDER BY id DESC LIMIT 300""",
                    (organization_id,),
                ).fetchall()
                self._send(200, [dict(row) for row in reversed(rows)])
                return
            if path == "/api/ai-training/chat" and method == "POST":
                data = self._body()
                employee_type = str(data.get("employeeType", "")).strip()
                message = str(data.get("message", "")).strip()[:2000]
                allowed_types = {"shared", "assistant", "chat", "reception", "whatsapp", "calls", "commercial_research"}
                if employee_type not in allowed_types or not message:
                    raise ApiError(400, "رسالة التدريب غير صحيحة")
                connection.execute(
                    "INSERT INTO ai_training_messages(organization_id,employee_type,sender,message,created_at) VALUES(?,?,?,?,?)",
                    (organization_id, employee_type, "owner", message, now()),
                )
                normalized = message.lower().strip()
                greeting = normalized.strip(" .،!؟") in {"السلام", "السلام عليكم", "هلا", "مرحبا", "مرحبًا", "صباح الخير", "مساء الخير"}
                is_question = "?" in message or "؟" in message or any(normalized.startswith(word) for word in ("وش", "شنو", "ماذا", "ما ", "هل", "كم", "متى", "وين", "أين", "كيف"))
                explicit_learning = any(
                    normalized.startswith(word)
                    for word in (
                        "احفظ", "حفظ", "خزن", "خزّن", "تعلم", "تعلّم",
                        "علمني", "اعلمك", "سجل المعلومة", "سجّل المعلومة",
                    )
                )
                should_learn = explicit_learning
                training = ai_training_text(connection, organization_id, employee_type)
                action = "answered"
                if greeting:
                    text = "وعليكم السلام ورحمة الله 👋 كيف أقدر أخدمك؟"
                elif any(word in normalized for word in ("انسَ", "انسى", "احذف", "امسح")):
                    text = "حتى لا أحذف معلومة بالخطأ، اختر المعلومة من قائمة المعلومات المحفوظة ثم أكد حذفها."
                    action = "delete_confirmation_required"
                elif should_learn:
                    fact = re.sub(
                        r"^(احفظ|حفظ|خزن|خزّن|تعلم|تعلّم|علمني|اعلمك|سجل المعلومة|سجّل المعلومة)\s*[:：-]?\s*",
                        "", message,
                    ).strip()
                    saved_facts = {
                        re.sub(r"^[•\-]\s*", "", line).strip().lower()
                        for line in training.splitlines()
                        if line.strip()
                    }
                    if not fact:
                        text = "اكتب المعلومة بعد كلمة «احفظ»، مثل: احفظ: سعر متر الزجاج 150 ريال."
                        action = "needs_fact"
                    elif fact.lower() in saved_facts:
                        text = f"هذه المعلومة محفوظة عندي من قبل: {fact} ✓"
                        action = "already_saved"
                    else:
                        new_content = (training + "\n• " + fact).strip() if training else "• " + fact
                        connection.execute(
                            """INSERT INTO ai_training(organization_id,employee_type,content,updated_at) VALUES(?,?,?,?)
                               ON CONFLICT(organization_id,employee_type) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at""",
                            (organization_id, employee_type, new_content[:12000], now()),
                        )
                        text = f"تمام، تعلمت وحفظت: {fact} ✓"
                        action = "saved"
                else:
                    package, daily_limit, used = ai_allowance(connection, organization_id)
                    system_prompt = "أنت موظف AI ودود خاص بهذه المؤسسة. تحدث بالعربية بشكل طبيعي ومفيد داخل نشاط المؤسسة. استخدم المعلومات المحفوظة عند توفرها. إذا كانت الرسالة طلب خدمة فاسأل عن التفاصيل اللازمة مثل المقاس والموقع والموعد، ولا تدّعي أنك حفظت الرسالة. إذا سأل عن معلومة غير محفوظة فقل بوضوح إنك لا تعرفها بعد."
                    user_prompt = f"المعلومات المحفوظة:\n{training or 'لا توجد معلومات'}\n\nرسالة المستخدم:\n{message}"
                    text = generate_ai_text(system_prompt, user_prompt)
                    connection.execute(
                        "INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)",
                        (organization_id, user["id"], "training_chat", now()),
                    )
                connection.execute(
                    "INSERT INTO ai_training_messages(organization_id,employee_type,sender,message,created_at) VALUES(?,?,?,?,?)",
                    (organization_id, employee_type, "assistant", text, now()),
                )
                connection.commit()
                self._send(200, {"text": text, "action": action})
                return
            if path == "/api/ai/commercial-research" and method == "POST":
                data = self._body()
                keywords = str(data.get("keywords", "")).strip()[:300]
                city = str(data.get("city", "")).strip()[:120]
                research_type = str(data.get("researchType", "عملاء محتملون")).strip()[:80]
                if not keywords or not city:
                    raise ApiError(400, "اكتب مجال البحث والمدينة")
                package, daily_limit, used = ai_allowance(connection, organization_id)
                if package != "vip":
                    raise ApiError(403, "موظف البحث التجاري متاح في باقة VIP")
                training = ai_training_text(connection, organization_id, "commercial_research")
                system_prompt = (
                    "أنت موظف بحث تجاري سعودي. ابحث في الإنترنت عن معلومات حديثة وعلنية فقط. "
                    "أجب بالعربية بوضوح، ولا تخترع أسماء أو أرقامًا. اذكر مصادر أو روابط مفيدة عند توفرها، "
                    "ونبّه أن بيانات الاتصال والأسعار تحتاج تحققًا قبل الاعتماد."
                )
                user_prompt = (
                    f"نوع البحث: {research_type}\nالمجال أو الكلمات: {keywords}\n"
                    f"المدينة: {city}\nتدريب المؤسسة المعتمد:\n{training}\nقدم نتيجة عملية مختصرة من 5 إلى 10 نقاط."
                )
                text = generate_ai_text(system_prompt, user_prompt, web_search=True)
                connection.execute(
                    "INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)",
                    (organization_id, user["id"], "commercial_research", now()),
                )
                connection.commit()
                self._send(200, {"text": text, "remaining": daily_limit - used - 1})
                return
            if path == "/api/ai/reception-reply" and method == "POST":
                data = self._body()
                message = str(data.get("message", "")).strip()[:3000]
                settings = data.get("settings")
                if not message:
                    raise ApiError(400, "اكتب رسالة العميل")
                if not isinstance(settings, dict):
                    settings = {}
                package, daily_limit, used = ai_allowance(connection, organization_id)
                if package not in ("basic", "vip"):
                    raise ApiError(403, "موظف الاستقبال متاح من الباقة الأساسية")
                allowed_keys = ("businessName", "businessInfo", "workingHours", "replyStyle")
                safe_settings = {
                    key: str(settings.get(key, ""))[:1500]
                    for key in allowed_keys
                }
                training = ai_training_text(connection, organization_id, "reception")
                system_prompt = (
                    "أنت موظف استقبال لمؤسسة سعودية. أجب بالعربية وفق بيانات المؤسسة فقط. "
                    "لا تخترع سعرًا أو موعدًا أو خدمة غير مذكورة. إذا نقصت معلومة فقل إن الموظف البشري سيتابع، "
                    "واجمع عند الحاجة اسم العميل ورقم التواصل ونوع الطلب. اجعل الرد مهذبًا ومختصرًا."
                )
                user_prompt = (
                    "إعدادات المؤسسة:\n"
                    + json.dumps(safe_settings, ensure_ascii=False, indent=2)
                    + "\nتدريب المؤسسة المعتمد:\n" + training + f"\nرسالة العميل الحالية:\n{message}"
                )
                text = generate_ai_text(system_prompt, user_prompt)
                connection.execute(
                    "INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)",
                    (organization_id, user["id"], "reception_reply", now()),
                )
                connection.commit()
                self._send(200, {"text": text, "remaining": daily_limit - used - 1})
                return
            if path == "/api/ai/commercial-report" and method == "POST":
                data = self._body()
                report_data = data.get("report")
                if not isinstance(report_data, dict):
                    raise ApiError(400, "بيانات التقرير غير صحيحة")
                package_row = connection.execute(
                    "SELECT package FROM subscriptions WHERE organization_id=?",
                    (organization_id,),
                ).fetchone()
                package = package_row["package"] if package_row else "free"
                default_limit = {"free": 5, "basic": 30, "vip": 100}.get(package, 5)
                custom_limit = connection.execute("SELECT daily_limit FROM ai_limits WHERE organization_id=?", (organization_id,)).fetchone()
                daily_limit = custom_limit["daily_limit"] if custom_limit else default_limit
                day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
                used = connection.execute(
                    "SELECT COUNT(*) AS count FROM ai_usage WHERE organization_id=? AND created_at>=?",
                    (organization_id, day_start),
                ).fetchone()["count"]
                if used >= daily_limit:
                    raise ApiError(429, "تم بلوغ الحد اليومي لموظفي AI")
                safe_report = {
                    str(key)[:60]: str(value)[:2000]
                    for key, value in report_data.items()
                }
                system_prompt = (
                    "أنت موظف متابعة تجارية سعودي محترف. اكتب تقرير متابعة اتصال "
                    "بالعربية الواضحة دون اختلاق معلومات. حافظ على الأرقام والحقائق، "
                    "واجعل التقرير عمليًا ومختصرًا ويتضمن الحالة والخطوة التالية."
                )
                user_prompt = "حوّل البيانات التالية إلى تقرير مهني:\n" + json.dumps(
                    safe_report, ensure_ascii=False, indent=2
                )
                text = generate_ai_text(system_prompt, user_prompt)
                connection.execute(
                    "INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)",
                    (organization_id, user["id"], "commercial_report", now()),
                )
                connection.commit()
                self._send(200, {"text": text, "remaining": daily_limit - used - 1})
                return
            if path == "/api/activate-code" and method == "POST":
                data = self._body()
                raw_code = str(data.get("code", "")).strip().upper()
                code_hash = hashlib.sha256(raw_code.encode()).hexdigest()
                code = connection.execute(
                    """SELECT * FROM activation_codes WHERE code_hash=? AND active=1
                       AND code_kind='activation' AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)""",
                    (code_hash, now()),
                ).fetchone()
                if code is None:
                    raise ApiError(400, "كود التفعيل غير صحيح أو منتهي")
                if code["assigned_username"] and code["assigned_username"].lower() != user["username"].lower():
                    raise ApiError(403, "هذا الكود مخصص لمستخدم آخر")
                package_expires = (datetime.now(timezone.utc) + timedelta(days=code["duration_days"])).isoformat()
                connection.execute("UPDATE activation_codes SET used_count=used_count+1 WHERE id=?", (code["id"],))
                connection.execute("UPDATE subscriptions SET package=?,starts_at=?,expires_at=? WHERE organization_id=?", (code["package"], now(), package_expires, organization_id))
                connection.commit()
                self._send(200, {"activated": True, "package": code["package"], "expiresAt": package_expires})
                return
            if method == "GET" and path == "/api/organization":
                org = connection.execute("""SELECT organizations.id,organizations.name,organizations.activity,organizations.phone,organizations.created_at,subscriptions.package,subscriptions.expires_at FROM organizations JOIN subscriptions ON subscriptions.organization_id=organizations.id WHERE organizations.id=?""", (organization_id,)).fetchone()
                self._send(200, dict(org))
                return
            if method == "PUT" and path == "/api/organization":
                require_permission(user, "manageSettings")
                data = self._body()
                connection.execute("UPDATE organizations SET name=?,activity=?,phone=? WHERE id=?", (str(data.get("name", "")).strip(), str(data.get("activity", "")).strip(), str(data.get("phone", "")).strip(), organization_id))
                audit_log(connection, organization_id, user["id"], "organization_updated", "تم تعديل بيانات المؤسسة", "organization", organization_id)
                connection.commit()
                self._send(200, {"saved": True})
                return
            if path == "/api/employees" and method == "GET":
                require_permission(user, "viewEmployees", "manageEmployees", "employees")
                rows = connection.execute(
                    """SELECT id,name,username,phone,email,job_title,permissions,active,created_at
                       FROM users WHERE organization_id=? AND role='employee' ORDER BY id DESC""",
                    (organization_id,),
                ).fetchall()
                result = []
                for row in rows:
                    item = dict(row)
                    item["permissions"] = json.loads(item["permissions"])
                    item["active"] = bool(item["active"])
                    result.append(item)
                self._send(200, result)
                return
            if path == "/api/employees" and method == "POST":
                require_permission(user, "manageEmployees")
                data = self._body()
                package_row = connection.execute(
                    "SELECT package FROM subscriptions WHERE organization_id=?",
                    (organization_id,),
                ).fetchone()
                package = package_row["package"] if package_row else "free"
                employee_limit = {"free": 1, "basic": 5}.get(package)
                employee_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM users WHERE organization_id=? AND role='employee'",
                    (organization_id,),
                ).fetchone()["count"]
                if employee_limit is not None and employee_count >= employee_limit:
                    message = "الباقة المجانية تسمح بموظف واحد فقط" if package == "free" else "الباقة الأساسية تسمح بخمسة موظفين فقط"
                    raise ApiError(403, message)
                if len(str(data.get("password", ""))) < 8:
                    raise ApiError(400, "كلمة مرور الموظف 8 خانات على الأقل")
                password_hash, salt = hash_password(str(data["password"]))
                try:
                    cursor = connection.execute(
                        """INSERT INTO users(organization_id,name,username,phone,email,password_hash,password_salt,role,job_title,permissions,active,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (organization_id, str(data.get("name", "")).strip(), str(data.get("username", "")).strip().lower(), str(data.get("phone", "")).strip(), str(data.get("email", "")).strip().lower(), password_hash, salt, "employee", str(data.get("role", "موظف")).strip(), json.dumps(data.get("permissions", {}), ensure_ascii=False), 1 if data.get("active", True) else 0, now()),
                    )
                    audit_log(connection, organization_id, user["id"], "employee_created", f"تمت إضافة الموظف {str(data.get('name', '')).strip()}", "employee", cursor.lastrowid)
                    connection.commit()
                    self._send(201, {"id": cursor.lastrowid})
                except DB_INTEGRITY_ERRORS:
                    raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
                return
            if path.startswith("/api/employees/") and method == "PUT":
                require_permission(user, "manageEmployees")
                employee_id = int(path.rsplit("/", 1)[1])
                data = self._body()
                name = str(data.get("name", "")).strip()
                username = str(data.get("username", "")).strip().lower()
                if not name or len(username) < 3:
                    raise ApiError(400, "اكتب اسم الموظف واسم مستخدم واضح")
                password = str(data.get("password", ""))
                if password and len(password) < 8:
                    raise ApiError(400, "كلمة مرور الموظف 8 خانات على الأقل")
                values = (
                    name, username, str(data.get("phone", "")).strip(),
                    str(data.get("email", "")).strip().lower(),
                    str(data.get("role", "موظف")).strip(),
                    json.dumps(data.get("permissions", {}), ensure_ascii=False),
                    1 if data.get("active", True) else 0,
                )
                try:
                    if password:
                        password_hash, salt = hash_password(password)
                        cursor = connection.execute(
                            """UPDATE users SET name=?,username=?,phone=?,email=?,job_title=?,permissions=?,active=?,password_hash=?,password_salt=?
                               WHERE id=? AND organization_id=? AND role='employee'""",
                            values + (password_hash, salt, employee_id, organization_id),
                        )
                    else:
                        cursor = connection.execute(
                            """UPDATE users SET name=?,username=?,phone=?,email=?,job_title=?,permissions=?,active=?
                               WHERE id=? AND organization_id=? AND role='employee'""",
                            values + (employee_id, organization_id),
                        )
                    if cursor.rowcount == 0:
                        raise ApiError(404, "الموظف غير موجود")
                    if data.get("active", True) is False:
                        connection.execute(
                            "DELETE FROM sessions WHERE user_id=?",
                            (employee_id,),
                        )
                    audit_log(connection, organization_id, user["id"], "employee_updated", f"تم تعديل الموظف {name}", "employee", employee_id)
                    connection.commit()
                except DB_INTEGRITY_ERRORS:
                    raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
                self._send(200, {"saved": True})
                return
            if path.startswith("/api/employees/") and method == "DELETE":
                require_permission(user, "deleteEmployees")
                employee_id = int(path.rsplit("/", 1)[1])
                employee = connection.execute("SELECT name FROM users WHERE id=? AND organization_id=? AND role='employee'", (employee_id, organization_id)).fetchone()
                connection.execute(
                    "DELETE FROM users WHERE id=? AND organization_id=? AND role='employee'",
                    (employee_id, organization_id),
                )
                audit_log(connection, organization_id, user["id"], "employee_deleted", f"تم حذف الموظف {employee['name'] if employee else employee_id}", "employee", employee_id)
                connection.commit()
                self._send(200, {"deleted": True})
                return
            if path == "/api/appointments" and method == "GET":
                require_permission(user, "viewAppointments", "manageAppointments", "appointments")
                rows = connection.execute(
                    """SELECT id,request_type,title,customer_name,phone,notes,scheduled_at,
                              status,source,created_at,updated_at
                       FROM appointment_requests WHERE organization_id=? ORDER BY scheduled_at ASC""",
                    (organization_id,),
                ).fetchall()
                self._send(200, [dict(row) for row in rows])
                return
            if path.startswith("/api/appointments/") and method == "PUT":
                require_permission(user, "manageAppointments")
                appointment_id = int(path.rsplit("/", 1)[1])
                data = self._body()
                status = str(data.get("status", "")).strip()
                if status not in ("pending", "accepted", "completed", "rejected"):
                    raise ApiError(400, "حالة الطلب غير صحيحة")
                appointment = connection.execute(
                    "SELECT * FROM appointment_requests WHERE id=? AND organization_id=?",
                    (appointment_id, organization_id),
                ).fetchone()
                if appointment is None:
                    raise ApiError(404, "طلب الموعد غير موجود")
                request_type = str(data.get("type", appointment["request_type"])).strip()
                if request_type not in ("موعد مقاس", "موعد صيانة", "طلب عميل"):
                    raise ApiError(400, "نوع الطلب غير صحيح")
                title = str(data.get("title", appointment["title"])).strip()[:160]
                customer_name = str(data.get("customer", appointment["customer_name"])).strip()[:120]
                phone = str(data.get("phone", appointment["phone"])).strip()[:40]
                notes = str(data.get("notes", appointment["notes"])).strip()[:1000]
                scheduled_at = str(data.get("scheduledAt", appointment["scheduled_at"])).strip()
                try:
                    parsed_time = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                    if parsed_time.tzinfo is None:
                        parsed_time = parsed_time.replace(tzinfo=timezone(timedelta(hours=3)))
                except ValueError:
                    raise ApiError(400, "تاريخ الموعد غير صحيح")
                try:
                    original_time = datetime.fromisoformat(str(appointment["scheduled_at"]).replace("Z", "+00:00"))
                    if original_time.tzinfo is None:
                        original_time = original_time.replace(tzinfo=timezone(timedelta(hours=3)))
                    time_changed = abs((parsed_time.astimezone(timezone.utc) - original_time.astimezone(timezone.utc)).total_seconds()) >= 30
                except ValueError:
                    time_changed = scheduled_at != appointment["scheduled_at"]
                connection.execute(
                    """UPDATE appointment_requests SET request_type=?,title=?,customer_name=?,phone=?,notes=?,
                              scheduled_at=?,status=?,updated_at=? WHERE id=? AND organization_id=?""",
                    (request_type, title, customer_name, phone, notes, scheduled_at, status, now(), appointment_id, organization_id),
                )
                reply_message = str(data.get("replyMessage", "")).strip()[:1000]
                if not reply_message:
                    display_time = format_arabic_datetime(parsed_time)
                    if status == "rejected":
                        reply_message = "نعتذر، تعذر قبول الموعد المطلوب. يرجى اختيار موعد آخر."
                    elif time_changed:
                        reply_message = f"الوقت المطلوب غير مناسب، وتم تعديل موعدك إلى {display_time}."
                    elif status == "accepted":
                        reply_message = f"تم تأكيد موعدك بتاريخ {display_time} ✓"
                    elif status == "completed":
                        reply_message = "تم إنجاز طلبك، وشكرًا لتواصلك معنا."
                if appointment["chat_session_id"] and reply_message:
                    connection.execute(
                        "INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,?,?,?)",
                        (appointment["chat_session_id"], "human", reply_message, now()),
                    )
                    connection.execute(
                        "UPDATE chat_sessions SET state=?,updated_at=? WHERE id=?",
                        ("closed" if status in ("accepted", "rejected", "completed") else "waiting_human", now(), appointment["chat_session_id"]),
                    )
                audit_log(connection, organization_id, user["id"], "appointment_updated", f"تم تحديث {request_type} للعميل {customer_name} إلى حالة {status}", "appointment", appointment_id)
                connection.commit()
                self._send(200, {"saved": True, "status": status, "replyMessage": reply_message})
                return
            if path == "/api/vehicles" and method == "GET":
                require_permission(user, "viewVehicles", "editVehicles", "vehicles")
                rows = connection.execute("SELECT * FROM vehicles WHERE organization_id=? ORDER BY id DESC", (organization_id,)).fetchall()
                self._send(200, [dict(row) for row in rows])
                return
            if path == "/api/vehicles" and method == "POST":
                require_permission(user, "editVehicles")
                data = self._body()
                cursor = connection.execute(
                    """INSERT INTO vehicles(organization_id,name,plate,registration_expiry,inspection_expiry,insurance_expiry,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (organization_id, str(data.get("name", "")).strip(), str(data.get("plate", "")).strip(), data.get("registrationExpiry"), data.get("inspectionExpiry"), data.get("insuranceExpiry"), now()),
                )
                connection.commit()
                self._send(201, {"id": cursor.lastrowid})
                return
            if path.startswith("/api/vehicles/") and method == "DELETE":
                require_permission(user, "deleteVehicles")
                vehicle_id = int(path.rsplit("/", 1)[1])
                connection.execute("DELETE FROM vehicles WHERE id=? AND organization_id=?", (vehicle_id, organization_id))
                connection.commit()
                self._send(200, {"deleted": True})
                return
            if path == "/api/ads" and method == "GET":
                purge_expired_ads(connection)
                rows = connection.execute(
                    """SELECT advertisements.id,advertisements.title,advertisements.message,advertisements.contact,
                              advertisements.approved_at,advertisements.expires_at,organizations.name AS advertiser
                       FROM advertisements JOIN organizations ON organizations.id=advertisements.organization_id
                       WHERE advertisements.active=1 AND advertisements.approved=1
                         AND (advertisements.expires_at IS NULL OR advertisements.expires_at>?)
                       ORDER BY advertisements.id DESC""",
                    (now(),),
                ).fetchall()
                self._send(200, [dict(row) for row in rows])
                return
            if path == "/api/ads" and method == "POST":
                if user["role"] != "admin":
                    raise ApiError(403, "هذه العملية للمدير فقط")
                package = connection.execute("SELECT package FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()["package"]
                if package != "vip":
                    raise ApiError(403, "إنشاء الإعلانات متاح لباقة VIP فقط")
                data = self._body()
                title = str(data.get("title", "")).strip()
                message = str(data.get("message", "")).strip()
                contact = str(data.get("contact", "")).strip()
                if not title or len(title) > 120:
                    raise ApiError(400, "عنوان الإعلان مطلوب وبحد أقصى 120 حرفًا")
                if len(message) > 1000 or len(contact) > 80:
                    raise ApiError(400, "محتوى الإعلان أو وسيلة التواصل طويلة جدًا")

                cursor = connection.execute(
                    "INSERT INTO advertisements(organization_id,title,message,contact,active,approved,created_at) VALUES(?,?,?,?,?,?,?)",
                    (organization_id, title, message, contact, 1, 0, now()),
                )
                connection.commit()
                self._send(201, {"id": cursor.lastrowid, "approved": False})
                return
            if path == "/api/subscription-requests" and method == "POST":
                if user["role"] != "admin":
                    raise ApiError(403, "طلب ترقية الباقة متاح لمالك المؤسسة فقط")
                data = self._body()
                requested_package = str(data.get("package", "")).strip().lower()
                if requested_package not in ("basic", "vip"):
                    raise ApiError(400, "اختر الباقة الأساسية أو VIP")
                discount_code = str(data.get("discountCode", "")).strip().upper()
                discount_percent = 0
                if discount_code:
                    code_hash = hashlib.sha256(discount_code.encode()).hexdigest()
                    code = connection.execute(
                        """SELECT discount_percent FROM activation_codes WHERE code_hash=? AND active=1
                           AND code_kind='discount' AND used_count<max_uses
                           AND (expires_at IS NULL OR expires_at>?)""",
                        (code_hash, now()),
                    ).fetchone()
                    if code is None:
                        raise ApiError(400, "كود الخصم غير صحيح أو منتهي")
                    discount_percent = int(code["discount_percent"])
                pending = connection.execute(
                    "SELECT id FROM subscription_requests WHERE organization_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                    (organization_id,),
                ).fetchone()
                if pending:
                    request_id = pending["id"]
                    connection.execute(
                        """UPDATE subscription_requests
                           SET requested_package=?,discount_code=?,discount_percent=?,created_at=?
                           WHERE id=?""",
                        (requested_package, discount_code, discount_percent, now(), request_id),
                    )
                else:
                    cursor = connection.execute(
                        """INSERT INTO subscription_requests(
                               organization_id,requested_package,discount_code,discount_percent,status,created_at
                           ) VALUES(?,?,?,?,?,?)""",
                        (organization_id, requested_package, discount_code, discount_percent, "pending", now()),
                    )
                    request_id = cursor.lastrowid
                connection.commit()
                self._send(201, {"id": request_id, "status": "pending", "discountPercent": discount_percent})
                return
            if path == "/api/subscription" and method == "GET":
                row = connection.execute("SELECT package,starts_at,expires_at FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()
                self._send(200, dict(row))
                return
        raise ApiError(404, "المسار غير موجود")

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def do_GET(self) -> None:
        self._run("GET")

    def do_POST(self) -> None:
        self._run("POST")

    def do_PUT(self) -> None:
        self._run("PUT")

    def do_DELETE(self) -> None:
        self._run("DELETE")

    def _run(self, method: str) -> None:
        try:
            self._dispatch(method)
        except ApiError as error:
            print(f"API ERROR {error.status} {self.path}: {error.message}")
            self._send(error.status, {"error": error.message})
        except Exception as error:
            print(f"ERROR: {error}")
            self._send(500, {"error": "حدث خطأ داخل الخادم"})


if __name__ == "__main__":
    init_db()
    print(f"Khdoom API: http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
