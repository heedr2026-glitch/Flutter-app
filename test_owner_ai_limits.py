import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from test_advertisements import test_db


class OwnerAiLimitsTest(unittest.TestCase):
    def test_owner_can_set_one_or_all_organization_limits(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "DB_PATH", Path(directory) / "test.db"), patch.object(server, "DATABASE_URL", ""), patch.object(server, "db", test_db), patch.dict(os.environ, {"KHDOOM_OWNER_KEY": "owner-test"}):
            server.init_db()
            with server.db() as connection:
                for organization_id, package in ((1, "free"), (2, "vip")):
                    connection.execute("INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)", (organization_id, "Org " + str(organization_id), server.now()))
                    connection.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,?,?)", (organization_id, package, server.now()))
                connection.commit()
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def put(path, value):
                request = Request("http://127.0.0.1:" + str(httpd.server_port) + path, method="PUT", data=json.dumps({"dailyLimit": value}).encode(), headers={"Content-Type": "application/json", "X-Owner-Key": "owner-test"})
                with urlopen(request, timeout=5) as response:
                    return json.load(response)

            try:
                self.assertEqual(put("/owner/api/organizations/1/ai-limit", 12)["dailyLimit"], 12)
                result = put("/owner/api/organizations/ai-limit", 77)
                self.assertEqual(result["updatedOrganizations"], 2)
                with server.db() as connection:
                    limits = [row["daily_limit"] for row in connection.execute("SELECT daily_limit FROM ai_limits ORDER BY organization_id").fetchall()]
                self.assertEqual(limits, [77, 77])
                with self.assertRaises(HTTPError) as invalid:
                    put("/owner/api/organizations/1/ai-limit", 0)
                self.assertEqual(invalid.exception.code, 400)
                invalid.exception.close()
                with self.assertRaises(HTTPError) as missing:
                    put("/owner/api/organizations/999/ai-limit", 10)
                self.assertEqual(missing.exception.code, 404)
                missing.exception.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
