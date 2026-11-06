"""Recruitment schema and the rules every recruitment screen reads.

The vacancy and application tables predate this module - the old HR screen at
/admin/hr/recruitment wrote them before the hiring desk existed, and now redirects
into it - so recruitment widens what is there instead of starting a second set of
hiring records. A department head's recruitment request, a candidate's application
and an applicant registered at the desk all land in the same two tables, which keeps
one pipeline, one history and one set of counts on the HR dashboard.

Two columns deliberately carry no CHECK constraint: the vacancy job type and the
application source. Older rows wrote free text to both, so a constraint added here
would turn a historic value into a boot failure or a rejected insert; the recruitment
screens check those values instead. Tables only recruitment writes carry their
constraints in the schema.
"""

from core.access import effective_permissions
from core.config import APPLICATION_SOURCES
from core.config import APPLICATION_STATUSES
from core.config import ATTEMPT_STATUSES
from core.config import EXAM_STATUSES
from core.config import INTERVIEW_OUTCOMES
from core.config import RECRUITMENT_CV_EXTENSIONS
from core.config import RECRUITMENT_CV_LIMIT
from core.config import RECRUITMENT_REVIEW_LEVEL
from core.config import RECRUITMENT_REQUEST_DEPARTMENT
from core.config import RECRUITMENT_REQUEST_TYPE
from core.config import RECRUITMENT_UPLOAD_DIR
from core.config import VACANCY_JOB_TYPES
from core.config import VACANCY_STATUSES
from core.db import check_in
from database import database_config
from datetime import datetime
from datetime import timedelta
from flask import g
from services.budget import add_column_if_missing
from services.workflow import account_position_level
from werkzeug.utils import secure_filename
import hashlib
import os
import secrets
import sqlite3
import uuid


# -------------------------------------------------------------------- schema
VACANCY_BODY = """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT,
        job_title TEXT NOT NULL,
        department TEXT NOT NULL,
        department_id INTEGER,
        job_type TEXT NOT NULL DEFAULT 'Full-time',
        location TEXT DEFAULT '',
        description TEXT DEFAULT '',
        requirements TEXT DEFAULT '',
        openings INTEGER NOT NULL DEFAULT 1,
        minimum_gpa REAL NOT NULL DEFAULT 0,
        minimum_age INTEGER NOT NULL DEFAULT 0,
        maximum_age INTEGER NOT NULL DEFAULT 0,
        internal_only INTEGER NOT NULL DEFAULT 0,
        estimated_cost REAL NOT NULL DEFAULT 0,
        budget_allocation_id INTEGER,
        approval_request_id INTEGER,
        created_by_id INTEGER,
        created_by_name TEXT DEFAULT '',
        hired_count INTEGER NOT NULL DEFAULT 0,
        closing_date TEXT,
        status TEXT NOT NULL DEFAULT 'Open' {status_check},
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (department_id) REFERENCES departments(id),
        FOREIGN KEY (created_by_id) REFERENCES users(id)
""".format(status_check=check_in("status", VACANCY_STATUSES))


VACANCY_DDL = "CREATE TABLE job_vacancies ({} )".format(VACANCY_BODY)


# What recruitment adds to the table the HR desk already used. An age of zero means
# "not a condition", so a vacancy written before these columns existed - or straight
# from the HR screen, which never asks for them - stays open to every applicant
# instead of failing an unseen 0-to-0 window.
VACANCY_ADDED = (
    ("reference", "TEXT"),
    ("department_id", "INTEGER"),
    ("minimum_gpa", "REAL NOT NULL DEFAULT 0"),
    ("minimum_age", "INTEGER NOT NULL DEFAULT 0"),
    ("maximum_age", "INTEGER NOT NULL DEFAULT 0"),
    ("internal_only", "INTEGER NOT NULL DEFAULT 0"),
    ("estimated_cost", "REAL NOT NULL DEFAULT 0"),
    ("budget_allocation_id", "INTEGER"),
    ("approval_request_id", "INTEGER"),
    ("created_by_id", "INTEGER"),
    ("created_by_name", "TEXT DEFAULT ''"),
    ("hired_count", "INTEGER NOT NULL DEFAULT 0"),
)

# The columns the legacy HR screen already writes, in the order a rebuild copies
# them back. Anything the live table has that is not listed is still carried across
# by name, so a column added by hand cannot be dropped by this migration.
VACANCY_LEGACY = ("job_title", "department", "job_type", "location", "description",
                  "requirements", "openings", "closing_date", "status", "created_at")


APPLICATION_DDL = """
    CREATE TABLE IF NOT EXISTS job_applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT,
        vacancy_id INTEGER NOT NULL,
        user_id INTEGER,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        date_of_birth TEXT,
        age INTEGER,
        gpa REAL,
        source TEXT NOT NULL DEFAULT 'Portal',
        education TEXT DEFAULT '',
        experience TEXT DEFAULT '',
        resume_filename TEXT DEFAULT '',
        cover_letter TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Applied' {status_check},
        screened_by_id INTEGER,
        screened_by_name TEXT DEFAULT '',
        screened_at TEXT,
        decision_note TEXT DEFAULT '',
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vacancy_id) REFERENCES job_vacancies(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
""".format(status_check=check_in("status", APPLICATION_STATUSES))


# No UNIQUE (vacancy_id, email): the HR desk registers applicants with a plain
# INSERT that would raise an integrity error, and a 500 on a legacy screen is a worse
# outcome than a duplicate the recruiter can see and reject. The recruitment form
# refuses the duplicate itself, in eligibility_problems().
APPLICATION_ADDED = (
    ("reference", "TEXT"),
    ("user_id", "INTEGER"),
    ("date_of_birth", "TEXT"),
    ("age", "INTEGER"),
    ("gpa", "REAL"),
    ("source", "TEXT NOT NULL DEFAULT 'Portal'"),
    ("screened_by_id", "INTEGER"),
    ("screened_by_name", "TEXT DEFAULT ''"),
    ("screened_at", "TEXT"),
    ("decision_note", "TEXT DEFAULT ''"),
    # The staff record a hire was registered as, and when. They stay nullable:
    # an application that never reached the desk's answer keeps its own history.
    ("employee_id", "INTEGER"),
    ("hired_at", "TEXT"),
)


EXAM_DDL = """
    CREATE TABLE IF NOT EXISTS recruitment_exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vacancy_id INTEGER NOT NULL,
        exam_title TEXT NOT NULL,
        description TEXT DEFAULT '',
        duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes > 0),
        total_marks REAL NOT NULL DEFAULT 100 CHECK (total_marks > 0),
        pass_mark REAL NOT NULL DEFAULT 50 CHECK (pass_mark >= 0),
        start_datetime TEXT,
        end_datetime TEXT,
        status TEXT NOT NULL DEFAULT 'Draft' {status_check},
        created_by_id INTEGER,
        created_by_name TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vacancy_id) REFERENCES job_vacancies(id),
        FOREIGN KEY (created_by_id) REFERENCES users(id)
    )
""".format(status_check=check_in("status", EXAM_STATUSES))


QUESTION_DDL = """
    CREATE TABLE IF NOT EXISTS exam_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        option_a TEXT NOT NULL DEFAULT '',
        option_b TEXT NOT NULL DEFAULT '',
        option_c TEXT NOT NULL DEFAULT '',
        option_d TEXT NOT NULL DEFAULT '',
        correct_answer TEXT NOT NULL DEFAULT 'A' {answer_check},
        marks REAL NOT NULL DEFAULT 1 CHECK (marks > 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (exam_id) REFERENCES recruitment_exams(id) ON DELETE CASCADE
    )
""".format(answer_check=check_in("correct_answer", ("A", "B", "C", "D")))


ATTEMPT_DDL = """
    CREATE TABLE IF NOT EXISTS exam_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL,
        application_id INTEGER NOT NULL,
        started_at TEXT,
        submitted_at TEXT,
        score REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Not Started' {status_check},
        access_token_hash TEXT,
        token_created_at TEXT,
        FOREIGN KEY (exam_id) REFERENCES recruitment_exams(id) ON DELETE CASCADE,
        FOREIGN KEY (application_id) REFERENCES job_applications(id) ON DELETE CASCADE,
        UNIQUE (exam_id, application_id)
    )
""".format(status_check=check_in("status", ATTEMPT_STATUSES))


ATTEMPT_ADDED = (
    ("access_token_hash", "TEXT"),
    ("token_created_at", "TEXT"),
)


ANSWER_DDL = """
    CREATE TABLE IF NOT EXISTS exam_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        selected_answer TEXT NOT NULL DEFAULT '',
        marks_awarded REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (attempt_id) REFERENCES exam_attempts(id) ON DELETE CASCADE,
        FOREIGN KEY (question_id) REFERENCES exam_questions(id) ON DELETE CASCADE,
        UNIQUE (attempt_id, question_id)
    )
"""


INTERVIEW_DDL = """
    CREATE TABLE IF NOT EXISTS recruitment_interviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vacancy_id INTEGER NOT NULL,
        application_id INTEGER NOT NULL,
        scheduled_at TEXT,
        location TEXT DEFAULT '',
        note TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Scheduled'
            {status_check},
        outcome TEXT NOT NULL DEFAULT 'Pending' {outcome_check},
        created_by_id INTEGER,
        created_by_name TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vacancy_id) REFERENCES job_vacancies(id),
        FOREIGN KEY (application_id) REFERENCES job_applications(id),
        FOREIGN KEY (created_by_id) REFERENCES users(id)
    )
""".format(status_check=check_in("status", ("Scheduled", "Held", "Cancelled")),
           outcome_check=check_in("outcome", INTERVIEW_OUTCOMES))


# What one panel member answers about a candidate, and the top of the scale they
# score on. Both sit in the table CHECK as well, so a screen and its schema cannot
# drift into offering different answers.
INTERVIEW_RECOMMENDATIONS = ("Hire", "Hold", "No hire")


INTERVIEW_SCORE_MAX = 10.0


PANEL_DDL = """
    CREATE TABLE IF NOT EXISTS interview_panel (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        interview_id INTEGER NOT NULL,
        panel_user_id INTEGER NOT NULL,
        panel_user_name TEXT DEFAULT '',
        added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (interview_id) REFERENCES recruitment_interviews(id)
            ON DELETE CASCADE,
        FOREIGN KEY (panel_user_id) REFERENCES users(id),
        UNIQUE (interview_id, panel_user_id)
    )
"""


SCORE_DDL = """
    CREATE TABLE IF NOT EXISTS interview_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        interview_id INTEGER NOT NULL,
        panel_user_id INTEGER NOT NULL,
        panel_user_name TEXT DEFAULT '',
        score REAL NOT NULL DEFAULT 0 CHECK (score >= 0),
        recommendation TEXT NOT NULL DEFAULT 'Hold'
            {recommendation_check},
        comment TEXT DEFAULT '',
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (interview_id) REFERENCES recruitment_interviews(id)
            ON DELETE CASCADE,
        FOREIGN KEY (panel_user_id) REFERENCES users(id),
        UNIQUE (interview_id, panel_user_id)
    )
""".format(recommendation_check=check_in("recommendation",
                                         INTERVIEW_RECOMMENDATIONS))


INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_vacancies_department ON job_vacancies"
    " (department_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_vacancies_reference ON job_vacancies (reference)",
    "CREATE INDEX IF NOT EXISTS idx_applications_vacancy ON job_applications"
    " (vacancy_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_applications_user ON job_applications"
    " (user_id, applied_at)",
    "CREATE INDEX IF NOT EXISTS idx_applications_email ON job_applications (email)",
    "CREATE INDEX IF NOT EXISTS idx_applications_employee ON job_applications"
    " (employee_id)",
    "CREATE INDEX IF NOT EXISTS idx_interviews_application ON recruitment_interviews"
    " (application_id)",
    "CREATE INDEX IF NOT EXISTS idx_scores_interview ON interview_scores (interview_id)",
    # A sitting link is looked up by its digest alone, and only one attempt may hold
    # a given digest.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_attempt_token ON exam_attempts"
    " (access_token_hash) WHERE access_token_hash IS NOT NULL",
)


def table_columns(db, table):
    return {row[1] for row in db.execute("PRAGMA table_info({})".format(table))}


def rebuild_vacancies(db):
    """Widen the vacancy status list without losing a row.

    SQLite keeps the CHECK inside the table definition, so the enumeration only
    changes by rebuilding the table. Applications refer to vacancies by table name,
    so the rebuilt table taking the old name back keeps the link intact. The foreign
    key check is off across the swap because dropping a parent table replays an
    implicit delete - and its key checks - against every child row.
    """
    live = [row[1] for row in db.execute("PRAGMA table_info(job_vacancies)").fetchall()]
    wanted = (["id"] + [name for name, _ in VACANCY_ADDED if name in live]
              + [name for name in VACANCY_LEGACY if name in live])
    copied = ", ".join(
        "COALESCE(NULLIF(status, ''), 'Open')" if name == "status"
        else '"{}"'.format(name)
        for name in wanted)
    columns = ", ".join('"{}"'.format(name) for name in wanted)
    # The scratch table lives in the vacancy file and is renamed inside it, so
    # both names carry the schema; only the rename target stays bare, as SQLite
    # requires.
    scratch = "{}.job_vacancies_next".format(
        database_config.TABLE_SCHEMA["job_vacancies"])
    db.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE {scratch} ({body} );
        INSERT INTO {scratch} ({columns})
            SELECT {copied} FROM job_vacancies;
        DROP TABLE job_vacancies;
        ALTER TABLE {scratch} RENAME TO job_vacancies;
        PRAGMA foreign_keys = ON;
    """.format(scratch=scratch, body=VACANCY_BODY, columns=columns, copied=copied))


def ensure_recruitment_schema():
    """Create or upgrade every recruitment table. Safe to call on every boot."""
    from core.db import connect_db
    db = connect_db()
    try:
        stored = db.execute(
            "SELECT sql FROM {}.sqlite_master WHERE type = 'table'"
            " AND name = 'job_vacancies'".format(
                database_config.TABLE_SCHEMA["job_vacancies"])).fetchone()
        if stored is None:
            db.execute(VACANCY_DDL)
        elif not all("'{}'".format(status) in stored[0] for status in VACANCY_STATUSES):
            rebuild_vacancies(db)
        for name, definition in VACANCY_ADDED:
            add_column_if_missing(db, "job_vacancies", name, definition)

        db.execute(APPLICATION_DDL)
        for name, definition in APPLICATION_ADDED:
            add_column_if_missing(db, "job_applications", name, definition)

        for statement in (EXAM_DDL, QUESTION_DDL, ATTEMPT_DDL, ANSWER_DDL,
                          INTERVIEW_DDL, PANEL_DDL, SCORE_DDL):
            db.execute(statement)
        # The sitting link arrived after the first draft of these tables, so an
        # existing exam database is topped up rather than rebuilt.
        for name, definition in ATTEMPT_ADDED:
            add_column_if_missing(db, "exam_attempts", name, definition)
        for statement in INDEX_STATEMENTS:
            db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ------------------------------------------------------------------ rights
# The discrete actions the hiring desks are made of. One vocabulary serves the
# screen that draws a button, the guard that refuses a POST and the audit row that
# says why, so the three can never disagree. Rights answer "may this account do
# this thing"; the department boundary in vacancy_clause still answers "which rows
# may this account see", and both have to say yes.
HIRING_RIGHTS = (
    "vacancy_view", "vacancy_request", "vacancy_publish",
    "applicant_view", "applicant_screen", "applicant_register", "applicant_hire",
    "exam_view", "exam_author", "exam_publish", "exam_result",
    "interview_view", "interview_book", "interview_score", "interview_decide",
    "hiring_report", "hiring_audit",
)

HIRING_RIGHT_LABEL = {
    "vacancy_view": "Read vacancies",
    "vacancy_request": "Raise a vacancy request",
    "vacancy_publish": "Publish and close a post",
    "applicant_view": "Read candidates and their CVs",
    "applicant_screen": "Screen candidates (shortlist, reject, select)",
    "applicant_register": "Register a walk-in applicant",
    "applicant_hire": "Register a hire as staff",
    "exam_view": "Read a paper",
    "exam_author": "Write a paper and its questions",
    "exam_publish": "Publish and close a paper",
    "exam_result": "Read examination results",
    "interview_view": "Read an interview",
    "interview_book": "Book a panel and record the desk's answer",
    "interview_score": "Score as a panel member",
    "interview_decide": "Decide an interview outcome",
    "hiring_report": "Read hiring reports",
    "hiring_audit": "Read every hiring record, write nothing",
}

# The rights that name a person the desk may move. A department seat holds them for
# its own posts only, which is what vacancy_clause already bounds.
HIRING_DEPARTMENT_RIGHTS = frozenset((
    "vacancy_request", "applicant_screen", "applicant_register", "exam_author",
    "exam_publish", "interview_book", "interview_decide", "applicant_hire"))

# What a module tick is worth in hiring terms. These are the same four authorities
# recruitment_scope has always used, written as rights instead of a level test, so
# an account keeps exactly the reach it has today until an administrator changes it.
HIRING_MODULE_RIGHTS = {
    "hr": frozenset(HIRING_RIGHTS) - frozenset(("hiring_audit",)),
    "audit": frozenset(("vacancy_view", "applicant_view", "exam_view", "exam_result",
                        "interview_view", "interview_score", "hiring_report",
                        "hiring_audit")),
    "finance": frozenset(("vacancy_view", "applicant_view", "exam_view", "exam_result",
                          "interview_view", "interview_score", "hiring_report")),
}

# A Manager, Director or CEO seat speaks for its own department: it raises posts and
# screens its candidates, but the paper and the hire stay with the HR desk. Panel
# scoring is on the list because the seat that sits on a panel answers for itself -
# interview membership, not this right, decides which desk it may score.
HIRING_LEADER_RIGHTS = frozenset((
    "vacancy_view", "vacancy_request", "applicant_view", "applicant_screen",
    "exam_view", "exam_result", "interview_view", "interview_score", "hiring_report"))

# The same three authorities written as role defaults, so an administrator can narrow
# or widen a role without touching the module tick that made it. No other role starts
# with hiring rights: a payroll, budget or staff account reaches the published board
# only, exactly as it does today.
HIRING_ROLE_BASELINE = {
    "hr": HIRING_MODULE_RIGHTS["hr"],
    "auditor": HIRING_MODULE_RIGHTS["audit"],
    "finance": HIRING_MODULE_RIGHTS["finance"],
}

# Every hiring action is taken from inside a desk screen, so a grant that names an
# action without the right to open the screen would strand it: saving a matrix always
# carries these along.
HIRING_VIEW_BASE = frozenset(("vacancy_view", "applicant_view"))

# The same list in the order a person reads a role dialog: the desk first, then the
# rights that desk is made of. Presentation only - every guard still asks the flat
# vocabulary above, so a group can be rearranged without touching an endpoint.
HIRING_RIGHT_GROUPS = (
    ("Vacancy management", ("vacancy_view", "vacancy_request", "vacancy_publish")),
    ("Candidates", ("applicant_view", "applicant_screen", "applicant_register",
                    "applicant_hire")),
    ("Examination", ("exam_view", "exam_author", "exam_publish", "exam_result")),
    ("Interview", ("interview_view", "interview_book", "interview_score",
                   "interview_decide")),
    ("Reports & oversight", ("hiring_report", "hiring_audit")),
)

HIRING_ROLE_DDL = """
    CREATE TABLE IF NOT EXISTS hiring_role_permissions (
        role_key TEXT NOT NULL,
        right TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (role_key, right)
    )
"""

HIRING_USER_DDL = """
    CREATE TABLE IF NOT EXISTS hiring_user_permissions (
        user_id INTEGER NOT NULL,
        right TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 1,
        granted_by INTEGER,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, right),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE SET NULL
    )
"""


def ensure_hiring_schema():
    """Create the two hiring grant tables. Safe to call on every boot."""
    from core.db import connect_db
    db = connect_db()
    try:
        db.execute(HIRING_ROLE_DDL)
        db.execute(HIRING_USER_DDL)
        db.execute("CREATE INDEX IF NOT EXISTS idx_hiring_user_rights"
                   " ON hiring_user_permissions (user_id)")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def stored_role_rights(db, role_key):
    """(has_rows, rights) from the editable role table alone."""
    try:
        rows = db.execute("SELECT right, allowed FROM hiring_role_permissions"
                          " WHERE role_key = ?", (role_key,)).fetchall()
    except sqlite3.Error:
        return False, set()
    if not rows:
        return False, set()
    return True, {row["right"] for row in rows
                  if row["allowed"] and row["right"] in HIRING_RIGHTS}


def role_rights(db, role_key):
    """What a role grants: its stored matrix when it has one, else the baseline."""
    has_rows, rights = stored_role_rights(db, role_key)
    if has_rows:
        return rights
    return set(HIRING_ROLE_BASELINE.get(role_key, ()))


def hiring_baseline(db, user):
    """Every hiring right this account's roles, modules and seat give it.

    This is the inheritance before the account's own rows: the baselines of the
    roles it carries - primary and additional - widened by what a module tick is
    worth in hiring terms, and by the department rights a Manager-or-above seat
    earns from its position. The permission screens compare against it, which is
    what makes a stored row a deviation rather than a second copy.
    """
    if user is None:
        return frozenset()
    if user["role"] == "admin":
        return frozenset(HIRING_RIGHTS)
    from core.access import account_role_keys
    from core.access import effective_permissions
    granted = effective_permissions(user)
    held = set()
    for role_key in account_role_keys(db, user):
        held |= role_rights(db, role_key)
    for module, rights in HIRING_MODULE_RIGHTS.items():
        if module in granted:
            held |= rights
    # A department seat earns its rights from the position, not from a tick, so the
    # reach follows the assignment screen with nothing stored to drift.
    level = account_position_level(user, db)
    if level is not None and level <= RECRUITMENT_REVIEW_LEVEL:
        held |= HIRING_LEADER_RIGHTS
    return frozenset(held)


def hiring_rights(db, user):
    """Every hiring right this account holds, widest authority winning.

    An administrator holds the whole list. Everyone else starts from what its
    roles, module ticks and position give, and the per-account rows are applied
    last and in both directions - that is how a desk borrows one right from a
    colleague or an administrator takes one away without changing anybody's role.
    Cached for the request, so the rail, the guard and the template reuse one read.
    """
    if user is None:
        return frozenset()
    if user["role"] == "admin":
        return frozenset(HIRING_RIGHTS)
    cache = g.get("_hiring_rights_cache")
    if cache is None:
        cache = {}
        g._hiring_rights_cache = cache
    if user["id"] in cache:
        return cache[user["id"]]

    held = set(hiring_baseline(db, user))
    try:
        rows = db.execute("SELECT right, allowed FROM hiring_user_permissions"
                          " WHERE user_id = ?", (user["id"],)).fetchall()
    except sqlite3.Error:
        rows = ()
    for row in rows:
        if row["right"] not in HIRING_RIGHTS:
            continue
        if row["allowed"]:
            held.add(row["right"])
        else:
            held.discard(row["right"])
    result = frozenset(held)
    cache[user["id"]] = result
    return result


def right_label(right):
    return HIRING_RIGHT_LABEL.get(right, right.replace("_", " ").title())


# --------------------------------------------------------------------- scope
def account_department(db, user):
    """(id, name) of the department this account speaks for.

    The account's own assignment wins and a linked employee row is the fallback -
    the same order the approval ladder uses. Neither is required: an account with no
    department still reaches the published board, it simply reviews nothing.
    """
    if user["department_id"]:
        row = db.execute("SELECT id, name FROM departments WHERE id = ?",
                         (user["department_id"],)).fetchone()
        if row is not None:
            return row["id"], row["name"]
    if user["employee_id"]:
        row = db.execute("SELECT department FROM employees WHERE id = ?",
                         (user["employee_id"],)).fetchone()
        name = (row["department"] or "").strip() if row is not None else ""
        if name:
            match = db.execute("SELECT id FROM departments"
                               " WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            return (match["id"] if match else None), name
    return None, ""


def recruitment_scope(db, user):
    """What this account may do in recruitment, worked out from its own record.

    Four authorities answer for a person and the widest that fits wins: the HR module
    runs hiring for the whole company, an audit account reads every record and writes
    none, a seat at Manager level or above speaks for its department, and everyone
    else reaches only the vacancies that have been published. An account whose only
    claim is a role matrix that names a right over its own people is seated at its own
    department, so a grant an administrator writes is a grant that can be used. Every
    screen calls this before it reads a row, so hiding a link is never the only answer
    - the same rules refuse the direct URL, the POST and the export.
    """
    if user is None:
        return None
    granted = effective_permissions(user)
    department_id, department = account_department(db, user)
    position_level = account_position_level(user, db)
    # The level says which rows this account may reach. The four authorities still
    # set it on their own; a role matrix only lifts an account that has none of them.
    hr_desk = user["role"] == "admin" or "hr" in granted
    can_audit = "audit" in granted
    is_leader = position_level is not None and position_level <= RECRUITMENT_REVIEW_LEVEL
    can_budget = "finance" in granted

    rights = hiring_rights(db, user)

    if hr_desk:
        level = "manage"
    elif can_audit:
        level = "audit"
    elif is_leader:
        level = "department"
    elif can_budget:
        level = "budget"
    elif (HIRING_DEPARTMENT_RIGHTS & rights) and (department_id or department):
        # A role matrix that names a right which moves a person binds that account to
        # its own department. Without this it would stay on the published board and
        # hold rights over rows it cannot reach, so the grant would be dead letter.
        level = "department"
    else:
        level = "board"

    scope = {
        "user_id": user["id"],
        "name": user["full_name"],
        "role": user["role"],
        "granted": granted,
        "department_id": department_id,
        "department": department,
        "position_level": position_level,
        "is_admin": user["role"] == "admin",
        # The level decides which rows are in reach; these rights decide which actions
        # the account may take on them. A screen asks the rights, so a grant an
        # administrator narrows is obeyed without a second rule anywhere else.
        "rights": rights,
        "can_manage": "vacancy_publish" in rights and "applicant_hire" in rights,
        # A manager or director reviews the candidates their own department raised;
        # the HR desk and the administrator review everywhere.
        "can_review": "applicant_screen" in rights,
        "can_request": "vacancy_request" in rights,
        "can_register": "applicant_register" in rights,
        "can_hire": "applicant_hire" in rights,
        "can_budget": can_budget,
        # An audit seat reads every hiring record and writes none: that is what the
        # hiring_audit right means, and a template that hides a form asks this flag.
        "read_only": level == "audit",
        "level": level,
    }
    scope["can"] = lambda right, vacancy=None: holds(scope, right, vacancy)
    return scope


def holds(scope, right, vacancy=None):
    """Whether this scope may take that action, on that row when one is named.

    Two answers have to agree: the account must hold the right, and the row must be
    inside the reach its level draws. A department seat therefore screens its own
    candidates and nothing else, while a right an administrator withheld fails even on
    a post the account can read.
    """
    if scope is None or right not in HIRING_RIGHTS:
        return False
    if right not in scope["rights"]:
        return False
    if vacancy is None or right not in HIRING_DEPARTMENT_RIGHTS:
        return True
    return can_see_vacancy(scope, vacancy)


def vacancy_clause(scope, alias="v"):
    """The WHERE fragment this scope is allowed to see, for a joined vacancy row.

    Handing back a fragment instead of running a query is what keeps a list, its
    count, its export and one record's detail page answering with the same limit -
    and it is why a row opened by id is refused rather than quietly rendered.
    """
    level = scope["level"]
    if level in ("manage", "audit"):
        return "1 = 1", ()
    if level == "department":
        parts, params = [], []
        if scope["department_id"]:
            parts.append("{}.department_id = ?".format(alias))
            params.append(scope["department_id"])
        if scope["department"]:
            parts.append("({alias}.department_id IS NULL"
                         " AND {alias}.department = ? COLLATE NOCASE)".format(alias=alias))
            params.append(scope["department"])
        parts.append("{}.created_by_id = ?".format(alias))
        params.append(scope["user_id"])
        return "(" + " OR ".join(parts) + ")", tuple(params)
    if level == "budget":
        # Finance is asked about a post once it carries money: a request it raised,
        # or a cost the HR desk attached to an open post.
        return ("({alias}.approval_request_id IS NOT NULL"
                " OR {alias}.estimated_cost > 0)").format(alias=alias), ()
    return "{}.status = 'Open'".format(alias=alias), ()


def public_board_clause():
    """What an unauthenticated visitor may read: a published post, never internal."""
    return "v.status = 'Open' AND v.internal_only = 0"


def can_see_vacancy(scope, vacancy):
    """Whether one already-read vacancy row is inside this scope."""
    if scope is None or vacancy is None:
        return False
    level = scope["level"]
    if level in ("manage", "audit"):
        return True
    if level == "board":
        return vacancy["status"] == "Open"
    if level == "budget":
        return bool(vacancy["approval_request_id"]) or \
            float(vacancy["estimated_cost"] or 0) > 0
    if vacancy["created_by_id"] == scope["user_id"]:
        return True
    if vacancy["department_id"]:
        return vacancy["department_id"] == scope["department_id"]
    wanted = (scope["department"] or "").strip().lower()
    return bool(wanted) and (vacancy["department"] or "").strip().lower() == wanted


def can_review_vacancy(scope, vacancy):
    """Review rights. An audit account never has them, whatever else it can read."""
    if scope is None or scope["read_only"] or vacancy is None:
        return False
    if scope["can_manage"]:
        return True
    return scope["can_review"] and can_see_vacancy(scope, vacancy)


# ------------------------------------------------------------------- records
def department_row(db, name, department_id=None):
    """The registered department a form names, or None.

    Never creates one: a vacancy that spells its department wrongly keeps the text
    the operator typed and links to no department id, which shows on the screen
    instead of quietly splitting the department list in two.
    """
    if department_id:
        return db.execute("SELECT id, name FROM departments WHERE id = ?",
                          (department_id,)).fetchone()
    name = (name or "").strip()
    if not name:
        return None
    return db.execute("SELECT id, name FROM departments"
                      " WHERE name = ? COLLATE NOCASE AND is_active = 1",
                      (name,)).fetchone()


def new_reference(db, table, prefix):
    """A human-readable record number, unique per year, safe against a retry.

    The number is the next free one for the year rather than a row count, so a
    deleted draft or two clerks saving in the same minute cannot reuse a number a
    printed letter already quotes.
    """
    label = "{}-{}".format(prefix, datetime.now().year)
    taken = set()
    for row in db.execute("SELECT reference FROM {} WHERE reference LIKE ?"
                          .format(table), ("{}-%".format(label),)).fetchall():
        tail = str(row[0] or "").rsplit("-", 1)[-1]
        if tail.isdigit():
            taken.add(int(tail))
    counter = 1
    while counter in taken:
        counter += 1
    return "{}-{:04d}".format(label, counter)


def age_years(date_of_birth, on=None):
    try:
        born = datetime.strptime((date_of_birth or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = on or datetime.now().date()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def clean_source(value):
    """The application channel, or the portal default for anything unrecognised."""
    value = (value or "").strip()
    return value if value in APPLICATION_SOURCES else "Portal"


def clean_job_type(value):
    value = (value or "").strip()
    return value if value in VACANCY_JOB_TYPES else "Full-time"


def duplicate_application(db, vacancy_id, email):
    """The application this email already sent for this post, if there is one.

    Checked here rather than by a UNIQUE index, because the legacy HR screen inserts
    applicants with a plain INSERT and an integrity error would be a 500 on a screen
    that has worked for years.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    return db.execute("SELECT id FROM job_applications WHERE vacancy_id = ?"
                      " AND lower(email) = ?", (vacancy_id, email)).fetchone()


def eligibility_problems(db, vacancy, values):
    """Every reason this candidate cannot apply for this post, before the insert.

    The post's own limits decide, so an application for a closing or a closed post,
    an under-age candidate or a transcript below the stated minimum is refused here
    rather than arriving as a row somebody has to remember to reject.
    """
    problems = []
    closing = (vacancy["closing_date"] or "").strip()
    if vacancy["status"] != "Open":
        problems.append("That vacancy is not accepting applications.")
    elif closing:
        try:
            closes = datetime.strptime(closing[:10], "%Y-%m-%d").date()
        except ValueError:
            closes = None
        if closes is not None and closes < datetime.now().date():
            problems.append("That vacancy closed on {}.".format(closing[:10]))
    if duplicate_application(db, vacancy["id"], values.get("email")):
        problems.append("This email address has already applied for this vacancy.")
    birth = (values.get("date_of_birth") or "").strip()
    years = age_years(birth) if birth else None
    if birth and years is None:
        problems.append("Enter the date of birth as YYYY-MM-DD.")
    elif years is not None:
        minimum = float(vacancy["minimum_age"] or 0)
        maximum = float(vacancy["maximum_age"] or 0)
        if minimum and years < minimum:
            problems.append("This post requires applicants aged {} or over."
                            .format(int(minimum)))
        if maximum and years > maximum:
            problems.append("This post is limited to applicants aged {} or under."
                            .format(int(maximum)))
    gpa = values.get("gpa")
    if gpa not in (None, ""):
        try:
            score = float(gpa)
        except (TypeError, ValueError):
            problems.append("Enter the GPA as a number, for example 3.42.")
        else:
            if not 0 <= score <= 7:
                problems.append("The GPA has to be between 0 and 7.")
            elif float(vacancy["minimum_gpa"] or 0) and score < float(vacancy["minimum_gpa"]):
                problems.append("This post requires a minimum GPA of {}."
                                .format(float(vacancy["minimum_gpa"])))
    return problems


# ------------------------------------------------------------------ cv files
def cv_upload_dir():
    RECRUITMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return RECRUITMENT_UPLOAD_DIR


def cv_size_limit():
    return "{} MB".format(RECRUITMENT_CV_LIMIT // (1024 * 1024))


def save_cv(uploaded, application_id):
    """(stored name, problem) for a submitted curriculum vitae.

    The file leaves the browser's name behind for a random one under the uploads
    directory, outside the static folder: a CV is personal data, so it is reachable
    only through a guarded download and never by guessing a URL.
    """
    original = secure_filename(uploaded.filename or "")
    extension = original.rsplit(".", 1)[1].lower() if "." in original else ""
    if extension not in RECRUITMENT_CV_EXTENSIONS:
        return None, "Attach the CV as a PDF, DOC or DOCX file."
    if uploaded.content_length and uploaded.content_length > RECRUITMENT_CV_LIMIT:
        return None, "The CV has to be {} or smaller.".format(cv_size_limit())
    stored = "{}-{}{}".format(application_id or 0, uuid.uuid4().hex[:16],
                              "." + extension)
    path = cv_upload_dir() / stored
    try:
        uploaded.save(str(path))
    except OSError:
        return None, "The CV could not be stored. Try again or send it by email."
    size = path.stat().st_size
    if size == 0:
        path.unlink()
        return None, "That CV file is empty."
    if size > RECRUITMENT_CV_LIMIT:
        path.unlink()
        return None, "The CV has to be {} or smaller.".format(cv_size_limit())
    return stored, ""


def cv_path(stored):
    """The readable path for a stored CV name, or None.

    Only a file name this module wrote resolves. Anything carrying a separator is
    refused before the filesystem is asked, so a tampered database value cannot point
    a download at another file.
    """
    stored = (stored or "").strip()
    if not stored or stored != secure_filename(stored) or "/" in stored \
            or os.sep in stored or (os.altsep and os.altsep in stored):
        return None
    path = RECRUITMENT_UPLOAD_DIR / stored
    return path if path.is_file() else None


# ------------------------------------------------------------------- requests
def request_summary(vacancy):
    """The description the approval desk reads, built from the post itself."""
    lines = ["{} post(s) of {} in {}.".format(
        int(vacancy["openings"] or 1), vacancy["job_title"], vacancy["department"])]
    if vacancy["job_type"]:
        lines.append("Employment type: {}.".format(vacancy["job_type"]))
    if vacancy["location"]:
        lines.append("Work location: {}.".format(vacancy["location"]))
    cost = float(vacancy["estimated_cost"] or 0)
    if cost > 0:
        lines.append("Estimated cost: {:,.2f} ETB.".format(cost))
    if vacancy["budget_allocation_id"]:
        lines.append("Charged to budget allocation #{}."
                     .format(vacancy["budget_allocation_id"]))
    return " ".join(lines)


def open_vacancy_request(db, vacancy):
    """Raise the company's own approval chain for a recruitment request.

    Recruitment keeps no second review status and no second queue: the request is
    the approval the approvals desk already answers, opened with the cost the post
    carries so the amount decides how many desks sign it. The chain is written back
    onto the vacancy, which is what lets that desk release the post or close it.
    """
    from services.links import open_approval
    title = "Recruitment {}: {} ({})".format(
        vacancy["reference"] or "#{}".format(vacancy["id"]), vacancy["job_title"],
        vacancy["department"])
    request_id = open_approval(
        db, "job_vacancies", vacancy["id"], title, RECRUITMENT_REQUEST_TYPE,
        RECRUITMENT_REQUEST_DEPARTMENT, float(vacancy["estimated_cost"] or 0),
        request_summary(vacancy))
    db.execute("UPDATE job_vacancies SET approval_request_id = ? WHERE id = ?",
               (request_id, vacancy["id"]))
    return request_id


# --------------------------------------------------------------------- exams
def parse_moment(value):
    value = (value or "").strip().replace("T", " ")[:19]
    if not value:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def exam_window_problem(exam, now=None):
    """Why this exam cannot be opened right now, or "" when it can."""
    now = now or datetime.now()
    opens = parse_moment(exam["start_datetime"])
    closes = parse_moment(exam["end_datetime"])
    if opens is not None and now < opens:
        return "The examination does not open until {}.".format(
            exam["start_datetime"].strip().replace("T", " "))
    if closes is not None and now > closes:
        return "The examination closed at {}.".format(
            exam["end_datetime"].strip().replace("T", " "))
    return ""


def start_attempt(db, exam_id, application_id):
    """This candidate's one attempt at this exam, started when none exists."""
    attempt = db.execute("SELECT * FROM exam_attempts WHERE exam_id = ?"
                        " AND application_id = ?", (exam_id, application_id)).fetchone()
    if attempt is None:
        result = db.execute("INSERT INTO exam_attempts (exam_id, application_id,"
                            " started_at, status) VALUES (?, ?, ?, 'In Progress')",
                            (exam_id, application_id,
                             datetime.now().isoformat(timespec="seconds")))
        attempt = db.execute("SELECT * FROM exam_attempts WHERE id = ?",
                             (result.lastrowid,)).fetchone()
    return attempt


def attempt_deadline(exam, attempt):
    """When an in-flight attempt must be handed in, whichever limit comes first."""
    started = parse_moment(attempt["started_at"])
    if started is None:
        return None
    minutes = int(exam["duration_minutes"] or 0)
    deadline = started + timedelta(minutes=minutes) if minutes else None
    closes = parse_moment(exam["end_datetime"])
    if closes is not None and (deadline is None or closes < deadline):
        deadline = closes
    return deadline


def seconds_left(exam, attempt, now=None):
    deadline = attempt_deadline(exam, attempt)
    if deadline is None:
        return None
    return max(0, int((deadline - (now or datetime.now())).total_seconds()))


def grade_attempt(db, exam, attempt, answers):
    """Score one attempt against the published key and settle it.

    Marks are worked out from the key every time and the previous answers replaced,
    so a hand-in that arrives after the timer - or a form replayed from the back
    button - cannot leave two partial answer sets behind. A late submission scores
    the unanswered paper rather than a form the candidate no longer controls.
    """
    questions = db.execute("SELECT id, correct_answer, marks FROM exam_questions"
                           " WHERE exam_id = ? ORDER BY id", (exam["id"],)).fetchall()
    deadline = attempt_deadline(exam, attempt)
    if deadline is not None and datetime.now() > deadline:
        answers = {}
    db.execute("DELETE FROM exam_answers WHERE attempt_id = ?", (attempt["id"],))
    total = 0.0
    for question in questions:
        selected = (answers.get("question_{}".format(question["id"])) or "").strip()
        if selected not in ("A", "B", "C", "D"):
            selected = ""
        awarded = float(question["marks"]) if selected == question["correct_answer"] else 0.0
        total += awarded
        db.execute("INSERT INTO exam_answers (attempt_id, question_id, selected_answer,"
                   " marks_awarded) VALUES (?, ?, ?, ?)",
                   (attempt["id"], question["id"], selected, awarded))
    db.execute("UPDATE exam_attempts SET score = ?, submitted_at = ?,"
               " status = 'Submitted', access_token_hash = NULL, token_created_at = NULL"
               " WHERE id = ? AND status = 'In Progress'",
               (total, datetime.now().isoformat(timespec="seconds"), attempt["id"]))
    return total


def exam_ranking(db, exam_id):
    """Submitted attempts, highest first, with competition ranking and pass state."""
    exam = db.execute("SELECT * FROM recruitment_exams WHERE id = ?",
                      (exam_id,)).fetchone()
    if exam is None:
        return []
    rows = db.execute("""
        SELECT a.id AS attempt_id, a.application_id, a.score, a.submitted_at,
               j.full_name, j.email, j.gpa, j.education, j.experience, j.status
        FROM exam_attempts a
        JOIN job_applications j ON j.id = a.application_id
        WHERE a.exam_id = ? AND a.status = 'Submitted'
        ORDER BY a.score DESC, a.submitted_at ASC, a.id ASC
    """, (exam_id,)).fetchall()
    ranked = []
    previous = None
    rank = 0
    for index, row in enumerate(rows, start=1):
        score = float(row["score"] or 0)
        if score != previous:
            rank = index
        ranked.append({
            "attempt_id": row["attempt_id"],
            "application_id": row["application_id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "gpa": row["gpa"],
            "education": row["education"],
            "experience": row["experience"],
            "application_status": row["status"],
            "score": score,
            "rank": rank,
            "passed": score >= float(exam["pass_mark"] or 0),
            "submitted_at": row["submitted_at"],
        })
        previous = score
    return ranked


def issue_exam_link(db, attempt):
    """A one-URL sitting link for a candidate who has no account.

    An external applicant does not get a row in the users table - that directory
    holds the company's own staff, and a second kind of account inside it would show
    up on the staff lists, the approval desks and the payroll screen. The link is the
    credential instead, so only its SHA-256 digest is stored, exactly as a
    password-reset token is, and a copied database hands over nothing replayable.
    """
    token = secrets.token_urlsafe(24)
    db.execute("UPDATE exam_attempts SET access_token_hash = ?, token_created_at = ?"
               " WHERE id = ?",
               (hashlib.sha256(token.encode("utf-8")).hexdigest(),
                datetime.now().isoformat(timespec="seconds"), attempt["id"]))
    return token


def attempt_for_link(db, token):
    """The attempt a sitting URL opens, or None for a used, revoked or wrong token."""
    digest = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    return db.execute("SELECT * FROM exam_attempts WHERE access_token_hash = ?",
                      (digest,)).fetchone()


def revoke_exam_link(db, attempt_id):
    db.execute("UPDATE exam_attempts SET access_token_hash = NULL,"
               " token_created_at = NULL WHERE id = ?", (attempt_id,))


# --------------------------------------------------------------- interviews
def invite_panel(db, interview_id, accounts):
    """Name the people expected to score one interview; existing seats are kept."""
    added = 0
    for account in accounts:
        result = db.execute("""
            INSERT OR IGNORE INTO interview_panel (interview_id, panel_user_id,
                                                   panel_user_name)
            VALUES (?, ?, ?)
        """, (interview_id, account["id"], account["full_name"]))
        added += result.rowcount
    return added


def is_panel_member(db, interview_id, user_id):
    return db.execute("SELECT 1 FROM interview_panel WHERE interview_id = ?"
                      " AND panel_user_id = ?", (interview_id, user_id)).fetchone() \
        is not None


def record_interview_score(db, interview, user, score, recommendation, comment):
    """One panel member's decision, stored once per person per interview."""
    db.execute("""
        INSERT INTO interview_scores (interview_id, panel_user_id, panel_user_name,
                                      score, recommendation, comment, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (interview_id, panel_user_id) DO UPDATE
            SET score = excluded.score,
                recommendation = excluded.recommendation,
                comment = excluded.comment,
                recorded_at = excluded.recorded_at,
                panel_user_name = excluded.panel_user_name
    """, (interview["id"], user["id"], user["full_name"], score, recommendation,
          comment, datetime.now().isoformat(timespec="seconds")))


def interview_rollup(db, application_id):
    """The newest live interview for this candidate, with its panel's figures."""
    interview = db.execute("""
        SELECT * FROM recruitment_interviews
        WHERE application_id = ? AND status <> 'Cancelled'
        ORDER BY id DESC LIMIT 1
    """, (application_id,)).fetchone()
    if interview is None:
        return None
    rows = db.execute("""
        SELECT score, recommendation, panel_user_name, comment
        FROM interview_scores WHERE interview_id = ? ORDER BY id
    """, (interview["id"],)).fetchall()
    tally = {}
    for row in rows:
        tally[row["recommendation"]] = tally.get(row["recommendation"], 0) + 1
    return {
        "interview": interview,
        "count": len(rows),
        "expected": db.execute("SELECT COUNT(*) AS n FROM interview_panel"
                               " WHERE interview_id = ?", (interview["id"],)).fetchone()["n"],
        "average": (round(sum(float(row["score"] or 0) for row in rows) / len(rows), 2)
                    if rows else 0.0),
        "recommendations": tally,
        "scores": rows,
    }


# ------------------------------------------------------------------ roll-ups
def pipeline(db, clause, params):
    """Application stage counts for the vacancies this scope can see."""
    row = db.execute("""
        SELECT COUNT(*) AS applications,
               SUM(CASE WHEN a.status = 'Applied' THEN 1 ELSE 0 END) AS applied,
               SUM(CASE WHEN a.status = 'Shortlisted' THEN 1 ELSE 0 END) AS shortlisted,
               SUM(CASE WHEN a.status = 'Interview' THEN 1 ELSE 0 END) AS interview,
               SUM(CASE WHEN a.status = 'Selected' THEN 1 ELSE 0 END) AS selected,
               SUM(CASE WHEN a.status = 'Rejected' THEN 1 ELSE 0 END) AS rejected,
               SUM(CASE WHEN a.status = 'Hired' THEN 1 ELSE 0 END) AS hired
        FROM job_applications a
        JOIN job_vacancies v ON v.id = a.vacancy_id
        WHERE {}
    """.format(clause), params).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def sync_hired_count(db, vacancy_id):
    """Keep the filled-post figure equal to the applications marked hired."""
    db.execute("UPDATE job_vacancies SET hired_count ="
               " (SELECT COUNT(*) FROM job_applications WHERE vacancy_id = ?"
               " AND status = 'Hired') WHERE id = ?", (vacancy_id, vacancy_id))


# ----------------------------------------------------- hire into staff records
# The post's employment type decided at advertisement is the contract the hire
# signs under, so the staff form opens on the matching employment type rather than
# asking the desk to read it off the vacancy a second time.
EMPLOYMENT_TYPE_BY_JOB = {
    "Full-time": "Permanent",
    "Part-time": "Temporary",
    "Contract": "Contract",
    "Temporary": "Temporary",
    "Internship": "Intern",
}


CONVERSION_STAGES = ("Selected", "Hired")


def hire_right_problem(db, user, vacancy_id):
    """Why this account may not register this post's hire as staff, or "".

    The employee screens live in the HR module and the pipeline lives in recruitment,
    so the hand-over between them is the one place a hiring right has to be re-asked:
    an account holding the HR module but no hire right must not close a pipeline it
    cannot even see, and a department seat must not hire outside its own posts.
    """
    scope = recruitment_scope(db, user)
    if scope is None:
        return "This account has no reach into the hiring desks."
    vacancy = db.execute("SELECT * FROM job_vacancies WHERE id = ?",
                         (vacancy_id,)).fetchone()
    if vacancy is None:
        return "That post no longer exists."
    if not scope["can"]("applicant_hire", vacancy):
        return ("Registering a hire needs the hire right, and this account does not"
                " hold it for this post.")
    return ""


def conversion_target(db, application_id):
    """The selected candidate whose staff record may be opened, or why not.

    Both the desk button and the employee form ask this before anything is read or
    written, so a candidate already registered cannot be hired twice into the
    directory and a person still in the pipeline cannot skip the desk's answer.
    """
    row = db.execute("""
        SELECT a.id AS application_id, a.status AS application_status,
               a.employee_id, a.hired_at, a.full_name, a.email, a.phone,
               a.date_of_birth, a.gpa, a.education, a.experience, a.user_id,
               v.id AS vacancy_id, v.job_title, v.department, v.location,
               v.job_type, v.openings, v.hired_count,
               v.reference AS vacancy_reference, v.created_by_id
        FROM job_applications a
        JOIN job_vacancies v ON v.id = a.vacancy_id
        WHERE a.id = ?
    """, (application_id,)).fetchone()
    if row is None:
        return None, "That application no longer exists."
    if row["employee_id"]:
        return None, ("{} already has an employee record; edit it from the staff"
                      " directory instead of registering them again."
                      .format(row["full_name"]))
    if row["application_status"] not in CONVERSION_STAGES:
        return None, ("Only a selected candidate can be registered as staff. Move"
                      " the pipeline to Selected first.")
    return row, ""


def employee_seed_from_application(target):
    """The staff-form values this application already answers.

    Nothing here invents a structure the application form never asked for: the
    free-text qualification and GPA ride into the notes so the record and the
    pipeline never tell two stories about the same transcript.
    """
    lines = ["Hired from application for the {} post ({}) in {}.".format(
        target["job_title"], target["vacancy_reference"] or
        "vacancy #{}".format(target["vacancy_id"]), target["department"])]
    if target["gpa"] is not None:
        lines.append("GPA {}.".format(float(target["gpa"])))
    if (target["education"] or "").strip():
        lines.append("Qualification: {}.".format(target["education"].strip()))
    return {
        "full_name": target["full_name"],
        "email": target["email"] or "",
        "phone": target["phone"] or "",
        "date_of_birth": target["date_of_birth"] or "",
        "department": target["department"],
        "job_title": target["job_title"],
        "employment_type": EMPLOYMENT_TYPE_BY_JOB.get(
            target["job_type"], "Permanent"),
        "work_location": target["location"] or "",
        "work_experience": (target["experience"] or "")[:1000],
        "hire_date": datetime.now().date().isoformat(),
        "notes": " ".join(lines)[:1000],
    }


def mark_application_hired(db, target, employee_id):
    """Close the pipeline on the application and point it at the staff record."""
    db.execute("""
        UPDATE job_applications SET status = 'Hired', employee_id = ?, hired_at = ?
        WHERE id = ?
    """, (employee_id, datetime.now().isoformat(timespec="seconds"),
          target["application_id"]))
    sync_hired_count(db, target["vacancy_id"])
