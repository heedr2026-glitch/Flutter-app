import json
import unittest
import reception_actions as actions
import appointment_followups
import server
from test_appointment_context import AppointmentContextTest


class ReceptionActionsTest(unittest.TestCase):
    tearDown=AppointmentContextTest.tearDown
    def setUp(self):
        AppointmentContextTest.setUp(self)
        self.c.execute('ALTER TABLE chat_messages ADD COLUMN created_at TEXT')
        appointment_followups.migrate(self.c)
        self.c.execute("UPDATE chat_sessions SET state='idle'")

    def send(self,text):
        cursor=self.c.execute("INSERT INTO chat_messages(session_id,sender,message) VALUES(1,'customer',?)",(text,))
        session=self.c.execute('SELECT * FROM chat_sessions WHERE id=1').fetchone()
        return cursor.lastrowid,server._appointment_chat_reply(self.c,1,session,text,cursor.lastrowid)

    def test_explicit_handoff_creates_inbox_not_booking_and_repeat_reuses(self):
        mid,answer=self.send('عطني موظف بشري اتكلم معه')
        self.assertIn('تم تسجيل طلب تواصل',answer)
        row=self.c.execute('SELECT * FROM appointment_requests').fetchone()
        self.assertEqual(row['branch_id'],'main')
        self.assertEqual(row['source'],'human_handoff')
        self.assertEqual(row['scheduled_at'],'')
        self.send('ابي اسأل عن سعر الزجاج')
        self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_followups').fetchone()[0],2)

    def test_price_does_not_turn_into_booking_or_name(self):
        for state in ('idle','await_name','await_datetime','waiting_human'):
            self.c.execute('UPDATE chat_sessions SET state=?',(state,))
            _,reply=self.send('كم السهر حق تركيب الزجاج')
            self.assertIsNone(reply)
            self.assertEqual(self.c.execute('SELECT count(*) FROM appointment_requests').fetchone()[0],0)
            self.assertEqual(self.c.execute('SELECT state FROM chat_sessions').fetchone()[0],state)

    def test_reply_closes_only_handoff_and_stays_in_session(self):
        mid,_=self.send('ابي موظف بشري')
        aid=self.c.execute('SELECT id FROM appointment_requests').fetchone()[0]
        result=appointment_followups.reply(self.c,1,'main',aid,{'message':'تفضل كيف نخدمك؟','throughMessageId':mid},server.now,server.ApiError)
        self.assertTrue(result['sent'])
        self.assertEqual(self.c.execute('SELECT status FROM appointment_requests').fetchone()[0],'completed')
        self.assertEqual(self.c.execute("SELECT session_id FROM chat_messages WHERE sender='human'").fetchone()[0],1)

    def test_cannot_route_reply_across_branches(self):
        mid,_=self.send('ابي موظف بشري')
        aid=self.c.execute('SELECT id FROM appointment_requests').fetchone()[0]
        with self.assertRaises(server.ApiError):
            appointment_followups.reply(self.c,1,'other',aid,{'message':'رد','throughMessageId':mid},server.now,server.ApiError)

    def test_declining_human_does_not_create_request(self):
        self.assertFalse(actions.wants_human('ما ابي موظف بشري'))
        self.assertFalse(actions.wants_human('كم السعر'))

    def test_exact_saved_qa_and_conflict(self):
        line='سؤال وجواب معتمد: '+json.dumps({'السؤال':'كم سعر الزجاج؟','الإجابة':'150 ريال للمتر'},ensure_ascii=False)
        self.assertEqual(actions.exact_answer('كم سعر الزجاج',line),'150 ريال للمتر')
        self.assertIsNone(actions.exact_answer('كم سعر باب الخشب',line))
        self.assertIsNone(actions.exact_answer('كم سعر الزجاج',line+'\n'+line.replace('150','200')))


if __name__=='__main__': unittest.main()
