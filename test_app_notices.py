"""تنبيهات التطبيق: مجتمع مهم، تذكير تجديد، ملخص يومي، تتبع المركبات، أعطال التطبيق، وتقييم حل الشكوى."""
import json
from datetime import datetime, timedelta, timezone

import app_notices
import server
import vehicle_tracking
from test_service_number_claims import ServiceNumberClaimsTest


class AppNoticesTest(ServiceNumberClaimsTest):
    def notices(self, user=1, after=0):
        status, data = self.call(f'/api/notices?after={after}', user=user)
        self.assertEqual(status, 200)
        return data

    def test_important_community_post_reaches_all_and_renew_reminders(self):
        self.assertEqual(self.notices()['items'], [])
        self.assertEqual(self.call('/owner/api/v2/community/admin-posts', 'POST', {'body': 'منشور عادي'})[0], 200)
        self.assertEqual(self.notices()['latestCommunity'], 0)
        status, out = self.call('/owner/api/v2/community/admin-posts', 'POST', {'body': 'تحديث مهم للجميع', 'important': True})
        self.assertEqual((status, out['notified']), (200, True))
        for user in (1, 2):
            data = self.notices(user)
            self.assertEqual([i['kind'] for i in data['items']], ['community'])
            self.assertGreater(data['latestCommunity'], 0)
        # تذكير التجديد: قبل 3 أيام من الانتهاء، مرة وحدة فقط.
        ends = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        with server.db() as c:
            c.execute("UPDATE subscriptions SET package='basic',expires_at=? WHERE organization_id=1", (ends,))
            self.assertEqual(app_notices.renew_reminders(c), 1)
            self.assertEqual(app_notices.renew_reminders(c), 0)
            c.commit()
        kinds = [(i['kind'], i['action']) for i in self.notices(1)['items']]
        self.assertIn(('renew', 'renew'), kinds)
        self.assertNotIn('renew', [i['kind'] for i in self.notices(2)['items']])
        # زر «أرسل تذكير» من الإدارة.
        self.assertEqual(self.call('/owner/api/notices/renew/2', 'POST', {})[0], 200)
        self.assertIn('renew', [i['kind'] for i in self.notices(2)['items']])
        self.assertEqual(self.call('/owner/api/notices/renew/2', 'POST', {}, user=1)[0], 401)

    def test_daily_summary_once_per_day_after_nine(self):
        late = datetime.now(timezone.utc).replace(hour=19, minute=0)  # 10 الليل بتوقيت السعودية
        with server.db() as c:
            c.execute("INSERT INTO support_tickets(organization_id,user_id,category,message,status,created_at,updated_at) VALUES(1,1,'أخرى','x','open',?,?)", (server.now(), server.now()))
            self.assertEqual(app_notices.daily_summaries(c, late.replace(hour=10)), 0)
            self.assertGreaterEqual(app_notices.daily_summaries(c, late), 1)
            self.assertEqual(app_notices.daily_summaries(c, late), 0)
            c.commit()
        items = [i for i in self.notices(1)['items'] if i['kind'] == 'daily_summary']
        self.assertEqual(len(items), 1)
        self.assertIn('شكوى', items[0]['body'])

    def test_tracking_alerts_and_silence(self):
        with server.db() as c:
            c.execute("""INSERT INTO vehicle_tracking_schedules(organization_id,vehicle_key,vehicle_name,driver_user_id,weekdays,start_minute,end_minute,enabled,updated_at,device_hash,driver_name,phone_status,last_heartbeat)
                VALUES(1,'v1','الهايلكس',0,'[1,2,3,4,5,6,7]',0,1439,1,?,'hash','سالم','tracking',?)""", (server.now(), (datetime.now(timezone.utc) - timedelta(minutes=9)).isoformat()))
            self.assertEqual(app_notices.tracking_silence(c, vehicle_tracking.in_schedule), 1)
            self.assertEqual(app_notices.tracking_silence(c, vehicle_tracking.in_schedule), 0)
            app_notices.tracking_status_changed(c, 1, 'v1', 'الهايلكس', 'سالم', 'silent', 'tracking')
            app_notices.tracking_status_changed(c, 1, 'v1', 'الهايلكس', 'سالم', 'tracking', 'location_disabled')
            c.commit()
        titles = [i['title'] for i in self.notices(1)['items'] if i['kind'] == 'tracking']
        self.assertEqual(len(titles), 3)
        self.assertIn('رجع تتبع الهايلكس ✅', titles)
        self.assertTrue(any('متوقف' in t for t in titles))

    def test_app_errors_and_ticket_rating(self):
        body = {'message': 'Null check operator used on a null value', 'stack': '#0 main', 'appVersion': '1.0.0+4'}
        self.assertEqual(self.call('/api/app-errors', 'POST', body, user=1)[1]['saved'], True)
        self.assertEqual(self.call('/api/app-errors', 'POST', body, user=1)[1]['saved'], False)
        status, errors = self.call('/owner/api/app-errors')
        self.assertEqual((status, errors['total'], errors['items'][0]['n']), (200, 1, 1))
        # تقييم الحل.
        ticket = self.call('/api/support-tickets', 'POST', {'category': 'أخرى', 'message': 'الصفحة بطيئة'}, user=1)[1]
        self.assertEqual(self.call(f"/api/support-tickets/{ticket['id']}/rating", 'POST', {'solved': True}, user=1)[0], 400)
        with server.db() as c:
            c.execute("UPDATE support_tickets SET status='resolved' WHERE id=?", (ticket['id'],)); c.commit()
        self.assertEqual(self.call(f"/api/support-tickets/{ticket['id']}/rating", 'POST', {'solved': False}, user=2)[0], 404)
        status, out = self.call(f"/api/support-tickets/{ticket['id']}/rating", 'POST', {'solved': False}, user=1)
        self.assertEqual((status, out['reopened']), (200, True))
        mine = self.call('/api/support-tickets', user=1)[1][0]
        self.assertEqual((mine['status'], mine['user_rating']), ('open', 'down'))
        # الإدارة تحلها من جديد: يرجع سؤال التقييم.
        self.call(f"/owner/api/v2/support/{ticket['id']}", 'PUT', {'status': 'resolved', 'owner_reply': 'تم الإصلاح'})
        mine = self.call('/api/support-tickets', user=1)[1][0]
        self.assertEqual((mine['status'], mine['user_rating']), ('resolved', ''))
