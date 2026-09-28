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

import server
from test_advertisements import test_db


class OwnerSupportFlowTest(unittest.TestCase):
    def test_customer_request_is_visible_and_linked_to_technical_task(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), \
             patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'support-test'}):
            server.init_db()
            with server.db() as connection:
                connection.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('Test organization', server.now()))
                connection.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(1,'vip',?)", (server.now(),))
                connection.execute('INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(1,1,?,?,?,?,?,?)', ('Test user', 'test', 'x', 'x', 'admin', server.now()))
                connection.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)', (hashlib.sha256(b'support-user').hexdigest(), 1, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), server.now()))
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def request(path, method='GET', body=None, owner=False):
                headers = {'Content-Type': 'application/json'}
                headers['X-Owner-Key' if owner else 'Authorization'] = 'support-test' if owner else 'Bearer support-user'
                with urlopen(Request(f'http://127.0.0.1:{httpd.server_port}{path}', method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers), timeout=5) as response:
                    return json.load(response)

            try:
                created = request('/api/support-tickets', 'POST', {'category': 'مشكلة تقنية', 'message': 'طلب دعم تجريبي للمؤسسة'})
                self.assertEqual(created['status'], 'in_progress')
                ticket_id = created['id']
                with server.db() as connection:
                    task = connection.execute('SELECT organization_id,support_ticket_id,status FROM technical_tasks WHERE support_ticket_id=?', (ticket_id,)).fetchone()
                    self.assertEqual((task['organization_id'], task['support_ticket_id'], task['status']), (1, ticket_id, 'diagnosing'))
                self.assertEqual(request('/owner/api/v2/summary', owner=True)['support'], 1)
                live = request('/owner/api/v2/live-changes?scope=support&status=in_progress', owner=True)
                self.assertEqual(live['total'], 1)
                self.assertEqual(live['signature'][0]['id'], ticket_id)
                for status in ('', 'in_progress'):
                    result = request('/owner/api/v2/support?status=' + status, owner=True)
                    self.assertEqual(result['total'], 1)
                    self.assertEqual(result['items'][0]['technical_status'], 'diagnosing')
                with server.db() as connection:
                    connection.execute("UPDATE support_tickets SET status='under_review' WHERE id=?", (ticket_id,))
                review = request('/owner/api/v2/support?status=under_review', owner=True)
                self.assertEqual(review['items'][0]['id'], ticket_id)
                self.assertEqual(request('/owner/api/v2/live-changes?scope=support&status=in_progress', owner=True)['total'], 0)
                self.assertEqual(request('/owner/api/v2/summary', owner=True)['support'], 1)
                with server.db() as connection:
                    connection.execute("INSERT INTO advertisements(organization_id,title,message,created_at) VALUES(1,'Ad request','Banner',?)", (server.now(),))
                ads = request('/owner/api/v2/live-changes?scope=ads&status=pending', owner=True)
                self.assertEqual(ads['total'], 1)
                self.assertEqual(ads['signature'][0]['status'], 'pending')
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
