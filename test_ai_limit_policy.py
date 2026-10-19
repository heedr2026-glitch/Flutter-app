import sqlite3
import unittest
from datetime import datetime, timezone

import ai_limit_policy
import service_quota


class AiLimitPolicyTest(unittest.TestCase):
    def setUp(self):
        c = self.c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE subscriptions(organization_id INTEGER, package TEXT, starts_at TEXT)")
        c.execute("CREATE TABLE ai_usage(organization_id INTEGER, created_at TEXT)")
        c.execute("CREATE TABLE ai_limits(organization_id INTEGER PRIMARY KEY, daily_limit INTEGER NOT NULL)")
        now = datetime.now(timezone.utc).isoformat()
        for i in (1, 2):
            c.execute("INSERT INTO organizations VALUES(?,?,?)", (i, f"org{i}", now))
            c.execute("INSERT INTO subscriptions VALUES(?,?,?)", (i, "basic", now))
        for _ in range(5):
            c.execute("INSERT INTO ai_usage VALUES(1,?)", (now,))

    def ai(self, org):
        return service_quota.snapshot(self.c, org)["services"]["ai"]

    def test_package_default_then_group(self):
        ai_limit_policy.migrate(self.c)
        self.assertEqual(self.ai(1)["daily_limit"], 30)
        ai_limit_policy.handle(self.c, "ai-limits/group", "PUT", {"daily": "4", "monthly": "100"})
        self.assertEqual(self.ai(1)["daily_limit"], 4)
        self.assertTrue(self.ai(1)["blocked"])
        self.assertEqual(self.ai(2)["limit"], 100)
        ai_limit_policy.handle(self.c, "ai-limits/group", "PUT", {"clear": True})
        self.assertEqual(self.ai(1)["daily_limit"], 30)

    def test_special_overrides_group_and_remove_returns(self):
        ai_limit_policy.handle(self.c, "ai-limits/group", "PUT", {"daily": "4", "monthly": "100"})
        ai_limit_policy.handle(self.c, "ai-limits/special/1", "PUT", {"daily": "50", "monthly": "900"})
        self.assertEqual(self.ai(1)["daily_limit"], 50)
        self.assertEqual(self.ai(1)["limit"], 900)
        self.assertEqual(self.ai(2)["daily_limit"], 4)
        data = ai_limit_policy.handle(self.c, "ai-limits/special/1", "PUT", {"remove": True})
        self.assertFalse([x for x in data["items"] if x["special"]])
        self.assertEqual(self.ai(1)["daily_limit"], 4)

    def test_validation(self):
        with self.assertRaises(ValueError):
            ai_limit_policy.handle(self.c, "ai-limits/group", "PUT", {"daily": "-1"})
        with self.assertRaises(ValueError):
            ai_limit_policy.handle(self.c, "ai-limits/special/1", "PUT", {"monthly": "5"})


if __name__ == "__main__":
    unittest.main()


class ServiceLimitTest(unittest.TestCase):
    def setUp(self):
        c = self.c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE subscriptions(organization_id INTEGER, package TEXT, starts_at TEXT)")
        c.execute("CREATE TABLE ai_usage(organization_id INTEGER, created_at TEXT)")
        now = datetime.now(timezone.utc).isoformat()
        for i in (1, 2):
            c.execute("INSERT INTO organizations VALUES(?,?,?)", (i, f"org{i}", now))
            c.execute("INSERT INTO subscriptions VALUES(?,?,?)", (i, "basic", now))

    def wa(self, org):
        return service_quota.snapshot(self.c, org)["services"]["whatsapp"]

    def test_group_and_special_whatsapp(self):
        self.assertIsNone(self.wa(1)["limit"])
        ai_limit_policy.handle(self.c, "service-limits/whatsapp/group", "PUT", {"monthly": "500"})
        self.assertEqual(self.wa(1)["limit"], 500)
        ai_limit_policy.handle(self.c, "service-limits/whatsapp/special/2", "PUT", {"monthly": "50"})
        self.assertEqual(self.wa(2)["limit"], 50)
        self.assertEqual(self.wa(1)["limit"], 500)
        data = ai_limit_policy.handle(self.c, "service-limits/whatsapp/special/2", "PUT", {"remove": True})
        self.assertEqual(self.wa(2)["limit"], 500)
        self.assertEqual(data["service"], "whatsapp")
        self.assertIsNone(service_quota.snapshot(self.c, 1)["services"]["calls"]["limit"])
        with self.assertRaises(ValueError):
            ai_limit_policy.handle(self.c, "service-limits/sms", "GET", {})
