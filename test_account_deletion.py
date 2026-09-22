import hashlib
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import server

_original_db = server.db

@contextmanager
def test_db():
    connection = _original_db()
    try:
        yield connection
    finally:
        connection.close()


class AccountDeletionTest(unittest.TestCase):
    def test_public_request_requires_no_login_and_deletes_only_after_verification(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), \
             patch.object(server, 'OWNER_KEY_PATH', Path(directory) / 'owner.key'), \
             patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'deletion-test-key'}):
            server.init_db()
            with server.db() as connection:
                connection.execute("INSERT INTO organizations(id,name,phone,created_at) VALUES(?,?,?,?)", (1, 'مؤسسة اختبار', '966500000000', server.now()))
                connection.execute("INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (1, 1, 'مستخدم اختبار', 'test-user', 'user@example.com', '966500000000', 'hash', 'salt', 'admin', server.now()))
                connection.commit()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(httpd.server_port)
            try:
                with urlopen(base + '/delete-account', timeout=5) as response:
                    page = response.read().decode('utf-8')
                    self.assertEqual(response.status, 200)
                    self.assertIn('طلب حذف حساب خدووم', page)
                body = json.dumps({'identifier': 'user@example.com'}).encode()
                request = Request(base + '/delete-account', data=body, headers={'Content-Type': 'application/json'}, method='POST')
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                    self.assertEqual(json.load(response)['status'], 'pending_verification')
                with server.db() as connection:
                    pending = connection.execute("SELECT request_id,status,organization_id FROM account_deletion_requests").fetchone()
                    self.assertEqual(pending['status'], 'pending_verification')
                    self.assertEqual(pending['organization_id'], 1)
                    self.assertIsNotNone(connection.execute('SELECT id FROM organizations WHERE id=1').fetchone())
                owner_headers = {'X-Owner-Key': 'deletion-test-key', 'Content-Type': 'application/json'}
                verify = Request(base + '/owner/api/account-deletion-requests/' + pending['request_id'], data=b'{"action":"verify"}', headers=owner_headers, method='PUT')
                with urlopen(verify, timeout=5) as response:
                    self.assertEqual(json.load(response)['status'], 'verified')
                complete = Request(base + '/owner/api/account-deletion-requests/' + pending['request_id'], data=b'{"action":"complete"}', headers=owner_headers, method='PUT')
                with urlopen(complete, timeout=5) as response:
                    self.assertEqual(json.load(response)['status'], 'completed')
                with server.db() as connection:
                    self.assertIsNone(connection.execute('SELECT id FROM organizations WHERE id=1').fetchone())
                    self.assertEqual(connection.execute('SELECT status FROM account_deletion_requests WHERE request_id=?', (pending['request_id'],)).fetchone()['status'], 'completed')
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()

