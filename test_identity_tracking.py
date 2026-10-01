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
