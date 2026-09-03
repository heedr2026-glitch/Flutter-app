"""Advertisement request limits and a single status projection for both UIs."""
from datetime import datetime, timedelta, timezone
import math


def requested_days(value=10):
    if type(value) is not int or not 1 <= value <= 10:
        raise ValueError('مدة طلب المشترك من يوم إلى 10 أيام فقط')
    return value


def owner_expiry(value, start):
    # None is explicitly unlimited; the owner is not subject to the user cap.
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError('اكتب عدد أيام صحيحًا أكبر من صفر، أو اختر بدون نهاية')
    try:
        return (start + timedelta(days=value)).isoformat()
    except (OverflowError, ValueError):
        raise ValueError('المدة أكبر من نطاق التاريخ المدعوم')


def project(row, at=None):
    result = dict(row)
    at = at or datetime.now(timezone.utc)
    end = datetime.fromisoformat(result['expires_at']) if result.get('expires_at') else None
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if result.get('approved') and end and end <= at:
        status = 'expired'
    elif result.get('approved') and result.get('active'):
        status = 'published'
    elif result.get('approved'):
        status = 'paused'
    elif result.get('active'):
        status = 'pending'
    else:
        status = 'rejected'
    result['status'] = status
    result['remaining_days'] = max(0, math.ceil((end-at).total_seconds()/86400)) if end else None
    return result
