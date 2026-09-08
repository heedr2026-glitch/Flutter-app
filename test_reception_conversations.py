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
from test_advertisements import test_db

class ReceptionConversationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(server,'DB_PATH',Path(self.temp.name)/'test.db'),
            patch.object(server,'DATABASE_URL',''),patch.object(server,'db',test_db),
            patch.dict(os.environ,{'KHDOOM_OWNER_KEY':'test-only','KHDOOM_BRANCH_SYNC_ENABLED':'0'})):
            patcher.start();self.addCleanup(patcher.stop)
        self.model=patch.object(server.AI_AGENT_SERVICE,'respond',return_value='رد اختباري').start()
        self.addCleanup(patch.stopall)
        server.init_db()
        with server.db() as c:
            for uid in (1,2):
                c.execute('INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)',(uid,'Test',server.now()))
                c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,'vip',?)",(uid,server.now()))
                c.execute('INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (uid,uid,'Test','test'+str(uid),'fake','fake','admin',server.now()))
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',
                    (hashlib.sha256(('test'+str(uid)).encode()).hexdigest(),uid,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),server.now()))
        self.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=self.httpd.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(self.httpd.shutdown(),self.httpd.server_close(),thread.join()))
        self.seq=0

    def call(self,message=None,uid=1,branch='main',client=None):
        headers={'Content-Type':'application/json'}
        if uid:headers['Authorization']='Bearer test'+str(uid)
        self.seq+=1
        body=None if message is None else json.dumps({'message':message,'branchId':branch,'clientMessageId':client or f'message_{self.seq:08}',
            'conversationId':1,'request':{'name':'FORGED','status':'waiting_for_manager'}}).encode()
        url=f'http://127.0.0.1:{self.httpd.server_port}/api/reception/conversation?branchId={branch}'
        try:
            with urlopen(Request(url,data=body,headers=headers),timeout=5) as response:return response.status,json.load(response)
        except HTTPError as error:
            with error:return error.code,json.load(error)

    def test_state_survives_new_requests_and_duplicate_delivery(self):
        status,data=self.call('ابي مسول يكلمني',client='start_0001')
        self.assertEqual(status,200);cid=data['conversationId']
        self.assertEqual(data['request']['status'],'waiting_for_name')
        self.assertNotIn('request_id',data['request'])
        self.call('حيدر')
        data=self.call()[1]
        self.assertEqual(data['conversationId'],cid)
        self.assertEqual(data['request']['name'],'حيدر')
        self.assertEqual(data['request']['status'],'waiting_for_phone')
        data=self.call('123')[1]
        self.assertNotIn('request_id',data['request'])
        data=self.call('٠٥٠١٢٣٤٥٦٧',client='phone_0001')[1]
        aid=data['request']['request_id']
        self.assertEqual(data['request']['status'],'waiting_for_manager')
        self.assertEqual(data['request']['phone'],'+966501234567')
        count=len(data['messages'])
        self.assertEqual(len(self.call('٠٥٠١٢٣٤٥٦٧',client='phone_0001')[1]['messages']),count)
        self.assertEqual(self.call('different',client='phone_0001')[0],409)
        self.assertIn('مسجل بالفعل',self.call('ابي المسؤول')[1]['messages'][-1]['message'])
        self.assertEqual(self.call()[1]['request']['request_id'],aid)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM appointment_requests').fetchone()[0],1)
            c.execute("UPDATE appointment_requests SET status='completed' WHERE id=?",(aid,))
        self.assertEqual(self.call()[1]['request']['status'],'completed')
        self.assertIn('مكتمل',self.call('ابي المسؤول')[1]['messages'][-1]['message'])
        self.assertFalse(self.model.called)

    def test_scope_permissions_and_no_client_forged_context(self):
        self.call('ابي المدير')
        self.assertEqual(self.call(uid=2)[1]['messages'],[])
        self.assertEqual(self.call(branch='other')[1]['messages'],[])
        self.assertEqual(self.call(uid=None)[0],401)
        with server.db() as c:c.execute("UPDATE users SET role='employee',permissions='{}' WHERE id=1")
        self.assertEqual(self.call()[0],403)
        with server.db() as c:c.execute("UPDATE users SET permissions=? WHERE id=1",(json.dumps({'manageSettings':True,'branch_id':'main'}),))
        self.assertEqual(self.call(branch='other')[0],403)

    def test_database_history_and_details_before_intent(self):
        self.call('اسمي حيدر')
        self.call('+966501234567')
        context=self.model.call_args.args[1]
        self.assertEqual(context.runtime['request_state']['name'],'حيدر')
        self.assertIn('اسمي حيدر',str(context.history))
        data=self.call('ابي المسؤول')[1]
        self.assertEqual(data['request']['status'],'waiting_for_manager')
        self.assertEqual(data['request']['name'],'حيدر')

    def test_concurrent_retries_create_one_request(self):
        from concurrent.futures import ThreadPoolExecutor
        self.call('ابي المسؤول')
        self.call('حيدر')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: self.call('0501234567',client='same_phone_01'),range(2)))
        self.assertTrue(all(status==200 for status,_ in results))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM appointment_requests').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM reception_turns WHERE client_id='same_phone_01'").fetchone()[0],1)

    def test_database_failure_never_confirms_or_partially_saves_request(self):
        self.call('ابي المسؤول')
        self.call('حيدر')
        with server.db() as c:
            c.execute("CREATE TRIGGER block_request BEFORE INSERT ON appointment_requests BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        self.assertEqual(self.call('0501234567',client='retry_phone_1')[0],500)
        data=self.call()[1]
        self.assertNotIn('request_id',data['request'])
        self.assertEqual(data['request']['status'],'waiting_for_phone')
        with server.db() as c:c.execute('DROP TRIGGER block_request')
        data=self.call('0501234567',client='retry_phone_1')[1]
        self.assertEqual(data['request']['status'],'waiting_for_manager')
        server.init_db()
        self.assertEqual(self.call()[1]['request']['request_id'],data['request']['request_id'])

    def test_model_failure_still_saves_and_manager_flow_works(self):
        self.model.side_effect=server.ai_core.AIServiceError(503,'offline')
        data=self.call('مرحبا')[1]
        self.assertEqual(len(data['messages']),2)
        self.assertIn('رسالتك محفوظة',data['messages'][-1]['message'])
        self.assertEqual(self.call('ابي المسؤول')[1]['request']['status'],'waiting_for_name')

if __name__=='__main__':unittest.main()
