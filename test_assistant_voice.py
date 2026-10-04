"""«اسألني» speaks a clear Saudi dialect, in writing and (through the server) in voice."""
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

import ai_core
import owner_admin
import server
import training_context
from test_owner_admin_fixes import test_db

AUDIO = b'ID3' + b'\x00' * 400


class AssistantDialectTest(unittest.TestCase):
    def test_assistant_uses_saudi_dialect_and_reception_keeps_its_professional_tone(self):
        assistant = ai_core.ROLES['assistant'].instructions
        self.assertIn('لهجة سعودية واضحة', assistant)
        self.assertIn('لا تكتب بالفصحى', assistant)
        self.assertNotIn('عربية سعودية بيضاء', assistant)
        self.assertIn('لا تخترع اسما أو سعرا', assistant)  # قواعد الأمانة باقية كما هي
        self.assertIn('عربية سعودية بيضاء', ai_core.ROLES['reception'].instructions)
        self.assertNotIn('لهجة سعودية واضحة', ai_core.ROLES['reception'].instructions)

    def test_fixed_how_to_answers_are_in_dialect_with_the_real_screen_names(self):
        info = {'package': {'package': 'vip', 'expires_at': '2026-12-31T00:00:00+00:00'}, 'deviceSnapshotCounts': {'vehicles': 3}}
        add_vehicle = training_context.direct_answer('كيف اضيف مركبة', info)
        self.assertIn('عشان تضيف مركبة', add_vehicle)
        self.assertIn('«المركبات ← إضافة مركبة»', add_vehicle)
        self.assertEqual(training_context.direct_answer('كم عدد المركبات', info), 'عندك الحين 3 من المركبات.')
        self.assertEqual(training_context.direct_answer('باقتي', info), 'باقتك الحالية هي VIP.\nوتنتهي يوم: 2026-12-31')
        for question in ('كيف اضيف موظف جديد', 'كيف اضيف فرع جديد', 'كيف اسوي اعلان', 'كيف افعل البصمة'):
            answer = training_context.direct_answer(question, info)
            self.assertNotRegex(answer, 'يمكنك|يرجى|قم ب|لديك')
            self.assertIn('«', answer)


class AssistantSpeechTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.calls = []
        self.fail = False

        def provider(payload):
            self.calls.append(payload)
            if self.fail:
                raise ai_core.AIServiceError(502, 'تعذر إنشاء الصوت بالذكاء الاصطناعي الآن')
            return AUDIO + payload['voice'].encode()
        self.patches = [
            patch.object(server, 'DB_PATH', Path(self.directory.name) / 'test.db'),
            patch.object(server, 'OWNER_KEY_PATH', Path(self.directory.name) / 'owner.key'),
            patch.object(server, 'DATABASE_URL', ''),
            patch.object(server, 'db', test_db),
            patch.object(owner_admin, '_FALLBACKS_READY', False),
            patch.object(ai_core, '_speech_http', provider),
            patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'voice-key', 'KHDOOM_SUBSCRIPTION_SWEEP_SECONDS': '0', 'KHDOOM_SPEECH_HOURLY': '4'}),
        ]
        for item in self.patches:
            item.start()
        for name in ('KHDOOM_VOICE_MALE', 'KHDOOM_VOICE_FEMALE', 'KHDOOM_TTS_MODEL'):
            os.environ.pop(name, None)
        server._RATE_LIMIT_BUCKETS.clear(); server._SPEECH_CACHE.clear(); server._SPEECH_WINDOW.clear()
        server.init_db()
        with server.db() as c:
            c.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('مؤسسة', server.now()))
            c.execute("INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(1,1,'مالك','owner1','','05','x','x','admin',?)", (server.now(),))
            c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)', (hashlib.sha256(b'tok').hexdigest(), 1, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), server.now()))
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def speak(self, body, token='tok'):
        request = Request(f'http://127.0.0.1:{self.httpd.server_port}/api/ai/speech', method='POST', data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, response.headers.get('Content-Type'), response.read()
        except HTTPError as error:
            return error.code, error.headers.get('Content-Type'), json.load(error)

    def test_reply_is_spoken_in_the_chosen_voice_and_repeats_are_free(self):
        status, kind, audio = self.speak({'text': 'سجلك  التجاري ينتهي يوم 30 أكتوبر', 'voice': 'male'})
        self.assertEqual((status, kind, audio), (200, 'audio/mpeg', AUDIO + b'cedar'))
        sent = self.calls[0]
        self.assertEqual((sent['model'], sent['voice'], sent['input'], sent['response_format']), ('gpt-4o-mini-tts', 'cedar', 'سجلك التجاري ينتهي يوم 30 أكتوبر', 'mp3'))
        self.assertIn('لهجة سعودية', sent['instructions'])
        self.assertEqual(self.speak({'text': 'سجلك التجاري ينتهي يوم 30 أكتوبر', 'voice': 'male'})[2], AUDIO + b'cedar')
        self.assertEqual(len(self.calls), 1)  # نفس النص بنفس الصوت من الذاكرة
        self.assertEqual(self.speak({'text': 'سجلك التجاري ينتهي يوم 30 أكتوبر', 'voice': 'female'})[2], AUDIO + b'marin')
        self.assertEqual(len(self.calls), 2)
        with patch.dict(os.environ, {'KHDOOM_VOICE_FEMALE': 'coral'}):
            self.assertEqual(self.speak({'text': 'مرحبا بك', 'voice': 'female'})[2], AUDIO + b'coral')

    def test_only_signed_in_users_and_real_text(self):
        self.assertEqual(self.speak({'text': 'مرحبا بك'}, token='')[0], 401)
        self.assertEqual(self.speak({'text': 'مرحبا بك'}, token='wrong')[0], 401)
        self.assertEqual(self.speak({'text': '  '})[0], 400)
        self.assertEqual(self.calls, [])
        long = self.speak({'text': 'كلمة ' * 500})
        self.assertEqual(long[0], 200)
        self.assertLessEqual(len(self.calls[0]['input']), ai_core.SPEECH_MAX_CHARS)

    def test_hourly_cap_and_provider_failure_tell_the_app_to_fall_back(self):
        for index in range(4):
            self.assertEqual(self.speak({'text': f'رسالة رقم {index}'})[0], 200)
        status, _, body = self.speak({'text': 'رسالة خامسة'})
        self.assertEqual(status, 429)
        self.assertIn('بصوت الجوال', body['error'])
        self.assertEqual(self.speak({'text': 'رسالة رقم 0'})[0], 200)  # المحفوظ لا يُحسب
        server._SPEECH_WINDOW.clear(); self.fail = True
        status, _, body = self.speak({'text': 'نص جديد'})
        self.assertEqual((status, body['error']), (502, 'تعذر إنشاء الصوت بالذكاء الاصطناعي الآن'))


if __name__ == '__main__':
    unittest.main()
