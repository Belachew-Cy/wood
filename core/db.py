"""SQLite connections, schema migrations and database maintenance."""

from core.config import BACKUP_DIR
from core.config import MODULES
from core.config import USER_ROLES
from core.config import USER_STATUS
from core.config import env_flag
from database import database_config
from datetime import datetime
from flask import g
from pathlib import Path
from werkzeug.security import generate_password_hash
import finance
import letters_db
import os
import secrets
import sqlite3
import sys


def connect_db(path=None):
    """One connection carrying the pragmas every caller relies on.

    The connection opens the user database and attaches a module file the first
    time a statement names a table that lives in it, so a request pays for the
    modules it actually reads. WAL is set once per file at boot (it is a
    property of the file); each connection gets a busy timeout instead, so two
    desks writing at tea time wait for each other rather than failing with
    "database is locked".
    """
    return database_config.connect(path)


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


FINANCE_SCHEMA = "finance"
LETTER_SCHEMA = "letter"
FINANCE_TABLES = frozenset(name for name, schema
                           in database_config.TABLE_SCHEMA.items()
                           if schema == FINANCE_SCHEMA)
LETTER_TABLES = frozenset(name for name, schema
                          in database_config.TABLE_SCHEMA.items()
                          if schema == LETTER_SCHEMA)


def qualified_table(table):
    return database_config.qualified(table)


def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def check_in(column, values, blank=False):
    """A CHECK clause over one vocabulary column.

    A descriptive column may default to the empty string, so its check lets blank
    through as well; a status or a currency always carries one of the words. Every
    desk builds its selects from the same tuple it hands here, so a stale browser
    tab cannot invent a value the table would not accept.
    """
    quoted = ", ".join(repr(v) for v in values)
    if blank:
        return "CHECK ({0} = '' OR {0} IN ({1}))".format(column, quoted)
    return f"CHECK ({column} IN ({quoted}))"


USERS_DDL = """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL {roles},
                status TEXT NOT NULL DEFAULT 'Active' {status},
                last_login TEXT
            )
""".format(roles=check_in("role", USER_ROLES), status=check_in("status", USER_STATUS))


USERS_BASE_COLUMNS = ("id", "full_name", "email", "phone", "password_hash",
                      "role", "status", "last_login")


USERS_EXTRA_COLUMNS = (
    ("username", "TEXT"),
    ("employee_id", "INTEGER"),
    ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("password_updated_at", "TEXT"),
    ("department_id", "INTEGER"),
    ("position_id", "INTEGER"),
)


USERS_RETIRED_COLUMNS = ("organizational_title",)


def migrate_users(db):
    """Bring users up to date: full staff role list, account status, last login.

    SQLite stores CHECK constraints inside the table definition, so widening the
    role list means rebuilding the table. Every row, password hash and later
    column (organizational title, username, employee link, password flags) is
    copied across; extra columns found on the live table but not listed here are
    rebuilt from PRAGMA so future migrations cannot silently lose data.
    """
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    if row is None:
        return
    definition = row[0]
    info = db.execute("PRAGMA table_info(users)").fetchall()
    columns = {entry[1] for entry in info}
    if (all(f"'{role}'" in definition for role in USER_ROLES)
            and "status" in columns and "last_login" in columns
            and all(name in columns for name, _ in USERS_EXTRA_COLUMNS)):
        return

    extras = list(USERS_EXTRA_COLUMNS)
    known = (set(USERS_BASE_COLUMNS) | {name for name, _ in extras}
             | set(USERS_RETIRED_COLUMNS))
    for entry in info:
        name, type_, notnull, default, _pk = entry[1], entry[2], entry[3], entry[4], entry[5]
        if name in known:
            continue
        extra = f'"{name}" {type_}'.rstrip()
        if notnull:
            extra += " NOT NULL"
        if default is not None:
            extra += f" DEFAULT {default}"
        extras.append((name, extra.replace(f'"{name}" ', "", 1)))
        known.add(name)
    extra_definitions = "".join(
        f",\n                {name} {definition}"
        if not definition.startswith('"') else f",\n                {definition}"
        for name, definition in extras
    )
    copied = [name for name in [entry[1] for entry in info]
              if name in known and name not in USERS_RETIRED_COLUMNS]
    column_list = ", ".join(f'"{name}"' for name in copied)
    select_list = ", ".join(
        ("COALESCE(status, 'Active')" if name == "status" else f'"{name}"')
        for name in copied
    )
    db.executescript(f"""
        CREATE TABLE users_next (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL {check_in('role', USER_ROLES)},
            status TEXT NOT NULL DEFAULT 'Active' {check_in('status', USER_STATUS)},
            last_login TEXT{extra_definitions}
        );

        INSERT INTO users_next ({column_list})
            SELECT {select_list} FROM users;

        DROP TABLE users;
        ALTER TABLE users_next RENAME TO users;
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_users_employee ON users(employee_id);
    """)


USER_PERMISSIONS_DDL = """
            CREATE TABLE user_permissions (
                user_id INTEGER NOT NULL,
                module TEXT NOT NULL {modules},
                allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, module),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
""".format(modules=check_in("module", MODULES))


def migrate_user_permissions(db):
    """Widen the module list stored in user_permissions when a module is added.

    Like the users table, SQLite keeps the CHECK inside the definition, so the
    table is rebuilt and the existing per-account ticks are copied back.
    """
    definition = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_permissions'"
    ).fetchone()
    if definition is None:
        db.execute(USER_PERMISSIONS_DDL)
        return
    if all(f"'{module}'" in definition[0] for module in MODULES):
        return

    db.executescript(f"""
        CREATE TABLE user_permissions_next (
            user_id INTEGER NOT NULL,
            module TEXT NOT NULL {check_in('module', MODULES)},
            allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, module),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        INSERT INTO user_permissions_next (user_id, module, allowed, updated_at)
            SELECT user_id, module, allowed, updated_at FROM user_permissions;

        DROP TABLE user_permissions;
        ALTER TABLE user_permissions_next RENAME TO user_permissions;
    """)


ROLES_DDL = """
    CREATE TABLE IF NOT EXISTS roles (
        key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        landing TEXT NOT NULL DEFAULT 'dashboard',
        is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
        is_standard INTEGER NOT NULL DEFAULT 0 CHECK (is_standard IN (0, 1)),
        department_id INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""


ROLE_PERMISSIONS_DDL = """
    CREATE TABLE IF NOT EXISTS role_permissions (
        role_key TEXT NOT NULL,
        module TEXT NOT NULL,
        PRIMARY KEY (role_key, module),
        FOREIGN KEY (role_key) REFERENCES roles(key) ON DELETE CASCADE
    )
"""


USER_ROLES_DDL = """
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id INTEGER NOT NULL,
        role_key TEXT NOT NULL,
        assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, role_key),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (role_key) REFERENCES roles(key) ON DELETE CASCADE
    )
"""


# The fine-grained ticks behind the six permission categories. Keys are checked
# against the catalogue in Python rather than a CHECK constraint, so adding a
# permission needs no table rebuild and old rows stay readable. A role with no
# rows at all falls back to what its module ticks imply, which is what keeps
# every existing role working the day this table is created.
ROLE_PERMISSION_GRANTS_DDL = """
    CREATE TABLE IF NOT EXISTS role_permission_grants (
        role_key TEXT NOT NULL,
        permission TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 1 CHECK (allowed IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (role_key, permission),
        FOREIGN KEY (role_key) REFERENCES roles(key) ON DELETE CASCADE
    )
"""


USER_PERMISSION_GRANTS_DDL = """
    CREATE TABLE IF NOT EXISTS user_permission_grants (
        user_id INTEGER NOT NULL,
        permission TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 1 CHECK (allowed IN (0, 1)),
        granted_by INTEGER,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, permission),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE SET NULL
    )
"""


# A binding says "accounts in this unit, at this rung, also carry this role".
# department_id 0 is the whole company and position_level -1 is every rung, so
# the primary key can carry a NULL-free answer. Departments live in the HR file,
# so the id is checked in Python rather than by a second database's foreign key;
# the role key does cascade, because roles and bindings share the user file.
DEPARTMENT_ROLES_DDL = """
    CREATE TABLE IF NOT EXISTS department_roles (
        department_id INTEGER NOT NULL DEFAULT 0,
        role_key TEXT NOT NULL,
        position_level INTEGER NOT NULL DEFAULT -1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (department_id, role_key, position_level),
        FOREIGN KEY (role_key) REFERENCES roles(key) ON DELETE CASCADE
    )
"""


def ensure_permission_grant_schema(db):
    db.execute(ROLE_PERMISSION_GRANTS_DDL)
    db.execute(USER_PERMISSION_GRANTS_DDL)
    db.execute("CREATE INDEX IF NOT EXISTS idx_user_permission_grants_user"
               " ON user_permission_grants (user_id)")



def init_db():
    from core.access import init_roles
    from services.accounts import migrate_account_identity, migrate_login_locks, reconcile_employee_links
    from services.budget import init_budget_schema
    from services.department_access import create_standard_roles
    from services.links import migrate_approval_links
    from services.org import ensure_org_tables, migrate_account_assignments
    from services.procurement import migrate_procurement_capture
    from services.workflow import init_approval_routes, init_management_policy, migrate_approval_policy
    db = connect_db()
    # Write-ahead logging survives across connections and lets readers work while
    # a desk is saving.
    db.execute("PRAGMA journal_mode = WAL")

    try:
        db.execute(USERS_DDL)
        # Sign-in throttling lives in the database so every worker process sees
        # the same attempt count.
        db.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                ip TEXT NOT NULL DEFAULT '',
                attempted_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_identifier"
                   " ON login_attempts(identifier, attempted_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip"
                   " ON login_attempts(ip, attempted_at)")
        # Password-reset requests are recorded separately from sign-in attempts so
        # asking for help never counts against the account's login allowance.
        db.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                user_id INTEGER,
                ip TEXT NOT NULL DEFAULT '',
                requested_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_identifier"
                   " ON password_reset_requests(identifier, requested_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_ip"
                   " ON password_reset_requests(ip, requested_at)")
        # A reset link is a bearer secret, so only its SHA-256 digest is stored.
        # One row per issued link lets a used or superseded link be refused without
        # keeping anything that can be replayed.
        db.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                requested_ip TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_reset_tokens_user"
                   " ON password_reset_tokens(user_id, created_at)")
        # One row per delivery attempt: the status, the timestamp and the reason a
        # message did not leave. event_key is the duplicate guard for a repeat of
        # the same event to the same mailbox.
        db.execute("""
            CREATE TABLE IF NOT EXISTS email_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                recipient TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'system',
                event_key TEXT,
                status TEXT NOT NULL
                    CHECK (status IN ('Sent', 'Failed', 'Skipped')),
                error TEXT DEFAULT '',
                related_type TEXT,
                related_id TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_email_delivery_time"
                   " ON email_deliveries(finished_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_email_delivery_event"
                   " ON email_deliveries(recipient, event_key, status)")
        # Department and position assignment lives on the account. The tables
        # have to exist before the retired signing titles are folded into
        # positions and the old column is dropped from the rebuilt user table.
        ensure_org_tables(db)
        migrate_account_assignments(db)
        migrate_users(db)
        migrate_user_permissions(db)
        init_roles(db)
        ensure_permission_grant_schema(db)
        init_management_policy(db)
        init_approval_routes(db)

        # Create HR employees table
        db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_code TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE,
                phone TEXT DEFAULT '',
                department TEXT NOT NULL,
                job_title TEXT NOT NULL,
                gender TEXT DEFAULT '',
                salary REAL NOT NULL DEFAULT 0,
                hire_date TEXT,
                employment_status TEXT NOT NULL DEFAULT 'Active'
                    CHECK (
                        employment_status IN (
                            'Active', 'On Leave', 'Inactive'
                        )
                    ),
                date_of_birth TEXT,
                address TEXT DEFAULT '',
                emergency_contact_name TEXT DEFAULT '',
                emergency_contact_phone TEXT DEFAULT '',
                employment_type TEXT DEFAULT 'Permanent',
                work_location TEXT DEFAULT '',
                education_level TEXT DEFAULT '',
                field_of_study TEXT DEFAULT '',
                institution TEXT DEFAULT '',
                graduation_year INTEGER,
                certifications TEXT DEFAULT '',
                work_experience TEXT DEFAULT '',
                required_documents TEXT DEFAULT '[]',
                education_documents TEXT DEFAULT '[]',
                supporting_documents TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                manager_id INTEGER,
                position_id INTEGER,
                profile_photo TEXT DEFAULT '',
                user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create HR attendance table
        db.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (
                        status IN ('Present', 'Absent', 'Late', 'Half Day')
                    ),
                check_in TEXT,
                check_out TEXT,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                UNIQUE (employee_id, attendance_date)
            )
        """)

        # Overtime is one request per worked extra session: the manager decision,
        # the HR verification, the attendance day it is matched against and the
        # payroll import all point at this row rather than re-deriving it.
        db.execute("""
            CREATE TABLE IF NOT EXISTS hr_overtime_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                overtime_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                break_minutes INTEGER NOT NULL DEFAULT 0,
                requested_hours NUMERIC NOT NULL DEFAULT 0,
                approved_hours NUMERIC NOT NULL DEFAULT 0,
                multiplier NUMERIC NOT NULL DEFAULT 1.5,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending Manager'
                    CHECK (status IN (
                        'Pending Manager', 'Pending HR', 'Approved', 'Rejected'
                    )),
                attendance_id INTEGER,
                manager_id INTEGER,
                manager_comment TEXT DEFAULT '',
                manager_decided_at TEXT,
                hr_user_id INTEGER,
                hr_comment TEXT DEFAULT '',
                hr_decided_at TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (attendance_id) REFERENCES attendance(id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hr_overtime_employee_date
                ON hr_overtime_requests(employee_id, overtime_date)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hr_overtime_status
                ON hr_overtime_requests(status, overtime_date)
        """)
        # One row per transition; the request itself keeps only the latest
        # decision, so this trail is what the overtime audit page reads.
        db.execute("""
            CREATE TABLE IF NOT EXISTS hr_overtime_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                overtime_request_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                previous_status TEXT,
                new_status TEXT,
                performed_by INTEGER,
                comment TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (overtime_request_id)
                    REFERENCES hr_overtime_requests(id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hr_overtime_audit_request
                ON hr_overtime_audit_logs(overtime_request_id)
        """)

        # Create payroll table
        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                payroll_month TEXT NOT NULL,
                basic_salary REAL NOT NULL CHECK (basic_salary >= 0),
                allowances REAL NOT NULL DEFAULT 0 CHECK (allowances >= 0),
                deductions REAL NOT NULL DEFAULT 0 CHECK (deductions >= 0),
                net_salary REAL NOT NULL CHECK (net_salary >= 0),
                payment_status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (payment_status IN ('Pending', 'Paid')),
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                UNIQUE (employee_id, payroll_month)
            )
        """)

        # The recruitment tables - job_vacancies and job_applications - belong to
        # services.recruitment, which is where their columns and status lists are
        # kept in step with the screens that read them. app.py calls it after this.

        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(employees)")}
        for column, definition in (
            ("gender", "TEXT DEFAULT ''"),
            ("salary", "REAL NOT NULL DEFAULT 0"),
            ("date_of_birth", "TEXT"),
            ("address", "TEXT DEFAULT ''"),
            ("emergency_contact_name", "TEXT DEFAULT ''"),
            ("emergency_contact_phone", "TEXT DEFAULT ''"),
            ("employment_type", "TEXT DEFAULT 'Permanent'"),
            ("work_location", "TEXT DEFAULT ''"),
            ("education_level", "TEXT DEFAULT ''"),
            ("field_of_study", "TEXT DEFAULT ''"),
            ("institution", "TEXT DEFAULT ''"),
            ("graduation_year", "INTEGER"),
            ("certifications", "TEXT DEFAULT ''"),
            ("work_experience", "TEXT DEFAULT ''"),
            ("required_documents", "TEXT DEFAULT '[]'"),
            ("education_documents", "TEXT DEFAULT '[]'"),
            ("supporting_documents", "TEXT DEFAULT '[]'"),
            ("notes", "TEXT DEFAULT ''"),
            ("manager_id", "INTEGER"),
            ("position_id", "INTEGER"),
            ("profile_photo", "TEXT DEFAULT ''"),
            ("user_id", "INTEGER"),
        ):
            if column not in existing_columns:
                db.execute(f"ALTER TABLE employees ADD COLUMN {column} {definition}")

        # Finance keeps its money records in finance.db; finance.init() below
        # creates that schema and moves any inline finance rows into it.

        # Create the inventory module tables in the same database as the accounts.
        db.execute("""
            CREATE TABLE IF NOT EXISTS inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                unit TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                min_stock REAL NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
                unit_cost REAL NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
                supplier TEXT DEFAULT '',
                location TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        inventory_columns = {
            row[1] for row in db.execute("PRAGMA table_info(inventory_items)")
        }
        for column, definition in (
            ("updated_by", "INTEGER"),
            ("updated_by_name", "TEXT"),
            ("updated_at", "TEXT"),
            # How many to buy when the shelf runs low; zero falls back to the
            # shortfall against the minimum level.
            ("reorder_qty", "REAL NOT NULL DEFAULT 0"),
        ):
            if column not in inventory_columns:
                db.execute("ALTER TABLE inventory_items ADD COLUMN {} {}".format(
                    column, definition))

        db.execute("""
            CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                movement_type TEXT NOT NULL CHECK (movement_type IN ('IN', 'OUT')),
                quantity REAL NOT NULL CHECK (quantity > 0),
                reference_type TEXT NOT NULL DEFAULT 'Manual',
                reference_no TEXT DEFAULT '',
                procurement_order_id INTEGER,
                notes TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES inventory_items(id)
            )
        """)

        movement_columns = {
            row[1] for row in db.execute("PRAGMA table_info(stock_movements)")
        }
        if "procurement_order_id" not in movement_columns:
            # The order lives in finance.db, so stock.db keeps its id without a
            # foreign key; the movement screen checks it against the order desk.
            db.execute("ALTER TABLE stock_movements"
                       " ADD COLUMN procurement_order_id INTEGER")

        # A stocktake freezes the shelf as the system knows it, records what the
        # counter actually found, and - once approved - posts the differences as
        # ordinary stock movements. No line ever edits a balance directly.
        db.execute("""
            CREATE TABLE IF NOT EXISTS inventory_stocktakes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                count_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'COUNTING'
                    CHECK (status IN ('COUNTING', 'SUBMITTED', 'APPROVED',
                                      'POSTED', 'REJECTED')),
                notes TEXT DEFAULT '',
                created_by INTEGER NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                submitted_at TEXT,
                decided_at TEXT,
                decided_by INTEGER,
                decided_by_name TEXT,
                decision_note TEXT DEFAULT '',
                posted_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS inventory_stocktake_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stocktake_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                expected_qty REAL NOT NULL DEFAULT 0,
                counted_qty REAL,
                variance REAL NOT NULL DEFAULT 0,
                counted_by_name TEXT,
                counted_at TEXT,
                UNIQUE (stocktake_id, item_id),
                FOREIGN KEY (stocktake_id)
                    REFERENCES inventory_stocktakes(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES inventory_items(id)
            )
        """)

        # ---------- audit trail ----------
        # One row per security-relevant event. Written best-effort by
        # log_activity(); a failure here must never break the route that
        # triggered it, so the columns stay forgiving.
        db.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                user_id TEXT,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                module TEXT NOT NULL,
                description TEXT NOT NULL,
                record_type TEXT,
                record_id TEXT,
                ip_address TEXT,
                outcome TEXT NOT NULL DEFAULT 'SUCCESS'
                    CHECK (outcome IN ('SUCCESS', 'FAILURE', 'DENIED')),
                details_json TEXT
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_occurred_at ON audit_logs(occurred_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_logs(username)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)")

        # ---------- procurement ----------
        # Purchasing lives in finance.db beside the money it commits;
        # finance.init() creates it and moves a legacy copy over.

        # ---------- approval workflow ----------
        # current_approver_step holds an APPROVAL_STEPS key, not a role name, so
        # the chain resolves against the same per-account ticks every other
        # module uses.
        db.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                request_type TEXT NOT NULL
                    CHECK (request_type IN (
                        'Purchase', 'Expense', 'Leave', 'Stock Adjustment',
                        'Payroll', 'Project', 'Other'
                    )),
                department TEXT NOT NULL
                    CHECK (department IN (
                        'hr', 'finance', 'payroll', 'inventory', 'procurement',
                        'production', 'sales', 'general'
                    )),
                amount REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
                description TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_by_name TEXT NOT NULL,
                current_approver_step TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Returned')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status)")

        db.execute("""
            CREATE TABLE IF NOT EXISTS approval_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                actor_id INTEGER,
                actor_name TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                comments TEXT DEFAULT '',
                action_at TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES approval_requests(id)
                    ON DELETE CASCADE
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_actor "
            "ON approval_actions(request_id, action_at)"
        )

        # ---------- role-based notifications ----------
        # One row per account, so read/unread belongs to the person rather than
        # to the event. dedupe_key keeps a standing alert (low stock, a pending
        # payroll) to a single row; announcements get one key per announcement.
        db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT 'system',
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                href TEXT DEFAULT '',
                icon TEXT DEFAULT 'bell',
                tone TEXT DEFAULT 'neutral',
                dedupe_key TEXT,
                read_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user"
                   " ON notifications(user_id, read_at)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe"
                   " ON notifications(user_id, dedupe_key)"
                   " WHERE dedupe_key IS NOT NULL")

        # Announcements keep their own row so the administrator can see what was
        # sent to whom, even after every recipient has read it.
        db.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                href TEXT DEFAULT '',
                audience TEXT NOT NULL DEFAULT 'all',
                created_by INTEGER,
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # An empty database still needs one administrator to sign in with, but a
        # password written in the source is a credential anyone who reads it can
        # use - so the first administrator takes WK_ADMIN_PASSWORD or a random
        # password printed once, and has to replace it at the first sign-in. The
        # demo colleague account is a teaching convenience and stays opt-in.
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            admin_password = os.environ.get("WK_ADMIN_PASSWORD", "").strip()
            generated = ""
            if not admin_password:
                admin_password = generated = secrets.token_urlsafe(12)
            db.execute(
                "INSERT INTO users (full_name, email, phone, password_hash,"
                " role, must_change_password)"
                " VALUES (?, ?, ?, ?, 'admin', 1)",
                ("Dereje Belay (Administrator)", "admin@derejebelay.com",
                 "+251911123456", generate_password_hash(admin_password)),
            )
            if generated:
                print("First run: the administrator (admin@derejebelay.com) was"
                      " created with this one-time password: {}\nIt is shown here"
                      " once and must be changed at the first sign-in."
                      .format(generated), file=sys.stderr)
            if env_flag("WK_ALLOW_DEMO_SEED"):
                db.execute(
                    "INSERT INTO users (full_name, email, phone, password_hash,"
                    " role, must_change_password) VALUES (?, ?, ?, ?, 'member', 1)",
                    ("Member Demo", "member@derejebelay.com", "+251911987654",
                     generate_password_hash("Member@123")),
                )

        # ---------- identity, purchasing and chain links ----------
        # These run after the tables above and after the seed block, because a
        # brand new database has to mint a username for the demo accounts too.
        migrate_account_identity(db)
        migrate_login_locks(db)
        reconcile_employee_links(db)
        # The finance file owns the purchasing tables; the copy out of this one
        # runs before anything reads a requisition or an order.
        finance.init(db)
        letters_db.init(db)
        # The standard role bundles are seeded after the register exists, because
        # a correspondence role also writes its starting letter actions. Seeding
        # is additive: an existing role keeps whatever the administrator set.
        create_standard_roles(db)
        init_budget_schema(db)
        migrate_procurement_capture(db)
        migrate_approval_links(db)
        migrate_approval_policy(db)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


INDEX_STATEMENTS = (
    ("employees", "CREATE INDEX IF NOT EXISTS idx_employees_status"
                  " ON employees(employment_status)"),
    ("employees", "CREATE INDEX IF NOT EXISTS idx_employees_user"
                  " ON employees(user_id)"),
    ("employees", "CREATE INDEX IF NOT EXISTS idx_employees_position"
                  " ON employees(position_id)"),
    ("attendance", "CREATE INDEX IF NOT EXISTS idx_attendance_date"
                   " ON attendance(attendance_date)"),
    ("attendance", "CREATE INDEX IF NOT EXISTS idx_attendance_employee"
                   " ON attendance(employee_id, attendance_date)"),
    ("payroll", "CREATE INDEX IF NOT EXISTS idx_payroll_month"
                " ON payroll(payroll_month)"),
    ("payroll", "CREATE INDEX IF NOT EXISTS idx_payroll_status"
                " ON payroll(payment_status)"),
    ("payroll", "CREATE INDEX IF NOT EXISTS idx_payroll_employee"
                " ON payroll(employee_id, payroll_month)"),
    ("leave_requests", "CREATE INDEX IF NOT EXISTS idx_leave_status"
                       " ON leave_requests(status)"),
    ("leave_requests", "CREATE INDEX IF NOT EXISTS idx_leave_employee"
                       " ON leave_requests(employee_id, start_date)"),
    ("employee_training", "CREATE INDEX IF NOT EXISTS idx_training_employee"
                          " ON employee_training(employee_id, start_date)"),
    ("performance_reviews", "CREATE INDEX IF NOT EXISTS idx_reviews_employee"
                            " ON performance_reviews(employee_id, review_date)"),
    ("inventory_items", "CREATE INDEX IF NOT EXISTS idx_inventory_active"
                        " ON inventory_items(active, quantity)"),
    ("stock_movements", "CREATE INDEX IF NOT EXISTS idx_stock_movements_item"
                        " ON stock_movements(item_id, created_at)"),
    ("inventory_stocktakes", "CREATE INDEX IF NOT EXISTS idx_stocktake_status"
                             " ON inventory_stocktakes(status, count_date)"),
    ("inventory_stocktake_lines",
     "CREATE INDEX IF NOT EXISTS idx_stocktake_line_session"
     " ON inventory_stocktake_lines(stocktake_id)"),
    ("budget_requests", "CREATE INDEX IF NOT EXISTS idx_budget_req_status"
                        " ON budget_requests(status, created_at)"),
    ("budget_allocations", "CREATE INDEX IF NOT EXISTS idx_budget_alloc_plan"
                           " ON budget_allocations(plan_id)"),
    ("approval_actions", "CREATE INDEX IF NOT EXISTS idx_approval_actions_request"
                         " ON approval_actions(request_id)"),
    ("approval_requests", "CREATE INDEX IF NOT EXISTS idx_approval_creator"
                          " ON approval_requests(created_by, created_at)"),
    ("notifications", "CREATE INDEX IF NOT EXISTS idx_notifications_created"
                      " ON notifications(user_id, created_at)"),
    ("audit_logs", "CREATE INDEX IF NOT EXISTS idx_audit_outcome"
                   " ON audit_logs(outcome, occurred_at)"),
    ("audit_logs", "CREATE INDEX IF NOT EXISTS idx_audit_module"
                   " ON audit_logs(module, id DESC)"),
    ("users", "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)"),
)


def init_indexes():
    """Indexes for the filters every dashboard and module list runs.

    Idempotent, so it is safe on an existing database; the indexes only make the
    same queries faster and never change their results. A table that no module
    has created is skipped rather than stopping the boot: an index is worth
    having, but never worth the whole application.
    """
    conn = connect_db()
    try:
        statements = []
        for table, statement in INDEX_STATEMENTS:
            if database_config.table_exists(conn, table):
                statements.append(statement)
        if statements:
            conn.executescript(";\n".join(statements) + ";")
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()


def _snapshot_db(source_path, target_path):
    """Copy one database file into a consistent standalone snapshot."""
    source = sqlite3.connect(str(source_path), timeout=15)
    destination = sqlite3.connect(str(target_path))
    try:
        with destination:
            source.backup(destination)
    finally:
        destination.close()
        source.close()


def backup_db_command():
    """Snapshot every module database into WK_BACKUP_DIR.

    One file per module per run, so a restore can take back a single desk (say
    payroll) without touching the rest of the system.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        keep = max(1, int(os.environ.get("WK_BACKUP_KEEP", "14")))
    except ValueError:
        keep = 14
    for label, source in database_config.database_files():
        if not Path(source).exists():
            continue
        target = BACKUP_DIR / "{}_{}.db".format(label, stamp)
        _snapshot_db(source, target)
        snapshots = sorted(BACKUP_DIR.glob("{}_*.db".format(label)),
                           key=lambda path: path.stat().st_mtime, reverse=True)
        for old in snapshots[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        print("Backup written to {}".format(target))


def check_db_command():
    """Run SQLite's integrity and foreign-key checks on each database."""
    for label, path in database_config.database_files():
        if not Path(path).exists():
            continue
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
        print("{} ({}): integrity_check {}, foreign_key_check {} problem(s)".format(
            label, Path(path).parent.name, integrity, len(problems)))
