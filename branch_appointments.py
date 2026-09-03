"""Branch-scoped public chat links and inbox authorization."""
import json
import re
import secrets


def migrate(connection, postgres=False):
    connection.execute('''CREATE TABLE IF NOT EXISTS branch_chat_links (
        organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        branch_id TEXT NOT NULL, branch_name TEXT NOT NULL,
        public_token TEXT NOT NULL UNIQUE,
        PRIMARY KEY (organization_id, branch_id))''')
    for table in ('chat_sessions', 'appointment_requests'):
        if postgres:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS branch_id TEXT NOT NULL DEFAULT 'main'")
        elif 'branch_id' not in {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')}:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN branch_id TEXT NOT NULL DEFAULT 'main'")


def authorized_branch(user, requested, error):
    branch = str(requested or 'main')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', branch):
        raise error(400, 'معرف الفرع غير صحيح')
    if user['role'] != 'admin':
        assigned = json.loads(user['permissions'] or '{}').get('branch_id', 'main')
        if branch != assigned:
            raise error(403, 'لا يمكنك الوصول إلى طلبات فرع آخر')
    return branch


def resolve_chat(connection, token):
    row = connection.execute('''SELECT o.id,o.name,o.activity,o.phone,s.package,
        'main' AS branch_id FROM organizations o JOIN subscriptions s
        ON s.organization_id=o.id WHERE o.public_chat_token=?''', (token,)).fetchone()
    if row is not None:
        return dict(row)
    row = connection.execute('''SELECT o.id,o.name,o.activity,o.phone,s.package,
        b.branch_id,b.branch_name FROM branch_chat_links b
        JOIN organizations o ON o.id=b.organization_id
        JOIN subscriptions s ON s.organization_id=o.id WHERE b.public_token=?''', (token,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['name'] += ' — ' + result['branch_name']
    return result


def chat_link(connection, organization_id, branch_id, name):
    if branch_id == 'main':
        return connection.execute('SELECT public_chat_token FROM organizations WHERE id=?', (organization_id,)).fetchone()['public_chat_token']
    connection.execute('''INSERT INTO branch_chat_links(organization_id,branch_id,branch_name,public_token)
        VALUES(?,?,?,?) ON CONFLICT(organization_id,branch_id) DO NOTHING''',
        (organization_id, branch_id, name, secrets.token_urlsafe(24)))
    return connection.execute('SELECT public_token FROM branch_chat_links WHERE organization_id=? AND branch_id=?', (organization_id, branch_id)).fetchone()['public_token']
