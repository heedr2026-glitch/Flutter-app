"""Durable customer follow-ups, linked to an existing session-owned booking."""
import re
from appointment_context import normalize


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS appointment_followups (
        message_id BIGINT PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
        appointment_id BIGINT NOT NULL REFERENCES appointment_requests(id) ON DELETE CASCADE,
        resolved INTEGER NOT NULL DEFAULT 0)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_appointment_followups_pending ON appointment_followups(appointment_id,resolved,message_id)')


def is_request(message):
    text = normalize(message)
    return any(phrase in text for phrase in (
        'تاخر', 'تاخير', 'ما وصل', 'ماوصل', 'محد جاني', 'ما جاني',
        'يتواصل معي', 'تواصلوا معي', 'كلمني', 'اتصلوا علي',
        'اجل الموعد', 'اجل موعد', 'تاجيل', 'غير الموعد', 'غير موعد',
    ))


def receive(c, org, session, context, message, message_id):
    if not is_request(message):
        return None
    rows = c.execute('''SELECT id FROM appointment_requests
        WHERE organization_id=? AND branch_id=? AND chat_session_id=? ORDER BY id DESC''',
        (org, session['branch_id'], session['id'])).fetchall()
    if not rows:
        # Keep incomplete booking slot collection working normally.
        if session['state'] in ('await_name','await_phone','await_type','await_datetime','await_confirmation'):
            return None
        return 'فهمت أنك تحتاج متابعة، لكن ما عندي موعد مرتبط بهذه المحادثة. افتح محادثة الحجز الأصلية أو تواصل مع المؤسسة؛ لم يتم إرسال بلاغ بعد.'
    requested = re.search(r'(?:#|رقم\s*)\s*([0-9٠-٩]+)', message)
    target = int(requested.group(1)) if requested else context.get('appointment_id')
    matched = [row for row in rows if str(row['id']) == str(target)] if target else []
    if requested and not matched:
        return 'رقم الموعد غير موجود في هذه المحادثة؛ لم يتم إرسال متابعة لهذا الرقم.'
    if not matched and len(rows) == 1:
        matched = rows
    if not matched:
        return 'عندك أكثر من موعد. اكتب بلاغك مع رقم الموعد، مثل: الموظف تأخر عن موعدي رقم 2. لم يتم إرسال البلاغ بعد.'
    source = c.execute("SELECT id FROM chat_messages WHERE id=? AND session_id=? AND sender='customer' AND message=?",
                       (message_id, session['id'], message)).fetchone()
    if source is None:
        return 'تعذر تسجيل المتابعة الآن؛ حاول إرسال رسالتك مرة أخرى.'
    appointment_id = matched[0]['id']
    c.execute('''INSERT INTO appointment_followups(message_id,appointment_id) VALUES(?,?)
        ON CONFLICT(message_id) DO NOTHING''', (message_id, appointment_id))
    return f'تم تسجيل بلاغك وإرساله إلى قائمة متابعة الموعد #{appointment_id} في التطبيق. لم يتغير موعدك؛ سيظهر رد الموظف هنا عندما يرد.'


def enrich(c, org, branch, rows):
    items = {r['id']: {**dict(r), 'followups': [], 'followup_count': 0, 'followup_latest_id': 0} for r in rows}
    pending = c.execute('''SELECT f.appointment_id,f.message_id,m.message,m.created_at
        FROM appointment_followups f JOIN appointment_requests a ON a.id=f.appointment_id
        JOIN chat_messages m ON m.id=f.message_id
        WHERE a.organization_id=? AND a.branch_id=? AND f.resolved=0 ORDER BY f.message_id DESC''', (org, branch)).fetchall()
    for row in pending:
        item = items.get(row['appointment_id'])
        if item is None: continue
        item['followup_count'] += 1
        item['followup_latest_id'] = max(item['followup_latest_id'], row['message_id'])
        if len(item['followups']) < 20:
            item['followups'].append({'id':row['message_id'], 'message':row['message'], 'createdAt':row['created_at']})
    return list(items.values())


def reply(c, org, branch, appointment_id, body, now, error):
    text = str(body.get('message', '')).strip()
    through = body.get('throughMessageId')
    if not text or len(text) > 1000 or type(through) is not int or through < 1:
        raise error(400, 'اكتب الرد وحدد المتابعة التي ترد عليها')
    row = c.execute('SELECT chat_session_id FROM appointment_requests WHERE id=? AND organization_id=? AND branch_id=?',
                    (appointment_id,org,branch)).fetchone()
    if row is None: raise error(404, 'الموعد غير موجود في هذا الفرع')
    selected = c.execute('SELECT message_id FROM appointment_followups WHERE appointment_id=? AND message_id=?', (appointment_id,through)).fetchone()
    if selected is None: raise error(404, 'المتابعة غير موجودة')
    cursor = c.execute('UPDATE appointment_followups SET resolved=1 WHERE appointment_id=? AND message_id<=? AND resolved=0', (appointment_id,through))
    if cursor.rowcount == 0: return {'sent':False,'alreadyReplied':True}
    c.execute("INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,'human',?,?)", (row['chat_session_id'],text,now()))
    return {'sent':True}
