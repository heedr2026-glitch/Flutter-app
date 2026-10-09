"""أرقام النمو في لوحة الإدارة: قمع التسجيل والإلغاء واستخدام المزايا."""
import server
from test_service_number_claims import ServiceNumberClaimsTest


class GrowthStatsTest(ServiceNumberClaimsTest):
    def test_growth_numbers_and_expired_subscription_counts_as_churn(self):
        self.assertEqual(self.call('/owner/api/growth', user=1)[0], 401)
        status, g = self.call('/owner/api/growth?days=30')
        self.assertEqual(status, 200)
        self.assertEqual([s['label'] for s in g['funnel']['steps']][0], 'سجّل')
        self.assertGreaterEqual(g['funnel']['steps'][0]['count'], 1)
        self.assertEqual(g['churn']['count'], 0)
        labels = {x['label'] for x in g['features']['items']}
        self.assertIn('المواعيد والطلبات', labels)
        self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'رسالة للدعم هنا'}, user=1)
        with server.db() as c:
            org = c.execute('SELECT id FROM organizations ORDER BY id LIMIT 1').fetchone()['id']
            c.execute("DELETE FROM subscriptions WHERE organization_id=?", (org,))
            c.execute("INSERT INTO subscriptions(organization_id,package,starts_at,expires_at) VALUES(?,?,?,?)", (org, 'vip', '2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00'))
            self.assertEqual(server.downgrade_expired_subscriptions(c), 1)
        g = self.call('/owner/api/growth')[1]
        self.assertEqual(g['churn']['count'], 1)
        self.assertEqual(g['churn']['items'][0]['kind'], 'انتهى وما جدد')
        support = [x for x in g['features']['items'] if x['label'] == 'الدعم'][0]
        self.assertEqual(support['count'], 1)
