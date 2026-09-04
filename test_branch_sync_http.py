import hashlib
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import server
from test_advertisements import test_db


class SyncHttpTest(unittest.TestCase):
    def test_feature_gate_auth_conflict_and_history(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server,'DB_PATH',Path(directory)/'test.db'), \
             patch.object(server,'DATABASE_URL',''), patch.object(server,'db',test_db), \
             patch.dict(os.environ,{'KHDOOM_OWNER_KEY':'test-only','KHDOOM_BRANCH_SYNC_ENABLED':'1'}):
            server.init_db()
            with server.db() as c:
                for uid in (1,2):
                    c.execute('INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)',(uid,'Test',server.now()))
                    c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,'vip',?)",(uid,server.now()))
                    c.execute('INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?)',
                              (uid,uid,'Test','test'+str(uid),'fake','fake','admin',server.now()))
                    c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',
                              (hashlib.sha256(('test'+str(uid)).encode()).hexdigest(),uid,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),server.now()))
            httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
            def req(path, method='GET', body=None, uid=1):
                headers={'Content-Type':'application/json'}
                if uid: headers['Authorization']='Bearer test'+str(uid)
                try:
                    with urlopen(Request('http://127.0.0.1:'+str(httpd.server_port)+path,
                        method=method, data=None if body is None else json.dumps(body).encode(),headers=headers),timeout=5) as response:
                        return response.status,json.load(response)
                except HTTPError as error:
                    with error: return error.code,json.load(error)
            path='/api/branch-sync/b1/vehicles'
            try:
                self.assertEqual(req(path,uid=None)[0],401)
                self.assertEqual(req(path)[1]['records'],[])
                data={'baseRevision':0,'data':{'name':'Device A'}}
                self.assertEqual(req(path+'/v1','PUT',data)[1]['revision'],1)
                self.assertEqual(req(path+'/v1','PUT',data)[1]['revision'],1)
                self.assertEqual(req(path+'/v1','PUT',{'baseRevision':0,'data':{'name':'Device B'}})[0],409)
                self.assertEqual(req(path,uid=2)[1]['records'],[])
                self.assertEqual(req(path.replace('/b1/','/main/'))[1]['records'],[])
                self.assertEqual(len(req(path+'/v1/history')[1]),1)
                self.assertEqual(req(path+'/v1/history',uid=2)[1],[])
                self.assertEqual(req(path+'/v2','PUT',{'baseRevision':0,'data':{'password':'blocked'}})[0],400)
                with patch.dict(os.environ,{'KHDOOM_BRANCH_SYNC_ENABLED':'0'}):
                    self.assertEqual(req(path)[0],503)
                    self.assertEqual(req(path+'/v1','PUT',data)[0],503)
                self.assertEqual(req(path)[1]['records'][0]['data']['name'],'Device A')
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()
