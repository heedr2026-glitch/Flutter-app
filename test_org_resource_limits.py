import sqlite3
import unittest

import package_limits


class OrgResourceLimitsTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        package_limits.initialize(self.c)
        package_limits.initialize_org(self.c)

    def test_override_applies_to_one_organization_only(self):
        self.assertEqual(package_limits.effective_limit(self.c, "basic", "vehicles", 1), 5)
        package_limits.save_org_limits(self.c, 1, {"vehicles": 7})
        self.assertEqual(package_limits.effective_limit(self.c, "basic", "vehicles", 1), 7)
        self.assertEqual(package_limits.effective_limit(self.c, "basic", "vehicles", 2), 5)

    def test_unlimited_and_reset(self):
        package_limits.save_org_limits(self.c, 1, {"employees": "unlimited"})
        self.assertIsNone(package_limits.effective_limit(self.c, "free", "employees", 1))
        package_limits.save_org_limits(self.c, 1, {"employees": None})
        self.assertEqual(package_limits.effective_limit(self.c, "free", "employees", 1), 1)

    def test_rejects_bad_values(self):
        for bad in ({"vehicles": -3}, {"vehicles": "x"}, {"ads": 2}):
            with self.assertRaises(ValueError):
                package_limits.save_org_limits(self.c, 1, bad)


if __name__ == "__main__":
    unittest.main()
