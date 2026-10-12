"""دفتر الفرص، البحث الأسبوعي، وتذكير المتابعة، ورابط عرض السعر الذكي."""
import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import commercial_leads
import server
from test_service_number_claims import ServiceNumberClaimsTest


class CommercialLeadsTest(ServiceNumberClaimsTest):
    def open_public(self, path, method='GET', body=None):
        req = Request(f'http://127.0.0.1:{self.httpd.server_port}{path}', method=method, headers={'Content-Type': 'application/json'},
                      data=None if body is None else json.dumps(body).encode())
        try:
            with urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except HTTPError as e:
            data = e.read().decode(); e.close()
            return e.code, data

    def test_leads_notebook_followup_and_quote_link(self):
        status, lead = self.call('/api/leads', 'POST', {'name': 'مؤسسة البناء الحديث', 'phone': '0551112222', 'activity': 'مقاولات'}, user=1)
        self.assertEqual((status, lead['duplicate']), (201, False))
        self.assertTrue(self.call('/api/leads', 'POST', {'name': 'مؤسسة البناء الحديث'}, user=1)[1]['duplicate'])
        self.assertEqual(self.call('/api/leads', 'POST', {'name': 'x'}, user=1)[0], 400)
        # المؤسسة 2 مو VIP: الدفتر مقفول عليها.
        with server.db() as c:
            c.execute("UPDATE subscriptions SET package='basic' WHERE organization_id=2"); c.commit()
        self.assertEqual(self.call('/api/leads', user=2)[0], 403)
        self.assertEqual(self.call('/api/quotes', 'POST', {'items': [{'title': 'x', 'price': 1}]}, user=2)[0], 403)
        self.assertEqual(self.call(f"/api/leads/{lead['id']}", 'PUT', {'status': 'contacted'}, user=2)[0], 403)
        out = self.call(f"/api/leads/{lead['id']}", 'PUT', {'status': 'contacted'}, user=1)[1]
        self.assertTrue(out['next_follow_up'])
        self.assertEqual(self.call(f"/api/leads/{lead['id']}", 'PUT', {'status': 'bogus'}, user=1)[0], 400)
        # بعد 3 أيام بدون تغيير: تذكير متابعة مرة وحدة.
        with server.db() as c:
            self.assertEqual(commercial_leads.follow_up_reminders(c, datetime.now(timezone.utc) + timedelta(days=4)), 1)
            self.assertEqual(commercial_leads.follow_up_reminders(c, datetime.now(timezone.utc) + timedelta(days=5)), 0)
            c.commit()
        kinds = [n['kind'] for n in self.call('/api/notices?after=0', user=1)[1]['items']]
        self.assertIn('lead_followup', kinds)
        # رابط عرض السعر.
        self.assertEqual(self.call('/api/quotes', 'POST', {'items': []}, user=1)[0], 400)
        status, quote = self.call('/api/quotes', 'POST', {'leadId': lead['id'], 'items': [{'title': 'واجهة زجاج', 'qty': 2, 'price': 1500}], 'validDays': 10}, user=1)
        self.assertEqual((status, quote['total']), (201, 3000))
        path = '/q/' + quote['token']
        self.assertTrue(quote['url'].endswith(path))
        status, page = self.open_public(path)
        self.assertEqual(status, 200)
        self.assertIn('3000', page)
        self.assertIn('موافق على العرض', page)
        self.open_public(path); self.open_public(path)
        kinds = [n['title'] for n in self.call('/api/notices?after=0', user=1)[1]['items'] if n['kind'] == 'quote']
        self.assertTrue(any('فتح عرض السعر' in t for t in kinds))
        self.assertTrue(any('3 مرات' in t for t in kinds))
        self.assertEqual(self.open_public(path + '/respond', 'POST', {'action': 'question', 'text': ''})[0], 400)
        status, body = self.open_public(path + '/respond', 'POST', {'action': 'accept'})
        self.assertEqual(status, 200)
        leads = self.call('/api/leads', user=1)[1]['items']
        self.assertEqual((leads[0]['status'], leads[0]['quote']['status']), ('won', 'accepted'))
        self.assertEqual(self.open_public('/q/doesnotexist123')[0], 404)

    def test_weekly_watch_adds_new_leads_once(self):
        self.assertEqual(self.call('/api/commercial-watch', 'PUT', {'enabled': True, 'keywords': '', 'city': 'الرياض'}, user=1)[0], 400)
        saved = self.call('/api/commercial-watch', 'PUT', {'enabled': True, 'keywords': 'مقاولين', 'city': 'الرياض', 'watch_type': 'projects'}, user=1)[1]
        self.assertTrue(saved['enabled'])
        fake = json.dumps([{'name': 'مشروع برج النخيل', 'activity': 'مقاولات', 'location': 'الرياض', 'phone': '', 'website': '', 'source': 'https://example.com', 'reason': 'بدأ البناء'},
                           {'name': 'مؤسسة الأفق', 'activity': 'مقاولات', 'location': 'الرياض', 'phone': '0553334444', 'website': '', 'source': 'x', 'reason': 'y'}])
        with server.db() as c:
            due = commercial_leads.due_watches(c)
            self.assertEqual([w['organization_id'] for w in due], [1])  # المؤسسة 1 VIP في بيانات الاختبار
            self.assertEqual(commercial_leads.run_watch(c, due[0], lambda prompt: '```json\n' + fake + '\n```'), 2)
            self.assertEqual(commercial_leads.run_watch(c, due[0], lambda prompt: fake), 0)
            self.assertEqual(commercial_leads.due_watches(c), [])
            c.commit()
        notices = [n for n in self.call('/api/notices?after=0', user=1)[1]['items'] if n['kind'] == 'leads_found']
        self.assertEqual(len(notices), 1)
        self.assertIn('مشاريع جديدة', notices[0]['title'])
        self.assertEqual(len(self.call('/api/leads', user=1)[1]['items']), 2)
