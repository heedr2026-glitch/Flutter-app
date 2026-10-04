"""Evidence-based support triage. No production repair or external credentials."""
from datetime import datetime, timedelta, timezone
import json
import re

POLICY = '''أنت اسألني – الموظف التقني في إدارة خدووم، مؤسستك في جيبك.
تخاطب المدير والفريق التقني بسعودية بسيطة ومهنية. تواصل المشترك عبر تذكرة الدعم فقط ولا تتواصل مع عملائه.
استقبل الشكوى وسجلها، اسأل عن الناقص فقط، افحص السجلات وحالة الخدمات وآخر تحديث متاح.
سم السبب مؤكدًا مع دليل أو محتملًا مع الفحص الناقص. لا تخترع نتائج ولا تقول تم الحل قبل تحقق فعلي.
قراءة مؤشرات الخدمات والمشترك والأداء مسموحة. كتابة الشكاوى والتقارير وتنبيهات المدير ورسائل الدعم مسموحة.
إعادة تشغيل الخدمات أو تعديل بيانات التشغيل أو الاشتراكات أو الكود تحتاج موافقة المدير المحددة قبل التنفيذ.
تغيير المفاتيح والتوكنات اقتراح فقط والمدير ينفذه. ممنوع حذف البيانات والجداول أو قراءة .env وكلمات المرور أو عرض الأسرار.
لا تعد بناء المشروع ولا تغير ميزة شغالة بلا سبب. وثق الآمر والإجراء والوقت والنتيجة، وتحقق من الخدمات المرتبطة بعد التعديل.
حرجة: توقف خدمة أو استقبال واتساب أو ضياع بيانات؛ عالية: تعطل ميزة أساسية؛ متوسطة: بطء أو خلل جزئي؛ منخفضة: تحسين.
بعد المعالجة أعد الفحص، ارفع تقريرًا للمدير، وأرسل للمشترك النتيجة أو خطوات آمنة. عند تعذر الحل صعد العائق والخطوة التالية.
لا تترك جاري الحل بلا تحديث؛ سجل انتظار المشترك أو الموافقة أو التصعيد. راجع المتأخر والمتكرر ونبه المدير واقترح حلًا جذريًا.
التقرير: الأولوية، الحالة، ما حدث، السبب ودرجة تأكيده والدليل، الحل بخطوات، ما نفذ ونتيجته، التحقق، رسالة المشترك، الموافقة، العائق والمتابعة.
تقرير المنصة: وقت آخر فحص، حالة الخدمات والربط، الشكاوى الجديدة والمحلولة والمفتوحة والمتأخرة، المتكرر، سرعة الصفحات، توصيات بالأولوية.
لا تقل كل شيء تمام دون فحص. قل ما قدرت أفحص هذا لأن وحدد الأداة أو الصلاحية الناقصة. لا تدع مراقبة أو تنفيذ أدوات غير متاحة.'''


def utcnow():
    return datetime.now(timezone.utc)


# ===================== معرفة الموظف التقني: يتعرف على الشكوى، يقرأ الحقائق، ويتعلم =====================
# كل ما هنا قراءة فقط. لا يُغيَّر اشتراك ولا مستخدم ولا جهاز؛ التنفيذ يبقى للإدارة.

_AR_MARKS = re.compile('[ً-ٰٟـ]')
_STOP = set("""في من على الى إلى عن مع هذا هذه ذلك التي الذي انا أنا انت هو هي نحن كان كانت يكون لما لكن بعد قبل عند عندي عندنا
كل شي شيء اي أي ما لا لم لن قد هل او أو ثم بس اذا إذا يعني مره مرة جدا جداً ابي أبي ابغى أبغى ممكن لو سمحت السلام عليكم مرحبا
طلب مشكلة مشكله المشكلة عندما حتى الان الآن اليوم امس أمس خدوم خدووم التطبيق تطبيق""".split())


def normalize(text):
    """توحيد الكتابة العربية حتى تتطابق الكلمات مهما اختلفت الهمزات والتاء المربوطة."""
    text = _AR_MARKS.sub('', str(text or '').casefold())
    for old, new in (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ى', 'ي'), ('ة', 'ه'), ('ؤ', 'و'), ('ئ', 'ي')):
        text = text.replace(old, new)
    return text


_STOP_NORMAL = {normalize(word) for word in _STOP}
_WORD = re.compile('[0-9a-zء-ي]+')


def tokens(text):
    return [word for word in _WORD.findall(normalize(text)) if len(word) >= 3 and word not in _STOP_NORMAL]


# نوع الشكوى ← جذور الكلمات التي تدل عليه (بعد التوحيد). الأكثر تطابقًا يفوز.
PROBLEM_TYPES = {
    'subscription': ('باقه', 'باقت', 'اشتراك', 'تفعل', 'تفعيل', 'ترقيه', 'حواله', 'تحويل', 'دفعت', 'ايصال'),
    'ads': ('اعلان',),
    'login': ('دخول', 'يدخل', 'ادخل', 'معلق', 'موقوف', 'محظور', 'المرور', 'باسورد'),
    'save': ('نحفظ', 'يحفظ', 'الحفظ', 'يضيف', 'اضفت', 'اضيف', 'اضافه'),
    'tracking': ('تتبع', 'موقع', 'خريطه'),
}
TYPE_NAMES = {'subscription': 'تفعيل الباقة', 'ads': 'إنشاء الإعلانات', 'login': 'الدخول للحساب', 'save': 'حفظ موظف أو مركبة', 'tracking': 'تتبع المركبة', 'whatsapp': 'واتساب', 'performance': 'بطء التطبيق'}
_EMPLOYEE = ('موظف',)
_VEHICLE = ('مركب', 'سيار')


def _mentions(text, stems):
    return any(stem in text for stem in stems)


def recognize(text):
    """نوع الشكوى إن كان من الأنواع المعروفة، وإلا نص فارغ (شكوى جديدة عليه)."""
    text = normalize(text)
    scores = {kind: sum(1 for stem in stems if stem in text) for kind, stems in PROBLEM_TYPES.items()}
    # «ما ينحفظ» بدون ذكر موظف أو مركبة شكوى أخرى؛ والموقع بدون مركبة ليس تتبعًا.
    if not _mentions(text, _EMPLOYEE + _VEHICLE):
        scores['save'] = 0
    if 'تتبع' not in text and not _mentions(text, _VEHICLE):
        scores['tracking'] = 0
    # عند التعادل يفوز النوع الأخص (التتبع قبل الباقة مثلًا).
    best = max(('tracking', 'ads', 'save', 'login', 'subscription'), key=lambda kind: scores[kind])
    return best if scores[best] else ''


def _day(value):
    return str(value or '')[:10] or 'غير محدد'


def collect_facts(c, ticket, owner, server):
    """حقائق المؤسسة التي تفسر الشكاوى المعروفة. قراءة فقط، وكل جدول غير موجود يُتجاوز بهدوء."""
    org = ticket['organization_id']
    has = lambda name: owner.table_exists(c, name, server)
    one = lambda sql, args=(): c.execute(sql, args).fetchone()
    now_iso = utcnow().isoformat()
    week = (utcnow() - timedelta(days=7)).isoformat()
    facts = {'package': 'free', 'expiresAt': None, 'expired': False}
    row = one('SELECT package,expires_at FROM subscriptions WHERE organization_id=?', (org,)) if has('subscriptions') else None
    if row:
        facts.update(package=row['package'] or 'free', expiresAt=row['expires_at'], expired=bool(row['expires_at'] and row['expires_at'] <= now_iso))
    facts['suspended'] = bool(owner.suspended(c, org)) if has('platform_org_state') else False
    limits = {}
    for resource, table, where in (('employees', 'users', " AND role='employee'"), ('vehicles', 'vehicles', '')):
        if has(table):
            limits[resource] = {'limit': server.package_resource_limit(facts['package'], resource, c), 'count': one('SELECT COUNT(*) AS n FROM ' + table + ' WHERE organization_id=?' + where, (org,))['n']}
    facts['limits'] = limits
    if has('subscription_requests'):
        row = one('SELECT requested_package,status,created_at,processed_at FROM subscription_requests WHERE organization_id=? ORDER BY id DESC LIMIT 1', (org,))
        facts['subscriptionRequest'] = dict(row) if row else None
    if has('login_failures'):
        facts['loginFailures'] = [dict(r) for r in c.execute('SELECT reason,COUNT(*) AS n,MAX(username) AS username,MAX(created_at) AS last_at FROM login_failures WHERE organization_id=? AND created_at>=? GROUP BY reason', (org, week)).fetchall()]
    if has('users'):
        facts['inactiveUsers'] = [r['username'] for r in c.execute('SELECT username FROM users WHERE organization_id=? AND active=0 AND archived_at IS NULL ORDER BY id LIMIT 10', (org,)).fetchall()]
    if has('blocked_devices'):
        facts['blockedDevices'] = one('SELECT COUNT(*) AS n FROM blocked_devices WHERE organization_id=?', (org,))['n']
    if has('advertisements'):
        facts['ads'] = {'pending': one('SELECT COUNT(*) AS n FROM advertisements WHERE organization_id=? AND approved=0 AND active=1 AND COALESCE(deleted,0)=0', (org,))['n']}
    if has('vehicle_tracking_schedules'):
        tracking = {'vehicles': [dict(r) for r in c.execute('SELECT vehicle_name,enabled,phone_status,last_heartbeat,linked_at FROM vehicle_tracking_schedules WHERE organization_id=? ORDER BY vehicle_name LIMIT 20', (org,)).fetchall()]}
        if has('vehicle_location_events'):
            tracking['lastLocationAt'] = one('SELECT MAX(recorded_at) AS last_at FROM vehicle_location_events WHERE organization_id=?', (org,))['last_at']
        facts['tracking'] = tracking
    if has('organization_api_errors'):
        facts['recentErrors'] = [dict(r) for r in c.execute('SELECT method,route,status,message,created_at FROM organization_api_errors WHERE organization_id=? AND created_at>=? ORDER BY id DESC LIMIT 8', (org, (utcnow() - timedelta(days=3)).isoformat())).fetchall()]
    return facts


def _error_for(facts, *fragments):
    for error in facts.get('recentErrors') or []:
        if any(fragment in error['route'] for fragment in fragments):
            return error
    return None


def _error_outcome(error, action):
    """نتيجة التشخيص من رسالة خطأ مسجلة: خطأ الخادم يحتاج مطوّرًا، وخطأ البيانات يُشرح للمشترك."""
    when = _day(error['created_at'])
    if int(error['status'] or 0) >= 500:
        return ('آخر محاولة ' + action + ' بتاريخ ' + when + ' فشلت بخطأ داخل الخادم (' + str(error['message']) + ') عند ' + str(error['route']) + '.', True,
                'آخر محاولة ' + action + ' فشلت بسبب خطأ عندنا في الخادم، وليس من بياناتك. حوّلناه للإدارة لإصلاحه وسنرد عليك هنا.',
                'خلل برمجي يحتاج تطويرًا: ' + str(error['route']) + ' يرجع ' + str(error['message']) + '.')
    return ('آخر محاولة ' + action + ' رُفضت بالرسالة: «' + str(error['message']) + '» بتاريخ ' + when + '.', True,
            'آخر محاولة ' + action + ' توقفت بسبب: ' + str(error['message']) + '. صحّح على هذا الأساس وجرّب مرة ثانية.', '')


PACKAGE_NAMES = {'free': 'المجانية', 'basic': 'الأساسية', 'vip': 'VIP'}
PHONE_STATUS = {'not_enabled': 'التتبع غير مفعّل في جوال السائق', 'stopped': 'السائق أوقف التتبع من جواله', 'permission_denied': 'جوال السائق رفض إذن الموقع', 'location_disabled': 'خدمة الموقع (GPS) مقفلة في جوال السائق'}


def diagnose_known(kind, facts, text):
    """يرجع (السبب، مؤكد؟، ما يقال للمشترك، ما يحتاجه من الإدارة أو '')."""
    package = PACKAGE_NAMES.get(facts['package'], facts['package'])
    if kind == 'subscription':
        request = facts.get('subscriptionRequest')
        if not request:
            return ('لا يوجد طلب اشتراك مسجل لهذه المؤسسة؛ الباقة الحالية ' + package + '.', True,
                    'لم يصلنا طلب اشتراك من حسابك، وباقتك الحالية ' + package + '. من صفحة الباقات اختر الباقة وأرفق إيصال التحويل ثم أرسل الطلب.', '')
        wanted = PACKAGE_NAMES.get(request['requested_package'], request['requested_package'])
        if request['status'] == 'pending':
            return ('طلب الاشتراك في باقة ' + wanted + ' مرسل بتاريخ ' + _day(request['created_at']) + ' وما زال بانتظار اعتماد الإدارة.', True,
                    'طلب اشتراكك في باقة ' + wanted + ' وصلنا بتاريخ ' + _day(request['created_at']) + ' وهو قيد المراجعة. تتفعل الباقة مباشرة بعد اعتماد التحويل.',
                    'طلب اشتراك بانتظارك: راجعه في «طلبات الاشتراك» واعتمده أو ارفضه.')
        if request['status'] == 'rejected':
            return ('آخر طلب اشتراك (باقة ' + wanted + ') رُفض بتاريخ ' + _day(request['processed_at']) + '.', True,
                    'طلب اشتراكك في باقة ' + wanted + ' لم يُعتمد. أعد إرسال الطلب من صفحة الباقات بإيصال تحويل واضح، أو راسلنا هنا بتفاصيل التحويل.', '')
        if facts['package'] == request['requested_package'] and not facts['expired']:
            return ('الباقة ' + wanted + ' مفعّلة فعلًا حتى ' + _day(facts['expiresAt']) + '؛ الطلب معتمد بتاريخ ' + _day(request['processed_at']) + '.', True,
                    'باقتك ' + wanted + ' مفعّلة حتى ' + _day(facts['expiresAt']) + '. سجّل خروج من التطبيق ثم ادخل من جديد لتظهر لك مزاياها.', '')
        return ('الطلب معتمد بتاريخ ' + _day(request['processed_at']) + ' لكن الاشتراك الحالي (' + package + ('، منتهي' if facts['expired'] else '') + ') لا يطابقه.', True,
                'طلبك معتمد لكن الباقة لم تظهر على حسابك كما يجب. حوّلنا طلبك للإدارة لتصحيحه وسنرد عليك هنا.',
                'طلب اشتراك معتمد لكن الباقة الحالية لا تطابقه؛ يحتاج تصحيحًا يدويًا من ملف المؤسسة.')
    if kind == 'ads':
        if facts['package'] != 'vip':
            return ('إنشاء الإعلانات متاح لباقة VIP فقط، وباقة المؤسسة ' + package + '.', True,
                    'إنشاء الإعلانات من مزايا باقة VIP، وباقتك الحالية ' + package + '. تقدر تترقى من صفحة الباقات.', '')
        if (facts.get('ads') or {}).get('pending'):
            return ('للمؤسسة ' + str(facts['ads']['pending']) + ' إعلان بانتظار مراجعة الإدارة.', True,
                    'إعلانك وصلنا وهو بانتظار مراجعة الإدارة، ويظهر للمشتركين بعد اعتماده.', 'إعلان مؤسسة بانتظار مراجعتك في «الإعلانات ← الطلبات».')
        error = _error_for(facts, '/api/ads', '/api/my-ads')
        if error:
            return _error_outcome(error, 'لإنشاء الإعلان')
        return ('الباقة VIP وتسمح بالإعلانات، ولا يوجد خطأ مسجل؛ السبب لم يتأكد.', False, '', '')
    if kind == 'login':
        if facts['suspended']:
            return ('المؤسسة موقوفة من إدارة خدووم، ولذلك يُرفض دخول مستخدميها.', True,
                    'حساب مؤسستك موقوف من إدارة خدووم. حوّلنا طلبك للإدارة وسنرد عليك هنا.', 'مؤسسة موقوفة تطلب الدخول؛ القرار لك من ملف المؤسسة.')
        reasons = {row['reason']: row for row in facts.get('loginFailures') or []}
        if 'account_inactive' in reasons:
            name, count = str(reasons['account_inactive']['username']), str(reasons['account_inactive']['n'])
            return ('المستخدم «' + name + '» موقوف داخل المؤسسة، ورُفض دخوله ' + count + ' مرة هذا الأسبوع.', True,
                    'المستخدم «' + name + '» موقوف من إدارة مؤسستك. مدير المؤسسة يعيد تفعيله من صفحة المستخدمين في التطبيق.', '')
        if 'device_blocked' in reasons:
            return ('محاولة دخول من جهاز محظور داخل المؤسسة (' + str(reasons['device_blocked']['n']) + ' مرة هذا الأسبوع).', True,
                    'الجهاز الذي تحاول الدخول منه محظور من مدير مؤسستك. مدير المؤسسة يلغي الحظر من صفحة الأمان والأجهزة في التطبيق.', '')
        if 'password_verification_failed' in reasons:
            name, count = str(reasons['password_verification_failed']['username']), str(reasons['password_verification_failed']['n'])
            return ('كلمة المرور غير صحيحة للمستخدم «' + name + '»: ' + count + ' محاولة هذا الأسبوع.', True,
                    'محاولات الدخول رُفضت لأن كلمة المرور غير صحيحة. مدير المؤسسة يقدر يعيّن كلمة مرور جديدة للمستخدم من صفحة المستخدمين.', '')
        if 'user_not_found' in reasons:
            return ('محاولات دخول باسم مستخدم غير موجود.', True, 'اسم المستخدم المكتوب غير مسجل عندنا. تأكد من اسم الدخول كما سجّله مدير المؤسسة.', '')
        if facts.get('inactiveUsers'):
            return ('لا توجد محاولات دخول مرفوضة هذا الأسبوع، لكن في المؤسسة مستخدمون موقوفون: ' + '، '.join(facts['inactiveUsers'][:5]) + '.', False, '', '')
        return ('لا توجد محاولات دخول مرفوضة مسجلة لهذه المؤسسة خلال 7 أيام؛ السبب لم يتأكد.', False, '', '')
    if kind == 'save':
        normal = normalize(text)
        labels = {'employees': 'الموظفين', 'vehicles': 'المركبات'}
        for resource, stems in (('employees', _EMPLOYEE), ('vehicles', _VEHICLE)):
            state = (facts.get('limits') or {}).get(resource)
            if _mentions(normal, stems) and state and state['limit'] is not None and state['count'] >= state['limit']:
                return ('وصلت المؤسسة حد ' + labels[resource] + ' في باقة ' + package + ': ' + str(state['count']) + ' من ' + str(state['limit']) + '.', True,
                        'وصلت الحد المسموح من ' + labels[resource] + ' في باقتك ' + package + ' (' + str(state['count']) + ' من ' + str(state['limit']) + ')، لذلك لا يُحفظ الجديد. تقدر تحذف غير المستخدم أو تترقى من صفحة الباقات.', '')
        error = _error_for(facts, '/api/employee', '/api/users', '/api/vehicles')
        if error:
            return _error_outcome(error, 'للحفظ')
        if _mentions(normal, _VEHICLE) and not _mentions(normal, _EMPLOYEE):
            # بيانات المركبات يحفظها التطبيق داخل الجوال، فلا يصل للخادم خطأ نراه من هنا.
            return ('بيانات المركبات تُحفظ داخل جوال المشترك لا في الخادم، ولا يوجد خطأ مسجل؛ السبب لا يظهر من الخادم.', False, '', '')
        return ('حد الباقة لم يُتجاوز ولا يوجد خطأ حفظ مسجل؛ السبب لم يتأكد وقد يكون خللًا يحتاج مراجعة.', False, '', '')
    if kind == 'tracking':
        tracking = facts.get('tracking') or {}
        vehicles = tracking.get('vehicles') or []
        if not vehicles:
            return ('لا توجد مركبة مفعّل لها التتبع في هذه المؤسسة.', True,
                    'لم يُفعّل التتبع لأي مركبة بعد. من صفحة المركبة فعّل التتبع واربط جوال السائق بالباركود.', '')
        for vehicle in vehicles:
            if vehicle['phone_status'] in PHONE_STATUS:
                return ('المركبة «' + str(vehicle['vehicle_name']) + '»: ' + PHONE_STATUS[vehicle['phone_status']] + '.', True,
                        'المركبة «' + str(vehicle['vehicle_name']) + '»: ' + PHONE_STATUS[vehicle['phone_status']] + '. افتح التطبيق في جوال السائق وفعّل الموقع واسمح بالإذن «دائمًا».', '')
        if not tracking.get('lastLocationAt'):
            return ('التتبع مفعّل لكن لم يصل أي موقع من جوال السائق حتى الآن.', True,
                    'التتبع مفعّل لكن لم يصلنا أي موقع من جوال السائق. تأكد أن جوال السائق مربوط بالباركود وأن الإنترنت والموقع شغّالين فيه.', '')
        return ('آخر موقع وصل بتاريخ ' + str(tracking['lastLocationAt'])[:16].replace('T', ' ') + ' (UTC)؛ التتبع يعمل داخل ساعات الجدول فقط.', False, '', '')
    return ('', False, '', '')


def match_playbook(c, text, owner, server):
    """حل تعلّمه الموظف التقني من شكوى سابقة أغلقتها الإدارة بيدها."""
    if not owner.table_exists(c, 'technical_playbooks', server):
        return None
    words = set(tokens(text))
    best = None
    for row in c.execute('SELECT id,title,signals,solution,ticket_id FROM technical_playbooks WHERE active=1 ORDER BY id DESC LIMIT 300').fetchall():
        try:
            signals = set(json.loads(row['signals'] or '[]'))
        except ValueError:
            continue
        shared = len(words & signals)
        if signals and shared >= 2 and shared / len(signals) >= .5 and (best is None or shared > best[0]):
            best = (shared, dict(row))
    return best[1] if best else None


def learn_from_resolution(c, ticket, resolution, actor, owner, server):
    """عند إغلاق الإدارة لشكوى لم يعرفها الموظف التقني: يحفظ الحل ليتعرف على مثيلاتها لاحقًا."""
    resolution = str(resolution or '').strip()
    if len(resolution) < 15 or not owner.table_exists(c, 'technical_playbooks', server):
        return None
    task = c.execute('SELECT id,knowledge FROM technical_tasks WHERE support_ticket_id=? ORDER BY id DESC LIMIT 1', (ticket['id'],)).fetchone()
    if not task or task['knowledge'] != 'novel':
        return None
    if c.execute('SELECT 1 FROM technical_playbooks WHERE ticket_id=?', (ticket['id'],)).fetchone():
        return None
    words = []
    for word in tokens(' '.join(str(ticket.get(key) or '') for key in ('category', 'title', 'message'))):
        if word not in words:
            words.append(word)
    if len(words) < 2:
        return None
    row = c.execute('INSERT INTO technical_playbooks(title,signals,solution,ticket_id,created_by,created_at) VALUES(?,?,?,?,?,?) RETURNING id',
                    (str(ticket.get('title') or ticket.get('category') or 'شكوى')[:160], json.dumps(words[:10], ensure_ascii=False), resolution[:1500], ticket['id'], str(actor)[:120], utcnow().isoformat())).fetchone()
    c.execute("UPDATE technical_tasks SET knowledge='taught' WHERE id=?", (task['id'],))
    return row['id']


def understand(c, ticket, owner, server, legacy_service=''):
    """قلب المعرفة: هل الشكوى من نوع معروف وما سببها المؤكد؟ أم تعلّمها سابقًا؟ أم جديدة عليه؟"""
    text = ' '.join(str(ticket.get(key) or '') for key in ('category', 'title', 'message'))
    kind = recognize(text)
    facts = collect_facts(c, ticket, owner, server)
    result = {'problemType': kind, 'facts': facts, 'knowledge': 'novel', 'confirmed': False, 'cause': '', 'customerReply': '', 'ownerAction': '', 'playbookId': None}
    if kind:
        cause, confirmed, customer, owner_action = diagnose_known(kind, facts, text)
        result.update(knowledge='known', cause=cause, confirmed=confirmed, customerReply=customer, ownerAction=owner_action)
        return result
    if legacy_service in ('whatsapp', 'performance'):
        # أنواع يفحصها الفاحص القديم بمؤشراته (ربط واتساب، سرعة الصفحات)؛ معروفة لكن سببها لا يتأكد من القراءة وحدها.
        result.update(knowledge='known', problemType=legacy_service)
        return result
    playbook = match_playbook(c, text, owner, server)
    if playbook:
        c.execute('UPDATE technical_playbooks SET uses=uses+1 WHERE id=?', (playbook['id'],))
        result.update(knowledge='learned', playbookId=playbook['id'], cause='شكوى مشابهة لطلب سابق #' + str(playbook['ticket_id']) + ' («' + playbook['title'] + '»).',
                      ownerAction='أعرف حلها من حالة سابقة. الحل الذي كتبته الإدارة وقتها: ' + playbook['solution'] + ' — راجعه وأرسله للمشترك إذا يناسب حالته.')
    return result



def inspect_ticket(c, ticket, owner, server):
    """Read only scoped diagnostic metadata; never read message content or secrets."""
    t = dict(ticket)
    org_id = t['organization_id']
    cutoff = (utcnow() - timedelta(hours=24)).isoformat()
    checks, limitations = [], []

    def read(key, label, sql, args, describe):
        # Check existence before issuing SQL: a missing Postgres table aborts a transaction.
        import re
        tables = re.findall(r'\b(?:FROM|JOIN)\s+([a-z_]+)', sql, re.I)
        if any(not owner.table_exists(c, name, server) for name in tables):
            limitations.append('ما قدرت أفحص ' + label + ' لأن سجل الخدمة غير متاح')
            return None
        row = c.execute(sql, args).fetchone()
        details, warning = describe(dict(row) if row else None)
        checks.append({'key': key, 'label': label, 'status': 'warning' if warning else 'ok', 'details': details})
        return row

    read('database', 'قاعدة البيانات', 'SELECT id,name FROM organizations WHERE id=?', (org_id,),
         lambda r: ('استعلام المؤسسة نجح' if r else 'المؤسسة غير موجودة', not bool(r)))
    read('package', 'الباقة', 'SELECT package,expires_at FROM subscriptions WHERE organization_id=?', (org_id,),
         lambda r: (('الباقة: ' + r['package'] + ('؛ انتهت' if r.get('expires_at') and r['expires_at'] <= utcnow().isoformat() else '')) if r else 'لا يوجد اشتراك مسجل', not r or bool(r.get('expires_at') and r['expires_at'] <= utcnow().isoformat())))
    read('users', 'المستخدمون', 'SELECT COUNT(*) n FROM users WHERE organization_id=? AND active=1', (org_id,),
         lambda r: (str(r['n']) + ' مستخدم نشط', r['n'] == 0))
    failures = read('login', 'سجل الدخول', 'SELECT COUNT(*) n FROM login_failures WHERE organization_id=? AND created_at>=?', (org_id, cutoff),
         lambda r: (str(r['n']) + ' محاولة فاشلة خلال 24 ساعة؛ العدد لا يثبت سبب الشكوى', r['n'] > 0))
    read('performance', 'سرعة الصفحات', 'SELECT COUNT(*) n,AVG(elapsed_ms) avg_ms,MAX(elapsed_ms) max_ms FROM page_performance_events WHERE organization_id=? AND created_at>=?', (org_id, cutoff),
         lambda r: ((str(r['n']) + ' قياس مسجل؛ المتوسط ' + str(round(r['avg_ms'] or 0)) + 'ms؛ الأعلى ' + str(r['max_ms'] or 0) + 'ms') if r['n'] else 'لا توجد قياسات حديثة؛ لا يمكن الحكم على السرعة', not r['n'] or (r['max_ms'] or 0) >= 1500))
    text = (t.get('category', '') + ' ' + t.get('message', '')).casefold()
    service = 'whatsapp' if any(x in text for x in ('واتساب', 'وتساب', 'whatsapp')) else 'performance' if any(x in text for x in ('بطء', 'بطي', 'بطئ', 'سرعة')) else 'login' if any(x in text for x in ('دخول', 'كلمة المرور')) else 'support'
    if service == 'whatsapp':
        read('whatsapp', 'ربط واتساب', 'SELECT updated_at FROM whatsapp_connections WHERE organization_id=?', (org_id,),
             lambda r: ('إعداد ربط محفوظ؛ هذا لا يثبت نجاح الإرسال والاستقبال' if r else 'لا يوجد ربط محفوظ', True))
        read('webhook', 'آخر وصول webhook', 'SELECT MAX(w.received_at) last_at FROM whatsapp_webhooks w JOIN whatsapp_connections x ON x.phone_number_id=w.phone_number_id WHERE x.organization_id=?', (org_id,),
             lambda r: ('آخر وصول مسجل: ' + str(r['last_at'] or 'لا يوجد') + '؛ لم يجر اختبار رسالة جديدة', True))
        limitations.append('ما قدرت أختبر واتساب مباشرة لأن أداة اختبار المزود غير مربوطة بهذا الفاحص')
    limitations.append('لم يُنفذ إصلاح أو اختبار إعادة إنتاج من جهاز المشترك؛ أدوات إعادة التشغيل والتعديل غير متاحة تلقائيًا')
    repeated = c.execute("SELECT COUNT(*) n FROM support_tickets WHERE organization_id=? AND category=? AND id<>? AND created_at>=?", (org_id, t.get('category', ''), t['id'], (utcnow()-timedelta(days=30)).isoformat())).fetchone()['n']
    severity = 'critical' if any(x in text for x in ('ضياع بيانات', 'فقد بيانات', 'ما يستقبل', 'لا يستقبل', 'الخدمة واقفة')) else 'high' if service in ('login', 'whatsapp') else 'medium'
    cause = 'لم يتحدد سبب الشكوى؛ مؤشرات الفحص أدناه لا تثبت سببًا جذريًا.'
    if repeated:
        cause += ' مشكلة متكررة محتملة: توجد ' + str(repeated) + ' شكاوى أخرى بنفس التصنيف خلال 30 يومًا؛ يلزم مقارنة الأسباب.'
    proposal = '1. اطلب وقت آخر حدوث وصورة الخطأ وخطوات إعادة المشكلة إن لم تكن مرفقة.\n2. قارن التجربة بالسجلات.\n3. ارفع خطة إصلاح محددة للمدير قبل تغيير التشغيل.\n4. أعد اختبار الوظيفة ثم اطلب تأكيد المشترك.'
    report = {'checkedAt': utcnow().isoformat(), 'ticketId': t['id'], 'service': service, 'severity': severity,
              'causeConfidence': 'unconfirmed', 'diagnosis': cause, 'checks': checks, 'limitations': limitations,
              'proposal': proposal, 'requiresApproval': True, 'repeated': repeated, 'verifiedResolved': False}
    return report


def process_ticket(c, ticket, owner, server):
    t = dict(ticket)
    report = inspect_ticket(c, t, owner, server)
    ts = report['checkedAt']
    know = understand(c, t, owner, server, report['service'])
    facts = know.pop('facts')
    report.update(knowledge=know['knowledge'], problemType=know['problemType'], ownerAction=know['ownerAction'])
    if know['knowledge'] == 'known':
        report['diagnosis'] = know['cause'] or report['diagnosis']
        report['causeConfidence'] = 'confirmed' if know['confirmed'] else 'unconfirmed'
        if know['confirmed']:
            report['proposal'] = know['ownerAction'] or 'أُبلغ المشترك بالسبب وطريقة الحل؛ لا يلزم إجراء من الإدارة.'
            report['requiresApproval'] = bool(know['ownerAction'])
    elif know['knowledge'] == 'learned':
        report['diagnosis'] = know['cause']
        report['proposal'] = know['ownerAction']
    else:
        report['diagnosis'] = 'شكوى من نوع جديد لم يمر عليّ من قبل؛ ما عرفت أحلها.'
        report['proposal'] = 'تحتاج مراجعتك. إذا كانت ميزة ناقصة أو خللًا في البرنامج فهي تحتاج تطويرًا. عند إغلاقك الطلب مع كتابة الحل أتعلمه للمرات القادمة.'
    resolved_by_explanation = know['knowledge'] == 'known' and know['confirmed'] and not know['ownerAction']
    needs_owner = 0 if resolved_by_explanation else 1
    task = c.execute('SELECT id FROM technical_tasks WHERE support_ticket_id=? ORDER BY id DESC LIMIT 1', (t['id'],)).fetchone()
    if not task:
        task = c.execute('INSERT INTO technical_tasks(organization_id,user_id,support_ticket_id,service,problem,severity,status,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id',
             (t['organization_id'], t.get('user_id'), t['id'], 'support', 'طلب دعم #' + str(t['id']) + ': ' + t.get('category', ''), report['severity'], 'queued', ts, 'support-monitor')).fetchone()
    result = ('الحالة: تحدد السبب وأُبلغ المشترك بطريقة الحل؛ بانتظار تأكيده.' if resolved_by_explanation else 'الحالة: بانتظار استكمال الفحص؛ لم يتم الحل.') + '\nالسبب: ' + report['diagnosis'] + '\nالفحص: ' + '\n'.join(x['label'] + ': ' + x['details'] for x in report['checks']) + '\nالعوائق: ' + '\n'.join(report['limitations']) + '\nالخطوة التالية: ' + report['proposal']
    c.execute("UPDATE technical_tasks SET status=?,severity=?,diagnosis=?,proposal=?,action_taken=?,result=?,finished_at=NULL WHERE id=?",
         ('diagnosed' if resolved_by_explanation else 'proposed', report['severity'], report['diagnosis'], report['proposal'], 'فحص سجلات فقط؛ لم تتغير بيانات التشغيل', result, task['id']))
    # تفسير الذكاء يُطلب مرة واحدة لكل شكوى جديدة؛ نحافظ عليه عند إعادة الفحص.
    c.execute("UPDATE technical_tasks SET knowledge=CASE WHEN knowledge='taught' THEN knowledge ELSE ? END,problem_type=?,needs_owner=?,facts=? WHERE id=?",
         (know['knowledge'], know['problemType'], needs_owner, json.dumps(facts, ensure_ascii=False, default=str)[:6000], task['id']))
    stored = c.execute('SELECT interpretation FROM technical_tasks WHERE id=?', (task['id'],)).fetchone()
    lines = interpretation_lines(stored['interpretation'] if stored else '')
    if lines and know['knowledge'] == 'novel':
        c.execute('UPDATE technical_tasks SET diagnosis=?,proposal=? WHERE id=?', (lines[0], lines[1], task['id']))
    reply = ('راجعنا مؤشرات الخدمة المسجلة لطلبك #' + str(t['id']) + '، لكن سبب المشكلة لم يتأكد ولم نعلن حلها. ' +
             'إذا لم ترفق التفاصيل، أرسل وقت آخر حدوث وصورة الخطأ وخطوات تكراره، دون كلمة مرور أو رموز تحقق. ' +
             'الخطوة التالية: مقارنة التجربة بالسجلات ثم مراجعة خطة المعالجة مع الإدارة. إذا أرسلت التفاصيل بالفعل فلا تحتاج تكرارها.')
    if know['knowledge'] == 'known' and know['confirmed']:
        reply = 'راجعنا طلبك #' + str(t['id']) + '. ' + know['customerReply'] + (' إذا بقيت المشكلة بعد ذلك اكتب لنا هنا.' if resolved_by_explanation else '')
    elif know['knowledge'] == 'learned':
        reply = 'طلبك #' + str(t['id']) + ' يشبه حالة سبق أن عالجناها. حوّلناه للإدارة لتأكيد الحل المناسب لحالتك، وسنرد عليك هنا.'
    elif know['knowledge'] == 'novel':
        reply = ('طلبك #' + str(t['id']) + ' من نوع جديد علينا، وحوّلناه لإدارة خدووم لمراجعته يدويًا وسنرد عليك هنا. ' +
                 'إذا عندك وقت حدوث المشكلة وخطواتها اكتبها هنا، بدون كلمات مرور أو رموز تحقق.')
    next_status = 'awaiting_user' if resolved_by_explanation or t['status'] == 'awaiting_user' else 'under_review'
    # رد المدير البشري لا يُستبدل بالرد الآلي؛ يُسجل تقرير الفحص في السجل فقط.
    applied = owner.automated_ticket_update(c, t['id'], next_status, reply, ts)
    human_reply = applied != next_status or c.execute("SELECT 1 FROM support_tickets WHERE id=? AND owner_reply_by='admin' AND owner_reply<>''", (t['id'],)).fetchone() is not None
    next_status = applied
    owner.support_event(c, t, actor_type='technical_ai', actor_name='الموظف التقني', event_type='technical_evidence_report', body=json.dumps(report, ensure_ascii=False), from_status=t['status'], to_status=next_status)
    if not human_reply:
        owner.support_event(c, t, actor_type='technical_ai', actor_name='الموظف التقني', event_type='technical_support_update', body=reply, from_status=next_status, to_status=next_status)
    owner.audit(c, 'support-monitor', 'support_evidence_report', 'ticket=' + str(t['id']) + ';task=' + str(task['id']))
    overdue = bool(t.get('created_at') and t['created_at'] < (utcnow()-timedelta(days=1)).isoformat())
    if report['severity'] == 'critical' or report['repeated'] or overdue:
        problem = 'متابعة طلب دعم #' + str(t['id'])
        old = c.execute('SELECT id FROM technical_incidents WHERE service=? AND organization_id=? AND problem=?', ('support', t['organization_id'], problem)).fetchone()
        if not old:
            c.execute('INSERT INTO technical_incidents(service,organization_id,problem,root_cause,proposal,severity,test_status,deployment_status,affected_organizations,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 ('support', t['organization_id'], problem, report['diagnosis'], report['proposal'], report['severity'], 'not_tested', 'proposed', 1, ts, ts))
    report['taskId'] = task['id']
    return report


def run_pending(c, owner, server, limit=5):
    if server.DATABASE_URL:
        lock = c.execute('SELECT pg_try_advisory_xact_lock(735422) AS acquired').fetchone()
        if not lock['acquired']:
            return []
    cutoff = (utcnow()-timedelta(hours=24)).isoformat()
    tickets = c.execute("""SELECT t.* FROM support_tickets t
        WHERE t.status IN ('open','under_review','in_progress','awaiting_user')
          AND NOT EXISTS (SELECT 1 FROM technical_tasks k WHERE k.support_ticket_id=t.id AND k.status='approved')
          AND (EXISTS (SELECT 1 FROM technical_tasks k WHERE k.support_ticket_id=t.id AND k.status IN ('queued','diagnosing'))
               OR NOT EXISTS (SELECT 1 FROM support_ticket_events e WHERE e.ticket_id=t.id AND e.event_type='technical_evidence_report' AND e.created_at>=?))
        ORDER BY t.id LIMIT ?""", (cutoff, limit)).fetchall()
    return [process_ticket(c, t, owner, server) for t in tickets]


INTERPRET = """أنت الموظف التقني في إدارة خدووم (تطبيق إدارة مؤسسات). وصلتك شكوى من مشترك لم تتعرف عليها قواعد الفحص.
اقرأ نص الشكوى وحقائق المؤسسة المرفقة، ثم أعد JSON فقط بهذه المفاتيح:
understood: جملة أو جملتان بسعودية بسيطة تشرح للمدير ماذا يقصد المشترك بالضبط.
likelyCause: السبب الأرجح اعتمادًا على الحقائق المرفقة فقط، أو "غير معروف".
canSolve: true فقط إذا كان الحل خطوة واضحة يقوم بها المشترك أو الإدارة من الشاشات الموجودة.
solution: خطوات الحل إذا canSolve=true، وإلا نص فارغ.
needsDevelopment: true إذا كانت الشكوى تطلب ميزة غير موجودة أو تدل على خلل في البرنامج.
missing: ما الذي ينقصك لتتأكد.
لا تخترع حقائق ولا شاشات ولا ميزات. إذا ما عرفت قل ذلك صراحة واجعل canSolve=false."""


def ai_ready():
    import os
    return bool(os.environ.get('KHDOOM_AI_API_KEY', '').strip() or os.environ.get('OPENAI_API_KEY', '').strip())


def interpretation_lines(raw):
    """سطرا التشخيص والاقتراح اللذان يراهما المدير من تفسير الذكاء، أو None إذا لا يوجد تفسير صالح."""
    try:
        data = json.loads(raw or '{}')
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get('understood') or '').strip():
        return None
    diagnosis = 'شكوى من نوع جديد. فهمي لها: ' + str(data['understood']).strip()[:500]
    cause = str(data.get('likelyCause') or '').strip()
    if cause and cause != 'غير معروف':
        diagnosis += ' السبب الأرجح: ' + cause[:400]
    if data.get('canSolve') is True and str(data.get('solution') or '').strip():
        proposal = 'أعرف أحلها (يحتاج موافقتك قبل إبلاغ المشترك): ' + str(data['solution']).strip()[:900]
    else:
        proposal = 'ما عرفت أحلها' + ('؛ لابد من تطوير لأنها ميزة غير موجودة أو خلل في البرنامج.' if data.get('needsDevelopment') is True else '؛ تحتاج مراجعتك.')
    missing = str(data.get('missing') or '').strip()
    if missing:
        proposal += ' ينقصني للتأكد: ' + missing[:300].rstrip('. ') + '.'
    return diagnosis, proposal + ' عند إغلاقك الطلب مع كتابة الحل أتعلمه للمرات القادمة.'


def interpret_pending(server, owner, client=None, limit=2):
    """يحاول فهم الشكاوى الجديدة بالذكاء، مرة واحدة لكل شكوى، ودون حجز اتصال قاعدة البيانات أثناء الانتظار."""
    if client is None and not ai_ready():
        return 0
    with server.db() as c:
        if not owner.table_exists(c, 'technical_tasks', server) or not owner.table_exists(c, 'support_tickets', server):
            return 0
        jobs = [dict(r) for r in c.execute("""SELECT k.id,k.facts,t.category,t.title,t.message FROM technical_tasks k JOIN support_tickets t ON t.id=k.support_ticket_id
            WHERE k.knowledge='novel' AND k.interpretation='' AND t.status NOT IN ('resolved','closed') ORDER BY k.id DESC LIMIT ?""", (limit,)).fetchall()]
        for job in jobs:
            c.execute("UPDATE technical_tasks SET interpretation=? WHERE id=?", ('{"state":"pending"}', job['id']))
        c.commit()
    if not jobs:
        return 0
    import ai_core
    client = client or ai_core.ResponsesClient()
    done = 0
    for job in jobs:
        try:
            question = json.dumps({'category': job['category'], 'title': job['title'], 'message': str(job['message'])[:2000], 'facts': json.loads(job['facts'] or '{}')}, ensure_ascii=False)
            response = client.transport({'model': ai_core.PRIMARY_MODEL, 'instructions': INTERPRET, 'input': [{'role': 'user', 'content': question}], 'max_output_tokens': 900, 'store': False})
            text = client.output_text(response).strip()
            data = json.loads(text[text.index('{'):text.rindex('}') + 1])
            if not isinstance(data, dict):
                raise ValueError('not an object')
            stored = json.dumps({key: data.get(key) for key in ('understood', 'likelyCause', 'canSolve', 'solution', 'needsDevelopment', 'missing')}, ensure_ascii=False)[:4000]
        except Exception as error:
            stored = json.dumps({'state': 'failed', 'error': type(error).__name__}, ensure_ascii=False)
        with server.db() as c:
            c.execute('UPDATE technical_tasks SET interpretation=? WHERE id=?', (stored, job['id']))
            lines = interpretation_lines(stored)
            if lines:
                c.execute("UPDATE technical_tasks SET diagnosis=?,proposal=? WHERE id=? AND knowledge='novel'", (lines[0], lines[1], job['id']))
                done += 1
            c.commit()
    return done


def completion_evidence(data, task):
    evidence = str(data.get('verificationEvidence', '')).strip()
    action = str(data.get('actionTaken', '')).strip()
    if data.get('verified') is not True or type(data.get('productionChanged')) is not bool or len(evidence) < 20 or len(action) < 10:
        raise ValueError('اكتب إجراء المعالجة ودليل اختبار فعلي وأكد نجاح التحقق قبل تسجيل الحل')
    if data.get('productionChanged') is True and not task.get('approved_by'):
        raise ValueError('تعديل التشغيل يحتاج موافقة المدير المسجلة قبل تسجيل الإنجاز')
    return action[:1000], evidence[:1500]
