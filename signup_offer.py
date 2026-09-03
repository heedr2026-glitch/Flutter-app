"""Owner-configured first-signup gift. Caller owns the registration transaction."""
import calendar
from datetime import datetime


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS signup_offer (
        id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
        max_claims INTEGER NOT NULL DEFAULT 10, claimed INTEGER NOT NULL DEFAULT 0,
        package TEXT NOT NULL DEFAULT 'basic', months INTEGER NOT NULL DEFAULT 6,
        revision INTEGER NOT NULL DEFAULT 0)''')
    c.execute('INSERT INTO signup_offer(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
    c.execute('''CREATE TABLE IF NOT EXISTS signup_offer_claims (
        organization_id BIGINT PRIMARY KEY, package TEXT NOT NULL,
        months INTEGER NOT NULL, starts_at TEXT NOT NULL, expires_at TEXT NOT NULL)''')


def overview(c):
    result = dict(c.execute('SELECT * FROM signup_offer WHERE id=1').fetchone())
    result['enabled'] = bool(result['enabled'])
    result['remaining'] = max(0, result['max_claims'] - result['claimed'])
    return result


def configure(c, data, error):
    if type(data.get('enabled')) is not bool:
        raise error(400, 'حدد تشغيل العرض أو إيقافه')
    for field, minimum, maximum in [('max_claims', 0, 1000000), ('months', 1, 60), ('revision', 0, 2147483647)]:
        if type(data.get(field)) is not int or not minimum <= data[field] <= maximum:
            raise error(400, 'تحقق من العدد والمدة ثم أعد تحميل الإعدادات')
    if data.get('package') not in ('basic', 'vip'):
        raise error(400, 'اختر الأساسية أو VIP')
    updated = c.execute('''UPDATE signup_offer SET enabled=?,max_claims=?,package=?,months=?,revision=revision+1
        WHERE id=1 AND revision=?''', (int(data['enabled']), data['max_claims'], data['package'], data['months'], data['revision']))
    if updated.rowcount != 1:
        raise error(409, 'تغير العرض أو عدد المستفيدين؛ حدّث الصفحة قبل الحفظ')
    return overview(c)


def claim(c, organization_id, started):
    existing = c.execute('SELECT * FROM signup_offer_claims WHERE organization_id=?', (organization_id,)).fetchone()
    if existing:
        return dict(existing)
    row = c.execute('''UPDATE signup_offer SET claimed=claimed+1,revision=revision+1
        WHERE id=1 AND enabled=1 AND claimed<max_claims RETURNING package,months''').fetchone()
    if row is None:
        return None
    start = datetime.fromisoformat(started)
    month_index = start.year * 12 + start.month - 1 + row['months']
    year, month = divmod(month_index, 12)
    month += 1
    end = start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
    result = {'organization_id': organization_id, 'package': row['package'], 'months': row['months'],
              'starts_at': started, 'expires_at': end.isoformat()}
    c.execute('''INSERT INTO signup_offer_claims(organization_id,package,months,starts_at,expires_at)
        VALUES(?,?,?,?,?)''', tuple(result.values()))
    return result
