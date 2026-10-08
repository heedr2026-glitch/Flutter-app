"""Owner console foundation. Staff credentials are isolated from customer accounts."""
import base64
import hashlib
import hmac
import json
import struct
import time
import math
import re
import secrets
import os
import service_quota
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

PERMISSIONS = ['organizations.view','organizations.edit','packages','codes','offers','usage','security','support','ads','community','rewards','suspend','admins','settings','integrations','finance']
ROLES = {'owner': PERMISSIONS, 'system': [p for p in PERMISSIONS if p != 'admins'], 'manager': [p for p in PERMISSIONS if p != 'admins'], 'support': ['organizations.view','organizations.edit','suspend','support'], 'technician': ['organizations.view','organizations.edit','usage','security','support','settings'], 'accounting': ['organizations.view','packages','codes','offers','usage','finance'], 'employee': ['organizations.view','support','ads'], 'ads': ['ads'], 'community': ['community']}
def stamp(): return datetime.now(timezone.utc).isoformat()
def rows(c,sql,args=()): return [dict(r) for r in c.execute(sql,args).fetchall()]
def scalar(c,sql,args=()): return c.execute(sql,args).fetchone()['n']
def audit(c,actor,action,target): c.execute('INSERT INTO platform_audit(actor,action,target,created_at) VALUES(?,?,?,?)',(actor,action,str(target)[:500],stamp()))

SENIOR_ROLES=('owner','system','manager')
def require_senior(actor,s,what='هذا الإجراء'):
 """إجراءات حساسة (حساب التحويل البنكي، الإيقاف الطارئ، الحذف النهائي) للمالك ومدير النظام والمدير فقط."""
 if (actor or {}).get('role') not in SENIOR_ROLES: raise s.ApiError(403,what+' متاح للمالك أو المدير فقط')

def totp_at(secret,step_index,digits=6):
 """رمز TOTP القياسي (RFC 6238, SHA-1, 30 ثانية) المتوافق مع تطبيقات المصادقة."""
 key=base64.b32decode(secret+'='*(-len(secret)%8),casefold=True)
 digest=hmac.new(key,struct.pack('>Q',int(step_index)),hashlib.sha1).digest()
 offset=digest[-1]&0x0F
 return str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7FFFFFFF)%(10**digits)).zfill(digits)

def totp_match(secret,code,now=None):
 """يعيد رقم الفترة المطابقة (الحالية أو ±1 لفرق الساعة) أو None."""
 code=re.sub(r'\s','',str(code or ''))
 if not secret or not re.fullmatch(r'\d{6}',code): return None
 current=int((time.time() if now is None else now)//30)
 for step in (current,current-1,current+1):
  if hmac.compare_digest(totp_at(secret,step),code): return step
 return None

def client_ip(h):
 """عنوان العميل الفعلي خلف موازن الاستضافة؛ بدونه يتشارك كل العملاء عنوان الموازن."""
 forwarded=str(h.headers.get('X-Forwarded-For','') or '').split(',')[0].strip()
 return (forwarded or h.client_address[0])[:64]

def automated_ticket_update(c,ticket_id,status,reply,ts,force_status=False):
 """تحديث آلي لطلب دعم لا يمسح ردًا كتبه مدير. يعيد الحالة التي ثبتت فعليًا."""
 row=c.execute('SELECT status,owner_reply,owner_reply_by FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
 if row is None: return status
 if row['owner_reply_by']=='admin' and str(row['owner_reply'] or '').strip():
  kept=status if force_status else row['status']
  c.execute('UPDATE support_tickets SET status=?,updated_at=? WHERE id=?',(kept,ts,ticket_id))
  return kept
 c.execute("UPDATE support_tickets SET status=?,owner_reply=?,owner_reply_by='auto',updated_at=? WHERE id=?",(status,reply,ts,ticket_id))
 return status

_FALLBACKS_READY=False
def _fallbacks_ready(c,s):
 """الجدول يُنشأ في ترحيل بدء التشغيل؛ قبل اكتماله لا نلمسه حتى لا يفشل الطلب."""
 global _FALLBACKS_READY
 if not _FALLBACKS_READY: _FALLBACKS_READY=table_exists(c,'subscription_fallbacks',s)
 return _FALLBACKS_READY

def restore_gift_fallbacks(c,current,s):
 """بعد انتهاء هدية VIP المؤقتة يرجع الاشتراك المدفوع السابق إن كانت مدته باقية."""
 if not _fallbacks_ready(c,s): return 0
 due=c.execute('SELECT f.organization_id,f.package,f.expires_at FROM subscription_fallbacks f JOIN subscriptions s ON s.organization_id=f.organization_id WHERE s.expires_at IS NOT NULL AND s.expires_at<=?',(current,)).fetchall()
 for item in due:
  if item['expires_at'] is None or item['expires_at']>current:
   c.execute('UPDATE subscriptions SET package=?,expires_at=? WHERE organization_id=?',(item['package'],item['expires_at'],item['organization_id']))
  c.execute('DELETE FROM subscription_fallbacks WHERE organization_id=?',(item['organization_id'],))
 return len(due)

def clear_gift_fallback(c,org,s):
 """تغيير الباقة يدويًا أو بدفع جديد يلغي أي اشتراك محفوظ للاسترجاع."""
 if _fallbacks_ready(c,s): c.execute('DELETE FROM subscription_fallbacks WHERE organization_id=?',(org,))

def audit_if_ready(c,actor,action,target,s):
 if table_exists(c,'platform_audit',s): audit(c,actor,action,target)

SERVICE_NUMBER_LABELS={'whatsapp':'واتساب','calls':'المكالمات'}
def service_phone(value):
 """أرقام فقط بصيغة دولية بلا + ولا 00، نفس صيغة توجيه واتساب والمكالمات."""
 digits=re.sub(r'[^0-9]','',str(value or '').translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789')))
 return digits[2:] if digits.startswith('00') else digits
def granted_number(c,org,service):
 """الرقم الذي اعتمدته إدارة المنصة لهذه المؤسسة في الخدمة، أو نص فارغ."""
 row=c.execute('SELECT phone FROM service_number_grants WHERE organization_id=? AND service=?',(int(org),service)).fetchone()
 return str(row['phone']) if row else ''
def service_numbers(c,org):
 return {service:granted_number(c,org,service) for service in SERVICE_NUMBER_LABELS}
def save_service_numbers(c,org,data,actor_name):
 """اعتماد أو إلغاء رقم خدمة لمؤسسة. الرقم الواحد لا يُعتمد لمؤسستين في الخدمة نفسها."""
 changed=[]
 for service,label in SERVICE_NUMBER_LABELS.items():
  if service not in data: continue
  phone=service_phone(data.get(service))
  if not phone:
   c.execute('DELETE FROM service_number_grants WHERE organization_id=? AND service=?',(org,service)); changed.append(service+'=')
   continue
  if not re.fullmatch(r'[1-9][0-9]{7,14}',phone): raise ValueError('رقم '+label+' غير صالح؛ اكتبه مع رمز الدولة مثل 9665xxxxxxxx')
  taken=c.execute('SELECT organization_id FROM service_number_grants WHERE service=? AND phone=? AND organization_id<>?',(service,phone,org)).fetchone()
  if taken: raise ValueError('رقم '+label+' معتمد لمؤسسة أخرى (رقم '+str(taken['organization_id'])+')')
  c.execute('INSERT INTO service_number_grants(organization_id,service,phone,created_by,created_at) VALUES(?,?,?,?,?) ON CONFLICT(organization_id,service) DO UPDATE SET phone=excluded.phone,created_by=excluded.created_by,created_at=excluded.created_at',(org,service,phone,str(actor_name)[:120],stamp()))
  changed.append(service+'='+phone)
 return changed

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
 f'''platform_credit_ledger(id {identity},organization_id BIGINT NOT NULL,service TEXT NOT NULL,units INTEGER NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''',
 # الاشتراك المدفوع الذي يُستعاد بعد انتهاء هدية VIP المؤقتة.
 '''subscription_fallbacks(organization_id BIGINT PRIMARY KEY,package TEXT NOT NULL,expires_at TEXT,created_at TEXT NOT NULL)''',
 # المدفوعات المعتمدة فعليًا: أساس تقرير الإيرادات بدل التقدير.
 f'''platform_payments(id {identity},organization_id BIGINT NOT NULL,package TEXT NOT NULL,months INTEGER NOT NULL DEFAULT 1,amount REAL NOT NULL DEFAULT 0,discount_code TEXT NOT NULL DEFAULT '',source TEXT NOT NULL DEFAULT 'transfer',request_id BIGINT UNIQUE,note TEXT NOT NULL DEFAULT '',approved_by TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''']
 schemas += [f'''platform_expenses(id {identity},provider TEXT NOT NULL,service TEXT NOT NULL,invoice_number TEXT NOT NULL DEFAULT '',subtotal REAL NOT NULL DEFAULT 0,tax REAL NOT NULL DEFAULT 0,total REAL NOT NULL DEFAULT 0,issued_at TEXT NOT NULL,due_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'unpaid',payment_method TEXT NOT NULL DEFAULT '',paid_at TEXT, payment_reference TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',attachment_data TEXT NOT NULL DEFAULT '',recurring INTEGER NOT NULL DEFAULT 0,recurrence TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''']
 schemas += [
  # أرقام الخدمات التي اعتمدتها إدارة المنصة لكل مؤسسة؛ لا تُربط خدمة برقم غير معتمد.
  '''service_number_grants(organization_id BIGINT NOT NULL,service TEXT NOT NULL,phone TEXT NOT NULL,created_by TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,PRIMARY KEY(organization_id,service))''',
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
 # سجل آخر الأخطاء لكل مؤسسة، وما تعلّمه الموظف التقني من الشكاوى التي أغلقتها الإدارة.
 schemas+=[f'''organization_api_errors(id {identity},organization_id BIGINT NOT NULL,user_id BIGINT,method TEXT NOT NULL DEFAULT '',route TEXT NOT NULL DEFAULT '',status INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''',
  f'''technical_playbooks(id {identity},title TEXT NOT NULL DEFAULT '',signals TEXT NOT NULL DEFAULT '[]',solution TEXT NOT NULL DEFAULT '',ticket_id BIGINT,uses INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_by TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''']
 # فواتير مزودي الخدمات: مبلغ الشهر ÷ استخدام المنصة في نفس الشهر = تكلفة الوحدة الحقيقية.
 schemas.append('''platform_service_invoices(month TEXT NOT NULL,service TEXT NOT NULL,amount_sar REAL NOT NULL,units INTEGER NOT NULL,unit_cost REAL NOT NULL,actor TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,PRIMARY KEY(month,service))''')
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
 for table,fields in {'platform_packages':[('ai_monthly','INTEGER')],'activation_codes':[('starts_at','TEXT'),('discount_amount','REAL NOT NULL DEFAULT 0'),('eligible_packages',"TEXT NOT NULL DEFAULT 'basic,vip'"),('eligible_durations',"TEXT NOT NULL DEFAULT '1,3,6,12'")],'package_offers':[('starts_at','TEXT'),('ends_at','TEXT'),('offer_type',"TEXT NOT NULL DEFAULT 'price'"),('discount_percent','REAL NOT NULL DEFAULT 0'),('base_price_sar','REAL')],'support_tickets':[('device_name',"TEXT NOT NULL DEFAULT ''"),('app_version',"TEXT NOT NULL DEFAULT ''"),('reference_code',"TEXT NOT NULL DEFAULT ''"),('title',"TEXT NOT NULL DEFAULT ''"),('scope',"TEXT NOT NULL DEFAULT 'private'"),('assigned_admin_id',"BIGINT"),('last_error',"TEXT NOT NULL DEFAULT ''"),('owner_reply_by',"TEXT NOT NULL DEFAULT ''")],'technical_tasks':[('support_ticket_id',"BIGINT"),('knowledge',"TEXT NOT NULL DEFAULT ''"),('problem_type',"TEXT NOT NULL DEFAULT ''"),('needs_owner',"INTEGER NOT NULL DEFAULT 0"),('facts',"TEXT NOT NULL DEFAULT ''"),('interpretation',"TEXT NOT NULL DEFAULT ''"),('suggested_action',"TEXT NOT NULL DEFAULT ''")],'platform_admins':[('totp_secret',"TEXT NOT NULL DEFAULT ''"),('totp_enabled','INTEGER NOT NULL DEFAULT 0'),('totp_last_step','BIGINT NOT NULL DEFAULT 0')],'advertisements':[('scheduled_at','TEXT'),('image_data',"TEXT NOT NULL DEFAULT ''"),('deleted',"INTEGER NOT NULL DEFAULT 0"),('display_seconds',"INTEGER NOT NULL DEFAULT 8"),('banner_config',"TEXT NOT NULL DEFAULT '{}'"),('published_at','TEXT')],'platform_advertisements':[('display_seconds',"INTEGER NOT NULL DEFAULT 8"),('banner_config',"TEXT NOT NULL DEFAULT '{}'"),('published_at','TEXT')],'login_failures':[('backend_status',"TEXT NOT NULL DEFAULT 'ok'"),('session_status',"TEXT NOT NULL DEFAULT 'not_created'"),('user_exists','INTEGER NOT NULL DEFAULT 0'),('account_active','INTEGER NOT NULL DEFAULT 0'),('organization_linked','INTEGER NOT NULL DEFAULT 0'),('password_hash_status',"TEXT NOT NULL DEFAULT 'not_checked'"),('permissions_status',"TEXT NOT NULL DEFAULT 'not_checked'")]}.items():
  existing=set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info('+table+')')}
  for name,typ in fields:
   if postgres or name not in existing: c.execute(f'ALTER TABLE {table} ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+name+' '+typ)
 ad_columns={row['column_name'] for row in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='advertisements'").fetchall()} if postgres else {row['name'] for row in c.execute('PRAGMA table_info(advertisements)')}
 if 'published_by' not in ad_columns: c.execute("ALTER TABLE advertisements ADD COLUMN "+('IF NOT EXISTS ' if postgres else '')+"published_by TEXT NOT NULL DEFAULT ''")
 for table,cols in [('ai_usage','organization_id,created_at'),('audit_logs','action,created_at'),('sessions','user_id,expires_at'),('support_tickets','status,id'),('support_tickets','reference_code'),('support_ticket_events','ticket_id,created_at'),('page_performance_events','created_at,page_name'),('employee_invitations','organization_id,status,created_at'),('organization_cameras','organization_id,branch_id,updated_at'),('vehicle_location_events','organization_id,vehicle_key,recorded_at'),('platform_audit','created_at'),('platform_login_events','ip,created_at'),('login_failures','created_at,username'),('readiness_results','run_id,service_key'),('organizations','created_at'),('subscriptions','package,organization_id'),('platform_credit_ledger','organization_id,service,created_at'),('platform_expenses','status,due_at'),('platform_payments','created_at'),('attendance_events','organization_id,user_id,occurred_at'),('attendance_exceptions','organization_id,user_id,starts_at'),('attendance_devices','organization_id,user_id'),('organization_api_errors','organization_id,created_at')]: c.execute(f'CREATE INDEX IF NOT EXISTS platform_idx_{table} ON {table}({cols})')
 # الطلبات المعتمدة قبل إضافة سجل المدفوعات تُنقل إليه مرة واحدة (المفتاح الفريد يمنع التكرار).
 c.execute("INSERT INTO platform_payments(organization_id,package,months,amount,discount_code,source,request_id,approved_by,created_at) SELECT r.organization_id,r.requested_package,r.paid_months,r.quoted_price,r.discount_code,'transfer',r.id,'',COALESCE(r.processed_at,r.created_at) FROM subscription_requests r WHERE r.status='approved' AND EXISTS (SELECT 1 FROM organizations o WHERE o.id=r.organization_id) ON CONFLICT(request_id) DO NOTHING")
 c.execute("UPDATE platform_credit_ledger SET reason=actor,actor=reason WHERE (reason='المالك' OR reason IN (SELECT name FROM platform_admins)) AND actor<>'المالك' AND actor NOT IN (SELECT name FROM platform_admins)")
 for key,package in [('free','free'),('basic','basic'),('vip','vip')]: c.execute('INSERT INTO readiness_test_accounts(account_key,package,active,created_at) VALUES(?,?,1,?) ON CONFLICT(account_key) DO UPDATE SET package=excluded.package,active=1',(f'__readiness_{key}__',package,stamp()))

# صور الإعلانات: قوائم لوحة الإدارة ترسل مرجعًا قصيرًا بدل الصورة كاملة (images=ref)،
# والمتصفح يجلب كل صورة مرة واحدة برابط ثابت يحفظه عنده. المرجع بصمة المحتوى نفسه.
AD_IMAGE_PREFIX='khdoom-image:'
_AD_IMAGES={}
_AD_IMAGES_LIMIT=400
def ad_image_ref(value):
 if not isinstance(value,str) or len(value)<2048 or not value.startswith('data:image/'): return value
 digest=hashlib.sha256(value.encode()).hexdigest()[:40]
 _AD_IMAGES.pop(digest,None); _AD_IMAGES[digest]=value
 while len(_AD_IMAGES)>_AD_IMAGES_LIMIT: _AD_IMAGES.pop(next(iter(_AD_IMAGES)))
 return AD_IMAGE_PREFIX+digest
def ad_images_as_refs(row):
 row=dict(row)
 if 'image_data' in row: row['image_data']=ad_image_ref(row['image_data'])
 raw=row.get('banner_config'); config=raw
 if isinstance(raw,str) and ('ImageData' in raw or 'image_data' in raw):
  try: config=json.loads(raw)
  except ValueError: config=raw
 if isinstance(config,dict) and any(key in config for key in ('bannerImageData','banner_image_data')):
  config={**config,**{key:ad_image_ref(config[key]) for key in ('bannerImageData','banner_image_data') if key in config}}
  row['banner_config']=json.dumps(config,ensure_ascii=False) if isinstance(raw,str) else config
 return row
def ad_image_lookup(digest,s):
 if not re.fullmatch(r'[0-9a-f]{40}',digest or ''): return None
 if digest in _AD_IMAGES: return _AD_IMAGES[digest]
 # بعد إعادة تشغيل الخادم تكون الذاكرة فارغة: نبحث في الجدولين مرة ونعيد تسجيل الصور.
 with s.db() as c:
  for table in ('advertisements','platform_advertisements'):
   if not table_exists(c,table,s): continue
   for row in c.execute('SELECT image_data,banner_config FROM '+table).fetchall(): ad_images_as_refs(row)
 return _AD_IMAGES.get(digest)
def permission(path,method):
 p=path.removeprefix('/owner/api/')
 if p.startswith('v2/'):
  p=p[3:]
  if p in ('me','me/password','logout','summary','live-changes'): return None
  # حالة الخدمات يراها أي مدير مسجل؛ طلب فحص جديد ومركز الأمان لصلاحية الأمن.
  if p=='service-health': return None if method=='GET' else 'security'
  if p.startswith(('service-health/','security-center')): return 'security'
  if p=='technical-ai/state' or p.startswith('me/2fa'): return None
  if p.startswith('finance'): return 'finance'
  if p.startswith('export/'): return {'organizations':'organizations.view','payments':'finance','expenses':'finance','audit':'security'}.get(p[7:],'admins')
  if p=='addons': return 'packages'
  if p.startswith('community/rewards/'): return 'rewards'
  if p.startswith('addon-offers'): return 'offers'
  if p.startswith(('accounts','branches','organization-verifications')): return 'organizations.view' if method=='GET' else 'organizations.edit'
  if p.startswith('directory'): return 'organizations.view' if method=='GET' else 'organizations.edit'
  if p.startswith(('credits','service-costs')): return 'usage'
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
 # استعلام واحد بدل اثنين: هذه الدالة تُستدعى مع كل طلب من التطبيق.
 r=c.execute('SELECT o.archived_at,COALESCE(z.suspended,0) suspended FROM organizations o LEFT JOIN platform_org_state z ON z.organization_id=o.id WHERE o.id=?',(org,)).fetchone()
 if r is None:
  r=c.execute('SELECT suspended FROM platform_org_state WHERE organization_id=?',(org,)).fetchone()
  return bool(r and r['suspended'])
 return bool(r['archived_at'] is not None or r['suspended'])
def paged(c,select,where,args,order,page):
 return {'items':rows(c,select+' '+where+' ORDER BY '+order+' LIMIT 30 OFFSET ?',[*args,(page-1)*30]),'total':scalar(c,'SELECT COUNT(*) n '+where,args),'page':page,'pageSize':30}
_KNOWN_TABLES=set()
def table_exists(c,name,s):
 if s.DATABASE_URL:
  # الجداول لا تُحذف أثناء التشغيل؛ نحفظ الإجابة الموجبة بدل سؤال قاعدة البيانات في كل طلب.
  if name in _KNOWN_TABLES: return True
  found=c.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?',(name,)).fetchone() is not None
  if found: _KNOWN_TABLES.add(name)
  return found
 return c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
def ensure_owner_tables(c,s,required):
 if all(table_exists(c,name,s) for name in required): return
 if s.DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(735421)')
 migrate(c,postgres=bool(s.DATABASE_URL))
SERVICE_LABELS={'whatsapp':'واتساب','ai':'الذكاء الاصطناعي','calls':'المكالمات'}
SERVICE_COSTS={'whatsapp':0.01,'ai':0.02,'calls':0.05}
def _chunks(values,size=400):
 values=list(values)
 for start in range(0,len(values),size): yield values[start:start+size]

USD_TO_SAR=3.75
def normalize_month(month):
 """YYYY-MM بأرقام لاتينية وسنة معقولة؛ يُخزَّن بهذه الصيغة ليصح الترتيب."""
 text=str(month or '').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789'))
 if not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])',text) or not 2020<=int(text[:4])<=2100: raise ValueError('اختر الشهر بصيغة سنة-شهر')
 return text
def month_bounds(month):
 """بداية الشهر الميلادي وبداية الشهر التالي (UTC) من نص YYYY-MM."""
 month=normalize_month(month)
 start=datetime(int(month[:4]),int(month[5:7]),1,tzinfo=timezone.utc)
 end=(start+timedelta(days=32)).replace(day=1)
 return start,end
def platform_month_usage(c,s,month):
 """استخدام كل المؤسسات في شهر ميلادي، بنفس تعريف الوحدات في أرصدة الخدمات."""
 start,end=month_bounds(month); a,b=start.isoformat(),end.isoformat()
 out={'ai':scalar(c,'SELECT COUNT(*) n FROM ai_usage WHERE created_at>=? AND created_at<?',(a,b)),'whatsapp':0,'calls':0}
 if table_exists(c,'whatsapp_messages',s): out['whatsapp']=scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages WHERE direction=? AND state<>? AND timestamp>=? AND timestamp<?',('outbound','failed',int(start.timestamp()),int(end.timestamp())))
 if table_exists(c,'call_logs',s):
  # الدقائق تُقرَّب لكل مؤسسة كما في جدول الأرصدة، ثم تُجمع.
  out['calls']=sum(math.ceil(int(r['n'] or 0)/60) for r in rows(c,'SELECT organization_id,COALESCE(SUM(CASE WHEN duration_seconds>0 THEN duration_seconds ELSE 0 END),0) n FROM call_logs WHERE created_at>=? AND created_at<? GROUP BY organization_id',(a,b)))
 return {k:int(v or 0) for k,v in out.items()}
def service_unit_costs(c,s):
 """تكلفة الوحدة المعتمدة لكل خدمة: من أحدث فاتورة مدخلة، وإلا رقم تقديري."""
 out={k:{'service':k,'label':SERVICE_LABELS[k],'unit_cost':SERVICE_COSTS[k],'source':'estimate','month':None,'amount_sar':None,'units':None} for k in SERVICE_LABELS}
 if not table_exists(c,'platform_service_invoices',s): return out
 for x in rows(c,'SELECT month,service,amount_sar,units,unit_cost FROM platform_service_invoices ORDER BY month'):
  if x['service'] in out: out[x['service']].update({'unit_cost':float(x['unit_cost']),'source':'invoice','month':x['month'],'amount_sar':float(x['amount_sar']),'units':int(x['units'])})
 return out
def credits_bulk(c,s,org_ids=None,with_ledger=True):
 """أرصدة واستهلاك عدة مؤسسات بعدد ثابت من الاستعلامات بدل استعلامات لكل مؤسسة.

 org_ids=None تعني كل المؤسسات. النتيجة: {organization_id: نفس شكل credits_summary}.
 """
 if org_ids is not None:
  org_ids=[int(x) for x in org_ids]
  if not org_ids: return {}
 month=stamp()[:7]+'-01'; today=stamp()[:10]
 month_ts=int(datetime.fromisoformat(month).replace(tzinfo=timezone.utc).timestamp()); today_ts=int(datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp())
 def grouped(sql,args=(),column='organization_id'):
  # ينفذ الاستعلام مرة لكل المؤسسات، أو على دفعات عند تحديد قائمة.
  if org_ids is None: return rows(c,sql.replace('{scope}','1=1'),args)
  out=[]
  for chunk in _chunks(org_ids): out.extend(rows(c,sql.replace('{scope}',column+' IN ('+','.join('?' for _ in chunk)+')'),[*args,*chunk]))
  return out
 ids=org_ids if org_ids is not None else [r['id'] for r in rows(c,'SELECT id FROM organizations')]
 packages={r['organization_id']:r for r in grouped("SELECT s.organization_id,COALESCE(s.package,'free') package,COALESCE(p.monthly,0) monthly,s.starts_at,p.ai_daily,p.whatsapp_units,p.calls_units FROM subscriptions s LEFT JOIN platform_packages p ON p.package=s.package WHERE {scope}",(),'s.organization_id')}
 adjustments={}; ledgers={}
 for x in grouped('SELECT organization_id,service,units,reason,actor,created_at FROM platform_credit_ledger WHERE created_at>=? AND {scope} ORDER BY id DESC',(month,)):
  org=x['organization_id']; totals=adjustments.setdefault(org,{k:0 for k in SERVICE_LABELS})
  if x['service'] in totals: totals[x['service']]+=int(x['units'] or 0)
  if with_ledger and len(ledgers.setdefault(org,[]))<100: ledgers[org].append({k:x[k] for k in ('service','units','reason','actor','created_at')})
 ai={r['organization_id']:r for r in grouped('SELECT organization_id,COUNT(*) month_count,COALESCE(SUM(CASE WHEN created_at>=? THEN 1 ELSE 0 END),0) today_count FROM ai_usage WHERE created_at>=? AND {scope} GROUP BY organization_id',(today,month))}
 wa={}
 if table_exists(c,'whatsapp_messages',s):
  wa={r['organization_id']:r for r in grouped('SELECT organization_id,COUNT(*) month_count,COALESCE(SUM(CASE WHEN timestamp>=? THEN 1 ELSE 0 END),0) today_count FROM whatsapp_messages WHERE direction=? AND state<>? AND timestamp>=? AND {scope} GROUP BY organization_id',(today_ts,'outbound','failed',month_ts))}
 calls={}
 if table_exists(c,'call_logs',s):
  calls={r['organization_id']:r for r in grouped('SELECT organization_id,COALESCE(SUM(CASE WHEN duration_seconds>0 THEN duration_seconds ELSE 0 END),0) month_seconds,COALESCE(SUM(CASE WHEN created_at>=? AND duration_seconds>0 THEN duration_seconds ELSE 0 END),0) today_seconds FROM call_logs WHERE created_at>=? AND {scope} GROUP BY organization_id',(today,month))}
 links={}
 if table_exists(c,'call_connections',s):
  links={r['organization_id']:r for r in grouped('SELECT organization_id,status,phone_number,last_error FROM call_connections WHERE {scope}')}
 quotas=service_quota.snapshot_bulk(c,org_ids)
 unit_costs={k:v['unit_cost'] for k,v in service_unit_costs(c,s).items()}
 # دخل الشهر الفعلي: آخر اشتراك معتمد لنفس الباقة ÷ عدد شهوره (مع الشهور المجانية)، وإلا سعر الباقة الشهري.
 # طلب أقدم من بداية الاشتراك الحالي (تفعيل بكود أو تجديد لاحق) لا يُعتمد.
 paid={}
 for x in grouped("SELECT organization_id,requested_package,paid_months,bonus_months,quoted_price,processed_at FROM subscription_requests WHERE status='approved' AND {scope} ORDER BY id"):
  paid[x['organization_id']]=x
 result={}
 for org in ids:
  package=packages.get(org) or {'package':'free','monthly':0,'ai_daily':5,'whatsapp_units':0,'calls_units':0}
  adjust=adjustments.get(org,{k:0 for k in SERVICE_LABELS}); a_row=ai.get(org,{}); w_row=wa.get(org,{}); c_row=calls.get(org,{}); link=links.get(org)
  usage={'ai':int(a_row.get('month_count') or 0),'whatsapp':int(w_row.get('month_count') or 0),'calls':math.ceil(int(c_row.get('month_seconds') or 0)/60)}
  daily={'ai':int(a_row.get('today_count') or 0),'whatsapp':int(w_row.get('today_count') or 0),'calls':math.ceil(int(c_row.get('today_seconds') or 0)/60)}
  quota=quotas.get(org) or {'services':{},'renews_at':None,'cycle_start':None}
  q={x:quota['services'].get(x) or {'base':None,'adjustments':0,'limit':None,'used':0,'used_today':0,'daily_limit':None,'remaining':None,'unlimited':True,'blocked':False,'reason':''} for x in SERVICE_LABELS}
  # الحد والمستخدم والمتبقي من دورة اشتراك المؤسسة (نفس ما يُطبَّق فعليًا)؛ التكلفة والاستهلاك الشهري بالشهر الميلادي.
  base={x:int(q[x]['base'] or 0) for x in SERVICE_LABELS}; adjust={x:int(q[x]['adjustments'] or 0) for x in SERVICE_LABELS}
  remaining={x:int(q[x]['remaining'] or 0) for x in SERVICE_LABELS}; cost={x:round(usage[x]*unit_costs[x],2) for x in SERVICE_LABELS}; total_cost=round(sum(cost.values()),2); monthly=float(package.get('monthly') or 0); income_source='package'
  request=paid.get(org); months=(int(request['paid_months'] or 0)+int(request['bonus_months'] or 0)) if request else 0
  if (package.get('package') or 'free')=='free': monthly=0.0; income_source='free'
  elif request and request['requested_package']==package.get('package') and float(request['quoted_price'] or 0)>0 and months>0 and str(request['processed_at'] or '')[:10]>=str(package.get('starts_at') or '')[:10]: monthly=round(float(request['quoted_price'])/months,2); income_source='paid'
  result[org]={'package':package.get('package') or 'free','subscription_value':monthly,'income_source':income_source,'cycle_start':quota['cycle_start'],'renews_at':quota['renews_at'],'services':{x:{'label':SERVICE_LABELS[x],'base':base[x],'adjustments':adjust[x],'limit':q[x]['limit'],'unlimited':q[x]['unlimited'],'used':q[x]['used'],'daily_limit':q[x]['daily_limit'],'blocked':q[x]['blocked'],'reason':q[x]['reason'],'used_month':usage[x],'used_today':daily[x],'remaining':remaining[x],'cost':cost[x]} for x in SERVICE_LABELS},'calls_status':link['status'] if link else 'not_connected','calls_phone':link['phone_number'] if link else None,'calls_error':link['last_error'] if link else '','total_remaining':sum(remaining.values()),'usage_month':sum(usage.values()),'usage_today':sum(daily.values()),'actual_cost':total_cost,'estimated_profit':round(monthly-total_cost,2),'ledger':ledgers.get(org,[])}
 return result

def credits_summary(c,org,s):
 return credits_bulk(c,s,[org])[int(org)]

def customer_usage_summary(c,org,s):
 """Safe, read-only balance view for an authenticated organization user."""
 summary=credits_summary(c,org,s)
 services={}
 total_limit=0
 total_used=0
 for key,item in summary['services'].items():
  # الأرقام من دورة اشتراك المؤسسة، وهي نفسها التي يُوقَف عندها الاستخدام.
  limit=max(0,int(item['limit'] or 0))
  used=max(0,int(item['used'] or 0))
  remaining=max(0,int(item['remaining'] or 0))
  total_limit+=limit
  total_used+=used if not item['unlimited'] else 0
  services[key]={
   'label':item['label'], 'limit':limit, 'usedMonth':used,
   'usedToday':max(0,int(item['used_today'] or 0)), 'remaining':remaining,
   'usagePercent':min(100,round(used*100/limit)) if limit else 0,
   'unlimited':bool(item['unlimited']), 'exhausted':bool(item['blocked']), 'reason':item['reason'],
   'dailyLimit':item['daily_limit'],
  }
 usage_month=max(0,total_used)
 return {
  'package':summary['package'],
  'renewalDate':summary['renews_at'],
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
 actual=scalar(c,'SELECT COALESCE(SUM(amount),0) n FROM platform_payments WHERE created_at>=?',(month,))
 return {'items':rows_data,'monthExpenses':expenses,'paid':paid,'remaining':round(expenses-paid,2),'overdue':overdue,'upcoming':upcoming,'subscriptionIncome':round(float(income or 0),2),'actualIncome':round(float(actual or 0),2),'net':round(float(income or 0)-expenses,2),'actualNet':round(float(actual or 0)-expenses,2)}
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

def apply_reward(c,ident,kind,amount,reason,actor_name):
 """تطبيق مكافأة على مؤسسة دون إتلاف اشتراك مدفوع قائم. تُستخدم من ملف المؤسسة ومن مكافآت المجتمع."""
 if not reason or kind not in ('days','month','vip','ai'): raise ValueError('اختر المكافأة واكتب سببها')
 if kind=='ai': c.execute('INSERT INTO platform_daily_credits VALUES(?,?,?) ON CONFLICT(organization_id,day) DO UPDATE SET units=platform_daily_credits.units+excluded.units',(ident,stamp()[:10],amount))
 else:
  sub=c.execute('SELECT * FROM subscriptions WHERE organization_id=?',(ident,)).fetchone(); now_iso=stamp()
  held=sub['package'] if sub else 'free'; held_until=sub['expires_at'] if sub else None
  paid=held in ('basic','vip') and (held_until is None or held_until>now_iso)
  if paid and held_until is None and (kind!='vip' or held=='vip'): raise ValueError('اشتراك المؤسسة مفتوح بدون تاريخ انتهاء؛ لا يحتاج تمديدًا')
  if kind=='vip' and held!='vip':
   # هدية VIP مؤقتة فوق اشتراك مدفوع: نحفظ الاشتراك الأصلي ليرجع بعد انتهاء الهدية.
   if paid: c.execute('INSERT INTO subscription_fallbacks(organization_id,package,expires_at,created_at) VALUES(?,?,?,?) ON CONFLICT(organization_id) DO NOTHING',(ident,held,held_until,now_iso))
   pkg='vip'; base=now_iso
  else:
   pkg=held if paid else 'basic'; base=max(now_iso,held_until) if paid else now_iso
  expiry=(datetime.fromisoformat(base)+timedelta(days=30 if kind=='month' else amount)).isoformat()
  c.execute('INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?) ON CONFLICT(organization_id) DO UPDATE SET package=excluded.package,expires_at=excluded.expires_at',(ident,pkg,now_iso,expiry))
 c.execute('INSERT INTO platform_rewards(organization_id,kind,amount,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(ident,kind,amount,reason[:500],actor_name,stamp()))

def ask_technical_agent(d,page,actor,h,s):
 """محادثة الموظف التقني دون حجز اتصال قاعدة البيانات أثناء انتظار مزود الذكاء.

 كل أداة تفتح اتصالًا قصيرًا وتغلقه؛ نداءات المزود (قد تأخذ ثوانيَ) تجري بلا اتصال مفتوح.
 تعيد None عند تعذر الذكاء ليكمل المسار بالفحص المبني على القواعد.
 """
 import technical_agent, ai_core
 question=str(d.get('question','')).strip()[:1000]
 if len(question)<4: raise ValueError('اكتب وصف المشكلة أولًا')
 if not technical_agent.ai_configured(): return None
 def run(route,method,data):
  with s.db() as c:
   out=dispatch(c,route,method,data,{},page,actor,h,s); c.commit(); return out
 def query(_,sql,args=()):
  with s.db() as c: return rows(c,sql,args)
 try: result=technical_agent.answer(None,question,d.get('history') if isinstance(d.get('history'),list) else [],run,query,actor['name'])
 except ai_core.AIServiceError as error:
  print('TECHNICAL AI FALLBACK:',error.message); return None
 with s.db() as c:
  audit(c,actor['name'],'technical_ai_chat',question[:200]); c.commit()
 return result

def handle(h,method,s):
 path=urlparse(h.path).path.rstrip('/')
 if path=='/owner' and method=='GET': h._send_html((s.ROOT/'owner_dashboard.html').read_text(encoding='utf-8').replace('20260924-ad-image-compress-fix','20260926-ad-request-image-small')); return True
 if path=='/owner/dashboard.js' and method=='GET': h._send_javascript((s.ROOT/'owner_dashboard.js').read_text(encoding='utf-8')); return True
 if path=='/owner/addons.js' and method=='GET': h._send_javascript((s.ROOT/'owner_addons.js').read_text(encoding='utf-8')); return True
 if not path.startswith('/owner/api/v2/'): return False
 route=path[len('/owner/api/v2/'):]
 try:
  if route=='login' and method=='POST':
   d=h._body(); username=str(d.get('username','')).strip().lower(); ip=client_ip(h)
   with s.db() as c:
    since=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
    if scalar(c,'SELECT COUNT(*) n FROM platform_login_events WHERE ip=? AND success=0 AND created_at>?',(ip,since))>=10 or scalar(c,'SELECT COUNT(*) n FROM platform_login_events WHERE account=? AND success=0 AND created_at>?',(username[:100],since))>=20: raise s.ApiError(429,'محاولات كثيرة؛ أعد المحاولة بعد 15 دقيقة')
    user=c.execute('SELECT * FROM platform_admins WHERE username=? AND active=1',(username,)).fetchone()
    valid=bool(user and s.verify_password(str(d.get('password','')),user['password_hash'],user['password_salt']))
    needs_code=False
    if valid and user['totp_enabled']:
     supplied=str(d.get('code','')).strip()
     if not supplied: needs_code=True; valid=False
     else:
      step=totp_match(user['totp_secret'],supplied)
      # الرمز يُقبل مرة واحدة فقط: إعادة استخدام نفس الفترة تُرفض.
      if step is None or step<=int(user['totp_last_step'] or 0): valid=False
      else: c.execute('UPDATE platform_admins SET totp_last_step=? WHERE id=?',(step,user['id']))
    if not needs_code: c.execute('INSERT INTO platform_login_events(account,ip,success,created_at) VALUES(?,?,?,?)',(username[:100],ip,int(valid),stamp()))
    if valid:
     token=secrets.token_urlsafe(32); c.execute('INSERT INTO platform_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],(datetime.now(timezone.utc)+timedelta(hours=8)).isoformat()))
    c.commit()
   if needs_code: h._send(401,{'error':'أدخل رمز التحقق من تطبيق المصادقة','needsCode':True}); return True
   if not valid: raise s.ApiError(401,'بيانات الدخول غير صحيحة')
   h._send(200,{'token':token}); return True
  actor=authorize(h,s)
  if route=='me' and method=='GET': h._send(200,actor); return True
  if route.startswith('ads/image/') and method=='GET':
   value=ad_image_lookup(route[len('ads/image/'):],s)
   if not value: raise s.ApiError(404,'صورة الإعلان غير موجودة')
   body=value.encode(); h._last_status=200; h.send_response(200)
   h.send_header('Content-Type','text/plain; charset=utf-8'); h.send_header('Content-Length',str(len(body)))
   # المرجع بصمة المحتوى، فالصورة لا تتغير أبدًا تحت نفس الرابط؛ يحفظها متصفح المدير فقط.
   h.send_header('Cache-Control','private, max-age=31536000, immutable'); h.send_header('X-Content-Type-Options','nosniff')
   h.end_headers(); h.wfile.write(body); return True
  if route.startswith('me/2fa'):
   if actor.get('id') is None: raise ValueError('حساب المالك يدخل بالمفتاح الرئيسي؛ التحقق بخطوتين لحسابات الموظفين')
   d=h._body() if method=='POST' else {}
   with s.db() as c:
    row=c.execute('SELECT username,password_hash,password_salt,totp_secret,totp_enabled FROM platform_admins WHERE id=? AND active=1',(actor['id'],)).fetchone()
    if not row: raise s.ApiError(401,'يلزم تسجيل الدخول بحساب إداري')
    if route=='me/2fa' and method=='GET': result={'enabled':bool(row['totp_enabled'])}
    elif route=='me/2fa/setup' and method=='POST':
     if row['totp_enabled']: raise ValueError('التحقق بخطوتين مفعّل؛ عطّله أولًا لتغيير المفتاح')
     secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
     c.execute('UPDATE platform_admins SET totp_secret=?,totp_enabled=0,totp_last_step=0 WHERE id=?',(secret,actor['id']))
     result={'secret':secret,'account':row['username'],'issuer':'Khdoom','uri':'otpauth://totp/Khdoom:'+row['username']+'?secret='+secret+'&issuer=Khdoom&digits=6&period=30'}
    elif route=='me/2fa/enable' and method=='POST':
     if row['totp_enabled']: raise ValueError('التحقق بخطوتين مفعّل بالفعل')
     step=totp_match(row['totp_secret'],d.get('code'))
     if step is None: raise ValueError('الرمز غير صحيح. تأكد من المفتاح ومن ساعة الجوال ثم جرّب الرمز الجديد')
     c.execute('UPDATE platform_admins SET totp_enabled=1,totp_last_step=? WHERE id=?',(step,actor['id']))
     audit(c,actor['name'],'two_factor_enabled','admins/'+str(actor['id'])); result={'enabled':True}
    elif route=='me/2fa/disable' and method=='POST':
     if not s.verify_password(str(d.get('current','')),row['password_hash'],row['password_salt']): raise s.ApiError(403,'كلمة المرور الحالية غير صحيحة')
     if row['totp_enabled'] and totp_match(row['totp_secret'],d.get('code')) is None: raise s.ApiError(403,'رمز التحقق غير صحيح')
     c.execute("UPDATE platform_admins SET totp_secret='',totp_enabled=0,totp_last_step=0 WHERE id=?",(actor['id'],))
     audit(c,actor['name'],'two_factor_disabled','admins/'+str(actor['id'])); result={'enabled':False}
    else: raise s.ApiError(404,'المسار غير موجود')
    c.commit()
   h._send(200,result); return True
  if route=='me/password' and method=='POST':
   d=h._body(); current=str(d.get('current','')); fresh=str(d.get('password',''))
   if actor.get('id') is None: raise ValueError('حساب المالك يدخل بالمفتاح الرئيسي؛ لا توجد له كلمة مرور هنا')
   if len(fresh)<12: raise ValueError('كلمة المرور الإدارية 12 حرفًا على الأقل')
   if fresh==current: raise ValueError('اختر كلمة مرور مختلفة عن الحالية')
   with s.db() as c:
    row=c.execute('SELECT password_hash,password_salt FROM platform_admins WHERE id=? AND active=1',(actor['id'],)).fetchone()
    if not row or not s.verify_password(current,row['password_hash'],row['password_salt']): raise s.ApiError(403,'كلمة المرور الحالية غير صحيحة')
    hashed,salt=s.hash_password(fresh)
    c.execute('UPDATE platform_admins SET password_hash=?,password_salt=? WHERE id=?',(hashed,salt,actor['id']))
    # تنتهي كل الجلسات الأخرى؛ تبقى الجلسة الحالية فقط.
    c.execute('DELETE FROM platform_sessions WHERE admin_id=? AND token_hash<>?',(actor['id'],hashlib.sha256(h.headers.get('X-Admin-Session','').encode()).hexdigest()))
    audit(c,actor['name'],'password_reset','admins/'+str(actor['id'])+' (self)'); c.commit()
   h._send(200,{'saved':True}); return True
  q={k:v[0] for k,v in parse_qs(urlparse(h.path).query).items()}; page=max(1,min(int(q.get('page',1)),100000)); d=h._body() if method in ('POST','PUT') else {}
  if route=='technical-ai/ask' and method=='POST' and not d.get('_rule'):
   answered=ask_technical_agent(d,page,actor,h,s)
   if answered is not None: h._send(200,answered); return True
   d={**d,'_rule':True}
  with s.db() as c:
   result=dispatch(c,route,method,d,q,page,actor,h,s)
   if method in ('POST','PUT','DELETE') and route!='logout': audit(c,actor['name'],method,route)
   c.commit()
  h._send(200,result); return True
 except (ValueError,TypeError,KeyError) as e: raise s.ApiError(400,str(e))

def dispatch(c,r,m,d,q,page,a,h,s):
 if r.startswith('directory/'):
  import identity_directory
  return identity_directory.handle(c,r,m,d,q,page,a,__import__(__name__),s)
 if r=='live-changes' and m=='GET':
  scope=q.get('scope','')
  if scope=='home':
   return {'support':scalar(c,"SELECT COUNT(*) n FROM support_tickets WHERE status IN ('open','under_review','in_progress','awaiting_user')") if 'support' in a['permissions'] else None,
           'ads':scalar(c,'SELECT COUNT(*) n FROM advertisements WHERE active=1 AND approved=1 AND (scheduled_at IS NULL OR scheduled_at<=?) AND (expires_at IS NULL OR expires_at>?)',(stamp(),stamp())) if 'ads' in a['permissions'] else None}
  if scope=='support' and 'support' in a['permissions']:
   where='FROM support_tickets t WHERE 1=1'; args=[]
   if q.get('status'): where+=' AND t.status=?'; args.append(q['status'])
   items=rows(c,"SELECT t.id,t.status,t.updated_at,(SELECT tt.status FROM technical_tasks tt WHERE tt.support_ticket_id=t.id OR (tt.support_ticket_id IS NULL AND tt.service='support' AND tt.problem LIKE ('طلب دعم #' || t.id || ':%')) ORDER BY tt.id DESC LIMIT 1) technical_status "+where+' ORDER BY t.id DESC LIMIT 30 OFFSET ?',[*args,(page-1)*30])
   return {'signature':items,'total':scalar(c,'SELECT COUNT(*) n '+where,args)}
  if scope=='ads' and 'ads' in a['permissions']:
   condition="CASE WHEN approved=1 AND expires_at IS NOT NULL AND expires_at<=? THEN 'expired' WHEN active=0 AND approved=0 THEN 'rejected' WHEN active=0 THEN 'stopped' WHEN approved=0 THEN 'pending' WHEN scheduled_at>? THEN 'scheduled' ELSE 'published' END"
   src='FROM (SELECT id,active,approved,expires_at,scheduled_at,published_at,review_note,'+condition+' status FROM advertisements WHERE COALESCE(deleted,0)=0) ads WHERE 1=1'; args=[stamp(),stamp()]
   if q.get('status'): src+=' AND status=?'; args.append(q['status'])
   items=rows(c,'SELECT id,active,approved,expires_at,scheduled_at,published_at,review_note,status '+src+' ORDER BY id DESC LIMIT 30 OFFSET ?',[*args,(page-1)*30])
   return {'signature':items,'total':scalar(c,'SELECT COUNT(*) n '+src,args)}
  raise s.ApiError(403,'لا تملك صلاحية عرض التحديثات')
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
   out['organizations']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE archived_at IS NULL'); out['activeSubscribers']=scalar(c,"SELECT COUNT(*) n FROM organizations o JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_org_state z ON z.organization_id=o.id WHERE o.archived_at IS NULL AND COALESCE(z.suspended,0)=0 AND s.package IN ('basic','vip') AND (s.expires_at IS NULL OR s.expires_at>?)",(stamp(),)); out['expiringList']=rows(c,"SELECT o.id,o.name,o.phone,s.package,s.expires_at FROM subscriptions s JOIN organizations o ON o.id=s.organization_id WHERE o.archived_at IS NULL AND s.package IN ('basic','vip') AND s.expires_at>? AND s.expires_at<=? ORDER BY s.expires_at LIMIT 25",(stamp(),(datetime.now(timezone.utc)+timedelta(days=14)).isoformat())); out['expiringSubscriptions']=scalar(c,"SELECT COUNT(*) n FROM subscriptions WHERE expires_at>? AND expires_at<=?",(stamp(),(datetime.now(timezone.utc)+timedelta(days=14)).isoformat())); out['packages']=rows(c,"SELECT COALESCE(s.package,'free') package,COUNT(*) total FROM organizations o LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE o.archived_at IS NULL GROUP BY s.package")
   out['newToday']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE archived_at IS NULL AND created_at>=?',(today,)); out['newMonth']=scalar(c,'SELECT COUNT(*) n FROM organizations WHERE archived_at IS NULL AND created_at>=?',(month,))
   out['problemOrganizations']=scalar(c,"SELECT COUNT(DISTINCT o.id) n FROM organizations o LEFT JOIN platform_org_state z ON z.organization_id=o.id LEFT JOIN login_failures f ON f.organization_id=o.id AND f.created_at>=? WHERE COALESCE(z.suspended,0)=1 OR f.id IS NOT NULL",((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),)) if table_exists(c,'login_failures',s) else scalar(c,'SELECT COUNT(*) n FROM organizations o JOIN platform_org_state z ON z.organization_id=o.id WHERE z.suspended=1')
   out['recentActivity']=rows(c,"SELECT o.name organization_name,a.action,a.summary,a.created_at FROM audit_logs a JOIN organizations o ON o.id=a.organization_id ORDER BY a.id DESC LIMIT 8") if table_exists(c,'audit_logs',s) else []
   out['serviceStatus']={'server':'ready','database':'ready','ai':'ready' if os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip() else 'warning','whatsapp':'ready' if table_exists(c,'whatsapp_connections',s) and scalar(c,'SELECT COUNT(*) n FROM whatsapp_connections') else 'warning','calls':'ready' if table_exists(c,'call_connections',s) and scalar(c,"SELECT COUNT(*) n FROM call_connections WHERE status='ready'") else 'warning'}
  if 'usage' in p:
   out['ai']=scalar(c,'SELECT COUNT(*) n FROM ai_usage'); out['calls']=0; out['callFailures']=0
   if table_exists(c,'call_logs',s):
    seconds=c.execute('SELECT COALESCE(SUM(duration_seconds),0) n FROM call_logs').fetchone()['n'] or 0; out['calls']=math.ceil(int(seconds)/60); out['callFailures']=scalar(c,"SELECT COUNT(*) n FROM call_logs WHERE status IN ('failed','no_answer','busy')")
   out['whatsapp']=scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages') if table_exists(c,'whatsapp_messages',s) else None
   out['lowBalanceOrganizations']=0
   if 'organizations.view' in p:
    for credits in credits_bulk(c,s,None,with_ledger=False).values():
     if any(v['base']>0 and v['remaining']<=max(1,math.ceil(v['base']*.1)) for v in credits['services'].values()): out['lowBalanceOrganizations']+=1
  if 'support' in p: out['support']=scalar(c,"SELECT COUNT(*) n FROM support_tickets WHERE status IN ('open','under_review','in_progress','awaiting_user')")
  if 'support' in p and table_exists(c,'technical_tasks',s) and table_exists(c,'support_tickets',s):
   # ما يحتاج المدير أن يعرفه من الموظف التقني: شكاوى جديدة عليه، أو معروفة تنتظر قرار الإدارة.
   out['technicalAttention']=rows(c,"SELECT t.id ticket_id,t.reference_code,t.title,t.category,o.name organization_name,k.knowledge,k.problem_type,k.diagnosis,k.proposal,t.created_at FROM technical_tasks k JOIN support_tickets t ON t.id=k.support_ticket_id JOIN organizations o ON o.id=t.organization_id WHERE k.needs_owner=1 AND k.knowledge IN ('novel','learned','known') AND t.status NOT IN ('resolved','closed') AND k.id=(SELECT MAX(k2.id) FROM technical_tasks k2 WHERE k2.support_ticket_id=t.id) ORDER BY CASE k.knowledge WHEN 'novel' THEN 0 WHEN 'learned' THEN 1 ELSE 2 END,t.id DESC LIMIT 20")
  if 'ads' in p: out['ads']=scalar(c,'SELECT COUNT(*) n FROM advertisements WHERE active=1 AND approved=1 AND (scheduled_at IS NULL OR scheduled_at<=?) AND (expires_at IS NULL OR expires_at>?)',(stamp(),stamp()))
  if 'security' in p:
   out['logins']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action IN ('login','failed_login','new_device') AND created_at>=?",(today,)); out['alerts']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action IN ('failed_login','blocked_device_login','new_device','owner_account_status') AND created_at>=?",(today,)); out['passwordResets']=scalar(c,"SELECT COUNT(*) n FROM audit_logs WHERE action='password_reset' AND created_at>=?",(today,))+scalar(c,"SELECT COUNT(*) n FROM platform_audit WHERE action='password_reset' AND created_at>=?",(today,))
   out['logins']+=scalar(c,'SELECT COUNT(*) n FROM platform_unknown_logins WHERE created_at>=?',(today,))
  return out
 if r=='service-health' and m=='GET':
  # الحالة تأتي من المراقب الفعلي (فحص كل بضع دقائق)، لا من قيم ثابتة.
  names={'openai':'ai','push':'notifications'}; status_map={'ok':'ready','warning':'warning','error':'error','unknown':'unknown','unconfigured':'not_configured'}
  services=[]; monitored=True
  try:
   import service_monitor
   # start() لا يكرر التشغيل؛ يضمن فقط أن المراقب مهيأ قبل قراءة حالته.
   service_monitor.start(s.db,s.DB_PATH,bool(s.DATABASE_URL),getattr(s,'PORT',None))
   snap=service_monitor.snapshot()
  except Exception:
   snap={'services':[]}; monitored=False
  for x in snap['services']:
   key=names.get(x['service'],x['service'])
   if key in ('whatsapp','calls'): continue
   checked=datetime.fromtimestamp(x['checked'],timezone.utc).isoformat() if x.get('checked') else None
   services.append({'service':key,'label':x.get('name') or key,'status':status_map.get(x['status'],'unknown'),'affected':0,'lastError':'' if x['status']=='ok' else x.get('detail',''),'detail':x.get('detail',''),'checkedAt':checked,'requests24h':x.get('requests24h') or 0,'failures24h':x.get('failures24h') or 0})
  whatsapp_count=scalar(c,'SELECT COUNT(*) n FROM whatsapp_connections') if table_exists(c,'whatsapp_connections',s) else 0
  whatsapp_recent=scalar(c,"SELECT COUNT(*) n FROM whatsapp_webhooks WHERE received_at>=?",(int((datetime.now(timezone.utc)-timedelta(hours=24)).timestamp()),)) if table_exists(c,'whatsapp_webhooks',s) else 0
  services.append({'service':'whatsapp','label':'واتساب','status':'not_connected' if not whatsapp_count else 'ready' if whatsapp_recent else 'warning','affected':whatsapp_count,'lastError':'لم تصل أي رسالة واردة من واتساب خلال 24 ساعة' if whatsapp_count and not whatsapp_recent else '','detail':f'{whatsapp_count} مؤسسة مربوطة'})
  if table_exists(c,'call_connections',s):
   call_count=scalar(c,"SELECT COUNT(*) n FROM call_connections WHERE status='ready'"); call_failures=scalar(c,"SELECT COUNT(*) n FROM call_logs WHERE status IN ('failed','no_answer','busy') AND created_at>=?",((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),)) if table_exists(c,'call_logs',s) else 0
   services.append({'service':'calls','label':'المكالمات','status':'warning' if call_failures else 'ready' if call_count else 'not_connected','affected':call_count,'lastError':f'{call_failures} مكالمة فاشلة خلال 24 ساعة' if call_failures else '','detail':f'{call_count} مؤسسة مربوطة'})
  return {'checkedAt':stamp(),'monitored':monitored,'services':services,'alerts':snap.get('alerts',[])[:20],'incidents':[x for x in services if x['status'] in ('warning','error')]}
 if r=='security-center' and m=='GET':
  failed=rows(c,"SELECT account,ip,success,created_at FROM platform_login_events WHERE success=0 ORDER BY id DESC LIMIT 30")
  blocked=rows(c,"SELECT organization_id,device_id,device_name,blocked_at FROM blocked_devices ORDER BY blocked_at DESC LIMIT 30") if table_exists(c,'blocked_devices',s) else []
  return {'firewall':'application-rate-limit','rateLimit':{'windowSeconds':60,'maxRequestsPerWindow':120},'failedLogins':failed,'blockedDevices':blocked,'https':'استضافة Render مسؤولة عن TLS؛ فعّل فرض HTTPS من إعدادات الاستضافة','secrets':'محفوظة في متغيرات البيئة ولا تعرض في اللوحة','backups':'تحتاج تخزينًا خارجيًا منفصلًا من إعدادات الاستضافة'}
 if r=='service-health/check' and m=='POST':
  service=str(d.get('service','')).strip()
  if service not in ('whatsapp','ai','calls','server','database','notifications'): raise ValueError('الخدمة غير معروفة')
  audit(c,a['name'],'service_check','service-health/'+service)
  started=False
  try:
   import service_monitor, threading
   service_monitor.start(s.db,s.DB_PATH,bool(s.DATABASE_URL),getattr(s,'PORT',None))
   threading.Thread(target=service_monitor.run_checks,args=(s.db,s.DB_PATH,bool(s.DATABASE_URL),getattr(s,'PORT',None)),daemon=True).start(); started=True
  except Exception: started=False
  return {'checkedAt':stamp(),'service':service,'started':started,'message':'بدأ فحص فعلي للخدمات الآن؛ حدّث الصفحة بعد لحظات لرؤية النتيجة' if started else 'تعذر بدء الفحص الآن؛ النتيجة المعروضة هي آخر فحص دوري'}
 if r=='technical-ai/ask' and m=='POST':
  question=str(d.get('question','')).strip()[:1000]
  if len(question)<4: raise ValueError('اكتب وصف المشكلة أولًا')
  if not d.get('_rule'):
   import technical_agent, ai_core
   if technical_agent.ai_configured():
    try:
     result=technical_agent.answer(c,question,d.get('history') if isinstance(d.get('history'),list) else [],lambda rr,mm,dd: dispatch(c,rr,mm,dd,{},page,a,h,s),rows,a['name'])
     audit(c,a['name'],'technical_ai_chat',question[:200])
     return result
    except ai_core.AIServiceError as error:
     print('TECHNICAL AI FALLBACK:',error.message)
  question_fold=question.casefold()
  org=None
  for candidate in rows(c,'SELECT id,name FROM organizations ORDER BY id'):
   if candidate['name'] and candidate['name'].casefold() in question_fold: org=candidate; break
  ticket=None
  ticket_match=re.search(r'(?:#|طلب\s*دعم|شكوى|بلاغ)\s*(\d+)',question_fold)
  if ticket_match:
   ticket=c.execute('SELECT id,organization_id,status,category,message,last_error FROM support_tickets WHERE id=?',(int(ticket_match.group(1)),)).fetchone()
   if ticket: org=c.execute('SELECT id,name FROM organizations WHERE id=?',(ticket['organization_id'],)).fetchone()
  if ticket:
   import technical_support
   full_ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(ticket['id'],)).fetchone()
   evidence=technical_support.process_ticket(c,full_ticket,__import__(__name__),s)
   evidence.update({'organization':dict(org) if org else None,'affected':org['name'] if org else 'المشترك','scope':'organization','warnings':sum(x['status']=='warning' for x in evidence['checks']),'status':'proposed'})
   evidence['diagnosis']+=' العوائق: '+'؛ '.join(evidence['limitations'])
   return evidence
  if question_fold in ('تقرير','وش وضع المنصة؟','وش وضع المنصة','حالة المنصة'):
   report=dispatch(c,'technical-ai/daily-report','GET',{},q,page,a,h,s)
   return {'service':'platform','scope':'global','affected':'خدووم','checks':report['serviceChecks'],'diagnosis':report['summary'],'proposal':report['recommendations'],'requiresApproval':True,'report':report}
  service='whatsapp' if any(x in question_fold for x in ('واتساب','whatsapp')) else 'calls' if any(x in question_fold for x in ('مكالمة','المكالمات','calls')) else 'login' if any(x in question_fold for x in ('دخول','تسجيل','401','كلمة المرور','تسجيل الدخول')) else 'ai' if any(x in question_fold for x in ('ai','ذكاء','اسألني','اسالني','المساعد')) else 'ads' if any(x in question_fold for x in ('إعلان','اعلان','الإعلانات','اعلانات')) else 'performance' if any(x in question_fold for x in ('بطء','بطيء','سرعة','صفحة')) else 'support' if any(x in question_fold for x in ('دعم','شكوى','بلاغ')) else 'platform'
  checks=[]; affected='عامة'; scope='global'
  if org:
   affected=org['name']; scope='organization'
   sub=c.execute('SELECT package,starts_at,expires_at FROM subscriptions WHERE organization_id=?',(org['id'],)).fetchone()
   users=scalar(c,'SELECT COUNT(*) n FROM users WHERE organization_id=? AND active=1',(org['id'],))
   open_support=scalar(c,"SELECT COUNT(*) n FROM support_tickets WHERE organization_id=? AND status IN ('open','under_review','in_progress')",(org['id'],))
   failures=scalar(c,'SELECT COUNT(*) n FROM login_failures WHERE organization_id=? AND created_at>=?',(org['id'],(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()))
   credits=credits_summary(c,org['id'],s)
   checks.extend([{'key':'package','label':'الباقة','status':'ok' if sub else 'warning','details':sub['package'] if sub else 'غير موجودة'}, {'key':'users','label':'المستخدمون','status':'ok' if users else 'warning','details':f'{users} مستخدم نشط'}, {'key':'support','label':'طلبات الدعم','status':'warning' if open_support else 'ok','details':f'{open_support} طلب مفتوح'}, {'key':'login','label':'الأخطاء الأخيرة','status':'warning' if failures else 'ok','details':f'{failures} محاولة/خطأ خلال 24 ساعة'}, {'key':'balance','label':'الاستخدام والرصيد','status':'warning' if any(v['base'] and v['remaining']<=max(1,math.ceil(v['base']*.1)) for v in credits['services'].values()) else 'ok','details':f"{credits['total_remaining']} وحدة متبقية"}])
   if service=='ai':
    ai_ready=bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip())
    ai_uses=scalar(c,'SELECT COUNT(*) n FROM ai_usage WHERE organization_id=? AND created_at>=?',(org['id'],stamp()[:10]))
    checks.append({'key':'ai','label':'خدمة اسألني','status':'ok' if ai_ready else 'warning','details':('الخدمة مهيأة' if ai_ready else 'مفتاح AI غير مهيأ')+f' · {ai_uses} استخدام اليوم'})
   if ticket:
    checks.append({'key':'ticket','label':'طلب الدعم','status':'warning' if ticket['status'] not in ('resolved','closed') else 'ok','details':f"#{ticket['id']} · {ticket['status']} · {ticket['category']}"})
  else:
   if table_exists(c,'page_performance_events',s):
    perf=c.execute("SELECT COUNT(*) samples,COALESCE(ROUND(AVG(elapsed_ms),0),0) avg_ms,COALESCE(MAX(elapsed_ms),0) max_ms,COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) failures FROM page_performance_events WHERE created_at>=?",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),)).fetchone()
    checks.append({'key':'performance','label':'الأداء','status':'warning' if perf['max_ms']>=1500 or perf['failures'] else 'ok','details':f"{perf['samples']} قياس · متوسط {int(perf['avg_ms'])}ms · أعلى {int(perf['max_ms'])}ms · فشل {int(perf['failures'])}"})
   checks.append({'key':'database','label':'قاعدة البيانات','status':'ok','details':'استعلام الفحص نجح'})
   checks.append({'key':'server','label':'السيرفر','status':'ok','details':'واجهة الإدارة استجابت'})
   if service=='ai':
    ai_ready=bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip())
    checks.append({'key':'ai','label':'خدمة اسألني','status':'ok' if ai_ready else 'warning','details':'إعداد خدمة AI على الخادم' if ai_ready else 'مفتاح AI غير مهيأ على الخادم'})
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
  support_counts=rows(c,'SELECT status,COUNT(*) n FROM support_tickets GROUP BY status')
  open_tickets=rows(c,"SELECT id,organization_id,category,status,created_at,updated_at FROM support_tickets WHERE status NOT IN ('resolved','closed') ORDER BY created_at LIMIT 100")
  overdue=[x for x in open_tickets if x['updated_at']<(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()]
  repeated=rows(c,"SELECT organization_id,category,COUNT(*) n FROM support_tickets WHERE created_at>=? GROUP BY organization_id,category HAVING COUNT(*)>1",((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),))
  page_metrics=rows(c,"SELECT page_name,COUNT(*) samples,AVG(elapsed_ms) avg_ms,MAX(elapsed_ms) max_ms FROM page_performance_events WHERE created_at>=? GROUP BY page_name",(start,))
  service_checks=[{'key':'database','label':'قاعدة البيانات','status':'ok','details':'استعلام التقرير نجح الآن'}, {'key':'api','label':'API الإدارة','status':'ok','details':'معالجة طلب التقرير تمت؛ لا يثبت سلامة كل المسارات'}, {'key':'whatsapp','label':'واتساب','status':'warning','details':'لم يجر اختبار إرسال واستقبال مباشر؛ يحتاج فحص المؤسسة المحددة'}]
  summary='فُحصت سجلات الإدارة وقاعدة البيانات في '+stamp()+'. الطلبات المفتوحة المعروضة: '+str(len(open_tickets))+'؛ المتأخرة: '+str(len(overdue))+'. لا يمكن اعتبار المنصة سليمة بالكامل دون اختبارات الخدمات الخارجية.'
  recommendations='1. مراجعة الشكاوى الحرجة والمتأخرة. 2. استكمال أدلة المشاكل المتكررة. 3. فحص قياسات الصفحات. 4. اعتماد إصلاح محدد قبل تغيير التشغيل.'
  return {'checkedAt':stamp(),'supportCounts':support_counts,'openTickets':open_tickets,'overdueTickets':overdue,'repeatedProblems':repeated,'pageMetrics':page_metrics,'serviceChecks':service_checks,'summary':summary,'recommendations':recommendations,'date':start,'counts':counts,'totalTasks':len(tasks),'performance':performance,'lastDetected':dict(last_detected) if last_detected else None,'lastCompleted':dict(last_completed) if last_completed else None,'state':dict(state) if state else {},'recentTasks':tasks[:20]}
 if r=='technical-ai/state' and m=='GET':
  # حالة الموظف التقني فقط (للرئيسية) بدل تحميل مئات المهام والسجلات.
  if not table_exists(c,'technical_agent_state',s): return {'state':{'status':'offline','lastTask':''}}
  state=c.execute('SELECT status,last_heartbeat,last_check,last_task FROM technical_agent_state WHERE id=1').fetchone()
  fresh=lambda v: bool(v and (datetime.now(timezone.utc)-datetime.fromisoformat(v)).total_seconds()<=180)
  live=bool(state and (fresh(state['last_check']) or fresh(state['last_heartbeat'])))
  return {'state':{'status':'working' if live and state['status']=='working' else 'online' if live else 'offline','lastTask':state['last_task'] if state else ''}}
 if r=='service-health/timings' and m=='GET':
  try: minutes=max(5,min(720,int(q.get('minutes',30))))
  except (TypeError,ValueError): minutes=30
  out=s.request_timings(minutes); out['databaseLatencyMs']=s.database_latency_ms(); out['database']=s.database_location(); out['checkedAt']=stamp()
  out['serverRegion']=os.environ.get('RENDER_REGION','') or os.environ.get('KHDOOM_REGION','')
  out['note']='القياس من داخل الخادم لهذه النسخة العاملة فقط ويبدأ من آخر تشغيل. زمن قاعدة البيانات هو زمن أبسط استعلام؛ كل صفحة تدفعه مرة لكل استعلام.'
  return out
 if r=='technical-ai/playbooks' and m=='GET':
  ensure_owner_tables(c,s,('technical_playbooks',))
  return {'items':rows(c,'SELECT id,title,solution,ticket_id,uses,created_by,created_at FROM technical_playbooks WHERE active=1 ORDER BY id DESC LIMIT 200')}
 if re.fullmatch(r'technical-ai/playbooks/\d+',r) and m=='DELETE':
  # «نسيان» حل تعلّمه الموظف التقني؛ يبقى السجل للمراجعة ولا يُستخدم بعدها.
  ensure_owner_tables(c,s,('technical_playbooks',)); ident=int(r.split('/')[2])
  if not c.execute('SELECT id FROM technical_playbooks WHERE id=? AND active=1',(ident,)).fetchone(): raise s.ApiError(404,'الحل غير موجود')
  c.execute('UPDATE technical_playbooks SET active=0 WHERE id=?',(ident,)); return {'deleted':True}
 if r=='technical-ai' and m=='GET':
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks','technical_incidents','login_failures'))
  state=c.execute('SELECT * FROM technical_agent_state WHERE id=1').fetchone()
  heartbeat=state['last_heartbeat'] if state else None
  last_check=state['last_check'] if state else None
  internal_live=bool(last_check and (datetime.now(timezone.utc)-datetime.fromisoformat(last_check)).total_seconds()<=180)
  external_live=bool(heartbeat and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat)).total_seconds()<=180)
  live=internal_live or external_live
  configured=bool(os.environ.get('KHDOOM_TECHNICAL_AI_SECRET','').strip())
  state_out={'status':'working' if live and state['status']=='working' else 'online' if live else 'offline','lastHeartbeat':heartbeat,'lastCheck':last_check,'lastTask':state['last_task'] if state else '','lastError':state['last_error'] if state else '','lastSuccess':state['last_success'] if state else '','internalMonitoring':internal_live,'externalAgentConfigured':configured,'externalAgentOnline':external_live}
  tasks=rows(c,"SELECT t.*,o.name organization_name,u.name user_name FROM technical_tasks t LEFT JOIN organizations o ON o.id=t.organization_id LEFT JOIN users u ON u.id=t.user_id ORDER BY t.id DESC LIMIT 100")
  task_stats={key:0 for key in ('queued','diagnosing','proposed','approved','completed','failed','not_executed')}
  for task in tasks:
   task_stats[task['status']]=task_stats.get(task['status'],0)+1
  failures=rows(c,"SELECT f.*,o.name organization_name,u.name user_name FROM login_failures f LEFT JOIN organizations o ON o.id=f.organization_id LEFT JOIN users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT 100")
  recent_failures=scalar(c,"SELECT COUNT(*) n FROM login_failures WHERE created_at>=?",((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),))
  import technical_support
  return {'instructions':technical_support.POLICY,'capabilities':{'scopedLogs':True,'databaseChecks':True,'recordedPageSpeed':True,'supportUpdates':True,'providerLiveTest':False,'restartService':False,'automaticProductionRepair':False},'state':state_out,'monitoring':internal_live,'tasks':tasks,'taskStats':task_stats,'loginFailures':failures,'loginAlert':recent_failures>=3,'items':rows(c,"SELECT i.*,o.name organization_name FROM technical_incidents i LEFT JOIN organizations o ON o.id=i.organization_id ORDER BY i.id DESC LIMIT 100"),'note':'المراقبة تجمع الحالة وتكتب التقارير؛ لا تعديل إنتاج أو نشر تلقائيًا.'}
 if r=='technical-ai/heartbeat' and m=='POST':
  secret=h.headers.get('X-Technical-AI-Secret','')
  expected=os.environ.get('KHDOOM_TECHNICAL_AI_SECRET','').strip()
  if not expected or not hmac.compare_digest(secret,expected): raise s.ApiError(401,'تعذر التحقق من موظف التقنية')
  task=str(d.get('task',''))[:500]; check=str(d.get('check',''))[:200]
  c.execute("INSERT INTO technical_agent_state(id,status,last_heartbeat,last_check,last_task,updated_at) VALUES(1,'online',?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='online',last_heartbeat=excluded.last_heartbeat,last_check=excluded.last_check,last_task=excluded.last_task,updated_at=excluded.updated_at",(stamp(),check or stamp(),task,stamp()))
  return {'saved':True,'status':'online'}
 if r=='integrations' and m=='GET':
  return {'items':rows(c,'SELECT key,name,category,status,required_permission,provider_configured,notes,updated_at FROM platform_integrations ORDER BY id'),'note':'هذه الوحدات مجهزة للتوسع فقط. لا توجد خدمة مستقبلية مفعلة دون تكامل رسمي وإعداد خادم وصلاحية مناسبة.'}
 if re.fullmatch(r'technical-ai/tasks/\d+/execute',r) and m=='POST':
  # الإجراء الذي اقترحه الموظف التقني ينفَّذ فقط بضغطة المدير، وبصلاحية المدير نفسه، وبعد إعادة التحقق من الحالة الآن.
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks'))
  ident=int(r.split('/')[2]); task=c.execute('SELECT * FROM technical_tasks WHERE id=?',(ident,)).fetchone()
  if not task: raise s.ApiError(404,'مهمة موظف AI غير موجودة')
  task=dict(task); org=task.get('organization_id')
  try: plan=json.loads(task.get('suggested_action') or 'null')
  except ValueError: plan=None
  if not isinstance(plan,dict) or not org or task['status'] in ('completed','failed','not_executed'): raise ValueError('لا يوجد إجراء مقترح قابل للتنفيذ لهذه المهمة')
  def need(perm):
   if perm not in a['permissions']: raise s.ApiError(403,'هذا الإجراء يحتاج صلاحية غير متاحة لحسابك')
  kind=plan.get('type')
  if kind=='unsuspend':
   need('suspend')
   if not suspended(c,org): raise ValueError('المؤسسة غير موقوفة الآن؛ لا حاجة للإجراء')
   dispatch(c,'organizations/%d/status'%org,'POST',{'suspended':False},{},page,a,h,s)
   done='فُك إيقاف المؤسسة'; customer='تم تفعيل حساب مؤسستك من جديد. سجّل الدخول الآن، وإذا بقيت المشكلة اكتب لنا هنا.'
  elif kind=='activate_user':
   need('organizations.edit')
   target=c.execute('SELECT id,name,active FROM users WHERE organization_id=? AND username=? AND archived_at IS NULL',(org,str(plan.get('username',''))),).fetchone()
   if not target: raise ValueError('المستخدم لم يعد موجودًا في هذه المؤسسة')
   if target['active']: raise ValueError('المستخدم مفعّل الآن؛ لا حاجة للإجراء')
   dispatch(c,'organizations/%d/users/%d/status'%(org,target['id']),'POST',{'active':True},{},page,a,h,s)
   done='أُعيد تفعيل المستخدم «'+str(plan.get('username'))+'»'; customer='أعدنا تفعيل المستخدم «'+str(plan.get('username'))+'». يقدر يسجّل الدخول الآن، وإذا بقيت المشكلة اكتب لنا هنا.'
  elif kind=='add_credit':
   need('usage')
   amount=number(d.get('amount'),1,100000,True)
   state=service_quota.snapshot(c,org)['services']
   wanted=[x for x in (plan.get('services') or []) if x in SERVICE_LABELS and not state[x]['unlimited']]
   if not wanted: raise ValueError('لا توجد خدمة محددة الرصيد لزيادتها')
   dispatch(c,'credits/%d'%org,'POST',{'adjustments':{x:amount for x in wanted},'reason':'زيادة من الموظف التقني لطلب دعم '+str(task.get('support_ticket_id') or '')},{},page,a,h,s)
   names=' و'.join(SERVICE_LABELS[x] for x in wanted)
   done='زيد رصيد '+names+' بمقدار '+str(amount)+' لهذه الدورة'; customer='أضفنا لك رصيدًا إضافيًا ('+str(amount)+') في '+names+' لهذه الدورة. تقدر تكمل استخدامك الآن.'
  else: raise ValueError('هذا الإجراء يُنفذ من صفحته في اللوحة')
  ts=stamp()
  c.execute("UPDATE technical_tasks SET status='completed',approved_by=?,action_taken=?,result=?,finished_at=?,needs_owner=0,suggested_action='' WHERE id=?",(a['name'],done+' بأمر '+a['name'],'نُفّذ الإجراء من لوحة الإدارة وأُبلغ المشترك وأُغلق الطلب.',ts,ident))
  if task.get('support_ticket_id') and table_exists(c,'support_tickets',s):
   ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(task['support_ticket_id'],)).fetchone()
   if ticket:
    c.execute("UPDATE support_tickets SET status='resolved',owner_reply=?,owner_reply_by='admin',updated_at=? WHERE id=?",(customer,ts,ticket['id']))
    support_event(c,dict(ticket),actor_type='admin',actor_name=a['name'],event_type='technical_action_executed',body=done,from_status=ticket['status'],to_status='resolved')
    s.audit_log(c,ticket['organization_id'],None,'support_ticket_updated','رد إدارة خدووم على طلب الدعم #'+str(ticket['id']),'security',str(ticket['id']))
  audit(c,a['name'],'technical_action_executed',json.dumps({'task_id':ident,'organization_id':org,'type':kind,'result':done},ensure_ascii=False))
  return {'saved':True,'message':done+'، وأُرسل الرد للمشترك.'}
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
   if task.get('support_ticket_id'):
    import technical_support
    ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(task['support_ticket_id'],)).fetchone()
    if not ticket: raise s.ApiError(404,'طلب الدعم غير موجود')
    evidence=technical_support.process_ticket(c,ticket,__import__(__name__),s)
    return {'saved':True,'message':'تم فحص السجلات وإرسال تحديث الدعم؛ لم يتم إعلان الحل','taskId':ident,'report':evidence}
   diagnosis=task['diagnosis'] or 'تمت مراجعة مؤشرات الخادم والخدمة المرتبطة بالمهمة دون الوصول إلى الأسرار أو بيانات مؤسسة أخرى.'
   proposal=task['proposal'] or 'تنفيذ الإصلاح المقترح في بيئة آمنة ثم اختبار النتيجة قبل اعتماد التنفيذ.'
   report='تقرير غير متحقق من تنفيذ إصلاح: المشكلة: '+title+' | الفحص: '+diagnosis+' | الخطة: '+proposal+' | التنفيذ الإنتاجي يحتاج موافقة الإدارة.'
   c.execute("UPDATE technical_tasks SET status='proposed',diagnosis=?,proposal=?,action_taken=?,result=? WHERE id=?",(diagnosis,proposal,'أُعد تقرير وخطة إصلاح بانتظار موافقة الإدارة',report,ident))
   message='تم إعداد التقرير وخطة الإصلاح.'
  elif action=='approve':
   if a.get('role') not in ('owner','manager'): raise s.ApiError(403,'موافقة المدير مطلوبة')
   c.execute("UPDATE technical_tasks SET status='approved',approved_by=?,action_taken=?,result=? WHERE id=?",(a['name'],'اعتمدت الإدارة تنفيذ الإصلاح؛ التنفيذ الفعلي يبقى يدويًا ومختبرًا','تمت الموافقة. لا توجد أي تعديلات تلقائية على الإنتاج.',ident))
   message='تمت الموافقة. يمكن تنفيذ الإصلاح يدويًا ثم تسجيل النتيجة.'
  elif action=='complete':
   if task['status'] not in ('approved','diagnosing','diagnosed','proposed'):
    raise ValueError('ابدأ الفحص وأعد التقرير قبل تسجيل الإنجاز')
   import technical_support
   action_taken,verification=technical_support.completion_evidence(d,task)
   result='تم التحقق بعد المعالجة: '+verification
   c.execute("UPDATE technical_tasks SET status='completed',action_taken=?,result=?,finished_at=? WHERE id=?",(action_taken,result,ts,ident))
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
    elif action=='complete': reply='تمت معالجة المشكلة والتحقق من النتيجة. إذا استمرت عندك، أرسل نتيجة التجربة عبر الدعم.'
    else: reply='لم تكتمل المعالجة؛ الطلب يحتاج متابعة ومعلومات إضافية، ولم يتم إعلان الحل.'
    next_status='resolved' if action=='complete' else 'in_progress' if action in ('start','report','approve') else ticket['status']
    next_status=automated_ticket_update(c,ticket['id'],next_status,reply,ts,force_status=action=='complete')
    support_event(c,dict(ticket),actor_type='technical_ai',actor_name='موظف التقنية AI',event_type='technical_'+action,body=reply,from_status=ticket['status'],to_status=next_status)
  audit(c,a['name'],'technical_task_'+action,json.dumps({'task':ident,'service':service},ensure_ascii=False))
  return {'saved':True,'message':message,'taskId':ident,'action':action}
 if r=='technical-ai/diagnose' and m=='POST':
  ensure_owner_tables(c,s,('technical_agent_state','technical_tasks','technical_incidents','login_failures'))
  service=str(d.get('service','')).strip(); services={'whatsapp':('واتساب','فشل webhook أو صلاحيات الربط','فحص رمز التحقق والتوقيع وسجل آخر webhook','تحديث الإعدادات فقط بعد نجاح اختبار مستقل'),'ai':('الذكاء الاصطناعي','الخدمة غير مهيأة أو تجاوزت الحد','مراجعة إعداد الخادم وحدود الباقة','إعادة مزامنة الحالة دون تغيير الأسرار'),'calls':('المكالمات','قناة الاتصال غير جاهزة أو بها فشل','فحص حالة قناة خدووم وسجل المكالمات','إعادة محاولة الاتصال بعد التحقق من الرصيد'),'login':('تسجيل الدخول','فشل مصادقة مستخدم أو أكثر','فحص وجود المستخدم وحالته وhash كلمة المرور وربط المؤسسة وLogin API والجلسات','اقتراح إعادة المزامنة أو إنهاء الجلسات المنتهية فقط؛ لا تغيير لكلمة المرور دون إجراء رسمي'),'server':('الخادم','بطء أو انقطاع في خادم خدووم','فحص استجابة API واتصال قاعدة البيانات وسجل الأخطاء','إعداد تقرير سبب العطل وخطة إصلاح ثم اختبارها قبل الاعتماد'),'database':('قاعدة البيانات','فشل استعلام أو بطء في البيانات','فحص اتصال القاعدة وسلامة الاستعلامات دون تغيير البيانات','اقتراح فهرسة أو إصلاح آمن بعد موافقة الإدارة')}
  if service not in services: raise ValueError('اختر خدمة مدعومة')
  label,problem,cause,proposal=services[service]; cause='احتمال غير مؤكد؛ المطلوب فحص: '+cause; ts=stamp(); organization_id=number(d.get('organization_id'),1,100000000,True) if d.get('organization_id') else None; user_id=number(d.get('user_id'),1,100000000,True) if d.get('user_id') else None; cur=c.execute('INSERT INTO technical_incidents(service,organization_id,problem,root_cause,proposal,severity,test_status,deployment_status,affected_organizations,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id', (service,organization_id,problem,cause,proposal,'medium','not_tested','proposed',1 if organization_id else 0,ts,ts)); incident_id=cur.fetchone()['id']; cur=c.execute('INSERT INTO technical_tasks(organization_id,user_id,service,problem,severity,status,diagnosis,proposal,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id',(organization_id,user_id,service,problem,'medium','diagnosed',cause,proposal,ts,'ai')); task_id=cur.fetchone()['id']; audit(c,a['name'],'technical_diagnosis',service); return {'id':incident_id,'taskId':task_id,'service':label,'problem':problem,'rootCause':cause,'proposal':proposal,'testStatus':'not_tested','deploymentStatus':'proposed'}
 if re.fullmatch(r'technical-ai/\d+/(test|approve|reject)',r) and m=='POST':
  ident=int(r.split('/')[1]); action=r.split('/')[2]; item=c.execute('SELECT id FROM technical_incidents WHERE id=?',(ident,)).fetchone()
  if not item: raise ValueError('التشخيص غير موجود')
  if action=='test':
   import technical_support
   technical_support.completion_evidence(d,{})
   c.execute("UPDATE technical_incidents SET test_status='passed',updated_at=? WHERE id=?",(stamp(),ident)); message='تم تسجيل نجاح الاختبار؛ لم يتم نشر أي كود'
  elif action=='approve':
   if a.get('role') not in ('owner','manager'): raise s.ApiError(403,'موافقة المدير مطلوبة')
   c.execute("UPDATE technical_incidents SET deployment_status='approved',approved_by=?,updated_at=? WHERE id=?",(a['name'],stamp(),ident)); message='تمت الموافقة للمراجعة؛ النشر ما زال يدويًا'
  else: c.execute("UPDATE technical_incidents SET deployment_status='rejected',updated_at=? WHERE id=?",(stamp(),ident)); message='تم رفض الإصلاح المقترح'
  audit(c,a['name'],'technical_'+action,ident); return {'saved':True,'message':message}
 if r=='admins' and m=='GET': return {'items':rows(c,'SELECT id,name,username,role,permissions,active,created_at,totp_enabled FROM platform_admins ORDER BY id DESC LIMIT 200'),'roles':ROLES,'permissions':PERMISSIONS}
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
   if d.get('reset2fa'):
    c.execute("UPDATE platform_admins SET totp_secret='',totp_enabled=0,totp_last_step=0 WHERE id=?",(ident,)); audit(c,a['name'],'two_factor_reset','admins/'+str(ident))
   c.execute('DELETE FROM platform_sessions WHERE admin_id=?',(ident,))
  return {'saved':True}
 if re.fullmatch(r'admins/\d+',r) and m=='DELETE':
  # الحذف النهائي للمالك فقط، ولحساب موقوف فقط: الإيقاف أولًا يمنع حذف حساب يعمل بالخطأ.
  if a.get('role')!='owner': raise s.ApiError(403,'حذف الحسابات الإدارية للمالك فقط')
  ident=int(r.split('/')[1])
  if ident==a.get('id'): raise ValueError('لا يمكنك حذف حسابك الحالي')
  row=c.execute('SELECT name,username,active FROM platform_admins WHERE id=?',(ident,)).fetchone()
  if not row: raise s.ApiError(404,'الحساب غير موجود')
  if row['active']: raise ValueError('أوقف الحساب أولًا ثم احذفه')
  c.execute('DELETE FROM platform_sessions WHERE admin_id=?',(ident,))
  c.execute('UPDATE support_tickets SET assigned_admin_id=NULL WHERE assigned_admin_id=?',(ident,))
  c.execute('DELETE FROM platform_admins WHERE id=?',(ident,))
  # الاسم يبقى في السجل نصًا حتى بعد حذف الحساب.
  audit(c,a['name'],'admin_deleted',str(row['name'])+' ('+str(row['username'])+')')
  return {'deleted':True}
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
  out=dict(org); out['service_numbers']=service_numbers(c,ident); out['account_id']=(c.execute('SELECT account_id FROM account_organizations WHERE organization_id=?',(ident,)).fetchone() or {'account_id':None})['account_id']; out['branches']=rows(c,'SELECT id,name,status FROM organization_branches WHERE organization_id=? ORDER BY created_at,id',(ident,)); out['users']=rows(c,"SELECT u.id,u.name,u.email,u.phone,u.role,u.active,(SELECT reason FROM login_failures f WHERE f.user_id=u.id ORDER BY f.id DESC LIMIT 1) last_login_failure FROM users u WHERE u.organization_id=? ORDER BY u.id LIMIT 100",(ident,)); out['devices']=rows(c,'SELECT se.token_hash id,se.device_name,se.device_id,se.last_seen_at,se.trusted,se.expires_at,u.name FROM sessions se JOIN users u ON u.id=se.user_id WHERE u.organization_id=? AND se.expires_at>? ORDER BY se.last_seen_at DESC LIMIT 100',(ident,stamp())); out['employees']=rows(c,'SELECT employee_type,COUNT(*) requests FROM ai_usage WHERE organization_id=? GROUP BY employee_type',(ident,)); out['ads']=rows(c,'SELECT id,title,active,approved,expires_at FROM advertisements WHERE organization_id=? ORDER BY id DESC LIMIT 50',(ident,)); out['rewards']=rows(c,'SELECT kind,amount,reason,actor,created_at FROM platform_rewards WHERE organization_id=? ORDER BY id DESC LIMIT 30',(ident,)); out['credits']=credits_summary(c,ident,s); wa=c.execute('SELECT phone_number,phone_number_id,waba_id,updated_at FROM whatsapp_connections WHERE organization_id=?',(ident,)).fetchone() if table_exists(c,'whatsapp_connections',s) else None; wa_messages=scalar(c,'SELECT COUNT(*) n FROM whatsapp_messages WHERE organization_id=?',(ident,)) if table_exists(c,'whatsapp_messages',s) else 0; wa_hook=c.execute('SELECT received_at FROM whatsapp_webhooks WHERE phone_number_id=?',(wa['phone_number_id'],)).fetchone() if wa and table_exists(c,'whatsapp_webhooks',s) else None; call_link=c.execute('SELECT phone_number,status,last_error,updated_at FROM call_connections WHERE organization_id=?',(ident,)).fetchone() if table_exists(c,'call_connections',s) else None; ai_ready=bool(os.environ.get('KHDOOM_AI_API_KEY','').strip() or os.environ.get('OPENAI_API_KEY','').strip()); out['services']={'account':{'status':'ok','label':'الحساب'},'package':{'status':'ok' if org['package'] else 'warning','label':'الباقة'},'permissions':{'status':'ok' if out['users'] else 'warning','label':'الصلاحيات'},'whatsapp':{'status':'ok' if wa else 'not_connected','label':'واتساب','phone':wa['phone_number'] if wa else None,'messages':wa_messages,'webhook':bool(wa_hook)},'ai':{'status':'ok' if ai_ready else 'not_configured','label':'AI','requests':out['credits']['services']['ai']['used_month']},'calls':{'status':call_link['status'] if call_link else 'not_connected','label':'المكالمات','phone':call_link['phone_number'] if call_link else None,'last_error':call_link['last_error'] if call_link else ''},'payment':{'status':'ok','label':'الدفع'},'server':{'status':'ok','label':'السيرفر'}}; return out
 if re.fullmatch(r'organizations/\d+/users/\d+/status',r) and m=='POST':
  _,org_part,_,user_part,_=r.split('/'); ident=int(org_part); user_id=int(user_part)
  user=c.execute('SELECT id,name,active FROM users WHERE id=? AND organization_id=?',(user_id,ident)).fetchone()
  if not user: raise s.ApiError(404,'المستخدم غير موجود في هذه المؤسسة')
  active=int(bool(d.get('active')))
  c.execute('UPDATE users SET active=? WHERE id=?',(active,user_id))
  if not active: c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
  audit(c,a['name'],'organization_user_status',json.dumps({'organization_id':ident,'user_id':user_id,'active':bool(active)},ensure_ascii=False))
  return {'saved':True,'active':bool(active),'message':'تم إعادة تفعيل المستخدم' if active else 'تم إيقاف المستخدم وإنهاء جلساته'}
 if re.fullmatch(r'organizations/\d+/service-numbers',r) and m=='POST':
  ident=int(r.split('/')[1])
  if not c.execute('SELECT id FROM organizations WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'المؤسسة غير موجودة')
  try: changed=save_service_numbers(c,ident,d if isinstance(d,dict) else {},a['name'])
  except ValueError as problem: raise s.ApiError(400,str(problem))
  if changed: audit(c,a['name'],'organization_service_numbers','organization='+str(ident)+';'+';'.join(changed))
  return {'saved':True,'service_numbers':service_numbers(c,ident)}
 if re.fullmatch(r'organizations/\d+/(status|logout|reward)',r) and m=='POST':
  ident=int(r.split('/')[1]); action=r.split('/')[2]
  if not c.execute('SELECT id FROM organizations WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'المؤسسة غير موجودة')
  if action=='status':
   c.execute('INSERT INTO platform_org_state VALUES(?,?) ON CONFLICT(organization_id) DO UPDATE SET suspended=excluded.suspended',(ident,int(bool(d.get('suspended'))))); c.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id=?)',(ident,))
  elif action=='logout':
   device=str(d.get('device','')); c.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id=?)'+(' AND token_hash=?' if device else ''),[ident]+([device] if device else []))
  else:
   kind=d.get('kind'); amount=number(d.get('amount'),1,3650,True); reason=str(d.get('reason','')).strip()
   apply_reward(c,ident,kind,amount,reason,a['name'])
  detail={'organization_id':ident}
  if action=='status': detail['suspended']=bool(d.get('suspended'))
  elif action=='logout': detail['device']='one' if d.get('device') else 'all'
  else: detail.update({'kind':kind,'amount':amount,'reason':reason[:200]})
  audit(c,a['name'],'organization_'+action,json.dumps(detail,ensure_ascii=False))
  return {'saved':True}
 if r=='packages' and m=='GET':
  out=rows(c,'SELECT * FROM platform_packages ORDER BY monthly')
  for item in out:
   price_map=package_price_map(c,item['package'])
   item['prices']=[{'months':months,'price_sar':price} for months,price in price_map.items()]
   item['service_limits']={
    'ai_daily':item['ai_daily'],
    'ai_monthly':item.get('ai_monthly'),
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
  raw_monthly=d['ai_monthly'] if 'ai_monthly' in d else dict(settings).get('ai_monthly')
  # فارغ = غير محدد: الحد الشهري يساوي اليومي × أيام الشهر.
  monthly_ai=None if raw_monthly is None or raw_monthly=='' else number(raw_monthly,0,100000000,True)
  if monthly_ai is not None and monthly_ai<daily: raise ValueError('حد AI الشهري لا يكون أقل من الحد اليومي')
  features=str(d.get('features',settings['features'] or ''))[:4000]
  c.execute('UPDATE platform_packages SET monthly=?,yearly=?,ai_daily=?,ai_monthly=?,ai_employees=?,whatsapp_units=?,calls_units=?,ads_units=?,features=? WHERE package=?',(prices[1],prices[12],daily,monthly_ai,employees,*limits,features,pkg))
  for months,price in prices.items(): c.execute('INSERT INTO package_prices(package,duration_months,price_sar,updated_at) VALUES(?,?,?,?) ON CONFLICT(package,duration_months) DO UPDATE SET price_sar=excluded.price_sar,updated_at=excluded.updated_at',(pkg,months,price,stamp()))
  audit(c,a['name'],'package_prices_updated',json.dumps({'package':pkg,'prices':prices},ensure_ascii=False))
  return {'saved':True,'prices':prices,'service_limits':{'ai_daily':daily,'ai_monthly':monthly_ai,'ai_employees':employees,'whatsapp_units':limits[0],'calls_units':limits[1],'ads_units':limits[2]},'price_warnings':warnings}
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
  bulk=credits_bulk(c,s,[item['id'] for item in items])
  for item in items: item['credits']=bulk[item['id']]
  income=round(sum(x['credits']['subscription_value'] for x in items),2); cost=round(sum(x['credits']['actual_cost'] for x in items),2)
  totals={'month':stamp()[:7],'income':income,'cost':cost,'profit':round(income-cost,2),'losing':sum(1 for x in items if x['credits']['estimated_profit']<0),'organizations':len(items)}
  return {'items':items,'total':len(items),'page':1,'pageSize':len(items),'totals':totals,'costs':service_unit_costs(c,s)}
 if r=='service-costs' and m=='GET':
  current=stamp()[:7]; previous=(datetime.fromisoformat(current+'-01')-timedelta(days=1)).strftime('%Y-%m')
  return {'costs':service_unit_costs(c,s),'invoices':rows(c,'SELECT month,service,amount_sar,units,unit_cost,actor,created_at FROM platform_service_invoices ORDER BY month DESC,service LIMIT 72'),'usage':{current:platform_month_usage(c,s,current),previous:platform_month_usage(c,s,previous)},'current_month':current,'previous_month':previous,'usd_to_sar':USD_TO_SAR}
 if r=='service-costs' and m=='POST':
  service=str(d.get('service','')).strip(); month=normalize_month(d.get('month'))
  if service not in SERVICE_LABELS: raise ValueError('اختر خدمة صحيحة')
  start,_=month_bounds(month)
  if start>datetime.now(timezone.utc): raise ValueError('لا يمكن إدخال فاتورة شهر لم يبدأ')
  amount=number(d.get('amount'),0,100000000)
  currency=str(d.get('currency','sar')).strip().lower()
  if currency not in ('sar','usd'): raise ValueError('العملة غير صحيحة')
  if amount<=0: raise ValueError('اكتب مبلغ الفاتورة')
  amount_sar=round(float(amount)*(USD_TO_SAR if currency=='usd' else 1),2)
  units=platform_month_usage(c,s,month)[service]
  if units<=0: raise ValueError('لا يوجد استخدام مسجل لهذه الخدمة في هذا الشهر، فلا يمكن حساب تكلفة الوحدة')
  unit_cost=round(amount_sar/units,6)
  c.execute('INSERT INTO platform_service_invoices(month,service,amount_sar,units,unit_cost,actor,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(month,service) DO UPDATE SET amount_sar=excluded.amount_sar,units=excluded.units,unit_cost=excluded.unit_cost,actor=excluded.actor,created_at=excluded.created_at',(month,service,amount_sar,units,unit_cost,a['name'],stamp()))
  audit(c,a['name'],'service_invoice_saved',json.dumps({'month':month,'service':service,'amount_sar':amount_sar,'units':units,'unit_cost':unit_cost},ensure_ascii=False))
  return {'saved':True,'month':month,'service':service,'amount_sar':amount_sar,'units':units,'unit_cost':unit_cost,'costs':service_unit_costs(c,s)}
 if re.fullmatch(r'service-costs/[0-9]{4}-[0-9]{2}/[a-z]+',r) and m=='DELETE':
  _,month,service=r.split('/')
  done=c.execute('DELETE FROM platform_service_invoices WHERE month=? AND service=?',(month,service))
  if not done.rowcount: raise s.ApiError(404,'الفاتورة غير موجودة')
  audit(c,a['name'],'service_invoice_deleted',month+'/'+service)
  return {'deleted':True,'costs':service_unit_costs(c,s)}
 if r=='expenses' and m=='GET':
  return finance_summary(c)
 if r=='finance/revenue' and m=='GET':
  try: span=max(1,min(36,int(q.get('months',12))))
  except (TypeError,ValueError): span=12
  today=datetime.now(timezone.utc); first=today.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
  keys=[]
  for _ in range(span):
   keys.append(first.strftime('%Y-%m')); first=(first-timedelta(days=1)).replace(day=1)
  keys.reverse(); since=keys[0]+'-01'
  payments=rows(c,'SELECT p.id,p.organization_id,o.name organization_name,p.package,p.months,p.amount,p.discount_code,p.source,p.note,p.approved_by,p.created_at FROM platform_payments p LEFT JOIN organizations o ON o.id=p.organization_id WHERE p.created_at>=? ORDER BY p.created_at DESC,p.id DESC',(since,))
  monthly={k:{'month':k,'total':0.0,'count':0,'basic':0.0,'vip':0.0} for k in keys}
  for x in payments:
   bucket=monthly.get(str(x['created_at'])[:7])
   if bucket:
    amount=float(x['amount'] or 0); bucket['total']+=amount; bucket['count']+=1
    if x['package'] in ('basic','vip'): bucket[x['package']]+=amount
  series=[{**v,'total':round(v['total'],2),'basic':round(v['basic'],2),'vip':round(v['vip'],2)} for v in monthly.values()]
  return {'months':series,'thisMonth':series[-1],'periodTotal':round(sum(v['total'] for v in series),2),'periodCount':sum(v['count'] for v in series),'payments':payments[:100],'note':'الأرقام من المدفوعات المعتمدة فعليًا (طلبات التحويل المقبولة والدفعات المسجلة يدويًا)، وليست تقديرًا من أسعار الباقات.'}
 if r=='finance/payments' and m=='POST':
  ident=number(d.get('organization_id'),1,10**9,True); amount=number(d.get('amount'),0.01,10**7); package=d.get('package'); months=number(d.get('months',1),1,60,True); note=str(d.get('note','')).strip()[:500]
  if package not in ('basic','vip'): raise ValueError('اختر الباقة الأساسية أو VIP')
  if not note: raise ValueError('اكتب ملاحظة توضح مصدر الدفعة')
  if not c.execute('SELECT id FROM organizations WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'المؤسسة غير موجودة')
  cur=c.execute('INSERT INTO platform_payments(organization_id,package,months,amount,source,note,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?) RETURNING id',(ident,package,months,amount,'manual',note,a['name'],stamp())); payment_id=cur.fetchone()['id']
  audit(c,a['name'],'payment_recorded',json.dumps({'id':payment_id,'organization_id':ident,'amount':amount,'package':package,'months':months},ensure_ascii=False))
  return {'saved':True,'id':payment_id}
 if r.startswith('export/') and m=='GET':
  kind=r[7:]; limit=5000
  if kind=='organizations':
   columns=[['id','الرقم'],['name','المؤسسة'],['phone','الجوال'],['owner_name','المالك'],['owner_username','اسم الدخول'],['package','الباقة'],['expires_at','ينتهي الاشتراك'],['suspended','موقوفة'],['created_at','تاريخ التسجيل']]
   data=rows(c,"SELECT o.id,o.name,o.phone,u.name owner_name,u.username owner_username,COALESCE(s.package,'free') package,s.expires_at,COALESCE(z.suspended,0) suspended,o.created_at FROM organizations o LEFT JOIN account_organizations ao ON ao.organization_id=o.id LEFT JOIN owner_accounts acc ON acc.id=ao.account_id LEFT JOIN users u ON u.id=acc.owner_user_id LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_org_state z ON z.organization_id=o.id WHERE o.archived_at IS NULL ORDER BY o.id DESC LIMIT ?",(limit,))
  elif kind=='payments':
   columns=[['id','الرقم'],['created_at','التاريخ'],['organization_name','المؤسسة'],['package','الباقة'],['months','الأشهر'],['amount','المبلغ ر.س'],['discount_code','كود الخصم'],['source','المصدر'],['note','ملاحظة'],['approved_by','اعتمدها']]
   data=rows(c,'SELECT p.id,p.created_at,o.name organization_name,p.package,p.months,p.amount,p.discount_code,p.source,p.note,p.approved_by FROM platform_payments p LEFT JOIN organizations o ON o.id=p.organization_id ORDER BY p.created_at DESC,p.id DESC LIMIT ?',(limit,))
  elif kind=='expenses':
   columns=[['id','الرقم'],['provider','المزود'],['service','الخدمة'],['invoice_number','رقم الفاتورة'],['subtotal','قبل الضريبة'],['tax','الضريبة'],['total','الإجمالي'],['issued_at','الإصدار'],['due_at','الاستحقاق'],['status','الحالة'],['paid_at','تاريخ الدفع'],['payment_method','طريقة الدفع'],['notes','ملاحظات']]
   data=rows(c,'SELECT id,provider,service,invoice_number,subtotal,tax,total,issued_at,due_at,status,paid_at,payment_method,notes FROM platform_expenses ORDER BY issued_at DESC,id DESC LIMIT ?',(limit,))
  elif kind=='audit':
   columns=[['id','الرقم'],['created_at','الوقت'],['actor','المسؤول'],['action','الإجراء'],['target','الهدف']]
   data=rows(c,'SELECT id,created_at,actor,action,target FROM platform_audit ORDER BY id DESC LIMIT ?',(limit,))
  else: raise s.ApiError(404,'نوع التصدير غير معروف')
  audit(c,a['name'],'data_export',kind+' rows='+str(len(data)))
  return {'kind':kind,'columns':columns,'rows':data,'truncated':len(data)>=limit,'filename':'khdoom-'+kind+'-'+stamp()[:10]+'.csv'}
 if r=='expenses' and m=='POST':
  provider=str(d.get('provider','')).strip()[:160]; service=str(d.get('service','other')).strip()[:40]
  issued=str(d.get('issued_at','')).strip()[:40]; due=str(d.get('due_at','')).strip()[:40]
  if not provider or not issued or not due: raise ValueError('المزود وتاريخ الإصدار والاستحقاق مطلوبة')
  try: issued=datetime.fromisoformat(issued[:10]).date().isoformat(); due=datetime.fromisoformat(due[:10]).date().isoformat()
  except ValueError: raise ValueError('اكتب تاريخ الإصدار والاستحقاق بصيغة سنة-شهر-يوم مثل 2026-10-15')
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
  ident=int(r.split('/')[1]); reason=str(d.get('reason','')).strip()[:500]; batch=d.get('adjustments')
  if isinstance(batch,dict):
   # عدة خدمات في حفظ واحد: إما تُحفظ كلها أو لا يُحفظ شيء.
   if any(k not in SERVICE_LABELS for k in batch): raise ValueError('اختر خدمة صحيحة')
   changes=[(k,number(batch[k],-1000000,1000000,True)) for k in SERVICE_LABELS if batch.get(k) not in (None,'')]
   changes=[(k,u) for k,u in changes if u]
  else:
   service=str(d.get('service','')).strip()
   if service not in SERVICE_LABELS: raise ValueError('اختر خدمة صحيحة')
   changes=[(service,number(d.get('units'),-1000000,1000000,True))]
   changes=[(k,u) for k,u in changes if u]
  if not changes or not reason: raise ValueError('اكتب كمية غير صفرية وسبب التعديل')
  if not c.execute('SELECT id FROM organizations WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'المؤسسة غير موجودة')
  for service,units in changes:
   c.execute('INSERT INTO platform_credit_ledger(organization_id,service,units,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(ident,service,units,reason,a['name'],stamp()))
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
 if r=='support/new' and m=='GET':
  # تنبيه إدارة خدووم بالشكاوى الجديدة: ما بعد آخر شكوى رآها المدير، أو آخر 48 ساعة عند أول فتح.
  ensure_owner_tables(c,s,('support_tickets',))
  latest=c.execute('SELECT COALESCE(MAX(id),0) n FROM support_tickets').fetchone()['n']
  select="SELECT t.id,t.reference_code,t.title,t.category,t.status,t.created_at,o.name organization_name FROM support_tickets t JOIN organizations o ON o.id=t.organization_id WHERE "
  if str(q.get('after','')).isdigit(): found=rows(c,select+'t.id>? ORDER BY t.id DESC LIMIT 20',(int(q['after']),)); count=scalar(c,'SELECT COUNT(*) n FROM support_tickets WHERE id>?',(int(q['after']),))
  else:
   since=(datetime.now(timezone.utc)-timedelta(hours=48)).isoformat()
   found=rows(c,select+'t.created_at>=? ORDER BY t.id DESC LIMIT 20',(since,)); count=scalar(c,'SELECT COUNT(*) n FROM support_tickets WHERE created_at>=?',(since,))
  for x in found:
   if not x['reference_code']: x['reference_code']=support_reference(x['id'],x.get('created_at'))
  return {'latest_id':int(latest or 0),'count':int(count or 0),'items':found}
 if r=='support' and m=='GET':
  # Every support request must have a visible, organization-scoped AI follow-up.
  # Older requests are repaired here as well, without altering their complaint.
  ensure_owner_tables(c,s,('support_tickets','technical_tasks','technical_agent_state','platform_notes','support_ticket_events'))
  args=[]; where='FROM support_tickets t JOIN organizations o ON o.id=t.organization_id LEFT JOIN subscriptions s ON s.organization_id=o.id LEFT JOIN platform_admins pa ON pa.id=t.assigned_admin_id WHERE 1=1'
  # Backfill readable references for legacy requests without changing their internal IDs.
  for legacy in rows(c, "SELECT id,created_at FROM support_tickets WHERE reference_code='' OR reference_code IS NULL"):
   c.execute('UPDATE support_tickets SET reference_code=? WHERE id=?',(support_reference(legacy['id'],legacy.get('created_at')),legacy['id']))
  if q.get('status')=='dev':
   # شكاوى خلل في التطبيق محوّلة للتطوير: انتهى النقاش فيها مع المشترك وتنتظر صدور التحديث.
   where+=" AND t.status NOT IN ('resolved','closed') AND EXISTS (SELECT 1 FROM technical_tasks k WHERE k.support_ticket_id=t.id AND k.problem_type='app_bug')"
  elif q.get('status'): where+=' AND t.status=?'; args.append(q['status'])
  # شكاوى VIP المفتوحة أولًا حتى لا يتأخر الرد عليها، ثم الأحدث.
  out=paged(c,'SELECT t.*,o.name organization_name,s.package,pa.name assigned_admin_name',where,args,"CASE WHEN s.package='vip' AND t.status NOT IN ('resolved','closed') THEN 0 ELSE 1 END,t.id DESC",page)
  ids=[t['id'] for t in out['items']]; marks=','.join('?' for _ in ids)
  task_columns='id,support_ticket_id,status,diagnosis,proposal,action_taken,result,started_at,finished_at,knowledge,problem_type,needs_owner,suggested_action'
  # مهام ومتابعات وملاحظات كل الطلبات المعروضة تُجلب دفعة واحدة بدل استعلامات لكل طلب.
  tasks={}
  if ids:
   for row in rows(c,f'SELECT {task_columns} FROM technical_tasks WHERE support_ticket_id IN ({marks}) ORDER BY id DESC',ids): tasks.setdefault(row['support_ticket_id'],row)
  for t in out['items']:
   # الصورة المرفقة لا تُرسل مع القائمة (قد تكون كبيرة)؛ تُطلب عند الضغط على «عرض الصورة».
   t['has_attachment']=bool(t.pop('attachment_data','') or '')
   task=tasks.get(t['id'])
   if not task:
    # مهام قديمة قبل إضافة عمود الربط كانت تُعرف من نص المشكلة.
    legacy=c.execute(f"SELECT {task_columns} FROM technical_tasks WHERE support_ticket_id IS NULL AND service='support' AND problem LIKE ? ORDER BY id DESC LIMIT 1",(f"طلب دعم #{t['id']}:%",)).fetchone()
    task=dict(legacy) if legacy else None
   if not task:
    ts=stamp()
    cur=c.execute('INSERT INTO technical_tasks(organization_id,branch_id,user_id,support_ticket_id,service,problem,severity,status,diagnosis,proposal,action_taken,started_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id',(t['organization_id'],str(t.get('branch_id') or '')[:120],t.get('user_id'),t['id'],'support',f"طلب دعم #{t['id']}: {t['category']} — {t['message']}",'medium','queued','تم استلام الشكوى وتحويلها إلى موظف AI للتشخيص الآمن','فحص السجلات والصلاحيات والربط المرتبطة بالمؤسسة، دون تغيير بيانات الإنتاج','بانتظار بدء الفحص',ts,'support-repair'))
    task=dict(c.execute(f'SELECT {task_columns} FROM technical_tasks WHERE id=?',(cur.fetchone()['id'],)).fetchone())
   if task['status']=='queued':
    ts=stamp(); reply='تم استلام طلبك وإسناده إلى موظف التقنية AI. بدأ الفحص الأولي، وسيظهر التقرير هنا عند اكتماله.'
    c.execute("UPDATE technical_tasks SET status='diagnosing',action_taken=?,started_at=?,finished_at=NULL WHERE id=?",('بدأ موظف التقنية AI الفحص الأولي الآمن',ts,task['id']))
    if t['status'] in ('open','under_review'):
     applied=automated_ticket_update(c,t['id'],'in_progress',reply,ts)
     support_event(c,t,actor_type='technical_ai',actor_name='موظف التقنية AI',event_type='technical_assigned',body=reply,from_status=t['status'],to_status=applied)
    task=dict(c.execute(f'SELECT {task_columns} FROM technical_tasks WHERE id=?',(task['id'],)).fetchone())
   t['technical_task']={k:task.get(k) for k in ('id','status','diagnosis','proposal','action_taken','result','started_at','finished_at','knowledge','problem_type','needs_owner')}
   try: t['technical_task']['suggested_action']=json.loads(task.get('suggested_action') or 'null') if task.get('status') not in ('completed','failed','not_executed') else None
   except ValueError: t['technical_task']['suggested_action']=None
   t['technical_task_id']=task['id']
   t['technical_status']=task['status']
   t['notes']=[]; t['events']=[]
  if ids:
   by_id={t['id']:t for t in out['items']}
   for note in rows(c,f'SELECT ticket_id,note,actor,created_at FROM platform_notes WHERE ticket_id IN ({marks}) ORDER BY id DESC',ids):
    bucket=by_id[note['ticket_id']]['notes']
    if len(bucket)<20: bucket.append({k:note[k] for k in ('note','actor','created_at')})
   for event in rows(c,f'SELECT ticket_id,actor_type,actor_name,event_type,from_status,to_status,body,created_at FROM support_ticket_events WHERE ticket_id IN ({marks}) ORDER BY id DESC',ids):
    bucket=by_id[event['ticket_id']]['events']
    if len(bucket)<50: bucket.append({k:event[k] for k in ('actor_type','actor_name','event_type','from_status','to_status','body','created_at')})
  return out
 if r=='support/dev-fixed' and m=='POST':
  # صدر تحديث يعالج الخلل: تُغلق الشكاوى المحوّلة للتطوير (كلها أو المحددة) ويُبلَّغ أصحابها.
  ensure_owner_tables(c,s,('support_tickets','technical_tasks','support_ticket_events'))
  wanted=d.get('ids'); message=str(d.get('message','')).strip()[:1000] or 'تم إصلاح الملاحظة التي بلّغت عنها في تحديث جديد للتطبيق. حدّث التطبيق من المتجر، وإذا بقيت المشكلة أرسل لنا من جديد.'
  found=rows(c,"SELECT t.* FROM support_tickets t WHERE t.status NOT IN ('resolved','closed') AND EXISTS (SELECT 1 FROM technical_tasks k WHERE k.support_ticket_id=t.id AND k.problem_type='app_bug') ORDER BY t.id")
  if isinstance(wanted,list): found=[t for t in found if t['id'] in {int(x) for x in wanted if str(x).isdigit()}]
  ts=stamp()
  for t in found:
   c.execute("UPDATE support_tickets SET status='resolved',owner_reply=?,owner_reply_by='admin',updated_at=? WHERE id=?",(message,ts,t['id']))
   c.execute("UPDATE technical_tasks SET status='completed',approved_by=?,action_taken=?,result=?,finished_at=?,needs_owner=0 WHERE support_ticket_id=? AND problem_type='app_bug'",(a['name'],'أُصلح الخلل في تحديث للتطبيق','أُبلغ المشترك بصدور التحديث وأُغلق الطلب.',ts,t['id']))
   support_event(c,t,actor_type='admin',actor_name=a['name'],event_type='status_changed',body=message,from_status=t['status'],to_status='resolved')
   s.audit_log(c,t['organization_id'],None,'support_ticket_updated','رد إدارة خدووم على طلب الدعم '+str(t.get('reference_code') or ('#'+str(t['id'])))+': تم الإصلاح في تحديث جديد','security',str(t['id']))
  if found: audit(c,a['name'],'support_dev_fixed',json.dumps({'tickets':[t['id'] for t in found]},ensure_ascii=False))
  return {'saved':True,'count':len(found),'message':'أُغلقت '+str(len(found))+' شكوى وأُبلغ أصحابها بصدور التحديث.' if found else 'لا توجد شكاوى محوّلة للتطوير.'}
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
  automated_ticket_update(c,ident,'in_progress','تم استلام طلبك وتحويله إلى الموظف التقني AI. جاري فحص المشكلة، وسيتم إبلاغك بالنتيجة بعد اكتمال المتابعة.',ts,force_status=True)
  fresh=dict(c.execute('SELECT id,organization_id,user_id FROM support_tickets WHERE id=?',(ident,)).fetchone())
  support_event(c,fresh,actor_type='admin',actor_name=a['name'],event_type='technical_followup',body='تم إرسال الطلب للمتابعة مع الموظف التقني AI',from_status='open',to_status='in_progress')
  c.execute('INSERT INTO platform_notes(ticket_id,note,actor,created_at) VALUES(?,?,?,?)',(ident,'تم إرسال الطلب للمتابعة مع الموظف التقني AI','لوحة أمن خدووم',ts))
  audit(c,a['name'],'support_technical_followup',json.dumps({'ticket_id':ident,'task_id':task_id},ensure_ascii=False))
  return {'saved':True,'taskId':task_id,'message':'تم إرسال الطلب للمتابعة مع الموظف التقني AI'}
 if re.fullmatch(r'support/\d+/attachment',r) and m=='GET':
  found=c.execute('SELECT attachment_data FROM support_tickets WHERE id=?',(int(r.split('/')[1]),)).fetchone()
  if not found or not found['attachment_data']: raise s.ApiError(404,'لا توجد صورة مرفقة بهذه الشكوى')
  return {'image':found['attachment_data']}
 if re.fullmatch(r'support/\d+',r) and m=='DELETE':
  ident=int(r.split('/')[1]); require_senior(a,s,'حذف طلب الدعم نهائيًا')
  if not c.execute('SELECT id FROM support_tickets WHERE id=?',(ident,)).fetchone(): raise s.ApiError(404,'طلب الدعم غير موجود')
  gone=c.execute('SELECT organization_id,category,status FROM support_tickets WHERE id=?',(ident,)).fetchone()
  c.execute('DELETE FROM platform_notes WHERE ticket_id=?',(ident,)); c.execute('DELETE FROM support_tickets WHERE id=?',(ident,)); audit(c,a['name'],'support_ticket_deleted',json.dumps({'ticket_id':ident,'organization_id':gone['organization_id'],'category':gone['category'],'status':gone['status']},ensure_ascii=False)); return {'deleted':True}
 if re.fullmatch(r'support/\d+',r) and m=='PUT':
  ident=int(r.split('/')[1]); status=d.get('status')
  if status not in ('open','under_review','in_progress','awaiting_user','resolved','closed'): raise ValueError('حالة غير صحيحة')
  ticket=c.execute('SELECT id,organization_id,user_id,status,scope,owner_reply FROM support_tickets WHERE id=?',(ident,)).fetchone()
  if not ticket: raise s.ApiError(404,'طلب الدعم غير موجود')
  previous_reply=str(ticket['owner_reply'] or '')
  reply=str(d.get('owner_reply',''))[:1000]; note=str(d.get('note',''))[:2000]
  last_error=str(d.get('last_error',''))[:1000]
  scope=str(d.get('scope') or ticket['scope'] or 'private')
  if scope not in ('private','global'): raise ValueError('نطاق المشكلة غير صحيح')
  assigned=d.get('assigned_admin_id') or a['id']
  c.execute('UPDATE support_tickets SET status=?,scope=?,last_error=?,owner_reply=?,owner_reply_by=?,assigned_admin_id=COALESCE(?,assigned_admin_id),updated_at=? WHERE id=?',(status,scope,last_error,reply,'admin' if reply.strip() else '',assigned,stamp(),ident))
  event_type='scope_changed' if scope!=ticket['scope'] else 'status_changed' if status!=ticket['status'] else 'reply_updated'
  support_event(c,dict(ticket),actor_type='admin',actor_name=a['name'],event_type=event_type,body=note or reply or last_error,from_status=ticket['status'],to_status=status)
  # رد جديد من الإدارة يظهر للمشترك تنبيهًا في جرس التطبيق.
  if reply.strip() and reply.strip()!=previous_reply.strip():
   s.audit_log(c,ticket['organization_id'],None,'support_ticket_updated','رد إدارة خدووم على طلب الدعم #'+str(ident),'security',str(ident))
  learned=None
  if status in ('resolved','closed') and (reply.strip() or note.strip()):
   import technical_support
   full=c.execute('SELECT id,organization_id,category,title,message FROM support_tickets WHERE id=?',(ident,)).fetchone()
   learned=technical_support.learn_from_resolution(c,dict(full),note.strip() or reply.strip(),a['name'],__import__(__name__),s)
   if learned: audit(c,a['name'],'technical_playbook_learned',json.dumps({'ticket_id':ident,'playbook_id':learned},ensure_ascii=False))
  if scope=='global' and ticket['scope']!='global':
   c.execute('INSERT INTO technical_incidents(service,organization_id,problem,root_cause,proposal,severity,test_status,deployment_status,affected_organizations,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('support',None,'عطل عام معلن من مركز الدعم','تحتاج عدة مؤسسات إلى مراجعة موحدة؛ لا يتم إصلاح الإنتاج تلقائيًا','فحص آمن ثم اختبار وموافقة قبل أي تطبيق عام','high','not_tested','proposed',0,stamp(),stamp()))
  if note: c.execute('INSERT INTO platform_notes(ticket_id,note,actor,created_at) VALUES(?,?,?,?)',(ident,note,a['name'],stamp()))
  return {'saved':True,'learned':bool(learned)}
 if r=='security/login-alerts' and m=='GET':
  latest=c.execute("SELECT a.id,a.created_at,o.name organization_name,u.name user_name,a.summary FROM audit_logs a JOIN organizations o ON o.id=a.organization_id LEFT JOIN users u ON u.id=a.actor_user_id WHERE a.action IN ('new_device','login') ORDER BY a.id DESC LIMIT 1").fetchone()
  return {'latest':dict(latest) if latest else None}
 if r=='security' and m=='GET':
  for table,keep_days in (('audit_logs',180),('platform_audit',400),('platform_login_events',90),('platform_unknown_logins',90)):
   if table_exists(c,table,s): c.execute('DELETE FROM '+table+' WHERE created_at<?',((datetime.now(timezone.utc)-timedelta(days=keep_days)).isoformat(),))
  c.execute('DELETE FROM sessions WHERE expires_at<?',(stamp(),)); c.execute('DELETE FROM platform_sessions WHERE expires_at<?',(stamp(),))
  category=q.get('type','login')
  if category=='audit': return paged(c,'SELECT *','FROM platform_audit',[],'id DESC',page)
  if category=='devices': return paged(c,'SELECT se.device_name,se.device_id,se.token_hash session_id,se.last_seen_at,se.trusted,u.name,u.organization_id','FROM sessions se JOIN users u ON u.id=se.user_id WHERE se.expires_at>?',[stamp()],'se.created_at DESC',page)
  if category=='password':
   return paged(c,'SELECT *',"FROM (SELECT summary,created_at,'customer_account' action FROM audit_logs WHERE action='password_reset' UNION ALL SELECT target summary,created_at,'admin_account' action FROM platform_audit WHERE action='password_reset') resets",[],'created_at DESC',page)

  actions="'login','failed_login','new_device'" if category=='login' else "'failed_login','new_device','blocked_device_login','owner_account_status','suspicious_login'"
  out=paged(c,'SELECT a.action,a.summary,a.created_at,o.name organization_name,a.organization_id',f'FROM audit_logs a JOIN organizations o ON o.id=a.organization_id WHERE a.action IN ({actions})',[],'a.id DESC',page); out['unknownLogins']=rows(c,'SELECT account,created_at FROM platform_unknown_logins ORDER BY id DESC LIMIT 30');out['adminLogins']=rows(c,'SELECT account,success,created_at FROM platform_login_events ORDER BY id DESC LIMIT 30'); return out
 if r=='usage' and m=='GET': return usage(c,q,page,s)
 if r=='ads/live' and m=='GET':
  import ad_policy
  ensure_owner_tables(c,s,('advertisements','platform_advertisements'))
  live=ad_policy.public_ads(c,stamp())
  return [ad_images_as_refs(ad) for ad in live] if q.get('images')=='ref' else live
 if r=='ads' and m=='GET':
  ensure_owner_tables(c,s,('advertisements',))
  condition="CASE WHEN a.approved=1 AND a.expires_at IS NOT NULL AND a.expires_at<=? THEN 'expired' WHEN a.active=0 AND a.approved=0 THEN 'rejected' WHEN a.active=0 THEN 'stopped' WHEN a.approved=0 THEN 'pending' WHEN a.scheduled_at>? THEN 'scheduled' ELSE 'published' END"
  where='FROM advertisements a JOIN organizations o ON o.id=a.organization_id WHERE COALESCE(a.deleted,0)=0'; args=[stamp(),stamp()]
  # Status expression is in the projection; use a subquery for pagination and filtering.
  src='FROM (SELECT a.*,o.name organization_name,'+condition+' status '+where+') ads WHERE 1=1'
  if q.get('status'): src+=' AND status=?'; args.append(q['status'])
  out=paged(c,'SELECT *',src,args,'id DESC',page)
  if q.get('images')=='ref': out['items']=[ad_images_as_refs(ad) for ad in out['items']]
  return out
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
  status=d.get('status'); start=date(d.get('scheduled_at',old['scheduled_at'])); end=date(d.get('expires_at',old['expires_at']))
  if status not in ('published','scheduled','rejected','stopped','pending') or (start and end and end<=start): raise ValueError('تحقق من الحالة والتاريخ')
  seconds=number(d.get('display_seconds',old['display_seconds'] if 'display_seconds' in old.keys() else 8),3,60,True)
  previous=old['banner_config'] if 'banner_config' in old.keys() else '{}'
  try: previous=json.loads(previous) if isinstance(previous,str) else dict(previous or {})
  except (TypeError,ValueError): previous={}
  config=d.get('banner_config',previous)
  if isinstance(config,str):
   try: config=json.loads(config or '{}')
   except (TypeError,ValueError): raise ValueError('إعدادات تصميم الشريط غير صحيحة')
  if not isinstance(config,dict): raise ValueError('إعدادات تصميم الشريط غير صحيحة')
  config={**previous,**config}
  for key in ('textColor','barColor','textAlign','logoPosition','fontSize','logoScale','height','textX','textY','logoX','logoY'):
   if key in d and d[key] not in (None,''): config[key]=d[key]
  safe={key:config.get(key) for key in ('textColor','barColor','textAlign','logoPosition','fontSize','logoScale','height','textX','textY','messageX','messageY','logoX','logoY','bannerX','bannerY','bannerWidth','bannerHeight','adType','mode','bannerImageData','banner_image_data','imageWidth','imageHeight','textLayers') if key in config}
  if safe.get('adType') not in (None,'text','image'): raise ValueError('نوع الإعلان غير صحيح')
  for image_key in ('bannerImageData','banner_image_data'):
   value=safe.get(image_key)
   if value and (not isinstance(value,str) or len(value)>850000 or not value.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,'))): raise ValueError('صيغة صورة الإعلان غير مدعومة أو حجمها كبير')
  if safe.get('textAlign') not in (None,'right','center','left') or safe.get('logoPosition') not in (None,'right','center','left'): raise ValueError('موضع التصميم غير صحيح')
  for key,low,high in (('fontSize',12,32),('logoScale',0.5,1.5),('height',44,180),('textX',0.08,0.92),('textY',0.15,0.85),('logoX',0.08,0.92),('logoY',0.15,0.85)):
   if key in safe: safe[key]=number(safe[key],low,high,False)
  for key in ('textColor','barColor'):
   if key in safe and safe[key] is not None and not re.fullmatch(r'#[0-9A-Fa-f]{6}',str(safe[key])): raise ValueError('لون التصميم غير صحيح')
  title=str(d.get('title',old['title']))[:120]; message=str(d.get('message',old['message']))[:1000]; contact=str(d.get('contact',old['contact']))[:80]; image=str(d.get('image_data','')) or str(old['image_data'] if 'image_data' in old.keys() else '')
  if image and (len(image)>850000 or not image.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,'))): raise ValueError('صيغة الصورة غير مدعومة أو حجمها كبير')
  active=int(status not in ('rejected','stopped')); approved=int(status in ('published','scheduled','stopped'))
  previous_published=old['published_at'] if 'published_at' in old.keys() else None
  published_at=(previous_published or stamp()) if status in ('published','scheduled') else None if status=='pending' else previous_published
  previous_actor=old['published_by'] if 'published_by' in old.keys() else ''
  published_by=(previous_actor or a['name']) if status in ('published','scheduled') else '' if status=='pending' else previous_actor
  approved_at=old['approved_at'] if old['approved'] else stamp()
  c.execute('UPDATE advertisements SET title=?,message=?,contact=?,image_data=?,active=?,approved=?,scheduled_at=?,expires_at=?,approved_at=?,published_at=?,published_by=?,review_note=?,display_seconds=?,banner_config=? WHERE id=?',(title,message,contact,image,active,approved,start,end,approved_at,published_at,published_by,str(d.get('review_note',old['review_note'] or ''))[:500],seconds,json.dumps(safe,ensure_ascii=False),ident))
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


