"""Persistent, owner-configurable resource limits. Null means unlimited."""

DEFAULT_LIMITS = {
    "free": {"employees": 1, "vehicles": 1},
    "basic": {"employees": 5, "vehicles": 5},
    "vip": {"employees": None, "vehicles": None},
}


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS package_resource_limits (
        package TEXT PRIMARY KEY,
        employees INTEGER NOT NULL CHECK (employees >= 0),
        vehicles INTEGER NOT NULL CHECK (vehicles >= 0)
    )""")


def read_limits(connection):
    result = {name: dict(values) for name, values in DEFAULT_LIMITS.items()}
    for row in connection.execute("SELECT package,employees,vehicles FROM package_resource_limits").fetchall():
        if row["package"] in ("free", "basic"):
            result[row["package"]] = {
                "employees": row["employees"], "vehicles": row["vehicles"]
            }
    return result


def save_limits(connection, payload):
    if not isinstance(payload, dict) or set(payload) != {"free", "basic"}:
        raise ValueError("يجب تحديد حدود الباقة المجانية والأساسية")
    # Validate the complete update before issuing any writes.
    for values in payload.values():
        if not isinstance(values, dict) or set(values) != {"employees", "vehicles"}:
            raise ValueError("حدد عدد الموظفين والمركبات لكل باقة")
        for value in values.values():
            if type(value) is not int or not 0 <= value <= 100000:
                raise ValueError("الحد يجب أن يكون عددًا صحيحًا من 0 إلى 100000")
    for package, values in payload.items():
        connection.execute(
            """INSERT INTO package_resource_limits(package,employees,vehicles)
               VALUES(?,?,?) ON CONFLICT(package) DO UPDATE SET
               employees=excluded.employees,vehicles=excluded.vehicles""",
            (package, values["employees"], values["vehicles"]),
        )
    return read_limits(connection)
