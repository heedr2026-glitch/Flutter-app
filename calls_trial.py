"""تجربة المكالمة الصوتية من لوحة الإدارة.

صفحة يكلم منها مدير المنصة موظف الاستقبال الذكي بالمايك، وبعد انتهاء المكالمة
يُكتب تقرير ويُحفظ في جدول مستقل. لا تمس مكالمات المشتركين ولا أرصدتهم:
لا تكتب في call_logs ولا في call_connections.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import ai_core

CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
REALTIME_MODEL = "gpt-realtime-2.1"
VOICES = {"male": "cedar", "female": "marin"}
MAX_TURNS = 200
MAX_TURN_CHARS = 2000
MAX_CALL_SECONDS = 600
# أبطأ قليلًا من السرعة العادية (1.0)؛ يمكن تغييرها من إعدادات الخادم دون نشر جديد.
SPEECH_SPEED = 0.85

INSTRUCTIONS = """أنت موظف استقبال هاتفي في «خدوم»، ترد على مكالمة واردة من متصل.
تكلم بالعربية بلهجة سعودية طبيعية وواضحة، بنبرة ودودة وهادئة، وبجمل قصيرة مثل كلام الناس في الهاتف.
تكلم على مهلك وبسرعة هادئة، ولا تستعجل في الكلام، وخذ وقفة قصيرة بين الجملة والثانية.
ابدأ المكالمة أنت بالترحيب: عرّف بنفسك أنك موظف الاستقبال في خدوم، وبلّغ المتصل أن المكالمة تُسجَّل كتابيًا، ثم اسأله كيف تخدمه.
المطلوب منك في المكالمة:
- اعرف اسم المتصل.
- افهم طلبه أو مشكلته بالتفصيل الكافي، واسأل سؤالًا واحدًا في كل مرة.
- إذا طلب موعدًا، اسأله عن اليوم والوقت المناسبين وسجّلهما كطلب موعد، وقل له إن الموعد يتأكد برسالة أو اتصال من الموظف المختص. لا تؤكد الحجز من عندك.
- قبل الختام أعد عليه باختصار ما فهمته، واسأله إن كان عنده شيء ثانٍ.
قواعد لازمة:
- لا تخترع أسعارًا ولا مواعيد ولا معلومات عن الخدمات. إذا سُئلت عن شيء لا تعرفه قل إن الموظف المختص بيرجع له، وسجّل سؤاله.
- لا تطلب كلمات مرور ولا أرقام بطاقات ولا رموز تحقق.
- لا تطل الكلام، ولا تقرأ قوائم طويلة.
"""

REPORT_INSTRUCTIONS = """تقرأ نص مكالمة بين متصل وموظف استقبال، وتكتب تقريرًا عنها لصاحب المؤسسة.
أعد كائن JSON فقط بهذه المفاتيح، بلا أي نص قبله أو بعده:
{"callerName": "اسم المتصل أو نص فارغ", "request": "طلب المتصل أو مشكلته في جملة أو جملتين", "appointment": "اليوم والوقت المطلوبان إن طلب موعدًا، وإلا نص فارغ", "followUp": true أو false حسب حاجة المكالمة إلى رجوع موظف للمتصل, "summary": "ملخص المكالمة في ثلاث جمل على الأكثر"}
اكتب بالعربية. لا تضف معلومة لم ترد في المكالمة، وما لم يُذكر اتركه فارغًا.
"""


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS call_trial_reports (
        id {identity}, caller_name TEXT NOT NULL DEFAULT '', request_text TEXT NOT NULL DEFAULT '',
        appointment TEXT NOT NULL DEFAULT '', follow_up INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '',
        duration_seconds INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")


def _api_key() -> str:
    api_key = (os.environ.get("KHDOOM_AI_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip())
    if not api_key:
        raise ai_core.AIServiceError(503, "خدمة AI لم تُفعّل في إعدادات الخادم بعد")
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        raise ai_core.AIServiceError(503, "مفتاح OpenAI في الخادم قيمة إرشادية وليس مفتاحا صالحا")
    return api_key


def _http(payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(CLIENT_SECRETS_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                      headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json; charset=utf-8"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def speech_speed() -> float:
    try:
        return max(0.6, min(float(os.environ.get("KHDOOM_REALTIME_SPEED", "") or SPEECH_SPEED), 1.2))
    except ValueError:
        return SPEECH_SPEED


def session_payload(voice: str, transcription: bool = True, speed: bool = True) -> dict[str, Any]:
    model = os.environ.get("KHDOOM_REALTIME_MODEL", "").strip() or REALTIME_MODEL
    audio: dict[str, Any] = {"output": {"voice": VOICES[voice]}}
    if speed:
        audio["output"]["speed"] = speech_speed()
    if transcription:
        audio["input"] = {"transcription": {"model": "gpt-4o-mini-transcribe", "language": "ar"}}
    return {"session": {"type": "realtime", "model": model, "instructions": INSTRUCTIONS, "audio": audio}}


def create_session(voice: Any, transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """مفتاح مؤقت قصير العمر يتصل به المتصفح مباشرة؛ مفتاح الخادم الحقيقي لا يغادره."""
    choice = "female" if str(voice or "").strip().lower() == "female" else "male"
    send = transport or _http
    result: dict[str, Any] | None = None
    transcription = True
    # تفريغ كلام المتصل وضبط السرعة اختياريان: إن رفض المزود أحدهما نكمل بدونه ولا نعطل التجربة.
    attempts = ((True, True), (True, False), (False, False))
    for index, (transcription, speed) in enumerate(attempts):
        try:
            result = send(session_payload(choice, transcription, speed))
            break
        except HTTPError as error:
            print(f"OPENAI REALTIME SESSION ERROR: {error.code}")
            if error.code == 400 and index < len(attempts) - 1:
                continue
            if error.code in (401, 403):
                raise ai_core.AIServiceError(502, "مفتاح OpenAI لا يملك صلاحية المحادثة الصوتية الحية")
            raise ai_core.AIServiceError(502, "تعذر بدء المحادثة الصوتية الآن")
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"OPENAI REALTIME CONNECTION ERROR: {error}")
            raise ai_core.AIServiceError(502, "تعذر الاتصال بخدمة الذكاء الاصطناعي")
    secret = ""
    if isinstance(result, dict):
        nested = result.get("client_secret")
        secret = str(result.get("value") or (nested.get("value") if isinstance(nested, dict) else "") or "")
    if not secret:
        raise ai_core.AIServiceError(502, "تعذر بدء المحادثة الصوتية الآن")
    return {"clientSecret": secret, "model": session_payload(choice)["session"]["model"], "voice": choice,
            "callerTranscription": transcription, "maxSeconds": MAX_CALL_SECONDS}


def clean_turns(turns: Any) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for turn in turns if isinstance(turns, list) else []:
        if not isinstance(turn, dict):
            continue
        text = " ".join(str(turn.get("text") or "").split())[:MAX_TURN_CHARS]
        if text:
            cleaned.append({"role": "agent" if turn.get("role") == "agent" else "caller", "text": text})
        if len(cleaned) >= MAX_TURNS:
            break
    return cleaned


def transcript_text(turns: list[dict[str, str]]) -> str:
    return "\n".join(("الموظف: " if turn["role"] == "agent" else "المتصل: ") + turn["text"] for turn in turns)


def build_report(turns: list[dict[str, str]], client: ai_core.ResponsesClient | None = None) -> dict[str, Any]:
    """تقرير منظم عن المكالمة. إن تعذر الذكاء نحفظ النص كما هو ولا نضيّع المكالمة."""
    text = transcript_text(turns)
    report = {"callerName": "", "request": "", "appointment": "", "followUp": True, "summary": "", "aiReport": False}
    if not text:
        return report
    try:
        client = client or ai_core.ResponsesClient()
        response = client.transport({"model": ai_core.PRIMARY_MODEL, "instructions": REPORT_INSTRUCTIONS, "input": text,
                                     "max_output_tokens": 1800, "store": False})
        raw = client.output_text(response)
        parsed = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        if not isinstance(parsed, dict):
            raise ValueError("report is not an object")
        report.update({"callerName": str(parsed.get("callerName") or "")[:160], "request": str(parsed.get("request") or "")[:2000],
                       "appointment": str(parsed.get("appointment") or "")[:2000], "followUp": bool(parsed.get("followUp")),
                       "summary": str(parsed.get("summary") or "")[:4000], "aiReport": True})
    except (ai_core.AIServiceError, ValueError, KeyError, TypeError) as error:
        print(f"CALL TRIAL REPORT ERROR: {type(error).__name__}")
        report["summary"] = "تعذر كتابة الملخص بالذكاء الاصطناعي؛ نص المكالمة محفوظ كاملًا."
    return report


def save_report(c, report: dict[str, Any], turns: list[dict[str, str]], duration_seconds: Any, created_at: str) -> dict[str, Any]:
    try:
        duration = max(0, min(int(duration_seconds or 0), 24 * 3600))
    except (TypeError, ValueError):
        duration = 0
    text = transcript_text(turns)[:30000]
    row = c.execute("""INSERT INTO call_trial_reports(caller_name,request_text,appointment,follow_up,summary,transcript,duration_seconds,created_at)
        VALUES(?,?,?,?,?,?,?,?) RETURNING id""",
                    (report["callerName"], report["request"], report["appointment"], int(bool(report["followUp"])),
                     report["summary"], text, duration, created_at)).fetchone()
    return {"id": row["id"], "callerName": report["callerName"], "request": report["request"], "appointment": report["appointment"],
            "followUp": bool(report["followUp"]), "summary": report["summary"], "transcript": text,
            "durationSeconds": duration, "createdAt": created_at, "aiReport": bool(report.get("aiReport"))}


def list_reports(c, limit: int = 20) -> list[dict[str, Any]]:
    rows = c.execute("""SELECT id,caller_name,request_text,appointment,follow_up,summary,transcript,duration_seconds,created_at
        FROM call_trial_reports ORDER BY id DESC LIMIT ?""", (max(1, min(int(limit), 100)),)).fetchall()
    return [{"id": row["id"], "callerName": row["caller_name"], "request": row["request_text"], "appointment": row["appointment"],
             "followUp": bool(row["follow_up"]), "summary": row["summary"], "transcript": row["transcript"],
             "durationSeconds": row["duration_seconds"], "createdAt": row["created_at"]} for row in rows]
