"""Owner-only service monitoring. Never stores request text, tokens or customer data."""
import json
from contextlib import contextmanager
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

_lock = threading.Lock()
_path = None
_stop = threading.Event()
_run_lock = threading.Lock()
_worker = None
SERVICES = {'server': 'السيرفر', 'database': 'قاعدة البيانات', 'openai': 'OpenAI / موظفو AI', 'whatsapp': 'WhatsApp', 'calls': 'المكالمات', 'push': 'الإشعارات'}


def configure(path):
    global _path
    _path = Path(path)
    _path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS checks(service TEXT PRIMARY KEY,status TEXT,detail TEXT,metrics TEXT,checked REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,service TEXT,ok INTEGER,created REAL,kind TEXT);
        CREATE INDEX IF NOT EXISTS events_time ON events(created);
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,service TEXT,status TEXT,message TEXT,created REAL);
        ''')


@contextmanager
def _connect():
    c = sqlite3.connect(str(_path), timeout=5)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def record(service, ok, kind='request'):
    if _path is None or service not in SERVICES:
        return
    try:
        with _lock, _connect() as c:
            previous = c.execute('SELECT ok FROM events WHERE service=? AND kind=? ORDER BY id DESC LIMIT 1', (service, kind)).fetchone()
            c.execute('INSERT INTO events(service,ok,created,kind) VALUES(?,?,?,?)', (service, int(ok), time.time(), kind))
            if kind == 'request' and ((not ok and (not previous or previous['ok'])) or (ok and previous and not previous['ok'])):
                c.execute('INSERT INTO alerts(service,status,message,created) VALUES(?,?,?,?)', (service, 'ok' if ok else 'error', 'تعافت محاولات الخدمة الفعلية.' if ok else 'فشلت محاولة فعلية للخدمة؛ راجع حالة المزود.', time.time()))
    except Exception:
        # Monitoring must not fail a customer operation.
        pass


def save_check(service, status, detail, metrics=None):
    with _lock, _connect() as c:
        old = c.execute('SELECT status FROM checks WHERE service=?', (service,)).fetchone()
        c.execute('INSERT INTO checks VALUES(?,?,?,?,?) ON CONFLICT(service) DO UPDATE SET status=excluded.status,detail=excluded.detail,metrics=excluded.metrics,checked=excluded.checked',
                  (service, status, detail, json.dumps(metrics or {}), time.time()))
        if (old and old['status'] != status) or (not old and status in ('error', 'warning')):
            c.execute('INSERT INTO alerts(service,status,message,created) VALUES(?,?,?,?)', (service, status, detail, time.time()))


def _number(name):
    try:
        value = float(os.environ.get(name, ''))
        return value if value > 0 and value < float('inf') else None
    except ValueError:
        return None


def probe_ai():
    import ai_core
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key or not key.isascii():
        return 'error', 'مفتاح AI غير مهيأ أو غير صالح؛ لم يرسل طلب تجريبي.', {}
    payload = {'model': ai_core.PRIMARY_MODEL, 'input': 'Reply with OK only.', 'max_output_tokens': 32, 'store': False}
    started = time.monotonic()
    try:
        req = Request(ai_core.RESPONSES_URL, data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urlopen(req, timeout=20) as response:
            result = json.load(response)
        if not ai_core.ResponsesClient.output_text(result):
            raise ValueError('empty response')
        record('openai', True, 'probe')
        return 'ok', 'نجح طلب تجريبي فعلي. الرصيد المالي غير متاح من هذا الفحص.', {'latencyMs': round((time.monotonic()-started)*1000), 'model': ai_core.PRIMARY_MODEL, 'balance': None}
    except HTTPError as error:
        record('openai', False, 'probe')
        details = {401: 'مفتاح AI مرفوض', 403: 'صلاحيات AI غير كافية', 429: 'رفض المزود الطلب بسبب الحصة أو معدل الطلبات'}
        return 'error', details.get(error.code, 'فشل طلب AI لدى المزود') + ' (HTTP ' + str(error.code) + ')', {'balance': None}
    except Exception:
        record('openai', False, 'probe')
        return 'error', 'تعذر إكمال طلب AI التجريبي أو وصل رد فارغ.', {'balance': None}


def run_checks(db_factory, db_path, postgres=False, port=None):
    if not _run_lock.acquire(blocking=False):
        return
    try:
        server_status, server_detail = 'unknown', 'لم يُفحص HTTP بعد.'
        if port is not None:
            try:
                with urlopen('http://127.0.0.1:' + str(port) + '/health', timeout=5) as response:
                    if json.load(response).get('status') != 'ok':
                        raise ValueError('health')
                server_status, server_detail = 'ok', 'نجح فحص HTTP الداخلي؛ تعطل الاستضافة الكامل يحتاج مراقبًا خارجيًا.'
            except Exception:
                server_status, server_detail = 'error', 'فشل فحص HTTP الداخلي للخادم.'
        try:
            with db_factory() as c:
                c.execute('SELECT 1').fetchone()
                size = c.execute('SELECT pg_database_size(current_database()) AS size').fetchone()['size'] if postgres else sum(p.stat().st_size for p in (Path(db_path), Path(str(db_path)+'-wal')) if p.exists())
            cap = _number('KHDOOM_DATABASE_LIMIT_BYTES')
            percent = round(size/cap*100, 1) if cap else None
            status = 'error' if percent is not None and percent >= 100 else 'warning' if percent is not None and percent >= 80 else 'ok'
            save_check('database', status, 'نجح استعلام قاعدة البيانات.' + (' اقتربت المساحة من الحد المحدد.' if status != 'ok' else ''), {'usedBytes': size, 'limitBytes': cap, 'usedPercent': percent, 'limitSource': 'حد يضبطه المالك' if cap else 'غير محدد'})
        except Exception:
            save_check('database', 'error', 'فشل الاتصال أو قياس مساحة قاعدة البيانات.')
        try:
            usage = shutil.disk_usage(Path(db_path).parent)
            percent = round(usage.used / usage.total * 100, 1)
            old_status = server_status
            status = 'error' if old_status == 'error' or percent >= 95 else 'warning' if percent >= 80 else old_status
            save_check('server', status, ('مساحة القرص مرتفعة. ' if percent >= 80 else '') + server_detail, {'diskUsedBytes': usage.used, 'diskTotalBytes': usage.total, 'diskUsedPercent': percent, 'subscriptionRemaining': None})
        except OSError:
            save_check('server', 'error' if server_status == 'error' else 'warning', server_detail + ' تعذر قياس مساحة قرص الخادم.')
        for name, label in [('whatsapp', 'واتساب'), ('calls', 'المكالمات')]:
            save_check(name, 'unconfigured', 'مزود ' + label + ' غير مربوط بالمراقبة؛ الرصيد وحالة المؤسسات غير متاحين.', {'balance': None, 'remainingDays': None})
        with _connect() as c:
            push = c.execute("SELECT ok,created FROM events WHERE service='push' ORDER BY id DESC LIMIT 1").fetchone()
            ai = c.execute("SELECT checked FROM checks WHERE service='openai'").fetchone()
        if not os.environ.get('KHDOOM_VAPID_PRIVATE_KEY') or not os.environ.get('KHDOOM_VAPID_PUBLIC_KEY'):
            save_check('push', 'unconfigured', 'مفاتيح Push غير مكتملة.')
        elif push and time.time()-push['created'] < 86400:
            save_check('push', 'ok' if push['ok'] else 'error', 'آخر محاولة إرسال قُبلت من مزود Push؛ لا يثبت وصولها للجهاز.' if push['ok'] else 'فشلت آخر محاولة إرسال Push.')
        else:
            save_check('push', 'unknown', 'Push مهيأ؛ لا توجد محاولة حديثة تؤكد عمله. لا نرسل إشعارات اختبار للعملاء.')
        if not ai or time.time()-ai['checked'] >= (_number('KHDOOM_AI_PROBE_INTERVAL_SECONDS') or 3600):
            save_check('openai', *probe_ai())
        with _lock, _connect() as c:
            c.execute('DELETE FROM events WHERE created<?', (time.time()-30*86400,))
            c.execute('DELETE FROM alerts WHERE created<?', (time.time()-90*86400,))
    finally:
        _run_lock.release()


def snapshot():
    now = time.time()
    with _lock, _connect() as c:
        rows = {r['service']: dict(r) for r in c.execute('SELECT * FROM checks')}
        alerts = [dict(r) for r in c.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 100')]
        services = []
        for key, name in SERVICES.items():
            row = rows.get(key, {'service': key, 'status': 'unknown', 'detail': 'بانتظار أول فحص.', 'metrics': '{}', 'checked': None})
            row['name'] = name
            row['metrics'] = json.loads(row['metrics'])
            stale_after = max(120, (_number('KHDOOM_AI_PROBE_INTERVAL_SECONDS') or 3600)*2) if key == 'openai' else max(120, (_number('KHDOOM_MONITOR_INTERVAL_SECONDS') or 300)*2)
            row['stale'] = row['checked'] is None or now-row['checked'] > stale_after
            if row['stale']:
                row['status'] = 'unknown'
                row['detail'] = 'نتيجة الفحص قديمة أو غير متاحة؛ لا تؤكد الحالة الحالية.'
            stats = c.execute('SELECT COUNT(*) AS requests,SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failures,MAX(CASE WHEN ok=1 THEN created END) AS lastSuccess FROM events WHERE service=? AND created>=?', (key, now-86400)).fetchone()
            row['requests24h'] = stats['requests']; row['failures24h'] = stats['failures'] or 0; row['lastSuccess'] = stats['lastSuccess']
            # Recent real failures must be visible before the next synthetic check.
            latest = c.execute("SELECT ok,created FROM events WHERE service=? AND kind='request' ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            if latest and not latest['ok'] and now-latest['created'] < 300:
                row['status'] = 'error'; row['detail'] = 'فشلت آخر محاولة فعلية خلال آخر خمس دقائق.'
            services.append(row)
    return {'services': services, 'alerts': alerts, 'generatedAt': now, 'externalMonitorRequired': True}


def start(db_factory, db_path, postgres=False, port=None):
    global _worker
    if _worker and _worker.is_alive():
        return
    configure(os.environ.get('KHDOOM_MONITOR_DB', str(Path(db_path).with_name('service-monitor.db'))))
    def loop():
        while not _stop.wait(2 if not _worker_ready[0] else max(60, _number('KHDOOM_MONITOR_INTERVAL_SECONDS') or 300)):
            _worker_ready[0] = True
            try:
                run_checks(db_factory, db_path, postgres, port)
            except Exception:
                # Next interval retries without taking down the API.
                pass
    _worker_ready = [False]
    _worker = threading.Thread(target=loop, name='service-monitor', daemon=True)
    _worker.start()
