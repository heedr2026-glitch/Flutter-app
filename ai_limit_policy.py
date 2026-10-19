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
    items.sort(key=lambda x: (-(x['month'] or 0), -(x['today'] or 0), str(x['name'])))
    return {'group': group(c), 'items': items}


def handle(c, r, m, d):
    for svc in OTHER:
        prefix = 'service-limits/' + svc
        if r == prefix or r.startswith(prefix + '/'):
            return handle_service(c, svc, r[len(prefix):].lstrip('/'), m, d)
    if r.startswith('service-limits'):
        raise ValueError('نوع الخدمة غير معروف')
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


# ---------------------------------------------------------------------------
# واتساب والمكالمات: نفس الفكرة بحد شهري فقط (رسائل صادرة / دقائق).
OTHER = ('whatsapp', 'calls')
MAX_UNITS = 10000000


def migrate_services(c):
    c.execute("""CREATE TABLE IF NOT EXISTS service_group_limits (
        service TEXT PRIMARY KEY,
        monthly_limit INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS service_special_limits (
        organization_id INTEGER NOT NULL,
        service TEXT NOT NULL,
        monthly_limit INTEGER,
        PRIMARY KEY (organization_id, service)
    )""")


def _service(name):
    if name not in OTHER:
        raise ValueError('نوع الخدمة غير معروف')
    return name


def service_overview(c, service):
    import service_quota
    row = c.execute('SELECT monthly_limit FROM service_group_limits WHERE service=?', (service,)).fetchone()
    names = {r['id']: r['name'] for r in c.execute('SELECT id,name FROM organizations').fetchall()}
    special = {r['organization_id'] for r in c.execute(
        'SELECT organization_id FROM service_special_limits WHERE service=?', (service,)).fetchall()}
    items = []
    for org, state in service_quota.snapshot_bulk(c).items():
        item = state['services'][service]
        items.append({'id': org, 'name': names.get(org, str(org)), 'package': state['package'],
                      'special': org in special, 'today': item['used_today'], 'daily_limit': None,
                      'month': item['used'], 'monthly_limit': item['limit']})
    items.sort(key=lambda x: (-(x['month'] or 0), str(x['name'])))
    return {'service': service, 'group': {'daily': None, 'monthly': row['monthly_limit'] if row else None},
            'items': items}


def handle_service(c, service, rest, m, d):
    service = _service(service)
    migrate_services(c)
    if rest == '' and m == 'GET':
        return service_overview(c, service)
    if rest == 'group' and m == 'PUT':
        c.execute('DELETE FROM service_group_limits WHERE service=?', (service,))
        if not d.get('clear'):
            monthly = _num(d.get('monthly'), 'الحد الشهري', MAX_UNITS)
            if monthly is not None:
                c.execute('INSERT INTO service_group_limits(service,monthly_limit) VALUES(?,?)', (service, monthly))
        return service_overview(c, service)
    if rest.startswith('special/') and m == 'PUT':
        org = int(rest.split('/', 1)[1])
        if c.execute('SELECT id FROM organizations WHERE id=?', (org,)).fetchone() is None:
            raise ValueError('المؤسسة غير موجودة')
        c.execute('DELETE FROM service_special_limits WHERE organization_id=? AND service=?', (org, service))
        if not d.get('remove'):
            monthly = _num(d.get('monthly'), 'الحد الشهري', MAX_UNITS)
            if monthly is None:
                raise ValueError('حدد الحد الشهري للمؤسسة')
            c.execute('INSERT INTO service_special_limits(organization_id,service,monthly_limit) VALUES(?,?,?)',
                      (org, service, monthly))
        return service_overview(c, service)
    raise ValueError('المسار غير موجود')
