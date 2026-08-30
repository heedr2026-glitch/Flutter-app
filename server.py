"""Khdoom API server. Standard-library only: Python 3 + SQLite."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KHDOOM_DB", ROOT / "khdoom.db"))
HOST = os.environ.get("KHDOOM_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("KHDOOM_PORT", "8080")))
OWNER_KEY_PATH = ROOT / "owner.key"


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
            CREATE INDEX IF NOT EXISTS idx_ads_active ON advertisements(active, approved);
            """
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
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
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
        if method == "GET" and path == "/owner":
            self._send_html(
                """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>لوحة مالك خدووم</title>
<style>body{margin:0;background:#071126;color:#fff;font-family:Tahoma;padding:24px}.wrap{max-width:900px;margin:auto}.card{background:#111f42;border:1px solid #1d4f7a;border-radius:20px;padding:22px;margin-bottom:14px}h1{color:#28c7ff}input,select,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #285682;background:#09152e;color:#fff}button{background:#0284c7;font-weight:bold;cursor:pointer}.vip{background:#d97706}.result{color:#7dd3fc;white-space:pre-wrap}</style></head><body><div class="wrap"><h1>لوحة مالك خدووم</h1>
<div class="card"><h2>الدخول الآمن</h2><input id="key" type="password" placeholder="مفتاح المالك"><button onclick="ownerLogin()">دخول لوحة المالك</button><div id="loginStatus" class="result">أدخل المفتاح ثم اضغط دخول</div></div>
<div class="card"><h2>صفحة أكواد التفعيل</h2><p>إنشاء وإدارة أكواد الباقة الأساسية وVIP في صفحة خاصة.</p><button onclick="location.href='/owner/codes'">فتح صفحة أكواد التفعيل</button></div>
<div class="card"><h2>المؤسسات</h2><button onclick="loadOrganizations()">عرض المؤسسات</button><div id="organizations" class="result"></div></div></div>
<script>const headers=()=>({'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value.trim()});async function ownerLogin(){let r=await fetch('/owner/api/organizations',{headers:headers()});let d=await r.json();let s=document.getElementById('loginStatus');if(r.ok){s.textContent='تم الدخول بنجاح ✓';loadOrganizations()}else{s.textContent=d.error||'تعذر الدخول'}}async function createCode(){let r=await fetch('/owner/api/codes',{method:'POST',headers:headers(),body:JSON.stringify({recipientName:document.getElementById('recipient').value,assignedUsername:document.getElementById('assignedUsername').value,customCode:document.getElementById('customCode').value,package:document.getElementById('package').value,durationDays:+document.getElementById('days').value,maxUses:+document.getElementById('uses').value})});let d=await r.json();document.getElementById('result').textContent=r.ok?'الكود: '+d.code+'\\nمخصص إلى: '+(d.recipientName||'غير محدد')+'\\nالباقة: '+d.package+'\\nالمدة: '+d.durationDays+' يوم':(d.error||'تعذر إنشاء الكود')}async function setPackage(id,pkg){let days=prompt('مدة الباقة بالأيام','30');if(!days)return;let r=await fetch('/owner/api/organizations/'+id+'/package',{method:'PUT',headers:headers(),body:JSON.stringify({package:pkg,durationDays:+days})});let d=await r.json();alert(d.saved?'تم تغيير الباقة':(d.error||'تعذر التغيير'));loadOrganizations()}async function loadOrganizations(){let r=await fetch('/owner/api/organizations',{headers:headers()});let data=await r.json();let box=document.getElementById('organizations');if(!Array.isArray(data)){box.textContent=data.error||'تعذر عرض المؤسسات';return}if(data.length===0){box.textContent='لا توجد مؤسسات في قاعدة البيانات الجديدة بعد. يلزم ربط تسجيل حساب التطبيق بالخادم ثم ستظهر المؤسسات هنا.';return}box.innerHTML=data.map(o=>`<div class="card"><b>${o.name}</b><p>الباقة الحالية: ${o.package} | ${o.phone}</p><button onclick="setPackage(${o.id},'free')">مجانية</button><button onclick="setPackage(${o.id},'basic')">فتح الأساسية</button><button class="vip" onclick="setPackage(${o.id},'vip')">فتح VIP</button></div>`).join('')}</script></body></html>"""
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
                    """SELECT organizations.id,organizations.name,organizations.phone,subscriptions.package,subscriptions.expires_at
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
            if any(not str(data.get(key, "")).strip() for key in required):
                raise ApiError(400, "أكمل بيانات الحساب والمؤسسة")
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
                        "INSERT INTO organizations(name,activity,phone,created_at) VALUES(?,?,?,?)",
                        (str(data["organizationName"]).strip(), str(data.get("activity", "")).strip(), str(data["phone"]).strip(), now()),
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
                org = connection.execute("SELECT id,name,activity,phone,created_at FROM organizations WHERE id=?", (organization_id,)).fetchone()
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
                    """SELECT id,name,username,phone,email,role,permissions,active,created_at
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
                if len(str(data.get("password", ""))) < 8:
                    raise ApiError(400, "كلمة مرور الموظف 8 خانات على الأقل")
                password_hash, salt = hash_password(str(data["password"]))
                try:
                    cursor = connection.execute(
                        """INSERT INTO users(organization_id,name,username,phone,email,password_hash,password_salt,role,permissions,active,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (organization_id, str(data.get("name", "")).strip(), str(data.get("username", "")).strip().lower(), str(data.get("phone", "")).strip(), str(data.get("email", "")).strip().lower(), password_hash, salt, "employee", json.dumps(data.get("permissions", {}), ensure_ascii=False), 1, now()),
                    )
                    connection.commit()
                    self._send(201, {"id": cursor.lastrowid})
                except sqlite3.IntegrityError:
                    raise ApiError(409, "اسم المستخدم مستخدم بالفعل")
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
                package = connection.execute("SELECT package FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()["package"]
                if package == "vip":
                    self._send(200, [])
                else:
                    rows = connection.execute(
                        """SELECT advertisements.id,advertisements.title,advertisements.message,advertisements.contact,organizations.name AS advertiser
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
                cursor = connection.execute(
                    "INSERT INTO advertisements(organization_id,title,message,contact,active,approved,created_at) VALUES(?,?,?,?,?,?,?)",
                    (organization_id, str(data.get("title", "")).strip(), str(data.get("message", "")).strip(), str(data.get("contact", "")).strip(), 1, 0, now()),
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
            self._send(error.status, {"error": error.message})
        except Exception as error:
            print(f"ERROR: {error}")
            self._send(500, {"error": "حدث خطأ داخل الخادم"})


if __name__ == "__main__":
    init_db()
    print(f"Khdoom API: http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
