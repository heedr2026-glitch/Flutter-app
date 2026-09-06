"""Durable, account/branch scoped reception conversations and manager requests."""
import json
import re
import secrets
from appointment_context import normalize


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS reception_conversations (
        organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        branch_id TEXT NOT NULL,
        session_id BIGINT NOT NULL UNIQUE REFERENCES chat_sessions(id) ON DELETE CASCADE,
        PRIMARY KEY(organization_id,user_id,branch_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS reception_turns (
        session_id BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL, message TEXT NOT NULL,
        PRIMARY KEY(session_id,client_id))""")


def session_for(c, org, uid, branch):
    return c.execute("""SELECT s.* FROM reception_conversations r JOIN chat_sessions s ON s.id=r.session_id
        WHERE r.organization_id=? AND r.user_id=? AND r.branch_id=?
        AND s.organization_id=r.organization_id AND s.branch_id=r.branch_id""", (org,uid,branch)).fetchone()


def snapshot(c, org, uid, branch):
    session=session_for(c,org,uid,branch)
    if session is None:
        return {'conversationId':None,'messages':[],'request':{}}
    state=json.loads(session['context_json'] or '{}')
    if state.get('request_id'):
        request=c.execute("""SELECT status FROM appointment_requests WHERE id=? AND organization_id=?
            AND branch_id=? AND chat_session_id=?""",(state['request_id'],org,branch,session['id'])).fetchone()
        state['status']=('waiting_for_manager' if request['status'] in ('pending','accepted') else request['status']) if request else 'unavailable'
    rows=c.execute('SELECT id,sender,message,created_at FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 100',(session['id'],)).fetchall()
    return {'conversationId':session['id'],'messages':[dict(r) for r in reversed(rows)],'request':state}


def manager_intent(message):
    text=normalize(message).replace('ؤ','و')
    if any(x in text for x in ('ما ابي','ماابي','لا اريد','لا تحول')):
        return False
    return bool(re.search(r'مسو?ول|مدير|موظف بشري|اكلم موظف|ابي موظف|ابغي موظف|يتواصل معي|اتصلوا علي',text))


def phone_number(message):
    digits=message.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789'))
    digits=re.sub(r'[\s()\-]','',digits)
    if re.fullmatch(r'05[0-9]{8}',digits): digits='+966'+digits[1:]
    if digits.startswith('00'): digits='+'+digits[2:]
    if re.fullmatch(r'9665[0-9]{8}',digits): digits='+'+digits
    return digits if re.fullmatch(r'\+[1-9][0-9]{7,14}',digits) else None


def manager_reply(c, org, session, message, mid, clock):
    state=json.loads(session['context_json'] or '{}')
    intent=manager_intent(message)
    if not intent and state.get('request_type')!='contact_manager':
        phone=phone_number(message)
        explicit=re.fullmatch(r'(?:اسمي|انا اسمي)\s+([\u0621-\u064a ]{2,80})',normalize(message))
        if phone: state['phone']=phone
        if explicit: state['name']=explicit.group(1).strip()
        if phone or explicit:
            c.execute('UPDATE chat_sessions SET context_json=?,updated_at=? WHERE id=?',
                (json.dumps(state,ensure_ascii=False),clock(),session['id']))
        return None
    if state.get('request_id'):
        request=c.execute("""SELECT id,status FROM appointment_requests WHERE id=? AND organization_id=?
            AND branch_id=? AND chat_session_id=?""",(state['request_id'],org,session['branch_id'],session['id'])).fetchone()
        if request is None: return 'الطلب السابق لم يعد متاحًا. راجع قائمة الطلبات مع المسؤول.'
        if request['status'] in ('pending','accepted'):
            if not intent:
                c.execute('INSERT INTO appointment_followups(message_id,appointment_id) VALUES(?,?) ON CONFLICT(message_id) DO NOTHING',(mid,request['id']))
            return f"طلبك مسجل بالفعل برقم #{request['id']} وبانتظار المسؤول. سيظهر رده هنا عندما يرد."
        label={'completed':'مكتمل','rejected':'مرفوض'}.get(request['status'],request['status'])
        return f"طلبك السابق رقم #{request['id']} حالته: {label}. راجع رد المسؤول في المحادثة."
    state['request_type']='contact_manager'
    text=normalize(message)
    phone=phone_number(message)
    if phone: state['phone']=phone
    explicit=re.fullmatch(r'(?:اسمي|انا اسمي)\s+([\u0621-\u064a ]{2,80})',text)
    plain=re.fullmatch(r'[\u0621-\u064a]+(?: [\u0621-\u064a]+){0,3}',text)
    not_name=re.search(r'سلام|مرحبا|شكرا|ابي|اريد|سعر|دوام|^نعم$|^لا$',text)
    if explicit: state['name']=explicit.group(1).strip()
    elif state.get('status')=='waiting_for_name' and plain and not intent and not not_name:
        state['name']=message.strip()
    if not state.get('name'):
        state['status']='waiting_for_name'
        reply='أكيد، وش اسمك عشان نسجل طلب تواصل مع المسؤول؟'
    elif not state.get('phone'):
        state['status']='waiting_for_phone'
        reply='تمام، وش رقم التواصل؟ اكتب رقمًا سعوديًا يبدأ بـ 05 أو رقمًا دوليًا مع رمز الدولة.'
    else:
        cursor=c.execute("""INSERT INTO appointment_requests(organization_id,chat_session_id,branch_id,
            request_type,title,customer_name,phone,notes,scheduled_at,status,source,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(org,session['id'],session['branch_id'],'طلب عميل',
            'طلب تواصل مع المسؤول',state['name'],state['phone'],'طلب من محادثة الاستقبال داخل التطبيق',
            '', 'pending','human_handoff',clock(),clock()))
        state['request_id']=cursor.lastrowid
        state['status']='waiting_for_manager'
        c.execute('INSERT INTO appointment_followups(message_id,appointment_id) VALUES(?,?)',(mid,cursor.lastrowid))
        reply=f"تم تسجيل طلبك برقم #{cursor.lastrowid} في قائمة طلبات المسؤول. سيظهر رده هنا عندما يرد."
    c.execute('UPDATE chat_sessions SET state=?,context_json=?,updated_at=? WHERE id=?',
              (state['status'],json.dumps(state,ensure_ascii=False),clock(),session['id']))
    return reply


def send(c, org, uid, branch, message, client_id, clock, generate, error, postgres=False):
    if not message or len(message)>3000: raise error(400,'اكتب رسالة من 1 إلى 3000 حرف')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',client_id): raise error(400,'معرف الرسالة غير صحيح')
    # Serialize retries/creation in the database, including multiple server processes.
    if postgres:
        c.execute('SELECT id FROM users WHERE id=? FOR UPDATE',(uid,)).fetchone()
    elif not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    session=session_for(c,org,uid,branch)
    if session is None:
        cursor=c.execute("""INSERT INTO chat_sessions(organization_id,public_token,state,context_json,created_at,updated_at,branch_id)
            VALUES(?,?,?,?,?,?,?)""",(org,secrets.token_urlsafe(32),'idle','{}',clock(),clock(),branch))
        c.execute('INSERT INTO reception_conversations(organization_id,user_id,branch_id,session_id) VALUES(?,?,?,?)',(org,uid,branch,cursor.lastrowid))
        session=session_for(c,org,uid,branch)
    prior=c.execute('SELECT message FROM reception_turns WHERE session_id=? AND client_id=?',(session['id'],client_id)).fetchone()
    if prior:
        if prior['message']!=message: raise error(409,'معرف الرسالة مستخدم لرسالة أخرى')
        return snapshot(c,org,uid,branch)
    c.execute('INSERT INTO reception_turns(session_id,client_id,message) VALUES(?,?,?)',(session['id'],client_id,message))
    history=c.execute('SELECT sender,message FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 12',(session['id'],)).fetchall()
    cursor=c.execute('INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,?,?,?)',(session['id'],'customer',message,clock()))
    reply=manager_reply(c,org,session,message,cursor.lastrowid,clock)
    if reply is None:
        fresh=session_for(c,org,uid,branch)
        reply=generate(fresh,[{'role':r['sender'],'text':r['message']} for r in reversed(history)],json.loads(fresh['context_json'] or '{}'))
    c.execute('INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,?,?,?)',(session['id'],'bot',reply,clock()))
    c.execute('UPDATE chat_sessions SET updated_at=? WHERE id=?',(clock(),session['id']))
    return snapshot(c,org,uid,branch)
