"""نسخة احتياطية ينزلها المالك فقط، وتنبيه في الموجز إذا مر أسبوع بدون نسخة."""
import gzip
import json
from urllib.request import Request, urlopen

import admin_agent
import server
from test_service_number_claims import ServiceNumberClaimsTest, KEY


class PlatformBackupTest(ServiceNumberClaimsTest):
    def test_owner_downloads_backup_and_brief_updates(self):
        self.assertEqual(self.call('/owner/api/backup', user=1)[0], 401)
        self.assertTrue(self.call('/owner/api/agent/brief')[1]['backup_overdue'])
        req = Request(f'http://127.0.0.1:{self.httpd.server_port}/owner/api/backup', headers={'X-Owner-Key': KEY})
        with urlopen(req, timeout=20) as r:
            self.assertEqual(r.headers['Content-Type'], 'application/gzip')
            doc = json.loads(gzip.decompress(r.read()))
        self.assertEqual(doc['khadoum_backup'], 1)
        self.assertIn('organizations', doc['tables'])
        self.assertNotIn('sessions', doc['tables'])
        brief = self.call('/owner/api/agent/brief')[1]
        if True:
            self.assertFalse(brief['backup_overdue'])
            self.assertTrue(brief['last_backup_at'])
