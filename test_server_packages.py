import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import server


class PackageAndAdvertisementRulesTest(unittest.TestCase):
    def test_employee_and_vehicle_limits_match_all_packages(self):
        for resource in ("employees", "vehicles"):
            self.assertEqual(server.package_resource_limit("free", resource), 1)
            self.assertEqual(server.package_resource_limit("basic", resource), 5)
            self.assertIsNone(server.package_resource_limit("vip", resource))

    def test_expired_paid_subscription_returns_to_free(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE subscriptions (organization_id INTEGER PRIMARY KEY, package TEXT, starts_at TEXT, expires_at TEXT)")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        connection.execute("INSERT INTO subscriptions VALUES (1, 'vip', 'old', ?)", (expired,))
        self.assertEqual(server.downgrade_expired_subscriptions(connection), 1)
        row = connection.execute("SELECT package, expires_at FROM subscriptions WHERE organization_id=1").fetchone()
        self.assertEqual(row["package"], "free")
        self.assertIsNone(row["expires_at"])

    def test_expired_organization_ad_is_deactivated(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE advertisements (id INTEGER PRIMARY KEY, approved INTEGER, active INTEGER, expires_at TEXT)")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        connection.execute("INSERT INTO advertisements VALUES (1, 1, 1, ?)", (expired,))
        self.assertEqual(server.purge_expired_ads(connection), 1)
        active = connection.execute("SELECT active FROM advertisements WHERE id=1").fetchone()["active"]
        self.assertEqual(active, 0)


if __name__ == "__main__":
    unittest.main()
