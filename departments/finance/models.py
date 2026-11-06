
"""
Finance Management Models
Dereje Wood & Kitchen Cabinet Management System

Core Finance database schema.

Uses an existing SQLite connection supplied by the application.
Does not create a separate database or duplicate departmental records.
"""

from datetime import datetime, date
import sqlite3


# =========================================================
# DATABASE HELPERS
# =========================================================

def _execute(db, sql, params=()):
    """Execute SQL using the existing database connection."""
    return db.execute(sql, params)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _validate_connection(db):
    """Ensure that a compatible SQLite connection was supplied."""
    if not isinstance(db, sqlite3.Connection):
        raise TypeError(
            "A valid existing sqlite3.Connection is required."
        )


# =========================================================
# FINANCE DATABASE INITIALIZATION
# =========================================================

def init_finance_tables(db):
    """
    Create Finance core tables in the existing database.

    Call this after the application's database connection
    has been initialized.

    Existing departmental tables are not modified.
    """

    _validate_connection(db)

    statements = [

        # -------------------------------------------------
        # 1. CHART OF ACCOUNTS
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            account_code TEXT NOT NULL UNIQUE,
            account_name TEXT NOT NULL,

            account_type TEXT NOT NULL CHECK (
                account_type IN (
                    'Asset',
                    'Liability',
                    'Equity',
                    'Income',
                    'Expense'
                )
            ),

            normal_balance TEXT NOT NULL CHECK (
                normal_balance IN ('Debit', 'Credit')
            ),

            parent_account_id INTEGER,

            description TEXT,

            is_control_account INTEGER NOT NULL DEFAULT 0
                CHECK (is_control_account IN (0, 1)),

            allow_manual_posting INTEGER NOT NULL DEFAULT 1
                CHECK (allow_manual_posting IN (0, 1)),

            status TEXT NOT NULL DEFAULT 'Active'
                CHECK (status IN ('Active', 'Inactive')),

            created_by INTEGER,
            created_at TEXT NOT NULL,

            updated_at TEXT,

            FOREIGN KEY (parent_account_id)
                REFERENCES finance_accounts(id)
                ON DELETE RESTRICT
        )
        """,

        # -------------------------------------------------
        # 2. FINANCIAL PERIODS
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            period_name TEXT NOT NULL UNIQUE,

            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,

            status TEXT NOT NULL DEFAULT 'Open'
                CHECK (
                    status IN (
                        'Open',
                        'Closed',
                        'Locked'
                    )
                ),

            created_by INTEGER,
            created_at TEXT NOT NULL,
            closed_by INTEGER,
            closed_at TEXT,

            CHECK (start_date <= end_date)
        )
        """,

        # -------------------------------------------------
        # 3. JOURNAL ENTRIES
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            entry_number TEXT NOT NULL UNIQUE,

            period_id INTEGER NOT NULL,

            journal_date TEXT NOT NULL,

            reference_number TEXT,

            description TEXT NOT NULL,

            source_department TEXT NOT NULL DEFAULT 'Finance',

            source_type TEXT,

            source_reference TEXT,

            currency TEXT NOT NULL DEFAULT 'ETB',

            exchange_rate NUMERIC NOT NULL DEFAULT 1
                CHECK (exchange_rate > 0),

            total_debit NUMERIC NOT NULL DEFAULT 0
                CHECK (total_debit >= 0),

            total_credit NUMERIC NOT NULL DEFAULT 0
                CHECK (total_credit >= 0),

            status TEXT NOT NULL DEFAULT 'Draft'
                CHECK (
                    status IN (
                        'Draft',
                        'Pending Approval',
                        'Approved',
                        'Posted',
                        'Rejected',
                        'Reversed'
                    )
                ),

            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,

            submitted_by INTEGER,
            submitted_at TEXT,

            approved_by INTEGER,
            approved_at TEXT,

            posted_by INTEGER,
            posted_at TEXT,

            reversed_by INTEGER,
            reversed_at TEXT,

            reversal_entry_id INTEGER,

            updated_at TEXT,

            FOREIGN KEY (period_id)
                REFERENCES finance_periods(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (reversal_entry_id)
                REFERENCES finance_journal_entries(id)
                ON DELETE RESTRICT,

            UNIQUE (
                source_department,
                source_type,
                source_reference
            )
        )
        """,

        # -------------------------------------------------
        # 4. JOURNAL ENTRY LINES
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            journal_entry_id INTEGER NOT NULL,

            account_id INTEGER NOT NULL,

            line_description TEXT,

            debit NUMERIC NOT NULL DEFAULT 0
                CHECK (debit >= 0),

            credit NUMERIC NOT NULL DEFAULT 0
                CHECK (credit >= 0),

            currency TEXT NOT NULL DEFAULT 'ETB',

            exchange_rate NUMERIC NOT NULL DEFAULT 1
                CHECK (exchange_rate > 0),

            debit_base NUMERIC NOT NULL DEFAULT 0
                CHECK (debit_base >= 0),

            credit_base NUMERIC NOT NULL DEFAULT 0
                CHECK (credit_base >= 0),

            department TEXT,

            project_reference TEXT,

            created_at TEXT NOT NULL,

            FOREIGN KEY (journal_entry_id)
                REFERENCES finance_journal_entries(id)
                ON DELETE CASCADE,

            FOREIGN KEY (account_id)
                REFERENCES finance_accounts(id)
                ON DELETE RESTRICT,

            CHECK (
                (debit > 0 AND credit = 0)
                OR
                (credit > 0 AND debit = 0)
            )
        )
        """,

        # -------------------------------------------------
        # 5. DEPARTMENT INTEGRATION RECORDS
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_integration_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            department TEXT NOT NULL CHECK (
                department IN (
                    'HR',
                    'Payroll',
                    'Marketing',
                    'Project',
                    'Asset',
                    'Inventory'
                )
            ),

            source_type TEXT NOT NULL,

            source_reference TEXT NOT NULL,

            description TEXT,

            amount NUMERIC NOT NULL DEFAULT 0
                CHECK (amount >= 0),

            currency TEXT NOT NULL DEFAULT 'ETB',

            exchange_rate NUMERIC NOT NULL DEFAULT 1
                CHECK (exchange_rate > 0),

            base_amount NUMERIC NOT NULL DEFAULT 0
                CHECK (base_amount >= 0),

            journal_entry_id INTEGER,

            status TEXT NOT NULL DEFAULT 'Received'
                CHECK (
                    status IN (
                        'Received',
                        'Pending Review',
                        'Approved',
                        'Posted',
                        'Rejected',
                        'Failed'
                    )
                ),

            received_at TEXT NOT NULL,

            reviewed_by INTEGER,
            reviewed_at TEXT,

            rejection_reason TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT,

            FOREIGN KEY (journal_entry_id)
                REFERENCES finance_journal_entries(id)
                ON DELETE RESTRICT,

            UNIQUE (
                department,
                source_type,
                source_reference
            )
        )
        """,

        # -------------------------------------------------
        # 6. FINANCE AUDIT LOGS
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            entity_type TEXT NOT NULL,

            entity_id INTEGER NOT NULL,

            action TEXT NOT NULL,

            previous_value TEXT,

            new_value TEXT,

            performed_by INTEGER NOT NULL,

            department TEXT,

            ip_address TEXT,

            remarks TEXT,

            created_at TEXT NOT NULL
        )
        """,

        # -------------------------------------------------
        # 7. CURRENCY EXCHANGE RATES
        # -------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS finance_exchange_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            from_currency TEXT NOT NULL,

            to_currency TEXT NOT NULL,

            exchange_rate NUMERIC NOT NULL
                CHECK (exchange_rate > 0),

            effective_date TEXT NOT NULL,

            source TEXT,

            status TEXT NOT NULL DEFAULT 'Active'
                CHECK (status IN ('Active', 'Inactive')),

            created_by INTEGER,
            created_at TEXT NOT NULL,

            UNIQUE (
                from_currency,
                to_currency,
                effective_date
            )
        )
        """,

        # -------------------------------------------------
        # INDEXES
        # -------------------------------------------------

        """
        CREATE INDEX IF NOT EXISTS idx_finance_accounts_parent
        ON finance_accounts(parent_account_id)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_period_dates
        ON finance_periods(start_date, end_date)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_journal_date
        ON finance_journal_entries(journal_date)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_journal_status
        ON finance_journal_entries(status)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_journal_period
        ON finance_journal_entries(period_id)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_journal_lines_entry
        ON finance_journal_lines(journal_entry_id)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_journal_lines_account
        ON finance_journal_lines(account_id)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_integration_department
        ON finance_integration_records(department, status)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_audit_entity
        ON finance_audit_logs(entity_type, entity_id)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_finance_exchange_date
        ON finance_exchange_rates(
            from_currency,
            to_currency,
            effective_date
        )
        """
    ]

    try:
        for statement in statements:
            _execute(db, statement)

        db.commit()

    except Exception:
        db.rollback()
        raise


# =========================================================
# CHART OF ACCOUNTS FUNCTIONS
# =========================================================

def create_finance_account(
    db,
    account_code,
    account_name,
    account_type,
    normal_balance,
    created_by,
    parent_account_id=None,
    description=None,
    is_control_account=False,
    allow_manual_posting=True
):
    """Create a new financial account."""

    _validate_connection(db)

    valid_types = {
        "Asset",
        "Liability",
        "Equity",
        "Income",
        "Expense"
    }

    if account_type not in valid_types:
        raise ValueError("Invalid account type.")

    if normal_balance not in {"Debit", "Credit"}:
        raise ValueError("Invalid normal balance.")

    if not account_code or not account_name:
        raise ValueError(
            "Account code and account name are required."
        )

    now = _now()

    try:
        cursor = _execute(
            db,
            """
            INSERT INTO finance_accounts (
                account_code,
                account_name,
                account_type,
                normal_balance,
                parent_account_id,
                description,
                is_control_account,
                allow_manual_posting,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_code.strip(),
                account_name.strip(),
                account_type,
                normal_balance,
                parent_account_id,
                description,
                int(is_control_account),
                int(allow_manual_posting),
                created_by,
                now
            )
        )

        account_id = cursor.lastrowid
        db.commit()

        return account_id

    except Exception:
        db.rollback()
        raise


def get_finance_accounts(db, active_only=True):
    """Retrieve chart of accounts."""

    _validate_connection(db)

    sql = """
        SELECT *
        FROM finance_accounts
    """

    if active_only:
        sql += " WHERE status = 'Active'"

    sql += " ORDER BY account_code"

    return _execute(db, sql).fetchall()


# =========================================================
# FINANCIAL PERIOD FUNCTIONS
# =========================================================

def create_financial_period(
    db,
    period_name,
    start_date,
    end_date,
    created_by
):
    """Create a financial accounting period."""

    _validate_connection(db)

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    if start > end:
        raise ValueError(
            "Period start date must not exceed end date."
        )

    existing = _execute(
        db,
        """
        SELECT id
        FROM finance_periods
        WHERE start_date <= ?
          AND end_date >= ?
        """,
        (end_date, start_date)
    ).fetchone()

    if existing:
        raise ValueError(
            "The requested financial period overlaps an existing period."
        )

    try:
        cursor = _execute(
            db,
            """
            INSERT INTO finance_periods (
                period_name,
                start_date,
                end_date,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                period_name.strip(),
                start_date,
                end_date,
                created_by,
                _now()
            )
        )

        period_id = cursor.lastrowid
        db.commit()

        return period_id

    except Exception:
        db.rollback()
        raise


def get_open_financial_period(db, journal_date):
    """Find the open period containing a journal date."""

    _validate_connection(db)

    return _execute(
        db,
        """
        SELECT *
        FROM finance_periods
        WHERE status = 'Open'
          AND start_date <= ?
          AND end_date >= ?
        LIMIT 1
        """,
        (journal_date, journal_date)
    ).fetchone()


# =========================================================
# JOURNAL ENTRY FUNCTIONS
# =========================================================

def create_journal_entry(
    db,
    entry_number,
    period_id,
    journal_date,
    description,
    created_by,
    source_department="Finance",
    source_type=None,
    source_reference=None,
    currency="ETB",
    exchange_rate=1
):
    """Create a draft journal entry header."""

    _validate_connection(db)

    if exchange_rate <= 0:
        raise ValueError("Exchange rate must be positive.")

    period = _execute(
        db,
        """
        SELECT *
        FROM finance_periods
        WHERE id = ?
          AND status = 'Open'
          AND start_date <= ?
          AND end_date >= ?
        """,
        (period_id, journal_date, journal_date)
    ).fetchone()

    if not period:
        raise ValueError(
            "No open financial period covers this journal date."
        )

    try:
        cursor = _execute(
            db,
            """
            INSERT INTO finance_journal_entries (
                entry_number,
                period_id,
                journal_date,
                description,
                source_department,
                source_type,
                source_reference,
                currency,
                exchange_rate,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_number,
                period_id,
                journal_date,
                description,
                source_department,
                source_type,
                source_reference,
                currency,
                exchange_rate,
                created_by,
                _now()
            )
        )

        entry_id = cursor.lastrowid
        db.commit()

        return entry_id

    except Exception:
        db.rollback()
        raise


def add_journal_line(
    db,
    journal_entry_id,
    account_id,
    created_at,
    debit=0,
    credit=0,
    currency="ETB",
    exchange_rate=1,
    department=None,
    project_reference=None,
    line_description=None
):
    """Add one debit or credit line to a draft journal."""

    _validate_connection(db)

    debit = float(debit or 0)
    credit = float(credit or 0)
    exchange_rate = float(exchange_rate)

    if debit < 0 or credit < 0:
        raise ValueError("Debit and credit cannot be negative.")

    if (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
        raise ValueError(
            "A journal line must contain either a debit or a credit."
        )

    if exchange_rate <= 0:
        raise ValueError("Exchange rate must be positive.")

    entry = _execute(
        db,
        """
        SELECT status
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_entry_id,)
    ).fetchone()

    if not entry or entry["status"] != "Draft":
        raise ValueError(
            "Journal lines can only be added to draft entries."
        )

    account = _execute(
        db,
        """
        SELECT id, status, allow_manual_posting
        FROM finance_accounts
        WHERE id = ?
        """,
        (account_id,)
    ).fetchone()

    if not account or account["status"] != "Active":
        raise ValueError("The selected account is not active.")

    if not account["allow_manual_posting"]:
        raise ValueError(
            "Manual posting is not allowed for this account."
        )

    debit_base = round(debit * exchange_rate, 2)
    credit_base = round(credit * exchange_rate, 2)

    try:
        cursor = _execute(
            db,
            """
            INSERT INTO finance_journal_lines (
                journal_entry_id,
                account_id,
                line_description,
                debit,
                credit,
                currency,
                exchange_rate,
                debit_base,
                credit_base,
                department,
                project_reference,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                journal_entry_id,
                account_id,
                line_description,
                debit,
                credit,
                currency,
                exchange_rate,
                debit_base,
                credit_base,
                department,
                project_reference,
                created_at or _now()
            )
        )

        line_id = cursor.lastrowid
        db.commit()

        return line_id

    except Exception:
        db.rollback()
        raise


def get_journal_lines(db, journal_entry_id):
    """Retrieve all lines belonging to a journal."""

    _validate_connection(db)

    return _execute(
        db,
        """
        SELECT
            jl.*,
            a.account_code,
            a.account_name,
            a.account_type
        FROM finance_journal_lines jl
        JOIN finance_accounts a
            ON a.id = jl.account_id
        WHERE jl.journal_entry_id = ?
        ORDER BY jl.id
        """,
        (journal_entry_id,)
    ).fetchall()


# =========================================================
# DEPARTMENT INTEGRATION FUNCTIONS
# =========================================================

def record_department_transaction(
    db,
    department,
    source_type,
    source_reference,
    description,
    amount,
    currency="ETB",
    exchange_rate=1
):
    """
    Register a source transaction received from a department.

    This does not post a journal or create a duplicate
    department transaction.
    """

    _validate_connection(db)

    valid_departments = {
        "HR",
        "Payroll",
        "Marketing",
        "Project",
        "Asset",
        "Inventory"
    }

    if department not in valid_departments:
        raise ValueError("Unsupported department.")

    amount = float(amount)
    exchange_rate = float(exchange_rate)

    if amount < 0 or exchange_rate <= 0:
        raise ValueError("Invalid amount or exchange rate.")

    base_amount = round(amount * exchange_rate, 2)

    try:
        cursor = _execute(
            db,
            """
            INSERT INTO finance_integration_records (
                department,
                source_type,
                source_reference,
                description,
                amount,
                currency,
                exchange_rate,
                base_amount,
                status,
                received_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Received', ?, ?)
            """,
            (
                department,
                source_type,
                str(source_reference),
                description,
                amount,
                currency,
                exchange_rate,
                base_amount,
                _now(),
                _now()
            )
        )

        record_id = cursor.lastrowid
        db.commit()

        return record_id

    except sqlite3.IntegrityError:
        db.rollback()
        raise ValueError(
            "This department transaction has already been received."
        )

    except Exception:
        db.rollback()
        raise


def get_department_integration_records(
    db,
    department=None,
    status=None
):
    """Retrieve department integration records."""

    _validate_connection(db)

    sql = """
        SELECT *
        FROM finance_integration_records
        WHERE 1 = 1
    """

    params = []

    if department:
        sql += " AND department = ?"
        params.append(department)

    if status:
        sql += " AND status = ?"
        params.append(status)

    sql += " ORDER BY received_at DESC, id DESC"

    return _execute(db, sql, params).fetchall()


# =========================================================
# FINANCE AUDIT LOG FUNCTIONS
# =========================================================

def create_finance_audit_log(
    db,
    entity_type,
    entity_id,
    action,
    performed_by,
    previous_value=None,
    new_value=None,
    department=None,
    ip_address=None,
    remarks=None
):
    """Record a Finance audit event."""

    _validate_connection(db)

    cursor = _execute(
        db,
        """
        INSERT INTO finance_audit_logs (
            entity_type,
            entity_id,
            action,
            previous_value,
            new_value,
            performed_by,
            department,
            ip_address,
            remarks,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            action,
            previous_value,
            new_value,
            performed_by,
            department,
            ip_address,
            remarks,
            _now()
        )
    )

    return cursor.lastrowid


# =========================================================
# CURRENCY EXCHANGE RATE FUNCTIONS
# =========================================================

def add_exchange_rate(
    db,
    from_currency,
    to_currency,
    exchange_rate,
    effective_date,
    created_by,
    source=None
):
    """Add an approved exchange-rate record."""

    _validate_connection(db)

    exchange_rate = float(exchange_rate)

    if exchange_rate <= 0:
        raise ValueError("Exchange rate must be positive.")

    if from_currency == to_currency:
        raise ValueError(
            "Source and target currencies must be different."
        )

    try:
        cursor = _execute(
            db,
            """
            INSERT INTO finance_exchange_rates (
                from_currency,
                to_currency,
                exchange_rate,
                effective_date,
                source,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                from_currency.upper(),
                to_currency.upper(),
                exchange_rate,
                effective_date,
                source,
                created_by,
                _now()
            )
        )

        rate_id = cursor.lastrowid
        db.commit()

        return rate_id

    except Exception:
        db.rollback()
        raise


def get_exchange_rate(
    db,
    from_currency,
    to_currency,
    effective_date
):
    """Get the latest active rate effective on a given date."""

    _validate_connection(db)

    if from_currency.upper() == to_currency.upper():
        return 1.0

    row = _execute(
        db,
        """
        SELECT exchange_rate
        FROM finance_exchange_rates
        WHERE from_currency = ?
          AND to_currency = ?
          AND effective_date <= ?
          AND status = 'Active'
        ORDER BY effective_date DESC, id DESC
        LIMIT 1
        """,
        (
            from_currency.upper(),
            to_currency.upper(),
            effective_date
        )
    ).fetchone()

    if not row:
        raise ValueError(
            f"No exchange rate found for "
            f"{from_currency}/{to_currency}."
        )

    return float(row["exchange_rate"])


# =========================================================
# JOURNAL BALANCE SUMMARY
# =========================================================

def get_journal_balance(db, journal_entry_id):
    """Calculate debit and credit totals in base currency."""

    _validate_connection(db)

    row = _execute(
        db,
        """
        SELECT
            COALESCE(SUM(debit_base), 0) AS total_debit,
            COALESCE(SUM(credit_base), 0) AS total_credit
        FROM finance_journal_lines
        WHERE journal_entry_id = ?
        """,
        (journal_entry_id,)
    ).fetchone()

    debit = round(float(row["total_debit"]), 2)
    credit = round(float(row["total_credit"]), 2)

    return {
        "total_debit": debit,
        "total_credit": credit,
        "difference": round(debit - credit, 2),
        "is_balanced": (
            debit > 0 and abs(debit - credit) < 0.01
        )
    }
