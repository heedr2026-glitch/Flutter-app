"""Web Push subscriptions scoped to one public customer-chat session."""
import json
import os


def migrate(connection, postgres=False):
    connection.execute(("""CREATE TABLE IF NOT EXISTS customer_push_subscriptions (
        id BIGSERIAL PRIMARY KEY,
        session_id BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        chat_token TEXT NOT NULL,
        auth TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""" if postgres else """CREATE TABLE IF NOT EXISTS customer_push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        chat_token TEXT NOT NULL,
        created_at TEXT NOT NULL
    )"""))


def save(connection, session_id, chat_token, subscription, created_at, error_type):
    endpoint = str(subscription.get("endpoint", "")).strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh, auth = str(keys.get("p256dh", "")).strip(), str(keys.get("auth", "")).strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        raise error_type(400, "بيانات اشتراك الإشعارات غير صحيحة")
    connection.execute("""INSERT INTO customer_push_subscriptions(session_id,chat_token,endpoint,p256dh,auth,created_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET
        session_id=excluded.session_id,chat_token=excluded.chat_token,p256dh=excluded.p256dh,auth=excluded.auth""",
        (session_id, chat_token, endpoint[:2000], p256dh[:1000], auth[:500], created_at))


def public_key():
    return os.environ.get("KHDOOM_VAPID_PUBLIC_KEY", "").strip()


def notify(connection, session_id, title, message):
    private_key = os.environ.get("KHDOOM_VAPID_PRIVATE_KEY", "").strip()
    if not private_key or not public_key():
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return 0
    rows = connection.execute(
        "SELECT id,chat_token,endpoint,p256dh,auth FROM customer_push_subscriptions WHERE session_id=?",
        (session_id,)).fetchall()
    sent = 0
    for row in rows:
        payload = json.dumps({"title": title, "body": message, "url": "/chat/" + row["chat_token"]}, ensure_ascii=False)
        try:
            webpush(
                subscription_info={"endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": os.environ.get("KHDOOM_VAPID_SUBJECT", "mailto:owner@khdoom.app")},
            )
            sent += 1
        except WebPushException as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) in (404, 410):
                connection.execute("DELETE FROM customer_push_subscriptions WHERE id=?", (row["id"],))
        except Exception:
            # A push-provider/network failure must never roll back the employee reply.
            continue
    return sent


def notify_employee_reply(connection, session_id):
    """Send a privacy-safe notification without customer or conversation data."""
    return notify(connection, session_id, "خدوم", "لديك رد جديد من المؤسسة")


def service_worker():
    return """self.addEventListener('push',e=>{let d={};try{d=e.data.json()}catch(_){};e.waitUntil(self.registration.showNotification('خدوم',{body:'لديك رد جديد من المؤسسة',data:{url:d.url||'/'},tag:'khdoom-customer-reply'}))});self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(ws=>{let u=e.notification.data.url;for(let w of ws){if(w.url.includes(u)){w.focus();return}}return clients.openWindow(u)}))});"""


def client_script(chat_token):
    token = json.dumps(chat_token, ensure_ascii=False)
    return """<script>
function pushB64(s){let p='='.repeat((4-s.length%4)%4),b=atob((s+p).replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from([...b].map(x=>x.charCodeAt(0)))}
async function enableReplyNotifications(){let status=document.getElementById('pushStatus');if(!sessionToken){status.textContent='أرسل أول رسالة في الشات، ثم فعّل الإشعارات.';return}if(!('serviceWorker'in navigator)||!('PushManager'in window)){status.textContent='أضف الصفحة للشاشة الرئيسية وافتحها كتطبيق لتفعيل الإشعارات.';return}let permission=await Notification.requestPermission();if(permission!=='granted'){status.textContent='لم تسمح بالإشعارات من إعدادات الجهاز.';return}try{let key=await fetch('/api/public-chat/"""+chat_token+"""/push-key').then(r=>r.json());if(!key.publicKey)throw Error('الإشعارات غير مهيأة بعد');let reg=await navigator.serviceWorker.register('/chat-push-sw.js');let sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:pushB64(key.publicKey)});let r=await fetch('/api/public-chat/"""+chat_token+"""/sessions/'+encodeURIComponent(sessionToken)+'/push-subscription',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});let d=await r.json();if(!r.ok)throw Error(d.error||'تعذر التفعيل');status.textContent='تم تفعيل إشعارات رد الموظف ✓';document.getElementById('enablePush').disabled=true}catch(e){status.textContent=e.message}}
</script>"""
