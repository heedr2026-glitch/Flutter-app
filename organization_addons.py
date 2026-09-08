"""Account -> verified organization -> branch; editable add-on policies and quotes."""
import calendar
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import owner_admin as admin

POLICY_FIELDS = ('included_organizations','free_branches','allow_branches','branch_monthly','branch_yearly','max_branches','allow_organizations','organization_monthly','organization_yearly','organization_discount','independent_package','max_organizations','verify_branches')

def month_after(value, months):
    dt=datetime.fromisoformat(value); year,month=divmod(dt.year*12+dt.month-1+months,12);month+=1
    return dt.replace(year=year,month=month,day=min(dt.day,calendar.monthrange(year,month)[1])).isoformat()

def migrate(c, postgres=False):
    identity='BIGSERIAL PRIMARY KEY' if postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    for definition in [
        '''addon_policies(package TEXT PRIMARY KEY,included_organizations INTEGER NOT NULL DEFAULT 1,free_branches INTEGER NOT NULL DEFAULT 0,allow_branches INTEGER NOT NULL DEFAULT 0,branch_monthly REAL,branch_yearly REAL,max_branches INTEGER,allow_organizations INTEGER NOT NULL DEFAULT 0,organization_monthly REAL,organization_yearly REAL,organization_discount REAL NOT NULL DEFAULT 0,independent_package INTEGER NOT NULL DEFAULT 1,max_organizations INTEGER,verify_branches INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0)''',
        '''owner_accounts(id BIGINT PRIMARY KEY,owner_user_id BIGINT NOT NULL UNIQUE REFERENCES users(id),created_at TEXT NOT NULL)''',
        '''account_organizations(organization_id BIGINT PRIMARY KEY REFERENCES organizations(id),account_id BIGINT NOT NULL REFERENCES owner_accounts(id),is_primary INTEGER NOT NULL DEFAULT 0,independent_package INTEGER NOT NULL DEFAULT 1,verified_at TEXT NOT NULL,verified_by TEXT NOT NULL)''',
        '''organization_branches(organization_id BIGINT NOT NULL REFERENCES organizations(id),id TEXT NOT NULL,name TEXT NOT NULL,phone TEXT NOT NULL DEFAULT '',address TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',period TEXT NOT NULL DEFAULT 'monthly',quote_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,PRIMARY KEY(organization_id,id))''',
        f'''organization_verifications(id {identity},account_id BIGINT NOT NULL REFERENCES owner_accounts(id),name TEXT NOT NULL,activity TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',evidence TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',review_note TEXT NOT NULL DEFAULT '',organization_id BIGINT,created_at TEXT NOT NULL,reviewed_at TEXT,reviewed_by TEXT,period TEXT NOT NULL DEFAULT 'monthly',quote_json TEXT NOT NULL DEFAULT '{{}}')''',
        f'''addon_promotions(id {identity},name TEXT NOT NULL,packages TEXT NOT NULL,free_branches INTEGER NOT NULL DEFAULT 0,discount_percent REAL NOT NULL DEFAULT 0,months INTEGER NOT NULL DEFAULT 3,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,max_claims INTEGER,claimed INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1)''',
        '''addon_promotion_claims(promotion_id BIGINT NOT NULL REFERENCES addon_promotions(id),account_id BIGINT NOT NULL REFERENCES owner_accounts(id),expires_at TEXT NOT NULL,PRIMARY KEY(promotion_id,account_id))''',
    ]:
        c.execute('CREATE TABLE IF NOT EXISTS '+definition)
    # Seed once only. Missing fee means the owner must configure it, never a hidden zero price.
    for package,free,allowed,monthly,maximum in [('free',0,0,None,0),('basic',0,1,29,None),('vip',1,1,None,None)]:
        c.execute('INSERT INTO addon_policies(package,free_branches,allow_branches,branch_monthly,max_branches,max_organizations) VALUES(?,?,?,?,?,1) ON CONFLICT(package) DO NOTHING',(package,free,allowed,monthly,maximum))
    # Only the existing primary administrator anchors an account. Never merge by phone/name.
    for org in admin.rows(c,'SELECT o.id,o.created_at,MIN(u.id) owner_id FROM organizations o JOIN users u ON u.organization_id=o.id AND u.role=? GROUP BY o.id,o.created_at',('admin',)):
        if not c.execute('SELECT organization_id FROM account_organizations WHERE organization_id=?',(org['id'],)).fetchone():
            c.execute('INSERT INTO owner_accounts VALUES(?,?,?) ON CONFLICT(id) DO NOTHING',(org['owner_id'],org['owner_id'],org['created_at']))
            c.execute('INSERT INTO account_organizations VALUES(?,?,1,1,?,?) ON CONFLICT(organization_id) DO NOTHING',(org['id'],org['owner_id'],admin.stamp(),'existing_primary_admin'))
    c.execute("INSERT INTO organization_branches(organization_id,id,name,created_at) SELECT id,'main','الفرع الرئيسي',created_at FROM organizations WHERE 1=1 ON CONFLICT(organization_id,id) DO NOTHING")
    c.execute("INSERT INTO organization_branches(organization_id,id,name,created_at) SELECT organization_id,branch_id,branch_name,? FROM branch_chat_links WHERE 1=1 ON CONFLICT(organization_id,id) DO NOTHING",(admin.stamp(),))
    import whatsapp_bridge
    whatsapp_bridge.initialize(c)
    # Existing unscoped history is retained explicitly, not guessed to be the main office.
    for table in ('ai_usage','vehicles','advertisements','support_tickets','audit_logs','sessions','whatsapp_messages'):
        present=set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info('+table+')')}
        if postgres or 'branch_id' not in present:
            c.execute('ALTER TABLE '+table+' ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+'branch_id TEXT')
        c.execute('CREATE INDEX IF NOT EXISTS addon_scope_'+table+' ON '+table+'(branch_id)')
    present=set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info(sessions)')}
    if postgres or 'scoped_organization_id' not in present:
        c.execute('ALTER TABLE sessions ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+'scoped_organization_id BIGINT')
    # Registration metadata is additive and keeps legacy organizations intact.
    for column in ('entity_type','commercial_registration','unified_number','city','address','organization_email','verification_status'):
        present=set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info(organizations)')}
        if postgres or column not in present:
            typ = "TEXT NOT NULL DEFAULT 'institution'" if column == 'entity_type' else ("TEXT NOT NULL DEFAULT 'pending'" if column == 'verification_status' else "TEXT NOT NULL DEFAULT ''")
            c.execute('ALTER TABLE organizations ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+column+' '+typ)
    c.execute('CREATE INDEX IF NOT EXISTS addon_entity_type ON organizations(entity_type)')
    c.execute('CREATE INDEX IF NOT EXISTS addon_account_members ON account_organizations(account_id,organization_id)')


def ensure_account(c, user):
    c.execute("INSERT INTO organization_branches(organization_id,id,name,created_at) VALUES(?,'main','الفرع الرئيسي',?) ON CONFLICT(organization_id,id) DO NOTHING",(user['organization_id'],admin.stamp()))
    account=c.execute('SELECT * FROM owner_accounts WHERE owner_user_id=?',(user['id'],)).fetchone()
    if account:return account['id']
    original=c.execute('SELECT organization_id,role FROM users WHERE id=?',(user['id'],)).fetchone()
    if not original or original['role']!='admin':return None
    linked=c.execute('SELECT account_id FROM account_organizations WHERE organization_id=?',(original['organization_id'],)).fetchone()
    if linked:return None # another administrator is not the verified owner
    c.execute('INSERT INTO owner_accounts VALUES(?,?,?) ON CONFLICT(id) DO NOTHING',(user['id'],user['id'],admin.stamp()))
    c.execute('INSERT INTO account_organizations VALUES(?,?,1,1,?,?) ON CONFLICT(organization_id) DO NOTHING',(original['organization_id'],user['id'],admin.stamp(),'registered_owner'))
    return user['id']


def policy(c, org):
    row=c.execute("SELECT COALESCE(s.package,'free') package FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE o.id=?",(org,)).fetchone()
    if not row:raise ValueError('المؤسسة غير موجودة')
    return dict(c.execute('SELECT * FROM addon_policies WHERE package=?',(row['package'],)).fetchone())


def lock_policy(c, package):
    # SQLite write lock / PostgreSQL row lock; quotas, claims and approvals share this transaction.
    c.execute('UPDATE addon_policies SET revision=revision WHERE package=?',(package,))


def save_policy(c, data):
    package=data.get('package')
    if package not in ('free','basic','vip'):raise ValueError('الباقة غير صحيحة')
    values=[]
    for field in POLICY_FIELDS:
        v=data.get(field)
        if field in ('allow_branches','allow_organizations','independent_package','verify_branches'):
            if type(v) is not bool:raise ValueError('حدد نعم أو لا')
            values.append(int(v))
        elif v is None and field in ('branch_monthly','branch_yearly','organization_monthly','organization_yearly','max_branches','max_organizations'):values.append(None)
        else:values.append(admin.number(v,1 if field in ('included_organizations','max_organizations') else 0,100 if field=='organization_discount' else 1000000,field not in ('branch_monthly','branch_yearly','organization_monthly','organization_yearly','organization_discount')))
    d=dict(zip(POLICY_FIELDS,values))
    if d['max_branches'] is not None and d['free_branches']>d['max_branches']:raise ValueError('الفروع المجانية تتجاوز الحد الأقصى')
    if d['max_organizations'] is not None and d['included_organizations']>d['max_organizations']:raise ValueError('المؤسسات المشمولة تتجاوز الحد الأقصى')
    result=c.execute('UPDATE addon_policies SET '+','.join(f+'=?' for f in POLICY_FIELDS)+',revision=revision+1 WHERE package=? AND revision=?',(*values,package,admin.number(data.get('revision'),0,2147483647,True)))
    if result.rowcount!=1:raise ValueError('تغيرت الإعدادات؛ حدّث الصفحة وأعد المحاولة')
    return {'saved':True}


def benefits(c, account, package, claim=False):
    free=0;percent=0;ends=[];used=[];now=admin.stamp()
    for offer in admin.rows(c,'SELECT * FROM addon_promotions WHERE active=1 ORDER BY id'):
        if package not in offer['packages'].split(','):continue
        grant=c.execute('SELECT expires_at FROM addon_promotion_claims WHERE promotion_id=? AND account_id=?',(offer['id'],account)).fetchone() if account else None
        if grant:
            if grant['expires_at']<=now:continue
            expiry=grant['expires_at']
        else:
            if not account or not offer['starts_at']<=now<offer['ends_at']:continue
            if offer['max_claims'] is not None and offer['claimed']>=offer['max_claims']:continue
            expiry=month_after(now,offer['months'])
            if claim:
                changed=c.execute('UPDATE addon_promotions SET claimed=claimed+1 WHERE id=? AND active=1 AND (max_claims IS NULL OR claimed<max_claims)',(offer['id'],))
                if changed.rowcount!=1:continue
                c.execute('INSERT INTO addon_promotion_claims VALUES(?,?,?)',(offer['id'],account,expiry))
        # Non-stacking promotions: use the best free count and best percentage.
        free=max(free,offer['free_branches']);percent=max(percent,offer['discount_percent']);ends.append(expiry);used.append(offer['id'])
    return {'free':free,'percent':percent,'benefit_ends':min(ends) if ends else None,'promotion_ids':used}


def quote_branch(c,org,period='monthly',claim=False):
    if period not in ('monthly','yearly'):raise ValueError('اختر شهري أو سنوي')
    p=policy(c,org)
    if not p['allow_branches']:raise ValueError('هذه الباقة لا تسمح بإضافة فروع')
    count=admin.scalar(c,"SELECT COUNT(*) n FROM organization_branches WHERE organization_id=? AND id<>'main' AND status<>'rejected'",(org,))
    if p['max_branches'] is not None and count>=p['max_branches']:raise ValueError('تم بلوغ الحد الأقصى للفروع')
    member=c.execute('SELECT account_id FROM account_organizations WHERE organization_id=?',(org,)).fetchone()
    benefit=benefits(c,member['account_id'] if member else None,p['package'],claim)
    free=p['free_branches']+benefit['free']
    base=p['branch_'+period]
    amount=0 if count<free else None if base is None else round(base*(100-benefit['percent'])/100,2)
    if amount is None:raise ValueError('رسوم الفرع لهذه المدة لم تضبط بعد؛ تواصل مع الإدارة')
    result={'package':p['package'],'period':period,'amount':amount,'base_fee':base,'free_branches':free,'existing_branches':count,'revision':p['revision'],**benefit}
    # Expiry is informational; exclude its moving seconds from the consent fingerprint.
    result['quote_id']=hashlib.sha256(json.dumps({k:v for k,v in result.items() if k!='benefit_ends'},sort_keys=True).encode()).hexdigest()
    return result


def create_branch(c,org,data):
    ident=str(data.get('id',''));name=str(data.get('name','')).strip()
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',ident) or ident=='main' or not 1<=len(name)<=80:raise ValueError('اسم أو معرف الفرع غير صحيح')
    p=policy(c,org);lock_policy(c,p['package'])
    existing=c.execute('SELECT * FROM organization_branches WHERE organization_id=? AND id=?',(org,ident)).fetchone()
    if existing:
        if existing['name']!=name:raise ValueError('معرف الفرع مستخدم')
        return dict(existing)
    preview=quote_branch(c,org,data.get('period','monthly'))
    if data.get('quote_id')!=preview['quote_id']:raise ValueError('تغيرت رسوم أو مقاعد الفرع؛ أعد عرض السعر قبل التأكيد')
    quote=quote_branch(c,org,data.get('period','monthly'),claim=True)
    if quote['amount']!=preview['amount']:raise ValueError('اكتملت مقاعد العرض؛ أعد عرض السعر')
    state='pending_verification' if p['verify_branches'] else 'pending_payment' if quote['amount']>0 else 'active'
    c.execute('INSERT INTO organization_branches(organization_id,id,name,phone,address,status,period,quote_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(org,ident,name,str(data.get('phone',''))[:80],str(data.get('address',''))[:300],state,quote['period'],json.dumps(quote),admin.stamp()))
    return dict(c.execute('SELECT * FROM organization_branches WHERE organization_id=? AND id=?',(org,ident)).fetchone())


def organization_quote(c, account, period='monthly'):
    if period not in ('monthly','yearly'):raise ValueError('مدة غير صحيحة')
    primary=c.execute('SELECT organization_id FROM account_organizations WHERE account_id=? AND is_primary=1',(account,)).fetchone()
    if not primary:raise ValueError('الحساب غير موثق')
    p=policy(c,primary['organization_id'])
    count=admin.scalar(c,'SELECT COUNT(*) n FROM account_organizations WHERE account_id=?',(account,))
    fee=0 if count<p['included_organizations'] else p['organization_'+period]
    return {'amount':None if fee is None else round(fee*(100-p['organization_discount'])/100,2),'period':period,'independent_package':bool(p['independent_package']),'revision':p['revision'],'allowed':bool(p['allow_organizations']),'discount':p['organization_discount']}


def request_organization(c, account, data):
    primary=c.execute('SELECT organization_id FROM account_organizations WHERE account_id=? AND is_primary=1',(account,)).fetchone()
    if not primary:raise ValueError('لم يثبت حساب المالك')
    p=policy(c,primary['organization_id']);lock_policy(c,p['package'])
    if not p['allow_organizations']:raise ValueError('الباقة لا تسمح بمؤسسة إضافية')
    count=admin.scalar(c,'SELECT COUNT(*) n FROM account_organizations WHERE account_id=?',(account,))+admin.scalar(c,"SELECT COUNT(*) n FROM organization_verifications WHERE account_id=? AND status='pending'",(account,))
    if p['max_organizations'] is not None and count>=p['max_organizations']:raise ValueError('تم بلوغ الحد الأقصى للمؤسسات والطلبات المعلقة')
    name=str(data.get('name','')).strip();evidence=str(data.get('evidence','')).strip()
    if not 2<=len(name)<=160 or not 5<=len(evidence)<=2000:raise ValueError('اكتب اسم المؤسسة ومرجعًا واضحًا لإثبات ملكيتها')
    quote=organization_quote(c,account,data.get('period','monthly'))
    if quote['amount'] is None:raise ValueError('رسوم المؤسسة لم تحدد بعد')
    ident=c.execute('INSERT INTO organization_verifications(account_id,name,activity,phone,evidence,created_at,period,quote_json) VALUES(?,?,?,?,?,?,?,?) RETURNING id',(account,name,str(data.get('activity',''))[:160],str(data.get('phone',''))[:80],evidence,admin.stamp(),quote['period'],json.dumps(quote))).fetchone()['id']
    return {'id':ident,'status':'pending','message':'بانتظار التحقق؛ لم تربط المؤسسة بالحساب بعد'}


def review_organization(c,ident,data,actor):
    req=c.execute("SELECT * FROM organization_verifications WHERE id=? AND status='pending'",(ident,)).fetchone()
    if not req:raise ValueError('الطلب غير موجود أو تمت معالجته')
    primary=c.execute('SELECT organization_id FROM account_organizations WHERE account_id=? AND is_primary=1',(req['account_id'],)).fetchone()
    p=policy(c,primary['organization_id']);lock_policy(c,p['package'])
    status=data.get('status');note=str(data.get('review_note','')).strip()
    if status not in ('approved','rejected') or len(note)<5:raise ValueError('حدد القرار وسبب التحقق أو الرفض')
    org=None
    if status=='approved':
        if data.get('ownership_verified') is not True:raise ValueError('يجب تأكيد التحقق من الملكية')
        count=admin.scalar(c,'SELECT COUNT(*) n FROM account_organizations WHERE account_id=?',(req['account_id'],))
        if not p['allow_organizations'] or (p['max_organizations'] is not None and count>=p['max_organizations']):raise ValueError('سياسة الباقة الحالية لا تسمح بإضافة المؤسسة')
        amount=organization_quote(c,req['account_id'],req['period'])['amount']
        if amount is None:raise ValueError('حدد رسوم المؤسسة الإضافية أولًا')
        if amount>0 and data.get('fees_reviewed') is not True:raise ValueError('راجع الرسوم قبل ربط المؤسسة')
    changed=c.execute("UPDATE organization_verifications SET status=?,review_note=?,reviewed_at=?,reviewed_by=? WHERE id=? AND status='pending'",(status,note[:2000],admin.stamp(),actor,ident))
    if changed.rowcount!=1:raise ValueError('عولج الطلب بواسطة مسؤول آخر')
    if status=='approved':
        org=c.execute('INSERT INTO organizations(name,activity,phone,public_chat_token,created_at) VALUES(?,?,?,?,?) RETURNING id',(req['name'],req['activity'],req['phone'],secrets.token_urlsafe(24),admin.stamp())).fetchone()['id']
        c.execute('INSERT INTO account_organizations VALUES(?,?,0,?,?,?)',(org,req['account_id'],p['independent_package'],admin.stamp(),actor))
        package='free' if p['independent_package'] else p['package']
        parent=c.execute('SELECT starts_at,expires_at FROM subscriptions WHERE organization_id=?',(primary['organization_id'],)).fetchone()
        c.execute('INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?)',(org,package,admin.stamp(),None if p['independent_package'] else parent['expires_at']))
        c.execute("INSERT INTO organization_branches(organization_id,id,name,created_at) VALUES(?,'main','الفرع الرئيسي',?)",(org,admin.stamp()))
        c.execute('UPDATE organization_verifications SET organization_id=? WHERE id=?',(org,ident))
    return {'saved':True,'organization_id':org,'status':status}


def check_scope(c,user,branch):
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',branch):raise ValueError('معرف الفرع غير صحيح')
    if user['role']!='admin' and json.loads(user['permissions'] or '{}').get('branch_id','main')!=branch:raise ValueError('ليس لديك صلاحية هذا الفرع')
    if branch=='main':return
    b=c.execute('SELECT status FROM organization_branches WHERE organization_id=? AND id=?',(user['organization_id'],branch)).fetchone()
    if not b or b['status']!='active':raise ValueError('الفرع غير مسجل أو بانتظار التفعيل؛ راجع إدارة الفروع')


def customer_context(c,user,h):
    user=dict(user);ensure_account(c,user)
    selected=h.headers.get('X-Organization-Id','')
    if selected:
        if not selected.isdigit():raise ValueError('معرف المؤسسة غير صحيح')
        if int(selected)!=user['organization_id']:
            member=c.execute('SELECT ao.organization_id FROM account_organizations ao JOIN owner_accounts a ON a.id=ao.account_id WHERE ao.organization_id=? AND a.owner_user_id=?',(int(selected),user['id'])).fetchone()
            if not member:raise ValueError('المؤسسة غير موثقة تحت حسابك')
            user['organization_id']=int(selected)
    parent=c.execute('SELECT root.package,root.starts_at,root.expires_at FROM account_organizations child JOIN account_organizations primary_org ON primary_org.account_id=child.account_id AND primary_org.is_primary=1 JOIN subscriptions root ON root.organization_id=primary_org.organization_id WHERE child.organization_id=? AND child.independent_package=0',(user['organization_id'],)).fetchone()
    if parent:
        c.execute('UPDATE subscriptions SET package=?,starts_at=?,expires_at=? WHERE organization_id=?',(parent['package'],parent['starts_at'],parent['expires_at'],user['organization_id']))
    branch=h.headers.get('X-Branch-Id')
    if branch is not None:
        check_scope(c,user,branch);user['current_branch']=branch
        digest=hashlib.sha256(h.headers.get('Authorization','')[7:].encode()).hexdigest()
        c.execute('UPDATE sessions SET branch_id=?,scoped_organization_id=? WHERE token_hash=?',(branch,user['organization_id'],digest))
    else:user['current_branch']=None
    return user


def branch_detail(c,org,ident):
    row=c.execute('SELECT b.*,o.name organization_name FROM organization_branches b JOIN organizations o ON o.id=b.organization_id WHERE b.organization_id=? AND b.id=?',(org,ident)).fetchone()
    if not row:raise ValueError('الفرع غير موجود')
    out=dict(row);out['quote']=json.loads(out.pop('quote_json'))
    users=admin.rows(c,'SELECT id,name,permissions,role,active FROM users WHERE organization_id=?',(org,))
    out['employees']=[{k:u[k] for k in ('id','name','role','active')} for u in users if (json.loads(u['permissions'] or '{}').get('branch_id','main')==ident)]
    for name,sql in {
        'devices':'SELECT se.device_name,se.last_seen_at,se.trusted FROM sessions se JOIN users u ON u.id=se.user_id WHERE COALESCE(se.scoped_organization_id,u.organization_id)=? AND se.branch_id=?',
        'ai':'SELECT employee_type,COUNT(*) requests FROM ai_usage WHERE organization_id=? AND branch_id=? GROUP BY employee_type',
        'vehicles':'SELECT id,name,plate FROM vehicles WHERE organization_id=? AND branch_id=? ORDER BY id DESC LIMIT 100',
        'ads':'SELECT id,title,active,approved FROM advertisements WHERE organization_id=? AND branch_id=? ORDER BY id DESC LIMIT 100',
        'support':'SELECT id,category,status,message FROM support_tickets WHERE organization_id=? AND branch_id=? ORDER BY id DESC LIMIT 100',
        'alerts':'SELECT action,summary,created_at FROM audit_logs WHERE organization_id=? AND branch_id=? ORDER BY id DESC LIMIT 100',
    }.items():out[name]=admin.rows(c,sql,(org,ident))
    out['ai_total']=sum(x['requests'] for x in out['ai']);out['calls']=None;out['whatsapp']=None
    # WhatsApp is explicitly mapped by a configured branch, never by the phone viewing it.
    out['whatsapp']=admin.scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages WHERE organization_id=? AND branch_id=?',(org,ident))
    return out


def account_detail(c,ident):
    account=c.execute('SELECT a.id,u.name,u.username FROM owner_accounts a JOIN users u ON u.id=a.owner_user_id WHERE a.id=?',(ident,)).fetchone()
    if not account:raise ValueError('الحساب غير موجود')
    out=dict(account);out['organizations']=admin.rows(c,'SELECT o.id,o.name,ao.is_primary,ao.independent_package,s.package FROM account_organizations ao JOIN organizations o ON o.id=ao.organization_id LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE ao.account_id=? ORDER BY ao.is_primary DESC,o.id',(ident,))
    total=0;unassigned=0
    for org in out['organizations']:
        org['branches']=admin.rows(c,'SELECT id,name,status FROM organization_branches WHERE organization_id=? ORDER BY created_at,id',(org['id'],))
        org['ai_total']=admin.scalar(c,'SELECT COUNT(*) n FROM ai_usage WHERE organization_id=?',(org['id'],));org['ai_unassigned']=admin.scalar(c,'SELECT COUNT(*) n FROM ai_usage WHERE organization_id=? AND branch_id IS NULL',(org['id'],));total+=org['ai_total'];unassigned+=org['ai_unassigned']
    out['ai_total']=total;out['ai_unassigned']=unassigned
    return out


def owner(c,r,m,d,q,page,a,s):
    if r=='addons' and m=='GET':return admin.rows(c,'SELECT * FROM addon_policies ORDER BY package')
    if r=='addons' and m=='PUT':return save_policy(c,d)
    if r=='addon-offers' and m=='GET':return admin.rows(c,'SELECT *,max_claims-claimed remaining FROM addon_promotions ORDER BY id DESC')
    if r=='addon-offers' and m=='POST':
        start=admin.date(d.get('starts_at'));end=admin.date(d.get('ends_at'));packages=str(d.get('packages',''));name=str(d.get('name','')).strip()
        if not start or not end or start>=end or not name or any(p not in ('free','basic','vip') for p in packages.split(',')):raise ValueError('تحقق من الاسم والتاريخ والباقات')
        c.execute('INSERT INTO addon_promotions(name,packages,free_branches,discount_percent,months,starts_at,ends_at,max_claims) VALUES(?,?,?,?,?,?,?,?)',(name[:160],packages,admin.number(d.get('free_branches',0),0,1000,True),admin.number(d.get('discount_percent',0),0,100),admin.number(d.get('months',3),1,120,True),start,end,None if d.get('max_claims') is None else admin.number(d['max_claims'],1,1000000,True)))
        return {'saved':True}
    if re.fullmatch('addon-offers/[0-9]+',r) and m=='PUT':
        c.execute('UPDATE addon_promotions SET active=? WHERE id=?',(int(bool(d.get('active'))),int(r.split('/')[1])));return {'saved':True}
    if r=='accounts' and m=='GET':
        where='FROM owner_accounts a JOIN users u ON u.id=a.owner_user_id';args=[]
        if q.get('search'):where+=' WHERE LOWER(u.name) LIKE ? OR LOWER(u.username) LIKE ?';args=['%'+q['search'].lower()[:100]+'%']*2
        return admin.paged(c,'SELECT a.id,u.name,u.username',where,args,'a.id DESC',page)
    if re.fullmatch('accounts/[0-9]+',r) and m=='GET':return account_detail(c,int(r.split('/')[1]))
    if r=='organization-verifications' and m=='GET':return admin.paged(c,'SELECT v.*,u.name owner_name','FROM organization_verifications v JOIN owner_accounts a ON a.id=v.account_id JOIN users u ON u.id=a.owner_user_id',[],'v.id DESC',page)
    if re.fullmatch('organization-verifications/[0-9]+',r) and m=='PUT':return review_organization(c,int(r.split('/')[1]),d,a['name'])
    match=re.fullmatch(r'branches/([0-9]+)/([a-zA-Z0-9_-]+)',r)
    if match:
        org=int(match[1]);ident=match[2]
        if m=='GET':return branch_detail(c,org,ident)
        if m=='PUT':
            status=d.get('status')
            if status not in ('active','suspended','rejected'):raise ValueError('حالة غير صحيحة')
            if ident=='main' and status!='active':raise ValueError('أوقف المؤسسة من ملفها بدل إيقاف مقرها الرئيسي')
            if status=='active' and d.get('reviewed') is not True:raise ValueError('أكد مراجعة رسوم الفرع والتحقق المطلوب')
            c.execute('UPDATE organization_branches SET name=?,phone=?,address=?,status=? WHERE organization_id=? AND id=?',(str(d.get('name',''))[:80],str(d.get('phone',''))[:80],str(d.get('address',''))[:300],status,org,ident));return {'saved':True}
    raise ValueError('المسار غير موجود')


def client(h,method,s):
    path=urlparse(h.path).path.rstrip('/')
    if not path.startswith('/api/addons/'):return False
    try:
        with s.db() as c:
            user=h._user(c);org=user['organization_id'];account=ensure_account(c,user)
            if user['role']!='admin':raise s.ApiError(403,'إدارة المؤسسات والفروع متاحة للمالك فقط')
            r=path[len('/api/addons/'):];d=h._body() if method=='POST' else {};q=parse_qs(urlparse(h.path).query)
            if r=='branches' and method=='GET':result=admin.rows(c,'SELECT * FROM organization_branches WHERE organization_id=? ORDER BY created_at,id',(org,))
            elif r=='quote' and method=='GET':result=quote_branch(c,org,q.get('period',['monthly'])[0])
            elif r=='branches' and method=='POST':result=create_branch(c,org,d)
            elif r=='organizations' and method=='POST':
                if not account:raise ValueError('هذا الحساب ليس حساب المالك الموثق')
                result=request_organization(c,account,d)
            elif r=='account' and method=='GET':
                if not account:raise ValueError('هذا الحساب ليس حساب المالك الموثق')
                result=account_detail(c,account)
            else:raise ValueError('المسار غير موجود')
            c.commit()
        h._send(200,result);return True
    except ValueError as e:raise s.ApiError(400,str(e))
