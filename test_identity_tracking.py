import hashlib
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import server
import vehicle_tracking
from test_advertisements import test_db

class ScheduleBoundaries(unittest.TestCase):
    def test_riyadh_half_open_and_overnight(self):
        s={'enabled':1,'weekdays':'[4]','start_minute':420,'end_minute':1020}
        for time,expected in [('2026-10-01T03:59:59+00:00',False),('2026-10-01T04:00:00+00:00',True),('2026-10-01T13:59:59+00:00',True),('2026-10-01T14:00:00+00:00',False),('2026-10-02T04:00:00+00:00',False)]:
            self.assertEqual(vehicle_tracking.in_schedule(s,datetime.fromisoformat(time)),expected,time)
        s.update(start_minute=1320,end_minute=120)
        for time,expected in [('2026-10-01T19:00:00+00:00',True),('2026-10-01T22:59:59+00:00',True),('2026-10-01T23:00:00+00:00',False),('2026-10-02T19:00:00+00:00',False)]:
            self.assertEqual(vehicle_tracking.in_schedule(s,datetime.fromisoformat(time)),expected,time)

class DirectoryTrackingHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for p in (patch.object(server,'DB_PATH',Path(self.temp.name)/'test.db'),patch.object(server,'OWNER_KEY_PATH',Path(self.temp.name)/'owner.key'),patch.object(server,'DATABASE_URL',''),patch.object(server,'db',test_db),patch.dict(os.environ,{'KHDOOM_OWNER_KEY':'test-platform-owner'})):
            p.start();self.addCleanup(p.stop)
        server._RATE_LIMIT_BUCKETS.clear()
        server.init_db()
        with server.db() as c:
            for org in (1,2):
                c.execute('INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)',(org,'Institution '+str(org),server.now()))
                c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,'vip',?)",(org,server.now()))
            hashed,salt=server.hash_password('test-password-123')
            for uid,org,role in ((1,1,'admin'),(2,1,'employee'),(3,2,'admin')):
                c.execute('INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?)',(uid,org,'User '+str(uid),'user'+str(uid),hashed,salt,role,server.now()))
                self.token(c,uid)
            c.commit()
        # Seed verified institution ownership, exactly as production migration does.
        server.init_db()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=self.http.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
    def token(self,c,user,token=None):
        token=token or 'test-token-'+str(user)
        c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),server.now()))
    def req(self,path,method='GET',data=None,user=1,owner=False,branch=None,token=None):
        if path=='/api/login': server._RATE_LIMIT_BUCKETS.clear()
        h={'Content-Type':'application/json','Authorization':'Bearer '+(token or 'test-token-'+str(user))}
        if owner:h['X-Owner-Key']='test-platform-owner'
        if branch:h['X-Branch-Id']=branch
        with urlopen(Request('http://127.0.0.1:'+str(self.http.server_port)+path,data=None if data is None else json.dumps(data).encode(),headers=h,method=method),timeout=5) as r:return json.load(r)
    def fails(self,code,*a,**kw):
        with self.assertRaises(HTTPError) as caught:self.req(*a,**kw)
        self.assertEqual(caught.exception.code,code);caught.exception.close()
    def test_directory_transfer_preserves_data_and_revokes_sessions(self):
        root='/owner/api/v2/directory/'
        counts=self.req(root+'counts',owner=True);self.assertEqual(counts,{'organizations':2,'users':3})
        listing=self.req(root+'organizations',owner=True)['items'];self.assertEqual(next(x for x in listing if x['id']==1)['owner_name'],'User 1')
        self.fails(401,root+'counts')
        self.fails(400,root+'users/1/archive','POST',{'confirmation':'User 1'},owner=True)
        self.req(root+'organizations/1/transfer','POST',{'newOwnerId':2,'previousOwnerMode':'keep','confirmation':'Institution 1'},owner=True)
        self.fails(401,'/api/session-status',user=1);self.fails(401,'/api/session-status',user=2)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM organizations').fetchone()['n'],2)
            self.assertEqual(c.execute('SELECT role FROM users WHERE id=2').fetchone()['role'],'admin')
            self.assertEqual(c.execute('SELECT role FROM users WHERE id=1').fetchone()['role'],'employee')
            self.assertEqual(c.execute('SELECT owner_user_id FROM owner_accounts a JOIN account_organizations ao ON ao.account_id=a.id WHERE ao.organization_id=1').fetchone()['owner_user_id'],2)
        self.req(root+'users/1/archive','POST',{'confirmation':'User 1'},owner=True)
        self.assertEqual(self.req(root+'counts',owner=True)['users'],2)
        self.req(root+'organizations/1/name','POST',{'name':'Renamed institution'},owner=True)
        self.req(root+'organizations/1/password','POST',{'password':'new-password-123'},owner=True)
        logged=self.req('/api/login','POST',{'username':'user2','password':'new-password-123'})
        self.assertTrue(logged['token'])
        self.req(root+'organizations/1/archive','POST',{'confirmation':'Renamed institution'},owner=True)
        self.assertEqual(self.req(root+'counts',owner=True),{'organizations':1,'users':1})
        self.fails(401,'/api/session-status',user=2,token=logged['token'])
        with server.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'],3)
    def test_barcode_link_without_account_and_revocation(self):
        minute=datetime.now(vehicle_tracking.RIYADH).hour*60+datetime.now(vehicle_tracking.RIYADH).minute
        def clock(n):n%=1440;return f'{n//60:02d}:{n%60:02d}'
        base='http://127.0.0.1:'+str(self.http.server_port)
        def raw(path,method='GET',data=None,token=None):
            h={'Content-Type':'application/json'}
            if token:h['Authorization']='Bearer '+token
            with urlopen(Request(base+path,data=None if data is None else json.dumps(data).encode(),headers=h,method=method),timeout=5) as r:return r.status,json.load(r)
        def raw_fails(code,*a,**kw):
            with self.assertRaises(HTTPError) as caught:raw(*a,**kw)
            self.assertEqual(caught.exception.code,code);caught.exception.close()
        data={'vehicleKey':'plate:777','vehicleName':'وانيت','weekdays':list(range(1,8)),'startTime':clock(minute-20),'endTime':clock(minute+20),'enabled':True}
        schedule='/api/vehicle-tracking/schedule'
        self.fails(403,schedule,'PUT',data,user=2)
        self.req(schedule,'PUT',data)
        owner=self.req(schedule+'?vehicleKey=plate:777')
        code=owner['linkCode'];self.assertEqual(len(code),8);self.assertFalse(owner['linked']);self.assertTrue(owner['linkUrl'].endswith(code))
        self.assertNotIn('device_hash',owner);self.assertNotIn('link_code',owner)
        raw_fails(404,'/api/driver-link/preview','POST',{'code':'AAAAAAAA'})
        status,preview=raw('/api/driver-link/preview','POST',{'code':owner['linkUrl']})
        self.assertEqual((preview['vehicleName'],preview['organizationName'],preview['hasDriver']),('وانيت','Institution 1',False));self.assertNotIn('token',preview)
        raw_fails(400,'/api/driver-link/claim','POST',{'code':code,'driverName':'سالم','accepted':False})
        raw_fails(400,'/api/driver-link/claim','POST',{'code':code,'driverName':'','accepted':True})
        status,claim=raw('/api/driver-link/claim','POST',{'code':code.lower(),'driverName':' سالم  أحمد ','accepted':True})
        token=claim['token'];self.assertTrue(token.startswith('kdrv_'));self.assertEqual(claim['vehicleKey'],'plate:777')
        status,a=raw('/api/vehicle-tracking/assignment',token=token)
        self.assertTrue(a['consented']);self.assertEqual((a['revision'],a['vehicle_key'],a['enabled']),(claim['revision'],'plate:777',1))
        self.assertNotIn('device_hash',a);self.assertNotIn('link_code',a)
        location={'vehicleKey':'plate:777','latitude':24.7,'longitude':46.7,'accuracyMeters':10,'capturedAt':datetime.now(timezone.utc).isoformat()}
        raw_fails(403,'/api/vehicle-tracking','POST',{**location,'vehicleKey':'plate:123'},token=token)
        raw_fails(400,'/api/vehicle-tracking','POST',{**location,'capturedAt':(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()},token=token)
        self.assertEqual(raw('/api/vehicle-tracking','POST',location,token=token)[0],201)
        raw('/api/vehicle-tracking/heartbeat','POST',{'status':'tracking'},token=token)
        seen=self.req('/api/vehicle-tracking?vehicleKey=plate:777');self.assertEqual((seen['status'],seen['driverName']),('live','سالم أحمد'))
        owner=self.req(schedule+'?vehicleKey=plate:777');self.assertTrue(owner['linked']);self.assertEqual(owner['driverName'],'سالم أحمد')
        # الجوال المرتبط لا يصل لأي مسار آخر، وتوكن عشوائي مرفوض.
        raw_fails(403,'/api/vehicle-tracking/schedule?vehicleKey=plate:777',token=token)
        raw_fails(401,'/api/vehicles',token=token)
        raw_fails(401,'/api/vehicle-tracking/assignment',token='kdrv_not-a-real-token')
        # تعديل الدوام لا يفصل الجوال ولا يغيّر رقم الربط.
        self.req(schedule,'PUT',{**data,'endTime':clock(minute+30)})
        self.assertEqual(raw('/api/vehicle-tracking/assignment',token=token)[1]['revision'],claim['revision'])
        # سائق جديد يصوّر نفس الباركود: يصير هو المرتبط وينفصل الأول.
        second=raw('/api/driver-link/claim','POST',{'code':code,'driverName':'فهد','accepted':True})[1]
        raw_fails(401,'/api/vehicle-tracking/assignment',token=token)
        self.assertEqual(raw('/api/vehicle-tracking','POST',{**location,'capturedAt':datetime.now(timezone.utc).isoformat()},token=second['token'])[0],201)
        # باركود جديد يلغي القديم ويفصل الجوال.
        self.fails(403,'/api/vehicle-tracking/link/rotate','POST',{'vehicleKey':'plate:777'},user=2)
        rotated=self.req('/api/vehicle-tracking/link/rotate','POST',{'vehicleKey':'plate:777'})
        self.assertNotEqual(rotated['linkCode'],code);self.assertFalse(rotated['linked'])
        raw_fails(401,'/api/vehicle-tracking/assignment',token=second['token'])
        raw_fails(404,'/api/driver-link/preview','POST',{'code':code})
        third=raw('/api/driver-link/claim','POST',{'code':rotated['linkCode'],'driverName':'ناصر','accepted':True})[1]
        self.assertFalse(self.req('/api/vehicle-tracking/link/unlink','POST',{'vehicleKey':'plate:777'})['linked'])
        raw_fails(401,'/api/vehicle-tracking/assignment',token=third['token'])
        # خارج الدوام يُرفض الموقع، والمؤسسة الأخرى لا ترى شيئًا.
        fourth=raw('/api/driver-link/claim','POST',{'code':rotated['linkCode'],'driverName':'ناصر','accepted':True})[1]
        self.req(schedule,'PUT',{**data,'startTime':clock(minute+60),'endTime':clock(minute+120)})
        raw_fails(403,'/api/vehicle-tracking','POST',{**location,'capturedAt':datetime.now(timezone.utc).isoformat()},token=fourth['token'])
        self.assertEqual(self.req('/api/vehicle-tracking?vehicleKey=plate:777',user=3)['status'],'no_location')
        with urlopen(base+'/driver?c='+code,timeout=5) as page:self.assertIn('أنا سائق',page.read().decode())
    def test_offline_points_upload_in_batches_with_real_times(self):
        minute=datetime.now(vehicle_tracking.RIYADH).hour*60+datetime.now(vehicle_tracking.RIYADH).minute
        def clock(n):n%=1440;return f'{n//60:02d}:{n%60:02d}'
        base='http://127.0.0.1:'+str(self.http.server_port)
        def raw(path,method='GET',data=None,token=None):
            h={'Content-Type':'application/json'}
            if token:h['Authorization']='Bearer '+token
            with urlopen(Request(base+path,data=None if data is None else json.dumps(data).encode(),headers=h,method=method),timeout=5) as r:return r.status,json.load(r)
        def raw_fails(code,*a,**kw):
            with self.assertRaises(HTTPError) as caught:raw(*a,**kw)
            self.assertEqual(caught.exception.code,code);caught.exception.close()
        # دوام يغطي اليوم كله تقريبًا (يبدأ بعد دقيقتين وينتهي بعد دقيقة من الغد).
        schedule={'vehicleKey':'plate:55','vehicleName':'وانيت','weekdays':list(range(1,8)),'startTime':clock(minute+2),'endTime':clock(minute+1),'enabled':True}
        self.req('/api/vehicle-tracking/schedule','PUT',schedule)
        code=self.req('/api/vehicle-tracking/schedule?vehicleKey=plate:55')['linkCode']
        token=raw('/api/driver-link/claim','POST',{'code':code,'driverName':'سالم','accepted':True})[1]['token']
        now=datetime.now(timezone.utc)
        def point(ago,lat=24.7,acc=8):return {'latitude':lat,'longitude':46.7,'accuracyMeters':acc,'capturedAt':(now-ago).isoformat()}
        path='/api/vehicle-tracking/batch'
        # نقاط أقدم من موافقة السائق الحالية لا تُحفظ.
        status,first=raw(path,'POST',{'vehicleKey':'plate:55','points':[point(timedelta(hours=3))]},token=token)
        self.assertEqual((status,first['stored'],first['skipped']),(200,0,1))
        with server.db() as c:
            c.execute("UPDATE vehicle_tracking_schedules SET consent_at=? WHERE vehicle_key='plate:55'",((now-timedelta(hours=10)).isoformat(),));c.commit()
        # وصلت بلا ترتيب: الأحدث أولًا ثم ما سُجِّل بلا اتصال، ومعها نقطة أقدم من مدة السماح ونقطة تالفة.
        batch=[point(timedelta(seconds=5),lat=24.73),point(timedelta(hours=3),lat=24.71),point(timedelta(hours=1),lat=24.72),point(timedelta(hours=27)),{'latitude':'x'},point(timedelta(seconds=40),lat=999)]
        status,saved=raw(path,'POST',{'vehicleKey':'plate:55','points':batch},token=token)
        self.assertEqual((status,saved['stored'],saved['skipped']),(200,3,3))
        # إعادة إرسال نفس الدفعة بعد انقطاع لا تكرر شيئًا.
        self.assertEqual(raw(path,'POST',{'vehicleKey':'plate:55','points':batch},token=token)[1]['stored'],0)
        with server.db() as c:
            rows=c.execute("SELECT latitude,recorded_at FROM vehicle_location_events WHERE vehicle_key='plate:55' ORDER BY recorded_at").fetchall()
        self.assertEqual([r['latitude'] for r in rows],[24.71,24.72,24.73])
        # الوقت المحفوظ هو وقت الالتقاط لا وقت الوصول.
        self.assertLess(abs((datetime.fromisoformat(rows[0]['recorded_at'])-(now-timedelta(hours=3))).total_seconds()),2)
        # آخر موقع هو الأحدث التقاطًا، والمسار مرتب زمنيًا مهما كان ترتيب الوصول.
        seen=self.req('/api/vehicle-tracking?vehicleKey=plate:55');self.assertEqual((seen['status'],seen['latitude']),('live',24.73))
        days={(now-timedelta(hours=h)).astimezone(vehicle_tracking.RIYADH).strftime('%Y-%m-%d') for h in (0,1,3)}
        ordered=[]
        for day in sorted(days):ordered+=[p['latitude'] for p in self.req('/api/vehicle-tracking/route?vehicleKey=plate:55&date='+day)['points']]
        self.assertEqual(ordered,[24.71,24.72,24.73])
        # الحدود: مركبة أخرى، توكن غير صحيح، دفعة فارغة أو أكبر من الحد.
        raw_fails(403,path,'POST',{'vehicleKey':'plate:1','points':[point(timedelta(seconds=1))]},token=token)
        raw_fails(401,path,'POST',{'vehicleKey':'plate:55','points':[point(timedelta(seconds=1))]},token='kdrv_not-real')
        raw_fails(400,path,'POST',{'vehicleKey':'plate:55','points':[]},token=token)
        raw_fails(400,path,'POST',{'vehicleKey':'plate:55','points':[point(timedelta(seconds=1))]*301},token=token)
        # مستخدم عادي ليس السائق المرتبط لا يرفع نقاطًا لهذه المركبة، والمؤسسة الأخرى لا ترى شيئًا.
        self.fails(403,path,'POST',{'vehicleKey':'plate:55','points':[point(timedelta(seconds=1))]},user=2)
        self.assertEqual(self.req('/api/vehicle-tracking?vehicleKey=plate:55',user=3)['status'],'no_location')
    def test_route_for_owner_day_distance_and_retention(self):
        with server.db() as c:
            def add(lat,lon,at,key='plate:9',org=1,acc=10):
                c.execute('INSERT INTO vehicle_location_events(organization_id,vehicle_key,latitude,longitude,accuracy_meters,recorded_at,user_id,created_at) VALUES(?,?,?,?,?,?,NULL,?)',(org,key,lat,lon,acc,at.isoformat(),at.isoformat()))
            now=datetime.now(timezone.utc)
            day_start=datetime.now(vehicle_tracking.RIYADH).replace(hour=0,minute=0,second=1,microsecond=0).astimezone(timezone.utc)
            add(24.1,46.1,now-timedelta(days=40));add(24.2,46.2,now-timedelta(days=35))          # أقدم من مدة الاحتفاظ
            add(24.7000,46.7000,day_start);add(24.7001,46.7000,day_start+timedelta(seconds=1))   # اهتزاز 11م يُتجاهل
            add(24.7090,46.7000,day_start+timedelta(seconds=2))                                  # ≈1000م
            add(24.9,46.9,day_start+timedelta(seconds=3),acc=500)                                # دقة ضعيفة تُتجاهل في المسافة
            add(24.5,46.5,day_start-timedelta(seconds=5))                                        # أمس
            add(21.0,39.0,day_start,org=2)                                                       # مؤسسة أخرى
            c.commit()
        path='/api/vehicle-tracking/route?vehicleKey=plate:9'
        self.fails(403,path,user=2)
        today=self.req(path)
        self.assertEqual(today['count'],4);self.assertEqual(today['retentionDays'],30)
        self.assertTrue(950<=today['distanceMeters']<=1050,today['distanceMeters'])
        self.assertEqual(today['points'][0]['latitude'],24.7);self.assertEqual(today['firstAt'],today['points'][0]['recordedAt'])
        yesterday=(datetime.now(vehicle_tracking.RIYADH)-timedelta(days=1)).strftime('%Y-%m-%d')
        self.assertEqual(self.req(path+'&date='+yesterday)['count'],1)
        other=self.req(path,user=3);self.assertEqual((other['count'],other['points'][0]['latitude']),(1,21.0))  # المؤسسة الأخرى ترى بياناتها فقط
        self.fails(400,path+'&date=2020-01-01');self.fails(400,path+'&date=bad')
        self.fails(400,path+'&date='+(datetime.now(vehicle_tracking.RIYADH)+timedelta(days=1)).strftime('%Y-%m-%d'))
        with server.db() as c:
            vehicle_tracking.purge_old(c,force=True);c.commit()
            old=c.execute("SELECT COUNT(*) n FROM vehicle_location_events WHERE vehicle_key='plate:9' AND organization_id=1 AND recorded_at<?",((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),)).fetchone()['n']
            self.assertEqual(old,0)
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM vehicle_location_events WHERE organization_id=2").fetchone()['n'],1)
            # مركبة ليس لها إلا موقع قديم يبقى آخر موقع لها محفوظًا.
            c.execute('INSERT INTO vehicle_location_events(organization_id,vehicle_key,latitude,longitude,accuracy_meters,recorded_at,user_id,created_at) VALUES(1,?,1,1,5,?,NULL,?)',('plate:old',(datetime.now(timezone.utc)-timedelta(days=90)).isoformat(),'x'))
            vehicle_tracking.purge_old(c,force=True);c.commit()
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM vehicle_location_events WHERE vehicle_key='plate:old'").fetchone()['n'],1)
    def test_schedule_consent_isolation_freshness_and_reapproval(self):
        minute=datetime.now(vehicle_tracking.RIYADH).hour*60+datetime.now(vehicle_tracking.RIYADH).minute
        def clock(n):n%=1440;return f'{n//60:02d}:{n%60:02d}'
        data={'vehicleKey':'plate:123','vehicleName':'Test car','driverUserId':2,'weekdays':list(range(1,8)),'startTime':clock(minute-20),'endTime':clock(minute+20),'enabled':True}
        schedule='/api/vehicle-tracking/schedule'
        self.fails(403,schedule,'PUT',data,user=2)
        self.fails(400,schedule,'PUT',{**data,'driverUserId':3})
        self.req(schedule,'PUT',data,branch='main')
        a=self.req('/api/vehicle-tracking/assignment',user=2);self.assertNotIn('source_session_hash',a)
        location={'vehicleKey':'plate:123','latitude':24.7,'longitude':46.7,'accuracyMeters':10,'capturedAt':datetime.now(timezone.utc).isoformat()}
        self.fails(403,'/api/vehicle-tracking','POST',location,user=2)
        self.fails(403,'/api/vehicle-tracking','POST',location,user=1)
        self.req('/api/vehicle-tracking/consent','POST',{'accepted':True,'revision':a['revision']},user=2)
        self.fails(403,'/api/vehicle-tracking','POST',location,user=2,branch='other')
        self.fails(400,'/api/vehicle-tracking','POST',{**location,'capturedAt':(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()},user=2)
        self.req('/api/vehicle-tracking','POST',location,user=2)
        self.assertEqual(self.req('/api/vehicle-tracking?vehicleKey=plate:123')['status'],'live')
        self.assertEqual(self.req('/api/vehicle-tracking?vehicleKey=plate:123',user=3)['status'],'no_location')
        with server.db() as c:self.token(c,2,'another-phone');c.commit()
        self.fails(403,'/api/vehicle-tracking','POST',location,user=2,token='another-phone')
        self.req(schedule,'PUT',data)
        self.fails(403,'/api/vehicle-tracking','POST',location,user=2)
        a=self.req('/api/vehicle-tracking/assignment',user=2)
        self.req('/api/vehicle-tracking/consent','POST',{'accepted':True,'revision':a['revision']},user=2)
        with server.db() as c:c.execute('UPDATE vehicle_tracking_schedules SET enabled=0 WHERE organization_id=1');c.commit()
        self.fails(403,'/api/vehicle-tracking','POST',location,user=2)
        with server.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) n FROM vehicle_location_events').fetchone()['n'],1)
    def test_driver_can_stop_and_schedule_delete_revokes_collection(self):
        data={'vehicleKey':'driver-car','vehicleName':'Car','driverUserId':2,'weekdays':list(range(1,8)),'startTime':'07:00','endTime':'17:00'}
        self.req('/api/vehicle-tracking/schedule','PUT',data)
        a=self.req('/api/vehicle-tracking/assignment',user=2)
        self.fails(409,'/api/vehicle-tracking/consent','POST',{'accepted':True,'revision':a['revision']+1},user=2)
        self.req('/api/vehicle-tracking/consent','POST',{'accepted':True,'revision':a['revision']},user=2)
        self.req('/api/vehicle-tracking/heartbeat','POST',{'status':'stopped'},user=2)
        self.assertFalse(self.req('/api/vehicle-tracking/assignment',user=2)['consented'])
        self.assertEqual(self.req('/api/vehicle-tracking?vehicleKey=driver-car')['status'],'stopped')
        self.fails(403,'/api/vehicle-tracking/schedule?vehicleKey=driver-car','DELETE',user=2)
        self.req('/api/vehicle-tracking/schedule?vehicleKey=driver-car','DELETE')
        self.assertEqual(self.req('/api/vehicle-tracking/assignment',user=2)['status'],'not_assigned')
    def test_transfer_keeps_other_institution_owned_by_old_owner(self):
        with server.db() as c:
            c.execute("INSERT INTO organizations(id,name,created_at) VALUES(4,'Other owned institution',?)",(server.now(),))
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(4,'vip',?)",(server.now(),))
            c.execute("INSERT INTO account_organizations VALUES(4,1,0,1,?,'test')",(server.now(),));c.commit()
        self.req('/owner/api/v2/directory/organizations/1/transfer','POST',{'newOwnerId':2,'previousOwnerMode':'remove','confirmation':'Institution 1'},owner=True)
        with server.db() as c:
            user=c.execute('SELECT * FROM users WHERE id=1').fetchone()
            self.assertEqual(user['organization_id'],4);self.assertEqual(user['role'],'admin');self.assertEqual(user['active'],1)
            self.assertEqual(c.execute('SELECT is_primary FROM account_organizations WHERE organization_id=4').fetchone()['is_primary'],1)
        logged=self.req('/api/login','POST',{'username':'user1','password':'test-password-123'})
        self.assertEqual(logged['organizationId'],4)
        self.req('/api/session-status',token=logged['token'])
    def test_login_events_visible_to_platform_only(self):
        logged=self.req('/api/login','POST',{'username':'user1','password':'test-password-123','deviceId':'different-phone','deviceName':'Test phone'})
        logs=self.req('/api/audit-logs',token=logged['token']);self.assertFalse(any(x['action'] in ('login','new_device') for x in logs))
        admin=self.req('/owner/api/v2/security?type=alerts',owner=True)
        self.assertTrue(any(x['action']=='new_device' for x in admin['items']))

if __name__=='__main__':unittest.main()
