"""Persistent, owner-configurable resource limits. Null means unlimited."""

DEFAULT_LIMITS = {
    "free": {"employees": 1, "vehicles": 1, "users": 1, "branches": 1, "organization_notifications": 5, "employee_notifications": 5},
    "basic": {"employees": 5, "vehicles": 5, "users": 5, "branches": 3, "organization_notifications": 30, "employee_notifications": 30},
    "vip": {"employees": None, "vehicles": None, "users": None, "branches": None, "organization_notifications": None, "employee_notifications": None},
}

RESOURCES = ("employees", "vehicles", "users", "branches", "organization_notifications", "employee_notifications")


def _row_value(row, name, index=None):
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index] if index is not None else None


def _row_has(row, name):
    try:
        return name in row.keys()
    except AttributeError:
        return False


def initialize(connection):
    postgres = hasattr(connection, "_connection")
    connection.execute("""CREATE TABLE IF NOT EXISTS package_resource_limits (
        package TEXT PRIMARY KEY,
        employees INTEGER NOT NULL CHECK (employees >= 0),
        vehicles INTEGER NOT NULL CHECK (vehicles >= 0)
    )""")
    columns = ({str(row["column_name"]) for row in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?",
        ("package_resource_limits",),
    ).fetchall()} if postgres else {
        str(row[1]) for row in connection.execute("PRAGMA table_info(package_resource_limits)").fetchall()
    })
    # SQLite and PostgreSQL both accept these additive migrations; no data is removed.
    definitions = {
        "users": "INTEGER",
        "branches": "INTEGER",
        "organization_notifications": "INTEGER",
        "employee_notifications": "INTEGER",
    }
    for name, definition in definitions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE package_resource_limits ADD COLUMN {'IF NOT EXISTS ' if postgres else ''}{name} {definition}")


def read_limits(connection):
    result = {name: dict(values) for name, values in DEFAULT_LIMITS.items()}
    try:
        for row in connection.execute("SELECT package FROM platform_packages").fetchall():
            result.setdefault(_row_value(row, "package", 0), dict(DEFAULT_LIMITS["vip"]))
    except Exception:
        # The limits table is initialized before the main schema on fresh installs.
        pass
    for row in connection.execute("SELECT * FROM package_resource_limits").fetchall():
        package = _row_value(row, "package", 0)
        if package not in result:
            result[package] = dict(DEFAULT_LIMITS["vip"])
        values = result[package]
        for resource in RESOURCES:
            index = {"employees": 1, "vehicles": 2, "users": 3, "branches": 4,
                     "organization_notifications": 5, "employee_notifications": 6}[resource]
            raw = _row_value(row, resource, index)
            if (_row_has(row, resource) or not hasattr(row, "keys")) and raw is not None:
                values[resource] = raw
        values["users"] = values.get("users", values.get("employees"))
        values["employees"] = values.get("employees", values.get("users"))
        if package == "vip":
            # Legacy employees/vehicles columns are NOT NULL; 100000 is the
            # storage sentinel for the unlimited VIP setting.
            if values.get("employees") == 100000:
                values["employees"] = None
            if values.get("vehicles") == 100000:
                values["vehicles"] = None
            if values.get("users") == 100000:
                values["users"] = None
    return result


def limit(connection, package, resource):
    values = read_limits(connection).get(package) or DEFAULT_LIMITS["vip"]
    return values.get(resource, values.get("employees") if resource == "users" else None)


def save_limits(connection, payload):
    if not isinstance(payload, dict) or not payload:
        raise ValueError("حدد حدود باقة واحدة على الأقل")
    # Validate the complete update before issuing any writes.
    for package, values in payload.items():
        if not isinstance(values, dict):
            raise ValueError("بيانات حدود الباقة غير صحيحة")
        if package not in DEFAULT_LIMITS:
            # أسماء الباقات ثابتة؛ أي اسم آخر يُرفض حتى لا يُخزَّن نص عشوائي يُعرض لاحقًا في صفحات الإدارة.
            raise ValueError("اسم الباقة غير معروف")
        if "employees" not in values and "users" not in values:
            raise ValueError("حدد عدد المستخدمين لكل باقة")
        if "vehicles" not in values:
            raise ValueError("حدد عدد المركبات لكل باقة")
        for value in values.values():
            if value is not None and (type(value) is not int or not 0 <= value <= 100000):
                raise ValueError("الحد يجب أن يكون عددًا صحيحًا من 0 إلى 100000")
    for package, values in payload.items():
        existing = connection.execute("SELECT * FROM package_resource_limits WHERE package=?", (package,)).fetchone()
        users = values.get("users", values.get("employees"))
        employees = values.get("employees", users)
        defaults = DEFAULT_LIMITS.get(package, DEFAULT_LIMITS["vip"])
        branches = values.get("branches", _row_value(existing, "branches", 4) if existing else defaults.get("branches"))
        org_notifications = values.get("organization_notifications", _row_value(existing, "organization_notifications", 5) if existing else defaults.get("organization_notifications"))
        employee_notifications = values.get("employee_notifications", _row_value(existing, "employee_notifications", 6) if existing else defaults.get("employee_notifications"))
        stored_employees = 100000 if employees is None else employees
        stored_vehicles = 100000 if values["vehicles"] is None else values["vehicles"]
        connection.execute(
            """INSERT INTO package_resource_limits(package,employees,vehicles,users,branches,organization_notifications,employee_notifications)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(package) DO UPDATE SET
               employees=excluded.employees,vehicles=excluded.vehicles,users=excluded.users,
               branches=excluded.branches,organization_notifications=excluded.organization_notifications,
               employee_notifications=excluded.employee_notifications""",
            (package, stored_employees, stored_vehicles, 100000 if users is None else users, branches, org_notifications, employee_notifications),
        )
    return read_limits(connection)


# ---------------------------------------------------------------------------
# حدود خاصة بمؤسسة واحدة: تتقدّم على حد الباقة لهذه المؤسسة فقط.
# عدم وجود صف = تتبع حد الباقة. القيمة NULL = بلا حد.
ORG_RESOURCES = ("employees", "vehicles", "branches")
_UNLIMITED = -1


def initialize_org(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS organization_resource_limits (
        organization_id INTEGER NOT NULL,
        resource TEXT NOT NULL,
        value INTEGER NOT NULL,
        PRIMARY KEY (organization_id, resource)
    )""")


def org_overrides(connection, organization_id):
    try:
        rows = connection.execute(
            "SELECT resource, value FROM organization_resource_limits WHERE organization_id=?",
            (organization_id,),
        ).fetchall()
    except Exception:
        return {}
    result = {}
    for row in rows:
        value = _row_value(row, "value", 1)
        result[_row_value(row, "resource", 0)] = None if value == _UNLIMITED else int(value)
    return result


def effective_limit(connection, package, resource, organization_id=None):
    """حد المؤسسة الخاص إن وُجد، وإلا حد الباقة. None = بلا حد."""
    if organization_id is not None and resource in ORG_RESOURCES:
        overrides = org_overrides(connection, organization_id)
        if resource in overrides:
            return overrides[resource]
    return limit(connection, package, resource)


def save_org_limits(connection, organization_id, payload):
    """payload: {resource: int | "unlimited" | None}; None يعني الرجوع لحد الباقة."""
    if not isinstance(payload, dict):
        raise ValueError("بيانات الحدود غير صحيحة")
    cleaned = {}
    for resource, value in payload.items():
        if resource not in ORG_RESOURCES:
            raise ValueError("نوع الحد غير معروف")
        if value is None or value == "":
            cleaned[resource] = None
        elif value == "unlimited":
            cleaned[resource] = _UNLIMITED
        elif type(value) is int and 0 <= value <= 100000:
            cleaned[resource] = value
        else:
            raise ValueError("الحد يجب أن يكون عددًا من 0 إلى 100000 أو بلا حد")
    for resource, value in cleaned.items():
        connection.execute(
            "DELETE FROM organization_resource_limits WHERE organization_id=? AND resource=?",
            (organization_id, resource),
        )
        if value is not None:
            connection.execute(
                "INSERT INTO organization_resource_limits(organization_id, resource, value) VALUES(?,?,?)",
                (organization_id, resource, value),
            )
    return org_overrides(connection, organization_id)
