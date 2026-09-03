import hashlib
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import ad_policy
import server

_db = server.db
@contextmanager
def test_db():
    connection = _db()
    try:
        with connection: yield connection
    finally: connection.close()

class AdRulesTest(unittest.TestCase):
    def test_subscriber_cap_and_owner_control(self):
        for valid in (1, 5, 10): self.assertEqual(ad_policy.requested_days(valid), valid)
        for invalid in (0, 11, -1, True, '5', None, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): ad_policy.requested_days(invalid)
        start = datetime.now(timezone.utc)
        self.assertEqual(ad_policy.owner_expiry(4000, start), (start+timedelta(days=4000)).isoformat())
        self.assertIsNone(ad_policy.owner_expiry(None, start))
        with self.assertRaises(ValueError): ad_policy.owner_expiry(0, start)

    def test_status_at_exact_expiration(self):
        at = datetime.now(timezone.utc)
        self.assertEqual(ad_policy.project({'active':1,'approved':1,'expires_at':at.isoformat()}, at)['status'], 'expired')
        self.assertEqual(ad_policy.project({'active':1,'approved':0}, at)['status'], 'pending')
        self.assertEqual(ad_policy.project({'active':0,'approved':0}, at)['status'], 'rejected')
        self.assertEqual(ad_policy.project({'active':1,'approved':1}, at)['status'], 'published')

    def test_http_request_review_expiry_and_tenant_isolation(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory)/'test.db'), \
             patch.object(server, 'OWNER_KEY_PATH', Path(directory)/'owner.key'), \
             patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY':'ad-test-key'}):
            server.init_db()
            with server.db() as c:
                for number in (1,2):
                    c.execute('INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)',(number,'Test organization',server.now()))
                    c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,'vip',?)",(number,server.now()))
                    c.execute("INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?)",(number,number,'Fake user','test'+str(number),'fake','fake','admin',server.now()))
                    c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',(hashlib.sha256(('test-token-'+str(number)).encode()).hexdigest(),number,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),server.now()))
                c.commit()
            httpd = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread = threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
            def req(path, method='GET', data=None, user=1, owner=False):
                headers={'Content-Type':'application/json','Authorization':'Bearer test-token-'+str(user)}
                if owner: headers['X-Owner-Key']='ad-test-key'
                with urlopen(Request('http://127.0.0.1:'+str(httpd.server_port)+path,
                    data=None if data is None else json.dumps(data).encode(),headers=headers,method=method),timeout=5) as r:
                    return json.load(r)
            try:
                for invalid in (0,11,True):
                    with self.assertRaises(HTTPError) as caught:
                        req('/api/ads','POST',{'title':'Test ad','requestedDays':invalid})
                    self.assertEqual(caught.exception.code,400); caught.exception.close()
                ad_id=req('/api/ads','POST',{'title':'Test ad','requestedDays':7})['id']
                self.assertEqual(req('/api/my-ads')[0]['status'],'pending')
                self.assertEqual(req('/api/my-ads')[0]['requested_days'],7)
                self.assertEqual(req('/api/my-ads',user=2),[])
                self.assertEqual(req('/api/ads'),[])
                with self.assertRaises(HTTPError) as caught:
                    req('/owner/api/ads/'+str(ad_id),'PUT',{'action':'approve','durationDays':20})
                self.assertEqual(caught.exception.code,401); caught.exception.close()
                for duration in (40,2,None):
                    req('/owner/api/ads/'+str(ad_id),'PUT',{'action':'approve','durationDays':duration,'reviewNote':'Approved duration'},owner=True)
                    mine=req('/api/my-ads')[0]
                    self.assertEqual(mine['status'],'published')
                    self.assertEqual(mine['review_note'],'Approved duration')
                    self.assertEqual(len(req('/api/ads')),1)
                    if duration is None: self.assertIsNone(mine['expires_at'])
                with server.db() as c:
                    c.execute('UPDATE advertisements SET expires_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),ad_id)); c.commit()
                self.assertEqual(req('/api/my-ads')[0]['status'],'expired')
                self.assertEqual(req('/api/ads'),[])
                self.assertEqual(req('/owner/api/ads',owner=True)[0]['status'],'expired')
                req('/owner/api/ads/'+str(ad_id),'PUT',{'action':'reject','reviewNote':'Please revise'},owner=True)
                self.assertEqual(req('/api/my-ads')[0]['status'],'rejected')
                self.assertEqual(req('/api/my-ads')[0]['review_note'],'Please revise')
                server.init_db()
                self.assertEqual(req('/api/my-ads')[0]['requested_days'],7)
                with urlopen('http://127.0.0.1:'+str(httpd.server_port)+'/owner',timeout=5) as r:
                    self.assertIn('ad-unlimited-',r.read().decode())
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)

if __name__ == '__main__': unittest.main()
