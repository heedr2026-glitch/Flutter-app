"""اختبارات إصلاحات لوحة الإدارة: الهدايا، رد المدير، سجل الأرصدة، القفل، الإعلانات، الباقات."""
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
import technical_support
from test_advertisements import test_db

KEY = 'fixes-test-key'


def iso(days=0, seconds=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, seconds=seconds)).isoformat()


class OwnerAdminFixesTest(unittest.TestCase):
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
        server._RATE_LIMIT_BUCKETS.clear()
        server.init_db()
        with server.db() as c:
            c.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('مؤسسة الاختبار', server.now()))
            c.execute('INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(1,1,?,?,?,?,?,?,?,?)', ('مالك', 'owner1', 'owner1@example.com', '966500000001', 'x', 'x', 'admin', server.now()))
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

    def subscription(self):
        with server.db() as c:
            return dict(c.execute('SELECT package,expires_at FROM subscriptions WHERE organization_id=1').fetchone())

    def set_subscription(self, package, expires_at):
        with server.db() as c:
            c.execute('DELETE FROM subscriptions WHERE organization_id=1')
            c.execute('INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(1,?,?,?)', (package, server.now(), expires_at))

    def test_vip_gift_keeps_the_paid_subscription_and_restores_it(self):
        paid_until = iso(days=300)
        self.set_subscription('basic', paid_until)
        status, _ = self.call('/owner/api/v2/organizations/1/reward', 'POST', {'kind': 'vip', 'amount': 7, 'reason': 'هدية'})
        self.assertEqual(status, 200)
        gifted = self.subscription()
        self.assertEqual(gifted['package'], 'vip')
        self.assertLess(gifted['expires_at'], iso(days=8))
        # انتهت الهدية: يرجع الاشتراك الأساسي بمدته الأصلية بدل النزول للمجانية.
        with server.db() as c:
            c.execute('UPDATE subscriptions SET expires_at=? WHERE organization_id=1', (iso(seconds=-5),))
        server.sweep_subscriptions_if_due(force=True)
        self.assertEqual(self.subscription(), {'package': 'basic', 'expires_at': paid_until})
        with server.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM subscription_fallbacks WHERE organization_id=1').fetchone())

    def test_days_gift_extends_remaining_time_and_free_org_gets_basic(self):
        paid_until = iso(days=100)
        self.set_subscription('basic', paid_until)
        self.assertEqual(self.call('/owner/api/v2/organizations/1/reward', 'POST', {'kind': 'days', 'amount': 10, 'reason': 'تعويض'})[0], 200)
        extended = self.subscription()
        self.assertEqual(extended['package'], 'basic')
        self.assertEqual(extended['expires_at'], (datetime.fromisoformat(paid_until) + timedelta(days=10)).isoformat())
        self.set_subscription('free', None)
        self.assertEqual(self.call('/owner/api/v2/organizations/1/reward', 'POST', {'kind': 'days', 'amount': 5, 'reason': 'تجربة'})[0], 200)
        trial = self.subscription()
        self.assertEqual(trial['package'], 'basic')
        self.assertLess(trial['expires_at'], iso(days=6))

    def test_expired_gift_without_fallback_still_downgrades(self):
        self.set_subscription('vip', iso(seconds=-5))
        server.sweep_subscriptions_if_due(force=True)
        self.assertEqual(self.subscription(), {'package': 'free', 'expires_at': None})

    def test_manual_package_change_is_audited_and_rejects_missing_organization(self):
        self.set_subscription('basic', iso(days=30))
        self.assertEqual(self.call('/owner/api/organizations/99999/package', 'PUT', {'package': 'vip', 'durationDays': 30})[0], 404)
        status, body = self.call('/owner/api/organizations/1/package', 'PUT', {'package': 'vip', 'durationDays': 30})
        self.assertEqual((status, body['package']), (200, 'vip'))
        with server.db() as c:
            row = c.execute("SELECT target FROM platform_audit WHERE action='subscription_changed'").fetchone()
        detail = json.loads(row['target'])
        self.assertEqual((detail['organization_id'], detail['package'], detail['previous']['package']), (1, 'vip', 'basic'))

    def test_admin_reply_survives_the_support_monitor(self):
        with server.db() as c:
            c.execute("INSERT INTO support_tickets(id,organization_id,user_id,category,message,status,created_at,updated_at) VALUES(1,1,1,'مشكلة','التطبيق لا يفتح','open',?,?)", (iso(days=-2), iso(days=-2)))
        status, _ = self.call('/owner/api/v2/support/1', 'PUT', {'status': 'in_progress', 'owner_reply': 'تواصلنا معك هاتفيًا وجاري الحل'})
        self.assertEqual(status, 200)
        with server.db() as c:
            reports = technical_support.run_pending(c, owner_admin, server)
            self.assertEqual(len(reports), 1)
            ticket = c.execute('SELECT status,owner_reply,owner_reply_by FROM support_tickets WHERE id=1').fetchone()
            self.assertEqual((ticket['status'], ticket['owner_reply'], ticket['owner_reply_by']), ('in_progress', 'تواصلنا معك هاتفيًا وجاري الحل', 'admin'))
            # تقرير الفحص يبقى محفوظًا في السجل حتى مع وجود رد المدير.
            self.assertIsNotNone(c.execute("SELECT 1 FROM support_ticket_events WHERE ticket_id=1 AND event_type='technical_evidence_report'").fetchone())

    def test_automation_still_replies_when_no_admin_replied(self):
        with server.db() as c:
            c.execute("INSERT INTO support_tickets(id,organization_id,user_id,category,message,status,created_at,updated_at) VALUES(2,1,1,'مشكلة','بطء','open',?,?)", (iso(days=-2), iso(days=-2)))
            technical_support.run_pending(c, owner_admin, server)
            ticket = c.execute('SELECT owner_reply,owner_reply_by FROM support_tickets WHERE id=2').fetchone()
        self.assertIn('لم نعلن حلها', ticket['owner_reply'])
        self.assertEqual(ticket['owner_reply_by'], 'auto')

    def test_credit_ledger_stores_reason_and_actor_in_their_columns(self):
        status, body = self.call('/owner/api/v2/credits/1', 'POST', {'service': 'ai', 'units': 50, 'reason': 'تعويض عن عطل'})
        self.assertEqual(status, 200)
        with server.db() as c:
            row = c.execute('SELECT reason,actor FROM platform_credit_ledger WHERE organization_id=1').fetchone()
        self.assertEqual((row['reason'], row['actor']), ('تعويض عن عطل', 'المالك'))
        self.assertEqual(body['credits']['ledger'][0]['actor'], 'المالك')

    def test_old_swapped_ledger_rows_are_repaired_once(self):
        with server.db() as c:
            c.execute("INSERT INTO platform_credit_ledger(organization_id,service,units,reason,actor,created_at) VALUES(1,'ai',5,'المالك','سبب قديم',?)", (server.now(),))
            c.execute("INSERT INTO platform_credit_ledger(organization_id,service,units,reason,actor,created_at) VALUES(1,'ai',6,'سبب سليم','المالك',?)", (server.now(),))
            owner_admin.migrate(c)
            owner_admin.migrate(c)
            rows = [(r['units'], r['reason'], r['actor']) for r in c.execute('SELECT units,reason,actor FROM platform_credit_ledger ORDER BY units')]
        self.assertEqual(rows, [(5, 'سبب قديم', 'المالك'), (6, 'سبب سليم', 'المالك')])

    def test_heartbeat_saves_state(self):
        with patch.dict(os.environ, {'KHDOOM_TECHNICAL_AI_SECRET': 'agent-secret'}):
            status, body = self.call('/owner/api/v2/technical-ai/heartbeat', 'POST', {'task': 'فحص', 'check': 'whatsapp'}, {'X-Technical-AI-Secret': 'agent-secret'})
        self.assertEqual((status, body['status']), (200, 'online'))

    def test_login_lockout_is_per_client_address_not_per_proxy(self):
        password = 'a-long-admin-password'
        self.assertEqual(self.call('/owner/api/v2/admins', 'POST', {'name': 'مدير', 'username': 'boss', 'role': 'manager', 'password': password})[0], 200)
        for _ in range(10):
            self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'boss', 'password': 'wrong'}, {'X-Forwarded-For': '203.0.113.9', 'X-Owner-Key': ''})[0], 401)
        # نفس العنوان مقفول، وعنوان عميل آخر خلف نفس الموازن يدخل طبيعيًا.
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'boss', 'password': password}, {'X-Forwarded-For': '203.0.113.9', 'X-Owner-Key': ''})[0], 429)
        status, body = self.call('/owner/api/v2/login', 'POST', {'username': 'boss', 'password': password}, {'X-Forwarded-For': '198.51.100.7', 'X-Owner-Key': ''})
        self.assertEqual(status, 200)
        self.assertTrue(body['token'])

    def test_rate_limit_buckets_follow_the_forwarded_address(self):
        codes = [self.call('/owner/api/v2/me', headers={'X-Forwarded-For': '203.0.113.50'})[0] for _ in range(server._RATE_LIMIT_MAX_REQUESTS + 5)]
        self.assertIn(429, codes)
        self.assertEqual(self.call('/owner/api/v2/me', headers={'X-Forwarded-For': '198.51.100.99'})[0], 200)

    def test_platform_ad_draft_is_not_published_until_asked(self):
        status, body = self.call('/owner/api/platform-ads', 'POST', {'title': 'عرض', 'message': 'نص', 'status': 'draft'})
        self.assertEqual((status, body['published']), (201, False))
        with server.db() as c:
            row = c.execute('SELECT active,published_at FROM platform_advertisements WHERE id=?', (body['id'],)).fetchone()
        self.assertEqual((row['active'], row['published_at']), (0, None))
        self.assertEqual(self.call(f"/owner/api/platform-ads/{body['id']}", 'PUT', {'active': True})[0], 200)
        with server.db() as c:
            row = c.execute('SELECT active,published_at FROM platform_advertisements WHERE id=?', (body['id'],)).fetchone()
        self.assertEqual(row['active'], 1)
        self.assertIsNotNone(row['published_at'])
        status, body = self.call('/owner/api/platform-ads', 'POST', {'title': 'منشور', 'message': 'نص'})
        self.assertEqual((status, body['published']), (201, True))
        self.assertEqual(self.call('/owner/api/platform-ads/987654', 'PUT', {'active': True})[0], 404)

    def test_package_limits_reject_unknown_package_names(self):
        status, _ = self.call('/owner/api/package-limits', 'PUT', {'<img src=x onerror=alert(1)>': {'users': 1, 'vehicles': 1}})
        self.assertEqual(status, 400)
        status, public = self.call('/api/package-limits')
        self.assertEqual(status, 200)
        self.assertEqual(sorted(public), ['basic', 'free', 'vip'])

    def test_service_health_reports_monitor_state_and_no_fake_ready(self):
        status, body = self.call('/owner/api/v2/service-health')
        self.assertEqual(status, 200)
        services = {item['service']: item for item in body['services']}
        self.assertNotIn('payment', services)
        self.assertEqual(services['whatsapp']['status'], 'not_connected')
        for item in body['services']:
            self.assertIn(item['status'], ('ready', 'warning', 'error', 'unknown', 'not_configured', 'not_connected'))

    def test_account_deletion_requests_are_listed_and_deletion_is_audited(self):
        with server.db() as c:
            server.create_account_deletion_request(c, 'owner1@example.com')
        status, body = self.call('/owner/api/account-deletion-requests')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['items']), 1)
        item = body['items'][0]
        self.assertEqual((item['organization_id'], item['organization_name'], item['status']), (1, 'مؤسسة الاختبار', 'pending_verification'))
        self.assertEqual(self.call('/owner/api/account-deletion-requests/' + item['request_id'], 'PUT', {'action': 'verify'})[0], 200)
        self.assertEqual(self.call('/owner/api/account-deletion-requests/' + item['request_id'], 'PUT', {'action': 'complete'})[0], 200)
        with server.db() as c:
            audited = c.execute("SELECT target FROM platform_audit WHERE action='organization_deleted'").fetchone()
            self.assertIsNone(c.execute('SELECT 1 FROM organizations WHERE id=1').fetchone())
        self.assertEqual(json.loads(audited['target'])['name'], 'مؤسسة الاختبار')

    def test_audit_log_is_not_erased_after_thirty_days(self):
        with server.db() as c:
            c.execute("INSERT INTO platform_audit(actor,action,target,created_at) VALUES('المالك','old_action','x',?)", (iso(days=-200),))
            c.execute("INSERT INTO platform_audit(actor,action,target,created_at) VALUES('المالك','ancient_action','x',?)", (iso(days=-500),))
        self.assertEqual(self.call('/owner/api/v2/security?type=audit')[0], 200)
        with server.db() as c:
            actions = {r['action'] for r in c.execute("SELECT action FROM platform_audit WHERE action IN ('old_action','ancient_action')")}
        self.assertEqual(actions, {'old_action'})

    def test_transfer_renewal_adds_time_on_top_of_the_remaining_period(self):
        paid_until = iso(days=20)
        self.set_subscription('basic', paid_until)
        with server.db() as c:
            c.execute("INSERT INTO subscription_requests(id,organization_id,requested_package,paid_months,created_at) VALUES(1,1,'basic',1,?)", (server.now(),))
            c.execute("INSERT INTO subscription_requests(id,organization_id,requested_package,paid_months,created_at) VALUES(2,1,'vip',1,?)", (server.now(),))
        self.assertEqual(self.call('/owner/api/subscription-requests/1', 'PUT', {'action': 'approve', 'durationDays': 30})[0], 200)
        renewed = self.subscription()
        self.assertEqual(renewed, {'package': 'basic', 'expires_at': (datetime.fromisoformat(paid_until) + timedelta(days=30)).isoformat()})
        # الترقية إلى باقة أخرى تبدأ من اليوم كما كانت.
        self.assertEqual(self.call('/owner/api/subscription-requests/2', 'PUT', {'action': 'approve', 'durationDays': 30})[0], 200)
        upgraded = self.subscription()
        self.assertEqual(upgraded['package'], 'vip')
        self.assertLess(upgraded['expires_at'], iso(days=31))
        # الطلب المعالج لا يُعتمد مرتين.
        self.assertEqual(self.call('/owner/api/subscription-requests/1', 'PUT', {'action': 'approve'})[0], 404)

    def test_community_reward_really_extends_the_subscription_and_admin_like_counts(self):
        paid_until = iso(days=40)
        self.set_subscription('basic', paid_until)
        with server.db() as c:
            c.execute("INSERT INTO community_posts(id,user_id,body,created_at) VALUES(1,1,'منشور مفيد',?)", (server.now(),))
        self.assertEqual(self.call('/owner/api/v2/community/rewards/1', 'POST', {'kind': 'days', 'amount': 7})[0], 200)
        self.assertEqual(self.subscription()['expires_at'], (datetime.fromisoformat(paid_until) + timedelta(days=7)).isoformat())
        self.assertEqual(self.call('/owner/api/v2/community/likes/1', 'POST', {})[0], 200)
        status, posts = self.call('/owner/api/v2/community/posts')
        self.assertEqual((status, posts['items'][0]['like_count']), (200, 1))

    def test_opening_a_member_chat_clears_the_unread_counter(self):
        with server.db() as c:
            c.execute("INSERT INTO community_messages(user_id,sender,body,created_at,read) VALUES(1,'user','مرحبا',?,0)", (server.now(),))
        self.assertEqual(self.call('/owner/api/v2/community/chat')[1]['items'][0]['unread'], 1)
        self.assertEqual(len(self.call('/owner/api/v2/community/chat/1')[1]['items']), 1)
        self.assertEqual(self.call('/owner/api/v2/community/chat')[1]['items'][0]['unread'], 0)

    def staff(self, username, role):
        """ينشئ موظفًا إداريًا بدوره الجاهز ويعيد ترويسة جلسته."""
        password = 'staff-password-' + username
        self.assertEqual(self.call('/owner/api/v2/admins', 'POST', {'name': username, 'username': username, 'role': role, 'password': password})[0], 200)
        status, body = self.call('/owner/api/v2/login', 'POST', {'username': username, 'password': password}, {'X-Owner-Key': ''})
        self.assertEqual(status, 200)
        return {'X-Owner-Key': '', 'X-Admin-Session': body['token']}

    def test_sensitive_actions_need_a_senior_role(self):
        with server.db() as c:
            c.execute("INSERT INTO support_tickets(id,organization_id,user_id,category,message,status,created_at,updated_at) VALUES(7,1,1,'مشكلة','نص','open',?,?)", (server.now(), server.now()))
        employee = self.staff('emp1', 'employee')
        accounting = self.staff('acc1', 'accounting')
        technician = self.staff('tech1', 'technician')
        manager = self.staff('mgr1', 'manager')
        iban = {'bankName': 'بنك', 'accountName': 'خدوم', 'iban': 'SA' + '1' * 22}
        self.assertEqual(self.call('/owner/api/v2/support/7', 'DELETE', headers=employee)[0], 403)
        self.assertEqual(self.call('/owner/api/payment-settings', 'PUT', iban, accounting)[0], 403)
        self.assertEqual(self.call('/owner/api/security/emergency', 'PUT', {'service': 'chat', 'enabled': False, 'hours': 1}, technician)[0], 403)
        with server.db() as c:
            self.assertIsNotNone(c.execute('SELECT 1 FROM support_tickets WHERE id=7').fetchone())
        self.assertEqual(self.call('/owner/api/payment-settings', 'PUT', iban, manager)[0], 200)
        self.assertEqual(self.call('/owner/api/v2/support/7', 'DELETE', headers=manager)[0], 200)
        with server.db() as c:
            self.assertIsNotNone(c.execute("SELECT 1 FROM platform_audit WHERE action='support_ticket_deleted'").fetchone())

    def test_any_admin_sees_service_health_but_not_other_sections(self):
        ads = self.staff('ads1', 'ads')
        self.assertEqual(self.call('/owner/api/v2/service-health', headers=ads)[0], 200)
        self.assertEqual(self.call('/owner/api/v2/service-health/check', 'POST', {'service': 'server'}, ads)[0], 403)
        self.assertEqual(self.call('/owner/api/v2/security-center', headers=ads)[0], 403)
        self.assertEqual(self.call('/owner/api/v2/admins', headers=ads)[0], 403)

    def test_admin_changes_own_password_and_old_one_stops_working(self):
        session = self.staff('self1', 'support')
        old = 'staff-password-self1'
        self.assertEqual(self.call('/owner/api/v2/me/password', 'POST', {'current': 'wrong', 'password': 'a-brand-new-password'}, session)[0], 403)
        self.assertEqual(self.call('/owner/api/v2/me/password', 'POST', {'current': old, 'password': 'short'}, session)[0], 400)
        self.assertEqual(self.call('/owner/api/v2/me/password', 'POST', {'current': old, 'password': 'a-brand-new-password'}, session)[0], 200)
        self.assertEqual(self.call('/owner/api/v2/me', headers=session)[0], 200)
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'self1', 'password': old}, {'X-Owner-Key': ''})[0], 401)
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'self1', 'password': 'a-brand-new-password'}, {'X-Owner-Key': ''})[0], 200)
        # حساب المالك بالمفتاح ليس له كلمة مرور هنا.
        self.assertEqual(self.call('/owner/api/v2/me/password', 'POST', {'current': 'x', 'password': 'a-brand-new-password'})[0], 400)

    def seed_directory(self):
        with server.db() as c:
            c.execute("UPDATE organizations SET phone='966511111111' WHERE id=1")
            c.execute('INSERT INTO organizations(id,name,phone,created_at) VALUES(2,?,?,?)', ('مغسلة النخبة', '966522222222', server.now()))
            c.execute('INSERT INTO organizations(id,name,phone,created_at) VALUES(3,?,?,?)', ('ورشة الأمانة', '966533333333', server.now()))
            c.execute('DELETE FROM subscriptions')
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(1,'free',?,NULL)", (server.now(),))
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(2,'basic',?,?)", (server.now(), iso(days=5)))
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(3,'vip',?,?)", (server.now(), iso(days=200)))
            c.execute('INSERT INTO platform_org_state(organization_id,suspended) VALUES(3,1)')

    def test_directory_search_filters_and_sorting(self):
        self.seed_directory()
        names = lambda query: [x['name'] for x in self.call('/owner/api/v2/directory/organizations?' + query)[1]['items']]
        self.assertEqual(names('search=96652222'), ['مغسلة النخبة'])
        self.assertEqual(names('status=expiring'), ['مغسلة النخبة'])
        self.assertEqual(names('status=suspended'), ['ورشة الأمانة'])
        self.assertEqual(names('status=paid'), ['ورشة الأمانة', 'مغسلة النخبة'])
        self.assertEqual(names('sort=expiry')[:2], ['مغسلة النخبة', 'ورشة الأمانة'])
        self.assertEqual(names('sort=oldest')[0], 'مؤسسة الاختبار')
        row = self.call('/owner/api/v2/directory/organizations?status=suspended')[1]['items'][0]
        self.assertEqual((row['suspended'], row['package']), (1, 'vip'))

    def test_summary_lists_expiring_paid_subscriptions_and_counts_only_paid(self):
        self.seed_directory()
        status, summary = self.call('/owner/api/v2/summary')
        self.assertEqual(status, 200)
        self.assertEqual([x['name'] for x in summary['expiringList']], ['مغسلة النخبة'])
        # المدفوع الساري غير الموقوف: المغسلة فقط (الورشة موقوفة، والأولى مجانية).
        self.assertEqual(summary['activeSubscribers'], 1)
        self.assertEqual(summary['lowBalanceOrganizations'], 0)

    def test_archived_organization_can_be_restored_by_owner_only(self):
        manager = self.staff('mgr2', 'manager')
        self.assertEqual(self.call('/owner/api/v2/directory/organizations/1/archive', 'POST', {'confirmation': 'مؤسسة الاختبار'})[0], 200)
        with server.db() as c:
            self.assertTrue(owner_admin.suspended(c, 1))
        self.assertEqual(self.call('/owner/api/v2/directory/archived', headers=manager)[0], 403)
        status, archived = self.call('/owner/api/v2/directory/archived')
        self.assertEqual((status, [x['id'] for x in archived['organizations']]), (200, [1]))
        self.assertEqual(self.call('/owner/api/v2/directory/organizations/1/restore', 'POST', {}, manager)[0], 403)
        self.assertEqual(self.call('/owner/api/v2/directory/organizations/1/restore', 'POST', {})[0], 200)
        with server.db() as c:
            self.assertFalse(owner_admin.suspended(c, 1))
        self.assertEqual(self.call('/owner/api/v2/directory/organizations/1/restore', 'POST', {})[0], 404)

    def test_subscription_requests_carry_receipt_only_while_pending(self):
        self.set_subscription('free', None)
        receipt = 'data:image/png;base64,AAAA'
        with server.db() as c:
            c.execute("INSERT INTO subscription_requests(id,organization_id,requested_package,transfer_name,transfer_receipt,quoted_price,status,created_at) VALUES(1,1,'basic','محمد',?,49,'pending',?)", (receipt, server.now()))
            c.execute("INSERT INTO subscription_requests(id,organization_id,requested_package,transfer_name,transfer_receipt,quoted_price,status,created_at) VALUES(2,1,'vip','علي',?,99,'approved',?)", (receipt, server.now()))
        status, items = self.call('/owner/api/subscription-requests')
        self.assertEqual(status, 200)
        by_id = {x['id']: x for x in items}
        self.assertEqual((by_id[1]['transfer_receipt'], by_id[1]['has_receipt'], by_id[1]['transfer_name']), (receipt, 1, 'محمد'))
        self.assertEqual((by_id[2]['transfer_receipt'], by_id[2]['has_receipt']), ('', 1))

    def test_expense_dates_must_be_real_dates(self):
        bad = {'provider': 'Render', 'issued_at': 'بكرة', 'due_at': '2026-10-15', 'subtotal': 10}
        self.assertEqual(self.call('/owner/api/v2/expenses', 'POST', bad)[0], 400)
        status, body = self.call('/owner/api/v2/expenses', 'POST', {**bad, 'issued_at': '2026-10-01'})
        self.assertEqual(status, 200)
        self.assertTrue(body['id'])

    def test_credits_page_matches_per_organization_summary(self):
        self.seed_directory()
        with server.db() as c:
            for _ in range(3):
                c.execute("INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(1,1,'x',?)", (server.now(),))
            c.execute("INSERT INTO platform_credit_ledger(organization_id,service,units,reason,actor,created_at) VALUES(1,'ai',40,'هدية','المالك',?)", (server.now(),))
        status, page = self.call('/owner/api/v2/credits')
        self.assertEqual((status, page['total']), (200, 3))
        first = next(x for x in page['items'] if x['id'] == 1)['credits']
        self.assertEqual((first['services']['ai']['used_month'], first['services']['ai']['adjustments']), (3, 40))
        self.assertEqual(self.call('/owner/api/v2/organizations/1')[1]['credits'], first)

    def test_html_pages_send_basic_security_headers(self):
        with urlopen(f'http://127.0.0.1:{self.httpd.server_port}/owner', timeout=10) as response:
            self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_two_step_login_setup_enforcement_replay_and_owner_reset(self):
        session = self.staff('otp1', 'support')
        password = 'staff-password-otp1'
        self.assertEqual(self.call('/owner/api/v2/me/2fa', headers=session)[1], {'enabled': False})
        status, setup = self.call('/owner/api/v2/me/2fa/setup', 'POST', {}, session)
        self.assertEqual(status, 200)
        secret = setup['secret']
        self.assertIn(secret, setup['uri'])
        self.assertEqual(self.call('/owner/api/v2/me/2fa/enable', 'POST', {'code': '000000'}, session)[0], 400)
        # قبل التفعيل لا يُطلب رمز عند الدخول.
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'otp1', 'password': password}, {'X-Owner-Key': ''})[0], 200)
        now_step = int(__import__('time').time() // 30)
        self.assertEqual(self.call('/owner/api/v2/me/2fa/enable', 'POST', {'code': owner_admin.totp_at(secret, now_step)}, session)[0], 200)
        status, body = self.call('/owner/api/v2/login', 'POST', {'username': 'otp1', 'password': password}, {'X-Owner-Key': ''})
        self.assertEqual((status, body.get('needsCode')), (401, True))
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'otp1', 'password': password, 'code': '123456'}, {'X-Owner-Key': ''})[0], 401)
        # الرمز الذي استُخدم للتفعيل لا يُقبل مرة ثانية؛ رمز الفترة التالية يُقبل.
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'otp1', 'password': password, 'code': owner_admin.totp_at(secret, now_step)}, {'X-Owner-Key': ''})[0], 401)
        status, body = self.call('/owner/api/v2/login', 'POST', {'username': 'otp1', 'password': password, 'code': owner_admin.totp_at(secret, now_step + 1)}, {'X-Owner-Key': ''})
        self.assertEqual(status, 200)
        fresh = {'X-Owner-Key': '', 'X-Admin-Session': body['token']}
        self.assertEqual(self.call('/owner/api/v2/me/2fa/disable', 'POST', {'current': 'wrong', 'code': '1'}, fresh)[0], 403)
        admins = self.call('/owner/api/v2/admins')[1]['items']
        target = next(x for x in admins if x['username'] == 'otp1')
        self.assertEqual(target['totp_enabled'], 1)
        # المالك يصفّر التحقق لمن فقد جواله.
        self.assertEqual(self.call(f"/owner/api/v2/admins/{target['id']}", 'PUT', {'name': 'otp1', 'username': 'otp1', 'role': 'support', 'reset2fa': True})[0], 200)
        self.assertEqual(self.call('/owner/api/v2/login', 'POST', {'username': 'otp1', 'password': password}, {'X-Owner-Key': ''})[0], 200)
        self.assertEqual(self.call('/owner/api/v2/me/2fa', headers={'X-Owner-Key': KEY})[0], 400)

    def test_totp_matches_the_published_reference_vectors(self):
        # RFC 6238 Appendix B, SHA-1, secret "12345678901234567890" (آخر 6 أرقام من القيم المرجعية).
        secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        self.assertEqual(owner_admin.totp_at(secret, 59 // 30), '287082')
        self.assertEqual(owner_admin.totp_at(secret, 1111111109 // 30), '081804')
        self.assertEqual(owner_admin.totp_at(secret, 1234567890 // 30), '005924')
        self.assertEqual(owner_admin.totp_match(secret, '287082', now=59), 1)
        self.assertIsNone(owner_admin.totp_match(secret, '287082', now=59 + 120))

    def test_approved_transfer_and_manual_payment_feed_the_revenue_report(self):
        self.set_subscription('free', None)
        with server.db() as c:
            c.execute("INSERT INTO subscription_requests(id,organization_id,requested_package,paid_months,quoted_price,discount_code,created_at) VALUES(1,1,'basic',3,139,'SAVE',?)", (server.now(),))
            c.execute("INSERT INTO subscription_requests(id,organization_id,requested_package,paid_months,quoted_price,status,processed_at,created_at) VALUES(2,1,'vip',1,99,'approved',?,?)", (iso(days=-40), iso(days=-41)))
            owner_admin.migrate(c)
            owner_admin.migrate(c)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM platform_payments').fetchone()['n'], 1)
        self.assertEqual(self.call('/owner/api/subscription-requests/1', 'PUT', {'action': 'approve', 'durationDays': 90, 'amount': 130})[0], 200)
        self.assertEqual(self.call('/owner/api/v2/finance/payments', 'POST', {'organization_id': 1, 'package': 'vip', 'months': 1, 'amount': 99, 'note': 'نقدًا'})[0], 200)
        self.assertEqual(self.call('/owner/api/v2/finance/payments', 'POST', {'organization_id': 999, 'package': 'vip', 'months': 1, 'amount': 99, 'note': 'x'})[0], 404)
        status, report = self.call('/owner/api/v2/finance/revenue?months=3')
        self.assertEqual(status, 200)
        self.assertEqual((report['thisMonth']['total'], report['thisMonth']['count']), (229.0, 2))
        self.assertEqual((report['thisMonth']['basic'], report['thisMonth']['vip']), (130.0, 99.0))
        self.assertEqual(report['periodTotal'], 328.0)
        self.assertEqual(len(report['months']), 3)
        transfer = next(x for x in report['payments'] if x['source'] == 'transfer' and x['amount'] == 130)
        self.assertEqual((transfer['months'], transfer['discount_code'], transfer['organization_name']), (3, 'SAVE', 'مؤسسة الاختبار'))
        self.assertEqual(self.call('/owner/api/v2/expenses')[1]['actualIncome'], 229.0)

    def test_exports_follow_permissions_and_are_audited(self):
        self.seed_directory()
        status, data = self.call('/owner/api/v2/export/organizations')
        self.assertEqual((status, len(data['rows']), data['truncated']), (200, 3, False))
        self.assertEqual([c[0] for c in data['columns']][:3], ['id', 'name', 'phone'])
        self.assertTrue(data['filename'].endswith('.csv'))
        accounting = self.staff('acc2', 'accounting')
        support = self.staff('sup2', 'support')
        self.assertEqual(self.call('/owner/api/v2/export/payments', headers=accounting)[0], 200)
        self.assertEqual(self.call('/owner/api/v2/export/expenses', headers=accounting)[0], 200)
        self.assertEqual(self.call('/owner/api/v2/export/audit', headers=accounting)[0], 403)
        self.assertEqual(self.call('/owner/api/v2/export/payments', headers=support)[0], 403)
        self.assertEqual(self.call('/owner/api/v2/export/unknown')[0], 404)
        audit = self.call('/owner/api/v2/export/audit')[1]
        self.assertTrue(any(row['action'] == 'data_export' for row in audit['rows']))

    def test_server_timings_and_light_technical_state(self):
        server._REQUEST_TIMINGS.clear()
        self.call('/owner/api/v2/summary')
        self.call('/owner/api/v2/organizations/1')
        # القياس يُسجَّل بعد إرسال الرد بلحظة، فنعيد القراءة حتى يظهر الطلب السابق.
        for _ in range(40):
            status, timings = self.call('/owner/api/v2/service-health/timings?minutes=5')
            self.assertEqual(status, 200)
            routes = {(x['method'], x['route']) for x in timings['routes']}
            if ('GET', '/owner/api/v2/organizations/:id') in routes:
                break
            __import__('time').sleep(0.05)
        self.assertIn(('GET', '/owner/api/v2/summary'), routes)
        self.assertIn(('GET', '/owner/api/v2/organizations/:id'), routes)
        self.assertIsInstance(timings['databaseLatencyMs'], float)
        ads = self.staff('ads2', 'ads')
        self.assertEqual(self.call('/owner/api/v2/service-health/timings', headers=ads)[0], 403)
        status, state = self.call('/owner/api/v2/technical-ai/state', headers=ads)
        self.assertEqual((status, state['state']['status']), (200, 'offline'))

    def test_support_list_keeps_notes_events_and_task_per_ticket(self):
        with server.db() as c:
            for ticket in (1, 2):
                c.execute("INSERT INTO support_tickets(id,organization_id,user_id,category,message,status,created_at,updated_at) VALUES(?,1,1,'مشكلة','نص','open',?,?)", (ticket, server.now(), server.now()))
        self.assertEqual(self.call('/owner/api/v2/support/1', 'PUT', {'status': 'open', 'note': 'ملاحظة داخلية'})[0], 200)
        status, page = self.call('/owner/api/v2/support')
        self.assertEqual((status, page['total']), (200, 2))
        by_id = {t['id']: t for t in page['items']}
        self.assertEqual([n['note'] for n in by_id[1]['notes']], ['ملاحظة داخلية'])
        self.assertEqual(by_id[2]['notes'], [])
        for ticket in by_id.values():
            self.assertEqual(ticket['technical_status'], 'diagnosing')
            self.assertTrue(ticket['technical_task_id'])
            self.assertTrue(any(e['event_type'] == 'technical_assigned' for e in ticket['events']))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM technical_tasks').fetchone()['n'], 2)
        self.call('/owner/api/v2/support')
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM technical_tasks').fetchone()['n'], 2)

    def test_suspension_check_and_last_seen_throttle(self):
        self.set_subscription('free', None)
        with server.db() as c:
            self.assertFalse(owner_admin.suspended(c, 1))
            self.assertFalse(owner_admin.suspended(c, 424242))
            c.execute('INSERT INTO platform_org_state(organization_id,suspended) VALUES(1,1)')
            self.assertTrue(owner_admin.suspended(c, 1))
            c.execute('UPDATE platform_org_state SET suspended=0 WHERE organization_id=1')
            c.execute('UPDATE organizations SET archived_at=? WHERE id=1', (server.now(),))
            self.assertTrue(owner_admin.suspended(c, 1))
            c.execute('UPDATE organizations SET archived_at=NULL WHERE id=1')
            import hashlib
            digest = hashlib.sha256(b'customer-token').hexdigest()
            c.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at,last_seen_at) VALUES(?,1,?,?,?)", (digest, iso(days=1), server.now(), iso(seconds=-10)))
        customer = {'X-Owner-Key': '', 'Authorization': 'Bearer customer-token'}
        recent = iso(seconds=-10)[:16]
        self.assertEqual(self.call('/api/subscription', headers=customer)[0], 200)
        with server.db() as c:
            # ظهور قبل عشر ثوانٍ: لا كتابة جديدة.
            self.assertEqual(c.execute('SELECT last_seen_at FROM sessions WHERE token_hash=?', (digest,)).fetchone()['last_seen_at'][:16], recent)
            c.execute('UPDATE sessions SET last_seen_at=? WHERE token_hash=?', (iso(seconds=-600), digest))
        self.assertEqual(self.call('/api/subscription', headers=customer)[0], 200)
        # الرد يُرسل قبل تثبيت العملية بلحظة، فننتظر التثبيت.
        deadline = __import__('time').time() + 3
        while True:
            with server.db() as c:
                seen = c.execute('SELECT last_seen_at FROM sessions WHERE token_hash=?', (digest,)).fetchone()['last_seen_at']
            if seen > iso(seconds=-60) or __import__('time').time() > deadline:
                break
            __import__('time').sleep(0.05)
        self.assertGreater(seen, iso(seconds=-60))

    def test_database_location_never_leaks_credentials(self):
        cases = {
            'postgresql://u:TopSecret9@dpg-abc123-a.frankfurt-postgres.render.com/khd': ('Render', 'external', '***.frankfurt-postgres.render.com'),
            'postgresql://u:TopSecret9@dpg-abc123-a/khd': ('Render', 'internal', '***'),
            'postgres://u:TopSecret9@ep-cool-123456.eu-central-1.aws.neon.tech/db?sslmode=require': ('Neon', 'external', '***.eu-central-1.aws.neon.tech'),
        }
        for url, (provider, link, host) in cases.items():
            with patch.object(server, 'DATABASE_URL', url):
                found = server.database_location()
            self.assertEqual((found['provider'], found['link'], found['host']), (provider, link, host))
            self.assertNotIn('TopSecret9', json.dumps(found))
            self.assertNotIn('dpg-abc123', json.dumps(found))
        status, timings = self.call('/owner/api/v2/service-health/timings')
        self.assertEqual((status, timings['database']['engine']), (200, 'SQLite'))


if __name__ == '__main__':
    unittest.main()
