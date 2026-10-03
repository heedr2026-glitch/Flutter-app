"""موظف التقنية AI في لوحة الإدارة: يفهم السؤال بنموذج ذكاء ويستدعي أدوات الفحص الموجودة فقط.

لا يضيف صلاحيات جديدة: كل أداة تمر عبر dispatch الحالي بنفس صلاحيات المدير الحالي،
ولا توجد أداة حذف أو تعديل إنتاجي أو قراءة أسرار. عند تعطل الذكاء يرجع الخادم للفحص الحالي.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable

import ai_core
import technical_support

MAX_TOOL_ROUNDS = 5

STYLE = '''
أسلوب الرد في المحادثة:
- رد مثل موظف تقني سعودي محترف يكلم مديره: جمل قصيرة وواضحة، بدون مقدمات آلية.
- ابدأ بالخلاصة (وش المشكلة أو وش الوضع)، بعدها الدليل من نتائج الأدوات، بعدها الخطوة التالية.
- لا تذكر نتيجة لم ترجع من أداة. إذا ما قدرت تفحص شيء قل بوضوح وش الناقص.
- اكتب نصًا عاديًا فقط: بدون نجوم ولا عناوين Markdown ولا جداول. للتعداد استخدم أرقامًا أو شرطة في أول السطر.
- إذا السؤال عام مثل السلام أو الشكر رد بجملة قصيرة بدون أدوات.
- إذا ذكر المدير مؤسسة بالاسم استخدم find_organization ثم scan_organization.
- إذا ذكر رقم شكوى أو بلاغ استخدم diagnose مع نص السؤال كما هو.
- لأسئلة حالة المنصة أو التقرير استخدم platform_report.
'''.strip()

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "platform_report", "description": "تقرير حالة المنصة الحالي: الخدمات، الشكاوى المفتوحة والمتأخرة والمتكررة، سرعة الصفحات، آخر المهام", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "service_health", "description": "آخر نتائج المراقب التلقائي للخدمات (السيرفر، قاعدة البيانات، الإشعارات، الذكاء)", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "find_organization", "description": "البحث عن مؤسسة مشتركة بالاسم وإرجاع رقمها", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}},
    {"type": "function", "name": "scan_organization", "description": "فحص فعلي لمؤسسة: الحساب، الدخول، الباقة، الرصيد، المستخدمون، الربط. يسجل مهمة فحص", "parameters": {"type": "object", "properties": {"organization_id": {"type": "integer"}}, "required": ["organization_id"], "additionalProperties": False}},
    {"type": "function", "name": "open_tickets", "description": "قائمة شكاوى الدعم غير المغلقة (بدون محتوى الرسائل)", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "diagnose", "description": "الفحص التقني الحالي لمشكلة أو شكوى برقمها؛ يسجل مهمة ويرجع الفحوصات والاقتراح", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"], "additionalProperties": False}},
]


def ai_configured() -> bool:
    return bool(os.environ.get('KHDOOM_AI_API_KEY', '').strip() or os.environ.get('OPENAI_API_KEY', '').strip())


class Toolbox:
    def __init__(self, c, run: Callable[[str, str, dict], Any], rows: Callable):
        self.c, self.run, self.rows = c, run, rows
        self.last_structured: dict[str, Any] | None = None

    def call(self, name: str, args: dict[str, Any]) -> Any:
        try:
            if name == 'platform_report':
                report = self.run('technical-ai/daily-report', 'GET', {})
                return {k: report.get(k) for k in ('checkedAt', 'summary', 'serviceChecks', 'counts', 'overdueTickets', 'repeatedProblems', 'pageMetrics', 'recommendations')}
            if name == 'service_health':
                import service_monitor
                return service_monitor.snapshot()
            if name == 'find_organization':
                term = str(args.get('name', '')).strip()[:80]
                return {'items': self.rows(self.c, 'SELECT id,name FROM organizations WHERE name LIKE ? ORDER BY id LIMIT 10', ('%' + term + '%',))}
            if name == 'scan_organization':
                result = self.run('technical-ai/organization/%d/scan' % int(args.get('organization_id', 0)), 'POST', {})
                self.last_structured = result
                return result
            if name == 'open_tickets':
                return {'items': self.rows(self.c, "SELECT t.id,t.organization_id,o.name organization_name,t.category,t.status,t.created_at,t.updated_at FROM support_tickets t LEFT JOIN organizations o ON o.id=t.organization_id WHERE t.status NOT IN ('resolved','closed') ORDER BY t.id DESC LIMIT 30")}
            if name == 'diagnose':
                result = self.run('technical-ai/ask', 'POST', {'question': str(args.get('question', ''))[:1000], '_rule': True})
                self.last_structured = result
                return result
            return {'error': 'أداة غير متاحة'}
        except Exception as error:  # الأداة تفشل بدون إسقاط المحادثة
            return {'error': str(getattr(error, 'message', '') or error)[:300]}


def answer(c, question: str, history: list[dict[str, str]], run, rows, admin_name: str, client: ai_core.ResponsesClient | None = None) -> dict[str, Any]:
    client = client or ai_core.ResponsesClient()
    tools = Toolbox(c, run, rows)
    clean_history = [{'role': h.get('role'), 'text': str(h.get('text', ''))[:1500]} for h in (history or [])[-8:] if isinstance(h, dict) and h.get('role') in ('user', 'assistant')]
    items: list[dict[str, Any]] = [{'role': 'user', 'content': json.dumps({'conversation': clean_history, 'current_message': question}, ensure_ascii=False)}]
    instructions = technical_support.POLICY + '\n\n' + STYLE + '\nاسم المدير الحالي: ' + admin_name
    safety = hashlib.sha256(('khdoom-admin:' + admin_name).encode()).hexdigest()[:64]
    for _ in range(MAX_TOOL_ROUNDS):
        payload = {'model': ai_core.PRIMARY_MODEL, 'instructions': instructions, 'input': items, 'tools': TOOLS, 'parallel_tool_calls': False, 'max_output_tokens': 1800, 'store': False, 'safety_identifier': safety}
        response = client.transport(payload)
        calls = [x for x in response.get('output', []) if isinstance(x, dict) and x.get('type') == 'function_call']
        if not calls:
            text = client.output_text(response)
            if not text:
                raise ai_core.AIServiceError(502, 'وصل رد فارغ من خدمة الذكاء الاصطناعي')
            out = dict(tools.last_structured or {})
            out.update({'answer': text, 'mode': 'ai'})
            return out
        items.extend(response.get('output', []))
        for call in calls:
            try:
                args = json.loads(call.get('arguments') or '{}')
            except json.JSONDecodeError:
                args = {}
            result = tools.call(str(call.get('name', '')), args if isinstance(args, dict) else {})
            items.append({'type': 'function_call_output', 'call_id': call.get('call_id'), 'output': json.dumps(result, ensure_ascii=False, default=str)[:12000]})
    raise ai_core.AIServiceError(502, 'تجاوز الموظف التقني الحد الآمن لعدد الأدوات')
