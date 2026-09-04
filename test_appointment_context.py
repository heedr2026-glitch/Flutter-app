import json
import sqlite3
import unittest
import server


class AppointmentContextTest(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:'); self.c.row_factory=sqlite3.Row
        self.c.executescript('''
          CREATE TABLE maintenance_modes(organization_id INTEGER,chat_until TEXT,appointments_until TEXT,message TEXT);
          CREATE TABLE global_maintenance(service TEXT,expires_at TEXT,message TEXT);
          CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY,state TEXT,context_json TEXT,branch_id TEXT,updated_at TEXT);
          CREATE TABLE chat_messages(id INTEGER PRIMARY KEY,session_id INTEGER,sender TEXT,message TEXT);
          CREATE TABLE appointment_requests(id INTEGER PRIMARY KEY,organization_id INTEGER,chat_session_id INTEGER,
          branch_id TEXT,request_type TEXT,title TEXT,customer_name TEXT,phone TEXT,notes TEXT,scheduled_at TEXT,
          status TEXT,source TEXT,created_at TEXT,updated_at TEXT);
          INSERT INTO chat_sessions VALUES(1,'closed','{}','main','');
          INSERT INTO chat_messages VALUES(1,1,'customer','اسمي حيدر');
        ''')

    def tearDown(self): self.c.close()

    def test_maintenance_blocks_new_booking_and_pending_confirmation(self):
        self.c.execute("INSERT INTO maintenance_modes(organization_id,appointments_until,message) VALUES(1,'2099-01-01T00:00:00+00:00','صيانة')")
        self.assertIn('صيانة', self.ask('أبي موعد جديد'))
        self.c.execute("UPDATE chat_sessions SET state='await_confirmation'")
        self.assertIn('صيانة', self.ask('نعم'))
        self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0], 0)

    def ask(self, text):
        session=self.c.execute('SELECT * FROM chat_sessions WHERE id=1').fetchone()
        return server._appointment_chat_reply(self.c,1,session,text)

    def booking(self, status='pending', session=1, branch='main', org=1):
        return self.c.execute('INSERT INTO appointment_requests(organization_id,chat_session_id,branch_id,scheduled_at,status) VALUES(?,?,?,?,?)',
          (org,session,branch,'2026-09-10T14:00:00+00:00',status)).lastrowid

    def test_closed_followup_uses_actual_booking_without_new_record(self):
        self.booking('accepted')
        answer=self.ask('أكد الموعد')
        self.assertIn('الخميس',answer); self.assertIn('5:00 مساءً',answer)
        self.assertIn('معتمد بالفعل',answer)
        self.assertNotIn('ما اسمك',answer)
        self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0],1)

    def test_pending_is_never_falsely_confirmed(self):
        self.booking()
        answer=self.ask('تأكيد موعدي')
        self.assertIn('لم يتم اعتماد',answer)
        self.assertEqual(self.c.execute('SELECT status FROM appointment_requests').fetchone()[0],'pending')

    def test_unknown_booking_does_not_start_new_name_form(self):
        answer=self.ask('أكد الموعد')
        self.assertIn('يا حيدر',answer)
        self.assertIn('محادثة الحجز الأصلية',answer)
        self.assertEqual(self.c.execute('SELECT state FROM chat_sessions').fetchone()[0],'closed')

    def test_other_session_branch_and_tenant_cannot_be_looked_up(self):
        self.booking(session=2); self.booking(branch='b1'); self.booking(org=2)
        answer=self.ask('أكد موعدي رقم 1')
        self.assertNotIn('2026-09-10',answer)
        self.assertIn('ما لقيت',answer)

    def test_multiple_bookings_require_selection(self):
        self.booking(); second=self.booking('accepted')
        self.assertIn('أكثر من طلب',self.ask('حالة موعدي'))
        answer=self.ask('أكد موعدي رقم '+str(second))
        self.assertIn('معتمد بالفعل',answer)

    def test_confirmation_submits_once_but_does_not_approve(self):
        context={'customer_name':'حيدر','phone':'000','request_type':'موعد مقاس','scheduled_at':'2026-09-10T14:00:00+00:00'}
        self.c.execute("UPDATE chat_sessions SET state='await_confirmation',context_json=?",(json.dumps(context),))
        self.assertIn('تم إرسال',self.ask('أكد الموعد'))
        self.assertIn('لم يتم اعتماد',self.ask('أكد الموعد'))
        rows=self.c.execute('SELECT status FROM appointment_requests').fetchall()
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['status'],'pending')

    def test_explicit_new_booking_keeps_known_name(self):
        self.booking('accepted')
        answer=self.ask('ابي موعد جديد')
        self.assertIn('يا حيدر',answer)
        self.assertIn('رقم التواصل',answer)
        self.assertEqual(self.c.execute('SELECT state FROM chat_sessions').fetchone()[0],'await_phone')

    def test_confirmation_question_is_not_consent(self):
        self.c.execute("UPDATE chat_sessions SET state='await_confirmation'")
        self.ask('هل تمام السعر؟')
        self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0],0)
