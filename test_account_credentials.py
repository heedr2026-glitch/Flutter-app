"""تغيير اسم المستخدم وكلمة المرور في الخادم لصاحب الجلسة."""
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


class AccountCredentialsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(server, 'DB_PATH', Path(self.directory.name) / 'test.db'),
            patch.object(server, 'OWNER_KEY_PATH', Path(self.directory.name) / 'owner.key'),
            patch.object(server, 'DATABASE_URL', ''),
            patch.object(server, 'db', test_db),
            patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'k', 'KHDOOM_SUBSCRIPTION_SWEEP_SECONDS': '0'}),
        ]
        for item in self.patches:
            item.start()
        server._RATE_LIMIT_BUCKETS.clear()
        server.init_db()
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with server.db() as c:
            for org in (1, 2):
                c.execute('INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)', (org, 'م%d' % org, server.now()))
                c.execute('INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?)', (org, 'free', server.now(), None))
            for uid, org, username, email, role in [(1, 1, 'manager1', 'm1@example.com', 'admin'), (2, 1, 'worker1', '', 'employee'), (3, 2, 'manager2', 'boss@example.com', 'admin')]:
                digest, salt = server.hash_password('oldpass123')
                c.execute('INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (uid, org, 'مستخدم', username, email, '', digest, salt, role, server.now()))
                for device in ('a', 'b'):
                    c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',
                              (hashlib.sha256(('tok%d%s' % (uid, device)).encode()).hexdigest(), uid, expiry, server.now()))
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def call(self, path, method='GET', body=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(f'http://127.0.0.1:{self.httpd.server_port}{path}', method=method,
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def change(self, token, **body):
        return self.call('/api/account/credentials', 'PUT', body, token)

    def login(self, username, password):
        return self.call('/api/login', 'POST', {'username': username, 'password': password, 'deviceId': 'd', 'deviceName': 'n'})[0]

    def test_requires_login_and_current_password(self):
        self.assertEqual(self.change(None, currentPassword='oldpass123', newPassword='newpass123')[0], 401)
        self.assertEqual(self.change('tok1a', currentPassword='wrong', newPassword='newpass123')[0], 403)
        self.assertEqual(self.login('manager1', 'oldpass123'), 200)

    def test_password_change_applies_on_server_and_disconnects_other_devices(self):
        status, body = self.change('tok1a', currentPassword='oldpass123', newPassword='newpass123')
        self.assertEqual((status, body.get('passwordChanged')), (200, True), body)
        self.assertEqual(self.login('manager1', 'oldpass123'), 401)
        self.assertEqual(self.login('manager1', 'newpass123'), 200)
        # الجلسة الحالية تبقى، وبقية أجهزة نفس المستخدم تنفصل، وغيره لا يتأثر.
        self.assertEqual(self.call('/api/organization', token='tok1a')[0], 200)
        self.assertEqual(self.call('/api/organization', token='tok1b')[0], 401)
        self.assertEqual(self.call('/api/organization', token='tok3b')[0], 200)
        self.assertEqual(self.login('manager2', 'oldpass123'), 200)

    def test_short_password_and_no_change_are_refused(self):
        self.assertEqual(self.change('tok1a', currentPassword='oldpass123', newPassword='short')[0], 400)
        self.assertEqual(self.change('tok1a', currentPassword='oldpass123')[0], 400)
        self.assertEqual(self.change('tok1a', currentPassword='oldpass123', username='Manager1')[0], 400)

    def test_username_change_is_unique_across_all_organizations(self):
        self.assertEqual(self.change('tok1a', currentPassword='oldpass123', username='manager2')[0], 409)
        self.assertEqual(self.change('tok1a', currentPassword='oldpass123', username='BOSS@example.com')[0], 409)
        self.assertEqual(self.change('tok1a', currentPassword='oldpass123', username='ab')[0], 400)
        status, body = self.change('tok1a', currentPassword='oldpass123', username='  New.Name ')
        self.assertEqual((status, body.get('username')), (200, 'new.name'), body)
        self.assertEqual(self.login('new.name', 'oldpass123'), 200)
        self.assertEqual(self.login('manager1', 'oldpass123'), 401)
        # تغيير الاسم وحده لا يفصل الأجهزة الأخرى.
        self.assertEqual(self.call('/api/organization', token='tok1b')[0], 200)

    def test_employee_changes_own_password_but_not_username(self):
        self.assertEqual(self.change('tok2a', currentPassword='oldpass123', username='hacker')[0], 403)
        self.assertEqual(self.change('tok2a', currentPassword='oldpass123', newPassword='workerpass9')[0], 200)
        self.assertEqual(self.login('worker1', 'workerpass9'), 200)
        self.assertEqual(self.login('manager1', 'oldpass123'), 200)


if __name__ == '__main__':
    unittest.main()
