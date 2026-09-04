import hashlib
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from test_advertisements import test_db


class BankTransferSubscriptionTest(unittest.TestCase):
    def test_owner_configures_bank_and_activates_named_transfer(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'DB_PATH', Path(directory) / 'test.db'), patch.object(server, 'DATABASE_URL', ''), patch.object(server, 'db', test_db), patch.dict(os.environ, {'KHDOOM_OWNER_KEY': 'owner-test'}):
            server.init_db()
            expiry = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
            with server.db() as c:
                c.execute("INSERT INTO organizations(id,name,phone,created_at) VALUES(1,'مؤسسة الاختبار','0500000000',?)", (server.now(),))
                c.execute("INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(1,'free',?)", (server.now(),))
                c.execute("INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,permissions,created_at) VALUES(1,1,'مالك المؤسسة','owner','x','x','admin','{}',?)", (server.now(),))
                c.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (hashlib.sha256(b'user-token').hexdigest(), 1, expiry, server.now()))
                c.commit()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()

            def call(path, method='GET', body=None, owner=False):
                headers = {'Content-Type':'application/json'}
                headers['X-Owner-Key' if owner else 'Authorization'] = 'owner-test' if owner else 'Bearer user-token'
                with urlopen(Request('http://127.0.0.1:'+str(httpd.server_port)+path, method=method, data=None if body is None else json.dumps(body).encode(), headers=headers), timeout=5) as response:
                    return json.load(response)

            try:
                bank = {'bankName':'بنك الاختبار','accountName':'شركة خدوم','iban':'SA0000000000000000000000','accountNumber':'123','instructions':'اكتب اسم المؤسسة في الملاحظات'}
                self.assertTrue(call('/owner/api/payment-settings','PUT',bank,True)['saved'])
                self.assertEqual(call('/api/payment-settings')['iban'], bank['iban'])
                offers = call('/api/package-offers')
                self.assertEqual(len(offers), 8)
                self.assertEqual(
                    {(x['package'], x['paid_months']) for x in offers},
                    {(package, months) for package in ('basic', 'vip') for months in (1, 3, 6, 12)},
                )
                basic_three = next(x for x in offers if x['package'] == 'basic' and x['paid_months'] == 3)
                self.assertTrue(call('/owner/api/package-offers/'+str(basic_three['id']),'PUT',{'priceSar':149.5},True)['saved'])
                server.init_db()
                refreshed_offers = call('/api/package-offers')
                self.assertEqual(next(x for x in refreshed_offers if x['id'] == basic_three['id'])['price_sar'], 149.5)
                vip = next(x for x in offers if x['package'] == 'vip')
                receipt = 'data:image/png;base64,iVBORw0KGgo='
                request = call('/api/subscription-requests','POST',{'package':'vip','offerId':vip['id'],'transferName':'حيدر محمد','transferReceipt':receipt})
                rows = call('/owner/api/subscription-requests', owner=True)
                self.assertEqual(rows[0]['transfer_name'], 'حيدر محمد')
                self.assertEqual(rows[0]['transfer_receipt'], receipt)
                self.assertEqual(rows[0]['quoted_price'], request['quotedPrice'])
                self.assertTrue(call('/owner/api/subscription-requests/'+str(request['id']),'PUT',{'action':'approve'},True)['saved'])
                self.assertEqual(call('/api/subscription')['package'], 'vip')
                with self.assertRaises(HTTPError) as invalid:
                    call('/api/subscription-requests','POST',{'package':'basic','offerId':offers[0]['id'],'transferName':''})
                self.assertEqual(invalid.exception.code, 400); invalid.exception.close()
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
