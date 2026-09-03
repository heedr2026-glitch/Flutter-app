"""Explicit customer-requested handoff; no booking, pricing or outbound autonomy."""
import json
import re
from appointment_context import normalize


def wants_human(message):
    text = normalize(message)
    if any(x in text for x in ('ما ابي','ماابي','لا اريد','ما ابغي','لا تحول')):
        return False
    return any(x in text for x in ('موظف بشري','الموظف البشري','اتكلم مع موظف','اكلم موظف',
        'اتواصل مع موظف','ابي موظف','ابغى موظف','اريد موظف','عطني موظف','حولني لموظف',
        'يتواصل معي','تواصلوا معي','اتصلوا علي'))


def price_question(message):
    text = normalize(message)
    return any(x in text for x in ('السعر','السهر','سعر','الاسعار','بكم','كم يكلف','كم التكلفه'))


def handoff(c, org, session, message, message_id, now):
    explicit = wants_human(message)
    if not explicit and message_id is None:
        return None
    pending = c.execute('''SELECT id FROM appointment_requests WHERE organization_id=? AND branch_id=?
        AND chat_session_id=? AND source='human_handoff' AND status='pending' ORDER BY id DESC LIMIT 1''',
        (org, session['branch_id'], session['id'])).fetchone()
    if not explicit and pending is None:
        return None
    if pending is None and not any(word in normalize(message) for word in ('موظف','بشري')):
        booking = c.execute("SELECT id FROM appointment_requests WHERE organization_id=? AND branch_id=? AND chat_session_id=? AND COALESCE(source,'')<>'human_handoff' LIMIT 1", (org,session['branch_id'],session['id'])).fetchone()
        if booking is not None:
            return None
    # While waiting, new messages become durable inbox followups, not fake booking fields.
    source = c.execute("SELECT id FROM chat_messages WHERE id=? AND session_id=? AND sender='customer' AND message=?",
                       (message_id, session['id'], message)).fetchone()
    if source is None:
        return 'تعذر تسجيل طلب التواصل الآن. أعد إرسال الرسالة؛ لم أرسل طلبًا بعد.'
    prior = c.execute('SELECT appointment_id FROM appointment_followups WHERE message_id=?', (message_id,)).fetchone()
    if prior:
        return f"طلب التواصل مسجل برقم #{prior['appointment_id']}. سيظهر رد الموظف هنا عندما يرد."
    if pending:
        request_id = pending['id']
    else:
        try: context=json.loads(session['context_json'] or '{}')
        except (TypeError,ValueError): context={}
        messages=c.execute('SELECT sender,message FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 8', (session['id'],)).fetchall()
        notes='\n'.join(str(row['sender'])+': '+str(row['message']) for row in reversed(messages))[-3500:]
        cursor=c.execute('''INSERT INTO appointment_requests(organization_id,chat_session_id,branch_id,
            request_type,title,customer_name,phone,notes,scheduled_at,status,source,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (org,session['id'],session['branch_id'],'طلب عميل','طلب تواصل مع موظف بشري',
             context.get('customer_name',''),context.get('phone',''),notes,'','pending','human_handoff',now(),now()))
        request_id=cursor.lastrowid
    c.execute('INSERT INTO appointment_followups(message_id,appointment_id) VALUES(?,?) ON CONFLICT(message_id) DO NOTHING', (message_id,request_id))
    return f'تم تسجيل طلب تواصل #{request_id} في قائمة المواعيد والطلبات للمؤسسة. هذا طلب تواصل وليس حجز موعد. اكتب تفاصيل استفسارك هنا؛ سيظهر رد الموظف في هذه المحادثة عندما يرد، وليس اتصالًا فوريًا.'


def exact_answer(message, training):
    def clean(text):
        return re.sub(r'[؟?،.!]', '', normalize(text)).strip()
    matches=[]
    for line in training.splitlines():
        prefix='سؤال وجواب معتمد: '
        if not line.startswith(prefix): continue
        try: row=json.loads(line[len(prefix):])
        except ValueError: continue
        if isinstance(row,dict) and isinstance(row.get('السؤال'),str) and isinstance(row.get('الإجابة'),str):
            if clean(row['السؤال'])==clean(message): matches.append(row['الإجابة'])
    unique=list(dict.fromkeys(matches))
    # Conflicting knowledge must be clarified, never arbitrarily choose a price.
    return unique[0] if len(unique)==1 else None
