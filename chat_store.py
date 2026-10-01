"""إدخال رسائل المحادثة مع رقم المؤسسة؛ نفس منطق server.insert_chat_message بدون استيراد server."""


def insert_chat_message(connection, organization_id, session_id, sender, message, created_at):
    try:
        has_scope = bool(connection.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=?",
            ("chat_messages", "organization_id"),
        ).fetchone())
    except Exception:
        has_scope = any(row[1] == "organization_id" for row in connection.execute("PRAGMA table_info(chat_messages)"))
    if has_scope:
        return connection.execute(
            "INSERT INTO chat_messages(organization_id,session_id,sender,message,created_at) VALUES(?,?,?,?,?)",
            (organization_id, session_id, sender, message, created_at),
        )
    return connection.execute(
        "INSERT INTO chat_messages(session_id,sender,message,created_at) VALUES(?,?,?,?)",
        (session_id, sender, message, created_at),
    )
