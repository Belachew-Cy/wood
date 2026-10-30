"""Username generation, employee links and account identity."""

from core.config import EMPLOYEE_ACCOUNT_STATUS
from core.config import INITIAL_PASSWORD_DIGITS
from core.config import INITIAL_PASSWORD_LENGTH
from core.config import INITIAL_PASSWORD_LOWER
from core.config import INITIAL_PASSWORD_UPPER
from core.config import PASSWORD_MIN_LENGTH
from core.config import USERNAME_FIRST
from core.config import USERNAME_PREFIX
from core.db import get_db
from services.recency import stamp as stamp_record
import secrets


def init_username_sequence(db=None):
    """Create (or keep) the counter that hands out DB-series sign-in names."""
    owns = db is None
    if owns:
        db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS username_sequence (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            next_number INTEGER NOT NULL
        )
    """)
    if db.execute(
        "SELECT next_number FROM username_sequence WHERE id = 1").fetchone() is None:
        db.execute("INSERT INTO username_sequence (id, next_number) VALUES (1, ?)",
                   (USERNAME_FIRST - 1,))
    if owns:
        db.commit()


def next_username(db):
    """The next sign-in name, from the counter row.

    The UPDATE takes the write lock first and the number is read after it, so the
    value this returns is exclusive to this caller for the rest of the transaction.
    """
    db.execute("UPDATE username_sequence SET next_number = next_number + 1 WHERE id = 1")
    number = db.execute(
        "SELECT next_number FROM username_sequence WHERE id = 1").fetchone()[0]
    return "{}{}".format(USERNAME_PREFIX, number)


def generate_initial_password():
    """A hand-out credential for a brand new account: letters and a digit.

    It is shown once on the User Management screen and never stored - the hash goes
    into password_hash and the account is gated with must_change_password, so the
    hand-out value cannot be kept.
    """
    pool = INITIAL_PASSWORD_UPPER + INITIAL_PASSWORD_LOWER + INITIAL_PASSWORD_DIGITS
    characters = [secrets.choice(INITIAL_PASSWORD_UPPER),
                  secrets.choice(INITIAL_PASSWORD_LOWER),
                  secrets.choice(INITIAL_PASSWORD_DIGITS)]
    characters += [secrets.choice(pool) for _ in range(INITIAL_PASSWORD_LENGTH - 3)]
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def password_problem(password, minimum=PASSWORD_MIN_LENGTH):
    """None when the password meets the shipped policy, otherwise the message."""
    if not password or len(password) < minimum:
        return "Use at least {} characters.".format(minimum)
    if not any(character.isdigit() for character in password):
        return "Include at least one number."
    if not any(character.isalpha() for character in password):
        return "Include at least one letter."
    return None


def migrate_login_locks(db):
    """Keep the sign-in lock on the account row so it survives any restart.

    The failure streak is stored rather than derived from login_attempts: the
    temporary lock expires but the streak stays at five, which is what makes the
    next three failures permanent.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    additions = (
        ("failed_login_count", "INTEGER NOT NULL DEFAULT 0"),
        ("lock_state", "TEXT NOT NULL DEFAULT 'unlocked'"),
        ("lock_until", "TEXT"),
        ("locked_at", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            db.execute("ALTER TABLE users ADD COLUMN {} {}".format(name, definition))


def migrate_account_identity(db):
    """Give every account a minted username, an employee link and a password gate.

    SQLite cannot add a UNIQUE constraint to an existing table, so uniqueness lives
    in an index, which is also what lets the column stay empty during the backfill -
    a UNIQUE index treats NULLs as distinct.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    if "username" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "employee_id" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN employee_id INTEGER")
    if "must_change_password" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER"
                   " NOT NULL DEFAULT 0")
    if "password_updated_at" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN password_updated_at TEXT")
    # Microsoft sign-in binds to an existing account: the object id is the stable
    # identity from the directory, the tenant records which directory vouched for it.
    if "ms_object_id" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN ms_object_id TEXT")
    if "ms_tenant_id" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN ms_tenant_id TEXT")
    if "ms_linked_at" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN ms_linked_at TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ms_object"
               " ON users(ms_object_id)")

    init_username_sequence(db)
    for row in db.execute("""
        SELECT id FROM users
        WHERE username IS NULL OR username = ''
        ORDER BY id
    """).fetchall():
        db.execute("UPDATE users SET username = ? WHERE id = ?",
                   (next_username(db), row["id"]))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")

    link_accounts_to_employees(db)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee ON users(employee_id)")


def link_accounts_to_employees(db):
    """Attach an account to the employee record that already carries its email.

    Only an unambiguous single match is linked, only for an account with no link
    yet, and never onto an employee somebody else already owns - so a shared
    address can never merge two people or steal an existing link.
    """
    for row in db.execute("""
        SELECT id, email FROM users
        WHERE employee_id IS NULL AND email IS NOT NULL AND email <> ''
    """).fetchall():
        matches = db.execute(
            "SELECT id FROM employees WHERE lower(email) = lower(?)", (row["email"],)
        ).fetchall()
        if len(matches) != 1:
            continue
        taken = db.execute("SELECT id FROM users WHERE employee_id = ?",
                           (matches[0]["id"],)).fetchone()
        if taken is None:
            db.execute("UPDATE users SET employee_id = ? WHERE id = ?",
                       (matches[0]["id"], row["id"]))


def account_for_employee(db, employee_id):
    if employee_id is None:
        return None
    return db.execute("SELECT * FROM users WHERE employee_id = ?", (employee_id,)).fetchone()


def sync_employee_to_account(db, employee_id, previous_status=None):
    """Mirror the staff file onto the account that carries it.

    Identity follows the employee, because HR owns the record. Access follows
    employment status, but only on the transitions the status really makes: an
    Inactive employee loses sign-in, and coming back from Inactive gives it again.
    An account an administrator disabled for some other reason is never quietly
    re-enabled. Returns the changes for the flash message and the audit row.
    """
    account = account_for_employee(db, employee_id)
    if account is None:
        return []
    employee = db.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if employee is None:
        return []

    changes = []
    if employee["full_name"] and account["full_name"] != employee["full_name"]:
        changes.append("name {} to {}".format(account["full_name"], employee["full_name"]))
        db.execute("UPDATE users SET full_name = ? WHERE id = ?",
                   (employee["full_name"], account["id"]))

    email = (employee["email"] or "").strip().lower()
    if email and account["email"] != email:
        held = db.execute("SELECT id FROM users WHERE lower(email) = ? AND id <> ?",
                          (email, account["id"])).fetchone()
        if held is None:
            changes.append("email {} to {}".format(account["email"], email))
            db.execute("UPDATE users SET email = ? WHERE id = ?", (email, account["id"]))

    phone = (employee["phone"] or "").strip()
    if phone and (account["phone"] or "") != phone:
        changes.append("phone {} to {}".format(account["phone"] or "(blank)", phone))
        db.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, account["id"]))

    wanted = EMPLOYEE_ACCOUNT_STATUS.get(employee["employment_status"], "Active")
    if account["status"] != wanted:
        if wanted == "Disabled":
            changes.append("account set to Disabled with the employee")
            db.execute("UPDATE users SET status = 'Disabled' WHERE id = ?", (account["id"],))
        elif previous_status == "Inactive":
            changes.append("account re-enabled with the employee")
            db.execute("UPDATE users SET status = 'Active' WHERE id = ?", (account["id"],))

    if changes:
        stamp_record(db, "users", account["id"])
    mirror_employee_account(db, employee_id, account["id"])
    return changes


def employee_table_columns(db):
    return {row[1] for row in db.execute("PRAGMA table_info(employees)").fetchall()}


def mirror_employee_account(db, employee_id, account_id):
    """Point employees.user_id at the account that owns the staff record.

    users.employee_id is the link the application trusts; the HR table carries the same
    pairing so a staff file can show its own sign-in without a join. That column is
    created by the hierarchy migration, which may not have run yet on a given database,
    so the write is conditional rather than assuming the schema.
    """
    if "user_id" not in employee_table_columns(db):
        return False
    db.execute("UPDATE employees SET user_id = ? WHERE id = ?", (account_id, employee_id))
    return True


def reconcile_employee_links(db):
    """Make the two halves of a staff link say the same thing, copying no field.

    Either side can have been written first: an account can name a record on User
    Management, or a record can name an account in the HR table. An account that names
    nothing but is named by exactly one record adopts it. After that the account side
    decides, so a record no account claims back loses a stale user_id. Names, emails
    and statuses only ever follow the record when HR saves it, never here - an
    existing account is never renamed by starting the app.
    """
    if "user_id" not in employee_table_columns(db):
        return
    for row in db.execute("""
        SELECT e.id AS employee_id, u.id AS account_id
        FROM employees AS e
        JOIN users AS u ON u.id = e.user_id
        WHERE u.employee_id IS NULL
    """).fetchall():
        claimed = db.execute("SELECT id FROM users WHERE employee_id = ?",
                             (row["employee_id"],)).fetchone()
        if claimed is None:
            db.execute("UPDATE users SET employee_id = ? WHERE id = ?",
                       (row["employee_id"], row["account_id"]))
    db.execute("""
        UPDATE employees SET user_id = NULL
        WHERE user_id IS NOT NULL
          AND id NOT IN (SELECT employee_id FROM users WHERE employee_id IS NOT NULL)
    """)
    for row in db.execute("SELECT id, employee_id FROM users"
                          " WHERE employee_id IS NOT NULL").fetchall():
        mirror_employee_account(db, row["employee_id"], row["id"])


def employee_link_problem(db, employee_id, account_id):
    """Why this account cannot be pointed at that staff record, or None when it can.

    The UNIQUE index on users.employee_id would refuse the write anyway. The check is
    what lets the screen name the account that already owns the record instead of
    failing on an IntegrityError that reads like an email clash.
    """
    if db.execute("SELECT id FROM employees WHERE id = ?", (employee_id,)).fetchone() is None:
        return "Choose a staff record that exists."
    owner = db.execute(
        "SELECT full_name FROM users WHERE employee_id = ? AND id <> ?",
        (employee_id, -1 if account_id is None else account_id)).fetchone()
    if owner is not None:
        return "{} already has an account for that staff record.".format(owner["full_name"])
    return None


def staff_link_choice(db, form, account_id):
    """Read the staff-record select: the id to link (or None) and why it was refused."""
    value = (form.get("employee_id") or "").strip()
    if not value:
        return None, None
    try:
        employee_id = int(value)
    except ValueError:
        return None, "Choose a staff record that exists."
    return employee_id, employee_link_problem(db, employee_id, account_id)


def adopt_account_for_employee(db, employee_id):
    """Link the one unlinked account that already carries this record's email.

    Registering somebody in HR should not need a second trip to User Management, but
    only an unambiguous single match is taken - the same rule the boot backfill uses -
    so a shared address can never merge two people or steal an existing link.
    """
    employee = db.execute("SELECT email FROM employees WHERE id = ?",
                          (employee_id,)).fetchone()
    email = (employee["email"] or "").strip().lower() if employee else ""
    if not email:
        return None
    matches = db.execute(
        "SELECT id FROM users WHERE lower(email) = ? AND employee_id IS NULL",
        (email,)).fetchall()
    if len(matches) != 1:
        return None
    account_id = matches[0]["id"]
    db.execute("UPDATE users SET employee_id = ? WHERE id = ?", (employee_id, account_id))
    sync_employee_to_account(db, employee_id)
    return account_id


def attach_account_to_record(db, account_id, employee_id):
    """Move an account's staff link and mirror both halves; return what synced.

    Dropping a link keeps the account, because accounts are only ever disabled on User
    Management. Taking one lets the record own the account's name, contact details and
    sign-in, which is what makes the two screens describe one person.
    """
    row = db.execute("SELECT employee_id FROM users WHERE id = ?", (account_id,)).fetchone()
    previous = row["employee_id"] if row is not None else None
    db.execute("UPDATE users SET employee_id = ? WHERE id = ?", (employee_id, account_id))
    if previous is not None and previous != employee_id:
        mirror_employee_account(db, previous, None)
    if employee_id is None:
        return []
    mirror_employee_account(db, employee_id, account_id)
    return sync_employee_to_account(db, employee_id)


def staff_records(db):
    """Every staff record with the account that owns it, for the link selects."""
    return db.execute("""
        SELECT e.id, e.full_name, e.employee_code, e.department, e.employment_status,
               u.id AS owner_id, u.full_name AS owner_name
        FROM employees AS e
        LEFT JOIN users AS u ON u.employee_id = e.id
        ORDER BY e.full_name, e.employee_code
    """).fetchall()


def employee_name(db, employee_id):
    row = db.execute("SELECT full_name FROM employees WHERE id = ?",
                     (employee_id,)).fetchone()
    return row["full_name"] if row else ""
