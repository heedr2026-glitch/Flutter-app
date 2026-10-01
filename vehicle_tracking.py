"""Driver schedules enforced by the server as well as the phone."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

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
    out=dict(row);out.pop('source_session_hash',None)
    out['weekdays']=json.loads(out['weekdays'])
    out['inSchedule']=in_schedule(dict(row))
    out['timezone']='Asia/Riyadh';out['serverTime']=datetime.now(timezone.utc).isoformat()
    return out


def assignment(c,user):
    return c.execute('SELECT * FROM vehicle_tracking_schedules WHERE organization_id=? AND driver_user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 1',(user['organization_id'],user['id'])).fetchone()


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
        if method=='GET': return public_schedule(old) or {'vehicle_key':key,'status':'not_configured'}
        if method=='DELETE':
            if old:
                c.execute("UPDATE vehicle_tracking_schedules SET enabled=0,revision=revision+1,consent_revision=0,source_session_hash='',phone_status='stopped' WHERE organization_id=? AND vehicle_key=?",(org,key))
                server.audit_log(c,org,user['id'],'vehicle_tracking_schedule','إيقاف ربط المركبة بالتتبع','vehicle',key)
            return {'saved':True}
        if method=='PUT':
            driver=int(data.get('driverUserId',0));days=data.get('weekdays')
            if not isinstance(days,list) or not days or any(type(x)is not int or not 1<=x<=7 for x in days): raise ValueError('حدد أيام الدوام')
            def minutes(value):
                if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(value)): raise ValueError('وقت الدوام غير صحيح')
                h,m=map(int,value.split(':'));return h*60+m
            start,end=minutes(data.get('startTime')),minutes(data.get('endTime'))
            if start==end: raise ValueError('وقت البداية والنهاية لا يكونان متساويين')
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
    out['status']='outside_schedule' if schedule and not in_schedule(schedule) else 'live' if schedule and fresh else 'offline' if schedule else 'last_location' if location else 'no_location'
    if schedule and schedule['phone_status'] in ('stopped','permission_denied','location_disabled','not_enabled'): out['status']=schedule['phone_status']
    return out
