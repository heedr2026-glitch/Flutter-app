"""أسعار وتاريخ العرض تطلع للمشترك بصيغة مقروءة."""
import json
import server
from test_admin_agent import AdminAgentTest


class OfferDisplayTest(AdminAgentTest):
    def test_offer_price_and_end_date_are_readable(self):
        with server.db() as c:
            c.execute("DELETE FROM package_prices")
            c.execute("INSERT INTO package_prices(package,duration_months,price_sar,updated_at) VALUES('basic',12,110,?)", (server.now(),))
            c.commit()
        proposal = self.tool('propose_offer', {'package': 'basic', 'paid_months': 12, 'offer_type': 'bonus', 'bonus_months': 3, 'days_valid': 2})
        self.assertTrue(self.tool('confirm_action', {'token': proposal['token']}, user_text='أكد')['done'])
        offer = [o for o in self.call('/api/package-offers', user=1)[1] if o['has_offer']][0]
        self.assertEqual(json.dumps(offer['price_sar']), '110')
        self.assertRegex(offer['ends_at'], r'^\d{2}-\d{2}-\d{4}$')
        self.assertTrue(offer['ends_at_iso'].startswith('20'))


for name in [n for n in dir(OfferDisplayTest) if n.startswith('test_') and n != 'test_offer_price_and_end_date_are_readable']:
    setattr(OfferDisplayTest, name, None)
