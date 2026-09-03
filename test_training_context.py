import json
import sqlite3
import unittest
import training_context as tc


class TrainingContextTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''
        CREATE TABLE organizations(id INTEGER,name TEXT);
        CREATE TABLE subscriptions(organization_id INTEGER,package TEXT,expires_at TEXT);
        CREATE TABLE ai_training_messages(id INTEGER PRIMARY KEY,organization_id INTEGER,employee_type TEXT,sender TEXT,message TEXT);
        INSERT INTO organizations VALUES(1,'مؤسسة حيدر'),(2,'مؤسسة أخرى');
        INSERT INTO subscriptions VALUES(1,'vip',NULL),(2,'basic',NULL);
        ''')

    def tearDown(self): self.c.close()

    def test_package_from_database_not_client(self):
        info=tc.context(self.c,1,{'package':'free','branchName':'الرياض','password':'secret'})
        self.assertEqual(info['package']['package'],'vip')
        self.assertNotIn('password',str(info))
        for question in ('الباقة','باقه','باقتي','وش باقتي'):
            self.assertIn('VIP',tc.direct_answer(question,info))

    def test_branch_short_word_asks_meaningful_followup(self):
        info=tc.context(self.c,1,{'branchName':'الرياض'})
        self.assertIn('الرياض',tc.direct_answer('فرع',info))
        self.assertIn('تقصد',tc.direct_answer('فرع',tc.context(self.c,1,None)))

    def test_history_is_bounded_ordered_and_tenant_and_role_scoped(self):
        for i in range(15):
            self.c.execute('INSERT INTO ai_training_messages(organization_id,employee_type,sender,message) VALUES(1,?,?,?)',('assistant','owner',str(i)))
        self.c.execute("INSERT INTO ai_training_messages(organization_id,employee_type,sender,message) VALUES(2,'assistant','owner','private')")
        self.c.execute("INSERT INTO ai_training_messages(organization_id,employee_type,sender,message) VALUES(1,'chat','owner','other employee')")
        history=tc.history(self.c,1,'assistant')
        self.assertEqual(len(history),12)
        self.assertEqual(history[0]['message'],'3')
        self.assertEqual(history[-1]['message'],'14')

    def test_prompt_includes_context_and_limits_execution(self):
        system,user=tc.prompts('وبعدها؟','سعر المتر 150',[{'sender':'owner','message':'اسمي حيدر'}],{'package':'vip'})
        payload=json.loads(user)
        self.assertIn('حيدر',payload['conversation'][0]['message'])
        self.assertIn('لا توجد لديك أدوات تنفيذ',system)
        self.assertEqual(payload['savedKnowledge'],'سعر المتر 150')

    def test_capacity_rejects_without_truncation_or_false_success(self):
        original='x'*11999
        self.assertEqual(tc.append_fact(original,'new fact'),(original,'full'))

    def test_fact_deduplicated_and_own_content_preserved(self):
        self.assertEqual(tc.append_fact('• باقة','باقه'),('• باقة','already_saved'))
        saved,status=tc.append_fact('تعليم قديم','معلومة جديدة')
        self.assertEqual(status,'saved')
        self.assertTrue(saved.startswith('تعليم قديم'))


if __name__=='__main__': unittest.main()
