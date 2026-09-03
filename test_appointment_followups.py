import json
import unittest
import appointment_followups as f
import server
import test_appointment_context as context_tests


class FollowupTest(unittest.TestCase):
    booking = context_tests.AppointmentContextTest.booking
    tearDown = context_tests.AppointmentContextTest.tearDown
    def setUp(self):
        context_tests.AppointmentContextTest.setUp(self)
        self.c.execute('ALTER TABLE chat_messages ADD COLUMN created_at TEXT')
        f.migrate(self.c)
        self.booking_id = self.booking('accepted')

    def ask_followup(self, message, session_id=1):
        source=self.c.execute("INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,'customer',?,?)",(session_id,message,server.now())).lastrowid
        session=self.c.execute('SELECT * FROM chat_sessions WHERE id=?',(session_id,)).fetchone()
        answer=server._appointment_chat_reply(self.c,1,session,message,source)
        return source,answer

    def pending(self):
        rows=self.c.execute('SELECT * FROM appointment_requests WHERE organization_id=1 AND branch_id=\'main\'').fetchall()
        return f.enrich(self.c,1,'main',rows)[0]

    def test_late_report_is_durable_and_does_not_change_booking(self):
        source,answer=self.ask_followup('انا الموظف تأخر')
        self.assertIn('تم تسجيل بلاغك',answer)
        self.assertEqual(self.pending()['followup_latest_id'],source)
        self.assertEqual(self.pending()['followups'][0]['message'],'انا الموظف تأخر')
        self.assertEqual(self.pending()['status'],'accepted')
        self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0],1)

    def test_reply_reaches_original_chat_and_retry_does_not_duplicate(self):
        source,_=self.ask_followup('الموظف ما وصل')
        body={'message':'نعتذر، سيصل الموظف بعد 20 دقيقة','throughMessageId':source}
        result=f.reply(self.c,1,'main',self.booking_id,body,server.now,server.ApiError)
        self.assertTrue(result['sent'])
        self.assertEqual(self.pending()['followup_count'],0)
        self.assertTrue(f.reply(self.c,1,'main',self.booking_id,body,server.now,server.ApiError)['alreadyReplied'])
        rows=self.c.execute("SELECT * FROM chat_messages WHERE sender='human'").fetchall()
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['session_id'],1)

    def test_new_followup_arriving_during_reply_stays_pending(self):
        first,_=self.ask_followup('الموظف تاخر')
        second,_=self.ask_followup('يتواصل معي')
        f.reply(self.c,1,'main',self.booking_id,{'message':'سنتواصل معك','throughMessageId':first},server.now,server.ApiError)
        self.assertEqual(self.pending()['followup_count'],1)
        self.assertEqual(self.pending()['followup_latest_id'],second)

    def test_wrong_branch_or_org_cannot_reply(self):
        source,_=self.ask_followup('اجل موعدي')
        for org,branch in ((2,'main'),(1,'b1')):
            with self.assertRaises(server.ApiError):
                f.reply(self.c,org,branch,self.booking_id,{'message':'رد','throughMessageId':source},server.now,server.ApiError)
        self.assertEqual(self.pending()['followup_count'],1)

    def test_unowned_booking_number_is_not_sent(self):
        _,answer=self.ask_followup('الموظف تأخر عن موعدي رقم 999')
        self.assertIn('لم يتم إرسال',answer)
        self.assertEqual(self.pending()['followup_count'],0)
