"""Owner-configured first-signup gift. Caller owns the registration transaction."""
import calendar
import re
from datetime import datetime, timedelta, timezone

RIYADH = timezone(timedelta(hours=3))


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
    # تاريخ نهاية العرض (اختياري): بعده يتوقف العرض حتى لو ما اكتمل العدد.
    if hasattr(c, '_connection'):  # PostgreSQL
        c.execute("ALTER TABLE signup_offer ADD COLUMN IF NOT EXISTS ends_on TEXT NOT NULL DEFAULT ''")
    elif 'ends_on' not in {row[1] for row in c.execute('PRAGMA table_info(signup_offer)').fetchall()}:
        c.execute("ALTER TABLE signup_offer ADD COLUMN ends_on TEXT NOT NULL DEFAULT ''")
    # تاريخ بداية العرض: يُسجَّل عند تشغيله، أو من أول مستفيد للعروض القديمة.
    if hasattr(c, '_connection'):
        c.execute("ALTER TABLE signup_offer ADD COLUMN IF NOT EXISTS starts_on TEXT NOT NULL DEFAULT ''")
    elif 'starts_on' not in {row[1] for row in c.execute('PRAGMA table_info(signup_offer)').fetchall()}:
        c.execute("ALTER TABLE signup_offer ADD COLUMN starts_on TEXT NOT NULL DEFAULT ''")


def overview(c):
    result = dict(c.execute('SELECT * FROM signup_offer WHERE id=1').fetchone())
    result['enabled'] = bool(result['enabled'])
    result['remaining'] = max(0, result['max_claims'] - result['claimed'])
    result['ends_on'] = result.get('ends_on') or ''
    result['expired'] = bool(result['ends_on'] and _today() > result['ends_on'])
    try:
        claims = [dict(r) for r in c.execute('''SELECT s.organization_id,s.package,s.months,s.starts_at,s.expires_at,o.name,
            (SELECT u.phone FROM users u WHERE u.organization_id=o.id AND u.role='admin' ORDER BY u.id LIMIT 1) phone
            FROM signup_offer_claims s LEFT JOIN organizations o ON o.id=s.organization_id
            ORDER BY s.starts_at DESC LIMIT 300''').fetchall()]
    except Exception:
        claims = [dict(r) for r in c.execute('''SELECT organization_id,package,months,starts_at,expires_at
            FROM signup_offer_claims ORDER BY starts_at DESC LIMIT 300''').fetchall()]
    starts_on = result.get('starts_on') or ''
    if not starts_on and claims:
        starts_on = min(_today(x['starts_at']) for x in claims)
    result['starts_on'] = starts_on
    result['today'] = _today()
    result['days_left'] = None
    if result['ends_on']:
        delta = datetime.strptime(result['ends_on'], '%Y-%m-%d') - datetime.strptime(_today(), '%Y-%m-%d')
        result['days_left'] = max(0, delta.days)
    result['claims'] = claims
    return result


def _today(started=None):
    moment = datetime.fromisoformat(started) if started else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(RIYADH).strftime('%Y-%m-%d')


def _phone_key(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[-9:] if len(digits) >= 9 else ''


def _cr_key(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits if len(digits) >= 5 else ''


def already_claimed(c, phones=(), registration=''):
    """هل سبق استفاد نفس الجوال أو نفس السجل التجاري من العرض بمؤسسة ثانية؟"""
    wanted_phones = {k for k in (_phone_key(p) for p in phones) if k}
    wanted_cr = _cr_key(registration)
    if not wanted_phones and not wanted_cr:
        return False
    rows = c.execute('''SELECT o.phone org_phone, o.commercial_registration cr,
        (SELECT u.phone FROM users u WHERE u.organization_id=o.id AND u.role='admin' ORDER BY u.id LIMIT 1) admin_phone
        FROM signup_offer_claims s JOIN organizations o ON o.id=s.organization_id''').fetchall()
    for row in rows:
        if wanted_cr and _cr_key(row['cr']) == wanted_cr:
            return True
        if wanted_phones & {_phone_key(row['org_phone']), _phone_key(row['admin_phone'])}:
            return True
    return False


def configure(c, data, error):
    if type(data.get('enabled')) is not bool:
        raise error(400, 'حدد تشغيل العرض أو إيقافه')
    for field, minimum, maximum in [('max_claims', 0, 1000000), ('months', 1, 60), ('revision', 0, 2147483647)]:
        if type(data.get(field)) is not int or not minimum <= data[field] <= maximum:
            raise error(400, 'تحقق من العدد والمدة ثم أعد تحميل الإعدادات')
    if data.get('package') not in ('basic', 'vip'):
        raise error(400, 'اختر الأساسية أو VIP')
    ends_on = str(data.get('ends_on') or '').strip()
    if ends_on:
        try:
            datetime.strptime(ends_on, '%Y-%m-%d')
        except ValueError:
            raise error(400, 'تاريخ نهاية العرض غير صحيح')
        if ends_on < _today():
            raise error(400, 'تاريخ نهاية العرض لازم يكون اليوم أو بعده')
    current = c.execute('SELECT enabled,starts_on FROM signup_offer WHERE id=1').fetchone()
    starts_on = (current['starts_on'] if current else '') or ''
    if data['enabled'] and (not current or not current['enabled']):
        starts_on = _today()  # إعادة تشغيل العرض تبدأ عدّه من اليوم
    updated = c.execute('''UPDATE signup_offer SET enabled=?,max_claims=?,package=?,months=?,ends_on=?,starts_on=?,revision=revision+1
        WHERE id=1 AND revision=?''', (int(data['enabled']), data['max_claims'], data['package'], data['months'], ends_on, starts_on, data['revision']))
    if updated.rowcount != 1:
        raise error(409, 'تغير العرض أو عدد المستفيدين؛ حدّث الصفحة قبل الحفظ')
    return overview(c)


def claim(c, organization_id, started, phones=(), registration=''):
    existing = c.execute('SELECT * FROM signup_offer_claims WHERE organization_id=?', (organization_id,)).fetchone()
    if existing:
        return dict(existing)
    if already_claimed(c, phones, registration):
        return None
    row = c.execute('''UPDATE signup_offer SET claimed=claimed+1,revision=revision+1
        WHERE id=1 AND enabled=1 AND claimed<max_claims AND (ends_on='' OR ends_on>=?) RETURNING package,months''', (_today(started),)).fetchone()
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
