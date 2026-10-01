import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import ai_core
import server
from test_advertisements import test_db


class TechnicalAgentTest(unittest.TestCase):
    def run_server(self, transport):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for p in (patch.object(server, 'DB_PATH', Path(directory.name) / 'test.db'), patch.object(server, 'DATABASE_URL', ''),
                  patch.object(server, 'db', test_db), patch.object(ai_core.ResponsesClient, '_http', lambda self, payload: transport(payload)),
                  patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'owner-test', 'OPENAI_API_KEY': 'configured'}, clear=False)):
            p.start(); self.addCleanup(p.stop)
        server.init_db()
        with server.db() as c:
            c.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('مؤسسة النور', server.now()))
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(1,'vip',?)", (server.now(),))
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)

        def ask(body):
            with urlopen(Request(f'http://127.0.0.1:{httpd.server_port}/owner/api/v2/technical-ai/ask', method='POST', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Owner-Key': 'owner-test'}), timeout=10) as r:
                return json.load(r)
        return ask

    def test_model_uses_existing_tools_and_answers_naturally(self):
        payloads = []

        def transport(payload):
            payloads.append(payload)
            if len(payloads) == 1:
                return {'output': [{'type': 'function_call', 'call_id': 'a', 'name': 'find_organization', 'arguments': json.dumps({'name': 'النور'})}]}
            if len(payloads) == 2:
                return {'output': [{'type': 'function_call', 'call_id': 'b', 'name': 'scan_organization', 'arguments': json.dumps({'organization_id': 1})}]}
            return {'output_text': 'فحصت مؤسسة النور: الباقة VIP شغالة.'}

        ask = self.run_server(transport)
        result = ask({'question': 'وش وضع مؤسسة النور؟', 'history': [{'role': 'user', 'text': 'مرحبا'}]})
        self.assertEqual(result['mode'], 'ai')
        self.assertIn('مؤسسة النور', result['answer'])
        self.assertTrue(result['checks'])
        self.assertIn('أنت اسألني', payloads[0]['instructions'])
        self.assertNotIn('delete', json.dumps(payloads[0]['tools']))
        found = json.loads(next(x['output'] for x in payloads[-1]['input'] if x.get('call_id') == 'a' and x.get('type') == 'function_call_output'))
        self.assertEqual(found['items'][0]['id'], 1)

    def test_model_failure_falls_back_to_rule_based_diagnosis(self):
        def transport(payload):
            raise ai_core.AIServiceError(502, 'down')

        ask = self.run_server(transport)
        result = ask({'question': 'وش وضع المنصة؟'})
        self.assertNotIn('answer', result)
        self.assertEqual(result['scope'], 'global')


if __name__ == '__main__':
    unittest.main()
