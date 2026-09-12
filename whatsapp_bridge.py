"""WhatsApp Cloud API: explicit organization binding, signed webhooks, no client secrets."""
import hashlib, hmac, json, os, re, time, uuid
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs

class Error(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

def configs():
    try:
        items = json.loads(os.environ.get("KHDOOM_WHATSAPP_CONFIG", "[]"))
        if not isinstance(items, list): raise ValueError()
        # Keep the secret JSON focused on credentials while allowing Meta's
        # current WABA/phone identifiers to be corrected without editing or
        # exposing the access token and app secret.
        waba_override = os.environ.get("KHDOOM_WHATSAPP_WABA_ID", "").strip()
        phone_override = os.environ.get("KHDOOM_WHATSAPP_PHONE_NUMBER_ID", "").strip()
        orgs, phones = set(), set()
        for c in items:
            if waba_override: c["waba_id"] = waba_override
            if phone_override: c["phone_number_id"] = phone_override
            c["organization_id"] = int(c["organization_id"])
            if c["organization_id"] <= 0 or c["organization_id"] in orgs: raise ValueError()
            for k in ("token", "app_secret", "verify_token", "phone_number_id", "waba_id", "api_version"):
                if not isinstance(c.get(k), str) or not c[k].strip(): raise ValueError()
            if not c["phone_number_id"].isdigit() or not c["waba_id"].isdigit(): raise ValueError()
            if not re.fullmatch(r"v[0-9]+\.0", c["api_version"]): raise ValueError()
            if c["phone_number_id"] in phones: raise ValueError()
            orgs.add(c["organization_id"]); phones.add(c["phone_number_id"])
        token_override = os.environ.get("KHDOOM_WHATSAPP_TOKEN_OVERRIDE", "").strip()
        if token_override:
            items = [dict(c, token=token_override) for c in items]
        return items
    except (ValueError, KeyError, TypeError):
        raise Error(503, "إعدادات واتساب على الخادم غير صحيحة")

def initialize(c):
    c.execute("""CREATE TABLE IF NOT EXISTS whatsapp_messages(
      id TEXT PRIMARY KEY, organization_id BIGINT NOT NULL, phone_number_id TEXT NOT NULL,
      peer TEXT NOT NULL, direction TEXT NOT NULL, body TEXT NOT NULL, branch_id TEXT,
      timestamp BIGINT NOT NULL, state TEXT NOT NULL, meta_id TEXT, client_id TEXT,
      UNIQUE(organization_id,phone_number_id,meta_id), UNIQUE(organization_id,client_id))""")
    if hasattr(c, '_connection'):
        c.execute('ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS branch_id TEXT')
    elif 'branch_id' not in {r['name'] for r in c.execute('PRAGMA table_info(whatsapp_messages)')}:
        c.execute('ALTER TABLE whatsapp_messages ADD COLUMN branch_id TEXT')
    c.execute("""CREATE TABLE IF NOT EXISTS whatsapp_webhooks(
      phone_number_id TEXT PRIMARY KEY, received_at BIGINT NOT NULL)""")

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def graph(cfg, path, body=None):
    request = Request("https://graph.facebook.com/" + cfg["api_version"] + "/" + path,
      data=None if body is None else json.dumps(body).encode(),
      headers={"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as e:
        code = e.code
        e.close()
        raise Error(502, "رفضت ميتا الطلب؛ تحقق من الرمز والصلاحيات ورقم الهاتف (HTTP %s)" % code)
    except (URLError, TimeoutError, ValueError, OSError):
        raise Error(502, "تعذر تأكيد استجابة ميتا؛ تحقق من الاتصال")

def config(org):
    return next((c for c in configs() if c["organization_id"] == org), None)

def status(c, org):
    cfg = config(org)
    if not cfg: return {"connected": False, "detail": "الخادم يعمل؛ إعداد واتساب لهذه المؤسسة لم يكتمل"}
    result = graph(cfg, cfg["phone_number_id"] + "?fields=id,display_phone_number,verified_name")
    if str(result.get("id")) != cfg["phone_number_id"]: raise Error(502, "معرّف الهاتف لا يطابق ميتا")
    received = c.execute("SELECT received_at FROM whatsapp_webhooks WHERE phone_number_id=?",
      (cfg["phone_number_id"],)).fetchone()
    return {"connected": True, "phone_number_id": cfg["phone_number_id"],
      "display_phone_number": result.get("display_phone_number", ""), "webhook_received": bool(received),
      "detail": "تم التحقق من الهاتف لدى ميتا. " + ("وصل إشعار موقّع من ميتا." if received else "بانتظار أول رسالة واردة للتحقق من الاستقبال.")}

def ingest(c, cfg, payload):
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        raise Error(400, "حدث غير صحيح")
    matched = inserted = 0
    for entry in payload.get("entry", []):
        incoming_entry_id = str(entry.get("id", ""))
        incoming_phone_ids = sorted({
            str(change.get("value", {}).get("metadata", {}).get("phone_number_id", ""))
            for change in entry.get("changes", [])
            if isinstance(change, dict)
        } - {""})
        if incoming_entry_id != cfg["waba_id"] or incoming_phone_ids and cfg["phone_number_id"] not in incoming_phone_ids:
            print("WhatsApp ingest identifiers entry_id=%s phone_ids=%s expected_waba=%s expected_phone=%s" % (
                incoming_entry_id, incoming_phone_ids, cfg["waba_id"], cfg["phone_number_id"]))
        if str(entry.get("id")) != cfg["waba_id"]: continue
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if str(value.get("metadata", {}).get("phone_number_id")) != cfg["phone_number_id"]: continue
            matched += 1
            c.execute("""INSERT INTO whatsapp_webhooks(phone_number_id,received_at) VALUES(?,?)
              ON CONFLICT(phone_number_id) DO UPDATE SET received_at=excluded.received_at""",
              (cfg["phone_number_id"], int(time.time())))
            for m in value.get("messages", []):
                mid, peer = str(m.get("id", "")), str(m.get("from", ""))
                if not mid or not re.fullmatch(r"[0-9]{7,15}", peer): continue
                try: stamp = min(int(m.get("timestamp", 0)), int(time.time()))
                except (ValueError, TypeError): continue
                body = str(m.get("text", {}).get("body", "")) if m.get("type") == "text" else "[رسالة غير نصية: " + str(m.get("type", ""))[:30] + "]"
                cursor = c.execute("""INSERT INTO whatsapp_messages
                  (id,organization_id,phone_number_id,peer,direction,body,timestamp,state,meta_id,branch_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                  (uuid.uuid4().hex,cfg["organization_id"],cfg["phone_number_id"],peer,"inbound",body[:10000],stamp,"received",mid,cfg.get("branch_id")))
                inserted += max(0, cursor.rowcount)
            for s in value.get("statuses", []):
                ranks = {"sending":0,"unknown":0,"accepted":1,"sent":2,"failed":2,"delivered":3,"read":4}
                state = s.get("status")
                if state not in ("sent","delivered","read","failed"): continue
                row = c.execute("""SELECT id,state FROM whatsapp_messages WHERE organization_id=?
                  AND phone_number_id=? AND meta_id=? AND direction='outbound'""",
                  (cfg["organization_id"],cfg["phone_number_id"],str(s.get("id","")))).fetchone()
                if row and ranks[state] >= ranks.get(row["state"],0):
                    c.execute("UPDATE whatsapp_messages SET state=? WHERE id=?", (state,row["id"]))

    print(f"WhatsApp ingest matched_changes={matched} inserted_messages={inserted} outcome={'processed' if matched else 'ignored_account_or_phone'}", flush=True)
    return {"matched_changes": matched, "inserted_messages": inserted}

def send(c, org, data):
    cfg = config(org)
    if not cfg: raise Error(409,"واتساب غير مهيأ لهذه المؤسسة")
    peer, text, cid = str(data.get("to","")), str(data.get("message","")).strip(), str(data.get("clientMessageId",""))
    if not re.fullmatch(r"[0-9]{7,15}",peer) or not 1 <= len(text) <= 4096 or not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}",cid):
        raise Error(400,"بيانات الرسالة غير صحيحة")
    old = c.execute("SELECT * FROM whatsapp_messages WHERE organization_id=? AND client_id=?",(org,cid)).fetchone()
    if old:
        if old["peer"] != peer or old["body"] != text or old["phone_number_id"] != cfg["phone_number_id"]:
            raise Error(409,"معرّف المحاولة مستخدم لرسالة أخرى")
        return dict(old)
    recent = c.execute("""SELECT id FROM whatsapp_messages WHERE organization_id=? AND phone_number_id=?
      AND peer=? AND direction='inbound' AND timestamp>? LIMIT 1""",
      (org,cfg["phone_number_id"],peer,int(time.time())-86400)).fetchone()
    if not recent: raise Error(409,"يلزم وصول رسالة من العميل خلال آخر 24 ساعة للرد النصي؛ القوالب غير مدعومة هنا بعد")
    mid = uuid.uuid4().hex
    cursor = c.execute("""INSERT INTO whatsapp_messages
      (id,organization_id,phone_number_id,peer,direction,body,timestamp,state,client_id,branch_id)
      VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
      (mid,org,cfg["phone_number_id"],peer,"outbound",text,int(time.time()),"sending",cid,cfg.get("branch_id")))
    c.commit()
    if cursor.rowcount != 1: raise Error(409,"المحاولة قيد التنفيذ؛ حدّث المحادثة")
    try:
        result = graph(cfg,cfg["phone_number_id"]+"/messages",
          {"messaging_product":"whatsapp","to":peer,"type":"text","text":{"body":text}})
        meta_id = result["messages"][0]["id"]
        c.execute("UPDATE whatsapp_messages SET state=?,meta_id=? WHERE id=?",("accepted",meta_id,mid))
    except (Error,KeyError,IndexError,TypeError):
        c.execute("UPDATE whatsapp_messages SET state=? WHERE id=?",("unknown",mid)); c.commit()
        raise Error(502,"لم نتأكد من الإرسال؛ حدّث المحادثة قبل محاولة جديدة لتجنب التكرار")
    c.commit()
    return dict(c.execute("SELECT * FROM whatsapp_messages WHERE id=?",(mid,)).fetchone())

def handle(h, method, db):
    path = urlparse(h.path).path.rstrip("/")
    if path != "/webhooks/whatsapp" and not path.startswith("/api/whatsapp/"): return False
    try:
        if path == "/webhooks/whatsapp":
            cfgs = configs()
            if method == "GET":
                q = parse_qs(urlparse(h.path).query)
                token = q.get("hub.verify_token",[""])[0]
                if q.get("hub.mode") != ["subscribe"] or not any(hmac.compare_digest(token.encode("utf-8"),c["verify_token"].encode("utf-8")) for c in cfgs):
                    raise Error(403,"تعذر التحقق")
                body = q.get("hub.challenge",[""])[0].encode()
                h.send_response(200); h.send_header("Content-Type","text/plain")
                h.send_header("Content-Length",str(len(body))); h.end_headers(); h.wfile.write(body)
                return True
            if method != "POST": raise Error(405,"طريقة غير مدعومة")
            try: length = int(h.headers.get("Content-Length","0"))
            except ValueError: raise Error(400,"حجم غير صحيح")
            if not 0 < length <= 1048576 or h.headers.get("Transfer-Encoding"): raise Error(413,"حجم غير مقبول")
            raw = h.rfile.read(length)
            signature = h.headers.get("X-Hub-Signature-256","")
            valid = [c for c in cfgs if hmac.compare_digest(signature,
              "sha256="+hmac.new(c["app_secret"].encode(),raw,hashlib.sha256).hexdigest())]
            if not valid: raise Error(403,"توقيع غير صحيح")
            try:
                payload = json.loads(raw)
                with db() as c:
                    initialize(c)
                    for cfg in valid: ingest(c,cfg,payload)
                    c.commit()
            except (ValueError,TypeError,AttributeError): raise Error(400,"حدث غير صحيح")
            h._send(200,{"received":True}); return True
        with db() as c:
            user = h._user(c)
            if user["role"] != "admin": raise Error(403,"إدارة واتساب متاحة لمسؤول المؤسسة فقط")
            initialize(c); org = user["organization_id"]
            if path == "/api/whatsapp/status" and method == "GET": result = status(c,org)
            elif path == "/api/whatsapp/messages" and method == "GET":
                result = {"messages":[dict(r) for r in c.execute(
                  "SELECT * FROM whatsapp_messages WHERE organization_id=? ORDER BY timestamp DESC,id DESC LIMIT 200",(org,)).fetchall()]}
            elif path == "/api/whatsapp/messages" and method == "POST": result = send(c,org,h._body())
            else: raise Error(404,"المسار غير موجود")
            c.commit(); h._send(200,result)
    except Error as e: h._send(e.status,{"error":e.message})
    return True
