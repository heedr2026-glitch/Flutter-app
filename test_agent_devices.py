"""موظف الإدارة: «تذكّر هذا الجوال» بدل كتابة المفتاح كل مرة، وأدوات الفحص التقني داخله."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from test_admin_agent import AdminAgentTest


class AgentDevicesTest(AdminAgentTest):
    def raw(self, path, method='GET', body=None, headers=None):
        req = Request(f'http://127.0.0.1:{self.httpd.server_port}{path}', method=method, headers={'Content-Type': 'application/json', **(headers or {})},
                      data=None if body is None else json.dumps(body).encode())
        try:
            with urlopen(req, timeout=10) as r:
                return r.status, r.read(), r.headers
        except HTTPError as e:
            data = e.read(); e.close()
            return e.code, data, e.headers

    def test_remember_device_then_revoke(self):
        # بدون مفتاح المالك ما ينحفظ جهاز.
        self.assertEqual(self.call('/owner/api/agent/device', 'POST', {}, user=1)[0], 401)
        status, issued = self.call('/owner/api/agent/device', 'POST', {'label': 'جوال'})
        self.assertEqual(status, 200)
        token = issued['token']
        self.assertTrue(token.startswith('agd_'))
        status, body, _ = self.raw('/owner/api/agent/brief', headers={'X-Agent-Device': token})
        self.assertEqual(status, 200)
        status, body, _ = self.raw('/owner/api/agent/tool', 'POST', {'name': 'platform_stats', 'arguments': '{}'}, {'X-Agent-Device': token})
        self.assertEqual(status, 200)
        self.assertIn('organizations', json.loads(body))
        # رمز الجهاز يفتح موظف الإدارة فقط، مو باقي لوحة الإدارة ولا إصدار أجهزة جديدة.
        self.assertEqual(self.raw('/owner/api/growth', headers={'X-Agent-Device': token})[0], 401)
        self.assertEqual(self.raw('/owner/api/agent/device', 'POST', {}, {'X-Agent-Device': token})[0], 401)
        self.assertEqual(self.raw('/owner/api/agent/brief', headers={'X-Agent-Device': 'agd_wrong'})[0], 401)
        self.assertEqual(self.call('/owner/api/agent/devices')[1]['active'], 1)
        self.assertEqual(self.call('/owner/api/agent/devices/revoke', 'POST', {})[1]['revoked'], 1)
        self.assertEqual(self.raw('/owner/api/agent/brief', headers={'X-Agent-Device': token})[0], 401)

    def test_home_screen_manifest_and_icon(self):
        status, body, headers = self.raw('/owner/agent/manifest.json')
        self.assertEqual(status, 200)
        manifest = json.loads(body)
        self.assertEqual(manifest['start_url'], '/owner/agent')
        self.assertEqual(manifest['display'], 'standalone')
        status, body, headers = self.raw('/owner/agent/icon-192.png')
        self.assertEqual((status, headers['Content-Type'], body[:4]), (200, 'image/png', b'\x89PNG'))
        page = self.raw('/owner/agent')[1].decode()
        self.assertIn('manifest.json', page)
        self.assertIn('تذكّر هذا الجوال', page)

    def test_technical_tools_inside_admin_agent(self):
        self.assertIn('error', self.tool('diagnose_problem', {'question': ''}))
        result = self.tool('diagnose_problem', {'question': 'الصفحات فيها بطء'})
        self.assertNotIn('error', result)
        self.assertIn('error', self.tool('scan_organization', {'organization_id': 999}))
        self.assertNotIn('error', self.tool('scan_organization', {'organization_id': 1}))


for name in [n for n in dir(AgentDevicesTest) if n.startswith('test_') and n not in ('test_remember_device_then_revoke', 'test_home_screen_manifest_and_icon', 'test_technical_tools_inside_admin_agent')]:
    setattr(AgentDevicesTest, name, None)
