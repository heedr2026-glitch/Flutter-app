import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import server
from test_advertisements import test_db


class TechnicalEmployeeTest(unittest.TestCase):
    def test_internal_monitor_and_assistant_diagnosis_are_reported_accurately(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), \
             patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'owner-test', 'OPENAI_API_KEY': 'configured'}, clear=False):
            server.init_db()
            checked_at = datetime.now(timezone.utc).isoformat()
            with server.db() as connection:
                connection.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('مؤسسة الاختبار', server.now()))
                connection.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(1,'vip',?)", (server.now(),))
                connection.execute("INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(1,1,'مدير','manager','x','x','admin',?)", (server.now(),))
                connection.execute("""INSERT INTO technical_agent_state(
                    id,status,last_check,last_task,last_error,last_success,updated_at)
                    VALUES(1,'online',?,'مراقبة تلقائية','','المراقبة الداخلية تعمل',?)""", (checked_at, checked_at))

            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def request(path, method='GET', body=None):
                with urlopen(Request(
                    f'http://127.0.0.1:{httpd.server_port}{path}',
                    method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type': 'application/json', 'X-Owner-Key': 'owner-test'},
                ), timeout=5) as response:
                    return json.load(response)

            try:
                state = request('/owner/api/v2/technical-ai')
                self.assertTrue(state['monitoring'])
                self.assertEqual(state['state']['status'], 'online')
                self.assertTrue(state['state']['internalMonitoring'])
                self.assertFalse(state['state']['externalAgentConfigured'])

                diagnosis = request('/owner/api/v2/technical-ai/ask', 'POST', {
                    'question': 'تأكد من اسألني في مؤسسة الاختبار لأنه لا يفهم متابعة الشات',
                })
                self.assertEqual(diagnosis['service'], 'ai')
                self.assertEqual(diagnosis['scope'], 'organization')
                ai_check = next(item for item in diagnosis['checks'] if item['key'] == 'ai')
                self.assertEqual(ai_check['status'], 'ok')
                self.assertIn('مهيأة', ai_check['details'])

                login = request('/owner/api/v2/technical-ai/ask', 'POST', {
                    'question': 'الموظف في مؤسسة الاختبار لا يستطيع تسجيل الدخول',
                })
                self.assertEqual(login['service'], 'login')
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
