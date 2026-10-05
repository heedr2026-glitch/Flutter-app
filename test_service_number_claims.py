"""ربط واتساب والمكالمات برقم معتمد من الإدارة فقط، وكود التفعيل العام للمخصَّص باسم المستخدم فقط."""
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
import whatsapp_bridge
from test_advertisements import test_db

KEY = 'claims-test-key'
WA_CONFIG = [{"organization_id": 99, "phone_number_id": "900", "waba_id": "777", "token": "t",
              "app_secret": "s", "verify_token": "v", "api_version": "v25.0"}]
META_NUMBERS = {"data": [
    {"id": "501", "display_phone_number": "+966 50 000 0001", "verified_name": "A"},
    {"id": "502", "display_phone_number": "+966 50 000 0002", "verified_name": "B"},
    {"id": "900", "display_phone_number": "+966 50 000 0900", "verified_name": "Platform"},
]}


class ServiceNumberClaimsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(server, 'DB_PATH', Path(self.directory.name) / 'test.db'),
            patch.object(server, 'OWNER_KEY_PATH', Path(self.directory.name) / 'owner.key'),
            patch.object(server, 'DATABASE_URL', ''),
            patch.object(server, 'db', test_db),
            patch.object(whatsapp_bridge, 'graph', lambda cfg, path, body=None: META_NUMBERS),
            patch.dict(os.environ, {'KHDOOM_OWNER_KEY': KEY, 'KHDOOM_SUBSCRIPTION_SWEEP_SECONDS': '0',
                                    'KHDOOM_WHATSAPP_CONFIG': json.dumps(WA_CONFIG)}),
        ]
        for item in self.patches:
            item.start()
        server._RATE_LIMIT_BUCKETS.clear()
        server.init_db()
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with server.db() as c:
            for number in (1, 2):
                c.execute('INSERT INTO organizations(id,name,phone,created_at) VALUES(?,?,?,?)',
                          (number, 'مؤسسة %d' % number, '96650000000%d' % number, server.now()))
                c.execute('INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (number, number, 'مدير', 'user%d' % number, 'u%d@example.com' % number, '', 'x', 'x', 'admin', server.now()))
                c.execute('INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?)',
                          (number, 'vip', server.now(), (datetime.now(timezone.utc) + timedelta(days=300)).isoformat()))
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',
                          (hashlib.sha256(('tok%d' % number).encode()).hexdigest(), number, expiry, server.now()))
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def call(self, path, method='GET', body=None, user=None):
        headers = {'Content-Type': 'application/json'}
        headers.update({'Authorization': 'Bearer tok%d' % user} if user else {'X-Owner-Key': KEY})
        request = Request(f'http://127.0.0.1:{self.httpd.server_port}{path}', method=method,
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def public(self, path, body):
        request = Request(f'http://127.0.0.1:{self.httpd.server_port}{path}', method='POST',
                          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def grant(self, organization, **numbers):
        return self.call('/owner/api/v2/organizations/%d/service-numbers' % organization, 'POST', numbers)

    # ---------- واتساب ----------
    def test_whatsapp_connect_requires_owner_grant(self):
        status, body = self.call('/api/whatsapp/connect', 'POST', {'phone': '966500000002'}, user=1)
        self.assertEqual(status, 403, body)
        with server.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM whatsapp_connections').fetchone())

    def test_whatsapp_connect_with_grant_and_other_org_blocked(self):
        self.assertEqual(self.grant(2, whatsapp='+966 50 000 0002')[0], 200)
        # مؤسسة 1 لا تستطيع أخذ رقم مؤسسة 2 حتى قبل أن تربطه صاحبته.
        self.assertEqual(self.call('/api/whatsapp/connect', 'POST', {'phone': '966500000002'}, user=1)[0], 403)
        status, body = self.call('/api/whatsapp/connect', 'POST', {'phone': '966500000002'}, user=2)
        self.assertEqual((status, body.get('connected')), (200, True), body)
        # إعادة الربط بنفس الرقم المرتبط مسموحة حتى لو أُلغي الاعتماد لاحقًا.
        self.assertEqual(self.grant(2, whatsapp='')[0], 200)
        self.assertEqual(self.call('/api/whatsapp/connect', 'POST', {'phone': '966500000002'}, user=2)[0], 200)
        with server.db() as c:
            rows = c.execute('SELECT organization_id,phone_number_id FROM whatsapp_connections').fetchall()
        self.assertEqual([(r['organization_id'], r['phone_number_id']) for r in rows], [(2, '502')])

    def test_same_number_cannot_be_granted_to_two_organizations(self):
        self.assertEqual(self.grant(1, whatsapp='966500000001')[0], 200)
        status, body = self.grant(2, whatsapp='966500000001')
        self.assertEqual(status, 400, body)
        self.assertEqual(self.grant(1, whatsapp='12')[0], 400)
        status, body = self.call('/owner/api/v2/organizations/1')
        self.assertEqual(body['service_numbers'], {'whatsapp': '966500000001', 'calls': ''})

    def test_server_configured_number_of_another_organization_is_refused(self):
        # رقم المنصة المعرّف في إعدادات الخادم لمؤسسة 99: حتى لو اعتُمد خطأً لا يُسجَّل لغيرها.
        self.assertEqual(self.grant(1, whatsapp='966500000900')[0], 200)
        status, body = self.call('/api/whatsapp/connect', 'POST', {'phone': '966500000900'}, user=1)
        self.assertEqual(status, 409, body)
        with server.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM whatsapp_connections').fetchone())
            whatsapp_bridge.configs(c)  # لا يرمي: الإعدادات بقيت سليمة للجميع

    # ---------- المكالمات ----------
    def test_calls_connect_requires_owner_grant(self):
        # مؤسسة 1 تغيّر رقمها إلى رقم مؤسسة 2 ثم تحاول الربط.
        self.assertEqual(self.call('/api/organization', 'PUT', {'name': 'مؤسسة 1', 'activity': 'x', 'phone': '966500000002'}, user=1)[0], 200)
        status, body = self.call('/api/calls/connect', 'POST', {}, user=1)
        self.assertEqual(status, 403, body)
        self.assertEqual(self.call('/api/calls/connect', 'POST', {}, user=2)[0], 403)
        self.assertEqual(self.grant(2, calls='966500000002')[0], 200)
        self.assertEqual(self.call('/api/calls/connect', 'POST', {}, user=2)[0], 200)
        self.assertEqual(self.call('/api/calls/connect', 'POST', {}, user=1)[0], 409)
        with server.db() as c:
            rows = c.execute('SELECT organization_id,phone_number FROM call_connections').fetchall()
        self.assertEqual([(r['organization_id'], r['phone_number']) for r in rows], [(2, '966500000002')])

    # ---------- كود التفعيل العام ----------
    def code(self, raw, assigned='', package='basic'):
        with server.db() as c:
            c.execute("""INSERT INTO activation_codes(code_hash,code_prefix,package,duration_days,max_uses,expires_at,recipient_name,assigned_username,code_kind,discount_percent,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (hashlib.sha256(raw.encode()).hexdigest(), raw[:4], package, 30, 5, None, '', assigned, 'activation', 0, server.now()))

    def package(self, organization):
        with server.db() as c:
            return c.execute('SELECT package FROM subscriptions WHERE organization_id=?', (organization,)).fetchone()['package']

    def test_public_activation_refuses_unassigned_code(self):
        self.code('FREECODE1')
        status, body = self.public('/api/activate-code-public', {'username': 'user2', 'code': 'FREECODE1'})
        self.assertEqual(status, 400, body)
        self.assertEqual(self.package(2), 'vip')
        # نفس الرد لاسم غير موجود: لا يكشف وجود الحسابات.
        other = self.public('/api/activate-code-public', {'username': 'nobody', 'code': 'FREECODE1'})
        self.assertEqual((other[0], other[1].get('error')), (400, body.get('error')))

    def test_public_activation_only_for_assigned_username(self):
        self.code('FORUSER1', assigned='user1')
        self.assertEqual(self.public('/api/activate-code-public', {'username': 'user2', 'code': 'FORUSER1'})[0], 400)
        self.assertEqual(self.package(2), 'vip')
        status, body = self.public('/api/activate-code-public', {'username': 'User1', 'code': 'foruser1'})
        self.assertEqual((status, body.get('package')), (200, 'basic'), body)
        self.assertEqual(self.package(1), 'basic')

    def test_logged_in_activation_of_unassigned_code_still_works(self):
        self.code('FREECODE2', package='basic')
        status, body = self.call('/api/activate-code', 'POST', {'code': 'FREECODE2'}, user=1)
        self.assertEqual(status, 200, body)
        self.assertEqual((self.package(1), self.package(2)), ('basic', 'vip'))


if __name__ == '__main__':
    unittest.main()
