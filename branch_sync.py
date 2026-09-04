"""Versioned, tenant-scoped synchronization primitives.

History lives in the application database. It is NOT an independent backup.
No local preferences or credentials are accepted as a bulk payload.
"""
import json
import re
from datetime import datetime, timezone

COLLECTIONS = {
    'branches': {'name'},
    'vehicles': {'name', 'plate', 'model', 'year', 'status', 'employeeId', 'employeeName', 'notes', 'attachments'},
    'employees': {'name', 'jobTitle', 'phone', 'email', 'notes', 'attachments'},
    'appointments': {'title', 'type', 'customer', 'phone', 'notes', 'date', 'status', 'assignedEmployeeId', 'attachments'},
    'documents': {'title', 'number', 'issuer', 'issueDate', 'expiryDate', 'website', 'notes', 'attachments'},
    'organization': {'name', 'activity', 'phone', 'notes', 'attachments'},
}
PERMISSIONS = {
    'vehicles': ('viewVehicles', 'editVehicles'),
    'employees': ('viewEmployees', 'manageEmployees'),
    'appointments': ('viewAppointments', 'manageAppointments'),
}


def migrate(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS branch_sync_records (
        organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        branch_id TEXT NOT NULL, collection TEXT NOT NULL, record_id TEXT NOT NULL,
        revision BIGINT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY (organization_id,branch_id,collection,record_id))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS branch_sync_history (
        organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        branch_id TEXT NOT NULL, collection TEXT NOT NULL, record_id TEXT NOT NULL,
        revision BIGINT NOT NULL, deleted INTEGER NOT NULL,
        payload TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY (organization_id,branch_id,collection,record_id,revision))''')


def identifier(value, error):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value):
        raise error(400, 'معرف المزامنة غير صحيح')
    return value


def authorize(user, branch, collection, write, error, *, deleted=False):
    identifier(branch, error)
    if collection not in COLLECTIONS:
        raise error(400, 'نوع البيانات غير مدعوم للمزامنة')
    if user['role'] == 'admin':
        return
    try:
        permissions = json.loads(user['permissions'] or '{}')
        if not isinstance(permissions, dict):
            permissions = {}
    except (TypeError, ValueError):
        permissions = {}
    if permissions.get('branch_id', 'main') != branch:
        raise error(403, 'لا يمكنك مزامنة بيانات فرع آخر')
    names = PERMISSIONS.get(collection)
    if write and deleted:
        delete_permission = {'vehicles':'deleteVehicles','employees':'deleteEmployees','appointments':'manageAppointments'}.get(collection)
        if delete_permission and permissions.get(delete_permission) is True:
            return
        raise error(403, 'ليس لديك صلاحية حذف هذه البيانات')
    if names and (permissions.get(names[1]) is True or
                  not write and permissions.get(names[0]) is True):
        return
    raise error(403, 'ليس لديك صلاحية لمزامنة هذه البيانات')


def validate_payload(collection, payload, error):
    if not isinstance(payload, dict) or set(payload) - COLLECTIONS[collection]:
        raise error(400, 'تحتوي البيانات على حقول غير مسموحة للمزامنة')
    for key, value in payload.items():
        if key == 'attachments':
            # File transfer is intentionally not yet enabled. Never sync device paths.
            if value != []:
                raise error(400, 'مزامنة المرفقات ليست مفعلة بعد')
        elif value is not None and (not isinstance(value, str) or len(value) > 8000):
            raise error(400, 'قيمة حقل المزامنة غير صحيحة أو طويلة جدًا')
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > 65536:
        raise error(413, 'حجم السجل أكبر من المسموح')
    return encoded


def project(row):
    return {'id': row['record_id'], 'revision': row['revision'],
            'deleted': bool(row['deleted']), 'data': json.loads(row['payload']),
            'updatedAt': row['updated_at']}


def list_records(connection, user, branch, collection, after, error):
    authorize(user, branch, collection, False, error)
    if after:
        identifier(after, error)
    rows = connection.execute('''SELECT * FROM branch_sync_records
        WHERE organization_id=? AND branch_id=? AND collection=? AND record_id>?
        ORDER BY record_id LIMIT 201''', (user['organization_id'], branch, collection, after)).fetchall()
    return {'records': [project(row) for row in rows[:200]],
            'nextCursor': rows[199]['record_id'] if len(rows) > 200 else None}


def save_record(connection, user, branch, collection, record_id, body, error):
    authorize(user, branch, collection, True, error, deleted=isinstance(body, dict) and body.get('deleted') is True)
    identifier(record_id, error)
    if not isinstance(body, dict) or set(body) - {'baseRevision', 'deleted', 'data'}:
        raise error(400, 'طلب مزامنة غير صحيح')
    base = body.get('baseRevision')
    deleted = body.get('deleted', False)
    if type(base) is not int or base < 0 or type(deleted) is not bool:
        raise error(400, 'إصدار المزامنة غير صحيح')
    encoded = validate_payload(collection, body.get('data', {}), error)
    if deleted and encoded != '{}':
        raise error(400, 'طلب الحذف لا يحتوي بيانات')
    identity = (user['organization_id'], branch, collection, record_id)
    timestamp = datetime.now(timezone.utc).isoformat()
    # Unique insert/CAS update serialize competing writers on both SQLite and PostgreSQL.
    if base == 0:
        cursor = connection.execute('''INSERT INTO branch_sync_records
            (organization_id,branch_id,collection,record_id,revision,deleted,payload,updated_at)
            VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(organization_id,branch_id,collection,record_id) DO NOTHING''',
            (*identity, int(deleted), encoded, timestamp))
    else:
        cursor = connection.execute('''UPDATE branch_sync_records
            SET revision=revision+1,deleted=?,payload=?,updated_at=?
            WHERE organization_id=? AND branch_id=? AND collection=? AND record_id=? AND revision=?''',
            (int(deleted), encoded, timestamp, *identity, base))
    row = connection.execute('''SELECT * FROM branch_sync_records
        WHERE organization_id=? AND branch_id=? AND collection=? AND record_id=?''', identity).fetchone()
    if cursor.rowcount != 1:
        # A lost response may be retried safely only if the current result is identical.
        if row and row['revision'] == base + 1 and row['payload'] == encoded and bool(row['deleted']) == deleted:
            return project(row)
        raise error(409, 'السجل تغيّر على جهاز آخر؛ حمّل النسختين واختر التعديل المناسب')
    connection.execute('''INSERT INTO branch_sync_history
        (organization_id,branch_id,collection,record_id,revision,deleted,payload,updated_at)
        VALUES(?,?,?,?,?,?,?,?)''', (*identity, row['revision'], row['deleted'], row['payload'], row['updated_at']))
    return project(row)


def history(connection, user, branch, collection, record_id, error):
    authorize(user, branch, collection, False, error)
    identifier(record_id, error)
    rows = connection.execute('''SELECT * FROM branch_sync_history
        WHERE organization_id=? AND branch_id=? AND collection=? AND record_id=?
        ORDER BY revision DESC LIMIT 100''', (user['organization_id'], branch, collection, record_id)).fetchall()
    return [project(row) for row in rows]
