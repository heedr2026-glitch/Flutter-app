import json
import os
import sqlite3
import unittest
from unittest.mock import patch
import customer_push


class CustomerPushTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.execute("CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY)")
        self.c.execute("INSERT INTO chat_sessions(id) VALUES(1)")
        customer_push.migrate(self.c)

    def tearDown(self):
        self.c.close()

    def test_subscription_is_scoped_and_validated(self):
        class Error(Exception):
            def __init__(self, status, message): self.status, self.message = status, message
        customer_push.save(self.c, 1, "chat1", {"endpoint":"https://push.test/1",
            "keys":{"p256dh":"public","auth":"secret"}}, "now", Error)
        row = self.c.execute("SELECT * FROM customer_push_subscriptions").fetchone()
        self.assertEqual(row["session_id"], 1)
        with self.assertRaises(Error):
            customer_push.save(self.c, 1, "chat1", {"endpoint":"http://bad"}, "now", Error)

    def test_missing_keys_is_safe(self):
        old = dict(os.environ)
        try:
            os.environ.pop("KHDOOM_VAPID_PRIVATE_KEY", None)
            os.environ.pop("KHDOOM_VAPID_PUBLIC_KEY", None)
            self.assertEqual(customer_push.notify(self.c, 1, "رد", "وصل"), 0)
        finally:
            os.environ.clear(); os.environ.update(old)


    def test_employee_reply_payload_is_generic(self):
        customer_push.save(self.c, 1, "chat1", {"endpoint":"https://push.test/1",
            "keys":{"p256dh":"public","auth":"secret"}}, "now", RuntimeError)
        captured = []
        old = dict(os.environ)
        try:
            os.environ["KHDOOM_VAPID_PRIVATE_KEY"] = "private"
            os.environ["KHDOOM_VAPID_PUBLIC_KEY"] = "public"
            with patch("pywebpush.webpush", side_effect=lambda **kwargs: captured.append(json.loads(kwargs["data"]))):
                self.assertEqual(customer_push.notify_employee_reply(self.c, 1), 1)
        finally:
            os.environ.clear(); os.environ.update(old)
        self.assertEqual(captured, [{"title":"خدوم", "body":"لديك رد جديد من المؤسسة", "url":"/chat/chat1"}])
        self.assertNotIn("عميل", json.dumps(captured, ensure_ascii=False))

    def test_service_worker_ignores_remote_title_and_body(self):
        script = customer_push.service_worker()
        self.assertIn("body:'لديك رد جديد من المؤسسة'", script)
        self.assertNotIn("d.body", script)
        self.assertNotIn("d.title", script)