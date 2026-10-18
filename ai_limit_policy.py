"""حدود الذكاء الاصطناعي: حد للمجموعة (كل المؤسسات) وقائمة مؤسسات بحدود خاصة.

* المجموعة: يومي وشهري يطبقان على كل مؤسسة ليست في القائمة الخاصة. فارغ = حسب الباقة.
* القائمة الخاصة: صف في ai_limits (يومي إلزامي، شهري اختياري). الحذف منها يرجعها للمجموعة.
"""


def _columns(c, table, postgres):
    if postgres:
        return {str(r['column_name']) for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?",
            (table,)).fetchall()}
    return {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate(c):
    postgres = hasattr(c, '_connection')
    c.execute("""CREATE TABLE IF NOT EXISTS ai_group_limits (
        id INTEGER PRIMARY KEY,
        daily_limit INTEGER,
        monthly_limit INTEGER
    )""")
    if 'monthly_limit' not in _columns(c, 'ai_limits', postgres):
        c.execute(f"ALTER TABLE ai_limits ADD COLUMN {'IF NOT EXISTS ' if postgres else ''}monthly_limit INTEGER")


def _num(value, label, maximum):
    if value is None or value == '':
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(f'{label}: اكتب رقمًا صحيحًا')
    if not 0 <= n <= maximum:
        raise ValueError(f'{label}: الرقم بين 0 و{maximum}')
    return n


def group(c):
    row = c.execute('SELECT daily_limit,monthly_limit FROM ai_group_limits WHERE id=1').fetchone()
    return {'daily': row['daily_limit'] if row else None, 'monthly': row['monthly_limit'] if row else None}


def set_group(c, d):
    if d.get('clear'):
        c.execute('DELETE FROM ai_group_limits WHERE id=1')
        return group(c)
    daily = _num(d.get('daily'), 'الحد اليومي', 100000)
    monthly = _num(d.get('monthly'), 'الحد الشهري', 3000000)
    c.execute('DELETE FROM ai_group_limits WHERE id=1')
    c.execute('INSERT INTO ai_group_limits(id,daily_limit,monthly_limit) VALUES(1,?,?)', (daily, monthly))
    return group(c)


def set_special(c, org, d):
    if c.execute('SELECT id FROM organizations WHERE id=?', (org,)).fetchone() is None:
        raise ValueError('المؤسسة غير موجودة')
    if d.get('remove'):
        c.execute('DELETE FROM ai_limits WHERE organization_id=?', (org,))
        return {'removed': True}
    daily = _num(d.get('daily'), 'الحد اليومي', 100000)
    if daily is None:
        raise ValueError('حدد الحد اليومي للمؤسسة')
    monthly = _num(d.get('monthly'), 'الحد الشهري', 3000000)
    c.execute('DELETE FROM ai_limits WHERE organization_id=?', (org,))
    c.execute('INSERT INTO ai_limits(organization_id,daily_limit,monthly_limit) VALUES(?,?,?)', (org, daily, monthly))
    return {'saved': True}


def overview(c):
    import service_quota
    names = {r['id']: r['name'] for r in c.execute('SELECT id,name FROM organizations').fetchall()}
    special = {r['organization_id'] for r in c.execute('SELECT organization_id FROM ai_limits').fetchall()}
    items = []
    for org, state in service_quota.snapshot_bulk(c).items():
        ai = state['services']['ai']
        items.append({'id': org, 'name': names.get(org, str(org)), 'package': state['package'],
                      'special': org in special, 'today': ai['used_today'], 'daily_limit': ai['daily_limit'],
                      'month': ai['used'], 'monthly_limit': ai['limit'] if ai['monthly_enforced'] else None})
    items.sort(key=lambda x: x['name'])
    return {'group': group(c), 'items': items}


def handle(c, r, m, d):
    migrate(c)
    if r == 'ai-limits' and m == 'GET':
        return overview(c)
    if r == 'ai-limits/group' and m == 'PUT':
        set_group(c, d)
        return overview(c)
    if r.startswith('ai-limits/special/') and m == 'PUT':
        set_special(c, int(r.rsplit('/', 1)[1]), d)
        return overview(c)
    raise ValueError('المسار غير موجود')
