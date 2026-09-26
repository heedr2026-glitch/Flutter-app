"""Owner console foundation. Staff credentials are isolated from customer accounts."""
import hashlib
import hmac
import json
import math
import re
import secrets
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

PERMISSIONS = ['organizations.view','organizations.edit','packages','codes','offers','usage','security','support','ads','community','rewards','suspend','admins','settings','integrations','finance']
ROLES = {'owner': PERMISSIONS, 'system': [p for p in PERMISSIONS if p != 'admins'], 'manager': [p for p in PERMISSIONS if p != 'admins'], 'support': ['organizations.view','organizations.edit','suspend','support'], 'technician': ['organizations.view','organizations.edit','usage','security','support','settings'], 'accounting': ['organizations.view','packages','codes','offers','usage','finance'], 'employee': ['organizations.view','support','ads'], 'ads': ['ads'], 'community': ['community']}
def stamp(): return datetime.now(timezone.utc).isoformat()
def rows(c,sql,args=()): return [dict(r) for r in c.execute(sql,args).fetchall()]
def scalar(c,sql,args=()): return c.execute(sql,args).fetchone()['n']
def audit(c,actor,action,target): c.execute('INSERT INTO platform_audit(actor,action,target,created_at) VALUES(?,?,?,?)',(actor,action,str(target)[:500],stamp()))

def support_reference(ticket_id, created_at=''):
 year=str(created_at or '')[:4]
 if not year.isdigit(): year=str(datetime.now(timezone.utc).year)
 return f"KHD-{year}-{int(ticket_id):06d}"

def support_event(c, ticket, *, actor_type, actor_name, event_type, body='', from_status='', to_status=''):
 c.execute("INSERT INTO support_ticket_events(ticket_id,organization_id,user_id,actor_type,actor_name,event_type,from_status,to_status,body,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (ticket['id'], ticket['organization_id'], ticket.get('user_id'), actor_type, actor_name[:120], event_type, from_status, to_status, body[:2000], stamp()))

def migrate(c,postgres=False):
 identity='BIGSERIAL PRIMARY KEY' if postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
 schemas=[
 f'''platform_admins(id {identity},name TEXT NOT NULL,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,password_salt TEXT NOT NULL,role TEXT NOT NULL,permissions TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)''',
 '''platform_sessions(token_hash TEXT PRIMARY KEY,admin_id BIGINT NOT NULL REFERENCES platform_admins(id),expires_at TEXT NOT NULL)''',
 f'''platform_audit(id {identity},actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,created_at TEXT NOT NULL)''',
 f'''platform_login_events(id {identity},account TEXT NOT NULL,ip TEXT NOT NULL,success INTEGER NOT NULL,created_at TEXT NOT NULL)''',
 f'''platform_unknown_logins(id {identity},account TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''platform_packages(package TEXT PRIMARY KEY,monthly REAL NOT NULL,yearly REAL NOT NULL,ai_daily INTEGER NOT NULL,ai_employees INTEGER NOT NULL,whatsapp_units INTEGER,calls_units INTEGER,ads_units INTEGER,features TEXT NOT NULL DEFAULT '')''',
 '''package_prices(package TEXT NOT NULL,duration_months INTEGER NOT NULL,price_sar REAL NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(package,duration_months))''',
 f'''platform_notes(id {identity},ticket_id BIGINT NOT NULL,note TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''',
 f'''support_ticket_events(id {identity},ticket_id BIGINT NOT NULL,organization_id BIGINT NOT NULL,user_id BIGINT,actor_type TEXT NOT NULL,actor_name TEXT NOT NULL,event_type TEXT NOT NULL,from_status TEXT NOT NULL DEFAULT '',to_status TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''',
 f'''page_performance_events(id {identity},organization_id BIGINT NOT NULL,user_id BIGINT,page_name TEXT NOT NULL,operation TEXT NOT NULL DEFAULT '',elapsed_ms INTEGER NOT NULL,success INTEGER NOT NULL DEFAULT 1,status_code INTEGER NOT NULL DEFAULT 0,app_version TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''',
 f'''employee_invitations(id {identity},organization_id BIGINT NOT NULL,name TEXT NOT NULL,phone TEXT NOT NULL,job_title TEXT NOT NULL DEFAULT 'موظف',permissions TEXT NOT NULL DEFAULT '{{}}',token_hash TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'pending',expires_at TEXT NOT NULL,created_by BIGINT,created_at TEXT NOT NULL,accepted_at TEXT,accepted_user_id BIGINT)''',
 f'''organization_cameras(id {identity},organization_id BIGINT NOT NULL,branch_id TEXT NOT NULL DEFAULT 'main',name TEXT NOT NULL,location TEXT NOT NULL DEFAULT '',connection_type TEXT NOT NULL DEFAULT 'rtsp',endpoint TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'not_connected',last_checked_at TEXT,created_by BIGINT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
 f'''vehicle_location_events(id {identity},organization_id BIGINT NOT NULL,vehicle_key TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,accuracy_meters REAL,recorded_at TEXT NOT NULL,user_id BIGINT,created_at TEXT NOT NULL)''',
 '''platform_org_state(organization_id BIGINT PRIMARY KEY,suspended INTEGER NOT NULL DEFAULT 0)''',
 f'''platform_rewards(id {identity},organization_id BIGINT NOT NULL,kind TEXT NOT NULL,amount INTEGER NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''platform_daily_credits(organization_id BIGINT NOT NULL,day TEXT NOT NULL,units INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(organization_id,day))''',
 f'''platform_credit_ledger(id {identity},organization_id BIGINT NOT NULL,service TEXT NOT NULL,units INTEGER NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''']
 schemas += [f'''platform_expenses(id {identity},provider TEXT NOT NULL,service TEXT NOT NULL,invoice_number TEXT NOT NULL DEFAULT '',subtotal REAL NOT NULL DEFAULT 0,tax REAL NOT NULL DEFAULT 0,total REAL NOT NULL DEFAULT 0,issued_at TEXT NOT NULL,due_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'unpaid',payment_method TEXT NOT NULL DEFAULT '',paid_at TEXT, payment_reference TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',attachment_data TEXT NOT NULL DEFAULT '',recurring INTEGER NOT NULL DEFAULT 0,recurrence TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''']
 schemas += [
  '''call_connections(organization_id BIGINT PRIMARY KEY,phone_number TEXT NOT NULL DEFAULT '',activity TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'not_connected',last_error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)''',
  f'''call_logs(id {identity},organization_id BIGINT NOT NULL,caller_phone TEXT NOT NULL DEFAULT '',caller_name TEXT NOT NULL DEFAULT '',direction TEXT NOT NULL DEFAULT 'inbound',status TEXT NOT NULL DEFAULT 'ended',started_at TEXT NOT NULL,duration_seconds INTEGER NOT NULL DEFAULT 0,transcript TEXT NOT NULL DEFAULT '',summary TEXT NOT NULL DEFAULT '',request_text TEXT NOT NULL DEFAULT '',appointment TEXT NOT NULL DEFAULT '',follow_up INTEGER NOT NULL DEFAULT 0,human_handoff INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''',
 f'''technical_incidents(id {identity},service TEXT NOT NULL,organization_id BIGINT,problem TEXT NOT NULL,root_cause TEXT NOT NULL,proposal TEXT NOT NULL,severity TEXT NOT NULL DEFAULT 'medium',test_status TEXT NOT NULL DEFAULT 'not_tested',deployment_status TEXT NOT NULL DEFAULT 'proposed',affected_organizations INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,approved_by TEXT NOT NULL DEFAULT '')'''
  ,f'''technical_agent_state(id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'offline', last_heartbeat TEXT, last_check TEXT, last_task TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', last_success TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)'''
 ,f'''technical_tasks(id {identity},organization_id BIGINT,branch_id TEXT NOT NULL DEFAULT '',user_id BIGINT,service TEXT NOT NULL,problem TEXT NOT NULL,severity TEXT NOT NULL DEFAULT 'medium',status TEXT NOT NULL DEFAULT 'diagnosing',diagnosis TEXT NOT NULL DEFAULT '',proposal TEXT NOT NULL DEFAULT '',action_taken TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '',started_at TEXT NOT NULL,finished_at TEXT,created_by TEXT NOT NULL DEFAULT 'ai',approved_by TEXT NOT NULL DEFAULT '')'''
 ,f'''login_failures(id {identity},organization_id BIGINT,user_id BIGINT,username TEXT NOT NULL DEFAULT '',device_name TEXT NOT NULL DEFAULT '',app_version TEXT NOT NULL DEFAULT '',ip TEXT NOT NULL DEFAULT '',error_code INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '',database_status TEXT NOT NULL DEFAULT 'ok',backend_status TEXT NOT NULL DEFAULT 'ok',session_status TEXT NOT NULL DEFAULT 'not_created',user_exists INTEGER NOT NULL DEFAULT 0,account_active INTEGER NOT NULL DEFAULT 0,organization_linked INTEGER NOT NULL DEFAULT 0,password_hash_status TEXT NOT NULL DEFAULT 'not_checked',permissions_status TEXT NOT NULL DEFAULT 'not_checked',created_at TEXT NOT NULL)'''
 ,f'''readiness_runs(id {identity},score INTEGER NOT NULL,ready_count INTEGER NOT NULL,review_count INTEGER NOT NULL,failed_count INTEGER NOT NULL,started_at TEXT NOT NULL,finished_at TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'safe')'''
 ,f'''readiness_results(id {identity},run_id BIGINT NOT NULL,service_key TEXT NOT NULL,label TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',proposal TEXT NOT NULL DEFAULT '',checked_at TEXT NOT NULL,FOREIGN KEY(run_id) REFERENCES readiness_runs(id) ON DELETE CASCADE)'''
 ,f'''readiness_test_accounts(id {identity},account_key TEXT UNIQUE NOT NULL,package TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)'''
  ,f'''platform_integrations(id {identity},key TEXT UNIQUE NOT NULL,name TEXT NOT NULL,category TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'planned',required_permission TEXT NOT NULL,provider_configured INTEGER NOT NULL DEFAULT 0,notes TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)'''
  ,f'''attendance_policies(organization_id BIGINT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,default_radius_m INTEGER NOT NULL DEFAULT 100,require_device_biometric INTEGER NOT NULL DEFAULT 0,allow_remote INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)'''
  ,'''attendance_branch_locations(organization_id BIGINT NOT NULL,branch_id TEXT NOT NULL,latitude REAL,longitude REAL,radius_m INTEGER NOT NULL DEFAULT 100,updated_at TEXT NOT NULL,PRIMARY KEY(organization_id,branch_id))'''
  ,f'''attendance_events(id {identity},organization_id BIGINT NOT NULL,user_id BIGINT NOT NULL,branch_id TEXT NOT NULL,event_type TEXT NOT NULL,occurred_at TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'future',verification TEXT NOT NULL DEFAULT 'not_enabled',status TEXT NOT NULL DEFAULT 'planned',note TEXT NOT NULL DEFAULT '')'''
  ,f'''attendance_exceptions(id {identity},organization_id BIGINT NOT NULL,user_id BIGINT NOT NULL,branch_id TEXT,kind TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,approved_by TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '')'''
  ,f'''attendance_devices(id {identity},organization_id BIGINT NOT NULL,user_id BIGINT NOT NULL,device_id TEXT NOT NULL,device_name TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'planned',last_seen_at TEXT NOT NULL DEFAULT '',UNIQUE(organization_id,user_id,device_id))'''
 ]
 for schema in schemas: c.execute('CREATE TABLE IF NOT EXISTS '+schema)
 for key,name,category,permission,notes in [('renewals','التجديدات والخدمات الحكومية','خدمات حكومية','integrations.renewals','جاهز لإضافة API رسمي مستقبلًا؛ التنبيهات فقط حاليًا'),('vehicles','تتبع المركبات','مركبات','integrations.vehicles','يحتاج جهازًا أو مزود تتبع معتمدًا'),('attendance','الحضور والبصمة','الموظفون','integrations.attendance','يرتبط بملف الموظف عند توفر جهاز أو API'),('cameras','كاميرات المؤسسة','أمن المؤسسة','integrations.cameras','الوصول مقيد بصلاحية مستقلة وغير مفعّل حاليًا'),('payments','بوابات الدفع','فوترة','integrations.payments','يحتاج مزود دفع رسمي'),('communications','مزودو الاتصالات','اتصالات','integrations.communications','يحتاج قناة خادم معتمدة')]:
  c.execute('INSERT INTO platform_integrations(key,name,category,status,required_permission,provider_configured,notes,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO NOTHING',(key,name,category,'planned',permission,0,notes,stamp()))
 for p,m,y,n in [('free',0,0,5),('basic',49,449,30),('vip',99,899,100)]:
  monthly=c.execute('SELECT price_sar FROM package_offers WHERE package=? AND paid_months=1 AND bonus_months=0 ORDER BY active DESC,id LIMIT 1',(p,)).fetchone()
  yearly=c.execute('SELECT price_sar FROM package_offers WHERE package=? AND paid_months=12 AND bonus_months=0 ORDER BY active DESC,id LIMIT 1',(p,)).fetchone()
  m=monthly['price_sar'] if monthly else m; y=yearly['price_sar'] if yearly else y
  c.execute('INSERT INTO platform_packages(package,monthly,yearly,ai_daily,ai_employees) VALUES(?,?,?,?,?) ON CONFLICT(package) DO NOTHING',(p,m,y,n,1))
 # The only persistent source for base subscription prices.
 defaults={'basic':{1:49,3:139,6:249,12:449},'vip':{1:99,3:279,6:499,12:899}}
 for package,prices in defaults.items():
  for months,fallback in prices.items():
   legacy=c.execute('SELECT price_sar FROM package_offers WHERE package=? AND paid_months=? AND bonus_months=0 ORDER BY id LIMIT 1',(package,months)).fetchone()
   value=float(legacy['price_sar']) if legacy else fallback
   c.execute('INSERT INTO package_prices(package,duration_months,price_sar,updated_at) VALUES(?,?,?,?) ON CONFLICT(package,duration_months) DO NOTHING',(package,months,value,stamp()))
 for table,fields in {'activation_codes':[('starts_at','TEXT'),('discount_amount','REAL NOT NULL DEFAULT 0'),('eligible_packages',"TEXT NOT NULL DEFAULT 'basic,vip'"),('eligible_durations',"TEXT NOT NULL DEFAULT '1,3,6,12'")],'package_offers':[('starts_at','TEXT'),('ends_at','TEXT'),('offer_type',"TEXT NOT NULL DEFAULT 'price'"),('discount_percent','REAL NOT NULL DEFAULT 0'),('base_price_sar','REAL')],'support_tickets':[('device_name',"TEXT NOT NULL DEFAULT ''"),('app_version',"TEXT NOT NULL DEFAULT ''"),('reference_code',"TEXT NOT NULL DEFAULT ''"),('title',"TEXT NOT NULL DEFAULT ''"),('scope',"TEXT NOT NULL DEFAULT 'private'"),('assigned_admin_id',"BIGINT"),('last_error',"TEXT NOT NULL DEFAULT ''")],'technical_tasks':[('support_ticket_id',"BIGINT")],'advertisements':[('scheduled_at','TEXT'),('image_data',"TEXT NOT NULL DEFAULT ''"),('deleted',"INTEGER NOT NULL DEFAULT 0"),('display_seconds',"INTEGER NOT NULL DEFAULT 8"),('banner_config',"TEXT NOT NULL DEFAULT '{}'"),('published_at','TEXT')],'platform_advertisements':[('display_seconds',"INTEGER NOT NULL DEFAULT 8"),('banner_config',"TEXT NOT NULL DEFAULT '{}'"),('published_at','TEXT')],'login_failures':[('backend_status',"TEXT NOT NULL DEFAULT 'ok'"),('session_status',"TEXT NOT NULL DEFAULT 'not_created'"),('user_exists','INTEGER NOT NULL DEFAULT 0'),('account_active','INTEGER NOT NULL DEFAULT 0'),('organization_linked','INTEGER NOT NULL DEFAULT 0'),('password_hash_status',"TEXT NOT NULL DEFAULT 'not_checked'"),('permissions_status',"TEXT NOT NULL DEFAULT 'not_checked'")]}.items():
  existing=set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info('+table+')')}
  for name,typ in fields:
   if postgres or name not in existing: c.execute(f'ALTER TABLE {table} ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+name+' '+typ)
 ad_columns={row['column_name'] for row in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='advertisements'").fetchall()} if postgres else {row['name'] for row in c.execute('PRAGMA table_info(advertisements)')}
 if 'published_by' not in ad_columns: c.execute("ALTER TABLE advertisements ADD COLUMN "+('IF NOT EXISTS ' if postgres else '')+"published_by TEXT NOT NULL DEFAULT ''")
 for table,cols in [('ai_usage','organization_id,created_at'),('audit_logs','action,created_at'),('sessions','user_id,expires_at'),('support_tickets','status,id'),('support_tickets','reference_code'),('support_ticket_events','ticket_id,created_at'),('page_performance_events','created_at,page_name'),('employee_invitations','organization_id,status,created_at'),('organization_cameras','organization_id,branch_id,updated_at'),('vehicle_location_events','organization_id,vehicle_key,recorded_at'),('platform_audit','created_at'),('platform_login_events','ip,created_at'),('login_failures','created_at,username'),('readiness_results','run_id,service_key'),('organizations','created_at'),('subscriptions','package,organization_id'),('platform_credit_ledger','organization_id,service,created_at'),('platform_expenses','status,due_at'),('attendance_events','organization_id,user_id,occurred_at'),('attendance_exceptions','organization_id,user_id,starts_at'),('attendance_devices','organization_id,user_id')]: c.execute(f'CREATE INDEX IF NOT EXISTS platform_idx_{table} ON {table}({cols})')
 for key,package in [('free','free'),('basic','basic'),('vip','vip')]: c.execute('INSERT INTO readiness_test_accounts(account_key,package,active,created_at) VALUES(?,?,1,?) ON CONFLICT(account_key) DO UPDATE SET package=excluded.package,active=1',(f'__readiness_{key}__',package,stamp()))

def permission(path,method):
 p=path.removeprefix('/owner/api/')
 if p.startswith('v2/'):
  p=p[3:]
  if p in ('me','logout','summary'): return None
  if p=='addons': return 'packages'
  if p.startswith('community/rewards/'): return 'rewards'
  if p.startswith('addon-offers'): return 'offers'
  if p.startswith(('accounts','branches','organization-verifications')): return 'organizations.view' if method=='GET' else 'organizations.edit'
  if p.startswith('credits'): return 'usage'
  if p.startswith('organizations/') and method!='GET': return 'suspend' if p.endswith('/status') else 'rewards' if p.endswith('/reward') else 'organizations.edit'
  for prefix,perm in [('admins','admins'),('organizations','organizations.view'),('packages','packages'),('codes','codes'),('offers','offers'),('usage','usage'),('expenses','finance'),('technical-ai','security'),('performance','security'),('readiness','security'),('integrations','integrations'),('security','security'),('support','support'),('ads','ads'),('community','community'),('settings','settings')]:
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
 """Accept normal JSON numbers and safely normalize Arabic/locale numeric input."""
 if isinstance(v,bool): raise ValueError('قيمة رقمية غير صحيحة')
 if isinstance(v,str):
  normalized=v.strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫٬','0123456789.,'))
  # Accept the same human-friendly formats as the admin form, while storing
  # only a real integer/float in the database.
  normalized=re.sub(r'[\s\u00a0\u202f,]','',normalized)
  try: v=float(normalized)
  except (TypeError,ValueError): raise ValueError('قيمة رقمية غير صحيحة')
 if type(v) not in (int,float) or not math.isfinite(v) or not minimum<=v<=maximum or (integer and int(v)!=v): raise ValueError('قيمة رقمية غير صحيحة')
 return int(v) if integer else round(v,2)
def date(v):
 if not v: return None
 d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
 if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
 return d.astimezone(timezone.utc).isoformat()
def discount(code,package,price,duration_months=None):
 r=dict(code)
 if r.get('starts_at') and r['starts_at']>stamp(): raise ValueError('الكود لم يبدأ بعد')
 if package not in r.get('eligible_packages','basic,vip').split(','): raise ValueError('الكود غير مخصص لهذه الباقة')
 if duration_months is not None and str(duration_months) not in r.get('eligible_durations','1,3,6,12').split(','): raise ValueError('الكود غير مخصص لمدة الاشتراك المختارة')
 return round(max(0,float(price)*(100-r['discount_percent'])/100-r.get('discount_amount',0)),2)
def active_offer(offer):
 o=dict(offer); t=stamp()
 return (not o.get('starts_at') or o['starts_at']<=t) and (not o.get('ends_at') or o['ends_at']>t)
PACKAGE_DURATIONS=(1,3,6,12)
def package_price_map(c,package):
 prices={int(row['duration_months']):float(row['price_sar']) for row in c.execute('SELECT duration_months,price_sar FROM package_prices WHERE package=? ORDER BY duration_months',(package,)).fetchall()}
 return {months:prices.get(months,0.0) for months in PACKAGE_DURATIONS}
def price_warnings(prices):
 warnings=[]
 for before,after in zip(PACKAGE_DURATIONS,PACKAGE_DURATIONS[1:]):
  if prices[after] < prices[before]: warnings.append(f'سعر {after} أشهر أقل من سعر {before} شهر')
  if prices[after] > prices[before]*(after/before): warnings.append(f'سعر {after} أشهر أعلى من جمع أسعار المدة الأقصر')
 return warnings
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
def ensure_owner_tables(c,s,required):
 if all(table_exists(c,name,s) for name in required): return
 if s.DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(735421)')
 migrate(c,postgres=bool(s.DATABASE_URL))
SERVICE_LABELS={'whatsapp':'واتساب','ai':'الذكاء الاصطناعي','calls':'المكالمات'}
SERVICE_COSTS={'whatsapp':0.01,'ai':0.02,'calls':0.05}
def credits_summary(c,org,s):
 package_row=c.execute('SELECT COALESCE(s.package,?) package,COALESCE(p.monthly,0) monthly,p.ai_daily,p.whatsapp_units,p.calls_units FROM subscriptions s LEFT JOIN platform_packages p ON p.package=s.package WHERE s.organization_id=?',('free',org)).fetchone()
 package=dict(package_row or {'package':'free','monthly':0,'ai_daily':5,'whatsapp_units':0,'calls_units':0})
 month=stamp()[:7]+'-01'; today=stamp()[:10]; adjustments={x:0 for x in SERVICE_LABELS}; ledger=rows(c,'SELECT service,units,reason,actor,created_at FROM platform_credit_ledger WHERE organization_id=? AND created_at>=? ORDER BY id DESC LIMIT 100',(org,month))
 for x in ledger:
  if x['service'] in adjustments: adjustments[x['service']]+=int(x['units'] or 0)
 ai_month=c.execute('SELECT COUNT(*) n FROM ai_usage WHERE organization_id=? AND created_at>=?',(org,month)).fetchone()['n']; ai_today=c.execute('SELECT COUNT(*) n FROM ai_usage WHERE organization_id=? AND created_at>=?',(org,today)).fetchone()['n']
 wa_month=0; wa_today=0
 if table_exists(c,'whatsapp_messages',s):
  wa_month=c.execute("SELECT COUNT(*) n FROM whatsapp_messages WHERE organization_id=? AND timestamp>=?",(org,int(datetime.fromisoformat(month).replace(tzinfo=timezone.utc).timestamp()))).fetchone()['n']; wa_today=c.execute("SELECT COUNT(*) n FROM whatsapp_messages WHERE organization_id=? AND timestamp>=?",(org,int(datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp()))).fetchone()['n']
 calls_month=0; calls_today=0
 if table_exists(c,'call_logs',s):
  calls_month=math.ceil(int((c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs WHERE organization_id=? AND started_at>=?',(org,month)).fetchone() or {'n':0})['n'] or 0)/60); calls_today=math.ceil(int((c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs WHERE organization_id=? AND started_at>=?',(org,today)).fetchone() or {'n':0})['n'] or 0)/60)
 usage={'ai':int(ai_month or 0),'whatsapp':int(wa_month or 0),'calls':int(calls_month)}; daily={'ai':int(ai_today or 0),'whatsapp':int(wa_today or 0),'calls':int(calls_today)}
 base={'ai':int(package.get('ai_daily') or 0)*30,'whatsapp':int(package.get('whatsapp_units') or 0),'calls':int(package.get('calls_units') or 0)}
 remaining={x:max(0,base[x]+adjustments[x]-usage[x]) for x in SERVICE_LABELS}; cost={x:round(usage[x]*SERVICE_COSTS[x],2) for x in SERVICE_LABELS}; total_cost=round(sum(cost.values()),2); monthly=float(package.get('monthly') or 0)
 call_link=c.execute('SELECT status,phone_number,last_error FROM call_connections WHERE organization_id=?',(org,)).fetchone() if table_exists(c,'call_connections',s) else None
 return {'package':package.get('package','free'),'subscription_value':monthly,'services':{x:{'label':SERVICE_LABELS[x],'base':base[x],'adjustments':adjustments[x],'used_month':usage[x],'used_today':daily[x],'remaining':remaining[x],'cost':cost[x]} for x in SERVICE_LABELS},'calls_status':call_link['status'] if call_link else 'not_connected','calls_phone':call_link['phone_number'] if call_link else None,'calls_error':call_link['last_error'] if call_link else '','total_remaining':sum(remaining.values()),'usage_month':sum(usage.values()),'usage_today':sum(daily.values()),'actual_cost':total_cost,'estimated_profit':round(monthly-total_cost,2),'ledger':ledger}

def customer_usage_summary(c,org,s):
 """Safe, read-only balance view for an authenticated organization user."""
 summary=credits_summary(c,org,s)
 services={}
 total_limit=0
 for key,item in summary['services'].items():
  limit=max(0,int(item['base'] or 0)+int(item['adjustments'] or 0))
  used=max(0,int(item['used_month'] or 0))
  remaining=max(0,int(item['remaining'] or 0))
  total_limit+=limit
  services[key]={
   'label':item['label'], 'limit':limit, 'usedMonth':used,
   'usedToday':max(0,int(item['used_today'] or 0)), 'remaining':remaining,
   'usagePercent':min(100,round(used*100/limit)) if limit else 0,
  }
 usage_month=max(0,int(summary['usage_month'] or 0))
 # Unit reset is monthly; expose only the next reset date, never ledger actors.
 next_month=datetime.now(timezone.utc).replace(day=1)+timedelta(days=32)
 next_month=next_month.replace(day=1)
 return {
  'package':summary['package'],
  'renewalDate':next_month.date().isoformat(),
  'totalRemaining':int(summary['total_remaining'] or 0),
  'usageMonth':usage_month,
  'usageToday':max(0,int(summary['usage_today'] or 0)),
  'usagePercent':min(100,round(usage_month*100/total_limit)) if total_limit else 0,
  'services':services,
 }

def finance_summary(c):
 today=stamp()[:10]; month=today[:7]+'-01'
 rows_data=rows(c,'SELECT * FROM platform_expenses ORDER BY due_at ASC,id DESC')
 for item in rows_data:
  if item['status']!='paid' and item['due_at']<today: item['status']='overdue'
 month_items=[x for x in rows_data if str(x['issued_at']).startswith(today[:7])]
 expenses=round(sum(float(x['total'] or 0) for x in month_items),2)
 paid=round(sum(float(x['total'] or 0) for x in month_items if x['status']=='paid'),2)
 overdue=round(sum(float(x['total'] or 0) for x in rows_data if x['status']=='overdue'),2)
 upcoming=round(sum(float(x['total'] or 0) for x in rows_data if x['status']!='paid' and x['due_at']>=today),2)
 income=scalar(c,'SELECT COALESCE(SUM(COALESCE(p.monthly,0)),0) n FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_packages p ON p.package=COALESCE(s.package,\'free\') WHERE s.expires_at IS NULL OR s.expires_at>=?',(today,))
 return {'items':rows_data,'monthExpenses':expenses,'paid':paid,'remaining':round(expenses-paid,2),'overdue':overdue,'upcoming':upcoming,'subscriptionIncome':round(float(income or 0),2),'net':round(float(income or 0)-expenses,2)}
ORG_FROM='FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_org_state z ON z.organization_id=o.id'
ORG_SELECT="SELECT o.id,o.name,o.phone,o.created_at,COALESCE(s.package,'free') package,s.starts_at,s.expires_at,COALESCE(z.suspended,0) suspended,(SELECT u.name FROM users u WHERE u.organization_id=o.id AND u.role='admin' ORDER BY u.id LIMIT 1) owner_name,(SELECT MAX(last_seen_at) FROM sessions se JOIN users u ON u.id=se.user_id WHERE u.organization_id=o.id) last_login"

def readiness_checks(c,s):
 """Run read-only launch probes; external integrations never receive test data."""
 checks=[]
 def add(key,label,status,result,error='',proposal=''):
  checks.append({'service_key':key,'label':label,'status':status,'result':result,'error':error,'proposal':proposal})
 try:
  c.execute('SELECT 1').fetchone(); db_ok=True
 except Exception as error:
  db_ok=False; add('database','قاعدة البيانات','not_ready','تعذر تنفيذ SELECT 1',type(error).__name__,'فحص اتصال قاعدة البيانات وإعدادات الخادم')
 if db_ok:
  scoped_tables=[x for x in ('users','organizations','subscriptions','sessions') if table_exists(c,x,s)]
  add('database','قاعدة البيانات','ready' if len(scoped_tables)==4 else 'warning',f"الاتصال سليم؛ الجداول الأساسية: {len(scoped_tables)}/4",'' if len(scoped_tables)==4 else 'جدول أساسي ناقص','إكمال الترحيل قبل الإطلاق')
 add('auth','التسجيل وتسجيل الدخول','ready' if all(table_exists(c,x,s) for x in ('users','organizations','sessions','readiness_test_accounts')) else 'not_ready','اختبار بنية التسجيل والجلسات وحسابات الاختبار الداخلية', '' if all(table_exists(c,x,s) for x in ('users','organizations','sessions','readiness_test_accounts')) else 'بنية الدخول غير مكتملة','تشغيل الترحيلات ثم اختبار حسابات الجاهزية')
 package_count=c.execute('SELECT COUNT(*) n FROM platform_packages').fetchone()['n'] if table_exists(c,'platform_packages',s) else 0
 add('packages','الباقات والاشتراكات','ready' if package_count>=3 and table_exists(c,'subscriptions',s) else 'not_ready',f'تم العثور على {package_count} تعريفات باقة؛ فحص العزل والصلاحيات محفوظ ضمن الاختبار الآمن','' if package_count>=3 else 'تعريفات الباقات ناقصة','مراجعة حدود كل باقة وحسابات الاختبار قبل الإطلاق')
 payment_mode=os.environ.get('KHDOOM_PAYMENT_MODE','').strip().lower(); sandbox=payment_mode in ('sandbox','test','test_mode')
 payment_settings=bool(c.execute("SELECT 1 FROM payment_settings WHERE bank_name<>'' AND account_name<>'' AND iban<>''").fetchone()) if table_exists(c,'payment_settings',s) else False
 add('payment','الدفع','ready' if sandbox and payment_settings else 'warning','لم يتم تنفيذ أي دفع حقيقي؛ فحص الإعدادات ووضع التشغيل فقط', '' if sandbox else 'وضع الدفع Sandbox غير مفعّل', 'تفعيل بوابة Sandbox واختبار نجاح/فشل الدفع قبل الإطلاق')
 ads_ok=table_exists(c,'advertisements',s) and table_exists(c,'platform_advertisements',s)
 add('ads','الإعلانات داخل التطبيق','ready' if ads_ok else 'warning','فحص جداول الإعلانات وقابلية الإدارة؛ اختبار المقاسات يتم دون نشر إعلان', '' if ads_ok else 'جداول إعلانات ناقصة','اختبار الظهور على الباقات والشاشات المختلفة')
 wa_ok=table_exists(c,'whatsapp_connections',s); wa_config=any(os.environ.get(x,'').strip() for x in ('KHDOOM_WHATSAPP_CONFIG','WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_VERIFY_TOKEN'))
 wa_rows=scalar(c,'SELECT COUNT(*) n FROM whatsapp_connections',()) if wa_ok else 0
 add('whatsapp','واتساب','ready' if wa_ok and wa_config and wa_rows else 'warning','فحص بنية الربط وإعدادات Webhook؛ لم تُرسل رسالة تجريبية إلى عميل حقيقي', '' if wa_ok and wa_config and wa_rows else 'الربط أو إعداد Webhook أو اتصال المؤسسة غير مكتمل','اختبار Webhook Sandbox برسالة معزولة')
 calls_ok=table_exists(c,'call_connections',s); calls_config=bool(os.environ.get('KHDOOM_CALLS_API_KEY','').strip() or os.environ.get('KHDOOM_CALLS_GATEWAY_URL','').strip())
 add('calls','المكالمات','ready' if calls_ok and calls_config else 'warning','فحص قناة المكالمات وسجل التقارير دون إجراء اتصال حقيقي', '' if calls_ok and calls_config else 'مزود الاتصال غير مهيأ','استخدام مزود Sandbox ثم اختبار المكالمة والتقرير')
 ai_ok=bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip()) and table_exists(c,'ai_usage',s)
 add('ai','موظفو AI','ready' if ai_ok else 'warning','فحص إعداد الموظف وسجل الاستخدام والعزل بالمؤسسة', '' if ai_ok else 'مفتاح AI أو سجل الاستخدام غير مهيأ','تنفيذ طلب اختبار محدود مع بيانات غير حساسة')
 support_ok=table_exists(c,'support_tickets',s) and table_exists(c,'technical_tasks',s)
 add('support','الدعم الفني','ready' if support_ok else 'warning','فحص إنشاء التذاكر وسجل التشخيص دون إنشاء تذكرة لمشترك','' if support_ok else 'جداول الدعم أو التشخيص ناقصة','تنفيذ تذكرة Sandbox ثم إغلاقها')
 monitor_ok=table_exists(c,'technical_agent_state',s) and table_exists(c,'technical_tasks',s)
 add('monitor','المراقبة والصيانة','ready' if monitor_ok else 'warning','فحص سجل المراقبة ومهام الإصلاح الآمن','' if monitor_ok else 'المراقب التقني غير مهيأ','تشغيل Heartbeat كل دقيقة وضبط مراقب خارجي')
 push_config=bool(os.environ.get('KHDOOM_VAPID_PUBLIC_KEY','').strip() and os.environ.get('KHDOOM_VAPID_PRIVATE_KEY','').strip())
 push_table=table_exists(c,'push_subscriptions',s) or table_exists(c,'customer_push_subscriptions',s)
 add('notifications','الإشعارات','ready' if push_config and push_table else 'warning','فحص مفاتيح Push وبنية الاشتراكات؛ لم يتم إرسال إشعار حقيقي','' if push_config and push_table else 'مفاتيح Push أو جدول اشتراكات الأجهزة غير مكتمل','اختبار إشعار داخلي ثم إشعار جوال تجريبي بموافقة الإدارة')
 admin_ok=table_exists(c,'platform_admins',s) and table_exists(c,'organizations',s)
 add('admin','لوحة الإدارة والأمن','ready' if admin_ok else 'not_ready','فحص جداول الإدارة والمؤسسات والبحث الأساسي','' if admin_ok else 'بنية الإدارة ناقصة','اختبار الصلاحيات والفلترة بحسابات الاختبار')
 return checks

def handle(h,method,s):
 path=urlparse(h.path).path.rstrip('/')
 if path=='/owner' and method=='GET': h._send_html((s.ROOT/'owner_dashboard.html').read_text(encoding='utf-8').replace('20260924-ad-image-compress-fix','20260926-ad-request-image-small')); return True
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
 if r=='readiness' and m=='GET':
  ensure_owner_tables(c,s,('readiness_runs','readiness_results','readiness_test_accounts'))
  run=c.execute('SELECT * FROM readiness_runs ORDER BY id DESC LIMIT 1').fetchone()
  results=[] if run is None else rows(c,'SELECT service_key,label,status,result,error,proposal,checked_at FROM readiness_results WHERE run_id=? ORDER BY id',(run['id'],))
  return {'latestRun':dict(run) if run else None,'results':results,'testAccounts':rows(c,'SELECT account_key,package,active,created_at FROM readiness_test_accounts ORDER BY id'),'safeMode':True,'note':'الفحص للقراءة والتجربة المعزولة فقط؛ لا حذف أو دفع حقيقي أو تغيير في بيانات المشتركين.'}
 if r=='readiness/run' and m=='POST':
  ensure_owner_tables(c,s,('readiness_runs','readiness_results','readiness_test_accounts'))
  checks=readiness_checks(c,s); started=stamp(); ready=sum(x['status']=='ready' for x in checks); review=sum(x['status'] in ('review','warning') for x in checks); failed=sum(x['status']=='not_ready' for x in checks); score=round(sum(100 if x['status']=='ready' else 60 if x['status'] in ('review','warning') else 0 for x in checks)/len(checks)) if checks else 0
  finished=stamp(); cur=c.execute('INSERT INTO readiness_runs(score,ready_count,review_count,failed_count,started_at,finished_at,mode) VALUES(?,?,?,?,?,?,?) RETURNING id',(score,ready,review,failed,started,finished,'safe')); run_id=cur.fetchone()['id']
  for x in checks: c.execute('INSERT INTO readiness_results(run_id,service_key,label,status,result,error,proposal,checked_at) VALUES(?,?,?,?,?,?,?,?)',(run_id,x['service_key'],x['label'],x['status'],x['result'],x['error'],x['proposal'],finished))
  return {'run':{'id':run_id,'score':score,'ready_count':ready,'review_count':review,'failed_count':failed,'started_at':started,'finished_at':finished,'mode':'safe'},'results':checks,'testAccounts':rows(c,'SELECT account_key,package,active FROM readiness_test_accounts ORDER BY id'),'safeMode':True}
 if r=='summary' and m=='GET':
  out={}; today=stamp()[:10]; month=today[:7]+'-01'; p=a['permissions']
  if 'organizations.view' in p:
   out['organizations']=scalar(c,'SELECT COUNT(*) n FROM organizations'); out['activeSubscribers']=scalar(c,"SELECT COUNT(*) n FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_org_state z ON z.organization_id=o.id WHERE COALESCE(z.suspended,0)=0 AND (s.expires_at IS NULL OR s.expires_at>?)",(stamp(),)); out['expiringSubscriptions']=scalar(c,"SELECT COUNT(*) n FROM subscriptions WHERE expires_at>? AND expires_at<=?",(stamp(),(datetime.now(timezone.utc)+timedelta(days=14)).isoformat())); out['packages']=rows(c,"SELECT COALESCE(s.package,'free') package,COUNT(*) total FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id GROUP BY s.package")
   out['newToday']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE created_at>=?',(today,)); out['newMonth']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE created_at>=?',(month,))
   out['problemOrganizations']=scalar(c,"SELECT COUNT(DISTINCT o.id) n FROM organizations o LEFT JOIN platform_org_state z ON z.organization_id=o.id LEFT JOIN login_failures f ON f.organization_id=o.id AND f.created_at>=? WHERE COALESCE(z.suspended,0)=1 OR f.id IS NOT NULL",((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),)) if table_exists(c,'login_failures',s) else scalar(c,'SELECT COUNT(*) n FROM organizations o JOIN platform_org_state z ON z.organization_id=o.id WHERE z.suspended=1')
   out['recentActivity']=rows(c,"SELECT o.name organization_name,a.action,a.summary,a.created_at FROM audit_logs a JOIN organizations o ON o.id=a.organization_id ORDER BY a.id DESC LIMIT 8") if table_exists(c,'audit_logs',s) else []
   out['serviceStatus']={'server':'ready','database':'ready','ai':'ready' if os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip() else 'warning','whatsapp':'ready' if table_exists(c,'whatsapp_connections',s) and scalar(c,'SELECT COUNT(*) n FROM whatsapp_connections') else 'warning','calls':'ready' if table_exists(c,'call_connections',s) and scalar(c,"SELECT COUNT(*) n FROM call_connections WHERE status='ready'") else 'warning','payment':'ready' if os.environ.get('KHDOOM_PAYMENT_MODE','').strip().lower() in ('sandbox','test','test_mode') else 'warning'}
  if 'usage' in p:
   out['ai']=scalar(c,'SELECT COUNT(*) n FROM ai_usage'); out['calls']=0; out['callFailures']=0
   if table_exists(c,'call_logs',s):
    seconds=c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs').fetchone()['n'] or 0; out['calls']=math.ceil(int(seconds)/60); out['callFailures']=scalar(c,"SELECT COUNT(*) n FROM call_logs WHERE status IN ('failed','no_answer','busy')")
   out['whatsapp']=scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages') if table_exists(c,'whatsapp_messages',s) else None
   out['lowBalanceOrganizations']=0
   if 'organizations.view' in p:
    for org in rows(c,'SELECT id FROM organizations'):
     credits=credits_summary(c,org['id'],s)
     if any(v['base']>0 and v['remaining']<=max(1,math.ceil(v['base']*.1)) for v in credits['services'].values()): out['lowBalanceOrganizations']+=1
  if 'support' in p: out['support']=scalar(c,"SELECT COUNT(*) n FROM support_tickets WHERE status IN ('open','in_progress')")
  if 'ads' in p: out['ads']=scalar(c,'SELECT COUNT(*) n FROM advertisements WHERE active=1 AND approved=1 AND (scheduled_at IS NULL OR scheduled_at<=?) AND (expires_at IS NULL OR expires_at>?)',(stamp(),stamp()))
  if 'security' in p:
   out['logins']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action IN ('login','failed_login','new_device') AND created_at>=?",(today,)); out['alerts']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action IN ('failed_login','blocked_device_login','new_device','owner_account_status') AND created_at>=?",(today,)); out['passwordResets']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action='password_reset' AND created_at>=?",(today,))+scalar(c,"SELECT COUNT(*) n FROM platform_audit WHERE action='password_reset' AND created_at>=?",(today,))
   out['logins']+=scalar(c,'SELECT COUNT(*) n FROM platform_unknown_logins WHERE created_at>=?',(today,))
  return out
 if r=='service-health' and m=='GET':
  services=[]
  whatsapp_count=scalar(c,'SELECT COUNT(*) n FROM whatsapp_connections') if table_exists(c,'whatsapp_connections',s) else 0
  whatsapp_errors=scalar(c,"SELECT COUNT(*) n FROM whatsapp_webhooks WHERE received_at<?",(int((datetime.now(timezone.utc)-timedelta(hours=24)).timestamp()),)) if table_exists(c,'whatsapp_webhooks',s) else 0
  services.append({'service':'whatsapp','label':'واتساب','status':'ready' if whatsapp_count and not whatsapp_errors else 'warning' if whatsapp_count else 'not_connected','affected':whatsapp_count,'lastError':'لا توجد مزامنة خلال 24 ساعة' if whatsapp_count and whatsapp_errors else ''})
  ai_ready=bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip())
  services.append({'service':'ai','label':'الذكاء الاصطناعي','status':'ready' if ai_ready else 'not_configured','affected':scalar(c,'SELECT COUNT(*) n FROM organizations') if not ai_ready else 0,'lastError':'' if ai_ready else 'مفتاح خدمة الذكاء غير مهيأ على الخادم'})
  if table_exists(c,'call_connections',s):
   call_count=scalar(c,"SELECT COUNT(*) n FROM call_connections WHERE status='ready'"); call_failures=scalar(c,"SELECT COUNT(*) n FROM call_logs WHERE status IN ('failed','no_answer','busy') AND created_at>=?",((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),)) if table_exists(c,'call_logs',s) else 0
   services.append({'service':'calls','label':'المكالمات','status':'warning' if call_failures else 'ready' if call_count else 'not_connected','affected':call_count,'lastError':f'{call_failures} مكالمة فاشلة خلال 24 ساعة' if call_failures else ''})
  services.extend([{'service':'payment','label':'الدفع','status':'ready','affected':0,'lastError':''},{'service':'server','label':'السيرفر','status':'ready','affected':0,'lastError':''},{'service':'notifications','label':'الإشعارات','status':'ready','affected':0,'lastError':''}])
  return {'checkedAt':stamp(),'services':services,'incidents':[x for x in services if x['status'] not in ('ready',)]}
 if r=='security-center' and m=='GET':
  failed=rows(c,"SELECT account,ip,success,created_at FROM platform_login_events WHERE success=0 ORDER BY id DESC LIMIT 30")
  blocked=rows(c,"SELECT organization_id,device_id,device_name,blocked_at FROM blocked_devices ORDER BY blocked_at DESC LIMIT 30") if table_exists(c,'blocked_devices',s) else []
  return {'firewall':'application-rate-limit','rateLimit':{'windowSeconds':60,'maxRequestsPerWindow':120},'failedLogins':failed,'blockedDevices':blocked,'https':'استضافة Render مسؤولة عن TLS؛ فعّل فرض HTTPS من إعدادات الاستضافة','secrets':'محفوظة في متغيرات البيئة ولا تعرض في اللوحة','backups':'تحتاج تخزينًا خارجيًا منفصلًا من إعدادات الاستضافة'}
 if r=='service-health/check' and m=='POST':
  service=str(d.get('service','')).strip()
  if service not in ('whatsapp','ai','calls','payment','server','notifications'): raise ValueError('الخدمة غير معروفة')
  audit(c,a['name'],'service_check','service-health/'+service)
  return {'checkedAt':stamp(),'service':service,'message':'تم تسجيل طلب الفحص؛ النتيجة الحالية متاحة في مركز الأعطال'}
 if r=='technical-ai/ask' and m=='POST':
  question=str(d.get('question','')).strip()[:1000]
  if len(question)<4: raise ValueError('اكتب وصف المشكلة أولًا')
  question_fold=question.casefold()
  org=None
  for candidate in rows(c,'SELECT id,name FROM organizations ORDER BY id'):
   if candidate['name'] and candidate['name'].casefold() in question_fold: org=candidate; break
  ticket=None
  ticket_match=re.search(r'(?:#|طلب\s*دعم|شكوى|بلاغ)\s*(\d+)',question_fold)
  if ticket_match:
   ticket=c.execute('SELECT id,organization_id,status,category,message,last_error FROM support_tickets WHERE id=?',(int(ticket_match.group(1)),)).fetchone()
   if ticket and not org: org=c.execute('SELECT id,name FROM organizations WHERE id=?',(ticket['organization_id'],)).fetchone()
  service='whatsapp' if any(x in question_fold for x in ('واتساب','whatsapp')) else 'calls' if any(x in question_fold for x in ('مكالمة','المكالمات','calls')) else 'ai' if any(x in question_fold for x in ('ai','ذكاء','موظف')) else 'login' if any(x in question_fold for x in ('دخول','تسجيل','401','كلمة المرور','تسجيل الدخول')) else 'ads' if any(x in question_fold for x in ('إعلان','اعلان','الإعلانات','اعلانات')) else 'performance' if any(x in question_fold for x in ('بطء','بطيء','سرعة','صفحة')) else 'support' if any(x in question_fold for x in ('دعم','شكوى','بلاغ')) else 'platform'
  checks=[]; affected='عامة'; scope='global'
  if org:
   affected=org['name']; scope='organization'
   sub=c.execute('SELECT package,starts_at,expires_at FROM subscriptions WHERE organization_id=?',(org['id'],)).fetchone()
   users=scalar(c,'SELECT COUNT(*) n FROM users WHERE organization_id=? AND active=1',(org['id'],))
   open_support=scalar(c,"SELECT COUNT(*) n FROM support_tickets WHERE organization_id=? AND status IN ('open','under_review','in_progress')",(org['id'],))
   failures=scalar(c,'SELECT COUNT(*) n FROM login_failures WHERE organization_id=? AND created_at>=?',(org['id'],(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()))
   credits=credits_summary(c,org['id'],s)
   checks.extend([{'key':'package','label':'الباقة','status':'ok' if sub else 'warning','details':sub['package'] if sub else 'غير موجودة'}, {'key':'users','label':'المستخدمون','status':'ok' if users else 'warning','details':f'{users} مستخدم نشط'}, {'key':'support','label':'طلبات الدعم','status':'warning' if open_support else 'ok','details':f'{open_support} طلب مفتوح'}, {'key':'login','label':'الأخطاء الأخيرة','status':'warning' if failures else 'ok','details':f'{failures} محاولة/خطأ خلال 24 ساعة'}, {'key':'balance','label':'الاستخدام والرصيد','status':'warning' if any(v['base'] and v['remaining']<=max(1,math.ceil(v['base']*.1)) for v in credits['services'].values()) else 'ok','details':f"{credits['total_remaining']} وحدة متبقية"}])
   if ticket:
    checks.append({'key':'ticket','label':'طلب الدعم','status':'warning' if ticket['status'] not in ('resolved','closed') else 'ok','details':f"#{ticket['id']} · {ticket['status']} · {ticket['category']}"})
  else:
   if table_exists(c,'page_performance_events',s):
    perf=c.execute("SELECT COUNT(*) samples,COALESCE(ROUND(AVG(elapsed_ms),0),0) avg_ms,COALESCE(MAX(elapsed_ms),0) max_ms,COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) failures FROM page_performance_events WHERE created_at>=?",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),)).fetchone()
    checks.append({'key':'performance','label':'الأداء','status':'warning' if perf['max_ms']>=1500 or perf['failures'] else 'ok','details':f"{perf['samples']} قياس · متوسط {int(perf['avg_ms'])}ms · أعلى {int(perf['max_ms'])}ms · فشل {int(perf['failures'])}"})
   checks.append({'key':'database','label':'قاعدة البيانات','status':'ok','details':'استعلام الفحص نجح'})
   checks.append({'key':'server','label':'السيرفر','status':'ok','details':'واجهة الإدارة استجابت'})
  warnings=[x for x in checks if x['status']=='warning']
  diagnosis=('تم فحص المؤسسة فعليًا من السجلات الحالية' if org else 'تم فحص مؤشرات المنصة والأداء الحالية')+('، وظهرت '+str(len(warnings))+' ملاحظات تحتاج متابعة' if warnings else '، ولم تظهر ملاحظات حرجة في القياسات المتاحة')
  proposal=('مراجعة عناصر التحذير أعلاه ثم تنفيذ إصلاح آمن بعد الموافقة' if warnings else 'الاستمرار بالمراقبة وجمع قياسات أكثر قبل أي تغيير')
  ts=stamp(); cur=c.execute('INSERT INTO technical_tasks(organization_id,service,problem,severity,status,diagnosis,proposal,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id',(org['id'] if org else None,service,question,'medium','diagnosed',diagnosis,proposal,ts,'manual')); task_id=cur.fetchone()['id']
  audit(c,a['name'],'technical_manual_diagnosis',json.dumps({'task':task_id,'organization_id':org['id'] if org else None},ensure_ascii=False))
  return {'taskId':task_id,'organization':org,'service':service,'diagnosis':diagnosis,'proposal':proposal,'affected':affected,'scope':scope,'checks':checks,'warnings':len(warnings),'requiresApproval':True if service in ('platform','login') else False,'status':'diagnosed'}
 if re.fullmatch(r'technical-ai/organization/\d+/scan',r) and m=='POST':
  ident=int(r.split('/')[2]); org=c.execute('SELECT id,name FROM organizations WHERE id=?',(ident,)).fetchone()
  if not org: raise s.ApiError(404,'المؤسسة غير موجودة')
  sub=c.execute('SELECT package,starts_at,expires_at FROM subscriptions WHERE organization_id=?',(ident,)).fetchone(); credits=credits_summary(c,ident,s)
  checks=[]
  checks.append({'key':'account','label':'الحساب','status':'ok','details':'المؤسسة موجودة'})
  checks.append({'key':'login','label':'تسجيل الدخول','status':'warning' if scalar(c,'SELECT COUNT(*) n FROM login_failures WHERE organization_id=? AND created_at>=?',(ident,(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat())) else 'ok','details':'تم فحص آخر محاولات الدخول'})
  checks.append({'key':'package','label':'الباقة','status':'ok' if sub else 'warning','details':sub['package'] if sub else 'غير موجودة'})
  checks.append({'key':'balance','label':'الرصيد','status':'warning' if any(v['base']>0 and v['remaining']<=max(1,math.ceil(v['base']*.1)) for v in credits['services'].values()) else 'ok','details':str(credits['total_remaining'])+' وحدة متبقية'})
  checks.append({'key':'permissions','label':'الصلاحيات','status':'ok' if scalar(c,'SELECT COUNT(*) n FROM users WHERE organization_id=? AND active=1',(ident,)) else 'warning','details':'تم فحص المستخدمين النشطين'})
  for key,label,ok,details in [('whatsapp','واتساب',table_exists(c,'whatsapp_connections',s) and bool(c.execute('SELECT 1 FROM whatsapp_connections WHERE organization_id=?',(ident,)).fetchone()),'حالة الربط الحالية'),('ai','AI',bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip()),'إعداد الخادم'),('calls','المكالمات',bool(c.execute('SELECT 1 FROM call_connections WHERE organization_id=? AND enabled=1',(ident,)).fetchone()) if table_exists(c,'call_connections',s) else False,'حالة الربط الحالية'),('database','قاعدة البيانات',True,'استعلام المؤسسة نجح'),('server','السيرفر',True,'الخدمة تستجيب')]: checks.append({'key':key,'label':label,'status':'ok' if ok else 'warning','details':details})
  warning_count=sum(x['status']=='warning' for x in checks); ts=stamp(); cur=c.execute('INSERT INTO technical_tasks(organization_id,service,problem,severity,status,diagnosis,proposal,action_taken,result,started_at,finished_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id',(ident,'organization','فحص شامل للمؤسسة','low','completed','فحص الحساب والخدمات والرصيد والصلاحيات','معالجة العناصر التي تظهر بتحذير بعد موافقة الإدارة','لا إجراء تلقائي','اكتمل الفحص مع '+str(warning_count)+' تحذير',ts,stamp(),'manual')); task_id=cur.fetchone()['id']; audit(c,a['name'],'technical_organization_scan',json.dumps({'organization_id':ident,'task':task_id},ensure_ascii=False)); return {'organization':dict(org),'checks':checks,'warnings':warning_count,'taskId':task_id}
 if r=='performance/diagnose' and m=='POST':
  ensure_owner_tables(c,s,('page_performance_events','technical_tasks'))
  page_name=str(d.get('page','')).strip()[:120]
  if not re.fullmatch(r'[A-Za-z0-9_./:?=&-]{1,120}',page_name): raise ValueError('حدد مسار الأداء الصحيح')
  cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
  metrics=c.execute("SELECT COUNT(*) samples,ROUND(AVG(elapsed_ms),0) avg_ms,MAX(elapsed_ms) max_ms,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) failures,COUNT(DISTINCT organization_id) organizations,MIN(organization_id) organization_id FROM page_performance_events WHERE page_name=? AND created_at>=?",(page_name,cutoff)).fetchone()
  if not metrics or not metrics['samples']: raise s.ApiError(404,'لا توجد قياسات حديثة لهذا المسار')
  metrics=dict(metrics); organizations=int(metrics['organizations'] or 0); failures=int(metrics['failures'] or 0); max_ms=int(metrics['max_ms'] or 0)
  scope='global_suspected' if organizations>=3 and (max_ms>=1500 or failures>=3) else 'private_or_unconfirmed'
  org_id=int(metrics['organization_id']) if organizations==1 and metrics.get('organization_id') else None
  severity='high' if failures>=3 or max_ms>=5000 else 'medium'
  diagnosis=f"رُصد {metrics['samples']} قياسًا لمسار {page_name}: متوسط {int(metrics['avg_ms'] or 0)}ms، أعلى {max_ms}ms، وفشل {failures}. النطاق: {'عطل عام محتمل' if scope=='global_suspected' else 'محدود أو غير مؤكد'}."
  proposal='مراجعة سجلات API وقاعدة البيانات والخدمات الخارجية، ثم اختبار الإصلاح في بيئة آمنة قبل عرضه للاعتماد.'
  ts=stamp(); cur=c.execute("INSERT INTO technical_tasks(organization_id,service,problem,severity,status,diagnosis,proposal,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id",(org_id,'performance',f'تشخيص أداء {page_name}',severity,'diagnosed',diagnosis,proposal,ts,'performance-monitor')); task_id=cur.fetchone()['id']
  audit(c,a['name'],'performance_diagnosis',json.dumps({'task':task_id,'page':page_name,'scope':scope},ensure_ascii=False))
  return {'taskId':task_id,'scope':scope,'diagnosis':diagnosis,'proposal':proposal,'requiresApproval':True}
 if r=='performance' and m=='GET':
  ensure_owner_tables(c,s,('page_performance_events',))
  try: minutes=max(5,min(1440,int(q.get('minutes',60))))
  except (TypeError,ValueError): minutes=60
  cutoff=(datetime.now(timezone.utc)-timedelta(minutes=minutes)).isoformat()
  where='FROM page_performance_events p LEFT JOIN organizations o ON o.id=p.organization_id LEFT JOIN users u ON u.id=p.user_id WHERE p.created_at>=?'
  args=[cutoff]
  summary=rows(c,"SELECT p.page_name,COUNT(*) samples,ROUND(AVG(p.elapsed_ms),0) avg_ms,MAX(p.elapsed_ms) max_ms,SUM(CASE WHEN p.success=0 THEN 1 ELSE 0 END) failures,COUNT(DISTINCT p.organization_id) organizations "+where+" GROUP BY p.page_name ORDER BY failures DESC,max_ms DESC,samples DESC",args)
  for item in summary:
   item['scope']='global_suspected' if int(item.get('organizations') or 0)>=3 and (int(item.get('max_ms') or 0)>=1500 or int(item.get('failures') or 0)>=3) else 'private_or_unconfirmed'
  details=paged(c,'SELECT p.*,o.name organization_name,u.name user_name',where,args,'p.id DESC',page)
  return {'minutes':minutes,'checkedAt':stamp(),'summary':summary,'items':details['items'],'total':details['total'],'page':details['page'],'pageSize':details['pageSize'],'note':'تسجل هذه الشاشة قياسات API البطيئة أو الفاشلة فقط. لا تحفظ محتوى الطلبات أو الرسائل، ولا تنفذ إصلاحًا تلقائيًا.'}
 if r=='technical-ai/daily-report' and m=='GET':
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks','technical_incidents','login_failures','page_performance_events'))
  start=stamp()[:10]
  performance_row=c.execute("SELECT COUNT(*) samples,SUM(CASE WHEN elapsed_ms>=1500 THEN 1 ELSE 0 END) slow,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) failed,COUNT(DISTINCT organization_id) organizations FROM page_performance_events WHERE created_at>=?",(start,)).fetchone()
  performance={key:int((performance_row[key] if performance_row else 0) or 0) for key in ('samples','slow','failed','organizations')}
  tasks=rows(c,"SELECT t.*,o.name organization_name FROM technical_tasks t LEFT JOIN organizations o ON o.id=t.organization_id WHERE t.started_at>=? ORDER BY t.id DESC",(start,))
  counts={key:0 for key in ('queued','diagnosing','proposed','approved','completed','failed','not_executed')}
  for task in tasks: counts[task['status']]=counts.get(task['status'],0)+1
  last_detected=c.execute("SELECT i.*,o.name organization_name FROM technical_incidents i LEFT JOIN organizations o ON o.id=i.organization_id WHERE i.created_at>=? ORDER BY i.id DESC LIMIT 1",(start,)).fetchone()
  last_completed=c.execute("SELECT t.*,o.name organization_name FROM technical_tasks t LEFT JOIN organizations o ON o.id=t.organization_id WHERE t.status='completed' AND COALESCE(t.finished_at,t.started_at)>=? ORDER BY t.id DESC LIMIT 1",(start,)).fetchone()
  state=c.execute('SELECT status,last_check,last_task,last_success,last_error FROM technical_agent_state WHERE id=1').fetchone()
  return {'date':start,'counts':counts,'totalTasks':len(tasks),'performance':performance,'lastDetected':dict(last_detected) if last_detected else None,'lastCompleted':dict(last_completed) if last_completed else None,'state':dict(state) if state else {},'recentTasks':tasks[:20]}
 if r=='technical-ai' and m=='GET':
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks','technical_incidents','login_failures'))
  state=c.execute('SELECT * FROM technical_agent_state WHERE id=1').fetchone()
  heartbeat=state['last_heartbeat'] if state else None
  live=bool(heartbeat and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat)).total_seconds()<=180)
  configured=bool(os.environ.get('KHDOOM_TECHNICAL_AI_SECRET','').strip())
  state_out={'status':'working' if live and state['last_task'] else 'online' if live else 'offline' if configured else 'not_configured','lastHeartbeat':heartbeat,'lastCheck':state['last_check'] if state else None,'lastTask':state['last_task'] if state else '','lastError':state['last_error'] if state else '','lastSuccess':state['last_success'] if state else ''}
  tasks=rows(c,"SELECT t.*,o.name organization_name,u.name user_name FROM technical_tasks t LEFT JOIN organizations o ON o.id=t.organization_id LEFT JOIN users u ON u.id=t.user_id ORDER BY t.id DESC LIMIT 100")
  task_stats={key:0 for key in ('queued','diagnosing','proposed','approved','completed','failed','not_executed')}
  for task in tasks:
   task_stats[task['status']]=task_stats.get(task['status'],0)+1
  failures=rows(c,"SELECT f.*,o.name organization_name,u.name user_name FROM login_failures f LEFT JOIN organizations o ON o.id=f.organization_id LEFT JOIN users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT 100")
  recent_failures=scalar(c,"SELECT COUNT(*) n FROM login_failures WHERE created_at>=?",((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),))
  return {'state':state_out,'monitoring':True,'tasks':tasks,'taskStats':task_stats,'loginFailures':failures,'loginAlert':recent_failures>=3,'items':rows(c,"SELECT i.*,o.name organization_name FROM technical_incidents i LEFT JOIN organizations o ON o.id=i.organization_id ORDER BY i.id DESC LIMIT 100"),'note':'المراقبة تجمع الحالة وتكتب التقارير؛ لا تعديل إنتاج أو نشر تلقائيًا.'}
 if r=='technical-ai/heartbeat' and m=='POST':
  secret=h.headers.get('X-Technical-AI-Secret','')
  expected=os.environ.get('KHDOOM_TECHNICAL_AI_SECRET','').strip()
  if not expected or not hmac.compare_digest(secret,expected): raise s.ApiError(401,'تعذر التحقق من موظف التقنية')
  task=str(d.get('task',''))[:500]; check=str(d.get('check',''))[:200]
  c.execute("INSERT INTO technical_agent_state(id,status,last_heartbeat,last_check,last_task,updated_at) VALUES(1,'online',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='online',last_heartbeat=excluded.last_heartbeat,last_check=excluded.last_check,last_task=excluded.last_task,updated_at=excluded.updated_at",(stamp(),stamp(),task,check,stamp()))
  return {'saved':True,'status':'online'}
 if r=='integrations' and m=='GET':
  return {'items':rows(c,'SELECT key,name,category,status,required_permission,provider_configured,notes,updated_at FROM platform_integrations ORDER BY id'),'note':'هذه الوحدات مجهزة للتوسع فقط. لا توجد خدمة مستقبلية مفعلة دون تكامل رسمي وإعداد خادم وصلاحية مناسبة.'}
 if re.fullmatch(r'technical-ai/tasks/\d+/(start|report|approve|complete|fail|skip)',r) and m=='POST':
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks'))
  parts=r.split('/'); ident=int(parts[2]); action=parts[3]
  task=c.execute('SELECT * FROM technical_tasks WHERE id=?',(ident,)).fetchone()
  if not task: raise s.ApiError(404,'مهمة موظف AI غير موجودة')
  task=dict(task); ts=stamp(); service=task['service']; title=task['problem'][:300]
  if action=='start':
   diagnosis=task['diagnosis'] or 'بدأ موظف AI فحص السجلات وحالة الخدمة والصلاحيات المرتبطة بالمهمة.'
   proposal=task['proposal'] or 'جمع نتائج الفحص ثم إعداد خطة إصلاح قابلة للمراجعة قبل أي تغيير.'
   c.execute("UPDATE technical_tasks SET status='diagnosing',diagnosis=?,proposal=?,action_taken=?,started_at=?,finished_at=NULL WHERE id=?",(diagnosis,proposal,'بدأ الفحص الآمن؛ لم يتم تعديل الإنتاج',ts,ident))
   c.execute("INSERT INTO technical_agent_state(id,status,last_heartbeat,last_check,last_task,last_error,updated_at) VALUES(1,'working',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='working',last_heartbeat=excluded.last_heartbeat,last_check=excluded.last_check,last_task=excluded.last_task,last_error='',updated_at=excluded.updated_at",(ts,'فحص '+service,title,'',ts))
   message='بدأ الفحص وتم حفظ حالة المهمة.'
  elif action=='report':
   diagnosis=task['diagnosis'] or 'تمت مراجعة مؤشرات الخادم والخدمة المرتبطة بالمهمة دون الوصول إلى الأسرار أو بيانات مؤسسة أخرى.'
   proposal=task['proposal'] or 'تنفيذ الإصلاح المقترح في بيئة آمنة ثم اختبار النتيجة قبل اعتماد التنفيذ.'
   report='تقرير موظف AI: المشكلة: '+title+' | الفحص: '+diagnosis+' | الخطة: '+proposal+' | التنفيذ الإنتاجي يحتاج موافقة الإدارة.'
   c.execute("UPDATE technical_tasks SET status='proposed',diagnosis=?,proposal=?,action_taken=?,result=? WHERE id=?",(diagnosis,proposal,'أُعد تقرير وخطة إصلاح بانتظار موافقة الإدارة',report,ident))
   message='تم إعداد التقرير وخطة الإصلاح.'
  elif action=='approve':
   c.execute("UPDATE technical_tasks SET status='approved',approved_by=?,action_taken=?,result=? WHERE id=?",(a['name'],'اعتمدت الإدارة تنفيذ الإصلاح؛ التنفيذ الفعلي يبقى يدويًا ومختبرًا','تمت الموافقة. لا توجد أي تعديلات تلقائية على الإنتاج.',ident))
   message='تمت الموافقة. يمكن تنفيذ الإصلاح يدويًا ثم تسجيل النتيجة.'
  elif action=='complete':
   if task['status'] not in ('approved','diagnosing','diagnosed','proposed'):
    raise ValueError('ابدأ الفحص وأعد التقرير قبل تسجيل الإنجاز')
   result='تم الإنجاز: تم توثيق معالجة المهمة واختبار النتيجة. راجع سجل التغييرات إن وُجد.'
   c.execute("UPDATE technical_tasks SET status='completed',action_taken=?,result=?,finished_at=? WHERE id=?",('تم تسجيل الإنجاز بعد المعالجة والاختبار',result,ts,ident))
   match=re.match(r'طلب دعم #(\d+):',task['problem'])
   if match and table_exists(c,'support_tickets',s):
    c.execute("UPDATE support_tickets SET status='resolved',updated_at=? WHERE id=?",(ts,int(match.group(1))))
   c.execute("INSERT INTO technical_agent_state(id,status,last_heartbeat,last_check,last_task,last_success,updated_at) VALUES(1,'online',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='online',last_heartbeat=excluded.last_heartbeat,last_check=excluded.last_check,last_task=excluded.last_task,last_success=excluded.last_success,updated_at=excluded.updated_at",(ts,'اكتمل الفحص',title,result,ts))
   message='تم الإنجاز وتحديث طلب الدعم المرتبط إلى محلول.'
  elif action=='fail':
   result='لم ينجز: يحتاج متابعة بشرية أو معلومات إضافية قبل الإصلاح.'
   c.execute("UPDATE technical_tasks SET status='failed',action_taken=?,result=?,finished_at=? WHERE id=?",('توقف التنفيذ لوجود عائق يحتاج مراجعة',result,ts,ident))
   message='سُجلت المهمة كغير منجزة مع سبب المتابعة.'
  else:
   result='لم يُنفذ: لم تبدأ إجراءات الإصلاح ولم تُجر أي تعديلات.'
   c.execute("UPDATE technical_tasks SET status='not_executed',action_taken=?,result=?,finished_at=? WHERE id=?",('أُغلق الطلب دون تنفيذ',result,ts,ident))
   message='سُجلت المهمة كغير منفذة.'
  if task.get('support_ticket_id') and table_exists(c,'support_tickets',s):
   ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(task['support_ticket_id'],)).fetchone()
   if ticket:
    if action=='start': reply='بدأ موظف التقنية AI الفحص الأولي الآمن لطلبك.'
    elif action=='report': reply=report
    elif action=='approve': reply='تم اعتماد خطة موظف التقنية AI، ويجري توثيق النتيجة.'
    else: reply=result
    next_status='resolved' if action=='complete' else 'in_progress' if action in ('start','report','approve') else ticket['status']
    c.execute('UPDATE support_tickets SET status=?,owner_reply=?,updated_at=? WHERE id=?',(next_status,reply,ts,ticket['id']))
    support_event(c,dict(ticket),actor_type='technical_ai',actor_name='موظف التقنية AI',event_type='technical_'+action,body=reply,from_status=ticket['status'],to_status=next_status)
  audit(c,a['name'],'technical_task_'+action,json.dumps({'task':ident,'service':service},ensure_ascii=False))
  return {'saved':True,'message':message,'taskId':ident,'action':action}
 if r=='technical-ai/diagnose' and m=='POST':
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks','technical_incidents','login_failures'))
  service=str(d.get('service','')).strip(); services={'whatsapp':('واتساب','فشل webhook أو صلاحيات الربط','فحص رمز التحقق والتوقيع وسجل آخر webhook','تحديث الإعدادات فقط بعد نجاح اختبار مستقل'),'ai':('الذكاء الاصطناعي','الخدمة غير مهيأة أو تجاوزت الحد','مراجعة إعداد الخادم وحدود الباقة','إعادة مزامنة الحالة دون تغيير الأسرار'),'calls':('المكالمات','قناة الاتصال غير جاهزة أو بها فشل','فحص حالة قناة خدووم وسجل المكالمات','إعادة محاولة الاتصال بعد التحقق من الرصيد'),'login':('تسجيل الدخول','فشل مصادقة مستخدم أو أكثر','فحص وجود المستخدم وحالته وhash كلمة المرور وربط المؤسسة وLogin API والجلسات','اقتراح إعادة المزامنة أو إنهاء الجلسات المنتهية فقط؛ لا تغيير لكلمة المرور دون إجراء رسمي'),'server':('الخادم','بطء أو انقطاع في خادم خدووم','فحص استجابة API واتصال قاعدة البيانات وسجل الأخطاء','إعداد تقرير سبب العطل وخطة إصلاح ثم اختبارها قبل الاعتماد'),'database':('قاعدة البيانات','فشل استعلام أو بطء في البيانات','فحص اتصال القاعدة وسلامة الاستعلامات دون تغيير البيانات','اقتراح فهرسة أو إصلاح آمن بعد موافقة الإدارة')}
  if service not in services: raise ValueError('اختر خدمة مدعومة')
  label,problem,cause,proposal=services[service]; ts=stamp(); organization_id=number(d.get('organization_id'),1,100000000,True) if d.get('organization_id') else None; user_id=number(d.get('user_id'),1,100000000,True) if d.get('user_id') else None; cur=c.execute('INSERT INTO technical_incidents(service,organization_id,problem,root_cause,proposal,severity,test_status,deployment_status,affected_organizations,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id', (service,organization_id,problem,cause,proposal,'medium','not_tested','proposed',1 if organization_id else 0,ts,ts)); incident_id=cur.fetchone()['id']; cur=c.execute('INSERT INTO technical_tasks(organization_id,user_id,service,problem,severity,status,diagnosis,proposal,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id',(organization_id,user_id,service,problem,'medium','diagnosed',cause,proposal,ts,'ai')); task_id=cur.fetchone()['id']; audit(c,a['name'],'technical_diagnosis',service); return {'id':incident_id,'taskId':task_id,'service':label,'problem':problem,'rootCause':cause,'proposal':proposal,'testStatus':'not_tested','deploymentStatus':'proposed'}
 if re.fullmatch(r'technical-ai/\d+/(test|approve|reject)',r) and m=='POST':
  ident=int(r.split('/')[1]); action=r.split('/')[2]; item=c.execute('SELECT id FROM technical_incidents WHERE id=?',(ident,)).fetchone()
  if not item: raise ValueError('التشخيص غير موجود')
  if action=='test': c.execute("UPDATE technical_incidents SET test_status='passed',updated_at=? WHERE id=?",(stamp(),ident)); message='تم تسجيل نجاح الاختبار؛ لم يتم نشر أي كود'
  elif action=='approve': c.execute("UPDATE technical_incidents SET deployment_status='approved',approved_by=?,updated_at=? WHERE id=?",(a['name'],stamp(),ident)); message='تمت الموافقة للمراجعة؛ النشر ما زال يدويًا'
  else: c.execute("UPDATE technical_incidents SET deployment_status='rejected',updated_at=? WHERE id=?",(stamp(),ident)); message='تم رفض الإصلاح المقترح'
  audit(c,a['name'],'technical_'+action,ident); return {'saved':True,'message':message}
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
  if q.get('search'):
   term='%'+q['search'].lower()[:100]+'%'; conditions.append('(LOWER(o.name) LIKE ? OR o.phone LIKE ? OR EXISTS (SELECT 1 FROM users su WHERE su.organization_id=o.id AND (LOWER(su.name) LIKE ? OR su.phone LIKE ? OR LOWER(su.username) LIKE ?)))'); args.extend([term,term,term,term,term])
  if q.get('since'): conditions.append('o.created_at>=?'); args.append(date(q['since']))
  return paged(c,ORG_SELECT,ORG_FROM+' WHERE '+' AND '.join(conditions),args,'o.id DESC',page)
 if re.fullmatch(r'organizations/\d+',r) and m=='GET':
  ident=int(r.split('/')[1]); org=c.execute(ORG_SELECT+',o.activity '+ORG_FROM+' WHERE o.id=?',(ident,)).fetchone()
  if not org: raise s.ApiError(404,'المؤسسة غير موجودة')
  out=dict(org); out['account_id']=(c.execute('SELECT account_id FROM account_organizations WHERE organization_id=?',(ident,)).fetchone() or {'account_id':None})['account_id']; out['branches']=rows(c,'SELECT id,name,status FROM organization_branches WHERE organization_id=? ORDER BY created_at,id',(ident,)); out['users']=rows(c,"SELECT u.id,u.name,u.email,u.phone,u.role,u.active,(SELECT reason FROM login_failures f WHERE f.user_id=u.id ORDER BY f.id DESC LIMIT 1) last_login_failure FROM users u WHERE u.organization_id=? ORDER BY u.id LIMIT 100",(ident,)); out['devices']=rows(c,'SELECT se.token_hash id,se.device_name,se.device_id,se.last_seen_at,se.trusted,se.expires_at,u.name FROM sessions se JOIN users u ON u.id=se.user_id WHERE u.organization_id=? AND se.expires_at>? ORDER BY se.last_seen_at DESC LIMIT 100',(ident,stamp())); out['employees']=rows(c,'SELECT employee_type,COUNT(*) requests FROM ai_usage WHERE organization_id=? GROUP BY employee_type',(ident,)); out['ads']=rows(c,'SELECT id,title,active,approved,expires_at FROM advertisements WHERE organization_id=? ORDER BY id DESC LIMIT 50',(ident,)); out['rewards']=rows(c,'SELECT kind,amount,reason,actor,created_at FROM platform_rewards WHERE organization_id=? ORDER BY id DESC LIMIT 30',(ident,)); out['credits']=credits_summary(c,ident,s); wa=c.execute('SELECT phone_number,phone_number_id,waba_id,updated_at FROM whatsapp_connections WHERE organization_id=?',(ident,)).fetchone() if table_exists(c,'whatsapp_connections',s) else None; wa_messages=scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages WHERE organization_id=?',(ident,)) if table_exists(c,'whatsapp_messages',s) else 0; wa_hook=c.execute('SELECT received_at FROM whatsapp_webhooks WHERE phone_number_id=?',(wa['phone_number_id'],)).fetchone() if wa and table_exists(c,'whatsapp_webhooks',s) else None; call_link=c.execute('SELECT phone_number,status,last_error,updated_at FROM call_connections WHERE organization_id=?',(ident,)).fetchone() if table_exists(c,'call_connections',s) else None; ai_ready=bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip()); out['services']={'account':{'status':'ok','label':'الحساب'},'package':{'status':'ok' if org['package'] else 'warning','label':'الباقة'},'permissions':{'status':'ok' if out['users'] else 'warning','label':'الصلاحيات'},'whatsapp':{'status':'ok' if wa else 'not_connected','label':'واتساب','phone':wa['phone_number'] if wa else None,'messages':wa_messages,'webhook':bool(wa_hook)},'ai':{'status':'ok' if ai_ready else 'not_configured','label':'AI','requests':out['credits']['services']['ai']['used_month']},'calls':{'status':call_link['status'] if call_link else 'not_connected','label':'المكالمات','phone':call_link['phone_number'] if call_link else None,'last_error':call_link['last_error'] if call_link else ''},'payment':{'status':'ok','label':'الدفع'},'server':{'status':'ok','label':'السيرفر'}}; return out
 if re.fullmatch(r'organizations/\d+/users/\d+/status',r) and m=='POST':
  _,org_part,_,user_part,_=r.split('/'); ident=int(org_part); user_id=int(user_part)
  user=c.execute('SELECT id,name,active FROM users WHERE id=? AND organization_id=?',(user_id,ident)).fetchone()
  if not user: raise s.ApiError(404,'المستخدم غير موجود في هذه المؤسسة')
  active=int(bool(d.get('active')))
  c.execute('UPDATE users SET active=? WHERE id=?',(active,user_id))
  if not active: c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
  audit(c,a['name'],'organization_user_status',json.dumps({'organization_id':ident,'user_id':user_id,'active':bool(active)},ensure_ascii=False))
  return {'saved':True,'active':bool(active),'message':'تم إعادة تفعيل المستخدم' if active else 'تم إيقاف المستخدم وإنهاء جلساته'}
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
 if r=='packages' and m=='GET':
  out=rows(c,'SELECT * FROM platform_packages ORDER BY monthly')
  for item in out:
   price_map=package_price_map(c,item['package'])
   item['prices']=[{'months':months,'price_sar':price} for months,price in price_map.items()]
   item['service_limits']={
    'ai_daily':item['ai_daily'],
    'ai_employees':item['ai_employees'],
    'whatsapp_units':item['whatsapp_units'],
    'calls_units':item['calls_units'],
    'ads_units':item['ads_units'],
   }
   item['price_warnings']=price_warnings(price_map) if item['package']!='free' else []
  return out
 if r=='packages' and m=='PUT':
  pkg=d.get('package')
  if pkg not in ('free','basic','vip'): raise ValueError('الباقة غير صحيحة')
  settings=c.execute('SELECT * FROM platform_packages WHERE package=?',(pkg,)).fetchone()
  if settings is None: raise ValueError('الباقة غير موجودة')
  current=package_price_map(c,pkg)
  prices={}
  for months in PACKAGE_DURATIONS:
   raw=d.get(f'price_{months}')
   if raw is None: raw=d.get('monthly') if months==1 else d.get('yearly') if months==12 else None
   if raw is None: raw=current[months]
   try: prices[months]=number(raw,0,100000000)
   except ValueError: raise ValueError(f'سعر مدة {months} شهر يجب أن يكون رقمًا صحيحًا أو عشريًا')
  if pkg=='free' and any(prices.values()): raise ValueError('الباقة المجانية سعرها صفر')
  warnings=price_warnings(prices) if pkg!='free' else []
  daily=number(d.get('ai_daily',settings['ai_daily']),1,1000000,True); employees=number(d.get('ai_employees',settings['ai_employees']),1,1000000,True)
  limits=[]
  for key in ('whatsapp_units','calls_units','ads_units'):
   raw=d[key] if key in d else settings[key]
   limits.append(None if raw is None or raw=='' else number(raw,0,1000000,True))
  features=str(d.get('features',settings['features'] or ''))[:4000]
  c.execute('UPDATE platform_packages SET monthly=?,yearly=?,ai_daily=?,ai_employees=?,whatsapp_units=?,calls_units=?,ads_units=?,features=? WHERE package=?',(prices[1],prices[12],daily,employees,*limits,features,pkg))
  for months,price in prices.items(): c.execute('INSERT INTO package_prices(package,duration_months,price_sar,updated_at) VALUES(?,?,?,?) ON CONFLICT(package,duration_months) DO UPDATE SET price_sar=excluded.price_sar,updated_at=excluded.updated_at',(pkg,months,price,stamp()))
  audit(c,a['name'],'package_prices_updated',json.dumps({'package':pkg,'prices':prices},ensure_ascii=False))
  return {'saved':True,'prices':prices,'service_limits':{'ai_daily':daily,'ai_employees':employees,'whatsapp_units':limits[0],'calls_units':limits[1],'ads_units':limits[2]},'price_warnings':warnings}
 if r=='codes' and m=='GET': return paged(c,'SELECT id,code_prefix,recipient_name,discount_percent,discount_amount,starts_at,expires_at,max_uses,used_count,eligible_packages,eligible_durations,active',"FROM activation_codes WHERE code_kind='discount'",[],'id DESC',page)
 if r=='codes' and m=='POST':
  code=str(d.get('code','')).upper().strip()
  if not re.fullmatch('[A-Z0-9_-]{2,40}',code): raise ValueError('الكود من حرفين إلى 40 حرفًا إنجليزيًا أو رقمًا')
  start=date(d.get('starts_at')); end=date(d.get('expires_at'))
  if start and end and end<=start: raise ValueError('نهاية الكود يجب أن تكون بعد بدايته')
  percent=number(d.get('discount_percent',0),0,100); fixed=number(d.get('discount_amount',0)); eligible=d.get('eligible_packages','basic,vip'); durations=d.get('eligible_durations','1,3,6,12')
  if any(x not in ('basic','vip') for x in eligible.split(',')) or any(int(x) not in PACKAGE_DURATIONS for x in durations.split(',') if x) or (percent and fixed) or not(percent or fixed): raise ValueError('اختر نسبة أو مبلغًا وحدد الباقات')
  digest=hashlib.sha256(code.encode()).hexdigest()
  if c.execute('SELECT id FROM activation_codes WHERE code_hash=?',(digest,)).fetchone(): raise ValueError('الكود موجود؛ لن يعاد تصفير استخدامه')
  c.execute('INSERT INTO activation_codes(code_hash,code_prefix,package,duration_days,max_uses,expires_at,recipient_name,code_kind,discount_percent,discount_amount,eligible_packages,eligible_durations,starts_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(digest,code,'basic',0,number(d.get('max_uses'),1,1000000,True),end,str(d.get('recipient_name',''))[:100],'discount',percent,fixed,eligible,durations,start,stamp())); return {'saved':True}
 if re.fullmatch(r'codes/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); old=c.execute("SELECT * FROM activation_codes WHERE id=? AND code_kind='discount'",(ident,)).fetchone()
  if not old: raise s.ApiError(404,'الكود غير موجود')
  if 'recipient_name' in d:
   start=date(d.get('starts_at')); end=date(d.get('expires_at')); percent=number(d.get('discount_percent',0),0,100); fixed=number(d.get('discount_amount',0)); maximum=number(d.get('max_uses'),old['used_count'],1000000,True); eligible=d.get('eligible_packages','basic,vip'); durations=d.get('eligible_durations','1,3,6,12')
   if (start and end and end<=start) or (percent and fixed) or any(x not in ('basic','vip') for x in eligible.split(',')) or any(int(x) not in PACKAGE_DURATIONS for x in durations.split(',') if x): raise ValueError('تحقق من المدة والخصم والباقات')
   c.execute('UPDATE activation_codes SET recipient_name=?,starts_at=?,expires_at=?,discount_percent=?,discount_amount=?,max_uses=?,eligible_packages=?,eligible_durations=? WHERE id=?',(str(d['recipient_name'])[:100],start,end,percent,fixed,maximum,eligible,durations,ident))
  else: c.execute('UPDATE activation_codes SET active=? WHERE id=?',(int(bool(d.get('active'))),ident))
  return {'saved':True}
 if r=='offers' and m=='GET':
  out=rows(c,'SELECT * FROM package_offers WHERE base_price_sar IS NOT NULL ORDER BY id DESC')
  for o in out:
   o['original_price_sar']=package_price_map(c,o['package']).get(int(o['paid_months']),o.get('base_price_sar') or o['price_sar'])
   o['effective_active']=bool(o['active'] and active_offer(o))
  return out
 if r=='credits' and m=='GET':
  condition='WHERE 1=1'; args=['free']
  if q.get('package') in ('free','basic','vip'): condition+=' AND COALESCE(s.package,?)=?'; args.extend(['free',q['package']])
  items=rows(c,'SELECT o.id,o.name,COALESCE(s.package,?) package FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id '+condition,args)
  for item in items: item['credits']=credits_summary(c,item['id'],s)
  return {'items':items,'total':len(items),'page':1,'pageSize':len(items)}
 if r=='expenses' and m=='GET':
  return finance_summary(c)
 if r=='expenses' and m=='POST':
  provider=str(d.get('provider','')).strip()[:160]; service=str(d.get('service','other')).strip()[:40]
  issued=str(d.get('issued_at','')).strip()[:40]; due=str(d.get('due_at','')).strip()[:40]
  if not provider or not issued or not due: raise ValueError('المزود وتاريخ الإصدار والاستحقاق مطلوبة')
  subtotal=number(d.get('subtotal',0),0,100000000); tax=number(d.get('tax',0),0,100000000); total=round(subtotal+tax,2)
  status=d.get('status','unpaid')
  if status not in ('paid','unpaid'): status='unpaid'
  attachment=str(d.get('attachment_data',''))
  if len(attachment)>3000000: raise ValueError('حجم المرفق كبير جدًا')
  paid_at=stamp() if status=='paid' else None
  cur=c.execute('INSERT INTO platform_expenses(provider,service,invoice_number,subtotal,tax,total,issued_at,due_at,status,payment_method,paid_at,payment_reference,notes,attachment_data,recurring,recurrence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(provider,service,str(d.get('invoice_number',''))[:100],subtotal,tax,total,issued,due,status,str(d.get('payment_method',''))[:80],paid_at,str(d.get('payment_reference',''))[:120],str(d.get('notes',''))[:2000],attachment,int(bool(d.get('recurring'))),str(d.get('recurrence',''))[:40],stamp(),stamp()))
  audit(c,a['name'],'finance_expense_created',json.dumps({'id':cur.lastrowid,'before':None,'after':{'total':total,'status':status}},ensure_ascii=False))
  return {'saved':True,'id':cur.lastrowid}
 if re.fullmatch(r'expenses/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); old=c.execute('SELECT * FROM platform_expenses WHERE id=?',(ident,)).fetchone()
  if not old: raise s.ApiError(404,'الفاتورة غير موجودة')
  status=d.get('status',old['status']);
  if status not in ('paid','unpaid'): raise ValueError('حالة الفاتورة غير صحيحة')
  payment_reference=str(d.get('payment_reference',old['payment_reference']))[:120]; payment_method=str(d.get('payment_method',old['payment_method']))[:80]
  paid_at=old['paid_at'] or stamp() if status=='paid' else None
  c.execute('UPDATE platform_expenses SET status=?,payment_method=?,paid_at=?,payment_reference=?,notes=?,updated_at=? WHERE id=?',(status,payment_method,paid_at,payment_reference,str(d.get('notes',old['notes']))[:2000],stamp(),ident))
  audit(c,a['name'],'finance_expense_updated',json.dumps({'id':ident,'before':{'status':old['status'],'paid_at':old['paid_at']},'after':{'status':status,'paid_at':paid_at}},ensure_ascii=False))
  return {'saved':True}
 if re.fullmatch(r'expenses/\d+',r) and m=='DELETE':
  ident=int(r.split('/')[1]); old=c.execute('SELECT id,provider,total,status FROM platform_expenses WHERE id=?',(ident,)).fetchone()
  if not old: raise s.ApiError(404,'الفاتورة غير موجودة')
  c.execute('DELETE FROM platform_expenses WHERE id=?',(ident,)); audit(c,a['name'],'finance_expense_deleted',json.dumps({'before':dict(old),'after':None},ensure_ascii=False)); return {'deleted':True}
 if re.fullmatch(r'credits/\d+',r) and m=='POST':
  ident=int(r.split('/')[1]); service=str(d.get('service','')).strip(); units=number(d.get('units'),-1000000,1000000,True); reason=str(d.get('reason','')).strip()[:500]
  if service not in SERVICE_LABELS: raise ValueError('اختر خدمة صحيحة')
  if units==0 or not reason: raise ValueError('اكتب كمية غير صفرية وسبب التعديل')
  if not c.execute('SELECT id FROM organizations WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'المؤسسة غير موجودة')
  c.execute('INSERT INTO platform_credit_ledger(organization_id,service,units,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(ident,service,units,a['name'],reason,stamp()))
  audit(c,a['name'],'credit_adjustment',f'{ident}/{service}/{units}/{reason}')
  return {'saved':True,'credits':credits_summary(c,ident,s)}
 if r=='offers' and m=='POST':
  pkg=d.get('package'); start=date(d.get('starts_at')); end=date(d.get('ends_at')); kind=d.get('offer_type','price')
  if pkg not in ('basic','vip','basic,vip') or not start or not end or end<=start or kind not in ('price','percent','bonus'): raise ValueError('حدد الباقة والمدة وتاريخ بداية ونهاية صحيحين للعرض')
  months=number(d.get('paid_months'),1,12,True); bonus=number(d.get('bonus_months',0),0,60,True); percent=number(d.get('discount_percent',0),0,100)
  if months not in PACKAGE_DURATIONS: raise ValueError('اختر مدة شهر أو 3 أو 6 أو 12 شهرًا')
  for package in pkg.split(','):
   base=package_price_map(c,package).get(months)
   if base is None: raise ValueError('السعر الأساسي لهذه المدة غير مهيأ')
   price=round(base*(100-percent)/100,2) if kind=='percent' else base if kind=='bonus' else number(d.get('price_sar'))
   c.execute('INSERT INTO package_offers(package,paid_months,bonus_months,price_sar,label,active,created_at,starts_at,ends_at,offer_type,discount_percent,base_price_sar) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(package,months,bonus,price,str(d.get('label',''))[:100],1,stamp(),start,end,kind,percent if kind=='percent' else 0,base))
  return {'saved':True}
 if re.fullmatch(r'offers/\d+',r) and m=='PUT': c.execute('UPDATE package_offers SET active=? WHERE id=?',(int(bool(d.get('active'))),int(r.split('/')[1]))); return {'saved':True}
 if re.fullmatch(r'offers/\d+',r) and m=='DELETE':
  ident=int(r.split('/')[1]); offer=c.execute('SELECT active,ends_at FROM package_offers WHERE id=?',(ident,)).fetchone()
  if not offer: raise s.ApiError(404,'العرض غير موجود')
  if offer['active'] and active_offer(offer): raise ValueError('أوقف العرض أولًا أو انتظر انتهاء مدته قبل الحذف')
  c.execute('DELETE FROM package_offers WHERE id=?',(ident,)); audit(c,a['name'],'offer_deleted',ident)
  return {'saved':True,'message':'تم حذف العرض المنتهي أو المتوقف'}
 if r=='support' and m=='GET':
  # Every support request must have a visible, organization-scoped AI follow-up.
  # Older requests are repaired here as well, without altering their complaint.
  ensure_owner_tables(c,s,('support_tickets','technical_tasks','technical_agent_state','platform_notes','support_ticket_events'))
  args=[]; where='FROM support_tickets t JOIN organizations o ON o.id=t.organization_id LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_admins pa ON pa.id=t.assigned_admin_id WHERE 1=1'
  # Backfill readable references for legacy requests without changing their internal IDs.
  for legacy in rows(c, "SELECT id,created_at FROM support_tickets WHERE reference_code='' OR reference_code IS NULL"):
   c.execute('UPDATE support_tickets SET reference_code=? WHERE id=?',(support_reference(legacy['id'],legacy.get('created_at')),legacy['id']))
  if q.get('status'): where+=' AND t.status=?'; args.append(q['status'])
  out=paged(c,'SELECT t.*,o.name organization_name,s.package,pa.name assigned_admin_name',where,args,'t.id DESC',page)
  for t in out['items']:
   task=c.execute("SELECT id,status,diagnosis,proposal,action_taken,result,started_at,finished_at FROM technical_tasks WHERE support_ticket_id=? OR (support_ticket_id IS NULL AND service='support' AND problem LIKE ?) ORDER BY id DESC LIMIT 1",(t['id'],f"طلب دعم #{t['id']}:%",)).fetchone()
   if not task:
    ts=stamp()
    cur=c.execute('INSERT INTO technical_tasks(organization_id,branch_id,user_id,support_ticket_id,service,problem,severity,status,diagnosis,proposal,action_taken,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id',(t['organization_id'],str(t.get('branch_id') or '')[:120],t.get('user_id'),t['id'],'support',f"طلب دعم #{t['id']}: {t['category']} — {t['message']}",'medium','queued','تم استلام الشكوى وتحويلها إلى موظف AI للتشخيص الآمن','فحص السجلات والصلاحيات والربط المرتبطة بالمؤسسة، دون تغيير بيانات الإنتاج','بانتظار بدء الفحص',ts,'support-repair'))
   task=c.execute('SELECT id,status,diagnosis,proposal,action_taken,result,started_at,finished_at FROM technical_tasks WHERE id=?',(cur.fetchone()['id'],)).fetchone()
   if task and task['status']=='queued':
    ts=stamp(); reply='تم استلام طلبك وإسناده إلى موظف التقنية AI. بدأ الفحص الأولي، وسيظهر التقرير هنا عند اكتماله.'
    c.execute("UPDATE technical_tasks SET status='diagnosing',action_taken=?,started_at=?,finished_at=NULL WHERE id=?",('بدأ موظف التقنية AI الفحص الأولي الآمن',ts,task['id']))
    if t['status'] in ('open','under_review'):
     c.execute("UPDATE support_tickets SET status='in_progress',owner_reply=?,updated_at=? WHERE id=?",(reply,ts,t['id']))
     support_event(c,t,actor_type='technical_ai',actor_name='موظف التقنية AI',event_type='technical_assigned',body=reply,from_status=t['status'],to_status='in_progress')
    task=c.execute('SELECT id,status,diagnosis,proposal,action_taken,result,started_at,finished_at FROM technical_tasks WHERE id=?',(task['id'],)).fetchone()
   if task:
    t['technical_task']=dict(task)
    t['technical_task_id']=task['id']
    t['technical_status']=task['status']
   t['notes']=rows(c,'SELECT note,actor,created_at FROM platform_notes WHERE ticket_id=? ORDER BY id DESC LIMIT 20',(t['id'],))
   t['events']=rows(c,'SELECT actor_type,actor_name,event_type,from_status,to_status,body,created_at FROM support_ticket_events WHERE ticket_id=? ORDER BY id DESC LIMIT 50',(t['id'],))
  return out
 if re.fullmatch(r'support/\d+/technical-followup',r) and m=='POST':
  ensure_owner_tables(c,s,('technical_tasks',))
  ident=int(r.split('/')[1]); ticket=c.execute('SELECT organization_id,user_id,category,message FROM support_tickets WHERE id=?',(ident,)).fetchone()
  if not ticket: raise s.ApiError(404,'طلب الدعم غير موجود')
  existing=c.execute("SELECT id,status FROM technical_tasks WHERE support_ticket_id=? OR (support_ticket_id IS NULL AND service='support' AND problem LIKE ?) ORDER BY id DESC LIMIT 1",(ident,f'طلب دعم #{ident}:%',)).fetchone()
  ts=stamp()
  if existing:
   c.execute("UPDATE technical_tasks SET status='diagnosing',diagnosis=?,action_taken=?,started_at=?,finished_at=NULL WHERE id=?",('تمت إعادة توجيه الطلب للمتابعة التقنية وجمع مؤشرات المشكلة','بانتظار فحص الموظف التقني AI',ts,existing['id']))
   task_id=existing['id']
  else:
   cur=c.execute('INSERT INTO technical_tasks(organization_id,user_id,support_ticket_id,service,problem,severity,status,diagnosis,proposal,action_taken,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id',(ticket['organization_id'],ticket['user_id'],ident,'support',f"طلب دعم #{ident}: {ticket['category']} — {ticket['message']}",'medium','queued','تم تحويل الطلب إلى الموظف التقني AI لجمع مؤشرات الحساب والخدمة','تشخيص السجلات والصلاحيات والربط دون تغيير كلمة المرور أو حذف البيانات','بانتظار فحص الموظف التقني AI',ts,'support-followup'))
   task_id=cur.fetchone()['id']
  c.execute("UPDATE support_tickets SET status='in_progress',updated_at=? WHERE id=?",(ts,ident))
  c.execute("UPDATE support_tickets SET owner_reply=? WHERE id=?",('تم استلام طلبك وتحويله إلى الموظف التقني AI. جاري فحص المشكلة، وسيتم إبلاغك بالنتيجة بعد اكتمال المتابعة.',ident))
  fresh=dict(c.execute('SELECT id,organization_id,user_id FROM support_tickets WHERE id=?',(ident,)).fetchone())
  support_event(c,fresh,actor_type='admin',actor_name=a['name'],event_type='technical_followup',body='تم إرسال الطلب للمتابعة مع الموظف التقني AI',from_status='open',to_status='in_progress')
  c.execute('INSERT INTO platform_notes(ticket_id,note,actor,created_at) VALUES(?,?,?,?)',(ident,'تم إرسال الطلب للمتابعة مع الموظف التقني AI','لوحة أمن خدووم',ts))
  audit(c,a['name'],'support_technical_followup',json.dumps({'ticket_id':ident,'task_id':task_id},ensure_ascii=False))
  return {'saved':True,'taskId':task_id,'message':'تم إرسال الطلب للمتابعة مع الموظف التقني AI'}
 if re.fullmatch(r'support/\d+',r) and m=='DELETE':
  ident=int(r.split('/')[1])
  if not c.execute('SELECT id FROM support_tickets WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'Support request not found')
  c.execute('DELETE FROM platform_notes WHERE ticket_id=?',(ident,)); c.execute('DELETE FROM support_tickets WHERE id=?',(ident,)); return {'deleted':True}
 if re.fullmatch(r'support/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); status=d.get('status')
  if status not in ('open','under_review','in_progress','awaiting_user','resolved','closed'): raise ValueError('حالة غير صحيحة')
  ticket=c.execute('SELECT id,organization_id,user_id,status,scope FROM support_tickets WHERE id=?',(ident,)).fetchone()
  if not ticket: raise s.ApiError(404,'طلب الدعم غير موجود')
  reply=str(d.get('owner_reply',''))[:1000]; note=str(d.get('note',''))[:2000]
  last_error=str(d.get('last_error',''))[:1000]
  scope=str(d.get('scope') or ticket['scope'] or 'private')
  if scope not in ('private','global'): raise ValueError('نطاق المشكلة غير صحيح')
  assigned=d.get('assigned_admin_id') or a['id']
  c.execute('UPDATE support_tickets SET status=?,scope=?,last_error=?,owner_reply=?,assigned_admin_id=COALESCE(?,assigned_admin_id),updated_at=? WHERE id=?',(status,scope,last_error,reply,assigned,stamp(),ident))
  event_type='scope_changed' if scope!=ticket['scope'] else 'status_changed' if status!=ticket['status'] else 'reply_updated'
  support_event(c,dict(ticket),actor_type='admin',actor_name=a['name'],event_type=event_type,body=note or reply or last_error,from_status=ticket['status'],to_status=status)
  if scope=='global' and ticket['scope']!='global':
   c.execute('INSERT INTO technical_incidents(service,organization_id,problem,root_cause,proposal,severity,test_status,deployment_status,affected_organizations,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('support',None,'عطل عام معلن من مركز الدعم','تحتاج عدة مؤسسات إلى مراجعة موحدة؛ لا يتم إصلاح الإنتاج تلقائيًا','فحص آمن ثم اختبار وموافقة قبل أي تطبيق عام','high','not_tested','proposed',0,stamp(),stamp()))
  if note: c.execute('INSERT INTO platform_notes(ticket_id,note,actor,created_at) VALUES(?,?,?,?)',(ident,note,a['name'],stamp()))
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
  if category=='devices': return paged(c,'SELECT se.device_name,se.device_id,se.token_hash session_id,se.last_seen_at,se.trusted,u.name,u.organization_id','FROM sessions se JOIN users u ON u.id=se.user_id WHERE se.expires_at>?',[stamp()],'se.created_at DESC',page)
  if category=='password':
   return paged(c,'SELECT *',"FROM (SELECT summary,created_at,'customer_account' action FROM audit_logs WHERE action='password_reset' UNION ALL SELECT target summary,created_at,'admin_account' action FROM platform_audit WHERE action='password_reset') resets",[],'created_at DESC',page)

  actions="'login','failed_login','new_device'" if category=='login' else "'failed_login','new_device','blocked_device_login','owner_account_status','suspicious_login'"
  out=paged(c,'SELECT a.action,a.summary,a.created_at,o.name organization_name,a.organization_id',f'FROM audit_logs a JOIN organizations o ON o.id=a.organization_id WHERE a.action IN ({actions})',[],'a.id DESC',page); out['unknownLogins']=rows(c,'SELECT account,created_at FROM platform_unknown_logins ORDER BY id DESC LIMIT 30');out['adminLogins']=rows(c,'SELECT account,success,created_at FROM platform_login_events ORDER BY id DESC LIMIT 30'); return out
 if r=='usage' and m=='GET': return usage(c,q,page,s)
 if r=='ads' and m=='GET':
  ensure_owner_tables(c,s,('advertisements',))
  condition="CASE WHEN a.approved=1 AND a.expires_at IS NOT NULL AND a.expires_at<=? THEN 'expired' WHEN a.active=0 AND a.approved=0 THEN 'rejected' WHEN a.active=0 THEN 'stopped' WHEN a.approved=0 THEN 'pending' WHEN a.scheduled_at>? THEN 'scheduled' ELSE 'published' END"
  where='FROM advertisements a JOIN organizations o ON o.id=a.organization_id WHERE COALESCE(a.deleted,0)=0'; args=[stamp(),stamp()]
  # Status expression is in the projection; use a subquery for pagination and filtering.
  src='FROM (SELECT a.*,o.name organization_name,'+condition+' status '+where+') ads WHERE 1=1'
  if q.get('status'): src+=' AND status=?'; args.append(q['status'])
  return paged(c,'SELECT *',src,args,'id DESC',page)
 if re.fullmatch(r'ads/\d+/preview',r) and m=='GET':
  ensure_owner_tables(c,s,('advertisements',))
  ident=int(r.split('/')[1]); ad=c.execute("SELECT a.*,o.name organization_name FROM advertisements a JOIN organizations o ON o.id=a.organization_id WHERE a.id=? AND COALESCE(a.deleted,0)=0",(ident,)).fetchone()
  if not ad: raise s.ApiError(404,'الإعلان غير موجود')
  out=dict(ad)
  try: out['banner_config']=json.loads(out.get('banner_config') or '{}')
  except (TypeError,ValueError): out['banner_config']={}
  out['preview_only']=True
  return out
 if re.fullmatch(r'ads/\d+',r) and m=='DELETE':
  ensure_owner_tables(c,s,('advertisements',))
  ident=int(r.split('/')[1]); old=c.execute('SELECT id,title,active,approved FROM advertisements WHERE id=? AND COALESCE(deleted,0)=0',(ident,)).fetchone()
  if not old: raise s.ApiError(404,'الإعلان غير موجود')
  c.execute('UPDATE advertisements SET active=0,deleted=1 WHERE id=?',(ident,)); audit(c,a['name'],'advertisement_deleted',json.dumps({'ad_id':ident,'title':old['title']},ensure_ascii=False)); return {'saved':True}
 if re.fullmatch(r'ads/\d+',r) and m=='PUT':
  ensure_owner_tables(c,s,('advertisements',))
  ident=int(r.split('/')[1]); old=c.execute('SELECT * FROM advertisements WHERE id=? AND COALESCE(deleted,0)=0',(ident,)).fetchone()
  if not old: raise s.ApiError(404,'الإعلان غير موجود')
  status=d.get('status'); start=date(d.get('scheduled_at')); end=date(d.get('expires_at'))
  if status not in ('published','scheduled','rejected','stopped','pending') or (start and end and end<=start): raise ValueError('تحقق من الحالة والتاريخ')
  seconds=number(d.get('display_seconds',old['display_seconds'] if 'display_seconds' in old.keys() else 8),3,60,True)
  config=d.get('banner_config',old['banner_config'] if 'banner_config' in old.keys() else '{}')
  if isinstance(config,str):
   try: config=json.loads(config or '{}')
   except (TypeError,ValueError): raise ValueError('إعدادات تصميم الشريط غير صحيحة')
  if not isinstance(config,dict): raise ValueError('إعدادات تصميم الشريط غير صحيحة')
  for key in ('textColor','barColor','textAlign','logoPosition','fontSize','logoScale','height','textX','textY','logoX','logoY'):
   if key in d and d[key] not in (None,''): config[key]=d[key]
  safe={key:config.get(key) for key in ('textColor','barColor','textAlign','logoPosition','fontSize','logoScale','height','textX','textY','logoX','logoY') if key in config}
  if safe.get('textAlign') not in (None,'right','center','left') or safe.get('logoPosition') not in (None,'right','left'): raise ValueError('موضع التصميم غير صحيح')
  for key,low,high in (('fontSize',12,32),('logoScale',0.5,1.5),('height',44,180),('textX',0.08,0.92),('textY',0.15,0.85),('logoX',0.08,0.92),('logoY',0.15,0.85)):
   if key in safe: safe[key]=number(safe[key],low,high,False)
  for key in ('textColor','barColor'):
   if key in safe and safe[key] is not None and not re.fullmatch(r'#[0-9A-Fa-f]{6}',str(safe[key])): raise ValueError('لون التصميم غير صحيح')
  title=str(d.get('title',old['title']))[:120]; message=str(d.get('message',old['message']))[:1000]; contact=str(d.get('contact',old['contact']))[:80]; image=str(d.get('image_data','')) or str(old['image_data'] if 'image_data' in old.keys() else '')
  if image and (len(image)>850000 or not image.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,'))): raise ValueError('صيغة الصورة غير مدعومة أو حجمها كبير')
  active=int(status not in ('rejected','stopped')); approved=int(status in ('published','scheduled','stopped'))
  published_at=stamp() if status in ('published','scheduled') else None if status=='pending' else old['published_at'] if 'published_at' in old.keys() else None
  published_by=a['name'] if status in ('published','scheduled') else '' if status=='pending' else old['published_by'] if 'published_by' in old.keys() else ''
  c.execute('UPDATE advertisements SET title=?,message=?,contact=?,image_data=?,active=?,approved=?,scheduled_at=?,expires_at=?,approved_at=?,published_at=?,published_by=?,review_note=?,display_seconds=?,banner_config=? WHERE id=?',(title,message,contact,image,active,approved,start,end,stamp(),published_at,published_by,str(d.get('review_note',''))[:500],seconds,json.dumps(safe,ensure_ascii=False),ident))
  audit(c,a['name'],'advertisement_'+status,json.dumps({'ad_id':ident,'before':{'status':old['approved'],'active':old['active'],'display_seconds':old['display_seconds'] if 'display_seconds' in old.keys() else 8},'after':{'status':status,'display_seconds':seconds}},ensure_ascii=False))
  previous_config=old['banner_config'] if 'banner_config' in old.keys() else '{}'
  audit(c,a['name'],'advertisement_design',json.dumps({'ad_id':ident,'before':previous_config,'after':safe},ensure_ascii=False))
  return {'saved':True,'status':status,'display_seconds':seconds}
 if r=='community' or r.startswith('community/'):
  import community_admin
  return community_admin.moderation(c,r,m,d,q,page,s.ApiError,a,bool(s.DATABASE_URL))
 if r=='settings' and m=='GET': return {'database':'PostgreSQL' if s.DATABASE_URL else 'SQLite','sessionHours':8,'pageSize':30,'note':'التكاليف الفعلية والمكالمات والمجتمع تحتاج ربط مصادرها. أسعار الباقات وحدود AI اليومية مرتبطة بالخادم. حقول وحدات واتساب والمكالمات وصفية إلى حين ربط مزود الفوترة.'}
 raise s.ApiError(404,'المسار غير موجود')

def usage(c,q,page,s):
 kind=q.get('type','ai'); today=stamp()[:10]; month=today[:7]+'-01'
 if kind=='calls':
  if not table_exists(c,'call_logs',s): return {'items':[],'note':'لا يوجد سجل مكالمات بعد.'}
  where='FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE 1=1'; args=[]
  if q.get('package') in ('free','basic','vip'): where+=' AND COALESCE(s.package,?)=?'; args.extend(['free',q['package']])
  if q.get('organization'): where+=' AND o.id=?'; args.append(int(q['organization']))
  out=paged(c,"SELECT o.id,o.name,COALESCE(s.package,'free') package",where,args,'o.id DESC',page); ids=[o['id'] for o in out['items']]
  for o in out['items']:
   total=c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs WHERE organization_id=?',(o['id'],)).fetchone()['n']; day=c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs WHERE organization_id=? AND started_at>=?',(o['id'],today)).fetchone()['n']; month_seconds=c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs WHERE organization_id=? AND started_at>=?',(o['id'],month)).fetchone()['n']; minutes=math.ceil(int(total or 0)/60); today_minutes=math.ceil(int(day or 0)/60); month_minutes=math.ceil(int(month_seconds or 0)/60); limit=c.execute('SELECT calls_units FROM platform_packages WHERE package=?',(o['package'],)).fetchone(); base=int(limit['calls_units'] or 0) if limit else 0; o.update({'total':minutes,'today':today_minutes,'month':month_minutes,'remaining':max(0,base-month_minutes),'daily_limit':base,'cost':round(minutes*SERVICE_COSTS['calls'],2),'conversations':c.execute('SELECT COUNT(*) n FROM call_logs WHERE organization_id=?',(o['id'],)).fetchone()['n']})
  out['summary']={'total':sum(x['total'] for x in out['items']),'today':sum(x['today'] for x in out['items']),'month':sum(x['month'] for x in out['items'])}; out['note']='كل دقيقة مكالمة تخصم من رصيد المكالمات. التكلفة المعروضة تقديرية حسب وزن الخدمة المحدد في الخادم.'; return out
 wa=kind=='whatsapp'
 if wa and not table_exists(c,'whatsapp_messages',s): return {'items':[],'note':'لم يسجل خادم واتساب بيانات في هذه القاعدة بعد.'}
 table='whatsapp_messages' if wa else 'ai_usage'; tf='timestamp' if wa else 'created_at'; day=int(datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp()) if wa else today; mon=int(datetime.fromisoformat(month).replace(tzinfo=timezone.utc).timestamp()) if wa else month
 where='FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE 1=1'; args=[]
 if q.get('package') in ('free','basic','vip'): where+=" AND COALESCE(s.package,'free')=?"; args.append(q['package'])
 if q.get('organization'): where+=' AND o.id=?'; args.append(int(q['organization']))
 out=paged(c,"SELECT o.id,o.name,COALESCE(s.package,'free') package",where,args,'o.id DESC',page)
 if not table_exists(c,table,s):
  for o in out['items']: o.update({'total':0,'today':0,'month':0,'daily_limit':None,'cost':None,'remaining':None})
  out['summary']={'total':0,'today':0,'month':0}; out['note']='لا يوجد سجل استهلاك بعد.'; return out
 ids=[o['id'] for o in out['items']]
 marks=','.join('?' for _ in ids)
 stats={}; overrides={}; credits={}
 if ids:
  stats={row['organization_id']:{'total':row['total'],'today':row['today_count'],'month':row['month_count'],**({'conversations':row['conversations']} if wa else {})} for row in rows(c,f'SELECT organization_id,COUNT(*) total,SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END) today_count,SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END) month_count'+(',COUNT(DISTINCT peer) conversations' if wa else '')+f' FROM {table} WHERE organization_id IN ({marks}) GROUP BY organization_id',[day,mon,*ids])}
  if not wa:
   if table_exists(c,'ai_limits',s):
    overrides={r['organization_id']:r['daily_limit'] for r in rows(c,f'SELECT * FROM ai_limits WHERE organization_id IN ({marks})',ids)}
   if table_exists(c,'platform_daily_credits',s):
    credits={r['organization_id']:r['units'] for r in rows(c,f'SELECT * FROM platform_daily_credits WHERE day=? AND organization_id IN ({marks})',[today,*ids])}
 packages={r['package']:r['ai_daily'] for r in rows(c,'SELECT package,ai_daily FROM platform_packages')} if table_exists(c,'platform_packages',s) else {'free':5,'basic':30,'vip':100}
 for o in out['items']:
  o.update(stats.get(o['id'],{'total':0,'today':0,'month':0}));o['daily_limit']=None if wa else overrides.get(o['id'],packages[o['package']])+credits.get(o['id'],0);o['cost']=None;o['remaining']=None if wa else max(0,o['daily_limit']-o['today'])
  if wa:o.setdefault('conversations',0)
 scope=f' FROM {table} WHERE organization_id IN (SELECT o.id '+where+')'
 summary=c.execute(f'SELECT COUNT(*) total,COALESCE(SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END),0) today,COALESCE(SUM(CASE WHEN {tf}>=? THEN 1 ELSE 0 END),0) month_count'+scope,[day,mon,*args]).fetchone()
 out['summary']={'total':summary['total'],'today':summary['today'],'month':summary['month_count']}
 out['note']='المتبقي لـ AI هو الحد اليومي ويشمل وحدات الإدارة لليوم. التكلفة غير متاحة لعدم وجود سجل فوترة.'
 return out
