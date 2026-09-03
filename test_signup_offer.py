import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import signup_offer
import server
from test_advertisements import test_db


class OfferTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:'); self.c.row_factory = sqlite3.Row
        signup_offer.migrate(self.c)
    def tearDown(self): self.c.close()
    def configure(self, **changes):
        data = {**signup_offer.overview(self.c), **changes}
        return signup_offer.configure(self.c, data, server.ApiError)
    def test_default_off_limit_increase_and_decrease(self):
        self.assertIsNone(signup_offer.claim(self.c, 1, '2026-01-31T10:00:00+00:00'))
        self.configure(enabled=True, max_claims=2, months=1)
        a = signup_offer.claim(self.c, 1, '2026-01-31T10:00:00+00:00')
        self.assertTrue(a['expires_at'].startswith('2026-02-28'))
        self.assertEqual(a, signup_offer.claim(self.c, 1, '2026-02-01T00:00:00+00:00'))
        signup_offer.claim(self.c, 2, '2026-01-31T10:00:00+00:00')
        self.assertIsNone(signup_offer.claim(self.c, 3, '2026-01-31T10:00:00+00:00'))
        self.configure(max_claims=1, months=12, package='vip')
        self.assertEqual(signup_offer.overview(self.c)['remaining'], 0)
        self.assertEqual(a, signup_offer.claim(self.c, 1, '2026-02-01T00:00:00+00:00'))
        self.configure(max_claims=3)
        self.assertEqual(signup_offer.claim(self.c, 3, '2026-01-31T10:00:00+00:00')['package'], 'vip')
    def test_rollback_and_stale_settings(self):
        data = self.configure(enabled=True)
        self.c.commit()
        signup_offer.claim(self.c, 1, '2026-01-01T00:00:00+00:00')
        with self.assertRaises(server.ApiError): signup_offer.configure(self.c, data, server.ApiError)
        self.c.rollback()
        self.assertEqual(signup_offer.overview(self.c)['claimed'], 0)
    def test_validation(self):
        for change in [{'months':0},{'months':True},{'max_claims':-1},{'enabled':1},{'package':'free'}]:
            with self.assertRaises(server.ApiError): self.configure(**change)
    def test_concurrent_capacity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.db'
            c = sqlite3.connect(path); c.row_factory=sqlite3.Row
            signup_offer.migrate(c)
            signup_offer.configure(c, {**signup_offer.overview(c),'enabled':True,'max_claims':3},server.ApiError)
            c.commit(); c.close()
            def register(i):
                with closing(sqlite3.connect(path, timeout=10)) as connection:
                    with connection:
                        connection.row_factory=sqlite3.Row
                        connection.execute('BEGIN IMMEDIATE')
                        return signup_offer.claim(connection,i,'2026-01-01T00:00:00+00:00') is not None
            with ThreadPoolExecutor(max_workers=6) as pool:
                self.assertEqual(sum(pool.map(register, range(12))),3)


class RegistrationOfferTest(unittest.TestCase):
    def test_registration_capacity_failure_and_owner_auth(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server,'DB_PATH',Path(temp)/'test.db'), patch.object(server,'DATABASE_URL',''), patch.object(server,'db',test_db), patch.dict(os.environ,{'KHDOOM_OWNER_KEY':'test-only'}):
            server.init_db()
            httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
            def req(path, method='GET', data=None, owner=True):
                headers={'Content-Type':'application/json'}
                if owner: headers['X-Owner-Key']='test-only'
                with urlopen(Request('http://127.0.0.1:'+str(httpd.server_port)+path,method=method,headers=headers,data=None if data is None else json.dumps(data).encode()),timeout=10) as r:
                    return json.load(r)
            def register(name):
                return req('/api/register','POST',{'name':name,'username':name,'phone':'000','password':'test-only-password','organizationName':name},owner=False)
            try:
                with self.assertRaises(HTTPError) as error: req('/owner/api/signup-offer',owner=False)
                self.assertEqual(error.exception.code,401); error.exception.close()
                state=req('/owner/api/signup-offer')
                req('/owner/api/signup-offer','PUT',{**state,'enabled':True,'max_claims':2,'months':12,'package':'vip'})
                self.assertEqual(register('testa')['package'],'vip')
                with self.assertRaises(HTTPError) as error: register('testa')
                self.assertEqual(error.exception.code,409); error.exception.close()
                self.assertEqual(req('/owner/api/signup-offer')['claimed'],1)
                self.assertEqual(register('testb')['package'],'vip')
                self.assertEqual(register('testc')['package'],'free')
                self.assertEqual(req('/owner/api/signup-offer')['claimed'],2)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()
