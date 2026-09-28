import sqlite3
import unittest

import server


class LoginIdentityTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE users(
               id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT,
               username TEXT, email TEXT, phone TEXT, role TEXT,
               active INTEGER, password_hash TEXT, password_salt TEXT,
               permissions TEXT)"""
        )

    def tearDown(self):
        self.connection.close()

    def add_user(self, ident, username, phone="", role="admin"):
        self.connection.execute(
            "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ident, ident, username, username, "", phone, role, 1, "h", "s", "{}"),
        )

    def test_arabic_username_accepts_legacy_spacing_and_unicode(self):
        self.add_user(1, "  مهدي  بن عشوان  ")
        user = server.find_login_user(self.connection, "مهدي بن عشوان")
        self.assertEqual(user["id"], 1)

    def test_manager_phone_matches_formatted_stored_value(self):
        self.add_user(1, "mahdi", "+966 54 000 1234")
        user = server.find_login_user(self.connection, "00966540001234")
        self.assertEqual(user["id"], 1)

    def test_ambiguous_employee_phone_does_not_select_random_account(self):
        self.add_user(1, "first", "0500000000", "employee")
        self.add_user(2, "second", "0500000000", "employee")
        self.assertIsNone(server.find_login_user(self.connection, "0500000000"))

    def test_one_manager_wins_when_phone_is_shared_with_employee(self):
        self.add_user(1, "manager", "0500000000", "admin")
        self.add_user(2, "employee", "0500000000", "employee")
        user = server.find_login_user(self.connection, "0500000000")
        self.assertEqual(user["id"], 1)


if __name__ == "__main__":
    unittest.main()
