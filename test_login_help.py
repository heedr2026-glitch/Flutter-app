import sqlite3
import unittest

import login_help


class LoginHelpTest(unittest.TestCase):
    def setUp(self):
        c = self.c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, archived_at TEXT)")
        c.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT, username TEXT, phone TEXT, archived_at TEXT)")
        c.execute("INSERT INTO organizations VALUES(1,'مؤسسة طاهر',NULL)")
        c.execute("INSERT INTO users VALUES(1,1,'محمد','mdalwi','0544850364',NULL)")

    def body(self, **kw):
        base = {'name': 'محمد', 'phone': '+966 54 485 0364', 'category': 'نسيت كلمة المرور', 'message': 'ما أقدر أدخل'}
        base.update(kw)
        return base

    def test_create_list_and_close(self):
        out = login_help.create(self.c, self.body(), '1.1.1.1')
        self.assertTrue(out['reference'].startswith('LH-'))
        data = login_help.handle_admin(self.c, 'login-help', 'GET', {}, {})
        self.assertEqual(data['counts']['open'], 1)
        item = data['items'][0]
        self.assertEqual(item['phone'], '0544850364')
        self.assertEqual(item['accounts'][0]['username'], 'mdalwi')
        login_help.handle_admin(self.c, f"login-help/{item['id']}", 'PUT', {'status': 'done', 'note': 'تم'}, {})
        self.assertEqual(login_help.handle_admin(self.c, 'login-help', 'GET', {}, {'status': 'done'})['counts']['done'], 1)

    def test_rate_limit_and_validation(self):
        for i in range(3):
            login_help.create(self.c, self.body(), f'2.2.2.{i}')
        with self.assertRaises(PermissionError):
            login_help.create(self.c, self.body(), '9.9.9.9')
        for bad in ({'name': ''}, {'phone': '12'}, {'category': 'x'}, {'image': 'javascript:x'}):
            with self.assertRaises(ValueError):
                login_help.create(self.c, self.body(**{'phone': '0500000000', **bad}), '3.3.3.3')


if __name__ == '__main__':
    unittest.main()
