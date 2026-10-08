"""إخفاء الواتساب والمكالمات من التطبيق بمفتاح في لوحة الإدارة، وصورة مرفقة مع شكوى الدعم."""
import base64
import json
import unittest
from urllib.request import urlopen

import app_features
from test_service_number_claims import ServiceNumberClaimsTest

PNG = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 64).decode()


class AppFeaturesTest(ServiceNumberClaimsTest):
    def test_whatsapp_and_calls_hidden_until_admin_shows_them(self):
        with urlopen(f'http://127.0.0.1:{self.httpd.server_port}/api/app-features', timeout=10) as r:
            self.assertEqual(json.load(r), {'whatsapp': False, 'calls': False})
        # المشترك لا يقدر يغيّرها؛ الإدارة فقط.
        self.assertEqual(self.call('/owner/api/app-features', 'POST', {'name': 'calls', 'enabled': True}, user=1)[0], 401)
        self.assertEqual(self.call('/owner/api/app-features', 'POST', {'name': 'sms', 'enabled': True})[0], 400)
        status, flags = self.call('/owner/api/app-features', 'POST', {'name': 'calls', 'enabled': True})
        self.assertEqual((status, flags), (200, {'whatsapp': False, 'calls': True}))
        # قيمة غير منطقية لا تُظهر الخدمة.
        self.assertEqual(self.call('/owner/api/app-features', 'POST', {'name': 'whatsapp', 'enabled': 'yes'})[1]['whatsapp'], False)
        with urlopen(f'http://127.0.0.1:{self.httpd.server_port}/api/app-features', timeout=10) as r:
            self.assertEqual(json.load(r), {'whatsapp': False, 'calls': True})
        self.assertEqual(self.call('/owner/api/app-features', 'POST', {'name': 'calls', 'enabled': False})[1]['calls'], False)

    def test_support_ticket_with_picture_reaches_admin_only(self):
        status, saved = self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'الصفحة ما تفتح عندي', 'imageData': PNG}, user=1)
        self.assertEqual(status, 201)
        self.assertTrue(saved['referenceCode'] and saved['createdAt'] and saved['hasAttachment'])
        # صورة بصيغة غير مقبولة ترفض الشكوى برسالة واضحة.
        self.assertEqual(self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'مشكلة ثانية هنا', 'imageData': 'data:text/html;base64,AAAA'}, user=1)[0], 400)
        self.assertEqual(self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'بدون صورة هنا'}, user=2)[0], 201)
        status, mine = self.call('/api/support-tickets', user=1)
        self.assertEqual(status, 200)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['has_attachment'], 1)
        self.assertNotIn('attachment_data', mine[0])
        self.assertTrue(mine[0]['created_at'])
        # قائمة الإدارة: علامة فقط بدون الصورة نفسها، والصورة تُطلب منفصلة.
        status, page = self.call('/owner/api/v2/support')
        self.assertEqual(status, 200)
        items = {item['reference_code']: item for item in page['items']}
        mine_admin = items[saved['referenceCode']]
        self.assertTrue(mine_admin['has_attachment'])
        self.assertNotIn('attachment_data', mine_admin)
        self.assertEqual([item['has_attachment'] for item in page['items'] if item['id'] != saved['id']], [False])
        status, image = self.call('/owner/api/v2/support/%d/attachment' % saved['id'])
        self.assertEqual((status, image['image']), (200, PNG))
        other = [item['id'] for item in page['items'] if item['id'] != saved['id']][0]
        self.assertEqual(self.call('/owner/api/v2/support/%d/attachment' % other)[0], 404)
        self.assertEqual(self.call('/owner/api/v2/support/%d/attachment' % saved['id'], user=1)[0], 401)

    def test_garbled_dash_in_old_complaints_is_repaired_for_subscriber(self):
        self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'رسالة قديمة â€” فيها شرطة'}, user=1)
        self.assertIn('قديمة — فيها', self.call('/api/support-tickets', user=1)[1][0]['message'])

    def test_attachment_validation(self):
        class E(Exception):
            def __init__(self, status, message): super().__init__(message); self.status = status
        self.assertEqual(app_features.clean_attachment('', E), '')
        self.assertEqual(app_features.clean_attachment(PNG, E), PNG)
        with self.assertRaises(E): app_features.clean_attachment('data:image/png;base64,' + 'A' * app_features.MAX_ATTACHMENT_CHARS, E)
        with self.assertRaises(E): app_features.clean_attachment('data:image/svg+xml;base64,AAAA', E)


del ServiceNumberClaimsTest

if __name__ == '__main__':
    unittest.main()
