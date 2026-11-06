"""Departments as the Admin Management console manages them.

The console reads the same HR tables the organization chart and Role &
Permissions already write. A unit registered here, a position opened on it or
a manager named for it is immediately the one every other screen sees, so no
department, position, role or permission is copied into a second register.

Accounts are placed by ``users.department_id`` and ``users.position_id``, and a
linked employee row follows the same placement. When an account has not been
placed directly, the staff record it is linked to is the placement - the same
rule the permission resolver applies - and the console shows that source so an
administrator knows which row a move will change.
"""

from core.access import role_granted_permissions
from core.access import role_permission_set
from core.config import ALL_PERMISSION_CATEGORIES
from core.config import PERMISSION_HIRING_RIGHTS
from core.config import ROLE_LABEL
from flask import url_for
from services.department_access import bind_role
from services.department_access import unbind_role
from services.letters import LETTER_VIEW_BASE
from services.letters import stored_letter_role_permissions
from services.department_tasks import department_task_view
from services.notifications import deliver_notification
from services.org import POSITION_LEVEL_LABEL
from services.org import build_department_tree
from services.org import clean_department_position
from services.org import clean_unit_type
from services.org import department_ancestor_ids
from services.org import sync_department_head
from services.recruitment import role_rights
import sqlite3


LEVEL_CHOICES = ((-1, "Every rung"),) + tuple(
    sorted(POSITION_LEVEL_LABEL.items()))


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(form, name, limit=300):
    return (form.get(name) or "").strip()[:limit]


def _count(db, sql, params=()):
    """One count, or zero when the table is not in this build yet."""
    try:
        return db.execute(sql, params).fetchone()[0] or 0
    except sqlite3.Error:
        return 0


def department_row(db, department_id):
    row = db.execute("SELECT * FROM departments WHERE id = ?",
                     (department_id,)).fetchone()
    if row is None:
        raise ValueError("That department no longer exists.")
    return row


def position_row(db, department_id, position_id):
    row = db.execute(
        "SELECT * FROM organizational_positions WHERE id = ?",
        (position_id,)).fetchone()
    if row is None:
        raise ValueError("That position no longer exists.")
    if row["department_id"] != department_id:
        raise ValueError("That position belongs to another department.")
    return row


def department_role(db, department_id, role_key):
    row = db.execute("SELECT * FROM roles WHERE key = ?",
                     (role_key,)).fetchone()
    if row is None:
        raise ValueError("That role no longer exists.")
    role_department = (int(row["department_id"] or 0)
                       if "department_id" in row.keys() else 0)
    if role_department != department_id:
        raise ValueError("That role is filed under another department.")
    return row


def _parent_map(units):
    return {unit["id"]: unit["parent_id"] for unit in units}


def _branch_chain(department_id, parents):
    chain, seen = [], set()
    cursor = department_id
    while cursor and cursor not in seen:
        seen.add(cursor)
        chain.append(cursor)
        cursor = parents.get(cursor)
    return chain


def _descendant_ids(department_id, units):
    children = {}
    for unit in units:
        children.setdefault(unit["parent_id"], []).append(unit["id"])
    found, stack = set(), list(children.get(department_id, ()))
    while stack:
        cursor = stack.pop()
        if cursor in found:
            continue
        found.add(cursor)
        stack.extend(children.get(cursor, ()))
    return found


def _role_modules(db, role_key, cache):
    if role_key not in cache:
        cache[role_key] = sorted(role_permission_set(role_key, db))
    return cache[role_key]


def _role_permissions(db, role_key, cache):
    """The catalogue keys a role grants, including its hiring-derived rows."""
    if role_key not in cache:
        keys = set(role_granted_permissions(db, role_key))
        rights = role_rights(db, role_key)
        for key, needed in PERMISSION_HIRING_RIGHTS.items():
            if set(needed) <= rights:
                keys.add(key)
        cache[role_key] = keys
    return cache[role_key]


def _role_letters(db, role_key, modules, cache):
    if role_key not in cache:
        has_rows, letters = stored_letter_role_permissions(db, role_key)
        if not has_rows and "letters" in modules:
            letters = set(LETTER_VIEW_BASE)
        cache[role_key] = set(letters)
    return cache[role_key]


def _load(db):
    """Every row the console needs, read once per page."""
    units = [dict(row) for row in db.execute("""
        SELECT d.id, d.name, d.description,
               COALESCE(d.is_active, 1) AS is_active,
               d.leadership_position, d.parent_id, d.head_employee_id,
               COALESCE(d.unit_type, 'Department') AS unit_type,
               d.created_at, p.name AS parent_name,
               e.full_name AS head_name, e.job_title AS head_title,
               (SELECT u.id FROM users AS u WHERE u.employee_id = e.id
                ORDER BY u.id LIMIT 1) AS head_account_id
        FROM departments AS d
        LEFT JOIN departments AS p ON p.id = d.parent_id
        LEFT JOIN employees AS e ON e.id = d.head_employee_id
        ORDER BY d.name COLLATE NOCASE
    """).fetchall()]

    accounts = [dict(row) for row in db.execute("""
        SELECT u.id, u.full_name, u.username, u.email, u.status, u.role,
               u.department_id, u.position_id, u.employee_id,
               d.name AS department_name,
               p.title AS position_title, p.level AS position_level,
               e.department AS employee_department,
               ep.title AS employee_position_title,
               ep.level AS employee_position_level,
               COALESCE(u.department_id, ep.department_id, ed.id)
                   AS effective_department_id,
               COALESCE(p.level, ep.level) AS effective_position_level
        FROM users AS u
        LEFT JOIN departments AS d ON d.id = u.department_id
        LEFT JOIN organizational_positions AS p ON p.id = u.position_id
        LEFT JOIN employees AS e ON e.id = u.employee_id
        LEFT JOIN organizational_positions AS ep ON ep.id = e.position_id
        LEFT JOIN departments AS ed ON ed.name = e.department COLLATE NOCASE
        ORDER BY u.full_name COLLATE NOCASE
    """).fetchall()]
    for account in accounts:
        if account["department_id"]:
            account["placement"] = "Assigned"
            account["placement_label"] = account["department_name"] or ""
        elif account["effective_department_id"]:
            account["placement"] = "Staff record"
            account["placement_label"] = account["employee_department"] or ""
        else:
            account["placement"] = "Unplaced"
            account["placement_label"] = ""
        account["position_label"] = (account["position_title"]
                                     or account["employee_position_title"] or "")

    employees = [dict(row) for row in db.execute("""
        SELECT e.id, e.full_name, e.job_title, e.department,
               COALESCE(e.employment_status, 'Active') AS employment_status,
               e.position_id, p.title AS position_title,
               p.level AS position_level,
               p.department_id AS position_department_id,
               d.id AS name_department_id,
               (SELECT u.id FROM users AS u WHERE u.employee_id = e.id
                ORDER BY u.id LIMIT 1) AS account_id,
               (SELECT u.username FROM users AS u WHERE u.employee_id = e.id
                ORDER BY u.id LIMIT 1) AS account_username
        FROM employees AS e
        LEFT JOIN organizational_positions AS p ON p.id = e.position_id
        LEFT JOIN departments AS d ON d.name = e.department COLLATE NOCASE
        ORDER BY CASE WHEN COALESCE(e.employment_status, 'Active') = 'Active'
                      THEN 0 ELSE 1 END, e.full_name COLLATE NOCASE
    """).fetchall()]
    for employee in employees:
        employee["effective_department_id"] = (
            employee["position_department_id"] or employee["name_department_id"])

    positions = [dict(row) for row in db.execute("""
        SELECT id, title, department_id, level, reports_to_position_id,
               COALESCE(is_active, 1) AS is_active, created_at
        FROM organizational_positions
        ORDER BY level, title COLLATE NOCASE
    """).fetchall()]
    position_titles = {row["id"]: row["title"] for row in positions}

    bindings = [dict(row) for row in db.execute("""
        SELECT b.department_id, b.role_key, b.position_level,
               r.name AS role_name, COALESCE(r.active, 1) AS role_active,
               COALESCE(r.is_system, 0) AS is_system
        FROM department_roles AS b
        LEFT JOIN roles AS r ON r.key = b.role_key
        ORDER BY b.department_id, b.role_key
    """).fetchall()]

    roles = [dict(row) for row in db.execute("""
        SELECT r.key, r.name, r.description, COALESCE(r.is_system, 0) AS is_system,
               COALESCE(r.is_standard, 0) AS is_standard,
               COALESCE(r.active, 1) AS active,
               COALESCE(r.department_id, 0) AS department_id,
               d.name AS department_name
        FROM roles AS r
        LEFT JOIN departments AS d ON d.id = r.department_id
        WHERE r.key <> 'admin'
        ORDER BY r.name COLLATE NOCASE
    """).fetchall()]
    role_names = {row["key"]: row["name"] for row in db.execute(
        "SELECT key, name FROM roles").fetchall()}

    return {
        "units": units,
        "accounts": accounts,
        "employees": employees,
        "positions": positions,
        "position_titles": position_titles,
        "bindings": bindings,
        "roles": roles,
        "role_names": role_names,
    }


def _reaches(accounts, parents, department_id, level):
    """How many active seats a binding on this unit and rung reaches now."""
    reached = 0
    for account in accounts:
        if account["status"] != "Active" or account["role"] == "admin":
            continue
        if department_id:
            chain = _branch_chain(account["effective_department_id"], parents)
            if department_id not in chain:
                continue
        if level != -1 and account["effective_position_level"] != level:
            continue
        reached += 1
    return reached


def _binding_view(binding, source, data, caches):
    role_key = binding["role_key"]
    role_name = binding.get("role_name") or data["role_names"].get(
        role_key, role_key)
    modules = _role_modules(caches["db"], role_key, caches["modules"])
    permissions = _role_permissions(caches["db"], role_key,
                                    caches["permissions"])
    letters = _role_letters(caches["db"], role_key, modules,
                            caches["letters"])
    level = binding["position_level"]
    return {
        "role_key": role_key,
        "role_name": role_name,
        "role_active": bool(binding["role_active"]),
        "is_system": bool(binding["is_system"]),
        "level": level,
        "level_label": ("Every rung" if level == -1
                        else POSITION_LEVEL_LABEL.get(level, "Staff")),
        "source": source,
        "modules": modules,
        "permission_count": len(permissions),
        "letter_count": len(letters),
        "reaches": _reaches(data["accounts"], data["parents"],
                            binding["department_id"], level),
    }


def _manager_candidates(data, unit_by_id):
    """Every staff record the manager picker can name, active seats first."""
    candidates = []
    for employee in data["employees"]:
        department_label = (employee["department"]
                            or unit_by_id.get(
                                employee["effective_department_id"], {}
                            ).get("name", ""))
        candidates.append({
            "id": employee["id"],
            "name": employee["full_name"],
            "title": employee["job_title"] or employee["position_title"] or "",
            "department": department_label,
            "active": employee["employment_status"] == "Active",
        })
    return candidates


def department_management_snapshot(db, selected_id=None):
    """The whole console: the unit tree, list counts and one unit's detail."""
    data = _load(db)
    units = data["units"]
    parents = _parent_map(units)
    data["parents"] = parents
    unit_by_id = {unit["id"]: unit for unit in units}

    accounts_by_dept = {}
    for account in data["accounts"]:
        if account["status"] != "Active":
            continue
        department_id = account["effective_department_id"]
        if department_id:
            accounts_by_dept[department_id] = accounts_by_dept.get(
                department_id, 0) + 1
    staff_by_dept = {}
    for employee in data["employees"]:
        if employee["employment_status"] != "Active":
            continue
        department_id = employee["effective_department_id"]
        if department_id:
            staff_by_dept[department_id] = staff_by_dept.get(
                department_id, 0) + 1

    positions_by_dept = {}
    for position in data["positions"]:
        bucket = positions_by_dept.setdefault(
            position["department_id"], {"total": 0, "active": 0})
        bucket["total"] += 1
        if position["is_active"]:
            bucket["active"] += 1

    roles_by_dept = {}
    for role in data["roles"]:
        if role["department_id"]:
            roles_by_dept[role["department_id"]] = roles_by_dept.get(
                role["department_id"], 0) + 1

    bindings_by_dept = {}
    for binding in data["bindings"]:
        if binding["department_id"]:
            bindings_by_dept[binding["department_id"]] = bindings_by_dept.get(
                binding["department_id"], 0) + 1

    children_count = {}
    for unit in units:
        if unit["parent_id"]:
            children_count[unit["parent_id"]] = children_count.get(
                unit["parent_id"], 0) + 1

    list_units = []
    for unit in units:
        position_count = positions_by_dept.get(
            unit["id"], {"total": 0, "active": 0})
        roles_here = roles_by_dept.get(unit["id"], 0)
        bindings_here = bindings_by_dept.get(unit["id"], 0)
        list_units.append({
            "id": unit["id"],
            "name": unit["name"],
            "description": unit["description"] or "",
            "unit_type": clean_unit_type(unit["unit_type"]),
            "is_active": bool(unit["is_active"]),
            "parent_id": unit["parent_id"],
            "parent_name": unit["parent_name"] or "",
            "leadership_position": (clean_department_position(
                unit["leadership_position"]) or "Director"),
            "head_employee_id": unit["head_employee_id"],
            "manager_name": unit["head_name"] or "",
            "manager_title": unit["head_title"] or "",
            "manager_account_id": unit["head_account_id"],
            "user_count": accounts_by_dept.get(unit["id"], 0),
            "staff_count": staff_by_dept.get(unit["id"], 0),
            "position_count": position_count["total"],
            "active_position_count": position_count["active"],
            "role_count": roles_here,
            "binding_count": bindings_here,
            "child_count": children_count.get(unit["id"], 0),
        })
    list_units.sort(key=lambda row: (not row["is_active"], row["name"].lower()))

    selected = None
    if selected_id is not None:
        selected = next((unit for unit in list_units
                         if unit["id"] == selected_id), None)
    if selected is None and list_units:
        selected = next((unit for unit in list_units if unit["is_active"]),
                        list_units[0])

    # Previous / next follow the registry order the table itself prints, so
    # the arrows step through the units exactly as they are listed.
    previous_unit = None
    next_unit = None
    unit_ordinal = None
    if selected is not None:
        index = list_units.index(selected)
        if index > 0:
            row = list_units[index - 1]
            previous_unit = {"id": row["id"], "name": row["name"]}
        if index + 1 < len(list_units):
            row = list_units[index + 1]
            next_unit = {"id": row["id"], "name": row["name"]}
        unit_ordinal = {"index": index + 1, "total": len(list_units)}

    detail = None
    if selected is not None:
        detail = _detail(db, selected, list_units, unit_by_id, data)

    role_groups = {}
    for role in data["roles"]:
        if not role["active"]:
            continue
        label = role["department_name"] or "Company-wide"
        role_groups.setdefault(label, []).append(role)
    ordered_groups = [
        {"label": label, "roles": role_groups[label]}
        for label in sorted(role_groups,
                            key=lambda name: (name != "Company-wide",
                                              name.lower()))
    ]

    active_accounts = [row for row in data["accounts"]
                       if row["status"] == "Active"]
    position_options = [{
        "id": position["id"],
        "title": position["title"],
        "department_id": position["department_id"],
        "department_name": unit_by_id.get(
            position["department_id"], {}).get("name", ""),
    } for position in data["positions"]
        if position["is_active"] and position["department_id"]]
    stats = {
        "units": len(list_units),
        "active_units": sum(1 for row in list_units if row["is_active"]),
        "inactive_units": sum(1 for row in list_units
                              if not row["is_active"]),
        "positions": len(data["positions"]),
        "active_positions": sum(1 for row in data["positions"]
                                if row["is_active"]),
        "roles": sum(1 for row in data["roles"] if row["department_id"]),
        "bindings": sum(1 for row in data["bindings"]
                        if row["department_id"]),
        "company_bindings": sum(1 for row in data["bindings"]
                                if not row["department_id"]),
        "accounts": sum(1 for row in active_accounts
                        if row["effective_department_id"]),
        "unplaced": sum(1 for row in active_accounts
                        if not row["effective_department_id"]),
        "staff": sum(1 for row in data["employees"]
                     if row["employment_status"] == "Active"),
    }

    # ------------------------------------------------------- admin register
    # An administrator is either an account whose primary role is admin (it
    # carries the whole catalogue by definition) or an account explicitly
    # granted admin_access through its own permission rows.
    admins = []
    admin_sql = """
        SELECT u.id, u.full_name, u.username, u.email, u.status, u.role,
               u.department_id, u.last_login, u.must_change_password,
               COALESCE(u.lock_state, 'unlocked') AS lock_state,
               u.lock_until,
               d.name AS department_name, p.title AS position_title,
               (SELECT COUNT(*) FROM user_permission_grants AS g
                 WHERE g.user_id = u.id AND g.permission = 'admin_access'
                   AND g.allowed = 1) AS granted_admin
        FROM users AS u
        LEFT JOIN departments AS d ON d.id = u.department_id
        LEFT JOIN organizational_positions AS p ON p.id = u.position_id
        WHERE u.role = 'admin'
           OR EXISTS (SELECT 1 FROM user_permission_grants AS g
                       WHERE g.user_id = u.id
                         AND g.permission = 'admin_access' AND g.allowed = 1)
        ORDER BY CASE WHEN u.role = 'admin' THEN 0 ELSE 1 END,
                 u.full_name COLLATE NOCASE
    """
    try:
        admin_rows = db.execute(admin_sql).fetchall()
    except sqlite3.Error:
        admin_rows = db.execute("""
            SELECT u.id, u.full_name, u.username, u.email, u.status, u.role,
                   u.department_id, u.last_login, u.must_change_password,
                   COALESCE(u.lock_state, 'unlocked') AS lock_state,
                   u.lock_until,
                   d.name AS department_name, p.title AS position_title,
                   0 AS granted_admin
            FROM users AS u
            LEFT JOIN departments AS d ON d.id = u.department_id
            LEFT JOIN organizational_positions AS p ON p.id = u.position_id
            WHERE u.role = 'admin'
            ORDER BY u.full_name COLLATE NOCASE
        """).fetchall()
    for row in admin_rows:
        entry = dict(row)
        entry["role_label"] = data["role_names"].get(
            entry["role"], ROLE_LABEL.get(entry["role"], entry["role"]))
        entry["basis"] = ("Administrator role" if entry["role"] == "admin"
                          else "Granted admin_access")
        admins.append(entry)

    return {
        "stats": stats,
        "units": list_units,
        "tree": build_department_tree(list_units),
        "selected": detail,
        "selected_id": selected["id"] if selected else None,
        "previous_unit": previous_unit,
        "next_unit": next_unit,
        "unit_ordinal": unit_ordinal,
        "admins": admins,
        "role_groups": ordered_groups,
        "level_choices": LEVEL_CHOICES,
        "manager_candidates": (detail["manager_candidates"] if detail
                               else _manager_candidates(data, unit_by_id)),
        "position_options": position_options,
    }


def _detail(db, unit, units, unit_by_id, data):
    department_id = unit["id"]
    parents = data["parents"]
    accounts = data["accounts"]
    positions = data["positions"]
    roles = data["roles"]
    role_names = data["role_names"]
    caches = {"db": db, "modules": {}, "permissions": {}, "letters": {}}

    children = [row for row in units if row["parent_id"] == department_id]

    # ------------------------------------------------------------ positions
    employees_by_position = {}
    for employee in data["employees"]:
        if employee["position_id"]:
            employees_by_position.setdefault(
                employee["position_id"], []).append(employee)
    accounts_by_position = {}
    for account in accounts:
        if account["position_id"]:
            accounts_by_position.setdefault(
                account["position_id"], []).append(account)
    reports_count = {}
    for position in positions:
        if position["reports_to_position_id"]:
            reports_count[position["reports_to_position_id"]] = (
                reports_count.get(position["reports_to_position_id"], 0) + 1)

    detail_positions = []
    for position in positions:
        if position["department_id"] != department_id:
            continue
        held_staff = employees_by_position.get(position["id"], [])
        held_accounts = accounts_by_position.get(position["id"], [])
        active_staff = [row for row in held_staff
                        if row["employment_status"] == "Active"]
        active_accounts = [row for row in held_accounts
                           if row["status"] == "Active"]
        child_count = reports_count.get(position["id"], 0)
        detail_positions.append({
            "id": position["id"],
            "title": position["title"],
            "level": position["level"],
            "level_label": POSITION_LEVEL_LABEL.get(position["level"], "Staff"),
            "reports_to_id": position["reports_to_position_id"],
            "reports_to_title": data["position_titles"].get(
                position["reports_to_position_id"], ""),
            "is_active": bool(position["is_active"]),
            "staff_count": len(active_staff),
            "account_count": len(active_accounts),
            "holder_names": [row["full_name"] for row in active_staff]
                             + [row["full_name"] for row in active_accounts],
            "child_count": child_count,
            "can_retire": not active_staff and not active_accounts,
            "can_delete": (not held_staff and not held_accounts
                           and not child_count),
            "created_at": position["created_at"],
        })

    # --------------------------------------------------------------- people
    accounts_here = [row for row in accounts
                     if row["effective_department_id"] == department_id]
    accounts_here.sort(key=lambda row: (row["status"] != "Active",
                                        row["full_name"].lower()))
    for account in accounts_here:
        account["role_label"] = role_names.get(
            account["role"], ROLE_LABEL.get(account["role"], account["role"]))

    staff_here = [row for row in data["employees"]
                  if row["employment_status"] == "Active"
                  and row["effective_department_id"] == department_id]
    staff_without_account = [row for row in staff_here
                             if not row["account_id"]]

    assignable = [row for row in accounts
                  if row["status"] == "Active"
                  and row["effective_department_id"] != department_id]
    assignable.sort(key=lambda row: (bool(row["effective_department_id"]),
                                     row["full_name"].lower()))

    # ----------------------------------------------------------------- roles
    primary_counts = {row["role"]: row["n"] for row in db.execute(
        "SELECT role, COUNT(*) AS n FROM users GROUP BY role").fetchall()}
    extra_counts = {row["role_key"]: row["n"] for row in db.execute(
        "SELECT role_key, COUNT(*) AS n FROM user_roles"
        " GROUP BY role_key").fetchall()}
    bound_counts = {row["role_key"]: row["n"] for row in db.execute(
        "SELECT role_key, COUNT(*) AS n FROM department_roles"
        " GROUP BY role_key").fetchall()}

    owned_roles = []
    for role in roles:
        if role["department_id"] != department_id:
            continue
        modules = _role_modules(db, role["key"], caches["modules"])
        permissions = _role_permissions(db, role["key"], caches["permissions"])
        letters = _role_letters(db, role["key"], modules, caches["letters"])
        owners = primary_counts.get(role["key"], 0) + extra_counts.get(
            role["key"], 0)
        bound = bound_counts.get(role["key"], 0)
        owned_roles.append({
            "key": role["key"],
            "name": role["name"],
            "description": role["description"] or "",
            "is_system": bool(role["is_system"]),
            "is_standard": bool(role["is_standard"]),
            "active": bool(role["active"]),
            "modules": modules,
            "granted": permissions,
            "letters": letters,
            "account_count": owners,
            "binding_count": bound,
            "can_delete": (not role["is_system"] and not role["is_standard"]
                           and not owners and not bound),
        })

    direct_bindings = []
    for binding in data["bindings"]:
        if binding["department_id"] == department_id:
            direct_bindings.append(_binding_view(
                binding, "This unit", data, caches))
    direct_bindings.sort(key=lambda row: row["role_name"].lower())

    inherited_bindings = []
    cursor = parents.get(department_id)
    seen = {department_id}
    while cursor and cursor not in seen:
        seen.add(cursor)
        source = unit_by_id.get(cursor, {}).get("name", "Parent unit")
        for binding in data["bindings"]:
            if binding["department_id"] == cursor:
                item = _binding_view(binding, source, data, caches)
                inherited_bindings.append(item)
        cursor = parents.get(cursor)
    for binding in data["bindings"]:
        if not binding["department_id"]:
            inherited_bindings.append(_binding_view(
                binding, "Company-wide", data, caches))
    inherited_bindings.sort(key=lambda row: (row["source"].lower(),
                                             row["role_name"].lower()))

    # The roles that actually hand this unit something right now: the ones
    # filed here and the active bindings that reach it from above.
    effective_roles = {}
    for role in owned_roles:
        if role["active"]:
            effective_roles[role["key"]] = role["name"]
    for binding in direct_bindings + inherited_bindings:
        if binding["role_active"]:
            effective_roles[binding["role_key"]] = binding["role_name"]

    permission_sources = {}
    for role_key, role_name in effective_roles.items():
        for permission in _role_permissions(db, role_key, caches["permissions"]):
            permission_sources.setdefault(permission, []).append(role_name)
    for names in permission_sources.values():
        names.sort(key=str.lower)

    permission_categories = []
    for category in ALL_PERMISSION_CATEGORIES:
        items, opened = [], 0
        for permission in category["permissions"]:
            sources = permission_sources.get(permission["key"], [])
            items.append({
                "key": permission["key"],
                "label": permission["label"],
                "description": permission["description"],
                "checked": bool(sources),
                "roles": sources,
            })
            if sources:
                opened += 1
        permission_categories.append({
            "key": category["key"],
            "label": category["label"],
            "icon": category["icon"],
            "note": category["note"],
            "permissions": items,
            "open": opened,
            "total": len(items),
        })
    permission_open = sum(row["open"] for row in permission_categories)
    permission_total = sum(row["total"] for row in permission_categories)

    letter_actions = set()
    for role_key in effective_roles:
        modules = _role_modules(db, role_key, caches["modules"])
        letter_actions |= _role_letters(db, role_key, modules, caches["letters"])

    # ------------------------------------------------------------- manager
    manager_row = None
    for employee in data["employees"]:
        if employee["id"] == unit_by_id[department_id].get("head_employee_id"):
            manager_row = employee
            break
    manager_candidates = _manager_candidates(data, unit_by_id)

    # ------------------------------------------------------- delete checks
    blockers = []
    if unit["child_count"]:
        blockers.append("{} sub-unit{}".format(
            unit["child_count"], "" if unit["child_count"] == 1 else "s"))
    if unit["position_count"]:
        blockers.append("{} position{}".format(
            unit["position_count"], "" if unit["position_count"] == 1 else "s"))
    direct_accounts = sum(1 for row in accounts
                          if row["department_id"] == department_id)
    if direct_accounts:
        blockers.append("{} assigned account{}".format(
            direct_accounts, "" if direct_accounts == 1 else "s"))
    if data["employees"]:
        staff_records = sum(
            1 for row in data["employees"]
            if row["effective_department_id"] == department_id)
        if staff_records:
            blockers.append("{} staff record{}".format(
                staff_records, "" if staff_records == 1 else "s"))
    if unit["role_count"]:
        blockers.append("{} role{} filed here".format(
            unit["role_count"], "" if unit["role_count"] == 1 else "s"))
    if unit["binding_count"]:
        blockers.append("{} role binding{}".format(
            unit["binding_count"], "" if unit["binding_count"] == 1 else "s"))
    linked_records = (
        _count(db, "SELECT COUNT(*) FROM job_vacancies WHERE department_id = ?",
               (department_id,))
        + _count(db, "SELECT COUNT(*) FROM budget_allocations"
                     " WHERE department_id = ?", (department_id,))
        + _count(db, "SELECT COUNT(*) FROM budget_requests"
                     " WHERE department_id = ?", (department_id,))
        + _count(db, "SELECT COUNT(*) FROM integration_department_mappings"
                     " WHERE hr_department_id = ?", (department_id,)))
    if linked_records:
        blockers.append("{} linked record{}".format(
            linked_records, "" if linked_records == 1 else "s"))

    blocked_parents = {department_id} | _descendant_ids(department_id, units)
    parent_choices = [row for row in units
                      if row["id"] not in blocked_parents]
    base = unit_by_id[department_id]

    return {
        "id": department_id,
        "name": unit["name"],
        "description": unit["description"],
        "unit_type": unit["unit_type"],
        "is_active": unit["is_active"],
        "parent_id": unit["parent_id"],
        "parent_name": unit["parent_name"],
        "leadership_position": (
            clean_department_position(base.get("leadership_position"))
            or "Director"),
        "created_at": base.get("created_at"),
        "head_employee_id": base.get("head_employee_id"),
        "manager": {
            "id": manager_row["id"] if manager_row else None,
            "name": manager_row["full_name"] if manager_row else "",
            "title": ((manager_row["job_title"]
                       or manager_row["position_title"] or "")
                      if manager_row else ""),
            "account_id": manager_row["account_id"] if manager_row else None,
        } if manager_row else None,
        "manager_candidates": manager_candidates,
        "children": children,
        "parent_choices": parent_choices,
        "positions": detail_positions,
        "positions_total": len(detail_positions),
        "positions_active": sum(1 for row in detail_positions
                                if row["is_active"]),
        "accounts": accounts_here,
        "account_count": sum(1 for row in accounts_here
                             if row["status"] == "Active"),
        "staff": staff_here,
        "staff_without_account": staff_without_account,
        "assignable_accounts": assignable,
        "owned_roles": owned_roles,
        "direct_bindings": direct_bindings,
        "inherited_bindings": inherited_bindings,
        "effective_role_count": len(effective_roles),
        "permission_categories": permission_categories,
        "permission_open": permission_open,
        "permission_total": permission_total,
        "tasks": department_task_view(db, department_id),
        "letter_actions": sorted(letter_actions),
        "blockers": blockers,
        "can_delete": not blockers,
    }


# ---------------------------------------------------------------- mutations
def create_department(db, form):
    name = _text(form, "name", 120)
    if len(name) < 2:
        raise ValueError("Give the department a name of at least two characters.")
    if db.execute("SELECT 1 FROM departments WHERE lower(name) = lower(?)",
                  (name,)).fetchone() is not None:
        raise ValueError("A department called {} already exists.".format(name))
    position = clean_department_position(form.get("leadership_position"))
    if not position:
        raise ValueError("Choose Executive, Director or Manager as the "
                         "department's leadership position.")
    unit_type = clean_unit_type(form.get("unit_type"))
    parent_id = _int(form.get("parent_id")) or None
    if parent_id and db.execute("SELECT 1 FROM departments WHERE id = ?",
                                (parent_id,)).fetchone() is None:
        raise ValueError("The selected parent unit does not exist.")
    head_id = _int(form.get("head_employee_id")) or None
    if head_id and db.execute("SELECT 1 FROM employees WHERE id = ?",
                              (head_id,)).fetchone() is None:
        raise ValueError("The selected manager does not exist.")
    description = _text(form, "description", 300)

    department_id = db.execute("""
        INSERT INTO departments
            (name, description, leadership_position, unit_type, parent_id)
        VALUES (?, ?, ?, ?, ?)
    """, (name, description, position, unit_type, parent_id)).lastrowid
    head_name = sync_department_head(
        db, {"id": department_id, "name": name}, head_id)
    if head_id:
        db.execute("UPDATE departments SET head_employee_id = ? WHERE id = ?",
                   (head_id, department_id))
    return {
        "notice": "Department {} registered{}.".format(
            name, " under its manager {}".format(head_name) if head_name else ""),
        "details": {"name": name, "unit_type": unit_type,
                    "leadership_position": position, "parent_id": parent_id,
                    "manager": head_name},
        "department_id": department_id,
    }


def update_department(db, department_id, form):
    current = department_row(db, department_id)
    name = _text(form, "name", 120)
    if len(name) < 2:
        raise ValueError("Give the department a name of at least two characters.")
    duplicate = db.execute(
        "SELECT 1 FROM departments WHERE lower(name) = lower(?) AND id <> ?",
        (name, department_id)).fetchone()
    if duplicate is not None:
        raise ValueError("Another department already uses that name.")
    position = clean_department_position(form.get("leadership_position"))
    if not position:
        raise ValueError("Choose Executive, Director or Manager as the "
                         "department's leadership position.")
    unit_type = clean_unit_type(form.get("unit_type"))
    parent_id = _int(form.get("parent_id")) or None
    head_id = _int(form.get("head_employee_id")) or None
    if parent_id == department_id:
        raise ValueError("A department cannot sit inside itself.")
    if parent_id:
        branch = db.execute("SELECT id, parent_id FROM departments").fetchall()
        if db.execute("SELECT 1 FROM departments WHERE id = ?",
                      (parent_id,)).fetchone() is None:
            raise ValueError("The selected parent unit does not exist.")
        if department_id in department_ancestor_ids(branch, parent_id):
            raise ValueError("A department cannot sit inside its own branch.")
    if head_id and db.execute("SELECT 1 FROM employees WHERE id = ?",
                              (head_id,)).fetchone() is None:
        raise ValueError("The selected manager does not exist.")
    description = _text(form, "description", 300)

    db.execute("""
        UPDATE departments
        SET name = ?, description = ?, leadership_position = ?, unit_type = ?,
            parent_id = ?, head_employee_id = ?
        WHERE id = ?
    """, (name, description, position, unit_type, parent_id, head_id,
          department_id))

    if (current["name"] or "") != name:
        # Employees store the department name, and several files keep a copy of
        # the link, so a rename has to reach every one of them.
        db.execute("UPDATE employees SET department = ? WHERE department = ?"
                   " COLLATE NOCASE", (name, current["name"]))
        for table in ("budget_allocations", "budget_requests"):
            db.execute("""
                UPDATE {0} SET department = ?, department_id = ?
                WHERE department_id = ? OR department = ? COLLATE NOCASE
            """.format(table), (name, department_id, department_id,
                                current["name"]))
        db.execute("""
            UPDATE job_vacancies SET department = ?, department_id = ?
            WHERE department_id = ? OR department = ? COLLATE NOCASE
        """, (name, department_id, department_id, current["name"]))
        db.execute("""
            UPDATE integration_department_mappings
            SET hr_department_name = ?, hr_department_id = ?
            WHERE hr_department_id = ? OR hr_department_name = ? COLLATE NOCASE
        """, (name, department_id, department_id, current["name"]))

    head_name = sync_department_head(
        db, {"id": department_id, "name": name}, head_id)
    return {
        "notice": "Department {} updated{}.".format(
            name, ", headed by {}".format(head_name) if head_name else ""),
        "details": {"name": name, "previous_name": current["name"],
                    "unit_type": unit_type, "parent_id": parent_id,
                    "leadership_position": position, "manager": head_name},
    }


def delete_department(db, department_id, confirm_name):
    current = department_row(db, department_id)
    if (confirm_name or "").strip().casefold() != (
            current["name"] or "").strip().casefold():
        raise ValueError("Type the department name exactly to confirm its "
                         "removal.")
    units = [dict(row) for row in db.execute(
        "SELECT id, name, parent_id FROM departments").fetchall()]
    blockers = []
    children = sum(1 for row in units if row["parent_id"] == department_id)
    if children:
        blockers.append("{} sub-unit(s)".format(children))
    positions = _count(db, "SELECT COUNT(*) FROM organizational_positions"
                           " WHERE department_id = ?", (department_id,))
    if positions:
        blockers.append("{} position(s)".format(positions))
    accounts = _count(db, "SELECT COUNT(*) FROM users WHERE department_id = ?",
                      (department_id,))
    if accounts:
        blockers.append("{} assigned account(s)".format(accounts))
    staff = _count(db, "SELECT COUNT(*) FROM employees"
                       " WHERE department = ? COLLATE NOCASE",
                   (current["name"],))
    if staff:
        blockers.append("{} staff record(s)".format(staff))
    roles = _count(db, "SELECT COUNT(*) FROM roles WHERE department_id = ?",
                   (department_id,))
    if roles:
        blockers.append("{} role(s) filed here".format(roles))
    bindings = _count(db, "SELECT COUNT(*) FROM department_roles"
                          " WHERE department_id = ?", (department_id,))
    if bindings:
        blockers.append("{} role binding(s)".format(bindings))
    linked = (
        _count(db, "SELECT COUNT(*) FROM job_vacancies WHERE department_id = ?",
               (department_id,))
        + _count(db, "SELECT COUNT(*) FROM budget_allocations"
                     " WHERE department_id = ?", (department_id,))
        + _count(db, "SELECT COUNT(*) FROM budget_requests"
                     " WHERE department_id = ?", (department_id,))
        + _count(db, "SELECT COUNT(*) FROM integration_department_mappings"
                     " WHERE hr_department_id = ?", (department_id,)))
    if linked:
        blockers.append("{} budget, vacancy or integration record(s)".format(
            linked))
    if blockers:
        raise ValueError(
            "{} still has {}. Move or remove them first, or deactivate the "
            "department instead - its records stay where they are.".format(
                current["name"], ", ".join(blockers)))
    db.execute("DELETE FROM department_roles WHERE department_id = ?",
               (department_id,))
    db.execute("DELETE FROM departments WHERE id = ?", (department_id,))
    return {
        "notice": "Department {} removed.".format(current["name"]),
        "details": {"name": current["name"]},
        "department_id": None,
    }


def set_department_status(db, department_id, active):
    current = department_row(db, department_id)
    wanted = bool(active)
    if bool(current["is_active"]) == wanted:
        return {"notice": "Department {} is already {}.".format(
                    current["name"], "active" if wanted else "inactive"),
                "changed": False}
    db.execute("UPDATE departments SET is_active = ? WHERE id = ?",
               (1 if wanted else 0, department_id))
    return {
        "notice": "Department {} {}.".format(
            current["name"], "activated" if wanted else "deactivated"),
        "details": {"name": current["name"], "active": wanted},
        "changed": True,
    }


def assign_manager(db, department_id, employee_id):
    current = department_row(db, department_id)
    head_id = _int(employee_id) or None
    head_name = ""
    if head_id:
        employee = db.execute("SELECT id FROM employees WHERE id = ?",
                              (head_id,)).fetchone()
        if employee is None:
            raise ValueError("The selected manager does not exist.")
        head_name = sync_department_head(
            db, {"id": department_id, "name": current["name"]}, head_id)
    db.execute("UPDATE departments SET head_employee_id = ? WHERE id = ?",
               (head_id, department_id))
    return {
        "notice": ("{} is now the manager of {}.".format(head_name,
                                                         current["name"])
                   if head_name else
                   "Manager cleared for {}.".format(current["name"])),
        "details": {"name": current["name"], "manager": head_name},
    }


def create_position(db, department_id, form):
    current = department_row(db, department_id)
    title = _text(form, "title", 120)
    if not title:
        raise ValueError("A position needs a title.")
    level = _int(form.get("level"), 4)
    if level is None or level < 0 or level > 4:
        level = 4
    reports_to = _int(form.get("reports_to_position_id")) or None
    if reports_to and db.execute(
            "SELECT 1 FROM organizational_positions WHERE id = ?",
            (reports_to,)).fetchone() is None:
        raise ValueError("The position it reports to does not exist.")
    if db.execute("""
            SELECT 1 FROM organizational_positions
            WHERE department_id = ? AND lower(title) = lower(?)
    """, (department_id, title)).fetchone() is not None:
        raise ValueError("{} already has a position called {}.".format(
            current["name"], title))
    try:
        position_id = db.execute("""
            INSERT INTO organizational_positions
                (title, department_id, level, reports_to_position_id)
            VALUES (?, ?, ?, ?)
        """, (title, department_id, level, reports_to)).lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("{} already has a position called {}.".format(
            current["name"], title))
    return {
        "notice": "Position {} added to {}.".format(title, current["name"]),
        "details": {"title": title, "level": level,
                    "department": current["name"]},
        "position_id": position_id,
    }


def update_position(db, department_id, form):
    current_department = department_row(db, department_id)
    position_id = _int(form.get("position_id"))
    position = position_row(db, department_id, position_id)
    title = _text(form, "title", 120)
    if not title:
        raise ValueError("A position needs a title.")
    level = _int(form.get("level"), position["level"])
    if level is None or level < 0 or level > 4:
        level = 4
    reports_to = _int(form.get("reports_to_position_id")) or None
    if reports_to == position_id:
        raise ValueError("A position cannot report to itself.")
    if reports_to:
        if db.execute("SELECT 1 FROM organizational_positions WHERE id = ?",
                      (reports_to,)).fetchone() is None:
            raise ValueError("The position it reports to does not exist.")
        seen, cursor, cycle = set(), reports_to, False
        while cursor is not None and cursor not in seen:
            if cursor == position_id:
                cycle = True
                break
            seen.add(cursor)
            row = db.execute(
                "SELECT reports_to_position_id FROM organizational_positions"
                " WHERE id = ?", (cursor,)).fetchone()
            cursor = row["reports_to_position_id"] if row is not None else None
        if cycle:
            raise ValueError("That reporting line would create a cycle.")
    duplicate = db.execute("""
        SELECT 1 FROM organizational_positions
        WHERE department_id = ? AND lower(title) = lower(?) AND id <> ?
    """, (department_id, title, position_id)).fetchone()
    if duplicate is not None:
        raise ValueError("{} already has a position called {}.".format(
            current_department["name"], title))
    db.execute("""
        UPDATE organizational_positions
        SET title = ?, level = ?, reports_to_position_id = ?
        WHERE id = ?
    """, (title, level, reports_to, position_id))
    if (position["title"] or "") != title:
        db.execute("UPDATE employees SET job_title = ? WHERE position_id = ?",
                   (title, position_id))
    return {
        "notice": "Position {} updated.".format(title),
        "details": {"title": title, "previous_title": position["title"],
                    "level": level, "department": current_department["name"]},
    }


def set_position_status(db, department_id, form):
    position_id = _int(form.get("position_id"))
    position = position_row(db, department_id, position_id)
    active = form.get("active") == "1"
    if not active:
        staff = _count(db, """
            SELECT COUNT(*) FROM employees
            WHERE position_id = ? AND COALESCE(employment_status, 'Active') = 'Active'
        """, (position_id,))
        accounts = _count(db, """
            SELECT COUNT(*) FROM users
            WHERE position_id = ? AND status = 'Active'
        """, (position_id,))
        if staff or accounts:
            raise ValueError(
                "Position {} is still held by {} active staff member(s) and "
                "{} account(s). Reassign them before retiring it.".format(
                    position["title"], staff, accounts))
    db.execute("UPDATE organizational_positions SET is_active = ? WHERE id = ?",
               (1 if active else 0, position_id))
    return {
        "notice": "Position {} {}.".format(
            position["title"], "restored" if active else "retired"),
        "details": {"title": position["title"], "active": active},
    }


def delete_position(db, department_id, position_id):
    position = position_row(db, department_id, position_id)
    if _count(db, "SELECT COUNT(*) FROM employees WHERE position_id = ?",
              (position_id,)):
        raise ValueError("Position {} still names a staff record. Clear that "
                         "assignment first.".format(position["title"]))
    if _count(db, "SELECT COUNT(*) FROM users WHERE position_id = ?",
              (position_id,)):
        raise ValueError("Position {} is still held by an account. Reassign "
                         "that account first.".format(position["title"]))
    if _count(db, "SELECT COUNT(*) FROM organizational_positions"
                  " WHERE reports_to_position_id = ?", (position_id,)):
        raise ValueError("Other positions report to {}. Re-point them before "
                         "deleting it.".format(position["title"]))
    db.execute("DELETE FROM organizational_positions WHERE id = ?",
               (position_id,))
    return {
        "notice": "Position {} deleted.".format(position["title"]),
        "details": {"title": position["title"]},
    }


def _account_placement(db, user_id):
    row = db.execute("""
        SELECT u.id, u.full_name, u.username, u.status, u.role,
               u.department_id, u.position_id, u.employee_id,
               d.name AS department_name,
               COALESCE(u.department_id, ep.department_id, ed.id)
                   AS effective_department_id,
               COALESCE(d.name, ed.name) AS effective_department_name
        FROM users AS u
        LEFT JOIN departments AS d ON d.id = u.department_id
        LEFT JOIN employees AS e ON e.id = u.employee_id
        LEFT JOIN organizational_positions AS ep ON ep.id = e.position_id
        LEFT JOIN departments AS ed ON ed.name = e.department COLLATE NOCASE
        WHERE u.id = ?
    """, (user_id,)).fetchone()
    if row is None:
        raise ValueError("That account no longer exists.")
    return row


def _target_position(db, department_id, position_id):
    if not position_id:
        return None
    position = db.execute(
        "SELECT id, title, department_id, COALESCE(is_active, 1) AS is_active"
        " FROM organizational_positions WHERE id = ?",
        (position_id,)).fetchone()
    if position is None:
        raise ValueError("The selected position does not exist.")
    if position["department_id"] != department_id:
        raise ValueError("The selected position belongs to another department.")
    if not position["is_active"]:
        raise ValueError("The selected position is retired. Choose an active one.")
    return position


def _place_account(db, account, department, position):
    db.execute("UPDATE users SET department_id = ?, position_id = ? WHERE id = ?",
               (department["id"], position["id"] if position else None,
                account["id"]))
    if account["employee_id"]:
        db.execute("""
            UPDATE employees
            SET department = ?, position_id = ?,
                job_title = COALESCE(NULLIF(?, ''), job_title)
            WHERE id = ?
        """, (department["name"], position["id"] if position else None,
              position["title"] if position else "", account["employee_id"]))
    assignment = department["name"] + (
        " · {}".format(position["title"]) if position else "")
    deliver_notification(
        db, account["id"], "Department assignment updated",
        "You are now placed as {} in the organization structure.".format(
            assignment),
        url_for("organization"), category="account", icon="users", tone="info")
    return assignment


def assign_user(db, department_id, form):
    department = department_row(db, department_id)
    if not department["is_active"]:
        raise ValueError("This department is inactive. Activate it before "
                         "placing people.")
    user_id = _int(form.get("user_id"))
    if not user_id:
        raise ValueError("Choose an account to place.")
    account = _account_placement(db, user_id)
    if account["status"] != "Active":
        raise ValueError("Only an active account can be placed in a "
                         "department.")
    if account["effective_department_id"] == department_id:
        raise ValueError("{} is already in {}.".format(
            account["full_name"], department["name"]))
    position = _target_position(db, department_id, _int(form.get("position_id")))
    previous = account["effective_department_name"] or "no department"
    assignment = _place_account(db, account, department, position)
    return {
        "notice": "{} placed in {}{}.".format(
            account["full_name"], assignment,
            " (moved from {})".format(previous) if previous != "no department"
            else ""),
        "details": {"user": account["full_name"], "department": department["name"],
                    "position": position["title"] if position else "",
                    "previous_department": previous},
    }


def update_user_placement(db, department_id, form):
    """Set an account's position inside the unit it already belongs to."""
    department = department_row(db, department_id)
    user_id = _int(form.get("user_id"))
    if not user_id:
        raise ValueError("Choose an account first.")
    account = _account_placement(db, user_id)
    if account["effective_department_id"] != department_id:
        raise ValueError("That account is not placed in this department.")
    position = _target_position(db, department_id, _int(form.get("position_id")))
    new_position_id = position["id"] if position else None
    if account["department_id"] == department_id and (
            account["position_id"] == new_position_id):
        return {
            "notice": "{} already holds that placement.".format(
                account["full_name"]),
            "changed": False,
        }
    assignment = _place_account(db, account, department, position)
    return {
        "notice": "{} placed as {}.".format(account["full_name"], assignment),
        "details": {"user": account["full_name"],
                    "department": department["name"],
                    "position": position["title"] if position else ""},
        "changed": True,
    }


def transfer_user(db, department_id, form):
    account_id = _int(form.get("user_id"))
    if not account_id:
        raise ValueError("Choose the account to transfer.")
    account = _account_placement(db, account_id)
    if account["effective_department_id"] != department_id:
        raise ValueError("That account is not placed in this department.")
    target_id = _int(form.get("target_department_id"))
    if not target_id:
        raise ValueError("Choose the department to transfer the account to.")
    target = department_row(db, target_id)
    if not target["is_active"]:
        raise ValueError("The target department is inactive.")
    if target["id"] == department_id:
        raise ValueError("That account is already in this department.")
    position = _target_position(db, target["id"], _int(form.get("position_id")))
    previous = department_row(db, department_id)["name"]
    assignment = _place_account(db, account, target, position)
    return {
        "notice": "{} transferred from {} to {}.".format(
            account["full_name"], previous, assignment),
        "details": {"user": account["full_name"], "from": previous,
                    "to": target["name"],
                    "position": position["title"] if position else ""},
        "department_id": target["id"],
    }


def remove_user(db, department_id, form):
    account_id = _int(form.get("user_id"))
    if not account_id:
        raise ValueError("Choose the account to remove.")
    account = _account_placement(db, account_id)
    if account["effective_department_id"] != department_id:
        raise ValueError("That account is not placed in this department.")
    department = department_row(db, department_id)
    db.execute("UPDATE users SET department_id = NULL, position_id = NULL"
               " WHERE id = ?", (account_id,))
    if account["employee_id"]:
        db.execute("""
            UPDATE employees
            SET department = '', position_id = NULL
            WHERE id = ?
        """, (account["employee_id"],))
    deliver_notification(
        db, account["id"], "Department assignment cleared",
        "You have been removed from {}. Your sign-in account is unchanged; "
        "an administrator can place you in another unit.".format(
            department["name"]),
        url_for("organization"), category="account", icon="users", tone="info")
    return {
        "notice": "{} removed from {}.".format(account["full_name"],
                                               department["name"]),
        "details": {"user": account["full_name"],
                    "department": department["name"]},
    }


def bind_department_role(db, department_id, form):
    department = department_row(db, department_id)
    if not department["is_active"]:
        raise ValueError("This department is inactive. Activate it before "
                         "granting roles.")
    role_key = (form.get("role_key") or "").strip()
    level = _int(form.get("position_level"), -1)
    if level is None:
        level = -1
    role = db.execute("SELECT name FROM roles WHERE key = ?",
                      (role_key,)).fetchone()
    added = bind_role(db, department_id, role_key, level)
    return {
        "notice": ("Role {} granted to {}{}.".format(
            role["name"] if role else role_key, department["name"],
            "" if level == -1 else " at the {} rung".format(
                POSITION_LEVEL_LABEL.get(level, "chosen")))
            if added else
            "That role is already granted at that rung."),
        "changed": added,
        "details": {"role": role_key, "department": department["name"],
                    "level": level},
    }


def unbind_department_role(db, department_id, form):
    department = department_row(db, department_id)
    role_key = (form.get("role_key") or "").strip()
    level = _int(form.get("position_level"), -1)
    role = db.execute("SELECT name FROM roles WHERE key = ?",
                      (role_key,)).fetchone()
    removed = unbind_role(db, department_id, role_key, level)
    if not removed:
        raise ValueError("That role is no longer granted here.")
    return {
        "notice": "Role {} withdrawn from {}.".format(
            role["name"] if role else role_key, department["name"]),
        "details": {"role": role_key, "department": department["name"],
                    "level": level},
    }


def delete_department_role(db, department_id, role_key):
    role = department_role(db, department_id, role_key)
    if role["is_system"]:
        raise ValueError("Built-in roles cannot be deleted.")
    if "is_standard" in role.keys() and role["is_standard"]:
        raise ValueError("Standard roles are recreated by the charter. "
                         "Deactivate {} instead.".format(role["name"]))
    primary = _count(db, "SELECT COUNT(*) FROM users WHERE role = ?",
                     (role_key,))
    extra = _count(db, "SELECT COUNT(*) FROM user_roles WHERE role_key = ?",
                   (role_key,))
    bound = _count(db, "SELECT COUNT(*) FROM department_roles"
                       " WHERE role_key = ?", (role_key,))
    if primary or extra or bound:
        raise ValueError(
            "Role {} still has {} account(s) and {} department binding(s). "
            "Remove it from them first.".format(
                role["name"], primary + extra, bound))
    db.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
    db.execute("DELETE FROM role_permission_grants WHERE role_key = ?",
               (role_key,))
    db.execute("DELETE FROM hiring_role_permissions WHERE role_key = ?",
               (role_key,))
    try:
        db.execute("DELETE FROM letter.letter_role_permissions"
                   " WHERE role_key = ?", (role_key,))
    except sqlite3.Error:
        pass
    db.execute("DELETE FROM roles WHERE key = ?", (role_key,))
    return {
        "notice": "Role {} deleted.".format(role["name"]),
        "details": {"role": role_key, "name": role["name"]},
    }
