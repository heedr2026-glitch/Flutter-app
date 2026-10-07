"""جسر المكالمات الهاتفية: رقم مزود الاتصال (Twilio) ← موظف خدوم الصوتي عبر OpenAI SIP.

المسار: المتصل يتصل على رقم المزود ← Twilio يسأل خدوم (webhook) ← خدوم يحدد المؤسسة من الرقم
المتصَل عليه ويحوّل المكالمة إلى OpenAI ← OpenAI يبلّغ خدوم بالمكالمة (webhook) ← خدوم يقبلها
بتعليمات المؤسسة ويتابع نصها ← بعد انتهائها يُكتب التقرير ويُسجَّل الموعد بانتظار الموافقة.

لا مكتبات خارجية: عميل WebSocket صغير مكتوب هنا لمتابعة نص المكالمة.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import ssl
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

import calls_trial

SIP_HOST = "sip.api.openai.com"
CALLS_URL = "https://api.openai.com/v1/realtime/calls"
SOCKET_URL = "wss://api.openai.com/v1/realtime"
REF_HEADER = "x-khdoom-ref"
PENDING_SECONDS = 120          # المهلة بين طلب Twilio ووصول المكالمة إلى OpenAI
WEBHOOK_TOLERANCE = 300
MAX_FRAME_BYTES = 4_000_000
REJECT_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>'

_active_lock = threading.Lock()
_active_calls: set[str] = set()


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    # رقم المزود الذي يستقبل مكالمات مؤسسة معيّنة؛ تحدده إدارة المنصة.
    c.execute("""CREATE TABLE IF NOT EXISTS call_gateway_numbers (
        phone TEXT PRIMARY KEY, organization_id BIGINT NOT NULL, created_at TEXT NOT NULL)""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS call_gateway_calls (
        id {identity}, ref TEXT NOT NULL, call_sid TEXT NOT NULL DEFAULT '', caller TEXT NOT NULL DEFAULT '',
        called TEXT NOT NULL DEFAULT '', organization_id BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        call_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_call_gateway_calls_ref ON call_gateway_calls(ref)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_call_gateway_calls_call ON call_gateway_calls(call_id)")


def digits(value: Any) -> str:
    text = str(value or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    found = "".join(ch for ch in text if ch.isdigit())
    return found[2:] if found.startswith("00") else found


def settings() -> dict[str, str]:
    env = lambda *names: next((os.environ.get(name, "").strip() for name in names if os.environ.get(name, "").strip()), "")
    return {"twilioToken": env("TWILIO_AUTH_TOKEN"), "openaiSecret": env("OPENAI_WEBHOOK_SECRET", "KHDOOM_OPENAI_WEBHOOK_SECRET"),
            "projectId": env("KHDOOM_OPENAI_PROJECT_ID", "OPENAI_PROJECT_ID"),
            "aiKey": env("KHDOOM_AI_API_KEY", "OPENAI_API_KEY")}


def status(c, base_url: str) -> dict[str, Any]:
    """حالة الإعداد للإدارة: هل القيم موجودة فقط، دون إظهار أي قيمة سرية."""
    found = settings()
    project = found["projectId"]
    calls = c.execute("""SELECT g.id,g.caller,g.called,g.status,g.note,g.created_at,o.name AS organization_name
        FROM call_gateway_calls g LEFT JOIN organizations o ON o.id=g.organization_id ORDER BY g.id DESC LIMIT 15""").fetchall()
    return {"twilioToken": bool(found["twilioToken"]), "openaiSecret": bool(found["openaiSecret"]),
            "projectId": bool(project) and bool(re.fullmatch(r"proj_[A-Za-z0-9_-]+", project)), "aiKey": bool(found["aiKey"]),
            "twilioWebhookUrl": base_url + "/webhooks/twilio/voice", "openaiWebhookUrl": base_url + "/webhooks/openai/realtime",
            "numbers": list_numbers(c),
            "recentCalls": [{"id": row["id"], "caller": row["caller"], "called": row["called"], "status": row["status"],
                             "note": row["note"], "createdAt": row["created_at"], "organizationName": row["organization_name"] or ""} for row in calls]}


# ---------- أرقام المزود ----------

def list_numbers(c) -> list[dict[str, Any]]:
    rows = c.execute("""SELECT g.phone,g.organization_id,g.created_at,o.name AS organization_name
        FROM call_gateway_numbers g LEFT JOIN organizations o ON o.id=g.organization_id ORDER BY g.created_at DESC""").fetchall()
    return [{"phone": row["phone"], "organizationId": row["organization_id"], "organizationName": row["organization_name"] or "",
             "createdAt": row["created_at"]} for row in rows]


def assign_number(c, phone: Any, organization_id: Any, error, now: str) -> dict[str, Any]:
    number = digits(phone)
    if not re.fullmatch(r"[1-9][0-9]{7,14}", number):
        raise error(400, "اكتب رقم المزود كاملًا مع مفتاح الدولة، مثل 14155550123")
    try:
        organization_id = int(organization_id)
    except (TypeError, ValueError):
        raise error(400, "اختر المؤسسة")
    organization = c.execute("SELECT id,name FROM organizations WHERE id=?", (organization_id,)).fetchone()
    if organization is None:
        raise error(404, "المؤسسة غير موجودة")
    c.execute("DELETE FROM call_gateway_numbers WHERE phone=?", (number,))
    c.execute("INSERT INTO call_gateway_numbers(phone,organization_id,created_at) VALUES(?,?,?)", (number, organization_id, now))
    return {"phone": number, "organizationId": organization_id, "organizationName": organization["name"]}


def remove_number(c, phone: Any) -> dict[str, Any]:
    number = digits(phone)
    c.execute("DELETE FROM call_gateway_numbers WHERE phone=?", (number,))
    return {"phone": number, "removed": True}


def organization_for(c, called: Any) -> int | None:
    """المؤسسة تُحدَّد من الرقم المتصَل عليه فقط: رقم مزود عيّنته الإدارة، أو رقم المؤسسة المربوط والمفعّل."""
    number = digits(called)
    if not number:
        return None
    row = c.execute("SELECT organization_id FROM call_gateway_numbers WHERE phone=?", (number,)).fetchone()
    if row is None:
        row = c.execute("SELECT organization_id FROM call_connections WHERE phone_number=? AND enabled=1", (number,)).fetchone()
    return int(row["organization_id"]) if row else None


# ---------- Twilio ----------

def twilio_signature(token: str, url: str, params: dict[str, str]) -> str:
    signed = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(token.encode("utf-8"), signed.encode("utf-8"), hashlib.sha1).digest()).decode("ascii")


def valid_twilio(token: str, url: str, params: dict[str, str], supplied: Any) -> bool:
    return bool(token and supplied) and hmac.compare_digest(twilio_signature(token, url, params), str(supplied).strip())


def dial_twiml(project: str, ref: str, seconds: int = calls_trial.MAX_CALL_SECONDS) -> str:
    target = f"sip:{project}@{SIP_HOST};transport=tls?{REF_HEADER}={ref}"
    return ('<?xml version="1.0" encoding="UTF-8"?><Response>'
            f'<Dial answerOnBridge="true" timeLimit="{int(seconds)}"><Sip>{escape(target)}</Sip></Dial></Response>')


def incoming_twilio(c, params: dict[str, str], now: str, blocked: Callable[[int], bool] | None = None) -> str:
    """رد خدوم على Twilio عند مكالمة واردة: تحويل إلى الموظف الصوتي، أو رفض إن لم يُعرف الرقم."""
    project = settings()["projectId"]
    organization_id = organization_for(c, params.get("To") or params.get("Called"))
    if not project or organization_id is None:
        return REJECT_TWIML
    caller = digits(params.get("From") or params.get("Caller"))
    called = digits(params.get("To") or params.get("Called"))
    sid = str(params.get("CallSid") or "")[:80]
    if blocked is not None and blocked(organization_id):
        c.execute("INSERT INTO call_gateway_calls(ref,call_sid,caller,called,organization_id,status,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (secrets.token_hex(8), sid, caller, called, organization_id, "rejected", "رصيد دقائق المكالمات منتهٍ", now))
        return REJECT_TWIML
    ref = secrets.token_hex(16)
    c.execute("INSERT INTO call_gateway_calls(ref,call_sid,caller,called,organization_id,status,created_at) VALUES(?,?,?,?,?,?,?)",
              (ref, sid, caller, called, organization_id, "pending", now))
    return dial_twiml(project, ref)


# ---------- OpenAI webhook ----------

def verify_openai(secret: str, headers: Any, raw: bytes, moment: float | None = None) -> bool:
    """توقيع Standard Webhooks: HMAC-SHA256 على «المعرّف.الوقت.الجسم»."""
    identifier, stamp, signatures = (str(headers.get(name) or "") for name in ("webhook-id", "webhook-timestamp", "webhook-signature"))
    if not (secret and identifier and stamp and signatures):
        return False
    try:
        if abs((moment if moment is not None else time.time()) - int(stamp)) > WEBHOOK_TOLERANCE:
            return False
    except ValueError:
        return False
    body = secret[6:] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
    except ValueError:
        key = secret.encode("utf-8")
    expected = base64.b64encode(hmac.new(key, f"{identifier}.{stamp}.".encode("utf-8") + raw, hashlib.sha256).digest()).decode("ascii")
    return any(hmac.compare_digest(expected, part[3:]) for part in signatures.split() if part.startswith("v1,"))


def _header(sip_headers: Any, name: str) -> str:
    for header in sip_headers if isinstance(sip_headers, list) else []:
        if isinstance(header, dict) and str(header.get("name") or "").lower() == name.lower():
            return str(header.get("value") or "")
    return ""


def _sip_number(value: str) -> str:
    found = re.search(r"sips?:\+?([0-9]{6,15})@", value) or re.search(r"\+?([0-9]{6,15})", value)
    return found.group(1) if found else ""


def _iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def claim_call(c, call_id: str, sip_headers: Any, now: str) -> dict[str, Any] | None:
    """يربط مكالمة OpenAI بطلب Twilio الذي سبقها. ما لا يسبقه طلب من Twilio يُرفض."""
    call_id = str(call_id or "")[:120]
    if not call_id or c.execute("SELECT 1 FROM call_gateway_calls WHERE call_id=?", (call_id,)).fetchone():
        return None
    current = _iso(now) or datetime.now(timezone.utc)
    oldest = current - timedelta(seconds=PENDING_SECONDS)
    fresh = lambda row: row is not None and (_iso(row["created_at"]) or current) >= oldest
    ref = _header(sip_headers, REF_HEADER).strip()
    row = None
    if re.fullmatch(r"[0-9a-f]{32}", ref):
        row = c.execute("SELECT id,organization_id,caller,called,created_at FROM call_gateway_calls WHERE ref=? AND status='pending' AND call_id=''", (ref,)).fetchone()
    if not fresh(row):
        # احتياط إن لم يُمرَّر الترويسة الخاصة: أحدث طلب معلّق من نفس رقم المتصل خلال المهلة.
        caller = _sip_number(_header(sip_headers, "From"))
        row = c.execute("""SELECT id,organization_id,caller,called,created_at FROM call_gateway_calls
            WHERE caller=? AND caller<>'' AND status='pending' AND call_id='' ORDER BY id DESC LIMIT 1""", (caller,)).fetchone() if caller else None
    if not fresh(row):
        return None
    c.execute("UPDATE call_gateway_calls SET call_id=?,status='accepted' WHERE id=?", (call_id, row["id"]))
    return {"id": row["id"], "organizationId": int(row["organization_id"]), "caller": row["caller"], "called": row["called"]}


# ---------- OpenAI: قبول المكالمة وإنهاؤها ----------

def _post(url: str, payload: dict[str, Any] | None) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
    headers = {"Authorization": f"Bearer {calls_trial._api_key()}"}
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    with urlopen(Request(url, data=data, method="POST", headers=headers), timeout=20) as response:
        response.read()


def accept_body(instructions: str, transcription: Any, speed: bool, turns: bool, extras: bool) -> dict[str, Any]:
    body = calls_trial.session_payload("male", transcription, speed, instructions, turns, extras)["session"]
    reduction = body.get("audio", {}).get("input", {}).get("noise_reduction")
    if reduction:
        reduction["type"] = "near_field"   # سماعة هاتف قريبة من الفم، لا مايك لابتوب.
    return body


def accept_call(call_id: str, instructions: str, post: Callable[[str, dict[str, Any] | None], None] | None = None) -> bool:
    """يقبل المكالمة بأفضل إعداد يقبله المزود؛ ما يرفضه نكمل بدونه كما في تجربة المتصفح."""
    post = post or _post
    for index, attempt in enumerate(calls_trial.ATTEMPTS):
        try:
            post(f"{CALLS_URL}/{call_id}/accept", accept_body(instructions, *attempt))
            return True
        except HTTPError as error:
            print(f"CALL GATEWAY ACCEPT ERROR: {error.code}")
            if error.code == 400 and index < len(calls_trial.ATTEMPTS) - 1:
                continue
            return False
        except (URLError, TimeoutError, OSError) as error:
            print(f"CALL GATEWAY ACCEPT CONNECTION ERROR: {type(error).__name__}")
            return False
    return False


def end_call(call_id: str, action: str = "hangup", post: Callable[[str, dict[str, Any] | None], None] | None = None) -> None:
    try:
        (post or _post)(f"{CALLS_URL}/{call_id}/{action}", {"status_code": 603} if action == "reject" else None)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        print(f"CALL GATEWAY {action.upper()} ERROR: {type(error).__name__}")


# ---------- متابعة نص المكالمة ----------

class RealtimeSocket:
    """عميل WebSocket بسيط (RFC 6455) يقرأ أحداث المكالمة. نصوص فقط، ويرد على ping."""

    def __init__(self, url: str, headers: dict[str, str], timeout: float = 15.0) -> None:
        parsed = urlparse(url)
        secure = parsed.scheme == "wss"
        host = parsed.hostname or ""
        raw = socket.create_connection((host, parsed.port or (443 if secure else 80)), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if secure else raw
        self.sock.settimeout(timeout)
        self.buffer = b""
        self.parts: list[bytes] = []
        try:
            self._handshake(parsed, headers)
        except BaseException:
            self.sock.close()
            raise

    def _handshake(self, parsed: Any, headers: dict[str, str]) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [f"GET {parsed.path or '/'}{'?' + parsed.query if parsed.query else ''} HTTP/1.1", f"Host: {parsed.netloc}",
                 "Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        lines += [f"{name}: {value}" for name, value in headers.items()]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk or len(self.buffer) > 65536:
                raise OSError("websocket handshake failed")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        text = head.decode("latin-1")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        found = re.search(r"(?im)^sec-websocket-accept:\s*(\S+)\s*$", text)
        if not text.split("\r\n", 1)[0].split(" ")[1:2] == ["101"] or not found or found.group(1) != accept:
            raise OSError("websocket handshake rejected: " + text.split("\r\n", 1)[0][:80])

    def _frame(self) -> tuple[bool, int, bytes] | None:
        data = self.buffer
        if len(data) < 2:
            return None
        length, offset = data[1] & 0x7F, 2
        if length == 126:
            if len(data) < 4:
                return None
            length, offset = struct.unpack("!H", data[2:4])[0], 4
        elif length == 127:
            if len(data) < 10:
                return None
            length, offset = struct.unpack("!Q", data[2:10])[0], 10
        if length > MAX_FRAME_BYTES:
            raise OSError("websocket frame too large")
        masked = bool(data[1] & 0x80)
        if len(data) < offset + (4 if masked else 0) + length:
            return None
        mask = data[offset:offset + 4] if masked else b""
        offset += len(mask)
        payload = data[offset:offset + length]
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.buffer = data[offset + length:]
        return bool(data[0] & 0x80), data[0] & 0x0F, payload

    def _send(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        size = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | size]) if size < 126 else bytes([0x80 | 126]) + struct.pack("!H", size)
                                          if size < 65536 else bytes([0x80 | 127]) + struct.pack("!Q", size))
        self.sock.sendall(head + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    def recv(self) -> str | None:
        """رسالة نصية كاملة، أو None إذا أُغلق الاتصال. يرفع TimeoutError إن لم يصل شيء في المهلة."""
        while True:
            frame = self._frame()
            if frame is None:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return None
                self.buffer += chunk
                continue
            final, opcode, payload = frame
            if opcode == 0x8:
                try:
                    self._send(0x8, payload[:2])
                except OSError:
                    pass
                return None
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                self.parts.append(payload)
                if final:
                    message, self.parts = b"".join(self.parts), []
                    return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._send(0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class TurnCollector:
    """يجمع كلام المتصل والموظف من أحداث المكالمة بترتيب الكلام لا بترتيب وصول التفريغ."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.turns: dict[str, dict[str, str]] = {}

    def _seen(self, item_id: Any) -> str:
        key = str(item_id or "")
        if key and key not in self.order:
            self.order.append(key)
        return key

    def handle(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        kind = str(event.get("type") or "")
        if kind in ("conversation.item.added", "conversation.item.created", "conversation.item.done"):
            item = event.get("item")
            self._seen(item.get("id") if isinstance(item, dict) else None)
        elif kind == "conversation.item.input_audio_transcription.completed":
            key = self._seen(event.get("item_id"))
            if key:
                self.turns[key] = {"role": "caller", "text": str(event.get("transcript") or "")}
        elif kind in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            key = self._seen(event.get("item_id"))
            if key:
                self.turns[key] = {"role": "agent", "text": str(event.get("transcript") or "")}

    def result(self) -> list[dict[str, str]]:
        return calls_trial.clean_turns([self.turns[key] for key in self.order if key in self.turns])


def _connect(call_id: str) -> RealtimeSocket:
    return RealtimeSocket(f"{SOCKET_URL}?call_id={call_id}", {"Authorization": f"Bearer {calls_trial._api_key()}"})


def monitor(call_id: str, connect: Callable[[str], Any] | None = None, max_seconds: int = calls_trial.MAX_CALL_SECONDS,
            clock: Callable[[], float] = time.monotonic, hangup: Callable[[str], None] | None = None) -> dict[str, Any]:
    """يتابع المكالمة حتى تنتهي أو تبلغ حدها الأقصى، ويعيد نصها ومدتها."""
    connect, hangup = connect or _connect, hangup or end_call
    collector = TurnCollector()
    started = clock()
    error = ""
    link = None
    try:
        link = connect(call_id)
        while True:
            if clock() - started >= max_seconds:
                hangup(call_id)
                break
            try:
                message = link.recv()
            except TimeoutError:
                continue
            if message is None:
                break
            try:
                collector.handle(json.loads(message))
            except ValueError:
                continue
    except (OSError, ValueError) as problem:
        error = f"تعذرت متابعة نص المكالمة: {type(problem).__name__} {str(problem)[:120]}"
        print("CALL GATEWAY MONITOR ERROR: " + error)
    finally:
        if link is not None:
            link.close()
    return {"turns": collector.result(), "durationSeconds": max(0, int(clock() - started)), "error": error}


# ---------- بعد المكالمة ----------

def finish(c, call: dict[str, Any], outcome: dict[str, Any], now: str,
           report_builder: Callable[[list[dict[str, str]]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """يكتب تقرير المكالمة في سجل مكالمات المؤسسة، ويسجّل الموعد المتفق عليه بانتظار الموافقة."""
    report_builder = report_builder or calls_trial.build_report
    turns = outcome["turns"]
    organization = next((row for row in calls_trial.list_organizations(c) if row["id"] == call["organizationId"]), None)
    report = report_builder(turns)
    if not report.get("callerPhone"):
        report["callerPhone"] = call["caller"]
    booking = calls_trial.book_appointment(c, organization, report, now) if turns else {"created": False, "reason": ""}
    appointment = str(report.get("appointment") or "")
    if booking.get("reason"):
        appointment = (appointment + " — " if appointment else "") + booking["reason"]
    row = c.execute("""INSERT INTO call_logs(organization_id,caller_phone,caller_name,direction,status,started_at,duration_seconds,transcript,
        summary,request_text,appointment,follow_up,human_handoff,last_error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                    (call["organizationId"], call["caller"][:40], str(report.get("callerName") or "")[:160], "inbound", "ended", now,
                     int(outcome["durationSeconds"]), calls_trial.transcript_text(turns)[:30000], str(report.get("summary") or "")[:4000],
                     str(report.get("request") or "")[:2000], appointment[:2000], int(bool(report.get("followUp"))), 0,
                     str(outcome.get("error") or "")[:1000], now)).fetchone()
    c.execute("UPDATE call_gateway_calls SET status='ended',note=? WHERE id=?",
              (str(outcome.get("error") or ("موعد مسجل" if booking.get("created") else ""))[:300], call["id"]))
    return {"callLogId": row["id"], "appointmentRequest": booking}


def handle_incoming(db: Callable[[], Any], now: Callable[[], str], call_id: str, sip_headers: Any,
                    accept: Callable[[str, str], bool] | None = None, watch: Callable[[str], dict[str, Any]] | None = None,
                    end: Callable[..., None] | None = None) -> str:
    """دورة مكالمة كاملة في خيط مستقل. تعيد وصفًا قصيرًا لما حصل (للسجل والاختبار)."""
    accept, watch, end = accept or accept_call, watch or monitor, end or end_call
    with _active_lock:
        if call_id in _active_calls:
            return "duplicate"
        _active_calls.add(call_id)
    try:
        with db() as c:
            if c.execute("SELECT 1 FROM call_gateway_calls WHERE call_id=?", (str(call_id)[:120],)).fetchone():
                return "duplicate"   # إعادة إرسال لنفس البلاغ بعد معالجته.
            call = claim_call(c, call_id, sip_headers, now())
            brief = calls_trial.organization_brief(c, call["organizationId"]) if call else None
            c.commit()
        if call is None or brief is None:
            end(call_id, "reject")
            return "rejected"
        if not accept(call_id, brief["instructions"]):
            with db() as c:
                c.execute("UPDATE call_gateway_calls SET status='failed',note=? WHERE id=?", ("تعذر قبول المكالمة لدى مزود الذكاء", call["id"]))
                c.commit()
            end(call_id, "reject")
            return "failed"
        outcome = watch(call_id)
        with db() as c:
            finish(c, call, outcome, now())
            c.commit()
        return "ended"
    except Exception as problem:  # خيط خلفي: لا نترك الخطأ يختفي بصمت.
        print(f"CALL GATEWAY ERROR: {type(problem).__name__} {str(problem)[:200]}")
        return "error"
    finally:
        with _active_lock:
            _active_calls.discard(call_id)
