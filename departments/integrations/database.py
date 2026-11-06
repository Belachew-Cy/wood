"""The integration registry lives beside the records it feeds.

Every table here is in the application database, not a file of its own: an
Active Directory account is mapped to an employee row, a department mapping names
a department, and a credential belongs to the integration that uses it. Those
foreign keys and joins only mean anything when the rows sit next to the records
they point at, so this module reads and writes ``core.db.get_db()`` like every
other desk does, and the audit trail keeps the history of what an administrator
changed here.
"""

# The systems a connection may be for. The order is the dropdown order, and the
# two words a synchronization adapter and a connection tester both answer to are
# the keys of those tables, so a type listed here is a type the module can use.
INTEGRATION_TYPES = (
    "Active Directory / LDAP",
    "Microsoft Entra ID",
    "REST API",
    "Email / SMTP",
    "SMS Gateway",
    "Biometric Attendance",
    "SAP ERP",
    "Other",
)

INTEGRATION_STATUS = ("active", "inactive")

# The two types that read a directory. A connection of either type registered
# from the directory desk also carries a profile row (DNs, filters, provisioning
# rules) and appears in this same registry.
AD_TYPE = "Active Directory / LDAP"
ENTRA_TYPE = "Microsoft Entra ID"
DIRECTORY_TYPES = (AD_TYPE, ENTRA_TYPE)

# A credential is stored by name, sealed, and never shown again. The names are
# fixed so a form cannot invent a column and a log line cannot carry a secret.
CREDENTIAL_NAMES = (
    "password",
    "api_key",
    "access_token",
    "client_secret",
    "service_account_password",
)

CREDENTIAL_LABEL = {
    "password": "Bind password",
    "api_key": "API key",
    "access_token": "Access token",
    "client_secret": "Client secret",
    "service_account_password": "Service account password",
}

MIN_SYNC_INTERVAL = 5
MAX_SYNC_INTERVAL = 10080

TABLES = ("integrations", "integration_credentials",
          "integration_connection_tests", "integration_sync_logs",
          "integration_errors", "integration_ad_users",
          "integration_department_mappings", "integration_employee_mappings")

DDL = """
    CREATE TABLE IF NOT EXISTS integrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        integration_type TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        endpoint TEXT NOT NULL DEFAULT '',
        username TEXT NOT NULL DEFAULT '',
        search_base TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'inactive'
            CHECK (status IN ('active', 'inactive')),
        sync_enabled INTEGER NOT NULL DEFAULT 0
            CHECK (sync_enabled IN (0, 1)),
        sync_interval INTEGER NOT NULL DEFAULT 60
            CHECK (sync_interval BETWEEN 5 AND 10080),
        next_run_at TEXT,
        last_sync_at TEXT,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_name
        ON integrations(name COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_integration_status ON integrations(status);
    CREATE INDEX IF NOT EXISTS idx_integration_due
        ON integrations(sync_enabled, next_run_at);

    CREATE TABLE IF NOT EXISTS integration_credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER NOT NULL,
        credential_name TEXT NOT NULL,
        encrypted_value TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (integration_id, credential_name),
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS integration_connection_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER,
        status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
        message TEXT NOT NULL DEFAULT '',
        response_time_ms REAL,
        tested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_connection_test_integration
        ON integration_connection_tests(integration_id, tested_at);

    CREATE TABLE IF NOT EXISTS integration_sync_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER,
        sync_type TEXT NOT NULL DEFAULT 'manual',
        status TEXT NOT NULL
            CHECK (status IN ('running', 'success', 'failed')),
        message TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_sync_log_integration
        ON integration_sync_logs(integration_id, started_at);

    CREATE TABLE IF NOT EXISTS integration_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER,
        error_type TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_integration_error_open
        ON integration_errors(resolved, created_at);

    CREATE TABLE IF NOT EXISTS integration_ad_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER NOT NULL,
        object_guid TEXT NOT NULL,
        username TEXT NOT NULL,
        full_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT '',
        job_title TEXT NOT NULL DEFAULT '',
        distinguished_name TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (integration_id, object_guid),
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_ad_user_username
        ON integration_ad_users(username);
    CREATE INDEX IF NOT EXISTS idx_ad_user_department
        ON integration_ad_users(department);

    CREATE TABLE IF NOT EXISTS integration_department_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER NOT NULL,
        ad_department TEXT NOT NULL,
        hr_department_id INTEGER,
        hr_department_name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (integration_id, ad_department),
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE CASCADE,
        FOREIGN KEY (hr_department_id) REFERENCES departments(id)
            ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS integration_employee_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id INTEGER NOT NULL,
        ad_object_guid TEXT NOT NULL,
        hr_employee_id INTEGER,
        mapping_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (mapping_status IN ('pending', 'mapped', 'ignored')),
        reviewed_by INTEGER,
        reviewed_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (integration_id, ad_object_guid),
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE CASCADE,
        FOREIGN KEY (hr_employee_id) REFERENCES employees(id)
            ON DELETE SET NULL,
        FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_employee_mapping_status
        ON integration_employee_mappings(mapping_status);
"""


def ensure_integrations_schema():
    """Create the integration tables. Safe to call on every boot."""
    from core.db import connect_db
    db = connect_db()
    try:
        db.executescript(DDL)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def stored_integration(db, integration_id):
    """One registry row by id, or None."""
    try:
        integration_id = int(integration_id)
    except (TypeError, ValueError):
        return None
    return db.execute("SELECT * FROM integrations WHERE id = ?",
                      (integration_id,)).fetchone()


def credential_names_set(db, integration_id):
    """The credential names this integration has on file - never their values."""
    return {row["credential_name"] for row in db.execute(
        "SELECT credential_name FROM integration_credentials"
        " WHERE integration_id = ?", (integration_id,)).fetchall()}


def due_integrations(db, now):
    """Enabled, active connections whose next run time has arrived.

    A registry row that also owns a directory profile is left to the directory
    engine: it previews and applies through its own run history, and the
    scheduler calls that path in the same pass. Answering it here as well would
    synchronize one connection twice.
    """
    return db.execute("""
        SELECT * FROM integrations AS i
        WHERE i.sync_enabled = 1 AND i.status = 'active'
          AND i.next_run_at IS NOT NULL AND i.next_run_at <= ?
          AND NOT EXISTS (SELECT 1 FROM directory_connections AS c
                           WHERE c.integration_id = i.id)
        ORDER BY i.next_run_at
    """, (now,)).fetchall()


def clean_name(value):
    return " ".join((value or "").split())[:120]


def clean_text(value, limit=300):
    return " ".join((value or "").split())[:limit]


def clean_interval(value, default=60):
    """The interval as a usable number, or None when the value is out of range."""
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return default
    if not MIN_SYNC_INTERVAL <= interval <= MAX_SYNC_INTERVAL:
        return None
    return interval


def type_is_known(integration_type):
    return integration_type in INTEGRATION_TYPES
