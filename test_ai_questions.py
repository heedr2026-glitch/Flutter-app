"""«الموظف يسألك» و«وضعك في السوق»."""
import ai_core
import ai_questions
import market_insights
import server
from test_service_number_claims import ServiceNumberClaimsTest


class AiQuestionsTest(ServiceNumberClaimsTest):
    def test_employee_asks_owner_answer_reaches_customer_and_training(self):
        self.assertIn('ask_owner', ai_core.ROLES['reception'].tools)
        with server.db() as c:
            session = c.execute("INSERT INTO chat_sessions(organization_id,public_token,created_at,updated_at) VALUES(1,'tok-q1',?,?)", (server.now(), server.now())).lastrowid
            first = ai_questions.ask(c, 1, 'reception', session, 'عندكم زجاج مقاوم للرصاص؟', 'عندكم زجاج ضد الرصاص؟', server.now())
            self.assertEqual(ai_questions.ask(c, 1, 'reception', session, 'عندكم زجاج مقاوم للرصاص؟', '', server.now()), first)
            c.commit()
        status, data = self.call('/api/ai-questions', user=1)
        self.assertEqual(status, 200)
        self.assertEqual([(i['employee_name'], i['question']) for i in data['items']], [('موظف المحادثة الخارجية', 'عندكم زجاج مقاوم للرصاص؟')])
        self.assertEqual(self.call('/api/ai-questions', user=2)[1]['items'], [])
        notices = self.call('/api/notices?after=0', user=1)[1]['items']
        self.assertIn('ai_question', [n['kind'] for n in notices])
        self.assertEqual(self.call(f'/api/ai-questions/{first}/answer', 'POST', {'answer': 'لا'}, user=2)[0], 404)
        status, out = self.call(f'/api/ai-questions/{first}/answer', 'POST', {'answer': 'لا، بس عندنا زجاج أمان مقسّى', 'saveFor': 'shared'}, user=1)
        self.assertEqual((status, out['deliveredToCustomer'], out['savedFor']), (200, True, 'shared'))
        self.assertEqual(self.call(f'/api/ai-questions/{first}/answer', 'POST', {'answer': 'مرة ثانية'}, user=1)[0], 409)
        with server.db() as c:
            msg = c.execute("SELECT sender,message FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (session,)).fetchone()
            taught = c.execute("SELECT employee_type,content FROM ai_training_instructions WHERE organization_id=1 ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(tuple(msg), ('human', 'لا، بس عندنا زجاج أمان مقسّى'))
        self.assertEqual(taught['employee_type'], 'shared')
        self.assertIn('مقاوم للرصاص', taught['content'])
        self.assertEqual(self.call('/api/ai-questions', user=1)[1]['items'], [])

    def test_market_insights_hidden_until_enough_peers(self):
        status, data = self.call('/api/market-insights', user=1)
        self.assertEqual(status, 200)
        self.assertFalse(data['marketReady'])
        self.assertTrue(any(i['kind'] == 'info' for i in data['items']))
        self.assertTrue(any('علّمه' in i['text'] for i in data['items']))
