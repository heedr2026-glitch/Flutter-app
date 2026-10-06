import json
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import server
import service_quota
import whatsapp_bridge
import test_identity_tracking


def ago(days=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).isoformat()


class CycleDates(unittest.TestCase):
    def test_cycle_follows_subscription_day_and_clamps_short_months(self):
        self.assertEqual(service_quota.cycle(date(2026, 9, 12), date(2026, 10, 5)), (date(2026, 9, 12), date(2026, 10, 12)))
        self.assertEqual(service_quota.cycle(date(2026, 9, 12), date(2026, 10, 12)), (date(2026, 10, 12), date(2026, 11, 12)))
        self.assertEqual(service_quota.cycle(date(2026, 9, 12), date(2026, 9, 12)), (date(2026, 9, 12), date(2026, 10, 12)))
        self.assertEqual(service_quota.cycle(date(2026, 1, 31), date(2026, 2, 28)), (date(2026, 2, 28), date(2026, 3, 31)))
        self.assertEqual(service_quota.cycle(date(2026, 1, 31), date(2026, 2, 27)), (date(2026, 1, 31), date(2026, 2, 28)))
        self.assertEqual(service_quota.cycle(date(2025, 12, 15), date(2026, 1, 3)), (date(2025, 12, 15), date(2026, 1, 15)))
        # اشتراك مؤرخ في المستقبل أو بلا تاريخ: الدورة تبدأ اليوم.
        self.assertEqual(service_quota.cycle(date(2027, 1, 1), date(2026, 10, 5))[0], date(2026, 10, 5))
        self.assertEqual(service_quota.cycle(None, date(2026, 10, 5))[0], date(2026, 10, 5))


class QuotaHTTP(test_identity_tracking.DirectoryTrackingHTTP):
    def setUp(self):
        super().setUp()
        with server.db() as c:
            whatsapp_bridge.initialize(c)
            # الدورة بدأت قبل 3 أيام.
            c.execute('UPDATE subscriptions SET starts_at=? WHERE organization_id=1', (ago(days=3),))
            c.commit()

    def package(self, **values):
        with server.db() as c:
            for key, value in values.items():
                c.execute('UPDATE platform_packages SET %s=? WHERE package=?' % key, (value, 'vip'))
            c.commit()

    def wa(self, c, direction='outbound', state='accepted', days=0, org=1, peer='966500000001'):
        c.execute("INSERT INTO whatsapp_messages(id,organization_id,phone_number_id,peer,direction,body,timestamp,state) VALUES(?,?,?,?,?,?,?,?)",
                  (server.secrets.token_hex(8), org, 'pn1', peer, direction, 'x', int(time.time()) - days * 86400, state))

    def ai(self, c, days=0, org=1, count=1):
        for _ in range(count):
            c.execute('INSERT INTO ai_usage(organization_id,user_id,employee_type,created_at) VALUES(?,?,?,?)', (org, 1 if org == 1 else 3, 'assistant', ago(days=days)))

    def state(self, org=1):
        with server.db() as c:
            return service_quota.snapshot(c, org)

    def test_unset_limits_never_block(self):
        with server.db() as c:
            for _ in range(5): self.wa(c)
            c.execute("INSERT INTO call_logs(organization_id,caller_phone,direction,status,started_at,duration_seconds,created_at) VALUES(1,'x','inbound','done',?,?,?)", (ago(), 6000, ago()))
            c.commit()
        state = self.state()
        for service in ('whatsapp', 'calls'):
            self.assertTrue(state['services'][service]['unlimited'])
            self.assertFalse(state['services'][service]['blocked'])
        self.assertEqual(state['services']['whatsapp']['used'], 5)
        self.assertEqual(state['services']['calls']['used'], 100)
        with server.db() as c:
            service_quota.check(c, 1, 'whatsapp'); service_quota.check(c, 1, 'calls')

    def test_whatsapp_counts_outbound_in_cycle_only_and_adjustment_is_real(self):
        self.package(whatsapp_units=2)
        with server.db() as c:
            self.wa(c); self.wa(c, days=2)
            self.wa(c, direction='inbound'); self.wa(c, state='failed'); self.wa(c, days=5); self.wa(c, org=2)
            c.commit()
        item = self.state()['services']['whatsapp']
        self.assertEqual((item['used'], item['limit'], item['remaining'], item['blocked'], item['reason']), (2, 2, 0, True, 'monthly'))
        self.assertFalse(self.state(2)['services']['whatsapp']['blocked'])
        with server.db() as c:
            with self.assertRaises(service_quota.Exhausted) as caught: service_quota.check(c, 1, 'whatsapp')
        self.assertIn(self.state()['renews_at'], caught.exception.message)
        self.req('/owner/api/v2/credits/1', 'POST', {'adjustments': {'whatsapp': 3}, 'reason': 'تعويض'}, owner=True)
        item = self.state()['services']['whatsapp']
        self.assertEqual((item['limit'], item['remaining'], item['blocked']), (5, 3, False))
        self.req('/owner/api/v2/credits/1', 'POST', {'adjustments': {'whatsapp': -4}, 'reason': 'تخفيض'}, owner=True)
        item = self.state()['services']['whatsapp']
        self.assertEqual((item['limit'], item['blocked']), (1, True))
        # تعديل من دورة سابقة لا يُحسب.
        with server.db() as c:
            c.execute("UPDATE platform_credit_ledger SET created_at=?", (ago(days=10),)); c.commit()
        self.assertEqual(self.state()['services']['whatsapp']['limit'], 2)

    def test_whatsapp_send_stops_at_limit_but_retry_of_sent_message_is_allowed(self):
        self.package(whatsapp_units=1)
        cfg = {'phone_number_id': 'pn1', 'token': 't', 'branch_id': None}
        with server.db() as c:
            self.wa(c, direction='inbound'); c.commit()
        with patch.object(whatsapp_bridge, 'config', return_value=cfg), patch.object(whatsapp_bridge, 'graph', return_value={'messages': [{'id': 'meta-1'}]}):
            with server.db() as c:
                sent = whatsapp_bridge.send(c, 1, {'to': '966500000001', 'message': 'أهلًا', 'clientMessageId': 'client-0001'})
                self.assertEqual(sent['state'], 'accepted')
                again = whatsapp_bridge.send(c, 1, {'to': '966500000001', 'message': 'أهلًا', 'clientMessageId': 'client-0001'})
                self.assertEqual(again['id'], sent['id'])
                with self.assertRaises(whatsapp_bridge.Error) as caught:
                    whatsapp_bridge.send(c, 1, {'to': '966500000001', 'message': 'ثانية', 'clientMessageId': 'client-0002'})
                self.assertEqual(caught.exception.status, 429)
                self.assertEqual(c.execute("SELECT COUNT(*) n FROM whatsapp_messages WHERE direction='outbound'").fetchone()['n'], 1)

    def test_ai_daily_cap_monthly_limit_and_extra_credit(self):
        self.package(ai_daily=2, ai_monthly=3)
        with server.db() as c:
            self.ai(c, count=2); c.commit()
            with self.assertRaises(server.ApiError) as caught: server.ai_allowance(c, 1)
            self.assertEqual((caught.exception.status, caught.exception.message), (429, service_quota.DAILY_MESSAGE))
            self.assertEqual(server.ai_allowance(c, 1, enforce=False), ('vip', 2, 2))
        item = self.state()['services']['ai']
        self.assertEqual((item['used'], item['limit'], item['used_today'], item['daily_limit'], item['reason']), (2, 3, 2, 2, 'daily'))
        # أمس 2 واليوم 1: السقف اليومي لم يُبلغ لكن رصيد الشهر انتهى.
        with server.db() as c:
            c.execute('DELETE FROM ai_usage'); self.ai(c, days=1, count=2); self.ai(c); c.commit()
            with self.assertRaises(server.ApiError) as caught: server.ai_allowance(c, 1)
            self.assertIn('انتهى رصيد', caught.exception.message)
            package, limit, used = server.ai_allowance(c, 1, enforce=False)
            self.assertEqual((limit, used), (1, 1))
        self.assertEqual(self.state()['services']['ai']['reason'], 'monthly')
        # رصيد إضافي من الإدارة يرفع الشهري واليومي معًا فيُستخدم فورًا.
        self.req('/owner/api/v2/credits/1', 'POST', {'adjustments': {'ai': 5}, 'reason': 'تعويض'}, owner=True)
        item = self.state()['services']['ai']
        self.assertEqual((item['limit'], item['daily_limit'], item['blocked'], item['remaining']), (8, 7, False, 5))
        with server.db() as c:
            self.assertEqual(server.ai_allowance(c, 1), ('vip', 6, 1))
        # الرصيد الإضافي لمرة واحدة: ما صُرف منه أمس فوق السقف اليومي يُخصم من سقف اليوم.
        with server.db() as c:
            c.execute('DELETE FROM ai_usage'); self.ai(c, days=1, count=5); c.commit()
        self.assertEqual(self.state()['services']['ai']['daily_limit'], 2 + 5 - 3)
        # استهلاك قبل بداية الدورة لا يُحسب.
        with server.db() as c:
            c.execute('DELETE FROM ai_usage'); c.execute('DELETE FROM platform_credit_ledger'); self.ai(c, days=6, count=9); c.commit()
        self.assertEqual(self.state()['services']['ai']['used'], 0)

    def test_ai_without_monthly_limit_behaves_like_the_daily_limit_only(self):
        self.package(ai_daily=4, ai_monthly=None)
        state = self.state()
        days = (date.fromisoformat(state['renews_at']) - date.fromisoformat(state['cycle_start'])).days
        self.assertEqual(state['services']['ai']['limit'], 4 * days)
        # بلا حد شهري محدد: استهلاك سابق أكبر من الحد المعروض لا يوقف المؤسسة؛ السقف اليومي وحده يحكم.
        with server.db() as c:
            self.ai(c, days=1, count=4 * days + 5); self.ai(c, count=1); c.commit()
            self.assertEqual(server.ai_allowance(c, 1), ('vip', 4, 1))
            c.execute('DELETE FROM ai_usage'); c.commit()
        # وزيادة يدوية بلا حد شهري = رصيد لمرة واحدة فوق السقف اليومي، لا زيادة يومية دائمة.
        self.req('/owner/api/v2/credits/1', 'POST', {'adjustments': {'ai': 6}, 'reason': 'تعويض'}, owner=True)
        self.assertEqual(self.state()['services']['ai']['daily_limit'], 10)
        with server.db() as c:
            self.ai(c, days=1, count=8); c.commit()
        self.assertEqual(self.state()['services']['ai']['daily_limit'], 4 + 6 - 4)
        with server.db() as c:
            self.ai(c, days=2, count=9); c.commit()
        self.assertEqual(self.state()['services']['ai']['daily_limit'], 4)
        with server.db() as c:
            c.execute('DELETE FROM ai_usage'); c.execute('DELETE FROM platform_credit_ledger'); c.commit()
        with server.db() as c:
            c.execute("INSERT INTO ai_limits(organization_id,daily_limit) VALUES(1,9)"); c.commit()
        self.package(ai_monthly=10)
        item = self.state()['services']['ai']
        self.assertEqual((item['daily_limit'], item['limit']), (9, 9 * days))
        # مكافأة AI ليوم واحد ترفع سقف اليوم والشهر.
        self.req('/owner/api/v2/organizations/2/reward', 'POST', {'kind': 'ai', 'amount': 6, 'reason': 'مكافأة'}, owner=True)
        self.package(ai_daily=4, ai_monthly=20)
        item = self.state(2)['services']['ai']
        self.assertEqual((item['daily_limit'], item['limit']), (10, 26))

    def test_outbound_call_stops_when_minutes_are_used(self):
        self.package(calls_units=2)
        with server.db() as c:
            c.execute("INSERT INTO call_connections(organization_id,phone_number,activity,enabled,status,last_error,updated_at) VALUES(1,'966500000009','',1,'ready','',?)", (ago(),))
            c.execute("INSERT INTO call_logs(organization_id,caller_phone,direction,status,started_at,duration_seconds,created_at) VALUES(1,'x','inbound','done',?,?,?)", (ago(), 61, ago()))
            c.commit()
        self.assertEqual(self.state()['services']['calls']['used'], 2)
        self.fails(429, '/api/calls/outbound', 'POST', {'phone': '966511111111'})
        self.req('/owner/api/v2/credits/1', 'POST', {'adjustments': {'calls': 1}, 'reason': 'تعويض'}, owner=True)
        self.assertTrue(self.req('/api/calls/outbound', 'POST', {'phone': '966511111111'})['queued'])

    def test_dashboard_and_app_show_the_enforced_numbers(self):
        self.package(whatsapp_units=10, ai_daily=5, ai_monthly=50)
        with server.db() as c:
            self.wa(c); self.wa(c); self.wa(c, direction='inbound'); self.ai(c, count=3); c.commit()
        item = next(x for x in self.req('/owner/api/v2/credits', owner=True)['items'] if x['id'] == 1)['credits']
        self.assertEqual((item['services']['whatsapp']['used'], item['services']['whatsapp']['limit'], item['services']['whatsapp']['remaining']), (2, 10, 8))
        self.assertEqual((item['services']['ai']['used'], item['services']['ai']['limit'], item['services']['ai']['daily_limit']), (3, 50, 5))
        self.assertTrue(item['services']['calls']['unlimited'])
        self.assertEqual(item['renews_at'], self.state()['renews_at'])
        app = self.req('/api/usage-summary')
        self.assertEqual(app['renewalDate'], self.state()['renews_at'])
        self.assertEqual((app['services']['whatsapp']['limit'], app['services']['whatsapp']['usedMonth'], app['services']['whatsapp']['remaining']), (10, 2, 8))
        self.assertEqual((app['services']['ai']['limit'], app['services']['ai']['usedMonth'], app['services']['ai']['exhausted']), (50, 3, False))
        self.assertTrue(app['services']['calls']['unlimited'])

    def test_quota_works_before_the_monthly_column_exists_and_with_odd_call_times(self):
        with server.db() as c:
            c.execute('ALTER TABLE platform_packages DROP COLUMN ai_monthly'); c.commit()
            item = service_quota.snapshot(c, 1)['services']['ai']
            self.assertEqual((item['monthly_enforced'], item['blocked']), (False, False))
            server.ai_allowance(c, 1)
            c.execute('ALTER TABLE platform_packages ADD COLUMN ai_monthly INTEGER'); c.commit()
        self.package(calls_units=5)
        with server.db() as c:
            for started, seconds in (('zzz', 120), ('9999-01-01', 60), (ago(), -500)):
                c.execute("INSERT INTO call_logs(organization_id,caller_phone,direction,status,started_at,duration_seconds,created_at) VALUES(1,'x','inbound','done',?,?,?)", (started, seconds, ago()))
            c.execute("INSERT INTO call_logs(organization_id,caller_phone,direction,status,started_at,duration_seconds,created_at) VALUES(1,'x','inbound','done','zzz',600,?)", (ago(days=9),))
            c.commit()
        self.assertEqual(self.state()['services']['calls']['used'], 3)

    def test_package_settings_store_the_monthly_ai_limit(self):
        self.req('/owner/api/v2/packages', 'PUT', {'package': 'vip', 'ai_daily': 20, 'ai_monthly': 300, 'whatsapp_units': 200}, owner=True)
        saved = next(x for x in self.req('/owner/api/v2/packages', owner=True) if x['package'] == 'vip')['service_limits']
        self.assertEqual((saved['ai_daily'], saved['ai_monthly'], saved['whatsapp_units'], saved['calls_units']), (20, 300, 200, None))
        self.fails(400, '/owner/api/v2/packages', 'PUT', {'package': 'vip', 'ai_daily': 20, 'ai_monthly': 5}, owner=True)
        self.req('/owner/api/v2/packages', 'PUT', {'package': 'vip', 'ai_monthly': None}, owner=True)
        saved = next(x for x in self.req('/owner/api/v2/packages', owner=True) if x['package'] == 'vip')['service_limits']
        self.assertIsNone(saved['ai_monthly'])

    def test_invoice_sets_real_unit_cost_and_profit_uses_paid_amount(self):
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        with server.db() as c:
            self.ai(c, count=3); self.ai(c, org=2, count=1)
            c.execute("INSERT INTO subscription_requests(organization_id,requested_package,paid_months,bonus_months,quoted_price,status,created_at,processed_at) VALUES(1,'vip',10,2,420,'approved',?,?)", (ago(days=4), ago(days=3)))
            c.execute("UPDATE subscriptions SET package='free' WHERE organization_id=2")
            c.commit()
        before = self.req('/owner/api/v2/credits', owner=True)
        self.assertEqual(before['costs']['ai']['source'], 'estimate')
        one = next(x for x in before['items'] if x['id'] == 1)['credits']
        self.assertEqual((one['subscription_value'], one['income_source']), (35.0, 'paid'))
        two = next(x for x in before['items'] if x['id'] == 2)['credits']
        self.assertEqual((two['subscription_value'], two['income_source']), (0.0, 'free'))
        saved = self.req('/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': month, 'amount': 2, 'currency': 'usd'}, owner=True)
        self.assertEqual((saved['amount_sar'], saved['units'], saved['unit_cost']), (7.5, 4, 1.875))
        after = self.req('/owner/api/v2/credits', owner=True)
        one = next(x for x in after['items'] if x['id'] == 1)['credits']
        two = next(x for x in after['items'] if x['id'] == 2)['credits']
        self.assertEqual((one['services']['ai']['cost'], one['actual_cost'], one['estimated_profit']), (5.62, 5.62, 29.38))
        self.assertEqual((two['actual_cost'], two['estimated_profit']), (1.88, -1.88))
        self.assertEqual(after['costs']['ai']['source'], 'invoice')
        totals = after['totals']
        self.assertEqual((totals['income'], totals['cost'], totals['profit'], totals['losing']), (35.0, 7.5, 27.5, 1))
        listing = self.req('/owner/api/v2/service-costs', owner=True)
        self.assertEqual([(x['month'], x['service']) for x in listing['invoices']], [(month, 'ai')])
        self.assertEqual(listing['usage'][month]['ai'], 4)
        # تعديل نفس الشهر يستبدل الفاتورة، ولا فاتورة بلا استخدام أو لشهر قادم أو بمبلغ صفر.
        self.assertEqual(self.req('/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': month, 'amount': 8}, owner=True)['unit_cost'], 2.0)
        self.fails(400, '/owner/api/v2/service-costs', 'POST', {'service': 'whatsapp', 'month': month, 'amount': 8}, owner=True)
        self.fails(400, '/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': '2999-01', 'amount': 8}, owner=True)
        self.fails(400, '/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': month, 'amount': 0}, owner=True)
        self.fails(400, '/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': '2026-13', 'amount': 5}, owner=True)
        self.fails(401, '/owner/api/v2/service-costs')
        self.req('/owner/api/v2/service-costs/' + month + '/ai', 'DELETE', owner=True)
        self.assertEqual(self.req('/owner/api/v2/credits', owner=True)['costs']['ai']['source'], 'estimate')
        self.fails(404, '/owner/api/v2/service-costs/' + month + '/ai', 'DELETE', owner=True)
        self.fails(400, '/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': '9999-12', 'amount': 5}, owner=True)
        arabic = month.translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))
        self.assertEqual(self.req('/owner/api/v2/service-costs', 'POST', {'service': 'ai', 'month': arabic, 'amount': 4}, owner=True)['month'], month)
        # اشتراك مدفوع أقدم من بداية الاشتراك الحالي لا يُحسب دخلًا؛ يُستخدم سعر الباقة.
        with server.db() as c:
            c.execute("UPDATE subscription_requests SET processed_at=?", (ago(days=40),)); c.commit()
        one = next(x for x in self.req('/owner/api/v2/credits', owner=True)['items'] if x['id'] == 1)['credits']
        self.assertEqual(one['income_source'], 'package')

    def test_complaint_alerts_the_platform_not_the_subscriber(self):
        created = self.req('/api/support-tickets', 'POST', {'category': 'مشكلة في الحساب', 'message': 'صفحة إنشاء إعلان فيها شريطان'})
        self.assertTrue(created['referenceCode'].startswith('KHD-'))
        logs = self.req('/api/audit-logs')
        self.assertFalse(any(x['action'] == 'support_request' for x in logs))
        sent = [x for x in logs if x['action'] == 'support_ticket_sent']
        self.assertEqual(len(sent), 1)
        self.assertIn(created['referenceCode'], sent[0]['summary'])
        self.assertNotEqual(sent[0]['target_type'], 'security')
        # سجل قديم بالاسم السابق لا يرجع تنبيهًا أمنيًا.
        with server.db() as c:
            server.audit_log(c, 1, 1, 'support_request', 'تم إرسال طلب دعم فني: قديم', 'security', '99'); c.commit()
        self.assertFalse(any(x['action'] == 'support_request' for x in self.req('/api/audit-logs')))
        # إدارة خدووم: أول فتح يعرض شكاوى آخر 48 ساعة برقمها وتاريخها واسم المؤسسة.
        first = self.req('/owner/api/v2/support/new', owner=True)
        self.assertEqual((first['count'], first['latest_id']), (1, created['id']))
        item = first['items'][0]
        self.assertEqual((item['reference_code'], item['organization_name']), (created['referenceCode'], 'Institution 1'))
        self.assertTrue(item['created_at'])
        self.assertEqual(self.req('/owner/api/v2/support/new?after=%d' % created['id'], owner=True)['count'], 0)
        second = self.req('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'شكوى ثانية'}, user=3)
        after = self.req('/owner/api/v2/support/new?after=%d' % created['id'], owner=True)
        self.assertEqual((after['count'], after['items'][0]['reference_code'], after['items'][0]['organization_name']), (1, second['referenceCode'], 'Institution 2'))
        self.fails(401, '/owner/api/v2/support/new')
        # المشترك لا يصل إلى مسار الإدارة.
        self.fails(404, '/api/support/new')


if __name__ == '__main__':
    unittest.main()
