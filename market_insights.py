"""«وضعك في السوق»: رسائل قصيرة للمشترك تقارن مؤسسته بمؤسسات نفس النشاط (بدون أسماء)،
مع نصائح من بيانات مؤسسته نفسها.

الخصوصية: لا تُعرض أي مقارنة إلا إذا كان فيه 5 مؤسسات أو أكثر في نفس النشاط عندها بيانات،
والأرقام دائمًا متوسطات بدون أي اسم.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MIN_PEERS = 5


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _response_hours(c, organization_id: int, since: str) -> float | None:
    rows = c.execute("""SELECT created_at,updated_at FROM appointment_requests WHERE organization_id=? AND created_at>=?
        AND status IN ('accepted','completed','rejected') LIMIT 300""", (organization_id, since)).fetchall()
    hours = []
    for row in rows:
        start, end = _parse(row["created_at"]), _parse(row["updated_at"])
        if start and end and end >= start:
            hours.append((end - start).total_seconds() / 3600)
    return sum(hours) / len(hours) if len(hours) >= 3 else None


def _requests(c, organization_id: int, since: str) -> int:
    row = c.execute("SELECT COUNT(*) n FROM appointment_requests WHERE organization_id=? AND created_at>=?", (organization_id, since)).fetchone()
    return int(row["n"] or 0)


def _fmt_hours(hours: float) -> str:
    if hours < 1:
        return f"{max(1, round(hours * 60))} دقيقة"
    if hours < 48:
        return f"{round(hours, 1):g} ساعة"
    return f"{round(hours / 24)} يوم"


def insights(c, organization_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()
    org = c.execute("SELECT activity FROM organizations WHERE id=?", (organization_id,)).fetchone()
    activity = str((org["activity"] if org else "") or "").strip()
    items: list[dict[str, str]] = []

    # ---- مقارنة السوق (بدون أسماء، ومن 5 مؤسسات فأكثر) ----
    peers = [r["id"] for r in c.execute("SELECT id FROM organizations WHERE id<>? AND activity=? AND archived_at IS NULL", (organization_id, activity)).fetchall()] if activity else []
    peer_requests = [n for n in (_requests(c, p, since) for p in peers) if n > 0]
    market_ready = len(peer_requests) >= MIN_PEERS
    if market_ready:
        mine = _requests(c, organization_id, since)
        average = sum(peer_requests) / len(peer_requests)
        if mine >= average * 1.2:
            items.append({"icon": "📈", "kind": "market", "text": f"طلبات عملائك هالشهر {mine}، أعلى من متوسط مؤسسات {activity} ({round(average)}). ممتاز!"})
        elif mine <= average * 0.8:
            items.append({"icon": "📉", "kind": "market", "text": f"طلبات عملائك هالشهر {mine}، ومتوسط مؤسسات {activity} {round(average)}. جرّب تنشر رابط المحادثة في حساباتك وتعلن في خدوم."})
        peer_speed = [h for h in (_response_hours(c, p, since) for p in peers) if h is not None]
        my_speed = _response_hours(c, organization_id, since)
        if my_speed is not None and len(peer_speed) >= MIN_PEERS:
            avg_speed = sum(peer_speed) / len(peer_speed)
            if my_speed > avg_speed * 1.3:
                items.append({"icon": "⏱️", "kind": "market", "text": f"ترد على طلبات العملاء خلال {_fmt_hours(my_speed)}، والمؤسسات اللي بنفس نشاطك خلال {_fmt_hours(avg_speed)}. الرد الأسرع يجيب عملاء أكثر."})
            elif my_speed < avg_speed * 0.8:
                items.append({"icon": "⚡", "kind": "market", "text": f"ردك على العملاء ({_fmt_hours(my_speed)}) أسرع من متوسط نشاطك ({_fmt_hours(avg_speed)}). استمر!"})
    else:
        items.append({"icon": "📊", "kind": "info", "text": "نجمع بيانات السوق. أول مقارنة بالمؤسسات اللي بنفس نشاطك تطلع لك لما يكتمل العدد الكافي، بدون أسماء أبدًا."})

    # ---- نصائح من بيانات مؤسستك ----
    waiting = c.execute("SELECT COUNT(*) n FROM appointment_requests WHERE organization_id=? AND status='pending' AND created_at<?",
                        (organization_id, (now - timedelta(hours=24)).isoformat())).fetchone()
    if int(waiting["n"] or 0):
        items.append({"icon": "🕒", "kind": "tip", "text": f"عندك {int(waiting['n'])} طلب عميل ينتظر ردك من أكثر من يوم. افتح «المواعيد والطلبات» ورد عليهم قبل لا يروحون لغيرك."})
    questions = c.execute("SELECT COUNT(*) n FROM ai_pending_questions WHERE organization_id=? AND status='pending'", (organization_id,)).fetchone()
    if int(questions["n"] or 0):
        items.append({"icon": "❓", "kind": "tip", "text": f"موظفينك عندهم {int(questions['n'])} سؤال ينتظر جوابك في «تعليم الموظفين». جاوبهم مرة وحدة ويتعلمون."})
    training = c.execute("SELECT COUNT(*) n FROM ai_training_instructions WHERE organization_id=? AND status='approved'", (organization_id,)).fetchone()
    if int(training["n"] or 0) < 5:
        items.append({"icon": "🎓", "kind": "tip", "text": "موظفك الذكي يعرف قليل عن مؤسستك. علّمه أسعارك وخدماتك وأوقات الدوام من «تعليم الموظفين»، وبيرد على العملاء أحسن."})
    return {"activity": activity, "marketReady": market_ready, "items": items}
