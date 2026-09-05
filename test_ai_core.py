import json
import os
import sqlite3
import unittest
from unittest.mock import patch

import ai_core


SCHEMA = """
CREATE TABLE organizations(id INTEGER PRIMARY KEY,name TEXT,activity TEXT,phone TEXT);
CREATE TABLE ai_training(organization_id INTEGER,employee_type TEXT,content TEXT,updated_at TEXT,PRIMARY KEY(organization_id,employee_type));
CREATE TABLE appointment_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,organization_id INTEGER,chat_session_id INTEGER,branch_id TEXT,request_type TEXT,title TEXT,customer_name TEXT,phone TEXT,notes TEXT,scheduled_at TEXT,status TEXT,source TEXT,created_at TEXT,updated_at TEXT);
CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,organization_id INTEGER,public_token TEXT,state TEXT,context_json TEXT,created_at TEXT,updated_at TEXT,branch_id TEXT);
"""


class FakeTransport:
    def __init__(self):
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        return {"output_text": "رد عربي طبيعي من موظف خدوم", "output": []}


class ToolTransport:
    def __init__(self):
        self.count = 0
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        self.count += 1
        if self.count == 1:
            return {"output": [{"type": "function_call", "name": "calculate_quote", "call_id": "call_1", "arguments": json.dumps({"service": "زجاج", "quantity": 3})}]}
        tool_output = json.loads(payload["input"][-1]["output"])
        return {"output_text": f"التقدير المبدئي {tool_output['total_sar']} ريال", "output": []}


class AICoreTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        ai_core.migrate(self.c)
        organizations = [
            (1, "مرايا الأحساء", "زجاج ومرايا", "0500000001", "• سعر زجاج 100 ريال\n• الموقع الأحساء"),
            (2, "بناء الشرق", "مقاولات", "0500000002", "• سعر معاينة 200 ريال"),
            (3, "تموين الحي", "بقالة", "0500000003", "• سعر سلة أساسية 75 ريال"),
            (4, "أناقة", "محل ملابس", "0500000004", "• سعر ثوب 120 ريال"),
            (5, "سيارتي", "تأجير سيارات", "0500000005", "• سعر إيجار يومي 180 ريال"),
        ]
        for oid, name, activity, phone, knowledge in organizations:
            self.c.execute("INSERT INTO organizations VALUES(?,?,?,?)", (oid, name, activity, phone))
            self.c.execute("INSERT INTO ai_training VALUES(?,?,?,?)", (oid, "shared", knowledge, "now"))
        self.c.execute("INSERT INTO chat_sessions(organization_id,public_token,state,context_json,created_at,updated_at,branch_id) VALUES(1,'s','idle','{}','now','now','main')")
        self.c.commit()

    def tearDown(self):
        self.c.close()

    def test_all_verticals_use_sol_responses_core(self):
        transport = FakeTransport()
        service = ai_core.AgentService(ai_core.ResponsesClient(transport))
        for company_id in range(1, 6):
            text = service.respond(self.c, ai_core.AgentContext(company_id, "reception"), "كم السعر؟")
            self.assertIn("عربي", text)
            payload = transport.payloads[-1]
            self.assertEqual(payload["model"], "gpt-5.6-sol")
            self.assertFalse(payload["store"])
            supplied = json.loads(payload["input"][0]["content"])
            self.assertEqual(supplied["company_context"]["company"]["id"], company_id)

    def test_company_data_is_strictly_isolated(self):
        one = ai_core.CompanyTools(self.c, ai_core.AgentContext(1, "reception"), lambda: "now").get_company_info()
        two = ai_core.CompanyTools(self.c, ai_core.AgentContext(2, "reception"), lambda: "now").get_company_info()
        self.assertIn("زجاج", " ".join(one["knowledge"]))
        self.assertNotIn("مقاولات", json.dumps(one, ensure_ascii=False))
        self.assertIn("مقاولات", two["company"]["activity"])
        self.assertNotIn("مرايا الأحساء", json.dumps(two, ensure_ascii=False))

    def test_tool_call_uses_stored_price_and_calculates_quote(self):
        transport = ToolTransport()
        text = ai_core.AgentService(ai_core.ResponsesClient(transport)).respond(self.c, ai_core.AgentContext(1, "reception"), "أبي 3 زجاج")
        self.assertIn("300.0", text)
        self.assertEqual(transport.payloads[1]["input"][-1]["type"], "function_call_output")

    def test_missing_price_is_never_invented(self):
        tools = ai_core.CompanyTools(self.c, ai_core.AgentContext(1, "reception"), lambda: "now")
        self.assertFalse(tools.get_price("ألمنيوم")["ok"])
        self.assertFalse(tools.calculate_quote("ألمنيوم", 2)["ok"])

    def test_memory_is_company_and_session_scoped(self):
        tools = ai_core.CompanyTools(self.c, ai_core.AgentContext(1, "reception", session_id=1), lambda: "now")
        result = tools.save_conversation_summary("العميل يريد زجاجا للمجلس")
        self.assertTrue(result["ok"])
        own = self.c.execute("SELECT summary FROM ai_conversation_summaries WHERE organization_id=1 AND session_id=1").fetchone()
        other = self.c.execute("SELECT summary FROM ai_conversation_summaries WHERE organization_id=2").fetchone()
        self.assertIn("زجاج", own["summary"])
        self.assertIsNone(other)

    def test_sensitive_writes_require_explicit_confirmation(self):
        tools = ai_core.CompanyTools(
            self.c,
            ai_core.AgentContext(
                1,
                "reception",
                session_id=1,
                runtime={"allowed_sensitive_tools": ["create_customer", "create_appointment", "handoff_to_human"]},
            ),
            lambda: "2026-09-05T00:00:00+00:00",
        )
        self.assertFalse(tools.create_customer("علي", "0501234567", False)["ok"])
        self.assertFalse(tools.create_appointment("علي", "0501234567", "مقاس", "2026-09-10T09:00:00+03:00", False)["ok"])
        self.assertFalse(tools.handoff_to_human("استفسار", False)["ok"])
        self.assertTrue(tools.create_customer("علي", "0501234567", True)["ok"])
        self.assertTrue(tools.create_appointment("علي", "0501234567", "موعد مقاس", "2026-09-10T09:00:00+03:00", True)["ok"])
        self.assertTrue(tools.handoff_to_human("طلب العميل موظفا", True)["ok"])

    def test_appointments_are_branch_and_company_scoped(self):
        self.c.execute("INSERT INTO appointment_requests(organization_id,branch_id,status,scheduled_at) VALUES(1,'main','accepted','2099-01-01T09:00:00+03:00')")
        own = ai_core.CompanyTools(self.c, ai_core.AgentContext(1, "reception"), lambda: "now").get_available_appointments("2099-01-01")
        other = ai_core.CompanyTools(self.c, ai_core.AgentContext(2, "reception"), lambda: "now").get_available_appointments("2099-01-01")
        self.assertNotIn("2099-01-01T09:00:00+03:00", own["available"])
        self.assertIn("2099-01-01T09:00:00+03:00", other["available"])

    def test_research_role_has_web_search_and_structured_grounding_prompt(self):
        transport = FakeTransport()
        ai_core.AgentService(ai_core.ResponsesClient(transport)).respond(self.c, ai_core.AgentContext(2, "commercial_research"), "ابحث لي عن مقاولين في الأحساء")
        self.assertIn({"type": "web_search"}, transport.payloads[0]["tools"])
        instructions = transport.payloads[0]["instructions"]
        for field in ("الاسم", "النشاط", "الموقع", "المصدر", "الأولوية"):
            self.assertIn(field, instructions)

    def test_role_permissions_block_unapproved_tool(self):
        tools = ai_core.CompanyTools(self.c, ai_core.AgentContext(1, "assistant"), lambda: "now")
        result = tools.call("create_customer", {"name": "علي", "phone": "0501234567", "confirmed_by_customer": True})
        self.assertFalse(result["ok"])

    def test_server_gate_hides_and_blocks_sensitive_tools(self):
        transport = FakeTransport()
        ai_core.AgentService(ai_core.ResponsesClient(transport)).respond(self.c, ai_core.AgentContext(1, "reception"), "احجز لي")
        names = {tool.get("name") for tool in transport.payloads[0]["tools"]}
        self.assertNotIn("create_appointment", names)
        blocked = ai_core.CompanyTools(self.c, ai_core.AgentContext(1, "reception", session_id=1), lambda: "now").call("create_customer", {"name": "علي", "phone": "0501234567", "confirmed_by_customer": True})
        self.assertFalse(blocked["ok"])

    def test_placeholder_api_key_is_rejected_before_network(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ضع_المفتاح_هنا"}):
            with self.assertRaises(ai_core.AIServiceError) as raised:
                ai_core.ResponsesClient()._http({"model": ai_core.PRIMARY_MODEL, "input": "test"})
        self.assertEqual(raised.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
