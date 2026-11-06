"""Budget plans, allocations, commitments and finance sync."""

from database.database_config import table_exists
from services.hr import hr_department_options
from datetime import date
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
import re


BUDGET_CURRENCIES = ("ETB", "USD", "EUR", "GBP")


BUDGET_PLANS_DDL = """
    CREATE TABLE IF NOT EXISTS budget_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_name TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        total_budget NUMERIC NOT NULL CHECK (total_budget >= 0),
        currency TEXT NOT NULL DEFAULT 'ETB'
            CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
        status TEXT NOT NULL DEFAULT 'Draft'
            CHECK (status IN ('Draft', 'Approved', 'Active', 'Closed')),
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""


BUDGET_ALLOCATIONS_DDL = """
    CREATE TABLE IF NOT EXISTS budget_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        allocation_name TEXT NOT NULL,
        department TEXT NOT NULL DEFAULT 'general',
        currency TEXT NOT NULL DEFAULT 'ETB'
            CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
        allocated_amount NUMERIC NOT NULL CHECK (allocated_amount >= 0),
        status TEXT NOT NULL DEFAULT 'Active'
            CHECK (status IN ('Active', 'Closed')),
        notes TEXT NOT NULL DEFAULT '',
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (plan_id) REFERENCES budget_plans(id)
    )
"""


BUDGET_REQUESTS_DDL = """
    CREATE TABLE IF NOT EXISTS budget_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_title TEXT NOT NULL,
        department TEXT NOT NULL,
        justification TEXT NOT NULL DEFAULT '',
        requested_amount NUMERIC NOT NULL CHECK (requested_amount > 0),
        currency TEXT NOT NULL DEFAULT 'ETB'
            CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
        status TEXT NOT NULL DEFAULT 'Pending'
            CHECK (status IN ('Pending', 'Approved', 'Rejected')),
        plan_id INTEGER,
        requested_by INTEGER,
        requested_by_name TEXT NOT NULL DEFAULT '',
        review_comment TEXT NOT NULL DEFAULT '',
        reviewed_by TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (plan_id) REFERENCES budget_plans(id)
    )
"""


BUDGET_APPROVALS_DDL = """
    CREATE TABLE IF NOT EXISTS budget_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        approver_id INTEGER,
        approver_name TEXT NOT NULL DEFAULT '',
        decision TEXT NOT NULL,
        comment TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (request_id) REFERENCES budget_requests(id)
            ON DELETE CASCADE
    )
"""


BUDGET_EXPENDITURES_DDL = """
    CREATE TABLE IF NOT EXISTS budget_expenditures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        allocation_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        amount NUMERIC NOT NULL CHECK (amount > 0),
        currency TEXT NOT NULL DEFAULT 'ETB'
            CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
        reference TEXT NOT NULL DEFAULT '',
        recorded_by INTEGER,
        recorded_by_name TEXT NOT NULL DEFAULT '',
        procurement_order_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (allocation_id) REFERENCES budget_allocations(id)
    )
"""


BUDGET_COMMITMENTS_DDL = """
    CREATE TABLE IF NOT EXISTS budget_commitments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        allocation_id INTEGER NOT NULL,
        source_table TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        amount NUMERIC NOT NULL CHECK (amount >= 0),
        currency TEXT NOT NULL DEFAULT 'ETB',
        status TEXT NOT NULL DEFAULT 'Open'
            CHECK (status IN ('Open', 'Released', 'Converted')),
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (source_table, source_id),
        FOREIGN KEY (allocation_id) REFERENCES budget_allocations(id)
    )
"""


def add_column_if_missing(db, table, column, definition):
    schema, bare = (table.split(".", 1) if "." in table else (None, table))
    pragma = ("PRAGMA {}.table_info({})".format(schema, bare)
              if schema else "PRAGMA table_info({})".format(bare))
    columns = {row[1] for row in db.execute(pragma)}
    if column in columns:
        return
    db.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition))


def init_budget_schema(db):
    """Create the budget module and the links the other desks need.

    Budget money is checked against three figures: what was allocated, what has
    already been spent, and what open commitments have reserved. The links added
    here let a requisition, an order and a supplier invoice point at the same
    allocation, which is what makes those three figures trustworthy.
    """
    for ddl in (BUDGET_PLANS_DDL, BUDGET_ALLOCATIONS_DDL, BUDGET_REQUESTS_DDL,
                BUDGET_APPROVALS_DDL, BUDGET_EXPENDITURES_DDL,
                BUDGET_COMMITMENTS_DDL):
        db.execute(ddl)
    db.execute("CREATE INDEX IF NOT EXISTS idx_budget_alloc_plan"
               " ON budget_allocations(plan_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_budget_alloc_department"
               " ON budget_allocations(department)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_budget_expenditure_alloc"
               " ON budget_expenditures(allocation_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_budget_commitment_alloc"
               " ON budget_commitments(allocation_id, status)")

    add_column_if_missing(db, "finance.procurement_requests",
                          "budget_allocation_id", "INTEGER")
    add_column_if_missing(db, "finance.procurement_orders",
                          "budget_allocation_id", "INTEGER")
    add_column_if_missing(db, "finance.procurement_orders", "invoiced_at", "TEXT")

    # The budget tables live in users.db beside the account tables. Finance lives
    # in finance.db and is attached to the same connection, so these links are
    # plain ids: SQLite cannot enforce a foreign key across two database files.
    add_column_if_missing(db, "budget_allocations", "department_id",
                          "INTEGER REFERENCES departments(id)")
    add_column_if_missing(db, "budget_requests", "department_id",
                          "INTEGER REFERENCES departments(id)")
    add_column_if_missing(db, "budget_expenditures", "finance_invoice_id",
                          "INTEGER")
    add_column_if_missing(db, "budget_expenditures", "finance_transaction_id",
                          "INTEGER")

    migrate_budget_links(db)


def budget_department_key(value):
    """One key for 'HR', 'Human Resources' and the requisition's 'hr'."""
    key = re.sub(r"[^a-z ]", "", (value or "").strip().lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return {
        "human resources": "hr",
        "human resource": "hr",
        "administration": "general",
        "admin": "general",
    }.get(key, key)


def budget_departments_defined(db):
    return db.execute(
        "SELECT 1 FROM departments WHERE is_active = 1 LIMIT 1"
    ).fetchone() is not None


def budget_department_names(db):
    """The department choices come from the HR master list, not a second one."""
    return [row["name"] for row in hr_department_options(db, active_only=True)]


def budget_department_match(db, name):
    return db.execute(
        "SELECT id, name FROM departments"
        " WHERE name = ? COLLATE NOCASE AND is_active = 1",
        ((name or "").strip(),),
    ).fetchone()


def budget_spendable_allocations(db):
    return [row for row in budget_allocation_rows(db)
            if row["status"] == "Active"
            and row["plan_status"] in ("Active", "Approved")]


def budget_allocation(db, allocation_id):
    return db.execute("""
        SELECT a.*, p.plan_name, p.fiscal_year, p.status AS plan_status
        FROM budget_allocations AS a
        LEFT JOIN budget_plans AS p ON p.id = a.plan_id
        WHERE a.id = ?
    """, (allocation_id,)).fetchone()


def budget_committed_amount(db, allocation_id, exclude_table=None, exclude_id=None):
    sql = """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM budget_commitments
        WHERE allocation_id = ? AND status = 'Open'
    """
    params = [allocation_id]
    if exclude_table is not None and exclude_id is not None:
        sql += " AND NOT (source_table = ? AND source_id = ?)"
        params.extend([exclude_table, exclude_id])
    return round(float(db.execute(sql, params).fetchone()["total"] or 0), 2)


def budget_spent_amount(db, allocation_id):
    return round(float(db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM budget_expenditures"
        " WHERE allocation_id = ?", (allocation_id,)).fetchone()["total"] or 0), 2)


def budget_available(db, allocation_id, exclude_table=None, exclude_id=None):
    """Allocated minus actual spending minus money already promised."""
    allocation = budget_allocation(db, allocation_id)
    if allocation is None:
        return 0.0
    return round(float(allocation["allocated_amount"] or 0)
                 - budget_spent_amount(db, allocation_id)
                 - budget_committed_amount(db, allocation_id,
                                           exclude_table, exclude_id), 2)


def budget_active_allocations(db, currency="ETB"):
    return db.execute("""
        SELECT a.*, p.plan_name, p.fiscal_year,
               a.allocated_amount
                 - (SELECT COALESCE(SUM(e.amount), 0) FROM budget_expenditures e
                     WHERE e.allocation_id = a.id)
                 - (SELECT COALESCE(SUM(c.amount), 0) FROM budget_commitments c
                     WHERE c.allocation_id = a.id AND c.status = 'Open')
                 AS available_amount
        FROM budget_allocations AS a
        JOIN budget_plans AS p ON p.id = a.plan_id
        WHERE a.status = 'Active' AND a.currency = ?
          AND p.status IN ('Active', 'Approved')
        ORDER BY a.department, a.allocation_name
    """, (currency,)).fetchall()


def budget_commit(db, allocation_id, source_table, source_id, amount,
                  description="", currency="ETB"):
    """Reserve money for a requisition or order, keeping one row per record."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute("""
        INSERT INTO budget_commitments (
            allocation_id, source_table, source_id, amount, currency,
            status, description, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'Open', ?, ?, ?)
        ON CONFLICT(source_table, source_id) DO UPDATE SET
            allocation_id = excluded.allocation_id,
            amount = excluded.amount,
            currency = excluded.currency,
            description = excluded.description,
            status = 'Open',
            updated_at = excluded.updated_at
    """, (allocation_id, source_table, source_id, round(float(amount), 2),
          currency, description, now, now))


def budget_release(db, source_table, source_id, status="Released"):
    db.execute("""
        UPDATE budget_commitments SET status = ?, updated_at = ?
        WHERE source_table = ? AND source_id = ? AND status = 'Open'
    """, (status, datetime.now(timezone.utc).isoformat(timespec="seconds"),
          source_table, source_id))


def budget_problem_for(db, allocation_id, amount, exclude_table=None,
                       exclude_id=None, require_active=True):
    """The server-side guard every spending screen shares."""
    allocation = budget_allocation(db, allocation_id)
    if allocation is None:
        return "Select a valid budget allocation."
    if require_active and allocation["status"] != "Active":
        return "That allocation is closed and cannot take new spending."
    if allocation["plan_status"] not in ("Active", "Approved"):
        return "The budget plan behind that allocation is not active yet."
    available = budget_available(db, allocation_id, exclude_table, exclude_id)
    if round(float(amount), 2) > available + 0.005:
        return ("That allocation only has {:,.2f} {} available; the request is "
                "{:,.2f} {}.".format(available, allocation["currency"],
                                     float(amount), allocation["currency"]))
    return None


def budget_finance_actor(db, created_by):
    """The account behind a Finance row, whose created_by is stored as text."""
    try:
        user_id = int(str(created_by).strip())
    except (TypeError, ValueError):
        return None, ""
    row = db.execute("SELECT full_name FROM users WHERE id = ?",
                     (user_id,)).fetchone()
    return user_id, (row["full_name"] if row else "")


def budget_record_finance_spend(db, allocation_id, description, amount, currency,
                                reference, recorded_by, recorded_by_name,
                                finance_transaction_id=None,
                                finance_invoice_id=None):
    """Book a Finance expense against an allocation, once per Finance record."""
    if finance_transaction_id is not None:
        linked = db.execute(
            "SELECT id FROM budget_expenditures WHERE finance_transaction_id = ?",
            (finance_transaction_id,)).fetchone()
    elif finance_invoice_id is not None:
        linked = db.execute(
            "SELECT id FROM budget_expenditures WHERE finance_invoice_id = ?",
            (finance_invoice_id,)).fetchone()
    else:
        linked = None
    if linked is not None:
        return linked["id"]

    return db.execute("""
        INSERT INTO budget_expenditures (
            allocation_id, description, amount, currency, reference,
            recorded_by, recorded_by_name, finance_transaction_id,
            finance_invoice_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        allocation_id, (description or "Expense")[:1000],
        str(Decimal(str(amount)).quantize(Decimal("0.01"))), currency,
        (reference or "")[:100], recorded_by, (recorded_by_name or "")[:150],
        finance_transaction_id, finance_invoice_id,
    )).lastrowid


def budget_post_finance_expenditure(db, expenditure_id, actor_id,
                                    exchange_rate=None):
    """Post a manually recorded expenditure to the Finance ledger once."""
    linked = db.execute(
        "SELECT id FROM finance.finance_transactions WHERE budget_expenditure_id = ?",
        (expenditure_id,)).fetchone()
    if linked is not None:
        return linked["id"]

    expenditure = db.execute(
        "SELECT * FROM budget_expenditures WHERE id = ?",
        (expenditure_id,)).fetchone()
    if expenditure is None:
        return None

    currency = expenditure["currency"] or "ETB"
    if currency == "ETB":
        rate = Decimal("1")
    else:
        try:
            rate = Decimal(str(exchange_rate))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(
                "Enter the exchange rate so this expenditure can be posted to Finance.")
        if not rate.is_finite() or rate <= 0:
            raise ValueError("The exchange rate must be greater than zero.")

    amount = Decimal(str(expenditure["amount"]))
    amount_etb = (amount * rate).quantize(Decimal("0.01"))
    reference = (expenditure["reference"] or "").strip() or "BUDGET-{}".format(
        expenditure_id)
    transaction_id = db.execute("""
        INSERT INTO finance.finance_transactions (
            transaction_date, transaction_type, category, description,
            party_name, currency, amount, exchange_rate, amount_etb,
            payment_method, reference, created_by, budget_allocation_id,
            budget_expenditure_id
        ) VALUES (?, 'expense', 'Budget expenditure', ?, '', ?, ?, ?, ?,
                  'Other', ?, ?, ?, ?)
    """, (
        date.today().isoformat(), expenditure["description"], currency,
        str(amount), str(rate), str(amount_etb), reference,
        str(actor_id), expenditure["allocation_id"], expenditure_id,
    )).lastrowid
    db.execute(
        "UPDATE budget_expenditures SET finance_transaction_id = ? WHERE id = ?",
        (transaction_id, expenditure_id))
    return transaction_id


def budget_sync_finance_history(db):
    """Fill the links for records booked before Finance and Budget were joined."""
    db.execute("""
        UPDATE budget_expenditures
        SET finance_invoice_id = (
            SELECT i.id FROM finance.finance_invoices AS i
            WHERE i.procurement_order_id = budget_expenditures.procurement_order_id
            ORDER BY i.id LIMIT 1
        )
        WHERE finance_invoice_id IS NULL AND procurement_order_id IS NOT NULL
    """)
    db.execute("""
        UPDATE budget_expenditures
        SET finance_transaction_id = (
            SELECT t.id FROM finance.finance_transactions AS t
            WHERE t.procurement_order_id = budget_expenditures.procurement_order_id
              AND t.budget_allocation_id = budget_expenditures.allocation_id
            ORDER BY t.id LIMIT 1
        )
        WHERE finance_transaction_id IS NULL AND procurement_order_id IS NOT NULL
    """)
    db.execute("""
        UPDATE finance.finance_transactions
        SET budget_expenditure_id = (
            SELECT e.id FROM budget_expenditures AS e
            WHERE e.finance_transaction_id = finance.finance_transactions.id
            ORDER BY e.id LIMIT 1
        )
        WHERE budget_expenditure_id IS NULL
    """)

    transactions = db.execute("""
        SELECT t.id, t.description, t.amount, t.currency, t.reference,
               t.created_by, t.budget_allocation_id
        FROM finance.finance_transactions AS t
        JOIN budget_allocations AS a ON a.id = t.budget_allocation_id
        LEFT JOIN budget_expenditures AS e ON e.finance_transaction_id = t.id
        WHERE t.transaction_type = 'expense'
          AND t.budget_allocation_id IS NOT NULL
          AND e.id IS NULL
          AND a.currency = t.currency
    """).fetchall()
    for row in transactions:
        actor_id, actor_name = budget_finance_actor(db, row["created_by"])
        expenditure_id = budget_record_finance_spend(
            db, row["budget_allocation_id"],
            row["description"] or "Finance transaction #{}".format(row["id"]),
            row["amount"], row["currency"], row["reference"] or "",
            actor_id, actor_name, finance_transaction_id=row["id"])
        db.execute("UPDATE finance.finance_transactions SET budget_expenditure_id = ?"
                   " WHERE id = ?", (expenditure_id, row["id"]))

    invoices = db.execute("""
        SELECT i.id, i.invoice_number, i.party_name, i.total_amount, i.currency,
               i.created_by, i.budget_allocation_id
        FROM finance.finance_invoices AS i
        JOIN budget_allocations AS a ON a.id = i.budget_allocation_id
        LEFT JOIN budget_expenditures AS e ON e.finance_invoice_id = i.id
        WHERE i.invoice_type = 'supplier'
          AND i.budget_allocation_id IS NOT NULL
          AND e.id IS NULL
          AND a.currency = i.currency
    """).fetchall()
    for row in invoices:
        actor_id, actor_name = budget_finance_actor(db, row["created_by"])
        budget_record_finance_spend(
            db, row["budget_allocation_id"],
            "Supplier invoice {} · {}".format(row["invoice_number"],
                                               row["party_name"]),
            row["total_amount"], row["currency"], row["invoice_number"],
            actor_id, actor_name, finance_invoice_id=row["id"])


def migrate_budget_links(db):
    """Attach the Budget tables to User Management and Finance in place."""
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_budget_alloc_department_id"
        " ON budget_allocations(department_id)",
        "CREATE INDEX IF NOT EXISTS idx_budget_req_department_id"
        " ON budget_requests(department_id)",
        "CREATE INDEX IF NOT EXISTS idx_budget_req_requester"
        " ON budget_requests(requested_by)",
        "CREATE INDEX IF NOT EXISTS idx_budget_exp_recorder"
        " ON budget_expenditures(recorded_by)",
        "CREATE INDEX IF NOT EXISTS idx_budget_exp_finance_tx"
        " ON budget_expenditures(finance_transaction_id)",
        "CREATE INDEX IF NOT EXISTS idx_budget_exp_finance_invoice"
        " ON budget_expenditures(finance_invoice_id)",
    ):
        db.execute(statement)

    # The department register lives in the HR file, so a correlated
    # "UPDATE budget... SET x = (SELECT ... FROM hr...)" cannot name its own
    # table bare inside the subquery - the two files make the reference
    # unresolvable. One lookup and one keyed write per row keeps each statement
    # inside a single file, and it only runs once both sides exist on a first
    # boot.
    if table_exists(db, "departments"):
        department_ids = {
            (name or "").strip().lower(): department_id
            for department_id, name in db.execute("SELECT id, name FROM departments")
        }
        for table in ("budget_allocations", "budget_requests"):
            pending = db.execute(
                "SELECT id, department FROM {0}"
                " WHERE department_id IS NULL AND department <> ''".format(table)
            ).fetchall()
            for row in pending:
                department_id = department_ids.get(
                    (row["department"] or "").strip().lower())
                if department_id is None:
                    continue
                db.execute(
                    "UPDATE {0} SET department_id = ? WHERE id = ?".format(table),
                    (department_id, row["id"]))

    budget_sync_finance_history(db)


def budget_amount(value, field="Amount"):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("{} must be a valid number.".format(field))
    if not amount.is_finite() or amount < 0:
        raise ValueError("{} cannot be negative.".format(field))
    return amount.quantize(Decimal("0.01"))


def budget_allocation_rows(db):
    return db.execute("""
        SELECT a.*, p.plan_name, p.fiscal_year, p.status AS plan_status,
               (SELECT COALESCE(SUM(e.amount), 0) FROM budget_expenditures e
                 WHERE e.allocation_id = a.id) AS spent_amount,
               (SELECT COALESCE(SUM(c.amount), 0) FROM budget_commitments c
                 WHERE c.allocation_id = a.id AND c.status = 'Open')
                 AS committed_amount,
               a.allocated_amount
                 - (SELECT COALESCE(SUM(e.amount), 0) FROM budget_expenditures e
                     WHERE e.allocation_id = a.id)
                 - (SELECT COALESCE(SUM(c.amount), 0) FROM budget_commitments c
                     WHERE c.allocation_id = a.id AND c.status = 'Open')
                 AS available_amount
        FROM budget_allocations AS a
        LEFT JOIN budget_plans AS p ON p.id = a.plan_id
        ORDER BY CASE a.status WHEN 'Active' THEN 0 ELSE 1 END,
                 a.department, a.id DESC
    """).fetchall()


def budget_overview(db):
    """Budget figures and short lists for the shared dashboard."""
    plans = db.execute("""
        SELECT * FROM budget_plans ORDER BY fiscal_year DESC, id DESC
    """).fetchall()
    allocated_total = db.execute("""
        SELECT COALESCE(SUM(allocated_amount), 0) AS total
        FROM budget_allocations WHERE currency = 'ETB' AND status = 'Active'
    """).fetchone()["total"]
    spent_total = db.execute("""
        SELECT COALESCE(SUM(e.amount), 0) AS total
        FROM budget_expenditures AS e
        JOIN budget_allocations AS a ON a.id = e.allocation_id
        WHERE a.currency = 'ETB' AND e.currency = 'ETB'
    """).fetchone()["total"]
    committed_total = db.execute("""
        SELECT COALESCE(SUM(c.amount), 0) AS total
        FROM budget_commitments AS c
        JOIN budget_allocations AS a ON a.id = c.allocation_id
        WHERE c.status = 'Open' AND a.currency = 'ETB' AND c.currency = 'ETB'
    """).fetchone()["total"]
    counts_row = db.execute("""
        SELECT COUNT(*) AS total_requests,
               SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END)
                   AS pending_requests,
               SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END)
                   AS approved_requests,
               SUM(CASE WHEN status = 'Rejected' THEN 1 ELSE 0 END)
                   AS rejected_requests
        FROM budget_requests
    """).fetchone()
    request_counts = {key: int(counts_row[key] or 0) for key in counts_row.keys()}
    request_counts["approved_note"] = "{} total · {} rejected".format(
        request_counts["total_requests"], request_counts["rejected_requests"])
    recent_requests = db.execute("""
        SELECT id, request_title, department, requested_amount, currency,
               status, created_at
        FROM budget_requests ORDER BY id DESC LIMIT 10
    """).fetchall()
    return {
        "plans": plans,
        "allocated_total": allocated_total,
        "spent_total": spent_total,
        "committed_total": committed_total,
        "available_total": float(allocated_total) - float(spent_total)
        - float(committed_total),
        "request_counts": request_counts,
        "recent_requests": recent_requests,
    }
