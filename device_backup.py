"""نسخة في الخادم لبيانات كانت تُحفظ على الجوال فقط (المركبات، سجل الموظفين، تواريخ الرخص...).

كل صف = مفتاح واحد لفرع واحد في مؤسسة واحدة. آخر حفظ يغلب (updated_at).
الجوال يرفع بعد كل حفظ، ويسترجع عند جوال جديد أو بعد تحديث التطبيق.
"""
import re
from datetime import datetime, timezone

ALLOWED_KEYS = {
    'business_vehicles', 'employee_alert_records', 'organization_alert_records',
    'organization_alert_categories_v1', 'electricity_bills',
    'daily_work_tasks_v1', 'daily_work_hidden_v1',
}
ALERT_PREFIX = 'alert_'
BRANCH_RE = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')
MAX_VALUE = 15 * 1024 * 1024        # مفتاح واحد (مع الصور المضمّنة)
MAX_ORG_TOTAL = 80 * 1024 * 1024    # مجموع المؤسسة


def stamp():
    return datetime.now(timezone.utc).isoformat()


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS device_backup (
        organization_id BIGINT NOT NULL,
        branch_id TEXT NOT NULL,
        data_key TEXT NOT NULL,
        value TEXT NOT NULL,
        size INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, branch_id, data_key)
    )''')


def _check(branch, key):
    branch = str(branch or 'main')
    key = str(key or '')
    if not BRANCH_RE.fullmatch(branch):
        raise ValueError('الفرع غير صحيح')
    if key not in ALLOWED_KEYS and not (key.startswith(ALERT_PREFIX) and 1 < len(key) <= 120 and '\n' not in key):
        raise ValueError('نوع البيانات غير مسموح')
    return branch, key


def index(c, org):
    migrate(c)
    rows = c.execute('SELECT branch_id,data_key,size,updated_at FROM device_backup WHERE organization_id=? ORDER BY branch_id,data_key',
                     (org,)).fetchall()
    return {'items': [{'branch': r['branch_id'], 'key': r['data_key'], 'size': r['size'],
                       'updatedAt': r['updated_at']} for r in rows]}


def get_item(c, org, branch, key):
    migrate(c)
    branch, key = _check(branch, key)
    row = c.execute('SELECT value,updated_at FROM device_backup WHERE organization_id=? AND branch_id=? AND data_key=?',
                    (org, branch, key)).fetchone()
    if row is None:
        raise LookupError('غير موجود')
    return {'branch': branch, 'key': key, 'value': row['value'], 'updatedAt': row['updated_at']}


def put_item(c, org, data):
    migrate(c)
    branch, key = _check(data.get('branch'), data.get('key'))
    value = data.get('value')
    if not isinstance(value, str):
        raise ValueError('القيمة غير صحيحة')
    size = len(value.encode('utf-8'))
    if size > MAX_VALUE:
        raise ValueError('البيانات أكبر من المسموح')
    updated = str(data.get('updatedAt') or '')[:40] or stamp()
    try:
        datetime.fromisoformat(updated.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('وقت التعديل غير صحيح')
    current = c.execute('SELECT updated_at,size FROM device_backup WHERE organization_id=? AND branch_id=? AND data_key=?',
                        (org, branch, key)).fetchone()
    if current is not None and str(current['updated_at']) > updated:
        # نسخة أحدث موجودة من جوال ثاني: لا نكتب فوقها.
        return {'saved': False, 'stale': True, 'updatedAt': current['updated_at']}
    total = c.execute('SELECT COALESCE(SUM(size),0) n FROM device_backup WHERE organization_id=?', (org,)).fetchone()['n']
    if int(total) - int(current['size'] if current else 0) + size > MAX_ORG_TOTAL:
        raise ValueError('مساحة النسخ الاحتياطي للمؤسسة ممتلئة')
    c.execute('DELETE FROM device_backup WHERE organization_id=? AND branch_id=? AND data_key=?', (org, branch, key))
    c.execute('''INSERT INTO device_backup(organization_id,branch_id,data_key,value,size,updated_at,saved_at)
                 VALUES(?,?,?,?,?,?,?)''', (org, branch, key, value, size, updated, stamp()))
    return {'saved': True, 'updatedAt': updated}
