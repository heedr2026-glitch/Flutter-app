import base64
import hashlib
import hmac
import io
import json
import os
import socket
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import ai_core
import call_gateway
import calls_trial
import server
from test_advertisements import test_db

ENV = {'TWILIO_AUTH_TOKEN': 'twilio-test', 'OPENAI_WEBHOOK_SECRET': 'whsec_' + base64.b64encode(b'openai-test-secret').decode(),
       'KHDOOM_OPENAI_PROJECT_ID': 'proj_test123', 'OPENAI_API_KEY': 'sk-test'}
REF = {'name': 'X-Khdoom-Ref', 'value': ''}


class ApiError(Exception):
    def __init__(self, status, message): super().__init__(message); self.status = status


def openai_headers(raw, identifier='evt_1', stamp=None, secret=ENV['OPENAI_WEBHOOK_SECRET']):
    stamp = str(int(stamp if stamp is not None else time.time()))
    key = base64.b64decode(secret[6:])
    signature = base64.b64encode(hmac.new(key, f'{identifier}.{stamp}.'.encode() + raw, hashlib.sha256).digest()).decode()
    return {'webhook-id': identifier, 'webhook-timestamp': stamp, 'webhook-signature': 'v1,wrong v1,' + signature}


def memory():
    c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
    c.executescript("""CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, activity TEXT);
        CREATE TABLE call_connections(organization_id INTEGER PRIMARY KEY, phone_number TEXT, enabled INTEGER);
        INSERT INTO organizations VALUES(7,'مراتك للزجاج','زجاج'),(8,'ثانية','مقاولات');
        INSERT INTO call_connections VALUES(8,'966112345678',1),(7,'966119999999',0);""")
    call_gateway.migrate(c)
    return c


class SignatureTest(unittest.TestCase):
    def test_twilio_signature_matches_published_example(self):
        # المثال المنشور في توثيق Twilio لخوارزمية التوقيع.
        params = {'CallSid': 'CA1234567890ABCDE', 'Caller': '+12349013030', 'Digits': '1234', 'From': '+12349013030', 'To': '+18005551212'}
        url = 'https://mycompany.com/myapp.php?foo=1&bar=2'
        self.assertEqual(call_gateway.twilio_signature('12345', url, params), '0/KCTR6DLpKmkAf8muzZqo1nDgQ=')
        self.assertTrue(call_gateway.valid_twilio('12345', url, params, '0/KCTR6DLpKmkAf8muzZqo1nDgQ='))
        self.assertFalse(call_gateway.valid_twilio('12345', url, dict(params, To='+19999999999'), '0/KCTR6DLpKmkAf8muzZqo1nDgQ='))
        self.assertFalse(call_gateway.valid_twilio('', url, params, '0/KCTR6DLpKmkAf8muzZqo1nDgQ='))
        self.assertFalse(call_gateway.valid_twilio('12345', url, params, None))

    def test_openai_webhook_signature_and_replay_window(self):
        raw = b'{"type":"realtime.call.incoming"}'
        secret = ENV['OPENAI_WEBHOOK_SECRET']
        self.assertTrue(call_gateway.verify_openai(secret, openai_headers(raw), raw))
        self.assertFalse(call_gateway.verify_openai(secret, openai_headers(raw), raw + b' '))
        self.assertFalse(call_gateway.verify_openai(secret, openai_headers(raw, stamp=time.time() - 3600), raw))
        self.assertFalse(call_gateway.verify_openai('whsec_' + base64.b64encode(b'other').decode(), openai_headers(raw), raw))
        self.assertFalse(call_gateway.verify_openai('', openai_headers(raw), raw))
        self.assertFalse(call_gateway.verify_openai(secret, {}, raw))
        self.assertFalse(call_gateway.verify_openai(secret, dict(openai_headers(raw), **{'webhook-timestamp': 'x'}), raw))


class RoutingTest(unittest.TestCase):
    def setUp(self): self.c = memory()
    def tearDown(self): self.c.close()

    def test_numbers_assigned_by_admin_decide_the_organization(self):
        with self.assertRaises(ApiError): call_gateway.assign_number(self.c, '123', 7, ApiError, 'now')
        with self.assertRaises(ApiError): call_gateway.assign_number(self.c, '+1 415 555 0123', 99, ApiError, 'now')
        with self.assertRaises(ApiError): call_gateway.assign_number(self.c, '+1 415 555 0123', '', ApiError, 'now')
        saved = call_gateway.assign_number(self.c, '+1 (415) 555-0123', 7, ApiError, 'now')
        self.assertEqual((saved['phone'], saved['organizationName']), ('14155550123', 'مراتك للزجاج'))
        self.assertEqual(call_gateway.organization_for(self.c, '+14155550123'), 7)
        # إعادة التعيين تنقل الرقم ولا تكرره.
        call_gateway.assign_number(self.c, '0014155550123', 8, ApiError, 'later')
        self.assertEqual(call_gateway.organization_for(self.c, '+14155550123'), 8)
        self.assertEqual(len(call_gateway.list_numbers(self.c)), 1)
        # رقم المؤسسة المربوط والمفعّل يصلح أيضًا، وغير المفعّل لا.
        self.assertEqual(call_gateway.organization_for(self.c, '+966112345678'), 8)
        self.assertIsNone(call_gateway.organization_for(self.c, '+966119999999'))
        self.assertIsNone(call_gateway.organization_for(self.c, ''))
        call_gateway.remove_number(self.c, '+14155550123')
        self.assertIsNone(call_gateway.organization_for(self.c, '+14155550123'))

    def test_twilio_call_is_dialed_to_the_agent_or_rejected(self):
        call_gateway.assign_number(self.c, '14155550123', 7, ApiError, 'now')
        params = {'To': '+14155550123', 'From': '+966542027855', 'CallSid': 'CA1'}
        with patch.dict(os.environ, ENV):
            twiml = call_gateway.incoming_twilio(self.c, params, server.now())
            row = self.c.execute("SELECT * FROM call_gateway_calls").fetchone()
            self.assertIn(f'<Sip>sip:proj_test123@sip.api.openai.com;transport=tls?x-khdoom-ref={row["ref"]}</Sip>', twiml)
            self.assertIn('timeLimit="600"', twiml)
            self.assertEqual((row['caller'], row['called'], row['organization_id'], row['status']), ('966542027855', '14155550123', 7, 'pending'))
            # رقم غير معروف، أو رصيد منتهٍ: رفض بلا تحويل.
            self.assertEqual(call_gateway.incoming_twilio(self.c, dict(params, To='+19990000000'), server.now()), call_gateway.REJECT_TWIML)
            self.assertEqual(call_gateway.incoming_twilio(self.c, params, server.now(), lambda org: True), call_gateway.REJECT_TWIML)
            self.assertEqual(self.c.execute("SELECT COUNT(*) FROM call_gateway_calls WHERE status='rejected'").fetchone()[0], 1)
        with patch.dict(os.environ, {'KHDOOM_OPENAI_PROJECT_ID': '', 'OPENAI_PROJECT_ID': ''}):
            self.assertEqual(call_gateway.incoming_twilio(self.c, params, server.now()), call_gateway.REJECT_TWIML)

    def test_openai_call_must_follow_a_twilio_request(self):
        call_gateway.assign_number(self.c, '14155550123', 7, ApiError, 'now')
        params = {'To': '+14155550123', 'From': '+966542027855', 'CallSid': 'CA1'}
        with patch.dict(os.environ, ENV):
            call_gateway.incoming_twilio(self.c, params, server.now())
            ref = self.c.execute("SELECT ref FROM call_gateway_calls").fetchone()['ref']
            # بلا طلب سابق: مرفوض.
            self.assertIsNone(call_gateway.claim_call(self.c, 'rtc_x', [{'name': 'From', 'value': '<sip:+966500000000@twilio>'}], server.now()))
            self.assertIsNone(call_gateway.claim_call(self.c, 'rtc_x', [{'name': 'x-khdoom-ref', 'value': 'f' * 32}], server.now()))
            self.assertIsNone(call_gateway.claim_call(self.c, '', [{'name': 'X-Khdoom-Ref', 'value': ref}], server.now()))
            call = call_gateway.claim_call(self.c, 'rtc_1', [{'name': 'X-Khdoom-Ref', 'value': ref}], server.now())
            self.assertEqual((call['organizationId'], call['caller']), (7, '966542027855'))
            # نفس المرجع لا يُستعمل مرتين، ونفس المكالمة لا تُقبل مرتين.
            self.assertIsNone(call_gateway.claim_call(self.c, 'rtc_2', [{'name': 'X-Khdoom-Ref', 'value': ref}], server.now()))
            self.assertIsNone(call_gateway.claim_call(self.c, 'rtc_1', [{'name': 'X-Khdoom-Ref', 'value': ref}], server.now()))
            # احتياط برقم المتصل إن لم تصل الترويسة الخاصة.
            call_gateway.incoming_twilio(self.c, params, server.now())
            by_caller = call_gateway.claim_call(self.c, 'rtc_3', [{'name': 'From', 'value': '"x" <sip:+966542027855@pstn.twilio.com>;tag=1'}], server.now())
            self.assertEqual(by_caller['organizationId'], 7)
            # طلب قديم تجاوز المهلة لا يُقبل.
            call_gateway.incoming_twilio(self.c, params, server.now())
            late = (datetime.fromisoformat(server.now()) + timedelta(seconds=call_gateway.PENDING_SECONDS + 5)).isoformat()
            stale_ref = self.c.execute("SELECT ref FROM call_gateway_calls ORDER BY id DESC LIMIT 1").fetchone()['ref']
            self.assertIsNone(call_gateway.claim_call(self.c, 'rtc_4', [{'name': 'X-Khdoom-Ref', 'value': stale_ref}], late))


class AcceptTest(unittest.TestCase):
    def test_accept_uses_phone_settings_and_falls_back(self):
        sent = []
        self.assertTrue(call_gateway.accept_call('rtc_1', 'تعليمات', lambda url, body: sent.append((url, body))))
        url, body = sent[0]
        self.assertEqual(url, 'https://api.openai.com/v1/realtime/calls/rtc_1/accept')
        self.assertEqual((body['type'], body['instructions'], body['audio']['output']['voice']), ('realtime', 'تعليمات', 'cedar'))
        self.assertEqual(body['audio']['input']['noise_reduction'], {'type': 'near_field'})
        self.assertEqual(body['audio']['input']['transcription']['language'], 'ar')
        self.assertNotIn('session', body)
        # تجربة المتصفح تبقى على إعدادها.
        self.assertEqual(calls_trial.session_payload('male')['session']['audio']['input']['noise_reduction'], {'type': 'far_field'})
        attempts = []
        def picky(url, body):
            attempts.append(body)
            if 'noise_reduction' in body['audio'].get('input', {}):
                raise HTTPError(url, 400, 'bad', {}, io.BytesIO(b'{}'))
        self.assertTrue(call_gateway.accept_call('rtc_1', 'x', picky))
        self.assertEqual(len(attempts), 2)
        def denied(url, body): raise HTTPError(url, 401, 'no', {}, io.BytesIO(b'{}'))
        self.assertFalse(call_gateway.accept_call('rtc_1', 'x', denied))
        def always_bad(url, body): raise HTTPError(url, 400, 'bad', {}, io.BytesIO(b'{}'))
        self.assertFalse(call_gateway.accept_call('rtc_1', 'x', always_bad))
        ended = []
        call_gateway.end_call('rtc_1', 'reject', lambda url, body: ended.append((url, body)))
        call_gateway.end_call('rtc_1', post=lambda url, body: ended.append((url, body)))
        self.assertEqual(ended, [('https://api.openai.com/v1/realtime/calls/rtc_1/reject', {'status_code': 603}),
                                 ('https://api.openai.com/v1/realtime/calls/rtc_1/hangup', None)])


EVENTS = [
    {'type': 'session.created'},
    {'type': 'conversation.item.added', 'item': {'id': 'item_1', 'role': 'user'}},
    {'type': 'conversation.item.added', 'item': {'id': 'item_2', 'role': 'assistant'}},
    {'type': 'response.output_audio_transcript.done', 'item_id': 'item_2', 'transcript': 'وعليكم السلام، معك مراتك للزجاج'},
    # تفريغ كلام المتصل يصل متأخرًا عن رد الموظف، والترتيب الصحيح بترتيب الكلام.
    {'type': 'conversation.item.input_audio_transcription.completed', 'item_id': 'item_1', 'transcript': 'السلام عليكم'},
    {'type': 'conversation.item.input_audio_transcription.completed', 'item_id': 'item_3', 'transcript': 'معك حيدر أبي موعد'},
    {'type': 'response.audio_transcript.done', 'item_id': 'item_4', 'transcript': ''},
]


class FakeLink:
    def __init__(self, messages): self.messages, self.closed = list(messages), False
    def recv(self):
        if not self.messages: return None
        message = self.messages.pop(0)
        if isinstance(message, Exception): raise message
        return message
    def close(self): self.closed = True


class MonitorTest(unittest.TestCase):
    def test_turns_follow_speaking_order(self):
        link = FakeLink([json.dumps(event, ensure_ascii=False) for event in EVENTS[:3]] + [TimeoutError(), 'not json', '[]'] +
                        [json.dumps(event, ensure_ascii=False) for event in EVENTS[3:]])
        outcome = call_gateway.monitor('rtc_1', lambda call_id: link)
        self.assertEqual(outcome['turns'], [{'role': 'caller', 'text': 'السلام عليكم'}, {'role': 'agent', 'text': 'وعليكم السلام، معك مراتك للزجاج'},
                                            {'role': 'caller', 'text': 'معك حيدر أبي موعد'}])
        self.assertEqual(outcome['error'], '')
        self.assertTrue(link.closed)

    def test_long_call_is_hung_up_and_connection_failure_is_reported(self):
        ticks = iter([0, 1, 700, 700, 700])
        hung = []
        outcome = call_gateway.monitor('rtc_1', lambda call_id: FakeLink([json.dumps(EVENTS[1]), json.dumps(EVENTS[4])] + ['{}'] * 5),
                                       max_seconds=600, clock=lambda: next(ticks), hangup=hung.append)
        self.assertEqual(hung, ['rtc_1'])
        self.assertEqual(outcome['durationSeconds'], 700)
        def broken(call_id): raise OSError('no route')
        failed = call_gateway.monitor('rtc_1', broken)
        self.assertEqual(failed['turns'], [])
        self.assertIn('تعذرت متابعة نص المكالمة', failed['error'])


def ws_frame(opcode, payload, final=True):
    head = bytes([(0x80 if final else 0) | opcode])
    size = len(payload)
    head += bytes([size]) if size < 126 else bytes([126]) + struct.pack('!H', size) if size < 65536 else bytes([127]) + struct.pack('!Q', size)
    return head + payload


class SocketTest(unittest.TestCase):
    def test_client_handshake_frames_ping_and_close(self):
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1)
        seen = {}
        big = 'م' * 40000
        def serve():
            connection, _ = listener.accept()
            request = b''
            while b'\r\n\r\n' not in request: request += connection.recv(4096)
            seen['request'] = request.decode()
            key = [line.split(': ', 1)[1] for line in seen['request'].split('\r\n') if line.lower().startswith('sec-websocket-key')][0]
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            payload = (f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n').encode()
            # أول إطار ملتصق برد المصافحة، ثم رسالة مجزأة، ثم ping، ثم رسالة كبيرة، ثم إغلاق.
            connection.sendall(payload + ws_frame(1, b'{"a":1}'))
            connection.sendall(ws_frame(1, 'مرح'.encode(), final=False) + ws_frame(9, b'hi') + ws_frame(0, 'با'.encode()))
            data = ws_frame(1, big.encode())
            connection.sendall(data[:70000]); time.sleep(0.05); connection.sendall(data[70000:])
            medium = ws_frame(1, b'x' * 300)
            connection.sendall(medium[:1]); time.sleep(0.05); connection.sendall(medium[1:] + ws_frame(8, struct.pack('!H', 1000)))
            reply = b''
            connection.settimeout(2)
            try:
                while len(reply) < 16:
                    chunk = connection.recv(4096)
                    if not chunk: break
                    reply += chunk
            except OSError: pass
            seen['reply'] = reply
            connection.close()
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        link = call_gateway.RealtimeSocket(f'ws://127.0.0.1:{listener.getsockname()[1]}/v1/realtime?call_id=rtc_1', {'Authorization': 'Bearer sk-test'}, timeout=3)
        self.assertEqual(link.recv(), '{"a":1}')
        self.assertEqual(link.recv(), 'مرحبا')
        self.assertEqual(link.recv(), big)
        self.assertEqual(link.recv(), 'x' * 300)
        self.assertIsNone(link.recv())
        link.close(); thread.join(3); listener.close()
        self.assertIn('GET /v1/realtime?call_id=rtc_1 HTTP/1.1', seen['request'])
        self.assertIn('Authorization: Bearer sk-test', seen['request'])
        # رد العميل: pong مقنّع بنفس الحمولة ثم إغلاق مقنّع.
        reply = seen['reply']
        self.assertEqual((reply[0], reply[1]), (0x8A, 0x80 | 2))
        self.assertEqual(bytes(b ^ reply[2 + i % 4] for i, b in enumerate(reply[6:8])), b'hi')
        self.assertEqual(reply[8], 0x88)

    def test_bad_handshake_is_refused(self):
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1)
        def serve():
            connection, _ = listener.accept(); connection.recv(4096)
            connection.sendall(b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n'); connection.close()
        threading.Thread(target=serve, daemon=True).start()
        with self.assertRaises(OSError):
            call_gateway.RealtimeSocket(f'ws://127.0.0.1:{listener.getsockname()[1]}/x', {}, timeout=3)
        listener.close()


class HttpTest(unittest.TestCase):
    def test_phone_call_from_twilio_to_report_and_appointment(self):
        tomorrow = (datetime.now(calls_trial.RIYADH) + timedelta(days=1)).strftime('%Y-%m-%d')
        report = {'callerName': 'حيدر', 'request': 'أخذ مقاسات', 'appointment': 'بكرة 4 العصر', 'appointmentAt': tomorrow + 'T16:00', 'service': 'أخذ المقاسات',
                  'callerPhone': '', 'followUp': True, 'summary': 'ملخص المكالمة'}
        posts = []
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB_PATH', Path(temp) / 'test.db'), patch.object(server, 'DATABASE_URL', ''), \
                patch.object(server, 'db', test_db), patch.dict(os.environ, dict(ENV, KHDOOM_OWNER_KEY='test-only')), \
                patch.object(call_gateway, '_post', lambda url, body: posts.append((url, body))), \
                patch.object(call_gateway, '_connect', lambda call_id: FakeLink([json.dumps(event, ensure_ascii=False) for event in EVENTS])), \
                patch.object(ai_core.ResponsesClient, '_http', lambda self, payload: {'output_text': json.dumps(report, ensure_ascii=False)}):
            server.init_db()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            host = '127.0.0.1:' + str(httpd.server_port)
            base = 'http://' + host
            def owner(path, method='GET', data=None, key='test-only'):
                headers = {'Content-Type': 'application/json'}
                if key: headers['X-Owner-Key'] = key
                with urlopen(Request(base + path, method=method, headers=headers, data=None if data is None else json.dumps(data).encode()), timeout=10) as r:
                    return json.load(r)
            def twilio(params, token='twilio-test'):
                signature = call_gateway.twilio_signature(token, 'https://' + host + '/webhooks/twilio/voice', params)
                request = Request(base + '/webhooks/twilio/voice', method='POST', data=urlencode(params).encode(),
                                  headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Twilio-Signature': signature})
                with urlopen(request, timeout=10) as r:
                    return r.headers.get('Content-Type'), r.read().decode()
            def openai(event, **signing):
                raw = json.dumps(event).encode()
                with urlopen(Request(base + '/webhooks/openai/realtime', method='POST', data=raw, headers=dict(openai_headers(raw, **signing), **{'Content-Type': 'application/json'})), timeout=10) as r:
                    return json.load(r)
            try:
                with server.db() as connection:
                    connection.execute("INSERT INTO organizations(id,name,activity,phone,created_at) VALUES(?,?,?,?,?)", (7, 'مراتك للزجاج', 'زجاج ومرايا', '966500000000', server.now()))
                    connection.commit()
                # الإدارة فقط تربط رقم المزود بالمؤسسة.
                with self.assertRaises(HTTPError) as denied: owner('/owner/api/calls-gateway', key='')
                self.assertEqual(denied.exception.code, 401)
                with self.assertRaises(HTTPError) as denied: owner('/owner/api/calls-gateway/numbers', 'POST', {'phone': '14155550123', 'organizationId': 7}, key='')
                self.assertEqual(denied.exception.code, 401)
                self.assertEqual(owner('/owner/api/calls-gateway/numbers', 'POST', {'phone': '+1 415 555 0123', 'organizationId': 7})['organizationName'], 'مراتك للزجاج')
                state = owner('/owner/api/calls-gateway')
                self.assertEqual((state['twilioToken'], state['openaiSecret'], state['projectId'], state['aiKey']), (True, True, True, True))
                self.assertTrue(state['twilioWebhookUrl'].endswith('/webhooks/twilio/voice'))
                self.assertNotIn('twilio-test', json.dumps(state)); self.assertNotIn('proj_test123', json.dumps(state))
                self.assertEqual(state['numbers'][0]['phone'], '14155550123')

                params = {'To': '+14155550123', 'From': '+966542027855', 'CallSid': 'CA1', 'CallStatus': 'ringing'}
                with self.assertRaises(HTTPError) as forged: twilio(params, token='wrong')
                self.assertEqual(forged.exception.code, 401)
                problems = owner('/owner/api/calls-gateway')['problems']
                self.assertIn('توقيع Twilio لم يطابق', problems[0]['detail']); self.assertNotIn('twilio-test', json.dumps(problems))
                # الخادم خلف وسيط: التوقيع بعنوان الخدمة العام يُقبل ولو اختلفت ترويسة Host.
                public = call_gateway.twilio_signature('twilio-test', 'https://khdoom-api.onrender.com/webhooks/twilio/voice', dict(params, To='+19990000000'))
                with urlopen(Request(base + '/webhooks/twilio/voice', method='POST', data=urlencode(dict(params, To='+19990000000')).encode(),
                                     headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Twilio-Signature': public}), timeout=10) as r:
                    self.assertEqual(r.read().decode(), call_gateway.REJECT_TWIML)
                self.assertIn('رقم غير مربوط', owner('/owner/api/calls-gateway')['problems'][0]['detail'])
                # وضع التجربة المؤقت: الإدارة فقط تشغله، ويقبل غير الموقّع ثم يُطفأ.
                unsigned = Request(base + '/webhooks/twilio/voice', method='POST', data=urlencode(dict(params, To='+19990000000')).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'})
                with self.assertRaises(HTTPError): urlopen(unsigned, timeout=10)
                with self.assertRaises(HTTPError) as denied: owner('/owner/api/calls-gateway/unsigned', 'POST', {'minutes': 60}, key='')
                self.assertEqual(denied.exception.code, 401)
                self.assertTrue(owner('/owner/api/calls-gateway/unsigned', 'POST', {'minutes': 60})['active'])
                self.assertTrue(owner('/owner/api/calls-gateway')['unsignedUntil'])
                with urlopen(unsigned, timeout=10) as r: self.assertEqual(r.read().decode(), call_gateway.REJECT_TWIML)
                self.assertIn('بدون توقيع', owner('/owner/api/calls-gateway')['problems'][1]['detail'])
                self.assertFalse(owner('/owner/api/calls-gateway/unsigned', 'POST', {'minutes': 0})['active'])
                with self.assertRaises(HTTPError): urlopen(unsigned, timeout=10)
                self.assertEqual(call_gateway.set_unsigned(9999) > time.time() + 7000, True); call_gateway.set_unsigned(0)
                content_type, twiml = twilio(params)
                self.assertTrue(content_type.startswith('text/xml'))
                ref = twiml.split('x-khdoom-ref=')[1].split('<')[0]
                self.assertIn('sip:proj_test123@sip.api.openai.com;transport=tls', twiml)
                self.assertEqual(twilio(dict(params, To='+19990000000'))[1], call_gateway.REJECT_TWIML)

                event = {'object': 'event', 'id': 'evt_1', 'type': 'realtime.call.incoming',
                         'data': {'call_id': 'rtc_abc', 'sip_headers': [{'name': 'From', 'value': 'sip:+966542027855@twilio'}, {'name': 'X-Khdoom-Ref', 'value': ref}]}}
                with self.assertRaises(HTTPError) as forged: openai(event, secret='whsec_' + base64.b64encode(b'wrong').decode())
                self.assertEqual(forged.exception.code, 401)
                self.assertEqual(openai({'type': 'response.completed', 'data': {}}), {'received': True})
                self.assertEqual(openai(event), {'received': True})
                deadline = time.time() + 10
                log = None
                while time.time() < deadline and log is None:
                    with server.db() as connection:
                        log = connection.execute("SELECT * FROM call_logs WHERE organization_id=7").fetchone()
                    time.sleep(0.05)
                self.assertIsNotNone(log)
                self.assertEqual((log['caller_name'], log['caller_phone'], log['direction'], log['status'], log['summary']), ('حيدر', '966542027855', 'inbound', 'ended', 'ملخص المكالمة'))
                self.assertIn('المتصل: السلام عليكم\nالموظف: وعليكم السلام، معك مراتك للزجاج\nالمتصل: معك حيدر أبي موعد', log['transcript'])
                self.assertIn('بانتظار الموافقة', log['appointment'])
                with server.db() as connection:
                    appointment = connection.execute("SELECT * FROM appointment_requests WHERE organization_id=7").fetchone()
                self.assertEqual((appointment['status'], appointment['source'], appointment['customer_name'], appointment['phone']), ('pending', 'ai_call', 'حيدر', '966542027855'))
                self.assertTrue(appointment['scheduled_at'].startswith(tomorrow + 'T16:00'))
                accept = [body for url, body in posts if url.endswith('/rtc_abc/accept')]
                self.assertEqual(len(accept), 1)
                self.assertIn('مراتك للزجاج', accept[0]['instructions'])
                # إعادة إرسال نفس البلاغ لا تكرر السجل ولا ترفض المكالمة.
                self.assertEqual(openai(event), {'received': True})
                # مكالمة لم يسبقها طلب من Twilio تُرفض.
                stray = {'type': 'realtime.call.incoming', 'data': {'call_id': 'rtc_stray', 'sip_headers': [{'name': 'From', 'value': 'sip:+15550001111@x'}]}}
                self.assertEqual(openai(stray, identifier='evt_2'), {'received': True})
                deadline = time.time() + 10
                while time.time() < deadline and not any(url.endswith('/rtc_stray/reject') for url, body in posts): time.sleep(0.05)
                self.assertTrue(any(url.endswith('/rtc_stray/reject') for url, body in posts))
                self.assertFalse(any(url.endswith('/rtc_abc/reject') for url, body in posts))
                with server.db() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM call_logs").fetchone()[0], 1)
                recent = owner('/owner/api/calls-gateway')['recentCalls']
                self.assertEqual((recent[0]['status'], recent[0]['organizationName'], recent[0]['note']), ('ended', 'مراتك للزجاج', 'موعد مسجل'))
                with urlopen(base + '/owner/calls-trial', timeout=10) as page:
                    self.assertIn('ربط الهاتف الحقيقي', page.read().decode())
            finally:
                httpd.shutdown(); httpd.server_close()


if __name__ == '__main__':
    unittest.main()
