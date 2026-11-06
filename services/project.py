"""Project delivery: the register, the team behind each job, its milestones and tasks.

A project is a piece of work the company has promised to a client - a kitchen
installation, a set of wardrobes, a shop fit-out. The desk keeps one row per job and
hangs the team, the milestones and the tasks off it, so the supervisor on site and
the accountant signing the cheque read the same record.

Nothing here opens a second registry of the people or the money the company already
keeps. A team member is an account from the user master data, the department comes
from the HR register, and what a project has committed is read from the budget
commitments the Budget desk already maintains. A project that draws on an allocation
reserves it through the same helper a requisition uses, so the budget screen and the
project screen cannot disagree about what is left.

Every write runs through the validators below, so a form, a direct URL and a forged
POST are answered in the same words; and the register issues the code rather than
the form, which is how two desks stop quoting different numbers for one client.
"""

from core.db import check_in
from core.db import connect_db
from core.validation import clean_amount
from core.validation import clean_choice
from core.validation import clean_date
from core.validation import clean_status
from core.validation import clean_text
from datetime import date
from services.workflow import APPROVAL_DEPARTMENTS


# ---------------------------------------------------------------- vocabularies
# One tuple per choice list. The screens draw their selects from these, the DDL
# repeats the same words in a CHECK, and anything else is refused before it
# reaches SQLite. Draft and Pending are the two states an approval chain may still
# answer, which is why they are register statuses and not a second status column.
PROJECT_STATUSES = ("Draft", "Pending", "In Progress", "On Hold", "Completed",
                    "Cancelled")
PROJECT_PRIORITIES = ("Low", "Normal", "High", "Urgent")
MILESTONE_STATUSES = ("Pending", "In Progress", "Completed")
TASK_STATUSES = ("Not Started", "In Progress", "Blocked", "Completed")
TEAM_ROLES = ("Project Manager", "Site Supervisor", "Site Engineer", "Foreman",
              "Carpenter", "Finisher", "Installer", "Quality Check",
              "Procurement", "Team Member")
PROJECT_CURRENCIES = ("ETB", "USD", "EUR", "GBP")

# The states a project may still be rewritten in - and the only two an approval
# chain can answer. Named once so a route cannot widen one without the other.
OPEN_STATUSES = ("Draft", "Pending")

# The moves the register desk makes itself, once the work is running. Approval
# answers are not on this list: they arrive from the shared chain, not a button.
PROJECT_MANUAL_STATUSES = ("On Hold", "In Progress", "Completed", "Cancelled")

# The two words the workflow speaks that a project keeps for itself: a signed chain
# starts the work, a refused chain cancels it.
FINAL_PROJECT_STATUS = {"Approved": "In Progress", "Rejected": "Cancelled"}


PROJECT_DDL = """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        project_name TEXT NOT NULL COLLATE NOCASE,
        client_name TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT 'general',
        department_id INTEGER,
        department_name TEXT NOT NULL DEFAULT '',
        manager_user_id INTEGER,
        start_date TEXT,
        end_date TEXT,
        budget NUMERIC NOT NULL DEFAULT 0 CHECK (budget >= 0),
        currency TEXT NOT NULL DEFAULT 'ETB' {currency},
        status TEXT NOT NULL DEFAULT 'Draft' {status},
        priority TEXT NOT NULL DEFAULT 'Normal' {priority},
        budget_allocation_id INTEGER,
        approval_request_id INTEGER,
        is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
        completed_at TEXT,
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((start_date IS NULL OR end_date IS NULL)
               OR end_date >= start_date)
    )
""".format(currency=check_in("currency", PROJECT_CURRENCIES),
           status=check_in("status", PROJECT_STATUSES),
           priority=check_in("priority", PROJECT_PRIORITIES))


MEMBER_DDL = """
    CREATE TABLE IF NOT EXISTS project_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        member_name TEXT NOT NULL DEFAULT '',
        team_role TEXT NOT NULL DEFAULT 'Team Member' {role},
        assigned_by INTEGER,
        assigned_by_name TEXT NOT NULL DEFAULT '',
        assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, user_id),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
""".format(role=check_in("team_role", TEAM_ROLES, blank=True))


MILESTONE_DDL = """
    CREATE TABLE IF NOT EXISTS project_milestones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        milestone_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        due_date TEXT,
        completed_date TEXT,
        status TEXT NOT NULL DEFAULT 'Pending' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
""".format(status=check_in("status", MILESTONE_STATUSES))


TASK_DDL = """
    CREATE TABLE IF NOT EXISTS project_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        milestone_id INTEGER,
        task_title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        assigned_user_id INTEGER,
        start_date TEXT,
        due_date TEXT,
        status TEXT NOT NULL DEFAULT 'Not Started' {status},
        priority TEXT NOT NULL DEFAULT 'Normal' {priority},
        progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((start_date IS NULL OR due_date IS NULL)
               OR due_date >= start_date),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
""".format(status=check_in("status", TASK_STATUSES),
           priority=check_in("priority", PROJECT_PRIORITIES))


PROJECT_DDL_STATEMENTS = (PROJECT_DDL, MEMBER_DDL, MILESTONE_DDL, TASK_DDL)


INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_project_status"
    " ON projects(status, is_archived)",
    "CREATE INDEX IF NOT EXISTS idx_project_manager"
    " ON projects(manager_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_department"
    " ON projects(department_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_member_user"
    " ON project_members(user_id, project_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_milestone_project"
    " ON project_milestones(project_id, due_date)",
    "CREATE INDEX IF NOT EXISTS idx_project_task_project"
    " ON project_tasks(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_project_task_assignee"
    " ON project_tasks(assigned_user_id, status)",
)


def ensure_project_schema():
    """Create or top up every project table. Safe to call on every boot."""
    db = connect_db()
    try:
        for statement in PROJECT_DDL_STATEMENTS:
            db.execute(statement)
        for statement in INDEX_STATEMENTS:
            db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------------------------------------------- the code
PROJECT_CODE_PREFIX = "DBP-"
PROJECT_CODE_WIDTH = 5


def next_project_code(db):
    """DBP-00000 for the first job, then one past the highest issued number.

    The register issues the number rather than the form, so two projects raised in
    the same week cannot end up carrying a code another client is already quoting.
    A code typed by hand is still accepted; the sequence only counts the DBP
    numbers, so an old PRJ row never pulls it backwards.
    """
    serial = -1
    for row in db.execute(
            "SELECT project_code FROM projects WHERE project_code LIKE ?",
            (PROJECT_CODE_PREFIX + "%",)).fetchall():
        tail = (row["project_code"] or "")[len(PROJECT_CODE_PREFIX):]
        if tail.isdigit():
            serial = max(serial, int(tail))
    serial += 1
    candidate = "{}{:0{w}d}".format(PROJECT_CODE_PREFIX, serial,
                                    w=PROJECT_CODE_WIDTH)
    while db.execute("SELECT 1 FROM projects WHERE project_code = ?",
                     (candidate,)).fetchone():
        serial += 1
        candidate = "{}{:0{w}d}".format(PROJECT_CODE_PREFIX, serial,
                                        w=PROJECT_CODE_WIDTH)
    return candidate

# ------------------------------------------------------------------ validators
# Each raises ValueError with the sentence a screen flashes, so a bad form is
# answered in the same words whichever route received it. A reference to another
# desk's register - an account, a department, an allocation - is checked against
# that register here, because SQLite cannot hold a foreign key across two files.
def clean_id(value, field="Record"):
    """The one number a select box or a URL carries, or nothing at all."""
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError("Choose a valid {}.".format(field))
    return int(text)


def account_ref(db, user_id, field="account"):
    """An active account from the user master data, looked up by id.

    A project names a person the company already employs, so the register refuses a
    number belonging to an account that was never created or has since left.
    """
    if user_id is None:
        return None
    account = db.execute(
        "SELECT id, full_name, username, status FROM users WHERE id = ?",
        (user_id,)).fetchone()
    if account is None:
        raise ValueError("Choose a valid {}.".format(field))
    if account["status"] != "Active":
        raise ValueError("That {} is no longer an active account.".format(field))
    return account


def department_ref(db, department_id):
    if department_id is None:
        return None
    row = db.execute("SELECT id, name FROM departments WHERE id = ?",
                     (department_id,)).fetchone()
    if row is None:
        raise ValueError("Choose a valid department.")
    return row


def allocation_ref(db, allocation_id):
    if allocation_id is None:
        return None
    row = db.execute("""
        SELECT a.id, a.allocation_name, a.department, a.allocated_amount,
               a.currency, a.status, p.plan_name AS plan_title
        FROM budget_allocations AS a
        JOIN budget_plans AS p ON p.id = a.plan_id
        WHERE a.id = ?
    """, (allocation_id,)).fetchone()
    if row is None:
        raise ValueError("Choose a valid budget allocation.")
    if row["status"] != "Active":
        raise ValueError("That budget allocation is closed.")
    return row


def approval_department(form, department):
    """The slug the approval chain is keyed on.

    The workflow routes a request by the department word it already knows, so a
    register name it has never seen - or no name at all - travels as 'general' and
    still reaches management rather than vanishing from the approval desk.
    """
    named = (department["name"] if department else "").strip().lower()
    chosen = clean_choice(form.get("approval_department"), "approval desk",
                          tuple(APPROVAL_DEPARTMENTS), default="")
    if chosen:
        return chosen.lower()
    return named if named in APPROVAL_DEPARTMENTS else "general"


def project_values(form, db, exclude_id=None):
    """Every column a project form may set, validated and ready to write.

    The code is issued by the register when the form leaves it blank, and refused
    when the form brings one another project already carries - case and spacing
    aside, because a client quotes "dbp-00004" and "DBP-00004" as one job.
    """
    name = clean_text(form.get("project_name"), "Project name", 180, required=True)
    code = clean_text(form.get("project_code"), "Project code", 40).upper()
    if code:
        clash = db.execute(
            "SELECT 1 FROM projects WHERE project_code = ? AND id <> ?",
            (code, exclude_id or 0)).fetchone()
        if clash is not None:
            raise ValueError("Project code {} is already registered.".format(code))
    else:
        code = next_project_code(db)

    start = clean_date(form.get("start_date"), "Start date")
    end = clean_date(form.get("end_date"), "End date")
    if start and end and end < start:
        raise ValueError("End date cannot be before start date.")

    manager = account_ref(db, clean_id(form.get("manager_user_id"),
                                       "project manager"), "project manager")
    department = department_ref(db, clean_id(form.get("department_id"),
                                             "department"))
    allocation = allocation_ref(db, clean_id(form.get("budget_allocation_id"),
                                             "budget allocation"))

    return {
        "project_code": code,
        "project_name": name,
        "client_name": clean_text(form.get("client_name"), "Client name", 180),
        "location": clean_text(form.get("location"), "Location", 180),
        "description": clean_text(form.get("description"), "Description", 2000),
        "department": approval_department(form, department),
        "department_id": department["id"] if department else None,
        "department_name": department["name"] if department else "",
        "manager_user_id": manager["id"] if manager else None,
        "start_date": start,
        "end_date": end,
        "budget": float(clean_amount(form.get("budget"), "Project budget")),
        "currency": clean_choice(form.get("currency"), "currency",
                                 PROJECT_CURRENCIES, default="ETB"),
        "priority": clean_choice(form.get("priority"), "priority",
                                 PROJECT_PRIORITIES, default="Normal"),
        "budget_allocation_id": allocation["id"] if allocation else None,
    }


def clean_progress(value):
    text = str(value if value is not None else "").strip()
    if not text:
        return 0
    if not text.isdigit():
        raise ValueError("Progress must be a whole number between 0 and 100.")
    number = int(text)
    if number > 100:
        raise ValueError("Progress cannot be more than 100%.")
    return number


def milestone_values(form):
    values = {
        "milestone_name": clean_text(form.get("milestone_name"), "Milestone name",
                                     180, required=True),
        "description": clean_text(form.get("description"), "Description", 2000),
        "due_date": clean_date(form.get("due_date"), "Due date", required=True),
        "completed_date": clean_date(form.get("completed_date"), "Completed date"),
        "status": clean_status(form.get("status"), "Status", MILESTONE_STATUSES),
    }
    if values["status"] == "Completed" and not values["completed_date"]:
        values["completed_date"] = date.today().isoformat()
    if values["status"] != "Completed":
        values["completed_date"] = None
    return values


def task_values(form, db, project_id=None):
    values = {
        "task_title": clean_text(form.get("task_title"), "Task title", 180,
                                 required=True),
        "description": clean_text(form.get("description"), "Description", 2000),
        "milestone_id": clean_id(form.get("milestone_id"), "milestone"),
        "assigned_user_id": None,
        "start_date": clean_date(form.get("start_date"), "Start date"),
        "due_date": clean_date(form.get("due_date"), "Due date"),
        "status": clean_status(form.get("status"), "Status", TASK_STATUSES),
        "priority": clean_choice(form.get("priority"), "priority",
                                 PROJECT_PRIORITIES, default="Normal"),
        "progress": clean_progress(form.get("progress")),
    }
    assignee = account_ref(db, clean_id(form.get("assigned_user_id"), "assignee"),
                           "assignee")
    values["assigned_user_id"] = assignee["id"] if assignee else None

    if values["start_date"] and values["due_date"] \
            and values["due_date"] < values["start_date"]:
        raise ValueError("Due date cannot be before start date.")
    if values["milestone_id"] is not None:
        owner = db.execute("SELECT project_id FROM project_milestones WHERE id = ?",
                           (values["milestone_id"],)).fetchone()
        if owner is None or (project_id is not None
                             and owner["project_id"] != project_id):
            raise ValueError("Choose a milestone from the same project.")
    # Progress and status are one fact told twice; the register keeps them agreed.
    if values["status"] == "Completed":
        values["progress"] = 100
    elif values["progress"] == 100:
        values["status"] = "Completed"
    return values


# ---------------------------------------------------------------------- writes
def insert_project(db, values, user):
    """One register row, opened as a draft by the account that raised it."""
    columns = list(values.keys()) + ["status", "created_by", "created_by_name"]
    params = [values[name] for name in values] + ["Draft", user["id"],
                                                  user["full_name"]]
    result = db.execute(
        "INSERT INTO projects ({}) VALUES ({})".format(
            ", ".join(columns), ", ".join("?" for _ in columns)),
        params)
    return result.lastrowid


def update_project(db, project_id, values):
    assignments = ", ".join("{} = ?".format(name) for name in values)
    db.execute("UPDATE projects SET {} WHERE id = ?".format(assignments),
               [values[name] for name in values] + [project_id])


def set_project_status(db, project_id, status):
    """Move a project along the register, closing the window when it stops."""
    completed = status in ("Completed", "Cancelled")
    db.execute("UPDATE projects SET status = ?, completed_at = ?"
               " WHERE id = ?",
               (status, date.today().isoformat() if completed else None,
                project_id))


def archive_project(db, project_id):
    db.execute("UPDATE projects SET is_archived = 1 WHERE id = ?", (project_id,))


def unarchive_project(db, project_id):
    db.execute("UPDATE projects SET is_archived = 0 WHERE id = ?", (project_id,))


def link_approval(db, project_id, request_id):
    db.execute("UPDATE projects SET approval_request_id = ?, status = 'Pending'"
               " WHERE id = ?", (request_id, project_id))


def add_member(db, project_id, account, team_role, actor):
    result = db.execute("""
        INSERT INTO project_members (
            project_id, user_id, username, member_name, team_role,
            assigned_by, assigned_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (project_id, account["id"], account["username"], account["full_name"],
          team_role, actor["id"], actor["full_name"]))
    return result.lastrowid


def remove_member(db, member_id):
    db.execute("DELETE FROM project_members WHERE id = ?", (member_id,))


def insert_milestone(db, project_id, values, user):
    result = db.execute("""
        INSERT INTO project_milestones (
            project_id, milestone_name, description, due_date, completed_date,
            status, created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, values["milestone_name"], values["description"],
          values["due_date"], values["completed_date"], values["status"],
          user["id"], user["full_name"]))
    return result.lastrowid


def update_milestone(db, milestone_id, values):
    assignments = ", ".join("{} = ?".format(name) for name in values)
    db.execute("UPDATE project_milestones SET {} WHERE id = ?".format(assignments),
               [values[name] for name in values] + [milestone_id])


def insert_task(db, project_id, values, user):
    result = db.execute("""
        INSERT INTO project_tasks (
            project_id, milestone_id, task_title, description, assigned_user_id,
            start_date, due_date, status, priority, progress,
            created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, values["milestone_id"], values["task_title"],
          values["description"], values["assigned_user_id"], values["start_date"],
          values["due_date"], values["status"], values["priority"],
          values["progress"], user["id"], user["full_name"]))
    return result.lastrowid


def update_task(db, task_id, values):
    assignments = ", ".join("{} = ?".format(name) for name in values)
    db.execute("UPDATE project_tasks SET {} WHERE id = ?".format(assignments),
               [values[name] for name in values] + [task_id])

# ----------------------------------------------------------------------- reads
# One select list, so the register, the dashboard, the detail page and the export
# all count the same figures. The counts are subqueries rather than a joined group
# because a project list is read a page at a time and a join would multiply rows.
PROJECT_FIELDS = """
    p.*,
    u.username AS manager_username,
    u.full_name AS manager_name,
    (SELECT count(*) FROM project_tasks AS t
      WHERE t.project_id = p.id) AS task_count,
    (SELECT count(*) FROM project_tasks AS t
      WHERE t.project_id = p.id AND t.status <> 'Completed') AS open_tasks,
    (SELECT count(*) FROM project_members AS m
      WHERE m.project_id = p.id) AS member_count,
    (SELECT count(*) FROM project_milestones AS ms
      WHERE ms.project_id = p.id AND ms.status <> 'Completed') AS open_milestones,
    (SELECT COALESCE(round(avg(t.progress), 0), 0) FROM project_tasks AS t
      WHERE t.project_id = p.id) AS progress,
    (SELECT COALESCE(sum(c.amount), 0) FROM budget_commitments AS c
      WHERE c.source_table = 'projects' AND c.source_id = p.id
        AND c.status = 'Open') AS committed
"""

PROJECT_FROM = "FROM projects AS p LEFT JOIN users AS u ON u.id = p.manager_user_id"


def project_row(db, project_id, include_archived=True):
    """One project with its manager's name, its counts and its commitment."""
    where = "WHERE p.id = ?" if include_archived else "WHERE p.id = ? AND p.is_archived = 0"
    return db.execute("SELECT {} {} {}".format(PROJECT_FIELDS, PROJECT_FROM, where),
                      (project_id,)).fetchone()


def project_rows(db, status=None, search=None, department_id=None,
                 manager_id=None, include_archived=False, limit=None):
    clauses, params = [], []
    if not include_archived:
        clauses.append("p.is_archived = 0")
    if status:
        clauses.append("p.status = ?")
        params.append(status)
    if department_id:
        clauses.append("p.department_id = ?")
        params.append(department_id)
    if manager_id:
        clauses.append("p.manager_user_id = ?")
        params.append(manager_id)
    if search:
        clauses.append("(p.project_name LIKE ? OR p.project_code LIKE ?"
                       " OR p.client_name LIKE ? OR p.location LIKE ?)")
        term = "%{}%".format(search)
        params.extend([term, term, term, term])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT {} {} {} ORDER BY p.is_archived, CASE p.status"
        " WHEN 'Pending' THEN 0 WHEN 'Draft' THEN 1 WHEN 'In Progress' THEN 2"
        " ELSE 3 END, p.priority = 'Urgent' DESC, p.priority = 'High' DESC,"
        " p.end_date IS NULL, p.end_date, p.id DESC"
    ).format(PROJECT_FIELDS, PROJECT_FROM, where)
    if limit:
        sql += " LIMIT {}".format(int(limit))
    return db.execute(sql, params).fetchall()


def member_rows(db, project_id=None, user_id=None):
    clauses, params = [], []
    if project_id is not None:
        clauses.append("m.project_id = ?")
        params.append(project_id)
    if user_id is not None:
        clauses.append("m.user_id = ?")
        params.append(user_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return db.execute("""
        SELECT m.*, p.project_code, p.project_name, p.status AS project_status,
               p.is_archived
        FROM project_members AS m
        JOIN projects AS p ON p.id = m.project_id
        {} ORDER BY p.id DESC, m.id
    """.format(where), params).fetchall()


def assignable_accounts(db):
    """The active accounts a job may be handed to, by name and username.

    The register offers the whole staff list rather than a second table of "project
    people": the right to assign is checked on the route, and the list is only ever
    a picker for the user master data the company already keeps.
    """
    return db.execute("""
        SELECT id, username, full_name, role FROM users
        WHERE status = 'Active'
        ORDER BY full_name COLLATE NOCASE, username
    """).fetchall()


def department_options(db):
    """The units the project form may name, from the HR register."""
    try:
        return db.execute(
            "SELECT id, name FROM departments WHERE is_active = 1"
            " ORDER BY name COLLATE NOCASE").fetchall()
    except Exception:
        # A database that has not grown the departments table yet answers with an
        # empty picker rather than failing the whole form.
        return []


def allocation_options(db):
    """The open allocations a project may reserve against, newest first."""
    return db.execute("""
        SELECT a.id, a.allocation_name, a.department, a.allocated_amount,
               a.currency, p.plan_name AS plan_title, p.status AS plan_status
        FROM budget_allocations AS a
        JOIN budget_plans AS p ON p.id = a.plan_id
        WHERE a.status = 'Active'
        ORDER BY p.plan_name COLLATE NOCASE, a.allocation_name COLLATE NOCASE
    """).fetchall()


def milestone_rows(db, project_id=None, milestone_id=None, only_open=False,
                   search=None):
    clauses, params = ["1 = 1"], []
    if project_id is not None:
        clauses.append("ms.project_id = ?")
        params.append(project_id)
    if milestone_id is not None:
        clauses.append("ms.id = ?")
        params.append(milestone_id)
    if only_open:
        clauses.append("ms.status <> 'Completed'")
    if search:
        clauses.append("ms.milestone_name LIKE ?")
        params.append("%{}%".format(search))
    return db.execute("""
        SELECT ms.*, p.project_code, p.project_name, p.status AS project_status,
               p.is_archived AS project_archived
        FROM project_milestones AS ms
        JOIN projects AS p ON p.id = ms.project_id
        WHERE {} AND p.is_archived = 0
        ORDER BY ms.status = 'Completed', ms.due_date IS NULL, ms.due_date, ms.id
    """.format(" AND ".join(clauses)), params).fetchall()


def task_rows(db, project_id=None, task_id=None, user_id=None, status=None,
              only_overdue=False, search=None, limit=None):
    clauses, params = ["1 = 1"], []
    if project_id is not None:
        clauses.append("t.project_id = ?")
        params.append(project_id)
    if task_id is not None:
        clauses.append("t.id = ?")
        params.append(task_id)
    if user_id is not None:
        clauses.append("t.assigned_user_id = ?")
        params.append(user_id)
    if status:
        clauses.append("t.status = ?")
        params.append(status)
    if only_overdue:
        clauses.append("t.status <> 'Completed' AND t.due_date IS NOT NULL"
                       " AND t.due_date < date('now')")
    if search:
        clauses.append("(t.task_title LIKE ? OR p.project_name LIKE ?"
                       " OR p.project_code LIKE ?)")
        term = "%{}%".format(search)
        params.extend([term, term, term])
    sql = """
        SELECT t.*, u.username AS assigned_username, u.full_name AS assigned_name,
               p.project_code, p.project_name, p.status AS project_status,
               ms.milestone_name
        FROM project_tasks AS t
        JOIN projects AS p ON p.id = t.project_id
        LEFT JOIN users AS u ON u.id = t.assigned_user_id
        LEFT JOIN project_milestones AS ms ON ms.id = t.milestone_id
        WHERE {} AND p.is_archived = 0
        ORDER BY t.status = 'Completed', t.due_date IS NULL, t.due_date, t.id DESC
    """.format(" AND ".join(clauses))
    if limit:
        sql += " LIMIT {}".format(int(limit))
    return db.execute(sql, params).fetchall()


def project_progress(db, project_id):
    """The average of a project's task progress, or zero with no tasks written."""
    row = db.execute("SELECT COALESCE(round(avg(progress), 0), 0) AS p"
                     " FROM project_tasks WHERE project_id = ?",
                     (project_id,)).fetchone()
    return int(row["p"] or 0)


def project_budget_position(db, project):
    """What the project has reserved against its allocation, and what is left.

    Read from the commitments Budget already keeps rather than a second tally, so
    the figure on the project card is the figure the allocation card uses.
    """
    committed = float(project["committed"] or 0)
    allocation_id = project["budget_allocation_id"]
    if not allocation_id:
        return {"committed": committed, "allocated": None, "remaining": None,
                "overdrawn": False}
    row = db.execute("""
        SELECT a.allocated_amount,
               (SELECT COALESCE(sum(c.amount), 0) FROM budget_commitments AS c
                 WHERE c.allocation_id = a.id AND c.status = 'Open') AS used,
               a.allocation_name, a.currency, p.plan_name AS plan_title
        FROM budget_allocations AS a
        JOIN budget_plans AS p ON p.id = a.plan_id
        WHERE a.id = ?
    """, (allocation_id,)).fetchone()
    if row is None:
        return {"committed": committed, "allocated": None, "remaining": None,
                "overdrawn": False}
    allocated = float(row["allocated_amount"] or 0)
    # The project's own reservation is excluded from the headroom it is judged on,
    # so a resubmit with a larger budget is measured against what is genuinely free.
    used_elsewhere = float(row["used"] or 0) - committed
    remaining = round(allocated - used_elsewhere, 2)
    return {"committed": committed, "allocated": allocated,
            "remaining": remaining, "overdrawn": committed > remaining,
            "allocation_name": row["allocation_name"],
            "currency": row["currency"], "plan_title": row["plan_title"]}


# ------------------------------------------------------------------- dashboards
def project_summary(db):
    """The counts the dashboard card carries, in one pass over the register."""
    rows = db.execute("""
        SELECT p.status, count(*) AS total,
               COALESCE(sum(p.budget), 0) AS budget
        FROM projects AS p WHERE p.is_archived = 0 GROUP BY p.status
    """).fetchall()
    figures = {row["status"]: {"total": row["total"], "budget": float(row["budget"])}
               for row in rows}
    counts = {name: figures.get(name, {}).get("total", 0)
              for name in PROJECT_STATUSES}
    open_tasks = db.execute("""
        SELECT count(*) AS c FROM project_tasks AS t
        JOIN projects AS p ON p.id = t.project_id
        WHERE p.is_archived = 0 AND t.status <> 'Completed'
    """).fetchone()["c"]
    overdue_tasks = db.execute("""
        SELECT count(*) AS c FROM project_tasks AS t
        JOIN projects AS p ON p.id = t.project_id
        WHERE p.is_archived = 0 AND t.status <> 'Completed'
          AND t.due_date IS NOT NULL AND t.due_date < date('now')
    """).fetchone()["c"]
    overdue_milestones = db.execute("""
        SELECT count(*) AS c FROM project_milestones AS ms
        JOIN projects AS p ON p.id = ms.project_id
        WHERE p.is_archived = 0 AND ms.status <> 'Completed'
          AND ms.due_date IS NOT NULL AND ms.due_date < date('now')
    """).fetchone()["c"]
    pending_value = db.execute("""
        SELECT COALESCE(sum(budget), 0) AS total FROM projects
        WHERE is_archived = 0 AND status = 'Pending'
    """).fetchone()["total"]
    return {
        "counts": counts,
        "live": counts["In Progress"] + counts["On Hold"],
        "awaiting": counts["Pending"],
        "drafts": counts["Draft"],
        "total": sum(counts.values()),
        "pending_value": float(pending_value or 0),
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "overdue_milestones": overdue_milestones,
    }


def workload_by_manager(db):
    """Who is carrying what, read from the register rather than a second list.

    The count is aliased away from the table name: the statement rewriter
    qualifies bare table names, and an alias called ``projects`` would be read as
    the table itself.
    """
    return db.execute("""
        SELECT u.username, u.full_name, count(p.id) AS job_count,
               COALESCE(sum(p.budget), 0) AS budget,
               (SELECT count(*) FROM project_tasks AS t
                 WHERE t.assigned_user_id = u.id AND t.status <> 'Completed') AS open_tasks
        FROM projects AS p
        JOIN users AS u ON u.id = p.manager_user_id
        WHERE p.is_archived = 0 AND p.status IN ('Draft', 'Pending', 'In Progress',
                                                 'On Hold')
        GROUP BY p.manager_user_id
        ORDER BY job_count DESC, open_tasks DESC
    """).fetchall()


def upcoming_milestones(db, days=30, limit=10):
    """The next dates the site has to hold, whether or not they have passed."""
    return db.execute("""
        SELECT ms.*, p.project_code, p.project_name, p.status AS project_status
        FROM project_milestones AS ms
        JOIN projects AS p ON p.id = ms.project_id
        WHERE p.is_archived = 0 AND ms.status <> 'Completed'
          AND ms.due_date IS NOT NULL
          AND ms.due_date <= date('now', '+' || ? || ' day')
        ORDER BY ms.due_date LIMIT ?
    """, (int(days), int(limit))).fetchall()


def status_breakdown(db):
    return db.execute("""
        SELECT status, count(*) AS total, COALESCE(sum(budget), 0) AS budget,
               COALESCE(sum(committed), 0) AS committed
        FROM (
            SELECT p.status, p.budget,
                   (SELECT COALESCE(sum(c.amount), 0) FROM budget_commitments AS c
                     WHERE c.source_table = 'projects' AND c.source_id = p.id
                       AND c.status = 'Open') AS committed
            FROM projects AS p WHERE p.is_archived = 0
        )
        GROUP BY status
        ORDER BY CASE status WHEN 'Pending' THEN 0 WHEN 'Draft' THEN 1
                             WHEN 'In Progress' THEN 2 ELSE 3 END
    """).fetchall()
