import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import server
import branch_appointments
from test_advertisements import test_db


class BranchInboxTest(unittest.TestCase):
    def test_isolated_links_sessions_bookings_and_mutations(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), \
             patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'test-only'}):
            server.init_db()
            expiry = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            with server.db() as c:
                for n in (1, 2):
                    c.execute('INSERT INTO organizations(id,name,public_chat_token,created_at) VALUES(?,?,?,?)', (n, 'Test', 'main'+str(n), server.now()))
                    c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,'vip',?)", (n, server.now()))
                for uid, oid, role, branch in ((1,1,'admin','main'), (2,2,'admin','main'), (3,1,'employee','b1')):
                    c.execute('INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,permissions,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                              (uid, oid, 'Test', 'test'+str(uid), 'fake', 'fake', role, json.dumps({'branch_id':branch, 'manageAppointments':True}), server.now()))
                    c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)', (hashlib.sha256(('test'+str(uid)).encode()).hexdigest(), uid, expiry, server.now()))
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            def req(path, method='GET', data=None, uid=1):
                with urlopen(Request('http://127.0.0.1:'+str(httpd.server_port)+path,
                    method=method, data=None if data is None else json.dumps(data).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer test'+str(uid)}), timeout=5) as response:
                    return json.load(response)
            def denied(path, method='GET', data=None, uid=1, code=403):
                with self.assertRaises(HTTPError) as caught:
                    req(path, method, data, uid)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
            try:
                links = {}
                for branch in ('main','b1','b2'):
                    payload = {'branchId':branch, 'branchName':branch}
                    result = req('/api/branch-chat', 'POST', payload)
                    self.assertEqual(req('/api/branch-chat', 'POST', payload), result)
                    links[branch] = result['path'].split('/')[-1]
                self.assertEqual(links['main'], 'main1')
                self.assertEqual(len(set(links.values())), 3)
                denied('/api/branch-chat','POST',{'branchId':'b1','branchName':'test'},uid=3)
                ids = {}
                for branch, token in links.items():
                    ids[branch] = req('/api/public-chat/'+token+'/appointments','POST',
                        {'customer':'Test', 'phone':'000', 'scheduledAt':expiry})['id']
                for branch in links:
                    inbox = req('/api/appointments?branchId='+branch)
                    self.assertEqual([x['id'] for x in inbox], [ids[branch]])
                    self.assertEqual(inbox[0]['branch_id'], branch)
                self.assertEqual(req('/api/appointments?branchId=b1',uid=2), [])
                denied('/api/appointments?branchId=main',uid=3)
                self.assertEqual(req('/api/appointments?branchId=b1',uid=3)[0]['id'], ids['b1'])
                denied('/api/appointments/'+str(ids['main'])+'?branchId=b1','PUT',{'status':'accepted'},code=404)
                denied('/api/appointments/'+str(ids['main'])+'?branchId=main','PUT',{'status':'accepted'},uid=3)
                # Deterministic conversational booking: seed collected details, then confirm through HTTP.
                with server.db() as c:
                    for branch in ('main','b1'):
                        c.execute('INSERT INTO chat_sessions(organization_id,branch_id,public_token,state,context_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                            (1,branch,'session-'+branch,'await_confirmation',json.dumps({'scheduled_at':expiry,'customer_name':'Test','phone':'000'}),server.now(),server.now()))
                reply=req('/api/public-chat/'+links['b1'],'POST',{'message':'نعم','sessionToken':'session-b1'})
                self.assertIn('تم إرسال', reply['text'])
                inbox=req('/api/appointments?branchId=b1')
                self.assertEqual(len(inbox),2)
                self.assertEqual(len(req('/api/appointments')),1)
                self.assertEqual(req('/api/public-chat/main1/sessions/session-b1/messages?after=0'),[])
                self.assertTrue(req('/api/public-chat/'+links['b1']+'/sessions/session-b1/messages?after=0'))
                # A session from another branch must not be reused on this link.
                other=req('/api/public-chat/'+links['b1'],'POST',{'message':'ابي موعد','sessionToken':'session-main'})
                self.assertNotEqual(other['sessionToken'],'session-main')
                req('/api/appointments/'+str(inbox[-1]['id'])+'?branchId=b1','PUT',{'status':'accepted'},uid=3)
                self.assertEqual(req('/api/appointments')[0]['status'],'pending')
                report=req('/api/public-chat/'+links['b1'],'POST',{'message':'الموظف تأخر','sessionToken':'session-b1'})
                self.assertIn('تم تسجيل بلاغك',report['text'])
                followup_item=next(x for x in req('/api/appointments?branchId=b1') if x['followup_count'])
                self.assertEqual(req('/api/appointments')[0]['followup_count'],0)
                reply_path='/api/appointments/'+str(followup_item['id'])+'/followup-reply?branchId=b1'
                reply_data={'message':'نعتذر، سنتواصل معك الآن','throughMessageId':followup_item['followup_latest_id']}
                denied(reply_path,'POST',reply_data,uid=2,code=404)
                self.assertTrue(req(reply_path,'POST',reply_data,uid=3)['sent'])
                self.assertTrue(req(reply_path,'POST',reply_data,uid=3)['alreadyReplied'])
                messages=req('/api/public-chat/'+links['b1']+'/sessions/session-b1/messages?after=0')
                self.assertEqual(len([m for m in messages if m['message']==reply_data['message']]),1)
                req('/api/public-chat/'+links['b1'],'POST',{'message':'ابي تاجيل موعدي','sessionToken':'session-b1'})
                followup_item=next(x for x in req('/api/appointments?branchId=b1') if x['followup_count'])
                new_time=(datetime.now(timezone.utc)+timedelta(days=5)).isoformat()
                req('/api/appointments/'+str(followup_item['id'])+'?branchId=b1','PUT',{'status':'accepted','scheduledAt':new_time,'followupThrough':followup_item['followup_latest_id']},uid=3)
                updated=next(x for x in req('/api/appointments?branchId=b1') if x['id']==followup_item['id'])
                self.assertEqual(updated['followup_count'],0)
                self.assertEqual(updated['scheduled_at'],new_time)
                server.init_db()  # migration is repeatable and preserves existing assignments
                self.assertEqual(len(req('/api/appointments?branchId=b1')),2)
                # New customer inquiry reads reception training without creating a booking.
                qa='سؤال وجواب معتمد: '+json.dumps({'السؤال':'كم سعر الزجاج؟','الإجابة':'150 ريال للمتر حسب النوع'},ensure_ascii=False)
                with server.db() as c:
                    c.execute('INSERT INTO ai_training(organization_id,employee_type,content,updated_at) VALUES(?,?,?,?)',(1,'reception',qa,server.now()))
                with patch.object(server,'generate_ai_text',return_value='السعر حسب نوع الزجاج، ما النوع المطلوب؟') as generate:
                    quote=req('/api/public-chat/'+links['b2'],'POST',{'message':'كم سعر الزجاج؟'})
                    self.assertEqual(quote['text'],'150 ريال للمتر حسب النوع')
                    generate.assert_not_called()
                    session_token=quote['sessionToken']
                    self.assertEqual(len(req('/api/appointments?branchId=b2')),1)
                    ticket=req('/api/public-chat/'+links['b2'],'POST',{'message':'ابي موظف بشري اتكلم معه','sessionToken':session_token})
                    self.assertIn('تم تسجيل طلب تواصل',ticket['text'])
                    handoff=next(x for x in req('/api/appointments?branchId=b2') if x['source']=='human_handoff')
                    self.assertEqual(handoff['scheduled_at'],'')
                    self.assertEqual(handoff['followup_count'],1)
                    self.assertFalse(any(x['source']=='human_handoff' for x in req('/api/appointments?branchId=b1')))
                    response={'message':'أهلًا، معك موظف المؤسسة، كيف نخدمك؟','throughMessageId':handoff['followup_latest_id']}
                    endpoint='/api/appointments/'+str(handoff['id'])+'/followup-reply?branchId=b2'
                    self.assertTrue(req(endpoint,'POST',response)['sent'])
                    transcript=req('/api/public-chat/'+links['b2']+'/sessions/'+session_token+'/messages?after=0')
                    self.assertTrue(any(x['sender']=='human' and x['message']==response['message'] for x in transcript))
                    generate.assert_not_called()
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()

    def test_legacy_rows_default_to_main(self):
        import sqlite3
        c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
        c.executescript('CREATE TABLE organizations(id INTEGER PRIMARY KEY); CREATE TABLE chat_sessions(id INTEGER); CREATE TABLE appointment_requests(id INTEGER); INSERT INTO appointment_requests VALUES(1); INSERT INTO chat_sessions VALUES(1);')
        branch_appointments.migrate(c); branch_appointments.migrate(c)
        self.assertEqual(c.execute('SELECT branch_id FROM appointment_requests').fetchone()[0],'main')
        self.assertEqual(c.execute('SELECT branch_id FROM chat_sessions').fetchone()[0],'main')
        c.close()
