"""Letters live in their own SQLite database, attached as `letter`.

Accounts, departments, the approval trail, notifications and the audit log stay
in users.db; finance and procurement records sit in finance.db. This module owns
letter.db: it attaches the file to every application connection, creates the
correspondence tables there, and moves a register out of a database that still
holds it inline. Every id is kept, so the notification links and audit records
that point at a letter keep pointing at the same row.
"""
import os
import sqlite3
from pathlib import Path

from database import database_config

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = database_config.module_path("letter")

SCHEMA = "letter"

# Every category the register files. The order is the dropdown order.
LETTER_TYPES = ("official", "incoming", "outgoing", "memo", "hr", "finance",
                "procurement", "legal", "confidential")

TABLES = ("letters", "letter_permissions", "letter_category_permissions",
          "letter_role_permissions", "letter_deliveries", "letter_reads",
          "letter_approval_steps", "letter_audit_logs")
TABLE_SET = frozenset(TABLES)

# Parents before children: the copy assigns no ids, so a child row can only
# land once its letter exists. The drop runs the other way round.
COPY_ORDER = ("letters", "letter_permissions", "letter_category_permissions",
              "letter_role_permissions", "letter_deliveries", "letter_reads",
              "letter_approval_steps", "letter_audit_logs")
DROP_ORDER = ("letter_audit_logs", "letter_approval_steps", "letter_reads",
              "letter_deliveries", "letter_role_permissions",
              "letter_category_permissions", "letter_permissions", "letters")


def _letters_table_sql():
    return """
    CREATE TABLE IF NOT EXISTS letter.letters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference_no TEXT UNIQUE,
        letter_type TEXT NOT NULL
            CHECK (letter_type IN ({types})),
        letter_kind TEXT,
        subject TEXT NOT NULL,
        sender TEXT,
        recipient TEXT,
        letter_date TEXT NOT NULL,
        body TEXT,
        status TEXT NOT NULL DEFAULT 'Draft',
        sender_user_id INTEGER,
        sender_department_id INTEGER,
        recipient_user_id INTEGER,
        recipient_department_id INTEGER,
        sent_at TEXT,
        approved_by INTEGER,
        approved_at TEXT,
        requires_approval INTEGER NOT NULL DEFAULT 0,
        approval_step TEXT,
        approval_depth INTEGER NOT NULL DEFAULT 3,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """.format(types=", ".join(repr(value) for value in LETTER_TYPES))


# The only foreign keys left are between two letters tables. SQLite cannot
# enforce a constraint across attached databases, so an account or department
# id is a plain column checked by the application.
LETTERS_DDL = _letters_table_sql() + """;
    CREATE TABLE IF NOT EXISTS letter.letter_permissions (
        user_id INTEGER NOT NULL,
        permission TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 0
            CHECK (allowed IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, permission)
    );

    CREATE TABLE IF NOT EXISTS letter.letter_category_permissions (
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 0
            CHECK (allowed IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, category)
    );

    CREATE TABLE IF NOT EXISTS letter.letter_role_permissions (
        role_key TEXT NOT NULL,
        permission TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 0
            CHECK (allowed IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (role_key, permission)
    );

    CREATE TABLE IF NOT EXISTS letter.letter_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        letter_id INTEGER NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
        user_id INTEGER,
        department_id INTEGER,
        folder TEXT NOT NULL CHECK (folder IN ('inbox', 'outbox')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((user_id IS NULL) <> (department_id IS NULL))
    );

    CREATE TABLE IF NOT EXISTS letter.letter_reads (
        letter_id INTEGER NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (letter_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS letter.letter_approval_steps (
        letter_id INTEGER NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
        step TEXT NOT NULL CHECK (step IN ('manager', 'director', 'ceo')),
        position INTEGER NOT NULL,
        decision TEXT NOT NULL DEFAULT 'Pending'
            CHECK (decision IN ('Pending', 'Approved', 'Returned')),
        actor_id INTEGER,
        actor_name TEXT,
        comments TEXT NOT NULL DEFAULT '',
        decided_at TEXT,
        PRIMARY KEY (letter_id, step)
    );

    CREATE UNIQUE INDEX IF NOT EXISTS letter.idx_letter_delivery_user
        ON letter_deliveries(letter_id, folder, user_id) WHERE user_id IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS letter.idx_letter_delivery_department
        ON letter_deliveries(letter_id, folder, department_id)
        WHERE department_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS letter.idx_letter_delivery_inbox
        ON letter_deliveries(folder, user_id);
    CREATE INDEX IF NOT EXISTS letter.idx_letter_delivery_dept
        ON letter_deliveries(folder, department_id);

    CREATE INDEX IF NOT EXISTS letter.idx_letters_reference
        ON letters(reference_no);
    CREATE INDEX IF NOT EXISTS letter.idx_letters_type_status
        ON letters(letter_type, status);
    CREATE INDEX IF NOT EXISTS letter.idx_letters_date
        ON letters(letter_date);
    CREATE INDEX IF NOT EXISTS letter.idx_letters_sender_user
        ON letters(sender_user_id);
    CREATE INDEX IF NOT EXISTS letter.idx_letters_recipient_user
        ON letters(recipient_user_id);
    CREATE INDEX IF NOT EXISTS letter.idx_letters_recipient_department
        ON letters(recipient_department_id);
    CREATE INDEX IF NOT EXISTS letter.idx_letters_approval_step
        ON letters(status, approval_step);

    CREATE TABLE IF NOT EXISTS letter.letter_audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        letter_id INTEGER,
        action TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS letter.idx_letter_audit_letter
        ON letter_audit_logs(letter_id, created_at);
"""


def ref(table):
    """The name to use in SQL for a letters table."""
    return "{}.{}".format(SCHEMA, table)


def attach(db):
    """Attach letter.db once per connection."""
    database_config.ensure_attached(db, SCHEMA)


def table_names(db):
    """Letter tables that currently exist, as bare names."""
    attach(db)
    return {row[0] for row in db.execute(
        "SELECT name FROM {}.sqlite_master WHERE type = 'table'".format(SCHEMA))}


def init(db):
    """Create the letters database and move an inline register into it."""
    attach(db)
    _enable_wal()
    db.executescript(LETTERS_DDL)
    _migrate_inline_tables(db)
    _backfill_addresses(db)


def _enable_wal():
    """WAL is a property of the file, so it is set once on its own connection."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    finally:
        conn.close()


def _columns(db, schema, table):
    if schema == "main":
        return [row[1] for row in db.execute("PRAGMA table_info({})".format(table))]
    return [row[1] for row in db.execute(
        "PRAGMA {}.table_info({})".format(schema, table))]


def _has_table(db, schema, table):
    if schema == "main":
        sql = "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?"
    else:
        sql = ("SELECT 1 FROM {}.sqlite_master WHERE type='table' AND name=?"
               .format(schema))
    return db.execute(sql, (table,)).fetchone() is not None


def _count(db, schema, table):
    return db.execute("SELECT COUNT(*) FROM {}.{}".format(schema, table)).fetchone()[0]


def _migrate_inline_tables(db):
    """Copy the register out of users.db, verify it, then drop the old copies.

    A database built before this split still carries the letter tables inline.
    The copy keeps every id, so deliveries, read marks, approval steps,
    notifications and audit records keep their letter. The old tables are only
    removed after each new table holds at least as many rows as the old one; a
    shortfall aborts the whole boot transaction.
    """
    legacy = [table for table in TABLES if _has_table(db, "main", table)]
    if not legacy:
        return

    for table in COPY_ORDER:
        if table not in legacy:
            continue
        target = _columns(db, SCHEMA, table)
        source = _columns(db, "main", table)
        shared = [name for name in source if name in target]
        if not shared:
            raise RuntimeError(
                "Letter migration found no shared columns for {}".format(table))
        db.execute(
            "INSERT OR IGNORE INTO {schema}.{table} ({cols})"
            " SELECT {cols} FROM main.{table}".format(
                schema=SCHEMA, table=table, cols=", ".join(shared)))
        if _count(db, SCHEMA, table) < _count(db, "main", table):
            raise RuntimeError(
                "Letter migration lost rows from {}; users.db was left intact"
                .format(table))

    for table in DROP_ORDER:
        if table in legacy:
            db.execute("DROP TABLE main.{}".format(table))


def _backfill_addresses(db):
    """Mail written before addressing existed belongs to its registrant.

    It lands in that account's outbox so the folder lists the whole history.
    """
    db.execute("""
        UPDATE letter.letters SET sender_user_id = created_by
        WHERE sender_user_id IS NULL AND letter_type IN ('official', 'outgoing')
    """)
    db.execute("""
        INSERT OR IGNORE INTO letter.letter_deliveries (letter_id, user_id, folder)
        SELECT l.id, COALESCE(l.sender_user_id, l.created_by), 'outbox'
        FROM letter.letters AS l
        WHERE l.letter_type IN ('official', 'outgoing')
          AND COALESCE(l.sender_user_id, l.created_by) IS NOT NULL
    """)
