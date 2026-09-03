"""Deterministic follow-up routing; never grants approval to public chat."""
import re
from datetime import datetime, timedelta, timezone


def normalize(text):
    return re.sub(r'[\u064b-\u065f\u0670ـ]', '', text).translate(str.maketrans('أإآى', 'اااي')).strip()


def is_followup(text):
    text = normalize(text)
    return any(phrase in text for phrase in (
        'اكد الموعد', 'اكد موعد', 'تاكيد الموعد', 'تاكيد موعد',
        'موعدي', 'حجزي', 'حالة الموعد', 'حاله الموعد', 'حالة الحجز', 'تم التاكيد',
    ))


def explicit_new(text):
    return any(phrase in normalize(text) for phrase in ('موعد جديد', 'حجز جديد', 'موعد ثاني', 'حجز ثاني'))


def remembered_name(connection, session, context):
    if context.get('customer_name'):
        return str(context['customer_name'])[:120]
    rows = connection.execute("SELECT message FROM chat_messages WHERE session_id=? AND sender='customer' ORDER BY id DESC LIMIT 20", (session['id'],)).fetchall()
    for row in rows:
        match = re.fullmatch(r'\s*(?:اسمي|إسمي|انا اسمي|أنا اسمي)\s+([\w\s]{2,80})[.،!]?\s*', row['message'])
        if match:
            return match.group(1).strip()
    return None


def describe(row):
    try:
        date = datetime.fromisoformat(row['scheduled_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone(timedelta(hours=3)))
        date = date.astimezone(timezone(timedelta(hours=3)))
        days = ('الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد')
        slot = f"{days[date.weekday()]} {date:%Y-%m-%d} الساعة {date.hour % 12 or 12}:{date.minute:02d} {'صباحًا' if date.hour < 12 else 'مساءً'}"
    except (ValueError, TypeError, AttributeError):
        slot = 'الوقت المسجل لدى المؤسسة'
    status = {
        'pending': 'طلبك بانتظار موافقة الموظف؛ لم يتم اعتماد الموعد بعد.',
        'accepted': 'الموعد معتمد بالفعل من الموظف.',
        'rejected': 'الطلب مرفوض من الموظف، وليس موعدًا مؤكدًا.',
        'completed': 'هذا الموعد مسجل كمكتمل، وليس حجزًا جديدًا.',
    }.get(row['status'], 'سأحتاج مراجعة الموظف لمعرفة حالة الطلب.')
    return f"طلبك رقم #{row['id']} يوم {slot}. {status}"


def followup_reply(connection, organization_id, session, context, message):
    if explicit_new(message):
        return None
    creation = {'await_name','await_phone','await_type','await_datetime','await_confirmation'}
    if session['state'] in creation:
        return None
    if not is_followup(message) and session['state'] != 'waiting_human':
        return None
    rows = connection.execute('''SELECT id,scheduled_at,status FROM appointment_requests
        WHERE organization_id=? AND branch_id=? AND chat_session_id=?
        ORDER BY id DESC LIMIT 6''', (organization_id, session['branch_id'], session['id'])).fetchall()
    requested = re.search(r'(?:#|رقم\s*)\s*([0-9٠-٩]+)', message)
    if requested:
        selected = [r for r in rows if r['id'] == int(requested.group(1))]
        if selected:
            return describe(selected[0])
        return 'ما لقيت هذا الرقم ضمن حجوزات هذه المحادثة. افتح محادثة الحجز الأصلية أو تواصل مع الموظف للتحقق.'
    linked = [r for r in rows if str(r['id']) == str(context.get('appointment_id'))]
    if linked:
        return describe(linked[0])
    if len(rows) == 1:
        return describe(rows[0])
    if len(rows) > 1:
        return 'عندك أكثر من طلب في هذه المحادثة. حدد رقم الطلب مع سؤالك عن الموعد:\n' + '\n'.join(describe(r) for r in rows)
    name = remembered_name(connection, session, context)
    return f"تمام{' يا ' + name if name else ''}، ما عندي حجز مرتبط بهذه المحادثة. افتح محادثة الحجز الأصلية أو أعط الموظف رقم الحجز للتحقق. لن أنشئ حجزًا جديدًا أو أؤكد موعدًا بهذه الرسالة."
