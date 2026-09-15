"""Small, additive tenant-integrity migrations shared by the API.

The application already scopes most queries with ``organization_id``.  This
module adds the missing denormalized scope to chat messages and the indexes
needed for high-volume tenant lookups without changing existing records.
"""

import re


def migrate(connection, *, postgres=False):
    """Add tenant scope to chat messages and repair legacy rows safely."""
    if postgres:
        columns = {
            row["column_name"]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=?",
                ("chat_messages",),
            ).fetchall()
        }
        if "organization_id" not in columns:
            connection.execute(
                "ALTER TABLE chat_messages ADD COLUMN organization_id BIGINT"
            )
    else:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(chat_messages)")}
        if "organization_id" not in columns:
            connection.execute("ALTER TABLE chat_messages ADD COLUMN organization_id INTEGER")

    # Every message belongs to exactly the tenant owning its session.  The
    # update is deliberately constrained so malformed legacy rows remain
    # visible for diagnostics rather than being assigned to a guessed tenant.
    connection.execute(
        """UPDATE chat_messages
           SET organization_id=(SELECT organization_id FROM chat_sessions s WHERE s.id=chat_messages.session_id)
           WHERE organization_id IS NULL"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant_session "
        "ON chat_messages(organization_id,session_id,id)"
    )

    # Fast and deterministic inbound call routing.  Uniqueness is enforced at
    # the application boundary because existing deployments may contain old
    # duplicate values that need an explicit owner decision before a unique
    # database constraint can be added.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_connections_phone "
        "ON call_connections(phone_number)"
    )
    for row in connection.execute("SELECT organization_id,phone_number FROM call_connections").fetchall():
        normalized = re.sub(r"[^0-9]", "", str(row["phone_number"] or ""))
        if normalized.startswith("00"):
            normalized = normalized[2:]
        if normalized and normalized != row["phone_number"]:
            connection.execute(
                "UPDATE call_connections SET phone_number=? WHERE organization_id=?",
                (normalized, row["organization_id"]),
            )


def validate_message_scope(connection, message_id):
    """Return false when a message and its session belong to different tenants."""
    row = connection.execute(
        """SELECT m.organization_id message_org,s.organization_id session_org
           FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id
           WHERE m.id=?""",
        (message_id,),
    ).fetchone()
    return bool(row and row["message_org"] == row["session_org"])
