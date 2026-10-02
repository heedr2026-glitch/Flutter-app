"""Driver schedules enforced by the server as well as the phone."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets

RIYADH=timezone(timedelta(hours=3))


def migrate(c, postgres=False):
    c.execute('''CREATE TABLE IF NOT EXISTS vehicle_tracking_schedules(
        organization_id BIGINT NOT NULL,vehicle_key TEXT NOT NULL,vehicle_name TEXT NOT NULL,
        branch_id TEXT NOT NULL DEFAULT 'main',driver_user_id BIGINT NOT NULL,
        weekdays TEXT NOT NULL,start_minute INTEGER NOT NULL,end_minute INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1,
        consent_revision INTEGER NOT NULL DEFAULT 0,source_session_hash TEXT NOT NULL DEFAULT '',
        consent_at TEXT,last_heartbeat TEXT,phone_status TEXT NOT NULL DEFAULT 'not_enabled',updated_at TEXT NOT NULL,
        PRIMARY KEY(organization_id,vehicle_key))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_vehicle_driver ON vehicle_tracking_schedules(organization_id,driver_user_id,enabled)')
    # ربط السائق بالباركود بدون حساب: كود المركبة، بصمة توكن الجوال، واسم السائق.
    existing=set() if postgres else {row[1] for row in c.execute('PRAGMA table_info(vehicle_tracking_schedules)')}
    for name,ddl in (('link_code',"TEXT NOT NULL DEFAULT ''"),('device_hash',"TEXT NOT NULL DEFAULT ''"),('driver_name',"TEXT NOT NULL DEFAULT ''"),('linked_at','TEXT'),('link_revision','INTEGER NOT NULL DEFAULT 0')):
        if postgres: c.execute('ALTER TABLE vehicle_tracking_schedules ADD COLUMN IF NOT EXISTS '+name+' '+ddl)
        elif name not in existing: c.execute('ALTER TABLE vehicle_tracking_schedules ADD COLUMN '+name+' '+ddl)
    c.execute('CREATE INDEX IF NOT EXISTS idx_vehicle_link_code ON vehicle_tracking_schedules(link_code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_vehicle_device ON vehicle_tracking_schedules(device_hash)')


def in_schedule(schedule, at=None):
    at=(at or datetime.now(timezone.utc)).astimezone(RIYADH)
    if not schedule['enabled']: return False
    days=json.loads(schedule['weekdays']);minute=at.hour*60+at.minute
    start,end=schedule['start_minute'],schedule['end_minute']
    if start<end: return at.isoweekday() in days and start<=minute<end
    return (at.isoweekday() in days and minute>=start) or ((at-timedelta(days=1)).isoweekday() in days and minute<end)


def session_hash(headers):
    return hashlib.sha256(headers.get('Authorization','')[7:].encode()).hexdigest()


def public_schedule(row):
    if not row: return None
    out=dict(row);out.pop('source_session_hash',None);out.pop('device_hash',None);out.pop('link_code',None)
    out['weekdays']=json.loads(out['weekdays'])
    out['inSchedule']=in_schedule(dict(row))
    out['timezone']='Asia/Riyadh';out['serverTime']=datetime.now(timezone.utc).isoformat()
    return out


def assignment(c,user):
    return c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND driver_user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 1',(user['organization_id'],user['id'])).fetchone()


LINK_ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
DEVICE_PREFIX='kdrv_'
LINK_URL='https://khdoom-api.onrender.com/driver?c='


def ensure_link_code(c,org,key,force=False):
    row=c.execute('SELECT link_code FROM vehicle_tracking_schedules WHERE organization_id=? AND vehicle_key=?',(org,key)).fetchone()
    if row and row['link_code'] and not force: return row['link_code']
    while True:
        code=''.join(secrets.choice(LINK_ALPHABET) for _ in range(8))
        if not c.execute('SELECT 1 FROM vehicle_tracking_schedules WHERE link_code=?',(code,)).fetchone(): break
    c.execute('UPDATE vehicle_tracking_schedules SET link_code=? WHERE organization_id=? AND vehicle_key=?',(code,org,key))
    return code


def owner_schedule(row):
    """عرض المالك: يضيف كود الباركود واسم السائق المرتبط؛ بصمة التوكن لا تُعرض أبدًا."""
    out=public_schedule(row)
    if not out: return None
    code=row['link_code'] or ''
    out.update({'linkCode':code,'linkUrl':LINK_URL+code if code else '','linked':bool(row['device_hash']),'driverName':row['driver_name'] or '','linkedAt':row['linked_at']})
    return out


def normalize_code(value):
    text=str(value or '').strip()
    if 'c=' in text: text=text.split('c=')[-1]
    return re.sub(r'[^A-Z0-9]','',text.upper())[:16]


def device_token_hash(headers):
    return hashlib.sha256(headers.get('Authorization','')[7:].encode()).hexdigest()


def device_request(c, path, method, data, headers, server):
    """مسارات السائق بلا حساب: معاينة الباركود وقبوله، ثم طلبات الجوال بتوكن الجهاز."""
    if path in ('/api/driver-link/preview','/api/driver-link/claim') and method=='POST':
        code=normalize_code(data.get('code'))
        row=c.execute('SELECT * FROM vehicle_tracking_schedules WHERE link_code=?',(code,)).fetchone() if len(code)==8 else None
        if not row or not row['enabled'] or row['driver_user_id']!=0: raise server.ApiError(404,'الباركود غير صحيح أو أُلغي؛ اطلب من المؤسسة باركود المركبة الحالي')
        org=row['organization_id']
        if server.owner_admin.suspended(c,org): raise server.ApiError(403,'المؤسسة موقوفة')
        organization=c.execute('SELECT name FROM organizations WHERE id=?',(org,)).fetchone()
        summary={'vehicleName':row['vehicle_name'],'organizationName':organization['name'] if organization else '','weekdays':json.loads(row['weekdays']),
            'start_minute':row['start_minute'],'end_minute':row['end_minute'],'timezone':'Asia/Riyadh','hasDriver':bool(row['device_hash'])}
        if path.endswith('/preview'): return 200,summary
        name=re.sub(r'\s+',' ',str(data.get('driverName','')).strip())[:80]
        if len(name)<2: raise ValueError('اكتب اسمك ليعرف صاحب المؤسسة من يقود المركبة')
        if data.get('accepted') is not True: raise ValueError('يلزم موافقتك على تتبع موقع جوالك خلال الدوام')
        token=DEVICE_PREFIX+secrets.token_urlsafe(32)
        c.execute('UPDATE vehicle_tracking_schedules SET device_hash=?,driver_name=?,linked_at=?,consent_at=?,link_revision=link_revision+1,phone_status=?,last_heartbeat=NULL WHERE organization_id=? AND vehicle_key=?',
            (hashlib.sha256(token.encode()).hexdigest(),name,server.now(),server.now(),'armed',org,row['vehicle_key']))
        server.audit_log(c,org,None,'driver_tracking_consent','ربط السائق «'+name+'» جواله بالمركبة عبر الباركود ووافق على التتبع خلال الدوام','vehicle',row['vehicle_key'])
        summary.update({'token':token,'vehicleKey':row['vehicle_key'],'revision':row['link_revision']+1,'branchId':row['branch_id']})
        return 200,summary
    if not headers.get('Authorization','').startswith('Bearer '+DEVICE_PREFIX): raise server.ApiError(404,'المسار غير موجود')
    row=c.execute('SELECT * FROM vehicle_tracking_schedules WHERE device_hash=?',(device_token_hash(headers),)).fetchone()
    # 401 يجعل خدمة الجوال تتوقف وتمسح بياناتها عند إلغاء الربط أو ربط سائق آخر.
    if not row or row['driver_user_id']!=0: raise server.ApiError(401,'أُلغي ربط هذا الجوال بالمركبة؛ صوّر الباركود من جديد')
    org=row['organization_id'];key=row['vehicle_key']
    if server.owner_admin.suspended(c,org): raise server.ApiError(403,'المؤسسة موقوفة')
    if path=='/api/vehicle-tracking/assignment' and method=='GET':
        out=public_schedule(row);out['consented']=True;out['revision']=row['link_revision']
        return 200,out
    if path=='/api/vehicle-tracking/heartbeat' and method=='POST':
        state=data.get('status')
        if state not in ('armed','tracking','outside_schedule','location_disabled','permission_denied','stopped'): raise ValueError('حالة التتبع غير صحيحة')
        if state=='tracking' and not in_schedule(row): state='outside_schedule'
        if state=='stopped':
            server.audit_log(c,org,None,'driver_tracking_stopped','أوقف السائق «'+(row['driver_name'] or '')+'» تتبع جواله','vehicle',key)
            c.execute("UPDATE vehicle_tracking_schedules SET device_hash='' WHERE organization_id=? AND vehicle_key=?",(org,key))
        c.execute('UPDATE vehicle_tracking_schedules SET phone_status=?,last_heartbeat=? WHERE organization_id=? AND vehicle_key=?',(state,server.now(),org,key))
        return 200,{'saved':True}
    if path=='/api/vehicle-tracking' and method=='POST':
        if str(data.get('vehicleKey','')).strip()[:160]!=key: raise server.ApiError(403,'هذا الجوال مرتبط بمركبة أخرى')
        if not in_schedule(row): raise server.ApiError(403,'التتبع متوقف خارج جدول الدوام')
        try: at=datetime.fromisoformat(str(data.get('capturedAt','')).replace('Z','+00:00'))
        except ValueError: raise ValueError('وقت التقاط الموقع مطلوب')
        if at.tzinfo is None: raise ValueError('وقت الموقع يجب أن يتضمن المنطقة الزمنية')
        age=(datetime.now(timezone.utc)-at).total_seconds()
        if not -30<=age<=120 or not in_schedule(row,at): raise ValueError('الموقع قديم أو التُقط خارج الدوام')
        latitude=float(data.get('latitude'));longitude=float(data.get('longitude'));accuracy=float(data.get('accuracyMeters',0) or 0)
        if not (-90<=latitude<=90 and -180<=longitude<=180 and 0<=accuracy<=100000): raise ValueError('بيانات تتبع المركبة غير صحيحة')
        stamp=server.now()
        c.execute('INSERT INTO vehicle_location_events(organization_id,vehicle_key,latitude,longitude,accuracy_meters,recorded_at,user_id,created_at) VALUES(?,?,?,?,?,?,NULL,?)',(org,key,latitude,longitude,accuracy,stamp,stamp))
        return 201,{'saved':True,'vehicleKey':key,'recordedAt':stamp}
    raise server.ApiError(403,'هذا الطلب غير متاح لجوال السائق')


def handle(c, path, method, data, query, user, headers, server):
    org=user['organization_id'];branch=user.get('current_branch')
    if path=='/api/vehicle-tracking/drivers' and method=='GET':
        if user['role']!='admin': raise server.ApiError(403,'إدارة السائقين متاحة للمالك فقط')
        # Names and IDs only; credentials and account permissions are never exposed.
        return [dict(r) for r in c.execute('SELECT id,name FROM users WHERE organization_id=? AND active=1 AND archived_at IS NULL ORDER BY name',(org,))]
    if path=='/api/vehicle-tracking/assignment' and method=='GET':
        row=assignment(c,user);out=public_schedule(row)
        if out: out['consented']=row['consent_revision']==row['revision'] and row['source_session_hash']==session_hash(headers)
        return out or {'status':'not_assigned'}
    if path=='/api/vehicle-tracking/consent' and method=='POST':
        row=assignment(c,user)
        if not row: raise server.ApiError(404,'لا توجد مركبة مرتبطة بحسابك')
        if data.get('accepted') is not True: raise ValueError('يلزم موافقة السائق على تتبع موقع جواله خلال الدوام')
        if data.get('revision')!=row['revision']: raise server.ApiError(409,'تغير الجدول؛ راجعه قبل الموافقة')
        c.execute('UPDATE vehicle_tracking_schedules SET consent_revision=revision,source_session_hash=?,consent_at=?,phone_status=? WHERE organization_id=? AND vehicle_key=?',(session_hash(headers),server.now(),'armed',org,row['vehicle_key']))
        server.audit_log(c,org,user['id'],'driver_tracking_consent','وافق السائق على تتبع جواله ضمن الجدول المحدد','vehicle',row['vehicle_key'])
        return {'saved':True}
    if path=='/api/vehicle-tracking/heartbeat' and method=='POST':
        row=assignment(c,user)
        if not row: raise server.ApiError(404,'لا توجد مركبة مرتبطة')
        if row['source_session_hash']!=session_hash(headers): raise server.ApiError(403,'أعد تفعيل التتبع من هذا الجوال')
        state=data.get('status')
        if state not in ('armed','tracking','outside_schedule','location_disabled','permission_denied','stopped'): raise ValueError('حالة التتبع غير صحيحة')
        if state=='tracking' and not in_schedule(row): state='outside_schedule'
        if state=='stopped':
            server.audit_log(c,org,user['id'],'driver_tracking_stopped','أوقف السائق تتبع جواله','vehicle',row['vehicle_key'])
            c.execute("UPDATE vehicle_tracking_schedules SET source_session_hash='',consent_revision=0 WHERE organization_id=? AND vehicle_key=?",(org,row['vehicle_key']))
        c.execute('UPDATE vehicle_tracking_schedules SET phone_status=?,last_heartbeat=? WHERE organization_id=? AND vehicle_key=?',(state,server.now(),org,row['vehicle_key']))
        return {'saved':True}
    if path=='/api/vehicle-tracking/schedule':
        if user['role']!='admin': raise server.ApiError(403,'تحديد السائق وجدول الدوام متاح لمالك المؤسسة فقط')
        key=str(data.get('vehicleKey') if method=='PUT' else query.get('vehicleKey',[''])[0]).strip()
        if not key or len(key)>160: raise ValueError('حدد المركبة')
        old=c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND vehicle_key=?',(org,key)).fetchone()
        if old and branch and old['branch_id']!=branch: raise server.ApiError(403,'المركبة تتبع فرعًا آخر')
        if method=='GET': return owner_schedule(old) or {'vehicle_key':key,'status':'not_configured'}
        if method=='DELETE':
            if old:
                c.execute("UPDATE vehicle_tracking_schedules SET enabled=0,revision=revision+1,consent_revision=0,source_session_hash='',phone_status='stopped' WHERE organization_id=? AND vehicle_key=?",(org,key))
                server.audit_log(c,org,user['id'],'vehicle_tracking_schedule','إيقاف ربط المركبة بالتتبع','vehicle',key)
            return {'saved':True}
        if method=='PUT':
            driver=int(data.get('driverUserId') or 0);days=data.get('weekdays')
            if not isinstance(days,list) or not days or any(type(x)is not int or not 1<=x<=7 for x in days): raise ValueError('حدد أيام الدوام')
            def minutes(value):
                if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(value)): raise ValueError('وقت الدوام غير صحيح')
                h,m=map(int,value.split(':'));return h*60+m
            start,end=minutes(data.get('startTime')),minutes(data.get('endTime'))
            if start==end: raise ValueError('وقت البداية والنهاية لا يكونان متساويين')
            if not driver:
                # ربط بالباركود: السائق بلا حساب، وتعديل الدوام لا يفصل جواله المرتبط.
                enabled=int(data.get('enabled',True) is True);name=str(data.get('vehicleName','مركبة'))[:120]
                if old and old['driver_user_id']==0:
                    c.execute('UPDATE vehicle_tracking_schedules SET vehicle_name=?,weekdays=?,start_minute=?,end_minute=?,enabled=?,revision=revision+1,updated_at=? WHERE organization_id=? AND vehicle_key=?',
                        (name,json.dumps(sorted(set(days))),start,end,enabled,server.now(),org,key))
                else:
                    c.execute('''INSERT INTO vehicle_tracking_schedules(organization_id,vehicle_key,vehicle_name,branch_id,driver_user_id,weekdays,start_minute,end_minute,enabled,updated_at)
                        VALUES(?,?,?,?,0,?,?,?,?,?) ON CONFLICT(organization_id,vehicle_key) DO UPDATE SET vehicle_name=excluded.vehicle_name,driver_user_id=0,
                        weekdays=excluded.weekdays,start_minute=excluded.start_minute,end_minute=excluded.end_minute,enabled=excluded.enabled,
                        revision=vehicle_tracking_schedules.revision+1,consent_revision=0,source_session_hash='',phone_status='not_enabled',updated_at=excluded.updated_at''',
                        (org,key,name,branch or 'main',json.dumps(sorted(set(days))),start,end,enabled,server.now()))
                    c.execute("UPDATE vehicle_tracking_schedules SET device_hash='',driver_name='',linked_at=NULL WHERE organization_id=? AND vehicle_key=?",(org,key))
                ensure_link_code(c,org,key)
                server.audit_log(c,org,user['id'],'vehicle_tracking_schedule','تعديل دوام تتبع المركبة (ربط بالباركود)','vehicle',key)
                return {'saved':True}
            candidate=c.execute('SELECT id,role,permissions FROM users WHERE id=? AND organization_id=? AND active=1 AND archived_at IS NULL',(driver,org)).fetchone()
            if not candidate: raise ValueError('اختر سائقًا نشطًا من المؤسسة')
            if candidate['role']!='admin' and json.loads(candidate['permissions'] or '{}').get('branch_id','main')!=(branch or 'main'): raise ValueError('السائق يتبع فرعًا آخر')
            if data.get('enabled',True) is True and c.execute('SELECT 1 FROM vehicle_tracking_schedules WHERE organization_id=? AND driver_user_id=? AND enabled=1 AND vehicle_key<>?',(org,driver,key)).fetchone(): raise ValueError('السائق مرتبط بمركبة أخرى؛ أوقف ربطها أولًا')
            enabled=int(data.get('enabled',True) is True)
            c.execute('''INSERT INTO vehicle_tracking_schedules(organization_id,vehicle_key,vehicle_name,branch_id,driver_user_id,weekdays,start_minute,end_minute,enabled,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(organization_id,vehicle_key) DO UPDATE SET vehicle_name=excluded.vehicle_name,driver_user_id=excluded.driver_user_id,
                weekdays=excluded.weekdays,start_minute=excluded.start_minute,end_minute=excluded.end_minute,enabled=excluded.enabled,
                revision=vehicle_tracking_schedules.revision+1,consent_revision=0,source_session_hash='',phone_status='not_enabled',updated_at=excluded.updated_at''',
                (org,key,str(data.get('vehicleName','مركبة'))[:120],branch or 'main',driver,json.dumps(sorted(set(days))),start,end,enabled,server.now()))
            server.audit_log(c,org,user['id'],'vehicle_tracking_schedule','تعديل ربط السائق وجدول التتبع؛ يلزم موافقة السائق على الجدول الجديد','vehicle',key)
            return {'saved':True}
    if path in ('/api/vehicle-tracking/link/rotate','/api/vehicle-tracking/link/unlink') and method=='POST':
        if user['role']!='admin': raise server.ApiError(403,'إدارة باركود المركبة متاحة لمالك المؤسسة فقط')
        key=str(data.get('vehicleKey','')).strip()[:160]
        row=c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND vehicle_key=?',(org,key)).fetchone()
        if not row: raise server.ApiError(404,'احفظ دوام المركبة أولًا')
        if branch and row['branch_id']!=branch: raise server.ApiError(403,'المركبة تتبع فرعًا آخر')
        c.execute("UPDATE vehicle_tracking_schedules SET device_hash='',driver_name='',linked_at=NULL,phone_status='not_enabled',link_revision=link_revision+1 WHERE organization_id=? AND vehicle_key=?",(org,key))
        if path.endswith('/rotate'): ensure_link_code(c,org,key,force=True)
        server.audit_log(c,org,user['id'],'vehicle_tracking_link','تغيير باركود المركبة' if path.endswith('/rotate') else 'إلغاء ربط جوال السائق','vehicle',key)
        return owner_schedule(c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND vehicle_key=?',(org,key)).fetchone())
    raise ValueError('مسار التتبع غير صحيح')


def accept_location(c,user,headers,data,server):
    key=str(data.get('vehicleKey','')).strip()[:160]
    row=c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND vehicle_key=?',(user['organization_id'],key)).fetchone()
    if not row:
        # Keep the existing owner-only one-time location feature.
        if user['role']!='admin': raise server.ApiError(403,'يلزم ربط السائق بالمركبة أولًا')
        return
    if user.get('current_branch') and row['branch_id']!=user['current_branch']: raise server.ApiError(403,'المركبة تتبع فرعًا آخر')
    if row['driver_user_id']!=user['id']: raise server.ApiError(403,'تحديث الموقع متاح للسائق المرتبط فقط')
    if row['consent_revision']!=row['revision'] or row['source_session_hash']!=session_hash(headers): raise server.ApiError(403,'يلزم قبول جدول التتبع وتفعيله من هذا الجوال')
    if not in_schedule(row): raise server.ApiError(403,'التتبع متوقف خارج جدول الدوام')
    try: at=datetime.fromisoformat(str(data.get('capturedAt','')).replace('Z','+00:00'))
    except ValueError: raise ValueError('وقت التقاط الموقع مطلوب')
    if at.tzinfo is None: raise ValueError('وقت الموقع يجب أن يتضمن المنطقة الزمنية')
    age=(datetime.now(timezone.utc)-at).total_seconds()
    if not -30<=age<=120 or not in_schedule(row,at): raise ValueError('الموقع قديم أو التُقط خارج الدوام؛ اطلب موقعًا جديدًا')


def location_status(c,user,key,server):
    schedule=c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND vehicle_key=?',(user['organization_id'],key)).fetchone()
    if schedule and user.get('current_branch') and schedule['branch_id']!=user['current_branch']: raise server.ApiError(403,'المركبة تتبع فرعًا آخر')
    if user['role']!='admin':
        if not schedule or schedule['driver_user_id']!=user['id']: raise server.ApiError(403,'عرض الموقع متاح للمالك والسائق المرتبط فقط')
    location=c.execute('SELECT vehicle_key,latitude,longitude,accuracy_meters,recorded_at FROM vehicle_location_events WHERE organization_id=? AND vehicle_key=? ORDER BY id DESC LIMIT 1',(user['organization_id'],key)).fetchone()
    out=dict(location) if location else {'vehicle_key':key,'status':'no_location'}
    fresh=False
    if location:
        at=datetime.fromisoformat(location['recorded_at']).replace(tzinfo=timezone.utc) if datetime.fromisoformat(location['recorded_at']).tzinfo is None else datetime.fromisoformat(location['recorded_at'])
        fresh=(datetime.now(timezone.utc)-at).total_seconds()<=180
    out['schedule']=public_schedule(schedule)
    if schedule and schedule['driver_user_id']==0: out['driverName']=schedule['driver_name'] or ''
    out['status']='outside_schedule' if schedule and not in_schedule(schedule) else 'live' if schedule and fresh else 'offline' if schedule else 'last_location' if location else 'no_location'
    if schedule and schedule['phone_status'] in ('stopped','permission_denied','location_disabled','not_enabled'): out['status']=schedule['phone_status']
    return out
