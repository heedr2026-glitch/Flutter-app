"""رصيد خدمات المؤسسة: حد شهري حسب الباقة يتجدد من تاريخ الاشتراك، وسقف يومي للذكاء الاصطناعي.

مصدر واحد لكل من يسأل «هل بقي رصيد؟»: طلبات الذكاء الاصطناعي، إرسال واتساب، المكالمات،
لوحة الإدارة وصفحة الرصيد في التطبيق. القواعد:

* الحد من الباقة (platform_packages) ويتجدد كل شهر من يوم بداية الاشتراك (UTC).
* حد غير محدد في الباقة (NULL) يعني «بلا حد»: لا إيقاف حتى تحدده الإدارة.
* تعديل الإدارة اليدوي (platform_credit_ledger) يضاف إلى حد الدورة الحالية فقط.
* الذكاء الاصطناعي له سقف يومي حتى لا يُصرف رصيد الشهر في أيام قليلة.
* رصيد واتساب يحسب الرسائل الصادرة فقط؛ الوارد من العملاء لا يُمنع ولا يُحسب.
"""
import calendar
import math
from datetime import date, datetime, timedelta, timezone

SERVICES = ('whatsapp', 'ai', 'calls')
LABELS = {'whatsapp': 'واتساب', 'ai': 'الذكاء الاصطناعي', 'calls': 'المكالمات'}
DEFAULT_AI_DAILY = {'free': 5, 'basic': 30, 'vip': 100}
DAILY_MESSAGE = 'تم بلوغ الحد اليومي لموظفي AI'
_KNOWN_TABLES = set()


class Exhausted(Exception):
    def __init__(self, service, reason, message, renews_at):
        super().__init__(message)
        self.service = service
        self.reason = reason
        self.message = message
        self.renews_at = renews_at


def _is_postgres(c):
    return hasattr(c, '_connection')


def table_exists(c, name):
    """واتساب تُنشأ جداوله عند أول استخدام؛ لا نستعلم عن جدول غير موجود."""
    if _is_postgres(c):
        if name in _KNOWN_TABLES:
            return True
        found = c.execute(
            'SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?',
            (name,),
        ).fetchone() is not None
        if found:
            _KNOWN_TABLES.add(name)
        return found
    return c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _day(value):
    """تاريخ UTC من نص ISO أو None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date()
    except (ValueError, OverflowError):
        try:
            return date.fromisoformat(str(value)[:10])
        except (ValueError, OverflowError):
            return None


def _add_months(anchor, months):
    index = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def cycle(anchor, today):
    """بداية الدورة الحالية وبداية الدورة التالية (يوم التجديد)."""
    if anchor is None or anchor > today:
        anchor = today
    months = (today.year - anchor.year) * 12 + today.month - anchor.month
    if _add_months(anchor, months) > today:
        months -= 1
    return _add_months(anchor, months), _add_months(anchor, months + 1)


def _rows(c, sql, args=()):
    return [dict(row) for row in c.execute(sql, args).fetchall()]


def _chunks(values, size=400):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def snapshot_bulk(c, org_ids=None, today=None):
    """حالة الرصيد لعدة مؤسسات بعدد ثابت من الاستعلامات. org_ids=None تعني الكل."""
    today = today or datetime.now(timezone.utc).date()
    if org_ids is not None:
        org_ids = [int(x) for x in org_ids]
        if not org_ids:
            return {}

    def grouped(sql, args=(), column='organization_id'):
        if org_ids is None:
            return _rows(c, sql.replace('{scope}', '1=1'), args)
        out = []
        for chunk in _chunks(org_ids):
            marks = ','.join('?' for _ in chunk)
            out.extend(_rows(c, sql.replace('{scope}', column + ' IN (' + marks + ')'), [*args, *chunk]))
        return out

    plans = grouped(
        """SELECT o.id organization_id,o.created_at,s.package,s.starts_at
           FROM organizations o
           LEFT JOIN subscriptions s ON s.organization_id=o.id
           WHERE {scope}""", (), 'o.id')
    # تُقرأ الباقات بـ * حتى لا يفشل الاستعلام إن وصل طلب قبل اكتمال إضافة عمود ai_monthly عند أول تشغيل.
    packages = {}
    if table_exists(c, 'platform_packages'):
        packages = {row['package']: row for row in _rows(c, 'SELECT * FROM platform_packages')}
    for plan in plans:
        settings = packages.get(plan['package'] or 'free') or {}
        for key in ('ai_daily', 'ai_monthly', 'whatsapp_units', 'calls_units'):
            plan[key] = settings.get(key)
    cycles = {}
    for plan in plans:
        anchor = _day(plan['starts_at']) or _day(plan['created_at'])
        cycles[plan['organization_id']] = cycle(anchor, today)
    if not cycles:
        return {}
    window = min(start for start, _ in cycles.values())
    window_iso = window.isoformat()
    window_ts = int(datetime(window.year, window.month, window.day, tzinfo=timezone.utc).timestamp())
    today_iso = today.isoformat()

    # الاستهلاك مجمّعًا باليوم (UTC)، ثم يُجمع لكل مؤسسة من بداية دورتها.
    per_day = {service: {} for service in SERVICES}

    def add(service, org, day, amount):
        per_day[service].setdefault(org, {})
        per_day[service][org][day] = per_day[service][org].get(day, 0) + int(amount or 0)

    for row in grouped(
            'SELECT organization_id,substr(created_at,1,10) d,COUNT(*) n FROM ai_usage '
            'WHERE created_at>=? AND {scope} GROUP BY organization_id,substr(created_at,1,10)', (window_iso,)):
        add('ai', row['organization_id'], row['d'], row['n'])
    if table_exists(c, 'whatsapp_messages'):
        for row in grouped(
                "SELECT organization_id,timestamp/86400 d,COUNT(*) n FROM whatsapp_messages "
                "WHERE direction='outbound' AND state<>'failed' AND timestamp>=? AND {scope} "
                "GROUP BY organization_id,timestamp/86400", (window_ts,)):
            day = (date(1970, 1, 1) + timedelta(days=int(row['d']))).isoformat()
            add('whatsapp', row['organization_id'], day, row['n'])
    if table_exists(c, 'call_logs'):
        for row in grouped(
                # وقت الخادم (created_at) لا وقت مزود الاتصال، والمدد السالبة لا تُنقص الاستهلاك.
                'SELECT organization_id,substr(created_at,1,10) d,'
                'COALESCE(SUM(CASE WHEN duration_seconds>0 THEN duration_seconds ELSE 0 END),0) n FROM call_logs '
                'WHERE created_at>=? AND {scope} GROUP BY organization_id,substr(created_at,1,10)', (window_iso,)):
            add('calls', row['organization_id'], row['d'], row['n'])

    adjustments = {}
    if table_exists(c, 'platform_credit_ledger'):
        for row in grouped('SELECT organization_id,service,units,created_at FROM platform_credit_ledger '
                           'WHERE created_at>=? AND {scope}', (window_iso,)):
            org = row['organization_id']
            if org in cycles and row['service'] in SERVICES and str(row['created_at'])[:10] >= cycles[org][0].isoformat():
                totals = adjustments.setdefault(org, {service: 0 for service in SERVICES})
                totals[row['service']] += int(row['units'] or 0)
    overrides = {}
    if table_exists(c, 'ai_limits'):
        overrides = {row['organization_id']: int(row['daily_limit'])
                     for row in grouped('SELECT organization_id,daily_limit FROM ai_limits WHERE {scope}')}
    gifts = {}
    if table_exists(c, 'platform_daily_credits'):
        for row in grouped('SELECT organization_id,day,units FROM platform_daily_credits WHERE day>=? AND {scope}',
                           (window_iso,)):
            gifts.setdefault(row['organization_id'], {})[str(row['day'])[:10]] = int(row['units'] or 0)

    result = {}
    for plan in plans:
        org = plan['organization_id']
        start, end = cycles[org]
        start_iso = start.isoformat()
        package = plan['package'] or 'free'
        adjust = adjustments.get(org, {service: 0 for service in SERVICES})

        def used_since(service, since_iso):
            return sum(n for day, n in per_day[service].get(org, {}).items() if day >= since_iso)

        gift_days = gifts.get(org, {})
        gift_today = gift_days.get(today_iso, 0)
        gift_cycle = sum(n for day, n in gift_days.items() if day >= start_iso)
        package_daily = plan['ai_daily'] if plan['ai_daily'] is not None else DEFAULT_AI_DAILY.get(package, 5)
        base_daily = overrides.get(org, int(package_daily))
        # حد شهري غير محدد أو مؤسسة لها سقف يومي خاص: يُطبَّق السقف اليومي وحده كما كان،
        # والحد الشهري المعروض (اليومي × أيام الدورة) للعرض فقط؛ فتغيير الباقة أو السقف وسط الدورة لا يوقف المؤسسة.
        ai_monthly_enforced = plan['ai_monthly'] is not None and org not in overrides
        ai_base = int(plan['ai_monthly']) if ai_monthly_enforced else base_daily * (end - start).days
        services = {}
        for service in SERVICES:
            if service == 'ai':
                base = ai_base
                limit = max(0, base + adjust['ai'] + gift_cycle)
                used = used_since('ai', start_iso)
                used_today = used_since('ai', today_iso)
                # الرصيد الإضافي من الإدارة رصيد لمرة واحدة يُستخدم في أي يوم فوق السقف اليومي:
                # ما صُرف منه في الأيام السابقة يُخصم، فزيادة 50 تعني 50 طلبًا إضافيًا لا 50 كل يوم.
                spent = sum(max(0, n - base_daily - gift_days.get(day, 0))
                            for day, n in per_day['ai'].get(org, {}).items() if start_iso <= day < today_iso)
                daily_limit = max(0, base_daily + gift_today + max(0, max(0, adjust['ai']) - spent))
            else:
                units = plan['whatsapp_units'] if service == 'whatsapp' else plan['calls_units']
                base = None if units is None else int(units)
                limit = None if base is None else max(0, base + adjust[service])
                raw = used_since(service, start_iso)
                raw_today = used_since(service, today_iso)
                used = math.ceil(raw / 60) if service == 'calls' else raw
                used_today = math.ceil(raw_today / 60) if service == 'calls' else raw_today
                daily_limit = None
            enforced = limit is not None and (service != 'ai' or ai_monthly_enforced)
            reason = ''
            if enforced and used >= limit:
                reason = 'monthly'
            elif daily_limit is not None and used_today >= daily_limit:
                reason = 'daily'
            services[service] = {
                'label': LABELS[service], 'base': base, 'adjustments': adjust[service], 'limit': limit,
                'used': used, 'used_today': used_today, 'daily_limit': daily_limit,
                'remaining': None if limit is None else max(0, limit - used),
                'unlimited': limit is None, 'monthly_enforced': enforced, 'blocked': bool(reason), 'reason': reason,
            }
        result[org] = {'package': package, 'cycle_start': start_iso, 'renews_at': end.isoformat(), 'services': services}
    return result


def snapshot(c, org, today=None):
    found = snapshot_bulk(c, [org], today).get(int(org))
    if found is None:
        raise LookupError('المؤسسة غير موجودة')
    return found


def exhausted_message(service, reason, renews_at):
    if reason == 'daily':
        return DAILY_MESSAGE
    return 'انتهى رصيد %s لهذا الشهر. يتجدد الرصيد يوم %s، أو يمكنك ترقية الباقة.' % (LABELS[service], renews_at)


def check(c, org, service, today=None):
    """يرفع Exhausted إذا انتهى رصيد الخدمة؛ وإلا يعيد حالة الخدمة."""
    state = snapshot(c, org, today)
    item = state['services'][service]
    if item['blocked']:
        raise Exhausted(service, item['reason'], exhausted_message(service, item['reason'], state['renews_at']), state['renews_at'])
    return item
