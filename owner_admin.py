"""Owner console foundation. Staff credentials are isolated from customer accounts."""
import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

PERMISSIONS = ['organizations.view','organizations.edit','packages','codes','offers','usage','security','support','ads','community','rewards','suspend','admins','settings']
ROLES = {'owner': PERMISSIONS, 'system': [p for p in PERMISSIONS if p != 'admins'], 'support': ['organizations.view','organizations.edit','suspend','support'], 'accounting': ['organizations.view','packages','codes','offers','usage'], 'ads': ['ads'], 'community': ['community']}
def stamp(): return datetime.now(timezone.utc).isoformat()
def rows(c,sql,args=()): return [dict(r) for r in c.execute(sql,args).fetchall()]
def scalar(c,sql,args=()): return c.execute(sql,args).fetchone()['n']
def audit(c,actor,action,target): c.execute('INSERT INTO platform_audit(actor,action,target,created_at) VALUES(?,?,?,?)',(actor,action,str(target)[:500],stamp()))

def migrate(c,postgres=False):
 identity='BIGSERIAL PRIMARY KEY' if postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
 schemas=[
 f'''platform_admins(id {identity},name TEXT NOT NULL,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,password_salt TEXT NOT NULL,role TEXT NOT NULL,permissions TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)''',
 '''platform_sessions(token_hash TEXT PRIMARY KEY,admin_id BIGINT NOT NULL REFERENCES platform_admins(id),expires_at TEXT NOT NULL)''',
 f'''platform_audit(id {identity},actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,created_at TEXT NOT NULL)''',
 f'''platform_login_events(id {identity},account TEXT NOT NULL,ip TEXT NOT NULL,success INTEGER NOT NULL,created_at TEXT NOT NULL)''',
 f'''platform_unknown_logins(id {identity},account TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''platform_packages(package TEXT PRIMARY KEY,monthly REAL NOT NULL,yearly REAL NOT NULL,ai_daily INTEGER NOT NULL,ai_employees INTEGER NOT NULL,whatsapp_units INTEGER,calls_units INTEGER,ads_units INTEGER,features TEXT NOT NULL DEFAULT '')''',
 f'''platform_notes(id {identity},ticket_id BIGINT NOT NULL,note TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''platform_org_state(organization_id BIGINT PRIMARY KEY,suspended INTEGER NOT NULL DEFAULT 0)''',
 f'''platform_rewards(id {identity},organization_id BIGINT NOT NULL,kind TEXT NOT NULL,amount INTEGER NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''platform_daily_credits(organization_id BIGINT NOT NULL,day TEXT NOT NULL,units INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(organization_id,day))''']
 for schema in schemas: c.execute('CREATE TABLE IF NOT EXISTS '+schema)
 for p,m,y,n in [('free',0,0,5),('basic',49,449,30),('vip',99,899,100)]:
  monthly=c.execute('SELECT price_sar FROM package_offers WHERE package=? AND paid_months=1 AND bonus_months=0 ORDER BY active DESC,id LIMIT 1',(p,)).fetchone()
  yearly=c.execute('SELECT price_sar FROM package_offers WHERE package=? AND paid_months=12 AND bonus_months=0 ORDER BY active DESC,id LIMIT 1',(p,)).fetchone()
  m=monthly['price_sar'] if monthly else m; y=yearly['price_sar'] if yearly else y
  c.execute('INSERT INTO platform_packages(package,monthly,yearly,ai_daily,ai_employees) VALUES(?,?,?,?,?) ON CONFLICT(package) DO NOTHING',(p,m,y,n,1))
 for table,fields in {'activation_codes':[('starts_at','TEXT'),('discount_amount','REAL NOT NULL DEFAULT 0'),('eligible_packages',"TEXT NOT NULL DEFAULT 'basic,vip'")],'package_offers':[('starts_at','TEXT'),('ends_at','TEXT'),('offer_type',"TEXT NOT NULL DEFAULT 'price'"),('discount_percent','REAL NOT NULL DEFAULT 0')],'support_tickets':[('device_name',"TEXT NOT NULL DEFAULT ''"),('app_version',"TEXT NOT NULL DEFAULT ''")],'advertisements':[('scheduled_at','TEXT'),('image_data',"TEXT NOT NULL DEFAULT ''"),('deleted','INTEGER NOT NULL DEFAULT 0')]}.items():
  existing=set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info('+table+')')}
  for name,typ in fields:
   if postgres or name not in existing: c.execute(f'ALTER TABLE {table} ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+name+' '+typ)
 for table,cols in [('ai_usage','organization_id,created_at'),('audit_logs','action,created_at'),('sessions','user_id,expires_at'),('support_tickets','status,id'),('platform_audit','created_at'),('platform_login_events','ip,created_at'),('organizations','created_at'),('subscriptions','package,organization_id')]: c.execute(f'CREATE INDEX IF NOT EXISTS platform_idx_{table} ON {table}({cols})')

def permission(path,method):
 p=path.removeprefix('/owner/api/')
 if p.startswith('v2/'):
  p=p[3:]
  if p in ('me','logout','summary'): return None
  if p=='addons': return 'packages'
  if p.startswith('addon-offers'): return 'offers'
  if p.startswith(('accounts','branches','organization-verifications')): return 'organizations.view' if method=='GET' else 'organizations.edit'
  if p.startswith('organizations/') and method!='GET': return 'suspend' if p.endswith('/status') else 'rewards' if p.endswith('/reward') else 'organizations.edit'
  for prefix,perm in [('admins','admins'),('organizations','organizations.view'),('packages','packages'),('codes','codes'),('offers','offers'),('usage','usage'),('security','security'),('support','support'),('ads','ads'),('community','community'),('settings','settings')]:
   if p.startswith(prefix): return perm
  return 'admins'
 if p.startswith('security/accounts/') and method != 'GET': return 'suspend'
 if p.startswith('security/devices/') and method != 'GET': return 'organizations.edit'
 if p=='security/emergency' and method != 'GET': return 'settings'
 for prefix,perm in [('package-limits','packages'),('package-offers','offers'),('signup-offer','offers'),('codes','codes'),('subscription-requests','packages'),('platform-ads','ads'),('ads','ads'),('support-tickets','support'),('security','security'),('services','settings'),('payment','packages')]:
  if p.startswith(prefix): return perm
 if p.startswith('organizations'): return 'organizations.view' if method=='GET' else 'packages' if p.endswith('/package') else 'organizations.edit'
 return 'admins'

def authorize(h,s):
 expected=s.os.environ.get('KHDOOM_OWNER_KEY','').strip()
 if not expected and s.OWNER_KEY_PATH.exists(): expected=s.OWNER_KEY_PATH.read_text(encoding='utf-8').strip()
 supplied=h.headers.get('X-Owner-Key','')
 if supplied and expected and hmac.compare_digest(supplied,expected): actor={'id':None,'name':'المالك','role':'owner','permissions':PERMISSIONS}
 else:
  token=h.headers.get('X-Admin-Session','')
  with s.db() as c:
   row=c.execute('SELECT a.id,a.name,a.role,a.permissions FROM platform_sessions se JOIN platform_admins a ON a.id=se.admin_id WHERE se.token_hash=? AND se.expires_at>? AND a.active=1',(hashlib.sha256(token.encode()).hexdigest(),stamp())).fetchone() if token else None
  if row is None: raise s.ApiError(401,'يلزم تسجيل الدخول بحساب إداري')
  actor=dict(row); actor['permissions']=json.loads(actor['permissions'])
 need=permission(urlparse(h.path).path.rstrip('/'),h.command)
 if need and need not in actor['permissions']: raise s.ApiError(403,'ليس لديك صلاحية هذا الإجراء')
 h.platform_actor=actor
 return actor

def number(v,minimum=0,maximum=1000000,integer=False):
 if type(v) not in (int,float) or not math.isfinite(v) or not minimum<=v<=maximum or (integer and int(v)!=v): raise ValueError('قيمة رقمية غير صحيحة')
 return int(v) if integer else round(v,2)
def date(v):
 if not v: return None
 d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
 if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
 return d.astimezone(timezone.utc).isoformat()
def discount(code,package,price):
 r=dict(code)
 if r.get('starts_at') and r['starts_at']>stamp(): raise ValueError('الكود لم يبدأ بعد')
 if package not in r.get('eligible_packages','basic,vip').split(','): raise ValueError('الكود غير مخصص لهذه الباقة')
 return round(max(0,float(price)*(100-r['discount_percent'])/100-r.get('discount_amount',0)),2)
def active_offer(offer):
 o=dict(offer); t=stamp()
 return (not o.get('starts_at') or o['starts_at']<=t) and (not o.get('ends_at') or o['ends_at']>t)
def daily_limit(c,org,package):
 p=c.execute('SELECT ai_daily FROM platform_packages WHERE package=?',(package,)).fetchone()
 override=c.execute('SELECT daily_limit FROM ai_limits WHERE organization_id=?',(org,)).fetchone()
 credit=c.execute('SELECT units FROM platform_daily_credits WHERE organization_id=? AND day=?',(org,stamp()[:10])).fetchone()
 return (override['daily_limit'] if override else p['ai_daily'])+(credit['units'] if credit else 0)
def suspended(c,org):
 r=c.execute('SELECT suspended FROM platform_org_state WHERE organization_id=?',(org,)).fetchone()
 return bool(r and r['suspended'])
def paged(c,select,where,args,order,page):
 return {'items':rows(c,select+' '+where+' ORDER BY '+order+' LIMIT 30 OFFSET ?',[*args,(page-1)*30]),'total':scalar(c,'SELECT COUNT(*) n '+where,args),'page':page,'pageSize':30}
def table_exists(c,name,s):
 if s.DATABASE_URL: return c.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?',(name,)).fetchone() is not None
 return c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
ORG_FROM='FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_org_state z ON z.organization_id=o.id'
ORG_SELECT="SELECT o.id,o.name,o.phone,o.created_at,COALESCE(s.package,'free') package,s.starts_at,s.expires_at,COALESCE(z.suspended,0) suspended,(SELECT MAX(last_seen_at) FROM sessions se JOIN users u ON u.id=se.user_id WHERE u.organization_id=o.id) last_login"

def handle(h,method,s):
 path=urlparse(h.path).path.rstrip('/')
 if path=='/owner' and method=='GET': h._send_html((s.ROOT/'owner_dashboard.html').read_text(encoding='utf-8')); return True
 if path=='/owner/dashboard.js' and method=='GET': h._send_javascript((s.ROOT/'owner_dashboard.js').read_text(encoding='utf-8')); return True
 if path=='/owner/addons.js' and method=='GET': h._send_javascript((s.ROOT/'owner_addons.js').read_text(encoding='utf-8')); return True
 if not path.startswith('/owner/api/v2/'): return False
 route=path[len('/owner/api/v2/'):]
 try:
  if route=='login' and method=='POST':
   d=h._body(); username=str(d.get('username','')).strip().lower(); ip=h.client_address[0]
   with s.db() as c:
    since=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
    if scalar(c,'SELECT COUNT(*) n FROM platform_login_events WHERE ip=? AND success=0 AND created_at>?',(ip,since))>=10: raise s.ApiError(429,'محاولات كثيرة؛ أعد المحاولة بعد 15 دقيقة')
    user=c.execute('SELECT * FROM platform_admins WHERE username=? AND active=1',(username,)).fetchone()
    valid=bool(user and s.verify_password(str(d.get('password','')),user['password_hash'],user['password_salt']))
    c.execute('INSERT INTO platform_login_events(account,ip,success,created_at) VALUES(?,?,?,?)',(username[:100],ip,int(valid),stamp()))
    if valid:
     token=secrets.token_urlsafe(32); c.execute('INSERT INTO platform_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],(datetime.now(timezone.utc)+timedelta(hours=8)).isoformat()))
    c.commit()
   if not valid: raise s.ApiError(401,'بيانات الدخول غير صحيحة')
   h._send(200,{'token':token}); return True
  actor=authorize(h,s)
  if route=='me' and method=='GET': h._send(200,actor); return True
  q={k:v[0] for k,v in parse_qs(urlparse(h.path).query).items()}; page=max(1,min(int(q.get('page',1)),100000)); d=h._body() if method in ('POST','PUT') else {}
  with s.db() as c:
   result=dispatch(c,route,method,d,q,page,actor,h,s)
   if method in ('POST','PUT','DELETE') and route!='logout': audit(c,actor['name'],method,route)
   c.commit()
  h._send(200,result); return True
 except (ValueError,TypeError,KeyError) as e: raise s.ApiError(400,str(e))

def dispatch(c,r,m,d,q,page,a,h,s):
 if r=='addons' or r.startswith(('addon-offers','accounts','branches/','organization-verifications')):
  import organization_addons
  return organization_addons.owner(c,r,m,d,q,page,a,s)
 if r=='logout' and m=='POST':
  c.execute('DELETE FROM platform_sessions WHERE token_hash=?',(hashlib.sha256(h.headers.get('X-Admin-Session','').encode()).hexdigest(),)); return {'saved':True}
 if r=='summary' and m=='GET':
  out={}; today=stamp()[:10]; month=today[:7]+'-01'; p=a['permissions']
  if 'organizations.view' in p:
   out['organizations']=scalar(c,'SELECT COUNT(*) n FROM organizations'); out['packages']=rows(c,"SELECT COALESCE(s.package,'free') package,COUNT(*) total FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id GROUP BY s.package")
   out['newToday']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE created_at>=?',(today,)); out['newMonth']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE created_at>=?',(month,))
  if 'usage' in p:
   out['ai']=scalar(c,'SELECT COUNT(*) n FROM ai_usage'); out['calls']=None; out['whatsapp']=scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages') if table_exists(c,'whatsapp_messages',s) else None
  if 'support' in p: out['support']=scalar(c,"SELECT COUNT(*) n FROM support_tickets WHERE status IN ('open','in_progress')")
  if 'ads' in p: out['ads']=scalar(c,'SELECT COUNT(*) n FROM advertisements WHERE active=1 AND approved=1 AND (scheduled_at IS NULL OR scheduled_at<=?) AND (expires_at IS NULL OR expires_at>?)',(stamp(),stamp()))
  if 'security' in p:
   out['logins']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action IN ('login','failed_login','new_device') AND created_at>=?",(today,)); out['alerts']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action IN ('failed_login','blocked_device_login','new_device','owner_account_status') AND created_at>=?",(today,)); out['passwordResets']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action='password_reset' AND created_at>=?",(today,))+scalar(c,"SELECT COUNT(*) n FROM platform_audit WHERE action='password_reset' AND created_at>=?",(today,))
   out['logins']+=scalar(c,'SELECT COUNT(*) n FROM platform_unknown_logins WHERE created_at>=?',(today,))
  return out
 if r=='admins' and m=='GET': return {'items':rows(c,'SELECT id,name,username,role,permissions,active,created_at FROM platform_admins ORDER BY id DESC LIMIT 200'),'roles':ROLES,'permissions':PERMISSIONS}
 if (r=='admins' and m=='POST') or (re.fullmatch(r'admins/\d+',r) and m=='PUT'):
  name=str(d.get('name','')).strip(); username=str(d.get('username','')).strip().lower(); role=d.get('role'); privileges=d.get('permissions',ROLES.get(role,[]))
  if not name or not re.fullmatch(r'[a-z0-9_.@-]{3,80}',username) or role not in ROLES or not isinstance(privileges,list) or any(p not in PERMISSIONS for p in privileges): raise ValueError('تحقق من الاسم والدور والصلاحيات')
  active=int(bool(d.get('active',True))); password=str(d.get('password','')); ident=int(r.split('/')[1]) if m=='PUT' else None
  if c.execute('SELECT id FROM platform_admins WHERE username=? AND id<>?',(username,ident or 0)).fetchone(): raise ValueError('اسم المستخدم مستخدم')
  if m=='POST' or password:
   if len(password)<12: raise ValueError('كلمة المرور الإدارية 12 حرفًا على الأقل')
   hashed,salt=s.hash_password(password)
  if m=='POST': c.execute('INSERT INTO platform_admins(name,username,password_hash,password_salt,role,permissions,active,created_at) VALUES(?,?,?,?,?,?,?,?)',(name,username,hashed,salt,role,json.dumps(privileges),active,stamp()))
  else:
   if ident==a['id']: raise ValueError('لا تعدل حسابك الحالي؛ استخدم حساب المالك')
   c.execute('UPDATE platform_admins SET name=?,username=?,role=?,permissions=?,active=? WHERE id=?',(name,username,role,json.dumps(privileges),active,ident))
   if password:
    c.execute('UPDATE platform_admins SET password_hash=?,password_salt=? WHERE id=?',(hashed,salt,ident));audit(c,a['name'],'password_reset','admins/'+str(ident))
   c.execute('DELETE FROM platform_sessions WHERE admin_id=?',(ident,))
  return {'saved':True}
 if r=='organizations' and m=='GET':
  conditions=['1=1']; args=[]
  if q.get('package') in ('free','basic','vip'): conditions.append("COALESCE(s.package,'free')=?"); args.append(q['package'])
  if q.get('search'): conditions.append('(LOWER(o.name) LIKE ? OR o.phone LIKE ?)'); args.extend(['%'+q['search'].lower()[:100]+'%']*2)
  if q.get('since'): conditions.append('o.created_at>=?'); args.append(date(q['since']))
  return paged(c,ORG_SELECT,ORG_FROM+' WHERE '+' AND '.join(conditions),args,'o.id DESC',page)
 if re.fullmatch(r'organizations/\d+',r) and m=='GET':
  ident=int(r.split('/')[1]); org=c.execute(ORG_SELECT+',o.activity '+ORG_FROM+' WHERE o.id=?',(ident,)).fetchone()
  if not org: raise s.ApiError(404,'المؤسسة غير موجودة')
  out=dict(org); out['account_id']=(c.execute('SELECT account_id FROM account_organizations WHERE organization_id=?',(ident,)).fetchone() or {'account_id':None})['account_id']; out['branches']=rows(c,'SELECT id,name,status FROM organization_branches WHERE organization_id=? ORDER BY created_at,id',(ident,)); out['users']=rows(c,'SELECT id,name,email,phone,role,active FROM users WHERE organization_id=? ORDER BY id LIMIT 100',(ident,)); out['devices']=rows(c,'SELECT se.token_hash id,se.device_name,se.device_id,se.last_seen_at,se.trusted,se.expires_at,u.name FROM sessions se JOIN users u ON u.id=se.user_id WHERE u.organization_id=? AND se.expires_at>? ORDER BY se.last_seen_at DESC LIMIT 100',(ident,stamp())); out['employees']=rows(c,'SELECT employee_type,COUNT(*) requests FROM ai_usage WHERE organization_id=? GROUP BY employee_type',(ident,)); out['ads']=rows(c,'SELECT id,title,active,approved,expires_at FROM advertisements WHERE organization_id=? ORDER BY id DESC LIMIT 50',(ident,)); out['rewards']=rows(c,'SELECT kind,amount,reason,actor,created_at FROM platform_rewards WHERE organization_id=? ORDER BY id DESC LIMIT 30',(ident,)); return out
 if re.fullmatch(r'organizations/\d+/(status|logout|reward)',r) and m=='POST':
  ident=int(r.split('/')[1]); action=r.split('/')[2]
  if not c.execute('SELECT id FROM organizations WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'المؤسسة غير موجودة')
  if action=='status':
   c.execute('INSERT INTO platform_org_state VALUES(?,?) ON CONFLICT(organization_id) DO UPDATE SET suspended=excluded.suspended',(ident,int(bool(d.get('suspended'))))); c.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id=?)',(ident,))
  elif action=='logout':
   device=str(d.get('device','')); c.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id=?)'+(' AND token_hash=?' if device else ''),[ident]+([device] if device else []))
  else:
   kind=d.get('kind'); amount=number(d.get('amount'),1,3650,True); reason=str(d.get('reason','')).strip()
   if not reason or kind not in ('days','month','vip','ai'): raise ValueError('اختر المكافأة واكتب سببها')
   if kind=='ai': c.execute('INSERT INTO platform_daily_credits VALUES(?,?,?) ON CONFLICT(organization_id,day) DO UPDATE SET units=platform_daily_credits.units+excluded.units',(ident,stamp()[:10],amount))
   else:
    sub=c.execute('SELECT * FROM subscriptions WHERE organization_id=?',(ident,)).fetchone(); pkg='vip' if kind=='vip' else sub['package'] if sub and sub['package']!='free' else 'basic'; base=max(stamp(),sub['expires_at'] or stamp()) if sub and (kind!='vip' or sub['package']=='vip') else stamp(); expiry=(datetime.fromisoformat(base)+timedelta(days=30 if kind=='month' else amount)).isoformat()
    c.execute('INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?) ON CONFLICT(organization_id) DO UPDATE SET package=excluded.package,expires_at=excluded.expires_at',(ident,pkg,stamp(),expiry))
   c.execute('INSERT INTO platform_rewards(organization_id,kind,amount,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(ident,kind,amount,reason[:500],a['name'],stamp()))
  return {'saved':True}
 if r=='packages' and m=='GET': return rows(c,'SELECT * FROM platform_packages ORDER BY monthly')
 if r=='packages' and m=='PUT':
  pkg=d.get('package')
  if pkg not in ('free','basic','vip'): raise ValueError('الباقة غير صحيحة')
  monthly=number(d.get('monthly')); yearly=number(d.get('yearly'))
  if pkg=='free' and (monthly or yearly): raise ValueError('الباقة المجانية سعرها صفر')
  daily=number(d.get('ai_daily'),1,1000000,True); employees=number(d.get('ai_employees'),1,1000000,True); limits=[None if d.get(k) is None else number(d[k],0,1000000,True) for k in ('whatsapp_units','calls_units','ads_units')]
  c.execute('UPDATE platform_packages SET monthly=?,yearly=?,ai_daily=?,ai_employees=?,whatsapp_units=?,calls_units=?,ads_units=?,features=? WHERE package=?',(monthly,yearly,daily,employees,*limits,str(d.get('features',''))[:4000],pkg))
  for months,price in [(1,monthly),(12,yearly)]: c.execute('UPDATE package_offers SET price_sar=? WHERE package=? AND paid_months=? AND bonus_months=0 AND starts_at IS NULL AND ends_at IS NULL',(price,pkg,months))
  return {'saved':True}
 if r=='codes' and m=='GET': return paged(c,'SELECT id,code_prefix,recipient_name,discount_percent,discount_amount,starts_at,expires_at,max_uses,used_count,eligible_packages,active',"FROM activation_codes WHERE code_kind='discount'",[],'id DESC',page)
 if r=='codes' and m=='POST':
  code=str(d.get('code','')).upper().strip()
  if not re.fullmatch('[A-Z0-9_-]{2,40}',code): raise ValueError('الكود من حرفين إلى 40 حرفًا إنجليزيًا أو رقمًا')
  start=date(d.get('starts_at')); end=date(d.get('expires_at'))
  if start and end and end<=start: raise ValueError('نهاية الكود يجب أن تكون بعد بدايته')
  percent=number(d.get('discount_percent',0),0,100); fixed=number(d.get('discount_amount',0)); eligible=d.get('eligible_packages','basic,vip')
  if any(x not in ('basic','vip') for x in eligible.split(',')) or (percent and fixed) or not(percent or fixed): raise ValueError('اختر نسبة أو مبلغًا وحدد الباقات')
  digest=hashlib.sha256(code.encode()).hexdigest()
  if c.execute('SELECT id FROM activation_codes WHERE code_hash=?',(digest,)).fetchone(): raise ValueError('الكود موجود؛ لن يعاد تصفير استخدامه')
  c.execute('INSERT INTO activation_codes(code_hash,code_prefix,package,duration_days,max_uses,expires_at,recipient_name,code_kind,discount_percent,discount_amount,eligible_packages,starts_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(digest,code,'basic',0,number(d.get('max_uses'),1,1000000,True),end,str(d.get('recipient_name',''))[:100],'discount',percent,fixed,eligible,start,stamp())); return {'saved':True}
 if re.fullmatch(r'codes/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); old=c.execute("SELECT * FROM activation_codes WHERE id=? AND code_kind='discount'",(ident,)).fetchone()
  if not old: raise s.ApiError(404,'الكود غير موجود')
  if 'recipient_name' in d:
   start=date(d.get('starts_at')); end=date(d.get('expires_at')); percent=number(d.get('discount_percent',0),0,100); fixed=number(d.get('discount_amount',0)); maximum=number(d.get('max_uses'),old['used_count'],1000000,True); eligible=d.get('eligible_packages','basic,vip')
   if (start and end and end<=start) or (percent and fixed) or any(x not in ('basic','vip') for x in eligible.split(',')): raise ValueError('تحقق من المدة والخصم والباقات')
   c.execute('UPDATE activation_codes SET recipient_name=?,starts_at=?,expires_at=?,discount_percent=?,discount_amount=?,max_uses=?,eligible_packages=? WHERE id=?',(str(d['recipient_name'])[:100],start,end,percent,fixed,maximum,eligible,ident))
  else: c.execute('UPDATE activation_codes SET active=? WHERE id=?',(int(bool(d.get('active'))),ident))
  return {'saved':True}
 if r=='offers' and m=='GET':
  out=rows(c,'SELECT * FROM package_offers ORDER BY id DESC')
  for o in out: o['effective_active']=bool(o['active'] and active_offer(o))
  return out
 if r=='offers' and m=='POST':
  pkg=d.get('package'); start=date(d.get('starts_at')); end=date(d.get('ends_at')); kind=d.get('offer_type','price')
  if pkg not in ('basic','vip','basic,vip') or (start and end and end<=start) or kind not in ('price','percent','bonus'): raise ValueError('تحقق من الباقة والتواريخ ونوع العرض')
  months=number(d.get('paid_months'),1,60,True); bonus=number(d.get('bonus_months',0),0,60,True); percent=number(d.get('discount_percent',0),0,100)
  for package in pkg.split(','):
   base=c.execute('SELECT monthly,yearly FROM platform_packages WHERE package=?',(package,)).fetchone()
   total=base['yearly'] if months==12 else base['monthly']*months
   price=round(total*(100-percent)/100,2) if kind=='percent' else total if kind=='bonus' else number(d.get('price_sar'))
   c.execute('INSERT INTO package_offers(package,paid_months,bonus_months,price_sar,label,active,created_at,starts_at,ends_at,offer_type,discount_percent) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(package,months,bonus,price,str(d.get('label',''))[:100],1,stamp(),start,end,kind,percent if kind=='percent' else 0))
  return {'saved':True}
 if re.fullmatch(r'offers/\d+',r) and m=='PUT': c.execute('UPDATE package_offers SET active=? WHERE id=?',(int(bool(d.get('active'))),int(r.split('/')[1]))); return {'saved':True}
 if r=='support' and m=='GET':
  args=[]; where='FROM support_tickets t JOIN organizations o ON o.id=t.organization_id LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE 1=1'
  if q.get('status'): where+=' AND t.status=?'; args.append(q['status'])
  out=paged(c,'SELECT t.*,o.name organization_name,s.package',where,args,'t.id DESC',page)
  for t in out['items']: t['notes']=rows(c,'SELECT note,actor,created_at FROM platform_notes WHERE ticket_id=? ORDER BY id DESC LIMIT 20',(t['id'],))
  return out
 if re.fullmatch(r'support/\d+',r) and m=='DELETE':
  ident=int(r.split('/')[1])
  if not c.execute('SELECT id FROM support_tickets WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'Support request not found')
  c.execute('DELETE FROM platform_notes WHERE ticket_id=?',(ident,)); c.execute('DELETE FROM support_tickets WHERE id=?',(ident,)); return {'deleted':True}
 if re.fullmatch(r'support/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); status=d.get('status')
  if status not in ('open','in_progress','resolved','closed'): raise ValueError('حالة غير صحيحة')
  c.execute('UPDATE support_tickets SET status=?,owner_reply=?,updated_at=? WHERE id=?',(status,str(d.get('owner_reply',''))[:1000],stamp(),ident))
  if d.get('note'): c.execute('INSERT INTO platform_notes(ticket_id,note,actor,created_at) VALUES(?,?,?,?)',(ident,str(d['note'])[:2000],a['name'],stamp()))
  return {'saved':True}
 if r=='security' and m=='GET':
  cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
  for table in ('audit_logs','platform_audit','platform_login_events','platform_unknown_logins'):
   try: c.execute('DELETE FROM '+table+' WHERE created_at<?',(cutoff,))
   except Exception: pass
  try: c.execute('DELETE FROM sessions WHERE expires_at<?',(stamp(),))
  except Exception: pass
  category=q.get('type','login')
  if category=='audit': return paged(c,'SELECT *','FROM platform_audit',[],'id DESC',page)
  if category=='devices': return paged(c,'SELECT se.device_name,se.device_id,se.last_seen_at,se.trusted,u.name,u.organization_id','FROM sessions se JOIN users u ON u.id=se.user_id WHERE se.expires_at>?',[stamp()],'se.created_at DESC',page)
  if category=='password':
   return paged(c,'SELECT *',"FROM (SELECT summary,created_at,'customer_account' action FROM audit_logs WHERE action='password_reset' UNION ALL SELECT target summary,created_at,'admin_account' action FROM platform_audit WHERE action='password_reset') resets",[],'created_at DESC',page)

  actions="'login','failed_login','new_device'" if category=='login' else "'failed_login','new_device','blocked_device_login','owner_account_status','suspicious_login'"
  out=paged(c,'SELECT a.action,a.summary,a.created_at,o.name organization_name,a.organization_id',f'FROM audit_logs a JOIN organizations o ON o.id=a.organization_id WHERE a.action IN ({actions})',[],'a.id DESC',page); out['unknownLogins']=rows(c,'SELECT account,created_at FROM platform_unknown_logins ORDER BY id DESC LIMIT 30');out['adminLogins']=rows(c,'SELECT account,success,created_at FROM platform_login_events ORDER BY id DESC LIMIT 30'); return out
 if r=='usage' and m=='GET': return usage(c,q,page,s)
 if r=='ads' and m=='GET':
  condition="CASE WHEN a.approved=1 AND a.expires_at IS NOT NULL AND a.expires_at<=? THEN 'expired' WHEN a.active=0 AND a.approved=0 THEN 'rejected' WHEN a.active=0 THEN 'stopped' WHEN a.approved=0 THEN 'pending' WHEN a.scheduled_at>? THEN 'scheduled' ELSE 'published' END"
  where='FROM advertisements a JOIN organizations o ON o.id=a.organization_id WHERE a.deleted=0'; args=[stamp(),stamp()]
  # Status expression is in the projection; use a subquery for pagination and filtering.
  src='FROM (SELECT a.*,o.name organization_name,'+condition+' status '+where+') ads WHERE 1=1'
  if q.get('status'): src+=' AND status=?'; args.append(q['status'])
  return paged(c,'SELECT *',src,args,'id DESC',page)
 if re.fullmatch(r'ads/\d+',r) and m=='DELETE':
  c.execute('UPDATE advertisements SET active=0,deleted=1 WHERE id=?',(int(r.split('/')[1]),)); return {'saved':True}
 if re.fullmatch(r'ads/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); status=d.get('status'); start=date(d.get('scheduled_at')); end=date(d.get('expires_at'))
  if status not in ('published','scheduled','rejected','stopped','pending') or (start and end and end<=start): raise ValueError('تحقق من الحالة والتاريخ')
  c.execute('UPDATE advertisements SET active=?,approved=?,scheduled_at=?,expires_at=?,approved_at=?,review_note=? WHERE id=?',(int(status not in ('rejected','stopped')),int(status in ('published','scheduled','stopped')),start,end,stamp(),str(d.get('review_note',''))[:500],ident)); return {'saved':True}
 if r=='community' or r.startswith('community/'):
  import community_admin
  return community_admin.moderation(c,r,m,d,q,page,s.ApiError)
 if r=='settings' and m=='GET': return {'database':'PostgreSQL' if s.DATABASE_URL else 'SQLite','sessionHours':8,'pageSize':30,'note':'التكاليف الفعلية والمكالمات والمجتمع تحتاج ربط مصادرها. أسعار الباقات وحدود AI اليومية مرتبطة بالخادم. حقول وحدات واتساب والمكالمات وصفية إلى حين ربط مزود الفوترة.'}
 raise s.ApiError(404,'المسار غير موجود')

def usage(c,q,page,s):
 kind=q.get('type','ai'); today=stamp()[:10]; month=today[:7]+'-01'
 if kind=='calls': return {'items':[],'note':'لا يوجد سجل مكالمات أو دقائق أو فواتير مزود في قاعدة البيانات الحالية.'}
 wa=kind=='whatsapp'
 if wa and not table_exists(c,'whatsapp_messages',s): return {'items':[],'note':'لم يسجل خادم واتساب بيانات في هذه القاعدة بعد.'}
 table='whatsapp_messages' if wa else 'ai_usage'; tf='timestamp' if wa else 'created_at'; day=int(datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp()) if wa else today; mon=int(datetime.fromisoformat(month).replace(tzinfo=timezone.utc).timestamp()) if wa else month
 where='FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE 1=1'; args=[]
 if q.get('package') in ('free','basic','vip'): where+=" AND COALESCE(s.package,'free')=?"; args.append(q['package'])
 if q.get('organization'): where+=' AND o.id=?'; args.append(int(q['organization']))
 out=paged(c,"SELECT o.id,o.name,COALESCE(s.package,'free') package",where,args,'o.id DESC',page)
 ids=[o['id'] for o in out['items']]
 marks=','.join('?' for _ in ids)
 stats={}; overrides={}; credits={}
 if ids:
  stats={row['organization_id']:row for row in rows(c,f'SELECT organization_id,COUNT(*) total,SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END) today,SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END) month'+(',COUNT(DISTINCT peer) conversations' if wa else '')+f' FROM {table} WHERE organization_id IN ({marks}) GROUP BY organization_id',[day,mon,*ids])}
  if not wa:
   overrides={r['organization_id']:r['daily_limit'] for r in rows(c,f'SELECT * FROM ai_limits WHERE organization_id IN ({marks})',ids)}
   credits={r['organization_id']:r['units'] for r in rows(c,f'SELECT * FROM platform_daily_credits WHERE day=? AND organization_id IN ({marks})',[today,*ids])}
 packages={r['package']:r['ai_daily'] for r in rows(c,'SELECT package,ai_daily FROM platform_packages')}
 for o in out['items']:
  o.update(stats.get(o['id'],{'total':0,'today':0,'month':0}));o['cost']=None;o['remaining']=None if wa else max(0,overrides.get(o['id'],packages[o['package']])+credits.get(o['id'],0)-o['today'])
  if wa:o.setdefault('conversations',0)
 scope=f' FROM {table} WHERE organization_id IN (SELECT o.id '+where+')'
 out['summary']=dict(c.execute(f'SELECT COUNT(*) total,COALESCE(SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END),0) today,COALESCE(SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END),0) month'+scope,[day,mon,*args]).fetchone())
 out['note']='المتبقي لـ AI هو الحد اليومي ويشمل وحدات الإدارة لليوم. التكلفة غير متاحة لعدم وجود سجل فوترة.'
 return out
