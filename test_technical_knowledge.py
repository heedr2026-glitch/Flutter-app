"""The technical employee recognises complaints, states the real cause, flags new ones and learns."""
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

import owner_admin
import server
import technical_support as support
from test_owner_admin_fixes import test_db

KEY = 'knowledge-test-key'


def iso(days=0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class FakeAI:
    """Stands in for the AI provider: returns a fixed answer and counts the calls."""
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    def transport(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError('provider down')
        return {'text': json.dumps(self.answer, ensure_ascii=False)}

    @staticmethod
    def output_text(response):
        return 'النتيجة:\n' + response['text']


class TechnicalKnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(server, 'DB_PATH', Path(self.directory.name) / 'test.db'),
            patch.object(server, 'OWNER_KEY_PATH', Path(self.directory.name) / 'owner.key'),
            patch.object(server, 'DATABASE_URL', ''),
            patch.object(server, 'db', test_db),
            patch.object(owner_admin, '_FALLBACKS_READY', False),
            patch.dict(os.environ, {'KHDOOM_OWNER_KEY': KEY, 'KHDOOM_SUBSCRIPTION_SWEEP_SECONDS': '0'}),
        ]
        for item in self.patches:
            item.start()
        os.environ.pop('OPENAI_API_KEY', None)
        os.environ.pop('KHDOOM_AI_API_KEY', None)
        server._RATE_LIMIT_BUCKETS.clear()
        server._ORG_ERROR_SEEN.clear()
        server.init_db()
        with server.db() as c:
            c.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('مغسلة الاختبار', server.now()))
            c.execute("INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(1,1,'مالك','owner1','','05','x','x','admin',?)", (server.now(),))
            c.execute("INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(2,1,'سالم','salem','','05','x','x','employee',?)", (server.now(),))
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(1,'free',?,NULL) ON CONFLICT(organization_id) DO UPDATE SET package='free',expires_at=NULL", (server.now(),))
            c.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (hashlib.sha256(b'user-token').hexdigest(), 1, iso(1), server.now()))
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def call(self, path, method='GET', body=None, headers=None):
        send = {'Content-Type': 'application/json', 'X-Owner-Key': KEY}
        send.update(headers or {})
        request = Request(f'http://127.0.0.1:{self.httpd.server_port}{path}', method=method,
                          data=json.dumps(body).encode() if body is not None else None, headers=send)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def ticket(self, message, category='مشكلة في الحساب'):
        """A subscriber opens a ticket and the technical employee handles it once."""
        with server.db() as c:
            ident = c.execute("INSERT INTO support_tickets(organization_id,user_id,category,message,status,owner_reply,created_at,updated_at,title) VALUES(1,1,?,?,'open','',?,?,?) RETURNING id",
                              (category, message, server.now(), server.now(), message[:60])).fetchone()['id']
            support.run_pending(c, owner_admin, server)
            row = dict(c.execute('SELECT status,owner_reply FROM support_tickets WHERE id=?', (ident,)).fetchone())
            task = dict(c.execute('SELECT * FROM technical_tasks WHERE support_ticket_id=? ORDER BY id DESC LIMIT 1', (ident,)).fetchone())
            c.commit()
        return ident, row, task

    def test_pending_subscription_request_is_named_and_sent_to_the_owner(self):
        with server.db() as c:
            c.execute("INSERT INTO subscription_requests(organization_id,requested_package,paid_months,transfer_name,transfer_receipt,quoted_price,created_at) VALUES(1,'vip',1,'محمد','',99,?)", ('2026-10-01T08:00:00+00:00',))
        ident, ticket, task = self.ticket('دفعت والباقة ما تفعلت')
        self.assertEqual((task['knowledge'], task['problem_type'], task['needs_owner']), ('known', 'subscription', 1))
        self.assertIn('بانتظار اعتماد الإدارة', task['diagnosis'])
        self.assertIn('2026-10-01', ticket['owner_reply'])
        self.assertIn('قيد المراجعة', ticket['owner_reply'])
        attention = self.call('/owner/api/v2/summary')[1]['technicalAttention']
        self.assertEqual([(x['ticket_id'], x['knowledge']) for x in attention], [(ident, 'known')])

    def test_ads_on_a_lower_package_is_explained_without_bothering_the_owner(self):
        ident, ticket, task = self.ticket('ما اقدر انشئ اعلان')
        self.assertEqual((task['knowledge'], task['problem_type'], task['needs_owner']), ('known', 'ads', 0))
        self.assertIn('VIP', ticket['owner_reply'])
        self.assertEqual(ticket['status'], 'awaiting_user')
        self.assertEqual(self.call('/owner/api/v2/summary')[1]['technicalAttention'], [])
        listed = self.call('/owner/api/v2/support?status=&page=1')[1]['items'][0]['technical_task']
        self.assertEqual((listed['knowledge'], listed['problem_type']), ('known', 'ads'))

    def test_blocked_login_names_the_recorded_reason(self):
        with server.db() as c:
            c.execute("INSERT INTO login_failures(organization_id,user_id,username,error_code,reason,created_at) VALUES(1,2,'salem',403,'account_inactive',?)", (server.now(),))
        _, ticket, task = self.ticket('الحساب معلق مايقدر يدخل التطبيق')
        self.assertEqual(task['problem_type'], 'login')
        self.assertIn('salem', ticket['owner_reply'])
        self.assertIn('موقوف', ticket['owner_reply'])

    def test_employee_limit_is_reported_with_the_real_numbers(self):
        _, ticket, task = self.ticket('اذا اضفت بيانات موظف ماينحفظ')
        self.assertEqual((task['problem_type'], task['needs_owner']), ('save', 0))
        self.assertIn('1 من 1', ticket['owner_reply'])

    def errors(self):
        for _ in range(40):  # الرد يُرسل قبل تسجيل الخطأ
            with server.db() as c:
                rows = [dict(r) for r in c.execute('SELECT organization_id,user_id,method,route,status,message FROM organization_api_errors ORDER BY id').fetchall()]
            if rows:
                return rows
            threading.Event().wait(.05)
        return []

    def test_subscriber_error_messages_are_recorded_and_used(self):
        token = {'Authorization': 'Bearer user-token', 'X-Owner-Key': ''}
        with server.db() as c:
            c.execute("UPDATE subscriptions SET package='vip' WHERE organization_id=1")
        status, body = self.call('/api/support-tickets', 'POST', {'message': 'x'}, headers=token)
        self.assertEqual(status, 400)
        self.assertEqual(self.errors(), [{'organization_id': 1, 'user_id': 1, 'method': 'POST', 'route': '/api/support-tickets', 'status': 400, 'message': body['error']}])
        self.call('/api/support-tickets', 'POST', {'message': 'x'}, headers=token)  # التكرار خلال دقيقة لا يُسجل مرتين
        self.assertEqual(self.call('/api/vehicles', headers={'X-Owner-Key': ''})[0], 401)  # غير المسجل لا يُسجل له شيء
        threading.Event().wait(.2)
        self.assertEqual(len(self.errors()), 1)
        # رسالة رفض مسجلة تُشرح للمشترك كما هي
        with server.db() as c:
            c.execute("INSERT INTO organization_api_errors(organization_id,user_id,method,route,status,message,created_at) VALUES(1,1,'POST','/api/employees',400,'رقم الجوال مستخدم لموظف آخر',?)", (server.now(),))
        _, ticket, task = self.ticket('اضفت موظف جديد وما ينحفظ')
        self.assertEqual((task['problem_type'], task['needs_owner']), ('save', 0))
        self.assertIn('رقم الجوال مستخدم لموظف آخر', ticket['owner_reply'])

    def test_server_error_is_reported_as_a_bug_for_development(self):
        with server.db() as c:
            c.execute("UPDATE subscriptions SET package='vip' WHERE organization_id=1")
            c.execute("INSERT INTO organization_api_errors(organization_id,user_id,method,route,status,message,created_at) VALUES(1,1,'POST','/api/employees',500,'خطأ داخل الخادم: IntegrityError',?)", (server.now(),))
        _, ticket, task = self.ticket('الموظف ما ينحفظ')
        self.assertEqual(task['needs_owner'], 1)
        self.assertIn('خلل برمجي يحتاج تطويرًا', task['proposal'])
        self.assertNotIn('IntegrityError', ticket['owner_reply'])
        self.assertIn('خطأ عندنا في الخادم', ticket['owner_reply'])

    def test_vehicle_save_complaint_admits_the_server_cannot_see_it(self):
        with server.db() as c:
            c.execute("UPDATE subscriptions SET package='vip' WHERE organization_id=1")
        _, ticket, task = self.ticket('اضفت مركبة جديدة وما تنحفظ')
        self.assertEqual((task['problem_type'], task['needs_owner']), ('save', 1))
        self.assertIn('داخل جوال المشترك', task['diagnosis'])
        self.assertIn('لم يتأكد', ticket['owner_reply'])

    def test_tracking_without_any_linked_vehicle(self):
        _, ticket, task = self.ticket('تتبع المركبة مايطلع')
        self.assertEqual(task['problem_type'], 'tracking')
        self.assertIn('لم يُفعّل التتبع', ticket['owner_reply'])

    def test_new_complaint_is_flagged_understood_by_ai_then_learned(self):
        ident, ticket, task = self.ticket('الفاتورة تطلع بالانجليزي وابيها عربي')
        self.assertEqual((task['knowledge'], task['needs_owner']), ('novel', 1))
        self.assertIn('نوع جديد', ticket['owner_reply'])
        self.assertIn('ما عرفت أحلها', task['diagnosis'])
        self.assertEqual(self.call('/owner/api/v2/summary')[1]['technicalAttention'][0]['knowledge'], 'novel')
        # بدون مفتاح ذكاء لا يحاول ولا يفشل
        self.assertEqual(support.interpret_pending(server, owner_admin), 0)
        ai = FakeAI({'understood': 'المشترك يبي الفاتورة تطبع بالعربي بدل الإنجليزي.', 'likelyCause': 'غير معروف', 'canSolve': False, 'solution': '', 'needsDevelopment': True, 'missing': 'صورة من الفاتورة'})
        self.assertEqual(support.interpret_pending(server, owner_admin, client=ai), 1)
        self.assertIn('الفاتورة تطلع بالانجليزي', json.dumps(ai.calls[0], ensure_ascii=False))
        with server.db() as c:
            task = dict(c.execute('SELECT diagnosis,proposal FROM technical_tasks WHERE support_ticket_id=?', (ident,)).fetchone())
        self.assertIn('فهمي لها: المشترك يبي الفاتورة', task['diagnosis'])
        self.assertIn('ما عرفت أحلها؛ لابد من تطوير', task['proposal'])
        self.assertEqual(support.interpret_pending(server, owner_admin, client=ai), 0)  # مرة واحدة فقط
        self.assertEqual(len(ai.calls), 1)
        # إعادة الفحص الدوري لا تمسح فهم الذكاء
        with server.db() as c:
            c.execute("DELETE FROM support_ticket_events WHERE event_type='technical_evidence_report'")
            support.run_pending(c, owner_admin, server)
            self.assertIn('فهمي لها', c.execute('SELECT diagnosis FROM technical_tasks WHERE support_ticket_id=?', (ident,)).fetchone()['diagnosis'])
            c.commit()
        # المدير يغلق الطلب ويكتب الحل: الموظف التقني يتعلمه
        status, body = self.call(f'/owner/api/v2/support/{ident}', 'PUT', {'status': 'resolved', 'owner_reply': 'تم', 'note': 'من الإعدادات ← لغة الفاتورة اختر العربية ثم اطبع من جديد.'})
        self.assertEqual((status, body['learned']), (200, True))
        books = self.call('/owner/api/v2/technical-ai/playbooks')[1]['items']
        self.assertEqual([(b['ticket_id'], b['uses']) for b in books], [(ident, 0)])
        again, ticket, task = self.ticket('الفاتورة عندي تطلع بالانجليزي مو عربي')
        self.assertEqual(task['knowledge'], 'learned')
        self.assertIn('لغة الفاتورة', task['proposal'])
        self.assertIn('يشبه حالة سبق أن عالجناها', ticket['owner_reply'])
        self.assertNotIn('لغة الفاتورة', ticket['owner_reply'])  # الحل لا يُرسل للمشترك قبل موافقة الإدارة
        self.assertEqual(self.call('/owner/api/v2/technical-ai/playbooks')[1]['items'][0]['uses'], 1)
        # المدير «ينسّيه» الحل: الشكوى المشابهة ترجع جديدة
        self.assertEqual(self.call(f"/owner/api/v2/technical-ai/playbooks/{books[0]['id']}", 'DELETE')[0], 200)
        self.assertEqual(self.ticket('الفاتورة تطلع بالانجليزي مره ثانيه')[2]['knowledge'], 'novel')

    def test_ai_that_can_solve_says_so_and_a_failing_ai_is_not_retried(self):
        first, _, _ = self.ticket('ابي اغير شعار المؤسسة في الفواتير')
        ai = FakeAI({'understood': 'يبي يغير الشعار.', 'likelyCause': 'ما يعرف مكان الإعداد', 'canSolve': True, 'solution': 'من ملف المؤسسة ← الشعار ← رفع صورة.', 'needsDevelopment': False, 'missing': ''})
        self.assertEqual(support.interpret_pending(server, owner_admin, client=ai), 1)
        with server.db() as c:
            proposal = c.execute('SELECT proposal FROM technical_tasks WHERE support_ticket_id=?', (first,)).fetchone()['proposal']
            reply = c.execute('SELECT owner_reply FROM support_tickets WHERE id=?', (first,)).fetchone()['owner_reply']
        self.assertIn('أعرف أحلها', proposal)
        self.assertIn('الشعار', proposal)
        self.assertNotIn('رفع صورة', reply)  # اقتراح الذكاء للمدير فقط
        second, _, _ = self.ticket('التقرير الشهري ما يتصدر اكسل')
        broken = FakeAI(fail=True)
        self.assertEqual(support.interpret_pending(server, owner_admin, client=broken), 0)
        self.assertEqual(support.interpret_pending(server, owner_admin, client=broken), 0)
        self.assertEqual(len(broken.calls), 1)
        with server.db() as c:
            self.assertIn('ما عرفت أحلها', c.execute('SELECT diagnosis FROM technical_tasks WHERE support_ticket_id=?', (second,)).fetchone()['diagnosis'])


if __name__ == '__main__':
    unittest.main()
