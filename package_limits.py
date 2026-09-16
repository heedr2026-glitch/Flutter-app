"""Persistent, owner-configurable resource limits. Null means unlimited."""

DEFAULT_LIMITS = {
    "free": {"employees": 1, "vehicles": 1, "users": 1, "branches": 1, "organization_notifications": 5, "employee_notifications": 5},
    "basic": {"employees": 5, "vehicles": 5, "users": 5, "branches": 3, "organization_notifications": 30, "employee_notifications": 30},
    "vip": {"employees": None, "vehicles": None, "users": None, "branches": None, "organization_notifications": None, "employee_notifications": None},
}

RESOURCES = ("employees", "vehicles", "users", "branches", "organization_notifications", "employee_notifications")


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
        str(row["name"]) for row in connection.execute("PRAGMA table_info(package_resource_limits)").fetchall()
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
            result.setdefault(row["package"], dict(DEFAULT_LIMITS["vip"]))
    except Exception:
        # The limits table is initialized before the main schema on fresh installs.
        pass
    for row in connection.execute("SELECT * FROM package_resource_limits").fetchall():
        if row["package"] not in result:
            result[row["package"]] = dict(DEFAULT_LIMITS["vip"])
        values = result[row["package"]]
        for resource in RESOURCES:
            if resource in row.keys() and row[resource] is not None:
                values[resource] = row[resource]
        values["users"] = values.get("users", values.get("employees"))
        values["employees"] = values.get("employees", values.get("users"))
        if row["package"] == "vip":
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
    for values in payload.values():
        if not isinstance(values, dict):
            raise ValueError("بيانات حدود الباقة غير صحيحة")
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
        branches = values.get("branches", existing["branches"] if existing and "branches" in existing.keys() else DEFAULT_LIMITS.get(package, DEFAULT_LIMITS["vip"]).get("branches"))
        org_notifications = values.get("organization_notifications", existing["organization_notifications"] if existing and "organization_notifications" in existing.keys() else DEFAULT_LIMITS.get(package, DEFAULT_LIMITS["vip"]).get("organization_notifications"))
        employee_notifications = values.get("employee_notifications", existing["employee_notifications"] if existing and "employee_notifications" in existing.keys() else DEFAULT_LIMITS.get(package, DEFAULT_LIMITS["vip"]).get("employee_notifications"))
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
