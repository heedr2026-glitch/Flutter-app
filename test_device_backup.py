import sqlite3
import unittest

import device_backup


class DeviceBackupTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row

    def test_put_get_index_and_stale(self):
        r = device_backup.put_item(self.c, 1, {'branch': 'main', 'key': 'business_vehicles', 'value': '[1]', 'updatedAt': '2026-10-21T10:00:00+00:00'})
        self.assertTrue(r['saved'])
        self.assertEqual(device_backup.get_item(self.c, 1, 'main', 'business_vehicles')['value'], '[1]')
        old = device_backup.put_item(self.c, 1, {'branch': 'main', 'key': 'business_vehicles', 'value': '[0]', 'updatedAt': '2026-10-20T10:00:00+00:00'})
        self.assertFalse(old['saved'])
        device_backup.put_item(self.c, 1, {'branch': 'b1', 'key': 'alert_السجل التجاري', 'value': '"2027-01-01"', 'updatedAt': '2026-10-21T11:00:00+00:00'})
        keys = [(x['branch'], x['key']) for x in device_backup.index(self.c, 1)['items']]
        self.assertIn(('b1', 'alert_السجل التجاري'), keys)
        self.assertEqual(device_backup.index(self.c, 2)['items'], [])

    def test_rejects_unknown_key_and_branch(self):
        for data in ({'branch': 'main', 'key': 'session_token', 'value': 'x'}, {'branch': '../x', 'key': 'business_vehicles', 'value': 'x'}):
            with self.assertRaises(ValueError):
                device_backup.put_item(self.c, 1, data)


if __name__ == '__main__':
    unittest.main()
