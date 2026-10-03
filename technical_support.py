"""Evidence-based support triage. No production repair or external credentials."""
from datetime import datetime, timedelta, timezone
import json

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
    task = c.execute('SELECT id FROM technical_tasks WHERE support_ticket_id=? ORDER BY id DESC LIMIT 1', (t['id'],)).fetchone()
    if not task:
        task = c.execute('INSERT INTO technical_tasks(organization_id,user_id,support_ticket_id,service,problem,severity,status,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id',
             (t['organization_id'], t.get('user_id'), t['id'], 'support', 'طلب دعم #' + str(t['id']) + ': ' + t.get('category', ''), report['severity'], 'queued', ts, 'support-monitor')).fetchone()
    result = 'الحالة: بانتظار استكمال الفحص؛ لم يتم الحل.\nالسبب: ' + report['diagnosis'] + '\nالفحص: ' + '\n'.join(x['label'] + ': ' + x['details'] for x in report['checks']) + '\nالعوائق: ' + '\n'.join(report['limitations']) + '\nالخطوة التالية: ' + report['proposal']
    c.execute("UPDATE technical_tasks SET status='proposed',severity=?,diagnosis=?,proposal=?,action_taken=?,result=?,finished_at=NULL WHERE id=?",
         (report['severity'], report['diagnosis'], report['proposal'], 'فحص سجلات فقط؛ لم تتغير بيانات التشغيل', result, task['id']))
    reply = ('راجعنا مؤشرات الخدمة المسجلة لطلبك #' + str(t['id']) + '، لكن سبب المشكلة لم يتأكد ولم نعلن حلها. ' +
             'إذا لم ترفق التفاصيل، أرسل وقت آخر حدوث وصورة الخطأ وخطوات تكراره، دون كلمة مرور أو رموز تحقق. ' +
             'الخطوة التالية: مقارنة التجربة بالسجلات ثم مراجعة خطة المعالجة مع الإدارة. إذا أرسلت التفاصيل بالفعل فلا تحتاج تكرارها.')
    next_status = 'under_review' if t['status'] != 'awaiting_user' else 'awaiting_user'
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


def completion_evidence(data, task):
    evidence = str(data.get('verificationEvidence', '')).strip()
    action = str(data.get('actionTaken', '')).strip()
    if data.get('verified') is not True or type(data.get('productionChanged')) is not bool or len(evidence) < 20 or len(action) < 10:
        raise ValueError('اكتب إجراء المعالجة ودليل اختبار فعلي وأكد نجاح التحقق قبل تسجيل الحل')
    if data.get('productionChanged') is True and not task.get('approved_by'):
        raise ValueError('تعديل التشغيل يحتاج موافقة المدير المسجلة قبل تسجيل الإنجاز')
    return action[:1000], evidence[:1500]
