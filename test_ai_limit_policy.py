import sqlite3
import unittest
from datetime import datetime, timezone

import ai_limit_policy
import service_quota


class LimitPolicyTest(unittest.TestCase):
    def setUp(self):
        c = self.c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE subscriptions(organization_id INTEGER, package TEXT, starts_at TEXT)")
        c.execute("CREATE TABLE ai_usage(organization_id INTEGER, created_at TEXT)")
        c.execute("CREATE TABLE ai_limits(organization_id INTEGER PRIMARY KEY, daily_limit INTEGER NOT NULL)")
        c.execute("""CREATE TABLE platform_packages(package TEXT PRIMARY KEY, monthly REAL, yearly REAL,
                     ai_daily INTEGER NOT NULL, ai_employees INTEGER, whatsapp_units INTEGER, calls_units INTEGER,
                     ads_units INTEGER, features TEXT, ai_monthly INTEGER)""")
        for name, daily in (("free", 5), ("basic", 30), ("vip", 100)):
            c.execute("INSERT INTO platform_packages(package,monthly,yearly,ai_daily,ai_employees) VALUES(?,0,0,?,1)",
                      (name, daily))
        now = datetime.now(timezone.utc).isoformat()
        c.execute("INSERT INTO organizations VALUES(1,'org1',?)", (now,))
        c.execute("INSERT INTO subscriptions VALUES(1,'basic',?)", (now,))
        c.execute("INSERT INTO organizations VALUES(2,'org2',?)", (now,))
        c.execute("INSERT INTO subscriptions VALUES(2,'vip',?)", (now,))
        for _ in range(5):
            c.execute("INSERT INTO ai_usage VALUES(1,?)", (now,))

    def svc(self, org, name="ai"):
        return service_quota.snapshot(self.c, org)["services"][name]

    def test_each_package_has_its_own_ai_limit(self):
        ai_limit_policy.handle(self.c, "ai-limits/package/basic", "PUT", {"daily": "4", "monthly": "100"})
        self.assertEqual(self.svc(1)["daily_limit"], 4)
        self.assertTrue(self.svc(1)["blocked"])
        self.assertEqual(self.svc(1)["limit"], 100)
        self.assertEqual(self.svc(2)["daily_limit"], 100)
        data = ai_limit_policy.handle(self.c, "ai-limits", "GET", {})
        self.assertEqual([p["package"] for p in data["packages"]], ["free", "basic", "vip"])
        self.assertEqual(data["items"][0]["id"], 1)

    def test_special_overrides_package_and_remove_returns(self):
        ai_limit_policy.handle(self.c, "ai-limits/special/1", "PUT", {"daily": "50", "monthly": "900"})
        self.assertEqual(self.svc(1)["daily_limit"], 50)
        self.assertEqual(self.svc(1)["limit"], 900)
        ai_limit_policy.handle(self.c, "ai-limits/special/1", "PUT", {"remove": True})
        self.assertEqual(self.svc(1)["daily_limit"], 30)

    def test_whatsapp_package_and_special(self):
        self.assertIsNone(self.svc(1, "whatsapp")["limit"])
        ai_limit_policy.handle(self.c, "service-limits/whatsapp/package/basic", "PUT", {"monthly": "500"})
        self.assertEqual(self.svc(1, "whatsapp")["limit"], 500)
        self.assertIsNone(self.svc(2, "whatsapp")["limit"])
        ai_limit_policy.handle(self.c, "service-limits/whatsapp/special/1", "PUT", {"monthly": "50"})
        self.assertEqual(self.svc(1, "whatsapp")["limit"], 50)
        ai_limit_policy.handle(self.c, "service-limits/whatsapp/special/1", "PUT", {"remove": True})
        self.assertEqual(self.svc(1, "whatsapp")["limit"], 500)
        self.assertIsNone(self.svc(1, "calls")["limit"])

    def test_validation(self):
        for route, body in (("ai-limits/package/basic", {"daily": "0"}),
                            ("ai-limits/package/basic", {"daily": "10", "monthly": "5"}),
                            ("ai-limits/package/gold", {"daily": "10"}),
                            ("ai-limits/special/1", {"monthly": "5"}),
                            ("service-limits/sms", {})):
            with self.assertRaises(ValueError):
                ai_limit_policy.handle(self.c, route, "PUT", body)


if __name__ == "__main__":
    unittest.main()
