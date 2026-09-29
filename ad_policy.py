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
    start = datetime.fromisoformat(result['scheduled_at']) if result.get('scheduled_at') else None
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if result.get('approved') and end and end <= at:
        status = 'expired'
    elif result.get('approved') and result.get('active') and start and start > at:
        status = 'scheduled'
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


def public_ads(connection, at):
    """Use the subscriber feed unchanged for the owner live simulation."""
    return [dict(row) for row in connection.execute(
        """SELECT advertisements.id,advertisements.title,advertisements.message,advertisements.contact,
                              advertisements.approved_at,advertisements.expires_at,organizations.name AS advertiser,
                              organizations.phone AS advertiser_phone,
                              '' AS promo_code,'organization' AS ad_source,advertisements.image_data,advertisements.display_seconds,advertisements.banner_config,advertisements.published_at
                       FROM advertisements JOIN organizations ON organizations.id=advertisements.organization_id
                       WHERE advertisements.active=1 AND advertisements.approved=1
                         AND (advertisements.scheduled_at IS NULL OR advertisements.scheduled_at<=?)
                         AND (advertisements.expires_at IS NULL OR advertisements.expires_at>?)
                       UNION ALL
                       SELECT id,title,message,'' AS contact,starts_at AS approved_at,expires_at,
                              'منصة خدووم' AS advertiser,'' AS advertiser_phone,promo_code,'platform' AS ad_source,image_data,display_seconds,banner_config,published_at
                       FROM platform_advertisements
                       WHERE active=1 AND (starts_at IS NULL OR starts_at<=?)
                         AND (expires_at IS NULL OR expires_at>?)
                       ORDER BY approved_at DESC""",
        (at, at, at, at),
    ).fetchall()]
