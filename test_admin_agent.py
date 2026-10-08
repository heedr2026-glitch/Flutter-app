"""موظف الإدارة: القراءة تنفذ مباشرة، والتعديلات لا تنفذ إلا بتأكيد صريح من المدير."""
import json
import unittest
from urllib.request import urlopen

import admin_agent
import ai_core
import server
from unittest.mock import patch
from test_service_number_claims import ServiceNumberClaimsTest


class AdminAgentTest(ServiceNumberClaimsTest):
    def tool(self, name, arguments=None, user_text=''):
        return self.call('/owner/api/agent/tool', 'POST', {'name': name, 'arguments': json.dumps(arguments or {}), 'userText': user_text})[1]

    def test_read_tools(self):
        found = self.tool('find_organization', {'name': 'مؤسسة'})
        self.assertEqual(found['count'], 2)
        status = self.tool('organization_status', {'organization_id': 1})
        self.assertEqual((status['name'], status['package'], status['users']), ('مؤسسة 1', 'VIP', 1))
        self.assertIn('error', self.tool('organization_status', {'organization_id': 999}))
        self.assertIn('error', self.tool('unknown_tool'))
        # المشترك ما يقدر يستخدم موظف الإدارة.
        self.assertEqual(self.call('/owner/api/agent/tool', 'POST', {'name': 'find_organization', 'arguments': '{}'}, user=1)[0], 401)

    def test_counts_users_and_password_resets(self):
        stats = self.tool('platform_stats')
        self.assertEqual((stats['organizations'], stats['by_package']['VIP'], stats['users']), (2, 2, 2))
        self.assertEqual(stats['new_last_7_days'], 2)
        with server.db() as c:
            c.execute("INSERT INTO users(id,organization_id,name,username,email,phone,password_hash,password_salt,role,created_at) VALUES(5,1,'سالم','salem','s@example.com','','x','x','employee',?)", (server.now(),))
            server.audit_log(c, 1, 1, 'login', 'دخول', 'security', '1')
            server.audit_log(c, 1, 5, 'failed_login', 'فشل', 'security', '5')
            server.audit_log(c, 1, 1, 'password_reset', 'إعادة تعيين كلمة مرور الموظف سالم', 'security', 5)
            server.audit_log(c, 1, 1, 'password_reset', 'إعادة تعيين كلمة مرور الموظف سالم', 'security', 5)
            server.audit_log(c, 2, 2, 'password_reset', 'مؤسسة ثانية', 'security', 2)
            c.commit()
        users = self.tool('organization_users', {'organization_id': 1})
        self.assertEqual(users['count'], 2)
        by_name = {u['username']: u for u in users['users']}
        self.assertTrue(by_name['user1']['last_login'])
        self.assertEqual(by_name['salem']['failed_logins_30_days'], 1)
        resets = self.tool('password_resets', {'organization_id': 1})
        self.assertEqual((resets['count'], resets['recent'][0]['for']), (2, 'salem'))
        self.assertEqual(self.tool('password_resets', {'organization_id': 1, 'username': 'user1'})['count'], 0)
        self.assertEqual(self.tool('password_resets', {'organization_id': 1, 'username': 'salem'})['count'], 2)
        self.assertIn('error', self.tool('password_resets', {'organization_id': 1, 'username': 'nobody'}))

    def test_daily_brief_and_expiring(self):
        from datetime import datetime, timedelta, timezone
        soon = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        with server.db() as c:
            c.execute('UPDATE subscriptions SET expires_at=? WHERE organization_id=1', (soon,))
            c.execute("UPDATE organizations SET phone='0501234567' WHERE id=1")
            c.commit()
        expiring = self.call('/owner/api/agent/expiring?days=14')[1]
        self.assertEqual([x['organization_id'] for x in expiring['items']], [1])
        self.assertEqual((expiring['items'][0]['days_left'] in (4, 5), expiring['items'][0]['whatsapp']), (True, '966501234567'))
        brief = self.call('/owner/api/agent/brief')[1]
        self.assertEqual(brief['organizations_total'], 2)
        self.assertEqual(len(brief['expiring_in_7_days']), 1)
        self.assertIn('open_complaints', brief)
        self.assertEqual(self.call('/owner/api/agent/brief', user=1)[0], 401)
        self.assertNotIn('error', self.tool('daily_brief'))

    def test_agent_addresses_admin_as_khadoum(self):
        self.assertIn('يا خدوم', admin_agent.INSTRUCTIONS)
        sent = []
        with patch.object(ai_core.ResponsesClient, '_http', lambda self, payload: sent.append(payload) or {'output_text': 'هلا يا خدوم'}):
            self.call('/owner/api/agent/chat', 'POST', {'message': 'هلا', 'history': []})
        self.assertNotIn('المالك', sent[0]['instructions'])

    def test_complaint_details_by_reference(self):
        status, saved = self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'الصفحة ما تفتح عندي'}, user=1)
        self.assertEqual(status, 201)
        details = self.tool('complaint_details', {'reference': saved['referenceCode']})
        self.assertEqual(details['ticket']['id'], saved['id'])
        self.assertEqual(details['ticket']['organization_name'], 'مؤسسة 1')
        self.assertEqual(self.tool('complaint_details', {'reference': str(saved['id'])})['ticket']['id'], saved['id'])
        self.assertIn('error', self.tool('complaint_details', {'reference': 'KHD-2026-999999'}))
        recheck = self.tool('recheck_complaint', {'reference': saved['referenceCode']})
        self.assertNotIn('error', recheck)
        self.assertTrue(recheck)
        self.assertNotIn('error', self.tool('platform_status'))

    def test_changes_need_explicit_confirmation(self):
        proposal = self.tool('propose_update_organization', {'organization_id': 1, 'name': 'مراتك للزجاج', 'activity': 'زجاج'})
        self.assertIn('مراتك للزجاج', proposal['summary'])
        # بدون كلمة «أكد» لا يتنفذ شي.
        self.assertIn('error', self.tool('confirm_action', {'token': proposal['token']}, user_text='تمام'))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT name FROM organizations WHERE id=1').fetchone()['name'], 'مؤسسة 1')
        done = self.tool('confirm_action', {'token': proposal['token']}, user_text='أكد')
        self.assertTrue(done['done'])
        with server.db() as c:
            row = c.execute('SELECT name,activity FROM organizations WHERE id=1').fetchone()
            self.assertEqual((row['name'], row['activity']), ('مراتك للزجاج', 'زجاج'))
            self.assertTrue(c.execute("SELECT 1 FROM platform_audit WHERE action='agent_organization_updated'").fetchone())
        # نفس الطلب ما يتنفذ مرتين.
        self.assertIn('error', self.tool('confirm_action', {'token': proposal['token']}, user_text='أكد'))
        self.assertIn('error', self.tool('propose_update_organization', {'organization_id': 1}))

    def test_subscription_extend_and_change(self):
        with server.db() as c:
            before = c.execute('SELECT expires_at FROM subscriptions WHERE organization_id=2').fetchone()['expires_at']
        extend = self.tool('propose_subscription', {'organization_id': 2, 'add_days': 30})
        self.assertIn('تمديد', extend['summary'])
        # التأكيد من زر الصفحة.
        self.assertTrue(self.call('/owner/api/agent/confirm', 'POST', {'token': extend['token']})[1]['done'])
        with server.db() as c:
            after = c.execute('SELECT package,expires_at FROM subscriptions WHERE organization_id=2').fetchone()
        self.assertEqual(after['package'], 'vip')
        self.assertEqual((admin_agent._parse(after['expires_at']) - admin_agent._parse(before)).days, 30)
        change = self.tool('propose_subscription', {'organization_id': 2, 'package': 'basic'})
        self.assertIn('الأساسية', change['summary'])
        self.tool('confirm_action', {'token': change['token']}, user_text='اكد')
        with server.db() as c:
            self.assertEqual(c.execute('SELECT package FROM subscriptions WHERE organization_id=2').fetchone()['package'], 'basic')
        self.assertIn('error', self.tool('propose_subscription', {'organization_id': 2}))
        self.assertIn('error', self.tool('propose_subscription', {'organization_id': 2, 'package': 'gold'}))

    def test_offer_requires_prices_and_creates_bonus_offer(self):
        self.assertIn('error', self.tool('propose_offer', {'package': 'basic', 'paid_months': 5, 'offer_type': 'bonus', 'bonus_months': 1}))
        with server.db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS package_prices(package TEXT, duration_months INTEGER, price_sar REAL)")
            c.execute("DELETE FROM package_prices")
            c.execute("INSERT INTO package_prices(package,duration_months,price_sar,updated_at) VALUES('basic',6,300,?)", (server.now(),))
            c.commit()
        self.assertIn('error', self.tool('propose_offer', {'package': 'vip', 'paid_months': 6, 'offer_type': 'bonus', 'bonus_months': 1}))
        proposal = self.tool('propose_offer', {'package': 'basic', 'paid_months': 6, 'offer_type': 'bonus', 'bonus_months': 1})
        self.assertIn('6 شهر', proposal['summary'])
        self.assertTrue(self.tool('confirm_action', {'token': proposal['token']}, user_text='أكد')['done'])
        with server.db() as c:
            offer = c.execute('SELECT package,paid_months,bonus_months,price_sar,offer_type,active FROM package_offers ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(tuple(offer), ('basic', 6, 1, 300.0, 'bonus', 1))
        percent = self.tool('propose_offer', {'package': 'basic', 'paid_months': 6, 'offer_type': 'percent', 'discount_percent': 20, 'days_valid': 7})
        self.assertIn('من 300 إلى 240 ريال', percent['summary'])
        self.assertTrue(self.tool('confirm_action', {'token': percent['token']}, user_text='أكد')['done'])
        self.assertIn('error', self.tool('propose_offer', {'package': 'basic', 'paid_months': 6, 'offer_type': 'price', 'price_sar': 350}))
        special = self.tool('propose_offer', {'package': 'basic', 'paid_months': 6, 'offer_type': 'price', 'price_sar': 199})
        self.assertTrue(self.tool('confirm_action', {'token': special['token']}, user_text='أكد')['done'])
        with server.db() as c:
            rows = [tuple(r) for r in c.execute('SELECT offer_type,price_sar,discount_percent FROM package_offers ORDER BY id DESC LIMIT 2').fetchall()]
        self.assertEqual(rows, [('price', 199.0, 0), ('percent', 240.0, 20.0)])
        # كود خصم.
        self.assertIn('error', self.tool('propose_discount_code', {'code': 'خصم', 'discount_percent': 10}))
        self.assertIn('error', self.tool('propose_discount_code', {'code': 'KH10', 'discount_percent': 10, 'discount_amount': 5}))
        code = self.tool('propose_discount_code', {'code': 'kh20', 'discount_percent': 20, 'packages': 'vip', 'max_uses': 50})
        self.assertIn('KH20', code['summary'])
        self.assertTrue(self.tool('confirm_action', {'token': code['token']}, user_text='أكد')['done'])
        with server.db() as c:
            saved = c.execute("SELECT discount_percent,eligible_packages,max_uses,code_kind FROM activation_codes WHERE code_prefix='KH20'").fetchone()
        self.assertEqual(tuple(saved), (20.0, 'vip', 50, 'discount'))
        self.assertIn('موجود', self.tool('propose_discount_code', {'code': 'KH20', 'discount_amount': 30})['error'])
        listing = self.tool('current_offers')
        self.assertEqual(listing['package_prices']['الأساسية']['6 شهر'], 300.0)
        self.assertEqual(listing['running_offers'], 3)
        self.assertEqual([o['type'] for o in listing['offers']][:3], ['سعر خاص', 'نسبة خصم', 'أشهر مجانية'])
        self.assertEqual((listing['discount_codes'][0]['code'], listing['discount_codes'][0]['discount'], listing['discount_codes'][0]['used'], listing['discount_codes'][0]['status']), ('KH20', '20%', '0 من 50', 'شغال'))

    def test_ad_decision(self):
        with server.db() as c:
            c.execute("INSERT INTO advertisements(organization_id,title,message,contact,active,approved,created_at,requested_days) VALUES(1,'خصم الزجاج','تفاصيل','050',1,0,?,5)", (server.now(),))
            c.commit()
            ad_id = c.execute('SELECT id FROM advertisements ORDER BY id DESC LIMIT 1').fetchone()['id']
        pending = self.tool('pending_ads')
        self.assertEqual([x['ad_id'] for x in pending['items']], [ad_id])
        proposal = self.tool('propose_ad_decision', {'ad_id': ad_id, 'decision': 'approve'})
        self.assertIn('5 يوم', proposal['summary'])
        self.assertTrue(self.tool('confirm_action', {'token': proposal['token']}, user_text='أكد')['done'])
        with server.db() as c:
            ad = c.execute('SELECT approved,active,expires_at FROM advertisements WHERE id=?', (ad_id,)).fetchone()
        self.assertEqual((ad['approved'], ad['active']), (1, 1))
        self.assertTrue(ad['expires_at'])
        self.assertEqual(self.tool('pending_ads')['count'], 0)

    def test_text_chat_runs_tools_and_returns_pending(self):
        replies = [
            {'output': [{'type': 'function_call', 'call_id': 'c1', 'name': 'propose_update_organization', 'arguments': json.dumps({'organization_id': 1, 'phone': '0555555555'})}]},
            {'output_text': 'جهزت التعديل. أأكد؟'},
        ]
        with patch.object(ai_core.ResponsesClient, '_http', lambda self, payload: replies.pop(0)):
            status, result = self.call('/owner/api/agent/chat', 'POST', {'message': 'غير رقم مؤسسة 1', 'history': []})
        self.assertEqual(status, 200)
        self.assertEqual(result['answer'], 'جهزت التعديل. أأكد؟')
        self.assertEqual(len(result['pending']), 1)
        self.assertIn('0555555555', result['pending'][0]['summary'])
        # النموذج ما يقدر يأكد بنفسه إذا رسالة المدير ما فيها «أكد».
        replies = [
            {'output': [{'type': 'function_call', 'call_id': 'c2', 'name': 'confirm_action', 'arguments': json.dumps({'token': result['pending'][0]['token']})}]},
            {'output_text': 'ما تنفذ'},
        ]
        with patch.object(ai_core.ResponsesClient, '_http', lambda self, payload: replies.pop(0)):
            self.call('/owner/api/agent/chat', 'POST', {'message': 'وش رأيك؟', 'history': []})
        with server.db() as c:
            self.assertNotEqual(c.execute('SELECT phone FROM organizations WHERE id=1').fetchone()['phone'], '0555555555')
        with urlopen(f'http://127.0.0.1:{self.httpd.server_port}/owner/agent', timeout=10) as page:
            self.assertIn('موظف الإدارة', page.read().decode())

    def test_confirm_words(self):
        for text in ('أكد', 'اكد', 'أكّد', 'نعم أكد التعديل', 'أؤكد'):
            self.assertTrue(admin_agent.confirmed_by(text), text)
        for text in ('تمام', 'لا', '', 'وش رأيك'):
            self.assertFalse(admin_agent.confirmed_by(text), text)





class VoiceSessionTest(unittest.TestCase):
    def test_voice_session_carries_tools_only_for_agent(self):
        import calls_trial
        sent = []
        with patch.object(calls_trial, '_http', lambda p: sent.append(p) or {'value': 'ek'}):
            self.assertEqual(admin_agent.voice_session('المالك')['clientSecret'], 'ek')
            calls_trial.create_session('male')
        self.assertEqual((sent[0]['session']['tool_choice'], len(sent[0]['session']['tools'])), ('auto', len(admin_agent.TOOLS)))
        self.assertNotIn('tools', sent[1]['session'])


del ServiceNumberClaimsTest

if __name__ == '__main__':
    unittest.main()
