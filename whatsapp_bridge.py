"""WhatsApp Cloud API: explicit organization binding, signed webhooks, no client secrets."""
import base64, hashlib, hmac, json, mimetypes, os, re, time, uuid
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs

_hmac_logging_pending = True

class Error(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

def configs(db_conn=None):
    try:
        items = json.loads(os.environ.get("KHDOOM_WHATSAPP_CONFIG", "[]"))
        if not isinstance(items, list): raise ValueError()
        # Keep the secret JSON focused on credentials while allowing Meta's
        # current WABA/phone identifiers to be corrected without editing or
        # exposing the access token and app secret.
        waba_override = os.environ.get("KHDOOM_WHATSAPP_WABA_ID", "").strip()
        phone_override = os.environ.get("KHDOOM_WHATSAPP_PHONE_NUMBER_ID", "").strip()
        # The environment override is the final source for the signing secret.
        orgs, phones = set(), set()
        for item in items:
            if waba_override: item["waba_id"] = waba_override
            if phone_override: item["phone_number_id"] = phone_override
            # Keep the configured secret intact.  The optional override is
            # evaluated by verify_webhook_signature alongside this value so
            # that key rotation remains secure and retries can be verified.
            item["organization_id"] = int(item["organization_id"])
            if item["organization_id"] <= 0 or item["organization_id"] in orgs: raise ValueError()
            for k in ("token", "app_secret", "verify_token", "phone_number_id", "waba_id", "api_version"):
                if not isinstance(item.get(k), str) or not item[k].strip(): raise ValueError()
            if not item["phone_number_id"].isdigit() or not item["waba_id"].isdigit(): raise ValueError()
            if not re.fullmatch(r"v[0-9]+\.0", item["api_version"]): raise ValueError()
            if item["phone_number_id"] in phones: raise ValueError()
            orgs.add(item["organization_id"]); phones.add(item["phone_number_id"])
        token_override = os.environ.get("KHDOOM_WHATSAPP_TOKEN_OVERRIDE", "").strip()
        if token_override:
            items = [dict(c, token=token_override) for c in items]
        # Keep Meta callback verification independently rotatable without
        # exposing or rewriting the credential JSON bundle in Render.
        verify_token_override = os.environ.get("KHDOOM_WHATSAPP_VERIFY_TOKEN_OVERRIDE", "")
        if verify_token_override:
            items = [dict(c, verify_token=verify_token_override) for c in items]
        if db_conn is not None and hasattr(db_conn, "execute") and items:
            template = items[0]
            rows = db_conn.execute("SELECT organization_id,phone_number,phone_number_id,waba_id FROM whatsapp_connections").fetchall()
            for row in rows:
                dynamic = dict(template)
                organization_id = int(row["organization_id"])
                dynamic.update({
                    "organization_id": organization_id,
                    "phone_number": str(row["phone_number"]),
                    "phone_number_id": str(row["phone_number_id"]),
                    "waba_id": str(row["waba_id"]),
                })
                configured = next((x for x in items if x["organization_id"] == organization_id), None)
                if configured is None:
                    items.append(dynamic)
                else:
                    # A saved institution connection is authoritative for its own
                    # phone/WABA IDs. The environment entry still supplies that
                    # institution's credentials, but must not pin it to the old
                    # default phone ID (which causes signed webhooks to be ignored).
                    configured.update({
                        "phone_number": dynamic["phone_number"],
                        "phone_number_id": dynamic["phone_number_id"],
                        "waba_id": dynamic["waba_id"],
                    })
        # Check the merged result as well as the environment template: a saved
        # tenant mapping must never make one Meta phone ID serve two institutions.
        merged_orgs, merged_phones = set(), set()
        for item in items:
            if item["organization_id"] in merged_orgs or item["phone_number_id"] in merged_phones:
                raise ValueError()
            merged_orgs.add(item["organization_id"])
            merged_phones.add(item["phone_number_id"])
        return items
    except (ValueError, KeyError, TypeError):
        raise Error(503, "إعدادات واتساب على الخادم غير صحيحة")

def initialize(c):
    c.execute("""CREATE TABLE IF NOT EXISTS whatsapp_connections(
      organization_id BIGINT PRIMARY KEY, phone_number TEXT NOT NULL,
      phone_number_id TEXT UNIQUE NOT NULL, waba_id TEXT NOT NULL,
      created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS whatsapp_messages(
      id TEXT PRIMARY KEY, organization_id BIGINT NOT NULL, phone_number_id TEXT NOT NULL,
      peer TEXT NOT NULL, direction TEXT NOT NULL, body TEXT NOT NULL, branch_id TEXT,
      timestamp BIGINT NOT NULL, state TEXT NOT NULL, meta_id TEXT, client_id TEXT,
      UNIQUE(organization_id,phone_number_id,meta_id), UNIQUE(organization_id,client_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS whatsapp_conversations(
      id TEXT PRIMARY KEY, organization_id BIGINT NOT NULL, sender_phone TEXT NOT NULL,
      created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
      UNIQUE(organization_id,sender_phone))""")
    optional_columns = (("media_type", "TEXT"), ("media_name", "TEXT"), ("media_id", "TEXT"), ("conversation_id", "TEXT"))
    if hasattr(c, '_connection'):
        # Do not catch a PostgreSQL ALTER error: a failed statement aborts the
        # whole transaction. Check the catalog before changing the schema.
        existing = {
            str(row['column_name'])
            for row in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=?",
                ("whatsapp_messages",),
            ).fetchall()
        }
        for column, definition in optional_columns:
            if column not in existing:
                c.execute("ALTER TABLE whatsapp_messages ADD COLUMN %s %s" % (column, definition))
    else:
        existing = {r['name'] for r in c.execute('PRAGMA table_info(whatsapp_messages)')}
        for column, definition in optional_columns:
            if column not in existing:
                c.execute('ALTER TABLE whatsapp_messages ADD COLUMN %s %s' % (column, definition))
    # Backfill both customer and staff messages so one customer always has
    # one conversation. Older outbound AI replies did not carry this ID and
    # appeared in a separate thread in the mobile inbox.
    for row in c.execute("""SELECT id,organization_id,peer FROM whatsapp_messages
      WHERE conversation_id IS NULL OR conversation_id=''""").fetchall():
        conversation_id = whatsapp_conversation_id(c, row["organization_id"], row["peer"])
        c.execute("UPDATE whatsapp_messages SET conversation_id=? WHERE id=?", (conversation_id, row["id"]))
    c.execute("""CREATE TABLE IF NOT EXISTS whatsapp_webhooks(
      phone_number_id TEXT PRIMARY KEY, received_at BIGINT NOT NULL)""")

def whatsapp_conversation_id(c, organization_id, sender_phone):
    row = c.execute("""SELECT id FROM whatsapp_conversations
      WHERE organization_id=? AND sender_phone=?""", (organization_id, sender_phone)).fetchone()
    now = int(time.time())
    if row:
        c.execute("UPDATE whatsapp_conversations SET updated_at=? WHERE id=?", (now, row["id"]))
        return str(row["id"])
    conversation_id = uuid.uuid4().hex
    c.execute("""INSERT INTO whatsapp_conversations
      (id,organization_id,sender_phone,created_at,updated_at)
      VALUES(?,?,?,?,?) ON CONFLICT(organization_id,sender_phone) DO NOTHING""",
      (conversation_id, organization_id, sender_phone, now, now))
    row = c.execute("""SELECT id FROM whatsapp_conversations
      WHERE organization_id=? AND sender_phone=?""", (organization_id, sender_phone)).fetchone()
    return str(row["id"]) if row else conversation_id

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

_GRAPH_SENSITIVE_RESPONSE_KEYS = {
    "access_token", "authorization", "token", "phone", "phone_number",
    "display_phone_number", "wa_id", "input", "to", "from", "body", "text",
}

def _safe_graph_response(value, key=""):
    if key.lower() in _GRAPH_SENSITIVE_RESPONSE_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _safe_graph_response(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_graph_response(v, key) for v in value]
    if isinstance(value, str):
        return value[:2000]
    return value

def _log_graph_message_response(cfg, path, status, raw):
    if not (path.endswith("/messages") and raw is not None):
        return
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except (UnicodeDecodeError, ValueError, TypeError):
        body = {"raw": raw[:2000].decode("utf-8", "replace")}
    error = body.get("error") if isinstance(body, dict) else None
    error_details = {}
    if isinstance(error, dict):
        for key in ("message", "type", "code", "error_subcode", "fbtrace_id"):
            if key in error:
                error_details[key] = _safe_graph_response(error[key], key)
    diagnostic = {
        "phone_number_id": str(cfg.get("phone_number_id", "")),
        "http_status": int(status),
        "response": _safe_graph_response(body),
        "meta_error": error_details,
    }
    print("WhatsApp Graph API send diagnostic " + json.dumps(
        diagnostic, ensure_ascii=False, separators=(",", ":")
    ), flush=True)

def graph(cfg, path, body=None):
    request = Request("https://graph.facebook.com/" + cfg["api_version"] + "/" + path,
      data=None if body is None else json.dumps(body).encode(),
      headers={"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            raw = response.read()
            _log_graph_message_response(cfg, path, response.status, raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except HTTPError as e:
        code = e.code
        detail = ""
        try:
            raw = e.read(4096)
            _log_graph_message_response(cfg, path, code, raw)
            payload = json.loads(raw.decode("utf-8", "replace"))
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                parts = [str(error.get(k, "")).strip() for k in ("code", "error_subcode", "type", "message")]
                detail = " ".join(p for p in parts if p)[:500]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        print("WhatsApp Graph API rejected request status=%s detail=%s" % (code, detail), flush=True)
        e.close()
        raise Error(502, "رفضت ميتا الطلب؛ تحقق من الرمز والصلاحيات ورقم الهاتف (HTTP %s)" % code)
    except (URLError, TimeoutError, ValueError, OSError):
        raise Error(502, "تعذر تأكيد استجابة ميتا؛ تحقق من الاتصال")

def graph_upload(cfg, filename, mime, content):
    boundary = "----KhdoomMedia" + uuid.uuid4().hex
    chunks = [
        ("--" + boundary + "\r\nContent-Disposition: form-data; name=messaging_product\r\n\r\nwhatsapp\r\n").encode(),
        ("--" + boundary + "\r\nContent-Disposition: form-data; name=file; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n" % (filename, mime)).encode(),
        content,
        ("\r\n--" + boundary + "--\r\n").encode(),
    ]
    request = Request("https://graph.facebook.com/" + cfg["api_version"] + "/" + cfg["phone_number_id"] + "/media",
      data=b"".join(chunks), headers={"Authorization": "Bearer " + cfg["token"],
      "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
        code = getattr(error, "code", "network")
        print("WhatsApp media upload failed status=%s" % code, flush=True)
        raise Error(502, "تعذر رفع الصورة إلى ميتا؛ تحقق من صلاحيات الوسائط")

def config(org, db_conn=None):
    return next((item for item in configs(db_conn) if item["organization_id"] == org), None)

def status(c, org):
    cfg = config(org, c)
    if not cfg: return {"connected": False, "detail": "الخادم يعمل؛ إعداد واتساب لهذه المؤسسة لم يكتمل"}
    result = graph(cfg, cfg["phone_number_id"] + "?fields=id,display_phone_number,verified_name")
    if str(result.get("id")) != cfg["phone_number_id"]: raise Error(502, "معرّف الهاتف لا يطابق ميتا")
    received = c.execute("SELECT received_at FROM whatsapp_webhooks WHERE phone_number_id=?",
      (cfg["phone_number_id"],)).fetchone()
    return {"connected": True, "phone_number_id": cfg["phone_number_id"],
      "display_phone_number": result.get("display_phone_number", ""), "webhook_received": bool(received),
      "detail": "تم التحقق من الهاتف لدى ميتا. " + ("وصل إشعار موقّع من ميتا." if received else "بانتظار أول رسالة واردة للتحقق من الاستقبال.")}

def ingest(c, cfg, payload, on_inbound=None):
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
                conversation_id = whatsapp_conversation_id(c, cfg["organization_id"], peer)
                cursor = c.execute("""INSERT INTO whatsapp_messages
                  (id,organization_id,phone_number_id,peer,direction,body,timestamp,state,meta_id,branch_id,conversation_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                  (uuid.uuid4().hex,cfg["organization_id"],cfg["phone_number_id"],peer,"inbound",body[:10000],stamp,"received",mid,cfg.get("branch_id"),conversation_id))
                if cursor.rowcount:
                    inserted += 1
                    if on_inbound is not None:
                        try:
                            on_inbound(c, cfg, peer, body[:10000], mid)
                        except Exception as error:
                            detail = getattr(error, "message", str(error))
                            print("WhatsApp auto-reply failed=%s detail=%s" % (
                                type(error).__name__, str(detail)[:240]), flush=True)
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

def signature_diagnostic(raw, cfgs):
    """Return routing identifiers only; never log message contents or sender numbers."""
    try:
        payload = json.loads(raw)
        entries = payload.get("entry", []) if isinstance(payload, dict) else []
        entry_ids, phone_ids, fields = set(), set(), set()
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict): continue
            if entry.get("id") is not None: entry_ids.add(str(entry["id"])[:32])
            changes = entry.get("changes", [])
            for change in changes if isinstance(changes, list) else []:
                if not isinstance(change, dict): continue
                field = change.get("field")
                if isinstance(field, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,50}", field): fields.add(field)
                value = change.get("value", {})
                metadata = value.get("metadata", {}) if isinstance(value, dict) else {}
                phone_id = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
                if phone_id is not None: phone_ids.add(str(phone_id)[:32])
        expected_wabas = sorted({str(c.get("waba_id", ""))[:32] for c in cfgs if c.get("waba_id")})
        expected_phones = sorted({str(c.get("phone_number_id", ""))[:32] for c in cfgs if c.get("phone_number_id")})
        return "payload_waba_ids=%s payload_phone_ids=%s fields=%s configured_waba_ids=%s configured_phone_ids=%s" % (
            sorted(entry_ids), sorted(phone_ids), sorted(fields), expected_wabas, expected_phones)
    except (ValueError, TypeError, AttributeError):
        return "payload_identifiers=unavailable configured_waba_ids=%s configured_phone_ids=%s" % (
            sorted({str(c.get("waba_id", ""))[:32] for c in cfgs if c.get("waba_id")}),
            sorted({str(c.get("phone_number_id", ""))[:32] for c in cfgs if c.get("phone_number_id")}))

def invalid_signature_payload_diagnostic(raw):
    """Extract only routing metadata for a rejected webhook; never process it."""
    result = {"object": None, "entry_ids": [], "fields": [], "phone_number_ids": []}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return result
        obj = payload.get("object")
        result["object"] = str(obj)[:80] if isinstance(obj, str) else None
        entry_ids, fields, phone_ids = set(), set(), set()
        entries = payload.get("entry", [])
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            if entry_id is not None:
                entry_ids.add(str(entry_id)[:32])
            changes = entry.get("changes", [])
            for change in changes if isinstance(changes, list) else []:
                if not isinstance(change, dict):
                    continue
                field = change.get("field")
                if isinstance(field, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,50}", field):
                    fields.add(field)
                value = change.get("value", {})
                metadata = value.get("metadata", {}) if isinstance(value, dict) else {}
                phone_id = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
                if phone_id is not None:
                    phone_ids.add(str(phone_id)[:32])
        result.update({"entry_ids": sorted(entry_ids), "fields": sorted(fields), "phone_number_ids": sorted(phone_ids)})
    except (ValueError, TypeError, AttributeError):
        pass
    return result

def safe_header(value, limit=200):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or ""))[:limit]

def verify_webhook_signature(raw, signature, cfgs):
    """Verify Meta's signature against untouched request bytes, without logging secrets or signatures."""
    override = os.environ.get("KHDOOM_WHATSAPP_APP_SECRET_OVERRIDE", "")
    # Meta can deliver a retried event that was signed by the previously
    # configured app while a replacement secret is being rolled out.  Keep
    # HMAC verification strict, but accept a signature from either secret
    # explicitly configured for this integration.  This also makes secret
    # rotation safe without ever accepting unsigned requests.
    secret_source = "KHDOOM_WHATSAPP_APP_SECRET_OVERRIDE+KHDOOM_WHATSAPP_CONFIG" if override else "KHDOOM_WHATSAPP_CONFIG"
    signature = (signature or "").strip()
    format_valid = bool(re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature))
    supplied = signature.partition("=")[2] if format_valid else ""
    verified, candidates, seen = [], [], set()
    for cfg in cfgs:
        secrets = []
        if override:
            secrets.append(override)
        configured_secret = cfg.get("app_secret", "")
        if configured_secret:
            secrets.append(configured_secret)
        for secret_value in secrets:
            # Preserve the exact configured value because whitespace is part
            # of the HMAC key.
            secret = str(secret_value).encode("utf-8")
            fingerprint = hashlib.sha256(secret).hexdigest()
            print("live_webhook_secret_fingerprint=" + fingerprint[:12], flush=True)
            expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
            matched = format_valid and hmac.compare_digest(expected, supplied)
            if fingerprint not in seen:
                candidates.append({
                    "secret_sha256": fingerprint,
                    "secret_length": len(secret),
                    "signature_match": bool(matched),
                    "expected_signature_edges": [expected[:8], expected[-8:]],
                    "supplied_signature_edges": [supplied[:8], supplied[-8:]] if format_valid else [],
                })
                seen.add(fingerprint)
            if matched and cfg not in verified:
                verified.append(cfg)
    diagnostic = {
        "signature_header": "X-Hub-Signature-256",
        "signature_header_present": bool(signature),
        "signature_length": len(signature or ""),
        "raw_body_length": len(raw),
        "raw_body_sha256": hashlib.sha256(raw).hexdigest(),
        "secret_env_name": secret_source,
        "signature_encoding_valid": format_valid,
        "candidates": candidates,
    }
    return verified, json.dumps(diagnostic, separators=(",", ":"), sort_keys=True)

def send(c, org, data):
    cfg = config(org, c)
    if not cfg: raise Error(409,"واتساب غير مهيأ لهذه المؤسسة")
    peer, text, cid = str(data.get("to","")), str(data.get("message","")).strip(), str(data.get("clientMessageId",""))
    media_type = str(data.get("mediaType", "")).strip().lower()
    media_name = str(data.get("mediaName", "image.jpg")).strip()[:120]
    encoded = str(data.get("mediaBase64", "")).strip()
    media_specs = {
        "image": ("image", "image/jpeg"),
        "audio": ("audio", "audio/ogg"),
        "video": ("video", "video/mp4"),
        "document": ("document", "application/octet-stream"),
    }
    if media_type and media_type not in media_specs:
        raise Error(415, "نوع المرفق غير مدعوم من واتساب")
    if media_type:
        if not encoded or len(encoded) > 950000:
            raise Error(413, "حجم الصورة أكبر من المسموح")
        if not re.fullmatch(r"[a-zA-Z0-9+/=_-]+", encoded):
            raise Error(400, "بيانات الصورة غير صحيحة")
        text = text or "[مرفق]"
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
    conversation_id = whatsapp_conversation_id(c, org, peer)
    cursor = c.execute("""INSERT INTO whatsapp_messages
      (id,organization_id,phone_number_id,peer,direction,body,timestamp,state,client_id,branch_id,conversation_id)
      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
      (mid,org,cfg["phone_number_id"],peer,"outbound",text,int(time.time()),"sending",cid,cfg.get("branch_id"),conversation_id))
    c.commit()
    if cursor.rowcount != 1: raise Error(409,"المحاولة قيد التنفيذ؛ حدّث المحادثة")
    try:
        if media_type:
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise Error(400, "بيانات الصورة غير صحيحة")
            if not 1 <= len(content) <= 700000:
                raise Error(413, "حجم المرفق أكبر من المسموح")
            kind, fallback_mime = media_specs[media_type]
            mime = mimetypes.guess_type(media_name)[0] or fallback_mime
            uploaded = graph_upload(cfg, media_name, mime, content)
            media_id = str(uploaded.get("id", ""))
            if not media_id: raise Error(502, "لم تُصدر ميتا معرف المرفق")
            media_body = {"id": media_id}
            if media_type == "image": media_body["caption"] = text if text != "[مرفق]" else ""
            if media_type == "document": media_body["filename"] = media_name
            result = graph(cfg,cfg["phone_number_id"]+"/messages",
              {"messaging_product":"whatsapp","to":peer,"type":kind,kind:media_body})
            c.execute("UPDATE whatsapp_messages SET state=?,meta_id=?,media_type=?,media_name=?,media_id=? WHERE id=?",
              ("accepted",result["messages"][0]["id"],media_type,media_name,media_id,mid))
            c.commit()
            return dict(c.execute("SELECT * FROM whatsapp_messages WHERE id=?",(mid,)).fetchone())
        result = graph(cfg,cfg["phone_number_id"]+"/messages",
          {"messaging_product":"whatsapp","to":peer,"type":"text","text":{"body":text}})
        meta_id = result["messages"][0]["id"]
        c.execute("UPDATE whatsapp_messages SET state=?,meta_id=? WHERE id=?",("accepted",meta_id,mid))
    except (Error,KeyError,IndexError,TypeError):
        c.execute("UPDATE whatsapp_messages SET state=? WHERE id=?",("unknown",mid)); c.commit()
        raise Error(502,"لم نتأكد من الإرسال؛ حدّث المحادثة قبل محاولة جديدة لتجنب التكرار")
    c.commit()
    return dict(c.execute("SELECT * FROM whatsapp_messages WHERE id=?",(mid,)).fetchone())

def handle(h, method, db, on_inbound=None):
    global _hmac_logging_pending
    path = urlparse(h.path).path.rstrip("/")
    if path != "/webhooks/whatsapp" and not path.startswith("/api/whatsapp/"): return False
    try:
        if path == "/webhooks/whatsapp":
            with db() as connection:
                initialize(connection)
                cfgs = configs(connection)
            if method == "GET":
                q = parse_qs(urlparse(h.path).query)
                token = q.get("hub.verify_token",[""])[0]
                if q.get("hub.mode") != ["subscribe"] or not any(hmac.compare_digest(token.encode("utf-8"),c["verify_token"].encode("utf-8")) for c in cfgs):
                    print("actual_403_source=whatsapp_bridge.py:handle:455", flush=True)
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
            if len(raw) != length: raise Error(400,"جسم الطلب غير مكتمل")
            signature = h.headers.get("X-Hub-Signature-256","")
            print("signature_check_called=true", flush=True)
            valid, signature_info = verify_webhook_signature(raw, signature, cfgs)
            details = json.loads(signature_info)
            details["content_type"] = safe_header(h.headers.get("Content-Type", ""))
            details["content_encoding"] = safe_header(h.headers.get("Content-Encoding", ""))
            details["content_length"] = length
            details["user_agent"] = safe_header(h.headers.get("User-Agent", ""))
            print("WhatsApp webhook signature diagnostic " + json.dumps(details, separators=(",", ":"), sort_keys=True), flush=True)
            if not valid:
                rejected_payload = invalid_signature_payload_diagnostic(raw)
                rejected_payload.update({
                    "content_type": safe_header(h.headers.get("Content-Type", "")),
                    "content_encoding": safe_header(h.headers.get("Content-Encoding", "")),
                    "content_length": length,
                    "user_agent": safe_header(h.headers.get("User-Agent", "")),
                })
                print("WhatsApp rejected webhook payload diagnostic " + json.dumps(rejected_payload, separators=(",", ":"), sort_keys=True), flush=True)
                if _hmac_logging_pending:
                    override = os.environ.get("KHDOOM_WHATSAPP_APP_SECRET_OVERRIDE", "")
                    secret_value = override if override else (cfgs[0].get("app_secret", "") if cfgs else "")
                    secret = str(secret_value).encode("utf-8")
                    received = signature.partition("=")[2] if re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature or "") else ""
                    computed = hmac.new(secret, raw, hashlib.sha256).hexdigest()
                    print("hmac_logging_path_hit=true", flush=True)
                    print("received_hmac_first12=" + received[:12], flush=True)
                    print("computed_hmac_first12=" + computed[:12], flush=True)
                    print("equal=" + str(hmac.compare_digest(computed, received)).lower(), flush=True)
                    _hmac_logging_pending = False
                print("actual_403_source=whatsapp_bridge.py:handle:496", flush=True)
                raise Error(403,"توقيع غير صحيح")
            try:
                payload = json.loads(raw)
                with db() as c:
                    initialize(c)
                    for cfg in valid: ingest(c,cfg,payload,on_inbound=on_inbound)
                    c.commit()
            except (ValueError,TypeError,AttributeError): raise Error(400,"حدث غير صحيح")
            h._send(200,{"received":True}); return True
        with db() as c:
            user = h._user(c)
            try:
                permissions = json.loads(user["permissions"] or "{}")
            except (TypeError, ValueError):
                permissions = {}
            is_admin = user["role"] == "admin"
            can_view = is_admin or permissions.get("viewConversations") is True
            can_reply = is_admin or permissions.get("replyConversations") is True
            if method == "GET" and path in ("/api/whatsapp/status", "/api/whatsapp/messages") and not can_view:
                raise Error(403,"لا تملك صلاحية مشاهدة محادثات واتساب")
            if method == "POST" and path == "/api/whatsapp/messages" and not can_reply:
                raise Error(403,"لا تملك صلاحية الرد على محادثات واتساب")
            if (path == "/api/whatsapp/connect" or method == "POST" and path != "/api/whatsapp/messages") and not is_admin:
                raise Error(403,"ربط واتساب متاح لمسؤول المؤسسة فقط")
            initialize(c); org = user["organization_id"]
            package_row = c.execute("SELECT package FROM subscriptions WHERE organization_id=?", (org,)).fetchone()
            if package_row and str(package_row["package"]).lower() != "vip":
                raise Error(403, "خدمة واتساب متاحة في باقة VIP فقط")
            if path == "/api/whatsapp/connect" and method == "POST":
                data = h._body()
                raw_phone = str(data.get("phone", "")).strip()
                phone = re.sub(r"[^0-9]", "", raw_phone)
                if phone.startswith("00"): phone = phone[2:]
                if not re.fullmatch(r"[1-9][0-9]{7,14}", phone):
                    raise Error(400, "أدخل رقم المؤسسة مع رمز الدولة")
                base = configs()[0] if configs() else None
                if not base: raise Error(503, "ربط واتساب الأساسي غير مهيأ على الخادم")
                rows = graph(base, base["waba_id"] + "/phone_numbers?fields=id,display_phone_number,verified_name&limit=200")
                numbers = rows.get("data", []) if isinstance(rows, dict) else []
                match = next((item for item in numbers if re.sub(r"[^0-9]", "", str(item.get("display_phone_number", ""))) == phone), None)
                if not match or not str(match.get("id", "")).isdigit():
                    labels = " ".join(
                        "%s %s" % (item.get("verified_name", ""), item.get("display_phone_number", ""))
                        for item in numbers if isinstance(item, dict)
                    ).casefold()
                    if len(numbers) == 1 and ("test" in labels or "1555" in re.sub(r"[^0-9]", "", labels)):
                        raise Error(409, "حساب Meta الحالي حساب اختبار (Test WhatsApp Business Account) ورقمه ثابت. اختر أو أنشئ حساب واتساب خدووم الحقيقي ثم أضف رقم المؤسسة وتحقق منه.")
                    raise Error(409, "أضف رقم المؤسسة في Meta وتحقق منه أولًا، ثم أعد الفحص")
                owner = c.execute("SELECT organization_id FROM whatsapp_connections WHERE phone_number_id=? AND organization_id<>?", (str(match["id"]), org)).fetchone()
                if owner: raise Error(409, "هذا الرقم مرتبط بمؤسسة أخرى في خدوم")
                now = int(time.time())
                c.execute("""INSERT INTO whatsapp_connections(organization_id,phone_number,phone_number_id,waba_id,created_at,updated_at)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(organization_id) DO UPDATE SET phone_number=excluded.phone_number,
                  phone_number_id=excluded.phone_number_id,waba_id=excluded.waba_id,updated_at=excluded.updated_at""",
                  (org, phone, str(match["id"]), base["waba_id"], now, now))
                result = {"connected": True, "phone": phone}
            elif path == "/api/whatsapp/status" and method == "GET": result = status(c,org)
            elif path == "/api/whatsapp/messages" and method == "GET":
                result = {"messages":[dict(r) for r in c.execute(
                  "SELECT * FROM whatsapp_messages WHERE organization_id=? ORDER BY timestamp DESC,id DESC LIMIT 200",(org,)).fetchall()]}
            elif path == "/api/whatsapp/messages" and method == "POST": result = send(c,org,h._body())
            else: raise Error(404,"المسار غير موجود")
            c.commit(); h._send(200,result)
    except Error as e: h._send(e.status,{"error":e.message})
    return True
