"""Organization hierarchy schema and shared org snapshot."""

from core.access import effective_permissions
from core.config import DB_PATH
from core.config import ROLE_LABEL
from core.db import connect_db
from services.hr import employee_photo_url
from services.workflow import account_position_level
import sqlite3


HIERARCHY_DB = str(DB_PATH)


def hierarchy_db():
    """Open the existing application database."""
    return connect_db(HIERARCHY_DB)


def hierarchy_columns(conn, table):
    """Return existing column names."""
    return {
        row["name"]
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
    }


DEPARTMENTS_DDL = """
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1,
        leadership_position TEXT NOT NULL DEFAULT 'Director',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
"""


POSITIONS_DDL = """
    CREATE TABLE IF NOT EXISTS organizational_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL COLLATE NOCASE,
        department_id INTEGER,
        level INTEGER NOT NULL DEFAULT 4,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(title, department_id),
        FOREIGN KEY(department_id)
            REFERENCES departments(id)
            ON DELETE SET NULL
    )
"""


POSITION_LEVEL_LABEL = {0: "CEO", 1: "Executive", 2: "Director",
                        3: "Manager", 4: "Staff"}


def ensure_org_tables(conn):
    """Create the department and position tables and the account link columns."""
    conn.execute(DEPARTMENTS_DDL)

    department_columns = hierarchy_columns(conn, "departments")
    if "is_active" not in department_columns:
        conn.execute("""
            ALTER TABLE departments
            ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1
        """)
    if "leadership_position" not in department_columns:
        conn.execute("""
            ALTER TABLE departments
            ADD COLUMN leadership_position TEXT NOT NULL DEFAULT 'Director'
        """)

    conn.execute(POSITIONS_DDL)

    user_columns = hierarchy_columns(conn, "users")
    if user_columns:
        if "department_id" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN department_id INTEGER")
        if "position_id" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN position_id INTEGER")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_department
            ON users(department_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_position
            ON users(position_id)
        """)


def ensure_position(conn, title, level, department_id=None):
    """The id of a position, creating it once when the table has none."""
    row = conn.execute("""
        SELECT id FROM organizational_positions
        WHERE title = ? COLLATE NOCASE
          AND (? IS NULL AND department_id IS NULL OR department_id = ?)
        ORDER BY id LIMIT 1
    """, (title, department_id, department_id)).fetchone()
    if row is not None:
        return row["id"]
    return conn.execute("""
        INSERT INTO organizational_positions (title, department_id, level)
        VALUES (?, ?, ?)
    """, (title, department_id, level)).lastrowid


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,)).fetchone() is not None


def migrate_account_assignments(db):
    """Fold account titles and employee links into department and position ids.

    The signing ladder stored a rank as text on the account. Each rank becomes a
    real position (CEO level 0 through Manager level 3), the account is assigned
    to it, and the old column is dropped. Accounts already linked to an employee
    also inherit that employee's department and position, so approval routing and
    the chart start from one source of truth.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    if not columns:
        return

    ranks = {
        "CEO": ("Chief Executive Officer", 0),
        "Executive": ("Executive", 1),
        "Director": ("Director", 2),
        "Manager": ("Manager", 3),
    }
    position_ids = {}
    for title, (position_title, level) in ranks.items():
        position_ids[title] = ensure_position(db, position_title, level)

    employee_rows = {}
    if table_exists(db, "employees"):
        # A database from before the hierarchy screens may lack these columns.
        employee_columns = hierarchy_columns(db, "employees")
        department_expr = (
            "e.department" if "department" in employee_columns else "NULL")
        position_expr = (
            "e.position_id" if "position_id" in employee_columns else "NULL")
        for row in db.execute(f"""
            SELECT e.id, {department_expr} AS department,
                   {position_expr} AS position_id, d.id AS department_id
            FROM employees AS e
            LEFT JOIN departments AS d
                ON d.name = {department_expr} COLLATE NOCASE
        """).fetchall():
            employee_rows[row["id"]] = row

    if "organizational_title" in columns:
        # A very old table may predate the employee link as well as the title.
        employee_link = ("employee_id" if "employee_id" in columns
                         else "NULL AS employee_id")
        for account in db.execute(
                "SELECT id, organizational_title, {} FROM users".format(
                    employee_link)).fetchall():
            title = (account["organizational_title"] or "").strip()
            employee = employee_rows.get(account["employee_id"])
            department_id = employee["department_id"] if employee else None
            position_id = position_ids.get(title)
            if position_id is None and employee is not None:
                position_id = employee["position_id"]
            if position_id is not None or department_id is not None:
                db.execute("""
                    UPDATE users SET position_id = ?, department_id = ?
                    WHERE id = ?
                """, (position_id, department_id, account["id"]))
        try:
            db.execute("ALTER TABLE users DROP COLUMN organizational_title")
        except sqlite3.Error:
            # An old SQLite build is left with an inert column that no code reads.
            pass

    # Accounts that never carried a title still follow the employee row they are
    # linked to, so a position set in HR also answers the approval desks.
    if "employee_id" in columns:
        for account in db.execute("""
            SELECT id, employee_id, department_id, position_id FROM users
            WHERE employee_id IS NOT NULL
        """).fetchall():
            employee = employee_rows.get(account["employee_id"])
            if employee is None:
                continue
            if account["position_id"] is None and employee["position_id"] is not None:
                db.execute("UPDATE users SET position_id = ? WHERE id = ?",
                           (employee["position_id"], account["id"]))
            if account["department_id"] is None and employee["department_id"] is not None:
                db.execute("UPDATE users SET department_id = ? WHERE id = ?",
                           (employee["department_id"], account["id"]))


def ensure_hierarchy_schema():
    """
    Add hierarchy tables and missing employee columns.
    Existing employee records are preserved.
    """
    with hierarchy_db() as conn:
        ensure_org_tables(conn)

        # Do not create a second employee table if HR already has one.
        existing_tables = {
            row["name"]
            for row in conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type = 'table'
            """).fetchall()
        }

        if "employees" not in existing_tables:
            conn.execute("""
                CREATE TABLE employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_code TEXT UNIQUE,
                    full_name TEXT NOT NULL,
                    email TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    department TEXT DEFAULT '',
                    job_title TEXT DEFAULT '',
                    hire_date TEXT,
                    employment_status TEXT DEFAULT 'Active'
                )
            """)

        employee_columns = hierarchy_columns(conn, "employees")

        # Add only missing columns to the existing HR employee table.
        if "manager_id" not in employee_columns:
            conn.execute("""
                ALTER TABLE employees
                ADD COLUMN manager_id INTEGER
            """)

        if "position_id" not in employee_columns:
            conn.execute("""
                ALTER TABLE employees
                ADD COLUMN position_id INTEGER
            """)

        if "profile_photo" not in employee_columns:
            conn.execute("""
                ALTER TABLE employees
                ADD COLUMN profile_photo TEXT DEFAULT ''
            """)

        if "user_id" not in employee_columns:
            conn.execute("""
                ALTER TABLE employees
                ADD COLUMN user_id INTEGER
            """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_employee_manager
            ON employees(manager_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_employee_department
            ON employees(department)
        """)


ORG_LEVEL_BY_TITLE = {
    "CEO": 1,
    "Executive": 2,
    "Director": 3,
    "Manager": 4,
    "Employee": 5,
}


ORG_TITLE_BY_LEVEL = {level: title for title, level in ORG_LEVEL_BY_TITLE.items()}


ORG_CHART_LEVELS = ((1, "CEO"), (2, "Executive"), (3, "Director"), (4, "Manager"))


ORG_CHART_RUNG_BY_POSITION_LEVEL = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5}


ORG_DEPARTMENT_POSITIONS = ("Executive", "Director", "Manager")


ORG_CHART_HUES = ("green", "violet", "teal", "amber", "rose")


def org_level_for_position(level):
    """Chart rung for an HR position level (1 Executive ... 4 Staff)."""
    try:
        return ORG_CHART_RUNG_BY_POSITION_LEVEL.get(int(level), 5)
    except (TypeError, ValueError):
        return 5


def person_org_level(position_level=None):
    """The rung a person occupies, read from their HR position."""
    return org_level_for_position(position_level)


def level_title(level):
    """The chart title for a rung, 'Employee' for staff and unplaced people."""
    return ORG_TITLE_BY_LEVEL.get(level, "Employee")


def clean_department_position(value):
    """Return a valid leadership position, or '' when the choice is missing."""
    value = (value or "").strip().title()
    return value if value in ORG_DEPARTMENT_POSITIONS else ""


def department_position_level(position):
    """Chart rung for an open department seat: Executive, Director or Manager."""
    return ORG_LEVEL_BY_TITLE.get(clean_department_position(position) or "Director", 3)


ORG_ROLE_DEPARTMENT = {
    "hr": "Human Resources",
    "finance": "Finance",
    "budget": "Budget",
    "payroll": "Payroll",
    "procurement": "Procurement",
    "inventory": "Inventory",
    "admin": "Administration",
    "auditor": "Audit",
}


ORG_DEPARTMENT_PROFILES = (
    (("human resource", "people", "personnel"), "users", "Human Resources",
     "Manages people, culture and organizational development.",
     (("users", "Recruitment & Selection"),
      ("handshake", "Employee Relations"),
      ("graduation", "Training & Development"),
      ("chart", "Performance Management"),
      ("shield", "HR Policies & Compliance"))),
    (("financ", "account"), "database", "Finance",
     "Handles financial planning, budgeting and reporting.",
     (("calculator", "Accounting & Bookkeeping"),
      ("file", "Financial Reporting"),
      ("chart", "Budget Management"),
      ("cash", "Procurement & Payments"),
      ("shield", "Tax & Compliance"))),
    (("budget",), "wallet", "Budget",
     "Plans, allocates and controls the company budget.",
     (("wallet", "Budget Planning"),
      ("chart", "Allocations & Forecasting"),
      ("cash", "Expenditure Control"),
      ("file", "Budget Reporting"),
      ("shield", "Compliance & Audit"))),
    (("operat", "production", "manufactur"), "gear", "Operations",
     "Coordinates daily operations and keeps production efficient.",
     (("gear", "Production & Manufacturing"),
      ("box", "Inventory & Stock"),
      ("truck", "Logistics & Delivery"),
      ("wrench", "Maintenance & Facilities"),
      ("shield", "Quality Control"))),
    (("sales", "marketing", "brand"), "megaphone", "Sales & Marketing",
     "Drives sales growth and builds brand value.",
     (("users", "Sales Team"),
      ("handshake", "Customer Relations"),
      ("target", "Marketing & Promotion"),
      ("chart", "Business Development"),
      ("search", "Market Research"))),
    (("information technology", "technolog", "software", "systems", "ict"),
     "monitor", "Information Technology",
     "Ensures systems, network and data security.",
     (("network", "Network & Infrastructure"),
      ("database", "System Administration"),
      ("code", "Software & Applications"),
      ("shield", "Cybersecurity"),
      ("headset", "Technical Support"))),
    (("payroll",), "cash", "Payroll",
     "Runs salaries, deductions and staff benefits on time.",
     (("cash", "Salary Processing"),
      ("file", "Payroll Records"),
      ("check", "Deductions & Benefits"),
      ("clock", "Payroll Calendar"),
      ("shield", "Statutory Compliance"))),
    (("inventory", "stock", "store", "warehouse"), "box", "Inventory",
     "Tracks stock, materials and the stores behind every job.",
     (("box", "Stock Control"),
      ("inbox", "Goods Received"),
      ("truck", "Material Issuing"),
      ("chart", "Stock Reporting"),
      ("shield", "Store Security"))),
    (("procure", "purchas", "supplier"), "cart", "Procurement",
     "Sources suppliers, negotiates prices and keeps purchasing moving.",
     (("users", "Supplier Management"),
      ("file", "Purchase Requests"),
      ("handshake", "Contract Negotiation"),
      ("truck", "Order Delivery"),
      ("cash", "Payment Terms"))),
    (("audit", "compliance"), "shield", "Audit",
     "Reviews controls and keeps the company compliant.",
     (("search", "Internal Reviews"),
      ("file", "Audit Reports"),
      ("shield", "Risk & Compliance"),
      ("chart", "Follow-up Actions"),
      ("check", "Control Testing"))),
    (("admin", "office", "secretar"), "briefcase", "Administration",
     "Keeps the office, records and company calendar running.",
     (("calendar", "Scheduling"),
      ("file", "Records & Filing"),
      ("users", "Office Coordination"),
      ("mail", "Correspondence"),
      ("lock", "Confidentiality"))),
)


ORG_DEFAULT_PROFILE = (
    "briefcase", "Company Leadership",
    "Leads the team, plans the work and reports to the chief executive.",
    (("users", "Team Leadership"),
     ("clipboard", "Work Planning"),
     ("chart", "Performance Reporting"),
     ("shield", "Quality & Compliance"),
     ("handshake", "Staff Coordination")))


def org_department_profile(name):
    """Icon, label, copy and manager functions for a department name."""
    key = (name or "").strip().lower()
    if key in ("hr", "h.r."):
        key = "human resources"
    elif key in ("it", "i.t."):
        key = "information technology"
    for fragments, icon, label, description, focus in ORG_DEPARTMENT_PROFILES:
        if any(fragment in key for fragment in fragments):
            return {
                "icon": icon,
                "label": label,
                "description": description,
                "focus": [{"icon": icon_name, "label": text}
                          for icon_name, text in focus],
            }
    icon, label, description, focus = ORG_DEFAULT_PROFILE
    return {
        "icon": icon,
        "label": label,
        "description": description,
        "focus": [{"icon": icon_name, "label": text}
                  for icon_name, text in focus],
    }


def org_initials(name):
    words = str(name or "").split()
    if len(words) > 1:
        return (words[0][0] + words[1][0]).upper()
    return words[0][:2].upper() if words else "?"


def dress_leader(node, department_notes):
    """Add the heading, branch copy and manager functions the chart prints."""
    department = (node["department"] or "").strip()
    if not department:
        department = ORG_ROLE_DEPARTMENT.get(node["role"] or "", "")
    profile = org_department_profile(department)
    title = (node["job_title"] or "").strip()
    lower_title = title.lower()
    level = node["level"]

    if level == 1:
        heading = "Chief Executive Officer (CEO)"
        if title and "ceo" not in lower_title and "chief" not in lower_title:
            heading = title
        description = "Leads the company and sets the strategic direction."
    elif level == 2:
        if lower_title == "executive" or not lower_title:
            heading = "Executive of {}".format(department or profile["label"])
        else:
            heading = title
        description = (
            department_notes.get(department.lower())
            or department_notes.get(profile["label"].lower())
            or profile["description"]
        )
    elif level == 3:
        if not lower_title:
            heading = "Director of {}".format(department or profile["label"])
        elif lower_title == "director" and department:
            heading = "Director of {}".format(department)
        elif "director" not in lower_title:
            heading = "Director of {}".format(department or profile["label"])
        else:
            heading = title
        description = (
            department_notes.get(department.lower())
            or department_notes.get(profile["label"].lower())
            or profile["description"]
        )
    else:
        if "manager" not in lower_title:
            heading = "{} Manager".format(department or profile["label"])
        elif lower_title == "manager" and department:
            heading = "{} Manager".format(department)
        else:
            heading = title
        description = profile["description"]

    node.update({
        "chart_department": department,
        "chart_heading": heading,
        "chart_subtitle": (
            (department or profile["label"]) if level >= 2 else ""),
        "chart_description": description,
        "chart_icon": profile["icon"],
        "chart_focus": profile["focus"] if level >= 4 else [],
        "chart_initials": org_initials(node["name"]),
    })


def leadership_tree(people, accounts, user=None, department_notes=None,
                    departments=None):
    """The chain of command published on Home, Organization and HR: CEO,
    executives, directors and managers. Employees stay out of it.

    The level on the HR position decides the rung, whether the seat belongs to an
    employee row or to a sign-in account that was assigned a department without
    one. Registered departments add a vacant seat at the position chosen when
    they were created, so an Executive department hangs directly under the CEO,
    a Director department under the executive tier and a Manager department below
    the directors.

    Explicit `manager_id` links win. Everything else falls back by rung:
    executives to the CEO, directors to an executive (or the CEO when there is
    no executive), and managers to a director (or the nearest executive/CEO).
    """
    leaders = {}
    employee_keys = {}

    for node in people.values():
        level = node["level"]
        if level > 4:
            continue
        title = level_title(level)
        display_title = node["job_title"] or title
        if title.lower() not in display_title.lower():
            display_title = "{} · {}".format(title, display_title) if display_title else title
        leader = dict(node)
        leader.update({
            "job_title": display_title,
            "title": title,
            "level": level,
            "children": [],
        })
        key = ("emp", node["id"])
        leaders[key] = leader
        employee_keys[node["id"]] = key

    for account in accounts:
        level = org_level_for_position(account["position_level"])
        if level > 4:
            continue
        if account["employee_id"] in employee_keys:
            continue  # the linked employee row already carries this person
        title = level_title(level)
        display_title = account["position_title"] or title
        if title.lower() not in display_title.lower():
            display_title = "{} · {}".format(title, display_title) if display_title else title
        leaders[("acct", account["id"])] = {
            "id": "account-{}".format(account["id"]),
            "name": account["full_name"],
            "job_title": display_title,
            "department": account["department_name"] or "",
            "level": level,
            "photo": None,
            "manager_id": None,
            "username": account["username"] or "",
            "role": account["role"],
            "role_label": ROLE_LABEL.get(account["role"], ""),
            "is_me": bool(user is not None and account["id"] == user["id"]),
            "children": [],
        }

    if not leaders:
        return []

    # A department's saved seat is drawn as a vacancy only while nobody at or
    # above that rung already carries the department on the chart.
    if departments:
        covered_levels = {}
        for node in leaders.values():
            department = (node["department"] or "").strip()
            if not department:
                department = ORG_ROLE_DEPARTMENT.get(node["role"] or "", "")
            if department:
                key = department.lower()
                covered_levels[key] = min(
                    covered_levels.get(key, 5), node["level"])

        for name, raw_position in departments.items():
            name = (name or "").strip()
            if not name:
                continue
            position = clean_department_position(raw_position) or "Director"
            seat_level = department_position_level(position)
            if covered_levels.get(name.lower(), 5) <= seat_level:
                continue
            leaders[("dept", name.lower())] = {
                "id": "department-{}".format(name.lower().replace(" ", "-")),
                "name": name,
                "job_title": position,
                "leadership_position": position,
                "department": name,
                "title": position,
                "level": seat_level,
                "photo": None,
                "manager_id": None,
                "username": "",
                "role": "",
                "role_label": "",
                "is_me": False,
                "children": [],
                "vacant": True,
            }

    ceos = [node for node in leaders.values() if node["level"] == 1]
    executives = [node for node in leaders.values() if node["level"] == 2]
    directors = [node for node in leaders.values() if node["level"] == 3]

    def pick_exact(nodes, department):
        if not department:
            return None
        wanted = department.lower()
        for node in nodes:
            if (node["department"] or "").strip().lower() == wanted:
                return node
        return None

    parent_keys = {}
    keys_by_object = {id(node): key for key, node in leaders.items()}
    for key, node in leaders.items():
        parent = None
        if key[0] == "emp":
            parent_key = employee_keys.get(node["manager_id"])
            if parent_key is not None and parent_key != key:
                parent = leaders.get(parent_key)

        if parent is None and node["level"] > 1:
            department = (node["department"] or "").strip()
            if not department:
                department = ORG_ROLE_DEPARTMENT.get(node["role"] or "", "")
            if node["level"] == 2:
                parent = ceos[0] if ceos else None
            elif node["level"] == 3:
                parent = (pick_exact(executives, department)
                          or (executives[0] if executives else None)
                          or (ceos[0] if ceos else None))
            else:
                parent = (pick_exact(directors, department)
                          or (directors[0] if directors else None)
                          or pick_exact(executives, department)
                          or (executives[0] if executives else None)
                          or (ceos[0] if ceos else None))

        parent_keys[key] = keys_by_object.get(id(parent)) if parent is not None else None

    # A corrupt manager link can loop; break it rather than hide the chart.
    for key in leaders:
        seen = set()
        cursor = key
        while cursor is not None:
            if cursor in seen:
                parent_keys[cursor] = None
                break
            seen.add(cursor)
            cursor = parent_keys.get(cursor)

    for node in leaders.values():
        node["children"] = []

    roots = []
    for key, node in leaders.items():
        parent = leaders.get(parent_keys.get(key)) if parent_keys.get(key) else None
        if parent is None or parent is node:
            roots.append(node)
        else:
            parent["children"].append(node)

    def sort_nodes(nodes):
        nodes.sort(key=lambda node: (node["level"], bool(node.get("vacant")),
                                     node["name"].lower()))
        for node in nodes:
            sort_nodes(node["children"])

    sort_nodes(roots)

    # The CEO is navy, executives carry the house accent, and each director
    # opens a branch hue that the managers under them share.
    hue_index = 0

    def paint(node, hue=None):
        nonlocal hue_index
        if node["level"] == 1:
            node["color"] = "ceo"
            inherited = None
        elif node["level"] == 2:
            node["color"] = "executive"
            inherited = None
        elif node["level"] == 3:
            node["color"] = ORG_CHART_HUES[hue_index % len(ORG_CHART_HUES)]
            hue_index += 1
            inherited = node["color"]
        else:
            if hue is None:
                node["color"] = ORG_CHART_HUES[hue_index % len(ORG_CHART_HUES)]
                hue_index += 1
                hue = node["color"]
            else:
                node["color"] = hue
            inherited = node["color"]
        for child in node["children"]:
            paint(child, inherited)

    for root in roots:
        paint(root)

    def dress(nodes):
        for node in nodes:
            dress_leader(node, department_notes or {})
            dress(node["children"])

    dress(roots)
    return roots


def group_leadership_tiers(roots):
    """Flatten the reporting tree into the ordered chart bands."""
    members = {level: [] for level, _title in ORG_CHART_LEVELS}

    def walk(nodes):
        for node in nodes:
            if node["level"] in members:
                members[node["level"]].append(node)
            walk(node["children"])

    walk(roots)

    tiers = []
    for level, title in ORG_CHART_LEVELS:
        group = members[level]
        if not group:
            continue
        group.sort(key=lambda node: (bool(node.get("vacant")), node["name"].lower()))
        tiers.append({"level": level, "title": title, "members": group})
    return tiers


def organization_snapshot(db, user=None, public=False):
    """The leadership tree and department rosters for this database.

    Accounts carry their own department and position, so a person can be placed
    on the structure before HR creates an employee row. When an account is linked
    to an employee, the employee row is the one shown and the account only adds
    its sign-in details.
    """
    active_accounts = db.execute("""
        SELECT u.id, u.full_name, u.username, u.role, u.employee_id,
               u.department_id, d.name AS department_name,
               u.position_id, p.title AS position_title,
               p.level AS position_level
        FROM users AS u
        LEFT JOIN departments AS d ON d.id = u.department_id
        LEFT JOIN organizational_positions AS p ON p.id = u.position_id
        WHERE u.status = 'Active'
        ORDER BY u.full_name
    """).fetchall()

    rows = db.execute("""
        SELECT e.id, e.full_name, e.department, e.job_title, e.manager_id,
               e.profile_photo, p.title AS position_title,
               p.level AS position_level,
               u.id AS account_id, u.role AS account_role,
               u.username AS username
        FROM employees AS e
        LEFT JOIN organizational_positions AS p ON p.id = e.position_id
        LEFT JOIN users AS u ON u.employee_id = e.id
        WHERE COALESCE(e.employment_status, 'Active') = 'Active'
        ORDER BY e.department, e.full_name
    """).fetchall()

    me_id = user["employee_id"] if user is not None else None
    people = {}
    for row in rows:
        level = person_org_level(row["position_level"])
        people[row["id"]] = {
            "id": row["id"],
            "name": row["full_name"],
            "job_title": row["job_title"] or "",
            "position_title": row["position_title"] or "",
            "department": row["department"] or "",
            "level": level,
            "title": level_title(level),
            "account_id": row["account_id"],
            "username": row["username"] or "",
            "photo": employee_photo_url(row),
            "manager_id": row["manager_id"],
            "role": row["account_role"] or "",
            "role_label": ROLE_LABEL.get(row["account_role"], ""),
            "is_me": bool(me_id and row["id"] == me_id),
            "children": [],
        }

    # Accounts without an employee row are people too: their assignment is what
    # puts them on a department roster and, at leadership level, on the chart.
    account_people = {}
    linked_employee_ids = {row["employee_id"] for row in active_accounts
                           if row["employee_id"] in people}
    for account in active_accounts:
        if account["employee_id"] in linked_employee_ids:
            continue
        level = person_org_level(account["position_level"])
        title = level_title(level)
        account_people[account["id"]] = {
            "id": "account-{}".format(account["id"]),
            "name": account["full_name"],
            "job_title": account["position_title"] or "",
            "position_title": account["position_title"] or "",
            "department": account["department_name"] or "",
            "level": level,
            "title": title,
            "account_id": account["id"],
            "username": account["username"] or "",
            "photo": None,
            "manager_id": None,
            "role": account["role"],
            "role_label": ROLE_LABEL.get(account["role"], ""),
            "is_me": bool(user is not None and account["id"] == user["id"]),
            "children": [],
        }

    roots = []
    for node in people.values():
        parent = people.get(node["manager_id"])
        if parent is not None and parent["id"] != node["id"]:
            parent["children"].append(node)
        else:
            roots.append(node)

    def sort_tree(nodes):
        nodes.sort(key=lambda node: (node["level"], node["name"].lower()))
        for node in nodes:
            sort_tree(node["children"])

    if people and not roots:
        # A cycle with no top cannot happen through the HR screen, but a row
        # edited straight into the database should still leave a chart.
        roots = [next(iter(people.values()))]
    sort_tree(roots)

    department_rows = db.execute(
        "SELECT name, description, is_active, leadership_position "
        "FROM departments ORDER BY name").fetchall()
    department_notes = {
        (row["name"] or "").strip().lower(): row["description"] or ""
        for row in department_rows
    }
    department_seats = {
        (row["name"] or "").strip(): (
            clean_department_position(row["leadership_position"]) or "Director"
        )
        for row in department_rows
        if row["is_active"] and (row["name"] or "").strip()
    }

    leadership_roots = leadership_tree(
        people, active_accounts, user, department_notes, department_seats)
    leadership_tiers = group_leadership_tiers(leadership_roots)

    def member_title(node):
        return (node["job_title"] or node["position_title"]
                or (level_title(node["level"]) if node["level"] <= 4 else ""))

    rosters = {}
    for node in people.values():
        if not node["department"]:
            continue
        rosters.setdefault(node["department"].strip().lower(), []).append({
            "name": node["name"],
            "title": member_title(node),
            "username": node["username"],
            "role": node["role"],
            "role_label": node["role_label"],
            "is_me": node["is_me"],
            "account_id": node["account_id"],
        })
    for node in account_people.values():
        if not node["department"]:
            continue
        rosters.setdefault(node["department"].strip().lower(), []).append({
            "name": node["name"],
            "title": member_title(node),
            "username": node["username"],
            "role": node["role"],
            "role_label": node["role_label"],
            "is_me": node["is_me"],
            "account_id": node["account_id"],
        })

    def roster_for(name):
        members = rosters.get((name or "").strip().lower(), [])
        members.sort(key=lambda member: member["name"].lower())
        return members

    departments = []
    named = set()
    for department in department_rows:
        named.add(department["name"].lower())
        if public and not department["is_active"]:
            continue
        profile = org_department_profile(department["name"])
        members = roster_for(department["name"])
        departments.append({
            "name": department["name"],
            "description": department["description"] or "",
            "count": len(members),
            "members": members,
            "icon": profile["icon"],
            "label": profile["label"],
            "is_active": bool(department["is_active"]),
            "leadership_position": (
                clean_department_position(department["leadership_position"])
                or "Director"
            ),
        })
    for key, members in rosters.items():
        if key in named:
            continue
        # The roster key is lower-cased; recover the stored spelling from a
        # person when possible, otherwise print the key as a title.
        name = key.title()
        for node in list(people.values()) + list(account_people.values()):
            if (node["department"] or "").strip().lower() == key:
                name = node["department"].strip()
                break
        profile = org_department_profile(name)
        departments.append({
            "name": name,
            "description": "",
            "count": len(members),
            "members": sorted(members, key=lambda member: member["name"].lower()),
            "icon": profile["icon"],
            "label": profile["label"],
            "is_active": True,
            "leadership_position": "",
        })
    departments.sort(key=lambda item: (-item["count"], item["name"].lower()))

    positions = db.execute(
        "SELECT COUNT(*) AS n FROM organizational_positions").fetchone()["n"]

    unassigned = [node for node in people.values() if not node["department"]]
    unassigned += [node for node in account_people.values()
                   if not node["department"]]
    unassigned.sort(key=lambda node: node["name"].lower())

    stats = {
        "people": len(people) + len(account_people),
        "employees": len(people),
        "accounts": len(active_accounts),
        "departments": len(departments),
        "positions": positions,
        "lines": sum(1 for node in people.values()
                     if node["manager_id"] in people),
        "unassigned": len(unassigned),
        "linked": sum(1 for node in people.values() if node["role"]),
        "roots": len(roots),
        "leaders": (sum(1 for node in people.values() if node["level"] <= 4)
                    + sum(1 for node in account_people.values()
                          if node["level"] <= 4)),
    }

    org_me = people.get(me_id)
    if org_me is None and user is not None:
        org_me = account_people.get(user["id"])

    if public:
        # Names and titles belong on the public chart; sign-in roles do not.
        def scrub(nodes):
            for node in nodes:
                node.pop("role", None)
                node.pop("role_label", None)
                node.pop("is_me", None)
                node.pop("account_id", None)
                node.pop("username", None)
                scrub(node["children"])

        scrub(roots)
        scrub(leadership_roots)
        for department in departments:
            for member in department["members"]:
                member.pop("role", None)
                member.pop("role_label", None)
                member.pop("is_me", None)
                member.pop("account_id", None)
                member.pop("username", None)

    return {
        "org_tree": roots,
        "org_leadership": leadership_roots,
        "org_leadership_tiers": leadership_tiers,
        "org_departments": departments,
        "org_stats": stats,
        "org_unassigned": unassigned,
        "org_me": org_me,
        "org_public": public,
    }


def org_leadership_level(user, db):
    """The chart rung an account sits on, from its assigned position."""
    if user is None:
        return None
    if "accounts" in effective_permissions(user):
        return 1
    level = account_position_level(user, db)
    if level is None:
        return None
    rung = org_level_for_position(level)
    return rung if rung <= 4 else None
