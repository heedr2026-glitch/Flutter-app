"""In-process regressions; no HTTP socket is needed."""
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import ad_policy
import owner_admin

AT = '2026-09-29T12:00:00+00:00'
IMAGE = 'data:image/png;base64,aGVsbG8='

class BannerFeedTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''
        CREATE TABLE organizations(id INTEGER PRIMARY KEY,name TEXT,phone TEXT);
        INSERT INTO organizations VALUES(1,'Test organization','123');
        CREATE TABLE advertisements(id INTEGER PRIMARY KEY,organization_id INTEGER,
          title TEXT,message TEXT DEFAULT '',contact TEXT DEFAULT '',active INTEGER DEFAULT 1,
          approved INTEGER DEFAULT 1,approved_at TEXT,expires_at TEXT,scheduled_at TEXT,
          image_data TEXT DEFAULT '',display_seconds INTEGER DEFAULT 8,banner_config TEXT,
          published_at TEXT,published_by TEXT,review_note TEXT,deleted INTEGER DEFAULT 0);
        CREATE TABLE platform_advertisements(id INTEGER PRIMARY KEY,title TEXT,message TEXT,
          promo_code TEXT,image_data TEXT,active INTEGER,starts_at TEXT,expires_at TEXT,
          display_seconds INTEGER,banner_config TEXT,published_at TEXT);
        CREATE TABLE platform_audit(actor TEXT,action TEXT,target TEXT,created_at TEXT);
        ''')
    def tearDown(self):
        self.c.close()
    def org(self, ident, **changes):
        data = dict(id=ident,organization_id=1,title='Ad',approved_at=AT,
                    banner_config=json.dumps({'adType':'image','bannerImageData':IMAGE}),
                    scheduled_at='2026-09-01T00:00:00+00:00',
                    expires_at='2026-10-01T00:00:00+00:00',published_at=AT,published_by='Original admin')
        data.update(changes)
        self.c.execute('INSERT INTO advertisements('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')',tuple(data.values()))
    def dispatch(self, route, method='GET', data=None):
        with patch.object(owner_admin,'stamp',return_value=AT):
            return owner_admin.dispatch(self.c,route,method,data or {},{},1,
                {'name':'New admin','permissions':['ads']},None,SimpleNamespace(DATABASE_URL=''))
    def test_live_feed_matches_subscriber_includes_platform_without_pagination(self):
        for ident in range(1,36): self.org(ident)
        self.org(40,approved=0)
        self.org(41,active=0)
        self.org(42,scheduled_at='2026-10-01T00:00:00+00:00')
        self.org(43,expires_at=AT)
        self.c.execute('INSERT INTO platform_advertisements VALUES(1,?,?,?,?,?,?,?,?,?,?)',
            ('Platform offer','Offer','CODE',IMAGE,1,'2026-09-29T11:59:59+00:00',None,12,'{}',AT))
        live=self.dispatch('ads/live')
        self.assertEqual(live,ad_policy.public_ads(self.c,AT))
        self.assertEqual(len(live),36)
        self.assertEqual(live[-1]['ad_source'],'platform')
        self.assertEqual(live[-1]['display_seconds'],12)
    def test_duration_update_preserves_full_image_and_publication_dates(self):
        self.org(1)
        before=dict(self.c.execute('SELECT * FROM advertisements').fetchone())
        self.dispatch('ads/1','PUT',{'status':'published','display_seconds':25})
        after=dict(self.c.execute('SELECT * FROM advertisements').fetchone())
        self.assertEqual(after['display_seconds'],25)
        for key in ('scheduled_at','expires_at','approved_at','published_at','published_by'):
            self.assertEqual(after[key],before[key],key)
        self.assertEqual(json.loads(after['banner_config']),json.loads(before['banner_config']))
    def test_trial_duration_pending_does_not_publish_ad(self):
        self.org(1,approved=0,published_at=None,published_by='')
        self.dispatch('ads/1','PUT',{'status':'pending','display_seconds':20})
        self.assertEqual(self.dispatch('ads/live'),[])
        self.assertEqual(self.c.execute('SELECT approved FROM advertisements').fetchone()[0],0)

if __name__ == '__main__': unittest.main()
