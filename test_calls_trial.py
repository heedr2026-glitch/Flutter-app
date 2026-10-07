import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import ai_core
import calls_trial
import server
from test_advertisements import test_db


def refuse(code):
    return HTTPError(calls_trial.CLIENT_SECRETS_URL, code, 'refused', {}, io.BytesIO(b'{}'))


class FakeClient:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.payloads = text, error, []
    def transport(self, payload):
        self.payloads.append(payload)
        if self.error: raise self.error
        return {'output_text': self.text}
    output_text = staticmethod(ai_core.ResponsesClient.output_text)


TURNS = [{'role': 'agent', 'text': 'حياك الله، معك استقبال خدوم'}, {'role': 'caller', 'text': 'معك  سالم،\nأبي موعد بكرة العصر'}]


class SessionTest(unittest.TestCase):
    def test_secret_voice_and_instructions(self):
        sent = []
        result = calls_trial.create_session('female', lambda payload: sent.append(payload) or {'value': 'ek_test'})
        self.assertEqual(result['clientSecret'], 'ek_test')
        self.assertEqual(result['voice'], 'female')
        self.assertTrue(result['callerTranscription'])
        session = sent[0]['session']
        self.assertEqual(session['type'], 'realtime')
        self.assertEqual(session['audio']['output']['voice'], 'marin')
        self.assertIn('transcription', session['audio']['input'])
        self.assertIn('سعودية', session['instructions'])

    def test_unknown_voice_is_male_and_nested_secret(self):
        result = calls_trial.create_session('robot', lambda payload: {'client_secret': {'value': 'ek_nested'}})
        self.assertEqual((result['voice'], result['clientSecret']), ('male', 'ek_nested'))

    def test_transcription_rejected_falls_back_without_it(self):
        sent = []
        def transport(payload):
            sent.append(payload)
            if 'input' in payload['session']['audio']: raise refuse(400)
            return {'value': 'ek_plain'}
        result = calls_trial.create_session('male', transport)
        self.assertEqual(len(sent), 2)
        self.assertFalse(result['callerTranscription'])
        self.assertEqual(result['clientSecret'], 'ek_plain')

    def test_refusals_and_empty_secret_become_service_errors(self):
        for transport in (lambda payload: (_ for _ in ()).throw(refuse(401)), lambda payload: (_ for _ in ()).throw(refuse(500)), lambda payload: {}):
            with self.assertRaises(ai_core.AIServiceError) as error: calls_trial.create_session('male', transport)
            self.assertEqual(error.exception.status, 502)

    def test_missing_server_key(self):
        with patch.dict(os.environ, {'KHDOOM_AI_API_KEY': '', 'OPENAI_API_KEY': ''}):
            with self.assertRaises(ai_core.AIServiceError) as error: calls_trial.create_session('male')
        self.assertEqual(error.exception.status, 503)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:'); self.c.row_factory = sqlite3.Row
        calls_trial.migrate(self.c)
    def tearDown(self): self.c.close()

    def test_clean_turns_drops_junk_and_caps(self):
        cleaned = calls_trial.clean_turns(TURNS + [{'role': 'x', 'text': ''}, 'junk', {'role': 'system', 'text': 'x' * 5000}])
        self.assertEqual(cleaned[1], {'role': 'caller', 'text': 'معك سالم، أبي موعد بكرة العصر'})
        self.assertEqual((cleaned[2]['role'], len(cleaned[2]['text'])), ('caller', calls_trial.MAX_TURN_CHARS))
        self.assertEqual(calls_trial.clean_turns(None), [])
        self.assertEqual(len(calls_trial.clean_turns([{'role': 'caller', 'text': 'a'}] * 500)), calls_trial.MAX_TURNS)

    def test_ai_report_saved_and_listed(self):
        turns = calls_trial.clean_turns(TURNS)
        client = FakeClient('هذا التقرير:\n' + json.dumps({'callerName': 'سالم', 'request': 'موعد', 'appointment': 'بكرة العصر', 'followUp': True, 'summary': 'طلب موعدًا'}, ensure_ascii=False))
        report = calls_trial.build_report(turns, client)
        self.assertIn('المتصل: معك سالم', client.payloads[0]['input'])
        self.assertTrue(report['aiReport'])
        saved = calls_trial.save_report(self.c, report, turns, '95', '2026-10-07T09:00:00+00:00')
        self.assertEqual((saved['callerName'], saved['appointment'], saved['durationSeconds']), ('سالم', 'بكرة العصر', 95))
        listed = calls_trial.list_reports(self.c)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], saved['id'])
        self.assertTrue(listed[0]['followUp'])
        self.assertTrue(listed[0]['transcript'].startswith('الموظف: حياك الله'))

    def test_ai_failure_keeps_the_transcript(self):
        turns = calls_trial.clean_turns(TURNS)
        for client in (FakeClient(error=ai_core.AIServiceError(502, 'x')), FakeClient('ليس JSON'), FakeClient('[1]')):
            report = calls_trial.build_report(turns, client)
            self.assertFalse(report['aiReport'])
            self.assertTrue(report['followUp'])
            saved = calls_trial.save_report(self.c, report, turns, None, '2026-10-07T09:00:00+00:00')
            self.assertIn('أبي موعد', saved['transcript'])
            self.assertEqual(saved['durationSeconds'], 0)

    def test_trial_never_touches_subscriber_tables(self):
        calls_trial.save_report(self.c, calls_trial.build_report([], None), calls_trial.clean_turns(TURNS), 5, '2026-10-07T09:00:00+00:00')
        tables = {row['name'] for row in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('call_logs', tables)
        self.assertNotIn('call_connections', tables)


class HttpTest(unittest.TestCase):
    def test_owner_only_routes_and_report_flow(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB_PATH', Path(temp) / 'test.db'), patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'db', test_db), patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'test-only'}), \
                patch.object(calls_trial, '_http', lambda payload: {'value': 'ek_http'}), \
                patch.object(ai_core.ResponsesClient, '_http', lambda self, payload: {'output_text': json.dumps({'callerName': 'سالم', 'request': 'موعد', 'appointment': '', 'followUp': False, 'summary': 'ملخص'}, ensure_ascii=False)}):
            server.init_db()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            base = 'http://127.0.0.1:' + str(httpd.server_port)
            def req(path, method='GET', data=None, owner=True):
                headers = {'Content-Type': 'application/json'}
                if owner: headers['X-Owner-Key'] = 'test-only'
                with urlopen(Request(base + path, method=method, headers=headers, data=None if data is None else json.dumps(data).encode()), timeout=10) as r:
                    return json.load(r)
            try:
                with urlopen(base + '/owner/calls-trial', timeout=10) as page:
                    html = page.read().decode('utf-8')
                    self.assertIn('تجربة المكالمات', html)
                    self.assertNotIn('microphone=()', page.headers.get('Permissions-Policy') or '')
                for path, method, data in (('/owner/api/calls-trial/session', 'POST', {}), ('/owner/api/calls-trial/report', 'POST', {'turns': TURNS}), ('/owner/api/calls-trial/reports', 'GET', None)):
                    with self.assertRaises(HTTPError) as error: req(path, method, data, owner=False)
                    self.assertEqual(error.exception.code, 401); error.exception.close()
                self.assertEqual(req('/owner/api/calls-trial/session', 'POST', {'voice': 'female'})['clientSecret'], 'ek_http')
                with self.assertRaises(HTTPError) as error: req('/owner/api/calls-trial/report', 'POST', {'turns': []})
                self.assertEqual(error.exception.code, 400); error.exception.close()
                saved = req('/owner/api/calls-trial/report', 'POST', {'turns': TURNS, 'durationSeconds': 42})
                self.assertEqual((saved['callerName'], saved['durationSeconds'], saved['followUp']), ('سالم', 42, False))
                reports = req('/owner/api/calls-trial/reports')['reports']
                self.assertEqual([r['id'] for r in reports], [saved['id']])
                with server.db() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) n FROM call_logs').fetchone()['n'], 0)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
