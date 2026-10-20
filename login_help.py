"""الدعم من صفحة الدخول: لمن لا يستطيع الدخول للتطبيق (نسي اسم المستخدم أو كلمة المرور...).

الطلب مفتوح بدون تسجيل دخول، لذلك له حد إرسال (3 بالساعة لكل جوال ولكل عنوان IP)
ولا يكشف أي بيانات حساب؛ الإدارة تتحقق وتتواصل مع صاحب الجوال.
"""
import re
from datetime import datetime, timedelta, timezone

CATEGORIES = ('نسيت اسم المستخدم', 'نسيت كلمة المرور', 'ما أقدر أدخل', 'مشكلة في التسجيل', 'غيره')
STATUSES = ('open', 'done')
MAX_PER_HOUR = 3
MAX_IMAGE = 600 * 1024


def stamp():
    return datetime.now(timezone.utc).isoformat()


def migrate(c):
    postgres = hasattr(c, '_connection')
    c.execute(f"""CREATE TABLE IF NOT EXISTS login_help_requests (
        id {'BIGSERIAL' if postgres else 'INTEGER'} PRIMARY KEY{'' if postgres else ' AUTOINCREMENT'},
        name TEXT NOT NULL, phone TEXT NOT NULL, category TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
        image_data TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', admin_note TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")


def reference(ident):
    return 'LH-' + str(int(ident)).zfill(6)


def _phone(value):
    digits = re.sub(r'\D', '', str(value or '').translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')))
    if digits.startswith('966') and len(digits) == 12:
        digits = '0' + digits[3:]
    if not 9 <= len(digits) <= 15:
        raise ValueError('اكتب رقم جوال صحيح')
    return digits


def create(c, data, ip):
    migrate(c)
    name = str(data.get('name', '')).strip()
    if not 2 <= len(name) <= 80:
        raise ValueError('اكتب اسمك')
    phone = _phone(data.get('phone'))
    category = str(data.get('category', '')).strip()
    if category not in CATEGORIES:
        raise ValueError('اختر نوع المشكلة')
    message = str(data.get('message', '')).strip()[:1000]
    image = str(data.get('image', '') or '')
    if image and (not image.startswith('data:image/') or len(image) > MAX_IMAGE * 4 // 3 + 100):
        raise ValueError('الصورة غير صالحة أو أكبر من 600 كيلوبايت')
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    recent = c.execute('SELECT COUNT(*) n FROM login_help_requests WHERE created_at>? AND (phone=? OR ip=?)',
                       (since, phone, ip)).fetchone()
    if int(recent['n'] if hasattr(recent, 'keys') else recent[0]) >= MAX_PER_HOUR:
        raise PermissionError('أرسلت طلبات كثيرة، حاول بعد ساعة')
    now = stamp()
    row = c.execute("""INSERT INTO login_help_requests(name,phone,category,message,image_data,status,ip,created_at,updated_at)
                       VALUES(?,?,?,?,?,'open',?,?,?) RETURNING id""",
                    (name, phone, category, message, image, ip[:64], now, now)).fetchone()
    ident = row['id'] if hasattr(row, 'keys') else row[0]
    return {'reference': reference(ident), 'createdAt': now}


def _matches(c, phone):
    try:
        rows = c.execute("""SELECT u.id,u.name,u.username,o.id organization_id,o.name organization_name
                            FROM users u JOIN organizations o ON o.id=u.organization_id
                            WHERE u.phone=? AND u.archived_at IS NULL AND o.archived_at IS NULL ORDER BY u.id DESC LIMIT 10""",
                         (phone,)).fetchall()
    except Exception:
        rows = c.execute("""SELECT u.id,u.name,u.username,o.id organization_id,o.name organization_name
                            FROM users u JOIN organizations o ON o.id=u.organization_id
                            WHERE u.phone=? ORDER BY u.id DESC LIMIT 10""", (phone,)).fetchall()
    return [dict(r) for r in rows]


def handle_admin(c, r, m, d, q):
    migrate(c)
    if r == 'login-help' and m == 'GET':
        status = q.get('status', 'open')
        where, args = '', ()
        if status in STATUSES:
            where, args = 'WHERE status=?', (status,)
        rows = [dict(x) for x in c.execute(
            f'SELECT id,name,phone,category,message,image_data,status,admin_note,created_at,updated_at '
            f'FROM login_help_requests {where} ORDER BY id DESC LIMIT 200', args).fetchall()]
        for row in rows:
            row['reference'] = reference(row['id'])
            row['accounts'] = _matches(c, row['phone'])
        counts = {s: c.execute('SELECT COUNT(*) n FROM login_help_requests WHERE status=?', (s,)).fetchone()['n']
                  for s in STATUSES}
        return {'items': rows, 'counts': counts}
    match = re.fullmatch(r'login-help/(\d+)', r)
    if match and m == 'PUT':
        ident = int(match.group(1))
        status = d.get('status', 'done')
        if status not in STATUSES:
            raise ValueError('حالة غير صحيحة')
        note = str(d.get('note', '')).strip()[:1000]
        if c.execute('SELECT id FROM login_help_requests WHERE id=?', (ident,)).fetchone() is None:
            raise ValueError('الطلب غير موجود')
        c.execute('UPDATE login_help_requests SET status=?,admin_note=?,updated_at=? WHERE id=?',
                  (status, note, stamp(), ident))
        return {'saved': True}
    raise ValueError('المسار غير موجود')
