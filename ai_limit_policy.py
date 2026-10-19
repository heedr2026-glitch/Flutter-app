"""الحسابات والاستهلاك: حد كل باقة لحالها + قائمة مؤسسات بحدود خاصة.

* حد الباقة يُحفظ في platform_packages نفسها (ai_daily / ai_monthly / whatsapp_units / calls_units)،
  فهذه الصفحة هي المكان الوحيد لتعديل الاستهلاك، وصفحة الباقات للأسعار.
* القائمة الخاصة: الذكاء الاصطناعي في ai_limits (يومي إلزامي، شهري اختياري)،
  وواتساب والمكالمات في service_special_limits (شهري). الحذف منها يرجع المؤسسة لحد باقتها.
"""

PACKAGES = ('free', 'basic', 'vip')
OTHER = ('whatsapp', 'calls')
UNIT_COLUMN = {'whatsapp': 'whatsapp_units', 'calls': 'calls_units'}
MAX_UNITS = 10000000


def _columns(c, table, postgres):
    if postgres:
        return {str(r['column_name']) for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?",
            (table,)).fetchall()}
    return {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate(c):
    postgres = hasattr(c, '_connection')
    if 'monthly_limit' not in _columns(c, 'ai_limits', postgres):
        c.execute(f"ALTER TABLE ai_limits ADD COLUMN {'IF NOT EXISTS ' if postgres else ''}monthly_limit INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS service_special_limits (
        organization_id INTEGER NOT NULL,
        service TEXT NOT NULL,
        monthly_limit INTEGER,
        PRIMARY KEY (organization_id, service)
    )""")
    # حد المجموعة القديم أُلغي: كل مؤسسة ترجع لحد باقتها.
    for table in ('ai_group_limits', 'service_group_limits'):
        try:
            c.execute(f'DROP TABLE IF EXISTS {table}')
        except Exception:
            pass


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


def _org(c, value):
    org = int(value)
    if c.execute('SELECT id FROM organizations WHERE id=?', (org,)).fetchone() is None:
        raise ValueError('المؤسسة غير موجودة')
    return org


def packages(c):
    out = []
    for row in c.execute('SELECT * FROM platform_packages').fetchall():
        row = dict(row)
        if row['package'] in PACKAGES:
            out.append({'package': row['package'], 'ai_daily': row.get('ai_daily'),
                        'ai_monthly': row.get('ai_monthly'), 'whatsapp': row.get('whatsapp_units'),
                        'calls': row.get('calls_units')})
    out.sort(key=lambda x: PACKAGES.index(x['package']))
    return out


def overview(c, service='ai'):
    import service_quota
    names = {r['id']: r['name'] for r in c.execute('SELECT id,name FROM organizations').fetchall()}
    if service == 'ai':
        special = {r['organization_id'] for r in c.execute('SELECT organization_id FROM ai_limits').fetchall()}
    else:
        special = {r['organization_id'] for r in c.execute(
            'SELECT organization_id FROM service_special_limits WHERE service=?', (service,)).fetchall()}
    items = []
    for org, state in service_quota.snapshot_bulk(c).items():
        item = state['services'][service]
        if service == 'ai':
            monthly = item['limit'] if item['monthly_enforced'] else None
            daily = item['daily_limit']
        else:
            monthly, daily = item['limit'], None
        items.append({'id': org, 'name': names.get(org, str(org)), 'package': state['package'],
                      'special': org in special, 'today': item['used_today'], 'daily_limit': daily,
                      'month': item['used'], 'monthly_limit': monthly})
    items.sort(key=lambda x: (-(x['month'] or 0), -(x['today'] or 0), str(x['name'])))
    return {'service': service, 'packages': packages(c), 'items': items}


def set_package(c, service, pkg, d):
    if pkg not in PACKAGES:
        raise ValueError('الباقة غير صحيحة')
    if service == 'ai':
        daily = _num(d.get('daily'), 'الحد اليومي', 100000)
        if daily is None or daily < 1:
            raise ValueError('الحد اليومي للباقة لازم يكون 1 أو أكثر')
        monthly = _num(d.get('monthly'), 'الحد الشهري', MAX_UNITS)
        if monthly is not None and monthly < daily:
            raise ValueError('الحد الشهري ما يكون أقل من اليومي')
        c.execute('UPDATE platform_packages SET ai_daily=?,ai_monthly=? WHERE package=?', (daily, monthly, pkg))
    else:
        monthly = _num(d.get('monthly'), 'الحد الشهري', MAX_UNITS)
        c.execute(f'UPDATE platform_packages SET {UNIT_COLUMN[service]}=? WHERE package=?', (monthly, pkg))


def set_special(c, service, org, d):
    org = _org(c, org)
    if service == 'ai':
        c.execute('DELETE FROM ai_limits WHERE organization_id=?', (org,))
        if d.get('remove'):
            return
        daily = _num(d.get('daily'), 'الحد اليومي', 100000)
        if daily is None:
            raise ValueError('حدد الحد اليومي للمؤسسة')
        monthly = _num(d.get('monthly'), 'الحد الشهري', MAX_UNITS)
        c.execute('INSERT INTO ai_limits(organization_id,daily_limit,monthly_limit) VALUES(?,?,?)', (org, daily, monthly))
        return
    c.execute('DELETE FROM service_special_limits WHERE organization_id=? AND service=?', (org, service))
    if d.get('remove'):
        return
    monthly = _num(d.get('monthly'), 'الحد الشهري', MAX_UNITS)
    if monthly is None:
        raise ValueError('حدد الحد الشهري للمؤسسة')
    c.execute('INSERT INTO service_special_limits(organization_id,service,monthly_limit) VALUES(?,?,?)',
              (org, service, monthly))


def handle(c, r, m, d):
    if r == 'ai-limits' or r.startswith('ai-limits/'):
        service, rest = 'ai', r[len('ai-limits'):].lstrip('/')
    else:
        parts = r.split('/', 2)
        if len(parts) < 2 or parts[1] not in OTHER:
            raise ValueError('نوع الخدمة غير معروف')
        service, rest = parts[1], (parts[2] if len(parts) > 2 else '')
    migrate(c)
    if rest == '' and m == 'GET':
        return overview(c, service)
    if rest.startswith('package/') and m == 'PUT':
        set_package(c, service, rest.split('/', 1)[1], d)
        return overview(c, service)
    if rest.startswith('special/') and m == 'PUT':
        set_special(c, service, rest.split('/', 1)[1], d)
        return overview(c, service)
    raise ValueError('المسار غير موجود')
