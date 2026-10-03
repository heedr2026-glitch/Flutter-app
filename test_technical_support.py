import sqlite3
import unittest
from types import SimpleNamespace
from datetime import timedelta
import technical_support as support
import owner_admin as owner


class SupportTriageTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        schemas = [
            'organizations(id INTEGER,name TEXT)',
            'subscriptions(organization_id INTEGER,package TEXT,expires_at TEXT)',
            'users(id INTEGER,organization_id INTEGER,active INTEGER)',
            'login_failures(organization_id INTEGER,created_at TEXT)',
            'page_performance_events(organization_id INTEGER,page_name TEXT,elapsed_ms INTEGER,created_at TEXT)',
            "support_tickets(id INTEGER PRIMARY KEY,organization_id INTEGER,user_id INTEGER,category TEXT,message TEXT,status TEXT,owner_reply TEXT,created_at TEXT,updated_at TEXT,owner_reply_by TEXT NOT NULL DEFAULT '')",
            'support_ticket_events(id INTEGER PRIMARY KEY,ticket_id INTEGER,organization_id INTEGER,user_id INTEGER,actor_type TEXT,actor_name TEXT,event_type TEXT,from_status TEXT,to_status TEXT,body TEXT,created_at TEXT)',
            "technical_tasks(id INTEGER PRIMARY KEY,organization_id INTEGER,user_id INTEGER,support_ticket_id INTEGER,service TEXT,problem TEXT,severity TEXT,status TEXT,diagnosis TEXT,proposal TEXT,action_taken TEXT,result TEXT,started_at TEXT,finished_at TEXT,created_by TEXT,approved_by TEXT DEFAULT '')",
            'technical_incidents(id INTEGER PRIMARY KEY,service TEXT,organization_id INTEGER,problem TEXT,root_cause TEXT,proposal TEXT,severity TEXT,test_status TEXT,deployment_status TEXT,affected_organizations INTEGER,created_at TEXT,updated_at TEXT)',
            'technical_agent_state(id INTEGER PRIMARY KEY,status TEXT,last_heartbeat TEXT,last_check TEXT,last_task TEXT,last_success TEXT,updated_at TEXT)',
            'platform_audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT,target TEXT,created_at TEXT)',
        ]
        for schema in schemas:
            self.c.execute('CREATE TABLE ' + schema)
        self.c.execute("INSERT INTO organizations VALUES(1,'first'),(2,'second')")
        self.c.execute("INSERT INTO subscriptions VALUES(1,'vip',NULL)")
        self.c.execute('INSERT INTO users VALUES(1,1,1)')
        ts = support.utcnow().isoformat()
        self.c.execute('INSERT INTO login_failures VALUES(2,?)', (ts,))
        self.c.execute("INSERT INTO page_performance_events VALUES(2,'private',9000,?)", (ts,))
        old = (support.utcnow()-timedelta(days=9)).isoformat()
        self.c.execute("INSERT INTO support_tickets VALUES(1,1,1,'واتساب','ما يستقبل','in_progress','',?,?,'')", (old, old))
        self.server = SimpleNamespace(DATABASE_URL='', ApiError=RuntimeError)

    def tearDown(self):
        self.c.close()

    def test_old_ticket_gets_real_report_update_and_admin_alert_without_false_resolution(self):
        reports = support.run_pending(self.c, owner, self.server)
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertFalse(r['verifiedResolved'])
        self.assertEqual(r['severity'], 'critical')
        self.assertIn('غير متاح', ' '.join(r['limitations']))
        self.assertIn('0 محاولة', next(x['details'] for x in r['checks'] if x['key']=='login'))
        self.assertNotIn('9000', str(r))
        ticket = self.c.execute('SELECT * FROM support_tickets').fetchone()
        self.assertEqual(ticket['status'], 'under_review')
        self.assertIn('لم نعلن حلها', ticket['owner_reply'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM technical_incidents').fetchone()[0], 1)
        self.assertEqual(support.run_pending(self.c, owner, self.server), [])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM technical_tasks').fetchone()[0], 1)

    def test_completion_requires_documented_verification_and_recorded_approval(self):
        with self.assertRaises(ValueError): support.completion_evidence({}, {})
        data={'verified':True,'productionChanged':True,'actionTaken':'تم تصحيح إعداد الخدمة بعد الموافقة','verificationEvidence':'اختبار استقبال رسالة تجريبية نجح الساعة 12:00'}
        with self.assertRaises(ValueError): support.completion_evidence(data, {})
        self.assertEqual(support.completion_evidence(data, {'approved_by':'مدير'})[1],data['verificationEvidence'])

    def test_actual_task_endpoint_rejects_empty_completion(self):
        report=support.run_pending(self.c,owner,self.server)[0]
        with self.assertRaises(ValueError):
            owner.dispatch(self.c,'technical-ai/tasks/'+str(report['taskId'])+'/complete','POST',{}, {},1,{'name':'مدير','role':'owner'},None,self.server)
        self.assertEqual(self.c.execute('SELECT status FROM support_tickets').fetchone()[0],'under_review')

    def test_documented_completion_updates_ticket_and_preserves_evidence(self):
        report=support.run_pending(self.c,owner,self.server)[0]
        data={'verified':True,'productionChanged':False,'actionTaken':'شرح إعادة التجربة للمشترك والتحقق من النتيجة','verificationEvidence':'أكد المشترك وصول الرسالة بعد إعادة التجربة الساعة 12:00'}
        owner.dispatch(self.c,'technical-ai/tasks/'+str(report['taskId'])+'/complete','POST',data,{},1,{'name':'مدير','role':'owner'},None,self.server)
        self.assertEqual(self.c.execute('SELECT status FROM support_tickets').fetchone()[0],'resolved')
        self.assertIn(data['verificationEvidence'],self.c.execute('SELECT result FROM technical_tasks').fetchone()[0])


if __name__ == '__main__': unittest.main()
