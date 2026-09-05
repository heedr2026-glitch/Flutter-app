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


class TrainingHttpTest(unittest.TestCase):
    def test_dialogue_grounding_saving_and_readonly_assistant(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server,'DB_PATH',Path(directory)/'test.db'), \
             patch.object(server,'DATABASE_URL',''), patch.object(server,'db',test_db), \
             patch.dict(os.environ,{'KHDOOM_OWNER_KEY':'test-only','KHDOOM_BRANCH_SYNC_ENABLED':'0'}), \
             patch.object(server.AI_AGENT_SERVICE,'respond',return_value='رد اختباري') as generate:
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
            def req(message, path='/api/ai-training/chat', uid=1, **extra):
                headers={'Content-Type':'application/json'}
                if uid: headers['Authorization']='Bearer test'+str(uid)
                body={'message':message,'employeeType':'assistant',**extra}
                try:
                    with urlopen(Request('http://127.0.0.1:'+str(httpd.server_port)+path,
                        method='POST',data=json.dumps(body).encode(),headers=headers),timeout=5) as response:
                        return response.status,json.load(response)
                except HTTPError as error:
                    with error: return error.code,json.load(error)
            try:
                self.assertEqual(req('فرع',uid=None)[0],401)
                self.assertIn('VIP',req('الباقة')[1]['text'])
                self.assertIn('الرياض',req('فرع',context={'branchName':'الرياض'})[1]['text'])
                self.assertFalse(generate.called)
                req('اسمي حيدر')
                req('وش اسمي؟')
                self.assertTrue(any(x['message']=='اسمي حيدر' for x in generate.call_args.args[1].history))
                req('وش اسمي؟',uid=2)
                self.assertFalse(any('حيدر' in x['message'] for x in generate.call_args.args[1].history))
                self.assertEqual(req('احفظها')[1]['action'],'needs_fact')
                self.assertEqual(req('احفظ: سعر المتر 150')[1]['action'],'saved')
                self.assertEqual(req('احفظ: سعر المتر 150')[1]['action'],'already_saved')
                req('اشرح شغلي',path='/api/ai/assistant',history=[{'sender':'owner','message':'أبي زجاج'}],context={'branchName':'الرياض','vehicles':2,'password':'secret'})
                context=generate.call_args.args[1]
                self.assertIn('سعر المتر 150',json.dumps(context.runtime,ensure_ascii=False))
                self.assertIn('أبي زجاج',json.dumps(context.history,ensure_ascii=False))
                self.assertNotIn('secret',json.dumps(context.runtime,ensure_ascii=False))
                with server.db() as c:
                    self.assertEqual(c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0],0)
                    c.execute("UPDATE users SET role='employee',permissions='{}' WHERE id=1")
                self.assertEqual(req('سؤال',path='/api/ai/assistant')[0],403)
                self.assertEqual(req('احفظ: خطأ')[0],403)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()


if __name__=='__main__': unittest.main()
