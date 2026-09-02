import copy
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import package_limits
import server

_original_db = server.db


@contextmanager
def closing_test_db():
    connection = _original_db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class PackageLimitsTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        package_limits.initialize(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_defaults_and_vip_unchanged(self):
        self.assertEqual(package_limits.read_limits(self.connection), package_limits.DEFAULT_LIMITS)

    def test_limits_drive_server_enforcement(self):
        payload = {"free": {"employees": 3, "vehicles": 0},
                   "basic": {"employees": 40, "vehicles": 70}}
        package_limits.save_limits(self.connection, payload)
        self.assertEqual(server.package_resource_limit("free", "employees", self.connection), 3)
        self.assertEqual(server.package_resource_limit("free", "vehicles", self.connection), 0)
        self.assertEqual(server.package_resource_limit("basic", "vehicles", self.connection), 70)
        self.assertIsNone(server.package_resource_limit("vip", "employees", self.connection))
        # Initialization is safe on an existing configured database.
        package_limits.initialize(self.connection)
        self.assertEqual(package_limits.read_limits(self.connection)["basic"]["employees"], 40)

    def test_invalid_updates_are_atomic(self):
        for invalid in (-1, 100001, True, 1.5, "5", None):
            payload = {"free": {"employees": 9, "vehicles": 9},
                       "basic": {"employees": invalid, "vehicles": 5}}
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                package_limits.save_limits(self.connection, payload)
            self.assertEqual(package_limits.read_limits(self.connection), package_limits.DEFAULT_LIMITS)
        for payload in (None, [], {}, {"vip": {"employees": 4, "vehicles": 4}}):
            with self.assertRaises(ValueError):
                package_limits.save_limits(self.connection, payload)


class PackageLimitsHttpTest(unittest.TestCase):
    def test_owner_auth_update_public_read_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(server, "DB_PATH", Path(directory) / "test.db"), \
                 patch.object(server, "OWNER_KEY_PATH", Path(directory) / "owner.key"), \
                 patch.object(server, "DATABASE_URL", ""), \
                 patch.object(server, "db", closing_test_db), \
                 patch.dict(os.environ, {"KHDOOM_OWNER_KEY": "test-only-owner-key"}):
                server.init_db()
                httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = "http://127.0.0.1:" + str(httpd.server_port)
                payload = {"free": {"employees": 2, "vehicles": 4},
                           "basic": {"employees": 25, "vehicles": 50}}

                def request(path, method="GET", data=None, owner=False):
                    headers = {"Content-Type": "application/json"}
                    if owner:
                        headers["X-Owner-Key"] = "test-only-owner-key"
                    body = None if data is None else json.dumps(data).encode()
                    with urlopen(Request(base + path, data=body, headers=headers, method=method), timeout=5) as response:
                        return json.load(response)

                try:
                    for method in ("GET", "PUT"):
                        with self.assertRaises(HTTPError) as error:
                            request("/owner/api/package-limits", method, payload if method == "PUT" else None)
                        self.assertEqual(error.exception.code, 401)
                        error.exception.close()
                    request("/owner/api/package-limits", "PUT", payload, owner=True)
                    actual = request("/api/package-limits")
                    self.assertEqual(actual["free"], payload["free"])
                    self.assertEqual(actual["basic"], payload["basic"])
                    self.assertIsNone(actual["vip"]["vehicles"])
                    bad = copy.deepcopy(payload)
                    bad["basic"]["employees"] = -5
                    with self.assertRaises(HTTPError) as error:
                        request("/owner/api/package-limits", "PUT", bad, owner=True)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                    server.init_db()
                    self.assertEqual(request("/api/package-limits"), actual)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
