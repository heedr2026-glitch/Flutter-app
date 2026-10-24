import hashlib
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server

_db = server.db


@contextmanager
def test_db():
    connection = _db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class VideoAdsTest(unittest.TestCase):
    def test_full_flow(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), \
             patch.object(server, 'OWNER_KEY_PATH', Path(directory) / 'owner.key'), \
             patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'video-key', 'KHDOOM_VIDEO_DIR': str(Path(directory) / 'videos')}):
            server.init_db()
            with server.db() as c:
                for number, package in ((1, 'vip'), (2, 'basic')):
                    c.execute('INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)', (number, 'مؤسسة ' + str(number), server.now()))
                    c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,?,?)", (number, package, server.now()))
                    c.execute("INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(?,?,?,?,?,?,?,?)", (number, number, 'u', 'vt' + str(number), 'x', 'x', 'admin', server.now()))
                    c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)', (hashlib.sha256(('vt-token-' + str(number)).encode()).hexdigest(), number, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), server.now()))
                c.commit()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = 'http://127.0.0.1:' + str(httpd.server_port)

            def call(path, method='GET', data=None, user=1, owner=False, raw=None, ctype='application/json'):
                headers = {'Content-Type': ctype}
                if owner:
                    headers['X-Owner-Key'] = 'video-key'
                else:
                    headers['Authorization'] = 'Bearer vt-token-' + str(user)
                body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
                try:
                    with urlopen(Request(base + path, data=body, method=method, headers=headers), timeout=10) as r:
                        content = r.read()
                        return r.status, (json.loads(content) if r.headers.get('Content-Type', '').startswith('application/json') else content)
                except HTTPError as e:
                    return e.code, json.loads(e.read() or b'{}')

            try:
                video = b'\x00\x00\x00\x18ftypmp42' + b'x' * 2000
                # VIP: مسودة ← رفع ← إرسال
                status, draft = call('/api/video-ads', 'POST', {'title': 'خصم 20%', 'seconds': 25, 'activity': 'زجاج', 'allowDownload': False})
                self.assertEqual(status, 200, draft)
                too_long = call('/api/video-ads', 'POST', {'title': 'طويل', 'seconds': 45})
                self.assertEqual(too_long[0], 400)
                self.assertEqual(call(draft['uploadUrl'], 'PUT', raw=video, ctype='video/mp4')[0], 200)
                status, sent = call('/api/video-ads/%d/submit' % draft['id'], 'POST', {})
                self.assertEqual(status, 200, sent)
                self.assertEqual(sent['quota']['used'], 1)

                # لوحة الإدارة: طلب جديد يظهر مع الفيديو
                status, listing = call('/owner/api/v2/video-ads?status=pending', owner=True)
                self.assertEqual(status, 200, listing)
                self.assertEqual(listing['counts']['pending'], 1)
                self.assertEqual(listing['items'][0]['package'], 'vip')
                status, content = call('/owner/api/v2/video-ads/%d/file' % draft['id'], owner=True)
                self.assertEqual((status, content), (200, video))
                self.assertEqual(call('/owner/api/v2/video-ads/%d' % draft['id'], 'PUT', {'action': 'approve'}, owner=True)[0], 400)
                self.assertEqual(call('/owner/api/v2/video-ads/%d' % draft['id'], 'PUT', {'action': 'approve', 'duration': '1h'}, owner=True)[0], 200)

                # العروض تعرض الإعلان لكل الباقات، والتنزيل مقفول لغير المعلن
                status, feed = call('/api/video-ads/feed', user=2)
                self.assertEqual([x['id'] for x in feed['items']], [draft['id']])
                self.assertTrue(feed['items'][0]['vip'])
                self.assertEqual(call('/api/video-ads/%d/download' % draft['id'], user=2)[0], 403)
                self.assertEqual(call('/api/video-ads/%d/file' % draft['id'], user=2)[0], 200)
                like = call('/api/video-ads/%d/like' % draft['id'], 'POST', {}, user=2)[1]
                self.assertEqual((like['liked'], like['likes']), (True, 1))
                self.assertEqual(call('/api/video-ads/feed', user=2)[1]['items'][0]['likes'], 1)
                self.assertTrue(call('/api/video-ads/feed', user=2)[1]['items'][0]['liked'])
                self.assertEqual(call('/api/video-ads/%d/like' % draft['id'], 'POST', {}, user=2)[1]['likes'], 0)

                # انتهاء المدة: يختفي ويبقى محفوظًا ويُعاد نشره
                with server.db() as c:
                    c.execute('UPDATE video_ads SET expires_at=? WHERE id=?', ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), draft['id']))
                    c.commit()
                self.assertEqual(call('/api/video-ads/feed', user=2)[1]['items'], [])
                mine = call('/api/video-ads/mine')[1]
                self.assertEqual(mine['items'][0]['status'], 'expired')
                self.assertEqual(call('/api/video-ads/%d/submit' % draft['id'], 'POST', {})[0], 200)

                # الرفض لا ينحسب من العدد
                self.assertEqual(call('/owner/api/v2/video-ads/%d' % draft['id'], 'PUT', {'action': 'reject'}, owner=True)[0], 400)
                self.assertEqual(call('/owner/api/v2/video-ads/%d' % draft['id'], 'PUT', {'action': 'reject', 'note': 'غير واضح'}, owner=True)[0], 200)
                self.assertEqual(call('/api/video-ads/quota')[1]['used'], 1)

                # الأساسية: لازم يدفع، والمالك يأكد التحويل قبل النشر
                status, other = call('/api/video-ads', 'POST', {'title': 'عرض', 'seconds': 10}, user=2)
                call(other['uploadUrl'], 'PUT', raw=video, user=2, ctype='video/mp4')
                status, refused = call('/api/video-ads/%d/submit' % other['id'], 'POST', {}, user=2)
                self.assertEqual(status, 400)
                self.assertIn('VIP', refused['error'])
                self.assertEqual(call('/api/video-ads/%d/submit' % other['id'], 'POST', {'pay': True, 'transferName': 'محمد'}, user=2)[0], 200)
                self.assertEqual(call('/owner/api/v2/video-ads/%d' % other['id'], 'PUT', {'action': 'approve', 'duration': '1d'}, owner=True)[0], 400)
                self.assertEqual(call('/owner/api/v2/video-ads/%d' % other['id'], 'PUT', {'action': 'approve', 'duration': '1d', 'paymentConfirmed': True}, owner=True)[0], 200)

                # المشترك يحذف من النشر، ومؤسسة ثانية ما تقدر تحذف إعلانه
                self.assertEqual(call('/api/video-ads/%d' % other['id'], 'DELETE', user=1)[0], 400)
                self.assertEqual(call('/api/video-ads/%d' % other['id'], 'DELETE', user=2)[0], 200)
                self.assertEqual(call('/api/video-ads/feed')[1]['items'], [])

                # الإعدادات والحد الخاص
                status, cfg = call('/owner/api/v2/video-ads/settings', 'PUT', {'max_seconds': 60, 'quota_vip': 7}, owner=True)
                self.assertEqual((cfg['max_seconds'], cfg['quota_vip']), (60, 7))
                self.assertEqual(call('/owner/api/v2/video-ads/quota/2', 'PUT', {'limit': 3}, owner=True)[0], 200)
                self.assertEqual(call('/api/video-ads/quota', user=2)[1]['limit'], 3)
                self.assertEqual(call('/owner/api/v2/video-ads/quota/2', 'PUT', {'limit': None}, owner=True)[0], 200)
                self.assertEqual(call('/api/video-ads/quota', user=2)[1]['limit'], 0)
                self.assertEqual(call('/api/video-ads', 'POST', {'title': 'دقيقة', 'seconds': 55})[0], 200)
            finally:
                httpd.shutdown()

    def test_trim_and_watermarked_download(self):
        import shutil, subprocess
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg غير مثبت')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), \
             patch.object(server, 'OWNER_KEY_PATH', Path(directory) / 'owner.key'), \
             patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'db', test_db), \
             patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'video-key', 'KHDOOM_VIDEO_DIR': str(Path(directory) / 'videos')}):
            source = Path(directory) / 'long.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x568:rate=15', '-t', '50', '-pix_fmt', 'yuv420p', str(source)], check=True)
            server.init_db()
            with server.db() as c:
                c.execute('INSERT INTO organizations(id,name,created_at) VALUES(1,?,?)', ('مراتك', server.now()))
                c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(1,'vip',?)", (server.now(),))
                c.execute("INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(1,1,'u','tr1','x','x','admin',?)", (server.now(),))
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)', (hashlib.sha256(b'tr-token').hexdigest(), 1, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), server.now()))
                c.commit()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = 'http://127.0.0.1:' + str(httpd.server_port)
            def call(path, method='GET', data=None, raw=None, auth=True):
                headers = {'Content-Type': 'video/mp4' if raw else 'application/json'}
                if auth: headers['Authorization'] = 'Bearer tr-token'
                body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
                try:
                    with urlopen(Request(base + path, data=body, method=method, headers=headers), timeout=120) as r:
                        content = r.read()
                        return r.status, (json.loads(content) if r.headers.get('Content-Type', '').startswith('application/json') else content), r.headers
                except HTTPError as e:
                    return e.code, json.loads(e.read() or b'{}'), e.headers
            try:
                draft = call('/api/video-ads', 'POST', {'title': 'مقصوص', 'seconds': 20, 'overlayText': 'خصم', 'overlayY': 0.7})[1]
                self.assertEqual(call(draft['uploadUrl'] + '?start=10&end=50', 'PUT', raw=source.read_bytes())[0], 400)
                status, up, _ = call(draft['uploadUrl'] + '?start=10&end=30', 'PUT', raw=source.read_bytes())
                self.assertEqual(status, 200, up)
                self.assertLess(abs(up['seconds'] - 20), 2)
                with server.db() as c:
                    row = c.execute('SELECT optimized,file_name FROM video_ads WHERE id=?', (draft['id'],)).fetchone()
                self.assertEqual(row['optimized'], 1)
                self.assertTrue(row['file_name'].endswith('_m.mp4'))
                link = call('/api/video-ads/%d/download-link' % draft['id'], 'POST', {})[1]['url']
                status, content, headers = call(link, auth=False)
                self.assertEqual(status, 200)
                self.assertIn('attachment', headers.get('Content-Disposition', ''))
                self.assertGreater(len(content), 1000)
                self.assertEqual(call(link[:-4] + '0000', auth=False)[0], 403)
                mine = call('/api/video-ads/mine')[1]['items'][0]
                self.assertEqual(mine['overlayY'], 0.7)
            finally:
                httpd.shutdown()


if __name__ == '__main__':
    unittest.main()
