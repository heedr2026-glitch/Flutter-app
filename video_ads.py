"""إعلانات الفيديو (صفحة «عروض» بأسلوب تيك توك).

- الإنشاء لمشتركي VIP بعدد شهري يحدده المالك، وغير VIP يدفع سعر الإعلان الواحد.
- كل فيديو يمر على مراجعة المالك، والمالك يحدد مدة الظهور عند الموافقة.
- بعد انتهاء المدة يختفي من العروض ويبقى محفوظًا عند المشترك ليعيد نشره.
- كل مؤسسة تحتفظ بحد أقصى من الفيديوهات؛ رفع فيديو زائد يحذف الأقدم.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import owner_admin as admin

# المدد المسموحة عند الموافقة (بالساعات).
DURATIONS = {"1h": 1, "2h": 2, "1d": 24, "2d": 48, "1w": 168, "1m": 720}
DURATION_LABELS = {"1h": "ساعة", "2h": "ساعتين", "1d": "يوم", "2d": "يومين", "1w": "أسبوع", "1m": "شهر"}
DEFAULT_SETTINGS = {
    "max_seconds": 30,          # المدة القصوى للفيديو، يزيدها المالك متى ما يبي
    "quota_free": 0,            # عدد الإعلانات المجانية بالشهر لكل باقة
    "quota_basic": 0,
    "quota_vip": 5,
    "paid_price_sar": 29,       # سعر الإعلان الواحد لغير VIP
    "max_saved": 5,             # حد الفيديوهات المحفوظة لكل مؤسسة
    "max_upload_mb": 40,
    "enabled": 1,
}
VIDEO_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm", "video/3gpp": ".3gp"}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def video_dir() -> Path:
    base = os.environ.get("KHDOOM_VIDEO_DIR", "").strip()
    if not base:
        data = Path("/data")
        base = str(data / "video_ads") if data.is_dir() and os.access(data, os.W_OK) else str(Path(__file__).resolve().parent / "data" / "video_ads")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate(c, postgres: bool = False) -> None:
    identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"""CREATE TABLE IF NOT EXISTS video_ads(
        id {identity},
        organization_id BIGINT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        caption TEXT NOT NULL DEFAULT '',
        overlay_text TEXT NOT NULL DEFAULT '',
        activity TEXT NOT NULL DEFAULT '',
        contact TEXT NOT NULL DEFAULT '',
        allow_download INTEGER NOT NULL DEFAULT 1,
        file_name TEXT NOT NULL DEFAULT '',
        content_type TEXT NOT NULL DEFAULT '',
        size_bytes BIGINT NOT NULL DEFAULT 0,
        seconds REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        paid INTEGER NOT NULL DEFAULT 0,
        price_sar REAL NOT NULL DEFAULT 0,
        transfer_name TEXT NOT NULL DEFAULT '',
        duration_key TEXT NOT NULL DEFAULT '',
        review_note TEXT NOT NULL DEFAULT '',
        reviewed_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        submitted_at TEXT,
        published_at TEXT,
        expires_at TEXT,
        deleted INTEGER NOT NULL DEFAULT 0)""")
    c.execute("CREATE TABLE IF NOT EXISTS video_ad_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS video_ad_quota_overrides(organization_id BIGINT PRIMARY KEY, monthly_limit INTEGER NOT NULL, updated_at TEXT NOT NULL)")
    c.execute(f"""CREATE TABLE IF NOT EXISTS video_ad_submissions(
        id {identity}, organization_id BIGINT NOT NULL, video_ad_id BIGINT NOT NULL,
        paid INTEGER NOT NULL DEFAULT 0, counted INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
    present = set() if postgres else {r["name"] for r in c.execute("PRAGMA table_info(video_ads)")}
    if postgres or "overlay_y" not in present:
        c.execute("ALTER TABLE video_ads ADD COLUMN " + ("IF NOT EXISTS " if postgres else "") + "overlay_y REAL NOT NULL DEFAULT 0.15")
    if postgres or "optimized" not in present:
        c.execute("ALTER TABLE video_ads ADD COLUMN " + ("IF NOT EXISTS " if postgres else "") + "optimized INTEGER NOT NULL DEFAULT 0")
    c.execute("CREATE INDEX IF NOT EXISTS video_ads_status ON video_ads(status, deleted)")
    c.execute("CREATE INDEX IF NOT EXISTS video_ads_org ON video_ads(organization_id, deleted)")
    c.execute("CREATE INDEX IF NOT EXISTS video_ad_submissions_org ON video_ad_submissions(organization_id, created_at)")
    c.execute("CREATE TABLE IF NOT EXISTS video_ad_likes(video_ad_id BIGINT NOT NULL, organization_id BIGINT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(video_ad_id, organization_id))")
    for key, value in DEFAULT_SETTINGS.items():
        c.execute("INSERT INTO video_ad_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (key, str(value)))
    c.execute("INSERT INTO video_ad_settings(key,value) VALUES('link_secret',?) ON CONFLICT(key) DO NOTHING", (secrets.token_hex(32),))


# ---------------------------------------------------------------- الإعدادات
def settings(c) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in c.execute("SELECT key,value FROM video_ad_settings").fetchall():
        if row["key"] in out:
            try:
                out[row["key"]] = float(row["value"]) if row["key"] == "paid_price_sar" else int(float(row["value"]))
            except ValueError:
                pass
    return out


def save_settings(c, data: dict, actor: str) -> dict:
    limits = {"max_seconds": (5, 600), "quota_free": (0, 1000), "quota_basic": (0, 1000), "quota_vip": (0, 1000),
              "paid_price_sar": (0, 100000), "max_saved": (1, 100), "max_upload_mb": (1, 500), "enabled": (0, 1)}
    changed = {}
    for key, (low, high) in limits.items():
        if key not in data or data[key] in (None, ""):
            continue
        value = admin.number(data[key], low, high, integer=key != "paid_price_sar")
        c.execute("INSERT INTO video_ad_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        changed[key] = value
    if changed:
        admin.audit(c, actor, "video_ad_settings_saved", json.dumps(changed, ensure_ascii=False))
    return settings(c)


# ---------------------------------------------------------------- الحصة الشهرية
def month_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def org_package(c, org: int) -> str:
    row = c.execute("SELECT package,expires_at FROM subscriptions WHERE organization_id=?", (org,)).fetchone()
    if not row or (row["expires_at"] and str(row["expires_at"]) <= stamp()):
        return "free"
    return row["package"] or "free"


def quota(c, org: int) -> dict:
    cfg = settings(c)
    package = org_package(c, org)
    override = c.execute("SELECT monthly_limit FROM video_ad_quota_overrides WHERE organization_id=?", (org,)).fetchone()
    limit = int(override["monthly_limit"]) if override else int(cfg.get("quota_" + package, 0))
    used = c.execute("SELECT COUNT(*) n FROM video_ad_submissions WHERE organization_id=? AND paid=0 AND counted=1 AND created_at>=?",
                     (org, month_start())).fetchone()["n"]
    return {"package": package, "limit": limit, "used": int(used), "remaining": max(0, limit - int(used)),
            "custom": bool(override), "paid_price_sar": cfg["paid_price_sar"], "max_seconds": cfg["max_seconds"],
            "max_saved": cfg["max_saved"], "can_create": package == "vip", "enabled": bool(cfg["enabled"])}


# ---------------------------------------------------------------- انتهاء المدة
def expire_due(c) -> int:
    now = stamp()
    rows = c.execute("SELECT id,organization_id,title FROM video_ads WHERE status='published' AND deleted=0 AND expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
    for row in rows:
        c.execute("UPDATE video_ads SET status='expired' WHERE id=?", (row["id"],))
        _notice(c, row["organization_id"], "انتهى إعلانك 🎬", f"انتهت مدة إعلان الفيديو «{row['title'] or 'بدون عنوان'}». محفوظ عندك في «إعلاناتي» وتقدر تعيد نشره أي وقت.", f"video_ad_expired:{row['id']}:{now[:13]}")
        admin.audit(c, "النظام", "video_ad_expired", row["id"])
    return len(rows)


def _notice(c, org, title, body, dedupe=""):
    try:
        import app_notices
        app_notices.add(c, org, "video_ad", title, body, "video_ads", dedupe)
    except Exception:  # التنبيه إضافة؛ لا يوقف العملية الأساسية
        pass


# ---------------------------------------------------------------- الملفات
def _file_path(row) -> Path | None:
    name = row["file_name"] if row else ""
    if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    return video_dir() / name


def _remove_file(row) -> None:
    path = _file_path(row)
    if path:
        for item in (path, path.with_name("wm_" + path.stem + ".mp4")):
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass


def storage_used() -> int:
    try:
        return sum(p.stat().st_size for p in video_dir().iterdir() if p.is_file())
    except OSError:
        return 0


def _send_file(h, row, s, download=False) -> None:
    path = _file_path(row)
    if not path or not path.exists():
        raise s.ApiError(404, "ملف الفيديو غير موجود")
    size = path.stat().st_size
    start, end = 0, size - 1
    rng = h.headers.get("Range", "")
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
    status = 200
    if match and (match[1] or match[2]):
        if match[1]:
            start = int(match[1]); end = int(match[2]) if match[2] else size - 1
        else:
            start = max(0, size - int(match[2]))
        end = min(end, size - 1)
        if start > end:
            raise s.ApiError(416, "نطاق غير صحيح")
        status = 206
    h._last_status = status
    h.send_response(status)
    h.send_header("Content-Type", row["content_type"] or "video/mp4")
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Content-Length", str(end - start + 1))
    if status == 206:
        h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    if download:
        h.send_header("Content-Disposition", f'attachment; filename="khdoom-ad-{row["id"]}{path.suffix}"')
    h.send_header("Cache-Control", "private, max-age=3600")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    with path.open("rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(262144, remaining))
            if not chunk:
                break
            h.wfile.write(chunk)
            remaining -= len(chunk)


# ---------------------------------------------------------------- لوحة الإدارة
def _owner_row(row) -> dict:
    out = dict(row)
    out["duration_label"] = DURATION_LABELS.get(out.get("duration_key") or "", "")
    return out


def owner_list(c, status: str, page: int) -> dict:
    if expire_due(c):
        c.commit()
    statuses = {"pending", "published", "rejected", "expired"}
    if status not in statuses:
        status = "pending"
    select = ("SELECT v.id,v.organization_id,v.title,v.caption,v.overlay_text,v.activity,v.contact,v.allow_download,v.size_bytes,v.seconds,"
              "v.status,v.paid,v.price_sar,v.transfer_name,v.duration_key,v.review_note,v.reviewed_by,v.created_at,v.submitted_at,"
              "v.published_at,v.expires_at,o.name organization_name")
    where = "FROM video_ads v JOIN organizations o ON o.id=v.organization_id WHERE v.deleted=0 AND v.status=?"
    order = "v.submitted_at ASC,v.id ASC" if status == "pending" else "v.id DESC"
    result = admin.paged(c, select, where, [status], order, page)
    result["items"] = [{**_owner_row(x), "package": org_package(c, x["organization_id"]),
                        "likes": int(c.execute("SELECT COUNT(*) n FROM video_ad_likes WHERE video_ad_id=?", (x["id"],)).fetchone()["n"])}
                       for x in result.get("items", [])]
    result["counts"] = {k: c.execute("SELECT COUNT(*) n FROM video_ads WHERE deleted=0 AND status=?", (k,)).fetchone()["n"] for k in statuses}
    result["durations"] = [{"key": k, "label": DURATION_LABELS[k]} for k in DURATIONS]
    return result


def owner_review(c, ident: int, data: dict, actor: str) -> dict:
    row = c.execute("SELECT * FROM video_ads WHERE id=? AND deleted=0", (ident,)).fetchone()
    if not row:
        raise ValueError("الإعلان غير موجود أو حذفه المشترك")
    action = data.get("action")
    title = row["title"] or "بدون عنوان"
    if action == "approve":
        if row["status"] != "pending":
            raise ValueError("هذا الطلب ليس بانتظار المراجعة")
        key = str(data.get("duration", ""))
        if key not in DURATIONS:
            raise ValueError("اختر مدة الإعلان")
        if row["paid"] and data.get("paymentConfirmed") is not True:
            raise ValueError("هذا إعلان مدفوع؛ أكّد وصول التحويل أولًا")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=DURATIONS[key])).isoformat()
        c.execute("UPDATE video_ads SET status='published',duration_key=?,published_at=?,expires_at=?,review_note='',reviewed_by=? WHERE id=?",
                  (key, now.isoformat(), expires, actor[:80], ident))
        _notice(c, row["organization_id"], "تم نشر إعلانك 🎉", f"إعلان الفيديو «{title}» صار منشورًا في العروض لمدة {DURATION_LABELS[key]}.", f"video_ad_published:{ident}:{now.isoformat()[:16]}")
        admin.audit(c, actor, "video_ad_approved", json.dumps({"id": ident, "duration": key}, ensure_ascii=False))
    elif action == "reject":
        if row["status"] != "pending":
            raise ValueError("هذا الطلب ليس بانتظار المراجعة")
        note = str(data.get("note", "")).strip()[:300]
        if not note:
            raise ValueError("اكتب سبب الرفض ليعرف المشترك وش يعدل")
        c.execute("UPDATE video_ads SET status='rejected',review_note=?,reviewed_by=? WHERE id=?", (note, actor[:80], ident))
        # الإعلان المرفوض لا ينحسب من العدد الشهري.
        c.execute("UPDATE video_ad_submissions SET counted=0 WHERE video_ad_id=? AND id=(SELECT MAX(id) FROM video_ad_submissions WHERE video_ad_id=?)", (ident, ident))
        _notice(c, row["organization_id"], "إعلانك يحتاج تعديل", f"إعلان الفيديو «{title}» لم يُقبل: {note}", f"video_ad_rejected:{ident}:{stamp()[:16]}")
        admin.audit(c, actor, "video_ad_rejected", json.dumps({"id": ident, "note": note}, ensure_ascii=False))
    elif action == "stop":
        if row["status"] != "published":
            raise ValueError("الإعلان غير منشور حاليًا")
        c.execute("UPDATE video_ads SET status='expired',expires_at=?,reviewed_by=? WHERE id=?", (stamp(), actor[:80], ident))
        _notice(c, row["organization_id"], "تم إيقاف إعلانك", f"تم إيقاف إعلان الفيديو «{title}» من الإدارة. محفوظ عندك وتقدر تعيد نشره.", f"video_ad_stopped:{ident}:{stamp()[:16]}")
        admin.audit(c, actor, "video_ad_stopped", ident)
    else:
        raise ValueError("الإجراء غير معروف")
    return {"saved": True}


def owner_overrides(c) -> list:
    items = admin.rows(c, "SELECT q.organization_id,q.monthly_limit,q.updated_at,o.name organization_name FROM video_ad_quota_overrides q JOIN organizations o ON o.id=q.organization_id ORDER BY o.name")
    return [{**x, "package": org_package(c, x["organization_id"])} for x in items]


def owner(c, r, m, d, q, page, a, s):
    actor = a.get("name") or "المالك"
    if r == "video-ads" and m == "GET":
        return owner_list(c, (q.get("status") or "pending"), page)
    if r == "video-ads/settings" and m == "GET":
        cfg = settings(c)
        cfg["storage_used_bytes"] = storage_used()
        cfg["overrides"] = owner_overrides(c)
        return cfg
    if r == "video-ads/settings" and m == "PUT":
        return save_settings(c, d, actor)
    match = re.fullmatch(r"video-ads/quota/([0-9]+)", r)
    if match and m == "PUT":
        org = int(match[1])
        if not c.execute("SELECT id FROM organizations WHERE id=?", (org,)).fetchone():
            raise ValueError("المؤسسة غير موجودة")
        if d.get("limit") in (None, ""):
            c.execute("DELETE FROM video_ad_quota_overrides WHERE organization_id=?", (org,))
            admin.audit(c, actor, "video_ad_quota_cleared", org)
        else:
            limit = admin.number(d.get("limit"), 0, 1000, True)
            c.execute("INSERT INTO video_ad_quota_overrides(organization_id,monthly_limit,updated_at) VALUES(?,?,?) ON CONFLICT(organization_id) DO UPDATE SET monthly_limit=excluded.monthly_limit,updated_at=excluded.updated_at", (org, limit, stamp()))
            admin.audit(c, actor, "video_ad_quota_set", json.dumps({"organization": org, "limit": limit}))
        return {"saved": True}
    match = re.fullmatch(r"video-ads/([0-9]+)", r)
    if match and m == "PUT":
        return owner_review(c, int(match[1]), d, actor)
    raise ValueError("المسار غير موجود")


def owner_file(h, route, s) -> None:
    match = re.fullmatch(r"video-ads/([0-9]+)/file", route)
    if not match:
        raise s.ApiError(404, "المسار غير موجود")
    with s.db() as c:
        row = c.execute("SELECT id,file_name,content_type FROM video_ads WHERE id=? AND deleted=0", (int(match[1]),)).fetchone()
    if not row:
        raise s.ApiError(404, "الإعلان غير موجود")
    _send_file(h, row, s)


# ---------------------------------------------------------------- تطبيق المشترك
def _attach_likes(c, items, org) -> None:
    """كل مؤسسة تعجب بالإعلان مرة وحدة؛ نرجع العدد وهل أعجبتها."""
    for item in items:
        item["likes"] = int(c.execute("SELECT COUNT(*) n FROM video_ad_likes WHERE video_ad_id=?", (item["id"],)).fetchone()["n"])
        item["liked"] = bool(c.execute("SELECT 1 FROM video_ad_likes WHERE video_ad_id=? AND organization_id=?", (item["id"], org)).fetchone())


def _public(row, base_contact="", package="") -> dict:
    return {"id": row["id"], "title": row["title"], "caption": row["caption"], "overlayText": row["overlay_text"],
            "activity": row["activity"], "contact": row["contact"] or base_contact, "allowDownload": bool(row["allow_download"]),
            "seconds": row["seconds"], "organizationId": row["organization_id"],
            "organizationName": row["organization_name"] if "organization_name" in row.keys() else "",
            "vip": package == "vip",
            "overlayY": float(row["overlay_y"]) if "overlay_y" in row.keys() else 0.15,
            "videoUrl": f"/api/video-ads/{row['id']}/file", "publishedAt": row["published_at"], "expiresAt": row["expires_at"]}


def _read_upload(h, s, max_mb: int, target: Path) -> int:
    """يكتب الفيديو مباشرة على القرص بدون تحميله كله في الذاكرة."""
    limit = max_mb * 1_048_576
    try:
        length = int(h.headers.get("Content-Length", "0"))
    except ValueError:
        raise s.ApiError(400, "حجم الملف غير صحيح")
    if length <= 0:
        raise s.ApiError(400, "أرسل ملف الفيديو")
    if length > limit:
        raise s.ApiError(413, f"حجم الفيديو أكبر من {max_mb} ميقا")
    remaining = length
    with target.open("wb") as f:
        while remaining > 0:
            chunk = h.rfile.read(min(1_048_576, remaining))
            if not chunk:
                break
            f.write(chunk)
            remaining -= len(chunk)
    if remaining:
        target.unlink(missing_ok=True)
        raise s.ApiError(400, "انقطع رفع الفيديو، حاول مرة ثانية")
    return length


def _ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe_seconds(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def trim_video(source: Path, target: Path, start: float, length: float) -> bool:
    """قص سريع بدون إعادة ترميز، مع تجهيز الملف للتشغيل المباشر."""
    try:
        done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(source), "-t", f"{length:.2f}",
                               "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(target)],
                              capture_output=True, timeout=180)
        ok = done.returncode == 0 and target.exists() and target.stat().st_size > 0
        got = probe_seconds(target) if ok else None
        if ok and got is not None and abs(got - length) <= 1.5:
            return True
        # القص السريع يتقيد بالإطارات المفتاحية؛ إذا طلع غير دقيق نعيد الترميز بدقة.
        done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(source), "-t", f"{length:.2f}",
                               "-vf", "scale='min(1080,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target)],
                              capture_output=True, timeout=600)
        return done.returncode == 0 and target.exists() and target.stat().st_size > 0
    except (subprocess.SubprocessError, OSError):
        return False


LOGO = Path(__file__).resolve().parent / "assets" / "images" / "khdoom_logo.png"


def optimize_video(source: Path) -> Path | None:
    """يضغط الفيديو لجودة الجوال (720p) ويجهزه للتشغيل الفوري؛ يرجع الملف الجديد."""
    if not _ffmpeg() or not source.exists():
        return None
    target = source.with_name(source.stem + "_m.mp4")
    temp = target.with_suffix(".part.mp4")
    try:
        done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source),
                               "-vf", "scale='if(gt(iw,ih),-2,min(720,iw))':'if(gt(iw,ih),min(720,ih),-2)'",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-maxrate", "2M", "-bufsize", "4M",
                               "-pix_fmt", "yuv420p", "-g", "60", "-c:a", "aac", "-b:a", "96k", "-ac", "2",
                               "-movflags", "+faststart", str(temp)],
                              capture_output=True, timeout=900)
        if done.returncode == 0 and temp.exists() and temp.stat().st_size > 0:
            temp.replace(target)
            return target
    except (subprocess.SubprocessError, OSError):
        pass
    temp.unlink(missing_ok=True)
    return None


def _optimize_row(c, row) -> bool:
    source = _file_path(row)
    if not source or not source.exists():
        return False
    result = optimize_video(source)
    if not result:
        return False
    c.execute("UPDATE video_ads SET file_name=?,size_bytes=?,content_type='video/mp4',optimized=1 WHERE id=?",
              (result.name, result.stat().st_size, row["id"]))
    for old in (source, source.with_name("wm_" + source.stem + ".mp4")):
        old.unlink(missing_ok=True)
    return True


_backfill_started = False


def start_backfill(s) -> None:
    """الفيديوهات القديمة تنضغط مرة وحدة بالخلفية بدون ما يعيد المشترك رفعها."""
    global _backfill_started
    if _backfill_started or not _ffmpeg():
        return
    _backfill_started = True

    def run():
        try:
            with s.db() as c:
                rows = c.execute("SELECT id,file_name FROM video_ads WHERE deleted=0 AND optimized=0 AND file_name<>'' ORDER BY id").fetchall()
            for row in rows:
                with s.db() as c:
                    _optimize_row(c, row)
                    c.commit()
        except Exception as error:  # الضغط تحسين؛ ما يوقف السيرفر
            print("video_ads backfill:", error, flush=True)

    import threading
    threading.Thread(target=run, daemon=True).start()


def watermarked(row) -> Path | None:
    """نسخة التنزيل عليها شعار خدوم في الزاوية؛ تنحفظ بعد أول تنزيل."""
    source = _file_path(row)
    if not source or not source.exists():
        return None
    target = source.with_name("wm_" + source.stem + ".mp4")
    if target.exists():
        return target
    if not _ffmpeg() or not LOGO.exists():
        return source
    temp = target.with_suffix(".part.mp4")
    try:
        done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-i", str(LOGO), "-filter_complex",
                               "[1:v][0:v]scale2ref=w=iw*0.16:h=ow/mdar[logo][base];[logo]format=rgba,colorchannelmixer=aa=0.85[l];[base][l]overlay=W-w-W*0.04:H-h-H*0.04",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "copy", "-movflags", "+faststart", str(temp)],
                              capture_output=True, timeout=600)
        if done.returncode == 0 and temp.exists():
            temp.replace(target)
            return target
    except (subprocess.SubprocessError, OSError):
        pass
    temp.unlink(missing_ok=True)
    return source


def _link_sig(c, ident: int, expires: int) -> str:
    row = c.execute("SELECT value FROM video_ad_settings WHERE key='link_secret'").fetchone()
    secret = (row["value"] if row else "") or "khdoom"
    return hmac.new(secret.encode(), f"{ident}:{expires}".encode(), hashlib.sha256).hexdigest()[:40]


def client(h, method, s) -> bool:
    path = urlparse(h.path).path.rstrip("/")
    if not path.startswith("/api/video-ads"):
        return False
    q = {k: v[0] for k, v in parse_qs(urlparse(h.path).query).items()}
    start_backfill(s)
    try:
        public = re.fullmatch(r"/api/video-ads/dl/([0-9]+)/([0-9]+)/([0-9a-f]{40})", path)
        if public and method == "GET":
            ident, expires = int(public[1]), int(public[2])
            with s.db() as c:
                if expires < time.time() or not hmac.compare_digest(public[3], _link_sig(c, ident, expires)):
                    raise s.ApiError(403, "رابط التنزيل انتهى، اضغط تنزيل مرة ثانية")
                row = c.execute("SELECT id,file_name,content_type FROM video_ads WHERE id=? AND deleted=0", (ident,)).fetchone()
            if not row:
                raise s.ApiError(404, "الفيديو غير متاح")
            path_wm = watermarked(row)
            if not path_wm:
                raise s.ApiError(404, "الفيديو غير متاح")
            _send_file(h, {"id": row["id"], "file_name": path_wm.name, "content_type": "video/mp4"}, s, download=True)
            return True
        # تشغيل/تنزيل الفيديو يحتاج تسجيل الدخول (المشغل يرسل ترويسة Authorization).
        match = re.fullmatch(r"/api/video-ads/([0-9]+)/(file|download)", path)
        if match and method == "GET":
            with s.db() as c:
                user = h._user(c)
                row = c.execute("SELECT id,organization_id,file_name,content_type,status,allow_download FROM video_ads WHERE id=? AND deleted=0", (int(match[1]),)).fetchone()
                if not row or (row["status"] != "published" and row["organization_id"] != user["organization_id"]):
                    raise s.ApiError(404, "الفيديو غير متاح")
                if match[2] == "download" and not row["allow_download"] and row["organization_id"] != user["organization_id"]:
                    raise s.ApiError(403, "المعلن لم يسمح بتنزيل هذا الفيديو")
            _send_file(h, row, s, download=match[2] == "download")
            return True
        with s.db() as c:
            user = h._user(c)
            org = user["organization_id"]
            expire_due(c)
            r = path[len("/api/video-ads"):].lstrip("/")
            if r == "feed" and method == "GET":
                activity = (q.get("activity") or "").strip()
                sql = ("SELECT v.*,o.name organization_name,o.phone org_phone FROM video_ads v JOIN organizations o ON o.id=v.organization_id "
                       "WHERE v.deleted=0 AND v.status='published' AND (v.expires_at IS NULL OR v.expires_at>?)")
                args = [stamp()]
                if activity:
                    sql += " AND v.activity=?"; args.append(activity[:60])
                sql += " ORDER BY v.published_at DESC LIMIT 50"
                items = [_public(x, x["org_phone"] or "", org_package(c, x["organization_id"])) for x in c.execute(sql, args).fetchall()]
                _attach_likes(c, items, org)
                result = {"items": items}
            elif re.fullmatch(r"[0-9]+/like", r) and method == "POST":
                ident = int(r.split("/")[0])
                if not c.execute("SELECT id FROM video_ads WHERE id=? AND deleted=0 AND status='published'", (ident,)).fetchone():
                    raise s.ApiError(404, "الإعلان غير متاح")
                if c.execute("SELECT 1 FROM video_ad_likes WHERE video_ad_id=? AND organization_id=?", (ident, org)).fetchone():
                    c.execute("DELETE FROM video_ad_likes WHERE video_ad_id=? AND organization_id=?", (ident, org))
                    liked = False
                else:
                    c.execute("INSERT INTO video_ad_likes(video_ad_id,organization_id,created_at) VALUES(?,?,?)", (ident, org, stamp()))
                    liked = True
                count = c.execute("SELECT COUNT(*) n FROM video_ad_likes WHERE video_ad_id=?", (ident,)).fetchone()["n"]
                result = {"liked": liked, "likes": int(count)}
            elif r == "quota" and method == "GET":
                result = quota(c, org)
            elif r == "mine" and method == "GET":
                rows = c.execute("SELECT * FROM video_ads WHERE organization_id=? AND deleted=0 ORDER BY id DESC", (org,)).fetchall()
                mine_items = [{**_public(x), "status": x["status"], "reviewNote": x["review_note"], "paid": bool(x["paid"])} for x in rows]
                _attach_likes(c, mine_items, org)
                result = {"items": mine_items,
                          "quota": quota(c, org)}
            elif r == "" and method == "POST":
                if user["role"] != "admin":
                    raise s.ApiError(403, "إنشاء الإعلانات للمالك أو المدير فقط")
                cfg = settings(c)
                if not cfg["enabled"]:
                    raise ValueError("إعلانات الفيديو موقفة مؤقتًا")
                d = h._body()
                seconds = admin.number(d.get("seconds", 0), 0, 100000)
                if seconds > cfg["max_seconds"] + 0.5:
                    raise ValueError(f"المدة القصوى للفيديو {cfg['max_seconds']} ثانية؛ قص الفيديو أولًا")
                content_type = str(d.get("contentType", "video/mp4"))
                if content_type not in VIDEO_TYPES:
                    raise ValueError("نوع الفيديو غير مدعوم")
                now = stamp()
                overlay_y = admin.number(d.get("overlayY", 0.15), 0, 1)
                cur = c.execute("INSERT INTO video_ads(organization_id,title,caption,overlay_text,activity,contact,allow_download,content_type,seconds,status,created_at,overlay_y) VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?)",
                                (org, str(d.get("title", "")).strip()[:80], str(d.get("caption", "")).strip()[:300], str(d.get("overlayText", "")).strip()[:120],
                                 str(d.get("activity", "")).strip()[:60], str(d.get("contact", "")).strip()[:40], int(d.get("allowDownload", True) is not False),
                                 content_type, seconds, now, overlay_y))
                ident = cur.lastrowid
                if ident is None:
                    ident = c.execute("SELECT MAX(id) n FROM video_ads WHERE organization_id=?", (org,)).fetchone()["n"]
                result = {"id": ident, "uploadUrl": f"/api/video-ads/{ident}/upload"}
            elif re.fullmatch(r"[0-9]+/upload", r) and method in ("PUT", "POST"):
                ident = int(r.split("/")[0])
                row = c.execute("SELECT * FROM video_ads WHERE id=? AND organization_id=? AND deleted=0", (ident, org)).fetchone()
                if not row or row["status"] != "draft":
                    raise ValueError("المسودة غير موجودة")
                cfg = settings(c)
                try:
                    start = max(0.0, float(q.get("start", 0) or 0))
                    end = float(q["end"]) if q.get("end") else None
                except ValueError:
                    raise ValueError("بداية ونهاية القص غير صحيحة")
                wants_trim = end is not None
                # الفيديو الطويل يرتفع كامل والسيرفر يقصه؛ لذلك حده أكبر.
                suffix = VIDEO_TYPES.get(row["content_type"], ".mp4")
                raw_path = video_dir() / f"raw_{ident}_{os.urandom(6).hex()}{suffix}"
                size = _read_upload(h, s, max(cfg["max_upload_mb"], 200) if wants_trim else cfg["max_upload_mb"], raw_path)
                final = video_dir() / f"v{ident}_{os.urandom(6).hex()}{suffix}"
                seconds = None
                if wants_trim and _ffmpeg():
                    length = end - start
                    if length <= 0 or length > cfg["max_seconds"] + 0.5:
                        raw_path.unlink(missing_ok=True)
                        raise ValueError(f"اختار مقطع مدته {cfg['max_seconds']} ثانية أو أقل")
                    if not trim_video(raw_path, final, start, length):
                        raw_path.unlink(missing_ok=True)
                        raise ValueError("تعذر قص الفيديو، جرّب فيديو ثاني")
                    raw_path.unlink(missing_ok=True)
                else:
                    raw_path.replace(final)
                seconds = probe_seconds(final)
                if seconds is not None and seconds > cfg["max_seconds"] + 1.5:
                    final.unlink(missing_ok=True)
                    raise ValueError(f"المدة القصوى للفيديو {cfg['max_seconds']} ثانية؛ قص الفيديو أولًا")
                if not wants_trim and final.stat().st_size > cfg["max_upload_mb"] * 1_048_576:
                    final.unlink(missing_ok=True)
                    raise ValueError(f"حجم الفيديو أكبر من {cfg['max_upload_mb']} ميقا")
                optimized = optimize_video(final)
                if optimized:
                    final.unlink(missing_ok=True)
                    final = optimized
                old = row
                c.execute("UPDATE video_ads SET file_name=?,size_bytes=?,seconds=COALESCE(?,seconds),optimized=?,content_type=? WHERE id=?",
                          (final.name, final.stat().st_size, seconds, 1 if optimized else 0, "video/mp4" if optimized else row["content_type"], ident))
                if old["file_name"]:
                    _remove_file(old)
                _trim_saved(c, org, keep=ident)
                result = {"saved": True, "id": ident, "seconds": seconds}
            elif re.fullmatch(r"[0-9]+/download-link", r) and method == "POST":
                ident = int(r.split("/")[0])
                row = c.execute("SELECT id,organization_id,status,allow_download FROM video_ads WHERE id=? AND deleted=0", (ident,)).fetchone()
                if not row or (row["status"] != "published" and row["organization_id"] != org):
                    raise s.ApiError(404, "الفيديو غير متاح")
                if not row["allow_download"] and row["organization_id"] != org:
                    raise s.ApiError(403, "المعلن لم يسمح بتنزيل هذا الفيديو")
                expires = int(time.time()) + 900
                result = {"url": f"/api/video-ads/dl/{ident}/{expires}/{_link_sig(c, ident, expires)}"}
            elif re.fullmatch(r"[0-9]+/submit", r) and method == "POST":
                result = _submit(c, s, h, org, int(r.split("/")[0]))
            elif re.fullmatch(r"[0-9]+", r) and method == "DELETE":
                ident = int(r)
                row = c.execute("SELECT * FROM video_ads WHERE id=? AND organization_id=? AND deleted=0", (ident, org)).fetchone()
                if not row:
                    raise ValueError("الإعلان غير موجود")
                if row["status"] == "pending":
                    # طلب ملغى لا ينحسب من العدد.
                    c.execute("UPDATE video_ad_submissions SET counted=0 WHERE video_ad_id=? AND id=(SELECT MAX(id) FROM video_ad_submissions WHERE video_ad_id=?)", (ident, ident))
                c.execute("UPDATE video_ads SET deleted=1,status='deleted' WHERE id=?", (ident,))
                _remove_file(row)
                admin.audit(c, "المشترك", "video_ad_deleted_by_subscriber", json.dumps({"id": ident, "organization": org, "was": row["status"]}))
                result = {"deleted": True}
            else:
                raise s.ApiError(404, "المسار غير موجود")
            c.commit()
        h._send(200, result)
        return True
    except ValueError as error:
        raise s.ApiError(400, str(error))


def _submit(c, s, h, org, ident) -> dict:
    row = c.execute("SELECT * FROM video_ads WHERE id=? AND organization_id=? AND deleted=0", (ident, org)).fetchone()
    if not row:
        raise ValueError("الإعلان غير موجود")
    if row["status"] not in ("draft", "expired", "rejected"):
        raise ValueError("الإعلان مرسل أو منشور بالفعل")
    if not row["file_name"]:
        raise ValueError("ارفع الفيديو أولًا")
    d = h._body()
    info = quota(c, org)
    paid = 0
    price = 0.0
    transfer = ""
    if info["can_create"] and info["remaining"] > 0 and d.get("pay") is not True:
        pass
    else:
        transfer = str(d.get("transferName", "")).strip()[:80]
        if d.get("pay") is not True or not transfer:
            if info["can_create"]:
                raise ValueError(f"خلصت إعلاناتك لهذا الشهر ({info['used']} من {info['limit']}). تقدر تدفع {info['paid_price_sar']:g} ريال للإعلان")
            raise ValueError(f"إعلانات الفيديو لمشتركي VIP. رقّ باقتك أو ادفع {info['paid_price_sar']:g} ريال للإعلان")
        paid, price = 1, float(info["paid_price_sar"])
    now = stamp()
    c.execute("UPDATE video_ads SET status='pending',submitted_at=?,paid=?,price_sar=?,transfer_name=?,review_note='',published_at=NULL,expires_at=NULL,duration_key='' WHERE id=?",
              (now, paid, price, transfer, ident))
    c.execute("INSERT INTO video_ad_submissions(organization_id,video_ad_id,paid,counted,created_at) VALUES(?,?,?,1,?)", (org, ident, paid, now))
    admin.audit(c, "المشترك", "video_ad_submitted", json.dumps({"id": ident, "organization": org, "paid": paid}))
    return {"submitted": True, "quota": quota(c, org)}


def _trim_saved(c, org, keep) -> None:
    """كل مؤسسة تحتفظ بحد أقصى من الفيديوهات؛ الزائد يحذف الأقدم غير المنشور/المنتظر."""
    limit = settings(c)["max_saved"]
    rows = c.execute("SELECT * FROM video_ads WHERE organization_id=? AND deleted=0 AND file_name<>'' ORDER BY id DESC", (org,)).fetchall()
    extra = len(rows) - limit
    for row in reversed(rows):
        if extra <= 0:
            break
        if row["id"] == keep or row["status"] in ("pending", "published"):
            continue
        c.execute("UPDATE video_ads SET deleted=1,status='deleted' WHERE id=?", (row["id"],))
        _remove_file(row)
        extra -= 1
