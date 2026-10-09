import unittest
from urllib.request import urlopen
from test_service_number_claims import ServiceNumberClaimsTest


class LegalPagesTest(ServiceNumberClaimsTest):
    def test_privacy_and_terms_pages(self):
        base = f'http://127.0.0.1:{self.httpd.server_port}'
        with urlopen(base + '/privacy', timeout=10) as r:
            html = r.read().decode()
        self.assertIn('سياسة الخصوصية', html); self.assertIn('OpenAI', html); self.assertIn('heedr.2026@gmail.com', html); self.assertIn('/terms', html)
        with urlopen(base + '/terms', timeout=10) as r:
            html = r.read().decode()
        self.assertIn('شروط استخدام تطبيق خدوم', html); self.assertIn('/privacy', html)
