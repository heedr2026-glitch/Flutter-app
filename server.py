"""Khdoom API server. Standard-library only: Python 3 + SQLite."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KHDOOM_DB", ROOT / "khdoom.db"))
HOST = os.environ.get("KHDOOM_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("KHDOOM_PORT", "8080")))
OWNER_KEY_PATH = ROOT / "owner.key"
AI_URL = os.environ.get("KHDOOM_AI_URL", "https://api.openai.com/v1/responses")
AI_MODEL = os.environ.get("KHDOOM_AI_MODEL", "gpt-4.1-mini")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


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
    connection: sqlite3.Connection, organization_id: int
) -> tuple[str, int, int]:
    package_row = connection.execute(
        "SELECT package FROM subscriptions WHERE organization_id=?",
        (organization_id,),
    ).fetchone()
    package = package_row["package"] if package_row else "free"
    daily_limit = {"free": 5, "basic": 30, "vip": 100}.get(package, 5)
    day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    used = connection.execute(
        "SELECT COUNT(*) AS count FROM ai_usage WHERE organization_id=? AND created_at>=?",
        (organization_id, day_start),
    ).fetchone()["count"]
    if used >= daily_limit:
        raise ApiError(429, "تم بلوغ الحد اليومي لموظفي AI")
    return package, daily_limit, used

def init_db() -> None:
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
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vehicles (
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
            CREATE TABLE IF NOT EXISTS advertisements (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              message TEXT NOT NULL DEFAULT '',
              contact TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              approved INTEGER NOT NULL DEFAULT 0,
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
        ad_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(advertisements)")
        }
        if "approved_at" not in ad_columns:
            connection.execute("ALTER TABLE advertisements ADD COLUMN approved_at TEXT")
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
    if not os.environ.get("KHDOOM_OWNER_KEY") and not OWNER_KEY_PATH.exists():
        OWNER_KEY_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")


def purge_expired_ads(connection: sqlite3.Connection) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    cursor = connection.execute(
        "DELETE FROM advertisements WHERE approved=1 AND approved_at IS NOT NULL AND approved_at<=?",
        (cutoff,),
    )
    return cursor.rowcount

def downgrade_expired_subscriptions(connection: sqlite3.Connection) -> int:
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


def issue_token(connection: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    connection.execute(
        "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
        (token_hash, user_id, expires, now()),
    )
    return token


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
    def _user(self, connection: sqlite3.Connection) -> sqlite3.Row:
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
            if organization["package"] not in ("basic", "vip"):
                raise ApiError(403, "موظف الاستقبال غير متاح لهذه المؤسسة حاليًا")
            page = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>موظف استقبال __ORG_NAME__</title>
<style>body{margin:0;background:linear-gradient(160deg,#071126,#142454);color:#fff;font-family:Tahoma,Arial;min-height:100vh}.wrap{max-width:720px;margin:auto;padding:22px}.head,.chat{background:#111f42;border:1px solid #24618d;border-radius:22px;padding:20px;margin-bottom:14px}h1{color:#38d4ff;margin:0 0 8px}.messages{min-height:260px;max-height:52vh;overflow:auto;display:flex;flex-direction:column;gap:10px;margin-bottom:14px}.msg{padding:12px 15px;border-radius:16px;white-space:pre-wrap;line-height:1.65}.bot{background:#15345f;align-self:flex-start}.customer{background:#087cab;align-self:flex-end}textarea,button{box-sizing:border-box;width:100%;padding:14px;border-radius:13px;border:1px solid #2f6b99;color:#fff;font:inherit}textarea{background:#09152e;min-height:88px;resize:vertical}button{background:#7c3aed;font-weight:bold;margin-top:10px;cursor:pointer}.note{color:#9bdcf5;font-size:13px}.error{color:#fbbf24}</style></head><body><main class="wrap"><section class="head"><h1>__ORG_NAME__</h1><p>مرحبًا بك، أنا موظف الاستقبال الذكي. كيف أقدر أخدمك؟</p><p class="note">لا ترسل بيانات بنكية أو رموز تحقق. قد يتابع معك موظف بشري عند الحاجة.</p></section><section class="chat"><div id="messages" class="messages"><div class="msg bot">مرحبًا بك في __ORG_NAME__. اكتب استفسارك أو تفاصيل طلبك.</div></div><textarea id="message" maxlength="1500" placeholder="اكتب رسالتك هنا"></textarea><button id="send" onclick="sendMessage()">إرسال</button><div id="status" class="note"></div></section></main>
<script>const history=[];function addMessage(text,type){const item=document.createElement('div');item.className='msg '+type;item.textContent=text;const box=document.getElementById('messages');box.appendChild(item);box.scrollTop=box.scrollHeight}async function sendMessage(){const input=document.getElementById('message'),button=document.getElementById('send'),status=document.getElementById('status'),message=input.value.trim();if(!message)return;addMessage(message,'customer');history.push({role:'customer',text:message});input.value='';button.disabled=true;status.textContent='جاري تجهيز الرد...';try{const response=await fetch('/api/public-chat/__CHAT_TOKEN__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,history:history.slice(-8)})});const data=await response.json();if(!response.ok)throw new Error(data.error||'تعذر الرد الآن');addMessage(data.text,'bot');history.push({role:'assistant',text:data.text});status.textContent=''}catch(error){status.textContent=error.message;status.className='note error'}finally{button.disabled=false;input.focus()}}</script></body></html>"""
            page = page.replace("__ORG_NAME__", html.escape(organization["name"])).replace("__CHAT_TOKEN__", chat_token)
            self._send_html(page)
            return
        if method == "POST" and path.startswith("/api/public-chat/"):
            chat_token = path.rsplit("/", 1)[1]
            data = self._body()
            message = str(data.get("message", "")).strip()[:1500]
            if not message:
                raise ApiError(400, "اكتب رسالة العميل")
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
                package, daily_limit, used = ai_allowance(connection, organization["id"])
                if package not in ("basic", "vip"):
                    raise ApiError(403, "موظف الاستقبال متاح من الباقة الأساسية")
                admin = connection.execute(
                    "SELECT id FROM users WHERE organization_id=? AND role='admin' AND active=1 ORDER BY id LIMIT 1",
                    (organization["id"],),
                ).fetchone()
                if admin is None:
                    raise ApiError(503, "لا يوجد مسؤول نشط للمؤسسة")
                system_prompt = (
                    "أنت موظف استقبال تابع للمؤسسة المذكورة. أجب بالعربية وباختصار وفق بيانات المؤسسة فقط. "
                    "لا تخترع أسعارًا أو خدمات أو مواعيد. إذا نقصت معلومة فقل إن موظفًا بشريًا سيتابع. "
                    "اطلب اسم العميل ورقم التواصل ونوع الطلب عند الحاجة، ولا تطلب بيانات بنكية أو رموز تحقق."
                )
                organization_info = {
                    "اسم المؤسسة": organization["name"],
                    "نشاط المؤسسة": organization["activity"],
                    "رقم المؤسسة للتواصل": organization["phone"],
                }
                user_prompt = (
                    "بيانات المؤسسة:\n" + json.dumps(organization_info, ensure_ascii=False)
                    + "\nالمحادثة السابقة:\n" + json.dumps(safe_history, ensure_ascii=False)
                    + "\nرسالة العميل الحالية:\n" + message
                )
                reply = generate_ai_text(system_prompt, user_prompt)
                connection.execute(
                    "INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)",
                    (organization["id"], admin["id"], "public_reception_chat", now()),
                )
                connection.commit()
            self._send(200, {"text": reply, "remaining": daily_limit - used - 1})
            return
        if method == "GET" and path == "/owner":
            self._send_html(
                """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>لوحة مالك خدووم</title>
<style>body{margin:0;background:#071126;color:#fff;font-family:Tahoma;padding:24px}.wrap{max-width:900px;margin:auto}.card{background:#111f42;border:1px solid #1d4f7a;border-radius:20px;padding:22px;margin-bottom:14px}h1{color:#28c7ff}input,select,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #285682;background:#09152e;color:#fff}button{background:#0284c7;font-weight:bold;cursor:pointer}.vip{background:#d97706}.result{color:#7dd3fc;white-space:pre-wrap}</style></head><body><div class="wrap"><h1>لوحة مالك خدووم</h1>
<div class="card"><h2>الدخول الآمن</h2><input id="key" type="password" placeholder="مفتاح المالك"><button onclick="ownerLogin()">دخول لوحة المالك</button><div id="loginStatus" class="result">أدخل المفتاح ثم اضغط دخول</div></div>
<div class="card"><h2>صفحة أكواد التفعيل</h2><p>إنشاء وإدارة أكواد الباقة الأساسية وVIP في صفحة خاصة.</p><button onclick="location.href='/owner/codes'">فتح صفحة أكواد التفعيل</button></div>
<div class="card"><h2>المؤسسات</h2><button onclick="loadOrganizations()">عرض المؤسسات</button><div id="organizations" class="result"></div></div><div class="card"><h2>مراجعة إعلانات VIP</h2><button class="vip" onclick="loadAds()">تحميل الإعلانات</button><div id="ads" class="result"></div></div></div>
<script>const headers=()=>({'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value.trim()});async function ownerLogin(){let r=await fetch('/owner/api/organizations',{headers:headers()});let d=await r.json();let s=document.getElementById('loginStatus');if(r.ok){s.textContent='تم الدخول بنجاح ✓';loadOrganizations();loadAds()}else{s.textContent=d.error||'تعذر الدخول'}}async function createCode(){let r=await fetch('/owner/api/codes',{method:'POST',headers:headers(),body:JSON.stringify({recipientName:document.getElementById('recipient').value,assignedUsername:document.getElementById('assignedUsername').value,customCode:document.getElementById('customCode').value,package:document.getElementById('package').value,durationDays:+document.getElementById('days').value,maxUses:+document.getElementById('uses').value})});let d=await r.json();document.getElementById('result').textContent=r.ok?'الكود: '+d.code+'\\nمخصص إلى: '+(d.recipientName||'غير محدد')+'\\nالباقة: '+d.package+'\\nالمدة: '+d.durationDays+' يوم':(d.error||'تعذر إنشاء الكود')}async function setPackage(id,pkg){let r=await fetch('/owner/api/organizations/'+id+'/package',{method:'PUT',headers:headers(),body:JSON.stringify({package:pkg,durationDays:30})});let d=await r.json();let box=document.getElementById('organizations');if(r.ok&&d.saved){box.textContent='تم تغيير الباقة لمدة 30 يوم ✓';await loadOrganizations()}else{box.textContent=d.error||'تعذر تغيير الباقة'}}async function loadOrganizations(){let r=await fetch('/owner/api/organizations',{headers:headers()});let data=await r.json();let box=document.getElementById('organizations');if(!Array.isArray(data)){box.textContent=data.error||'تعذر عرض المؤسسات';return}if(data.length===0){box.textContent='لا توجد مؤسسات في قاعدة البيانات الجديدة بعد. يلزم ربط تسجيل حساب التطبيق بالخادم ثم ستظهر المؤسسات هنا.';return}box.innerHTML=data.map(o=>`<div class="card"><b>${o.name}</b><p>الباقة الحالية: ${o.package} | ${o.phone}</p><a href="/chat/${o.public_chat_token}" target="_blank" style="display:block;color:#7dd3fc;margin:10px 0">فتح رابط محادثة الزبائن</a><button onclick="setPackage(${o.id},'free')">مجانية</button><button onclick="setPackage(${o.id},'basic')">فتح الأساسية لمدة 30 يوم</button><button class="vip" onclick="setPackage(${o.id},'vip')">فتح VIP لمدة 30 يوم</button></div>`).join('')}async function loadAds(){let r=await fetch('/owner/api/ads',{headers:headers()});let data=await r.json();let box=document.getElementById('ads');if(!Array.isArray(data)){box.textContent=data.error||'تعذر تحميل الإعلانات';return}box.innerHTML=data.length?data.map(a=>`<div class="card"><b>${esc(a.title)}</b><p>المؤسسة: ${esc(a.organization_name)}</p><p>${esc(a.message||'')}</p><p>التواصل: ${esc(a.contact||'')}</p><p>الحالة: ${a.approved?'مقبول':a.active?'بانتظار المراجعة':'مرفوض'}</p><button onclick="reviewAd(${a.id},'approve')">قبول ونشر</button><button class="vip" onclick="reviewAd(${a.id},'reject')">رفض</button></div>`).join(''):'لا توجد إعلانات للمراجعة'}async function reviewAd(id,action){let r=await fetch('/owner/api/ads/'+id,{method:'PUT',headers:headers(),body:JSON.stringify({action})});let d=await r.json();alert(r.ok?(action==='approve'?'تم قبول الإعلان ونشره':'تم رفض الإعلان'):(d.error||'تعذر تحديث الإعلان'));if(r.ok)loadAds()}function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</script></body></html>"""
            )
            return
        if method == "GET" and path == "/owner/codes":
            self._send_html(
                """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>أكواد تفعيل خدووم</title>
<style>body{margin:0;background:#071126;color:#fff;font-family:Tahoma;padding:24px}.wrap{max-width:900px;margin:auto}.card{background:#111f42;border:1px solid #1d4f7a;border-radius:20px;padding:22px;margin-bottom:14px}h1{color:#28c7ff}input,select,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #285682;background:#09152e;color:#fff}button{background:#0284c7;font-weight:bold;cursor:pointer}.vip{background:#d97706}.result{color:#7dd3fc;white-space:pre-wrap}.code{border-right:4px solid #f59e0b}</style></head><body><div class="wrap"><h1>صفحة أكواد تفعيل خدووم</h1><p><a href="/owner" style="color:#7dd3fc">العودة إلى لوحة المالك</a></p>
<div class="card"><h2>مفتاح المالك</h2><input id="key" type="password" placeholder="مفتاح المالك"><button onclick="login()">دخول وتحميل الأكواد</button><div id="status" class="result"></div></div>
<div class="card"><h2>إنشاء كود جديد</h2><input id="recipient" placeholder="اسم الشخص أو المؤسسة"><input id="assignedUsername" placeholder="اسم المستخدم المسموح له (اختياري)"><input id="customCode" placeholder="الكود الخاص مثل VIP-AHMED-2026"><select id="package"><option value="basic">الأساسية</option><option value="vip">VIP</option></select><input id="days" type="number" value="30" placeholder="مدة الاشتراك بالأيام"><input id="uses" type="number" value="1" placeholder="عدد مرات الاستخدام"><button class="vip" onclick="createCode()">إنشاء كود التفعيل</button><div id="result" class="result"></div></div>
<div class="card"><h2>سجل الأكواد</h2><button onclick="loadCodes()">تحديث السجل</button><div id="codes"></div></div></div>
<script>const headers=()=>({'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value.trim()});async function login(){let r=await fetch('/owner/api/codes',{headers:headers()});let d=await r.json();document.getElementById('status').textContent=r.ok?'تم الدخول بنجاح ✓':(d.error||'تعذر الدخول');if(r.ok)render(d)}async function createCode(){let r=await fetch('/owner/api/codes',{method:'POST',headers:headers(),body:JSON.stringify({recipientName:document.getElementById('recipient').value,assignedUsername:document.getElementById('assignedUsername').value,customCode:document.getElementById('customCode').value,package:document.getElementById('package').value,durationDays:+document.getElementById('days').value,maxUses:+document.getElementById('uses').value})});let d=await r.json();document.getElementById('result').textContent=r.ok?'احتفظ بهذا الكود وأرسله للمستفيد فقط: '+d.code+'\\nالمستفيد: '+(d.recipientName||'غير محدد')+'\\nالباقة: '+d.package+' لمدة '+d.durationDays+' يوم':(d.error||'تعذر إنشاء الكود');if(r.ok)loadCodes()}async function loadCodes(){let r=await fetch('/owner/api/codes',{headers:headers()});let d=await r.json();if(r.ok)render(d);else document.getElementById('codes').textContent=d.error||'تعذر تحميل الأكواد'}function render(data){let box=document.getElementById('codes');box.innerHTML=data.length?data.map(c=>`<div class="card code"><b>${c.code_prefix}...</b><p>المستفيد: ${c.recipient_name||'غير محدد'} | المستخدم: ${c.assigned_username||'أي مستخدم'}</p><p>الباقة: ${c.package} | الاستخدام: ${c.used_count}/${c.max_uses} | مدة الاشتراك: ${c.duration_days} يوم</p></div>`).join(''):'لا توجد أكواد بعد'}</script></body></html>"""
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
                              advertisements.created_at,advertisements.approved_at,organizations.name AS organization_name
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
            with db() as connection:
                cursor = connection.execute(
                    "UPDATE advertisements SET approved=?,active=?,approved_at=? WHERE id=?",
                    (approved, active, now() if approved else None, advertisement_id),
                )
                connection.commit()
            if cursor.rowcount == 0:
                raise ApiError(404, "الإعلان غير موجود")
            self._send(200, {"saved": True, "approved": bool(approved)})
            return
        if path == "/owner/api/codes" and method == "POST":
            self._owner()
            data = self._body()
            package = str(data.get("package", ""))
            if package not in ("basic", "vip"):
                raise ApiError(400, "اختر باقة صحيحة")
            duration_days = max(1, min(int(data.get("durationDays", 30)), 3650))
            max_uses = max(1, min(int(data.get("maxUses", 1)), 10000))
            prefix = "VIP" if package == "vip" else "BASIC"
            custom_code = str(data.get("customCode", "")).strip().upper()
            if custom_code and (len(custom_code) < 6 or len(custom_code) > 40):
                raise ApiError(400, "الكود الخاص يجب أن يكون من 6 إلى 40 خانة")
            if custom_code and not all(c.isalnum() or c in "-_" for c in custom_code):
                raise ApiError(400, "استخدم في الكود حروفًا وأرقامًا وشرطة فقط")
            code = custom_code or f"{prefix}-{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            recipient_name = str(data.get("recipientName", "")).strip()
            assigned_username = str(data.get("assignedUsername", "")).strip().lower()
            try:
                with db() as connection:
                    connection.execute(
                        """INSERT INTO activation_codes(code_hash,code_prefix,package,duration_days,max_uses,expires_at,recipient_name,assigned_username,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (code_hash, code[:10], package, duration_days, max_uses, expires_at, recipient_name, assigned_username, now()),
                    )
            except sqlite3.IntegrityError:
                raise ApiError(409, "هذا الكود مستخدم، اختر كودًا آخر")
            self._send(201, {"code": code, "recipientName": recipient_name, "assignedUsername": assigned_username, "package": package, "durationDays": duration_days, "maxUses": max_uses, "codeExpiresAt": expires_at})
            return
        if path == "/owner/api/codes" and method == "GET":
            self._owner()
            with db() as connection:
                rows = connection.execute(
                    """SELECT id,code_prefix,recipient_name,assigned_username,package,duration_days,max_uses,used_count,expires_at,active,created_at
                       FROM activation_codes ORDER BY id DESC"""
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
            return
        if path == "/owner/api/organizations" and method == "GET":
            self._owner()
            with db() as connection:
                rows = connection.execute(
                    """SELECT organizations.id,organizations.name,organizations.phone,organizations.public_chat_token,subscriptions.package,subscriptions.expires_at
                       FROM organizations JOIN subscriptions ON subscriptions.organization_id=organizations.id ORDER BY organizations.id DESC"""
                ).fetchall()
            self._send(200, [dict(row) for row in rows])
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
                               AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)""",
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
                    token = issue_token(connection, cursor.lastrowid)
                self._send(201, {"token": token, "organizationId": organization_id, "package": package, "expiresAt": package_expires})
            except sqlite3.IntegrityError:
                raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
            return
        if method == "POST" and path == "/api/login":
            data = self._body()
            with db() as connection:
                user = connection.execute(
                    "SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1",
                    (str(data.get("username", "")).strip(),),
                ).fetchone()
                if user is None or not verify_password(str(data.get("password", "")), user["password_hash"], user["password_salt"]):
                    raise ApiError(401, "اسم المستخدم أو كلمة المرور غير صحيحة")
                token = issue_token(connection, user["id"])
                package = connection.execute("SELECT package FROM subscriptions WHERE organization_id=?", (user["organization_id"],)).fetchone()["package"]
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
                       AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)""",
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
                system_prompt = (
                    "أنت موظف بحث تجاري سعودي. ابحث في الإنترنت عن معلومات حديثة وعلنية فقط. "
                    "أجب بالعربية بوضوح، ولا تخترع أسماء أو أرقامًا. اذكر مصادر أو روابط مفيدة عند توفرها، "
                    "ونبّه أن بيانات الاتصال والأسعار تحتاج تحققًا قبل الاعتماد."
                )
                user_prompt = (
                    f"نوع البحث: {research_type}\nالمجال أو الكلمات: {keywords}\n"
                    f"المدينة: {city}\nقدم نتيجة عملية مختصرة من 5 إلى 10 نقاط."
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
                system_prompt = (
                    "أنت موظف استقبال لمؤسسة سعودية. أجب بالعربية وفق بيانات المؤسسة فقط. "
                    "لا تخترع سعرًا أو موعدًا أو خدمة غير مذكورة. إذا نقصت معلومة فقل إن الموظف البشري سيتابع، "
                    "واجمع عند الحاجة اسم العميل ورقم التواصل ونوع الطلب. اجعل الرد مهذبًا ومختصرًا."
                )
                user_prompt = (
                    "إعدادات المؤسسة:\n"
                    + json.dumps(safe_settings, ensure_ascii=False, indent=2)
                    + f"\nرسالة العميل الحالية:\n{message}"
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
                daily_limit = {"free": 5, "basic": 30, "vip": 100}.get(package, 5)
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
                       AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)""",
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
                if user["role"] != "admin":
                    raise ApiError(403, "هذه العملية للمدير فقط")
                data = self._body()
                connection.execute("UPDATE organizations SET name=?,activity=?,phone=? WHERE id=?", (str(data.get("name", "")).strip(), str(data.get("activity", "")).strip(), str(data.get("phone", "")).strip(), organization_id))
                connection.commit()
                self._send(200, {"saved": True})
                return
            if path == "/api/employees" and method == "GET":
                if user["role"] != "admin":
                    raise ApiError(403, "هذه العملية للمدير فقط")
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
                if user["role"] != "admin":
                    raise ApiError(403, "هذه العملية للمدير فقط")
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
                    connection.commit()
                    self._send(201, {"id": cursor.lastrowid})
                except sqlite3.IntegrityError:
                    raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
                return
            if path.startswith("/api/employees/") and method == "PUT":
                if user["role"] != "admin":
                    raise ApiError(403, "هذه العملية للمدير فقط")
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
                    connection.commit()
                except sqlite3.IntegrityError:
                    raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
                self._send(200, {"saved": True})
                return
            if path.startswith("/api/employees/") and method == "DELETE":
                if user["role"] != "admin":
                    raise ApiError(403, "هذه العملية للمدير فقط")
                employee_id = int(path.rsplit("/", 1)[1])
                connection.execute(
                    "DELETE FROM users WHERE id=? AND organization_id=? AND role='employee'",
                    (employee_id, organization_id),
                )
                connection.commit()
                self._send(200, {"deleted": True})
                return
            if path == "/api/vehicles" and method == "GET":
                rows = connection.execute("SELECT * FROM vehicles WHERE organization_id=? ORDER BY id DESC", (organization_id,)).fetchall()
                self._send(200, [dict(row) for row in rows])
                return
            if path == "/api/vehicles" and method == "POST":
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
                vehicle_id = int(path.rsplit("/", 1)[1])
                connection.execute("DELETE FROM vehicles WHERE id=? AND organization_id=?", (vehicle_id, organization_id))
                connection.commit()
                self._send(200, {"deleted": True})
                return
            if path == "/api/ads" and method == "GET":
                purge_expired_ads(connection)
                rows = connection.execute(
                    """SELECT advertisements.id,advertisements.title,advertisements.message,advertisements.contact,
                              advertisements.approved_at,organizations.name AS advertiser
                       FROM advertisements JOIN organizations ON organizations.id=advertisements.organization_id
                       WHERE advertisements.active=1 AND advertisements.approved=1 ORDER BY advertisements.id DESC"""
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
