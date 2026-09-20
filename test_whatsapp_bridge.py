import contextlib
import hashlib
import hmac
import io
import json
import os
import sqlite3
import unittest
from email.message import Message
from unittest.mock import patch

import whatsapp_bridge as bridge


BASE_CONFIG = {
    "organization_id": 1,
    "phone_number_id": "12345",
    "waba_id": "67890",
    "token": "test-token",
    "app_secret": "base-secret",
    "verify_token": "verify-token",
    "api_version": "v25.0",
}


def signed(raw, secret):
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


class SignatureTests(unittest.TestCase):
    pass


def _valid_body_test(raw):
    def test(self):
        supplied = signed(raw, BASE_CONFIG["app_secret"])
        verified, diagnostic = bridge.verify_webhook_signature(raw, supplied, [BASE_CONFIG])
        self.assertEqual(verified, [BASE_CONFIG])
        details = json.loads(diagnostic)
        self.assertEqual(details["raw_body_length"], len(raw))
        self.assertEqual(details["raw_body_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(details["candidates"][0]["signature_match"])
        self.assertNotIn(supplied, diagnostic)

    return test


_valid_bodies = [
    b'{"a":1}', b'{ "a":1}', b'{"a": 1}', b'{\n"a":1}', b'{"a":1}\n',
    b' {"a":1} ', b'{"text":"hello"}', '{"text":"مرحبا"}'.encode(),
    b'{"a":1,"b":2}', b'{"b":2,"a":1}',
]
for _index, _raw in enumerate(_valid_bodies):
    setattr(SignatureTests, f"test_verifies_verbatim_body_{_index:02d}", _valid_body_test(_raw))


def _changed_body_test(raw):
    def test(self):
        supplied = signed(raw, BASE_CONFIG["app_secret"])
        verified, diagnostic = bridge.verify_webhook_signature(raw + b" ", supplied, [BASE_CONFIG])
        self.assertEqual(verified, [])
        self.assertFalse(json.loads(diagnostic)["candidates"][0]["signature_match"])

    return test


_changed_bodies = [b'{"a":1}', b'{"a": 1}', b'{\n"a":1}', b'{"text":"hello"}', b'{"a":1}\r\n']
for _index, _raw in enumerate(_changed_bodies):
    setattr(SignatureTests, f"test_rejects_changed_body_{_index:02d}", _changed_body_test(_raw))


def _invalid_signature_test(value):
    def test(self):
        verified, diagnostic = bridge.verify_webhook_signature(b'{"a":1}', value, [BASE_CONFIG])
        self.assertEqual(verified, [])
        details = json.loads(diagnostic)
        self.assertFalse(details["signature_encoding_valid"])
        self.assertFalse(details["candidates"][0]["signature_match"])

    return test


_invalid_signatures = ["", "sha1=" + "0" * 64, "sha256=bad", "SHA256=" + "0" * 64, "sha256=" + "0" * 63]
for _index, _value in enumerate(_invalid_signatures):
    setattr(SignatureTests, f"test_rejects_invalid_signature_{_index:02d}", _invalid_signature_test(_value))


def _override_secret_test(secret):
    def test(self):
        raw = b'{"secret":"not logged"}\n'
        env = {
            "KHDOOM_WHATSAPP_CONFIG": json.dumps([BASE_CONFIG]),
            "KHDOOM_WHATSAPP_APP_SECRET_OVERRIDE": secret,
        }
        signature = signed(raw, secret)
        real_hmac_new = hmac.new
        used_keys = []
        def capture_hmac_key(key, *args, **kwargs):
            used_keys.append(key)
            return real_hmac_new(key, *args, **kwargs)
        with patch.dict(os.environ, env):
            configured = bridge.configs()
            with patch("whatsapp_bridge.hmac.new", side_effect=capture_hmac_key):
                verified, diagnostic = bridge.verify_webhook_signature(raw, signature, [BASE_CONFIG])
        self.assertEqual(verified, [BASE_CONFIG])
        self.assertEqual(configured[0]["app_secret"], secret)
        details = json.loads(diagnostic)
        self.assertEqual(details["secret_env_name"], "KHDOOM_WHATSAPP_APP_SECRET_OVERRIDE")
        self.assertEqual(details["candidates"][0]["secret_length"], len(secret.encode("utf-8")))
        self.assertEqual(details["candidates"][0]["secret_sha256"], hashlib.sha256(used_keys[0]).hexdigest())
        self.assertTrue(details["candidates"][0]["signature_match"])
        self.assertEqual(used_keys, [secret.encode("utf-8")])

    return test


_secrets = ["leading space", "trailing space ", " both ", "line\nfeed", "مفتاح-اختبار"]
for _index, _secret in enumerate(_secrets):
    setattr(SignatureTests, f"test_uses_override_secret_verbatim_{_index:02d}", _override_secret_test(_secret))


class SecretSourceTests(unittest.TestCase):
    def test_falls_back_to_config_secret_when_override_is_absent(self):
        raw = b'{"fallback":true}'
        real_hmac_new = hmac.new
        used_keys = []
        def capture_hmac_key(key, *args, **kwargs):
            used_keys.append(key)
            return real_hmac_new(key, *args, **kwargs)
        env = {"KHDOOM_WHATSAPP_CONFIG": json.dumps([BASE_CONFIG])}
        signature = signed(raw, BASE_CONFIG["app_secret"])
        with patch.dict(os.environ, env, clear=True):
            with patch("whatsapp_bridge.hmac.new", side_effect=capture_hmac_key):
                verified, diagnostic = bridge.verify_webhook_signature(
                    raw, signature, [BASE_CONFIG])
        details = json.loads(diagnostic)
        self.assertEqual(verified, [BASE_CONFIG])
        self.assertEqual(details["secret_env_name"], "KHDOOM_WHATSAPP_CONFIG")
        self.assertEqual(details["candidates"][0]["secret_sha256"], hashlib.sha256(used_keys[0]).hexdigest())
        self.assertEqual(used_keys, [BASE_CONFIG["app_secret"].encode("utf-8")])


class RawHttpBodyTests(unittest.TestCase):
    def test_http_handler_verifies_raw_request_bytes_before_json_parse(self):
        event = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "67890", "changes": [{"value": {
                "metadata": {"phone_number_id": "12345"},
                "messages": [{"id": "wamid.test", "from": "966500000000",
                              "timestamp": "1720000000", "type": "text",
                              "text": {"body": "test"}}],
            }}]}],
        }
        raw = b" \r\n" + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode() + b"\n "
        class Handler:
            path = "/webhooks/whatsapp"
            headers = Message()
            rfile = io.BytesIO(raw)

            def _send(self, status, payload):
                self.response = (status, payload)

        handler = Handler()
        handler.headers["Content-Length"] = str(len(raw))
        handler.headers["Content-Type"] = "application/json; charset=utf-8"
        handler.headers["X-Hub-Signature-256"] = signed(raw, BASE_CONFIG["app_secret"])
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        @contextlib.contextmanager
        def db():
            yield connection
        try:
            with patch.dict(os.environ, {"KHDOOM_WHATSAPP_CONFIG": json.dumps([BASE_CONFIG])}):
                self.assertTrue(bridge.handle(handler, "POST", db))
            self.assertEqual(handler.response[0], 200)
            self.assertEqual(connection.execute("SELECT count(*) FROM whatsapp_messages").fetchone()[0], 1)
            row = connection.execute("SELECT conversation_id FROM whatsapp_messages").fetchone()
            self.assertTrue(row[0])
            self.assertEqual(connection.execute("SELECT count(*) FROM whatsapp_conversations").fetchone()[0], 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
