"""Finance records live in their own SQLite database, attached as `finance`.

The rest of the system keeps accounts, budget and procurement in users.db. This
module owns finance.db: it attaches the file to every application connection,
creates the money tables there, and moves records out of a database that still
holds them inline. Cross-module joins keep working because SQLite can join an
attached database in the same query; the link columns stay on both sides.
"""
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get(
    "WK_FINANCE_DB_PATH", str(BASE_DIR / "finance.db"))).resolve()

SCHEMA = "finance"
TABLES = ("finance_transactions", "finance_invoices", "finance_payments",
          "procurement_suppliers", "procurement_requests",
          "procurement_orders", "procurement_request_items",
          "procurement_deliveries")
TABLE_SET = frozenset(TABLES)

# Parents before children: the copy assigns no ids, so a child row can only
# land once its parent exists. The drop runs the other way round.
COPY_ORDER = ("procurement_suppliers", "procurement_requests",
              "procurement_orders", "procurement_request_items",
              "procurement_deliveries", "finance_invoices",
              "finance_transactions", "finance_payments")
DROP_ORDER = ("finance_payments", "procurement_deliveries",
              "procurement_request_items", "procurement_orders",
              "procurement_requests", "procurement_suppliers",
              "finance_invoices", "finance_transactions")

# The finance side keeps no foreign key into users.db: SQLite cannot enforce a
# constraint across attached databases. Budget and procurement links are plain
# id columns, checked by the application and by the consistency test.
TRANSACTIONS_DDL = """
    CREATE TABLE IF NOT EXISTS finance.finance_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_date TEXT NOT NULL,
        transaction_type TEXT NOT NULL
            CHECK (transaction_type IN ('income', 'expense')),
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        party_name TEXT DEFAULT '',
        employee_id INTEGER,
        currency TEXT NOT NULL DEFAULT 'ETB',
        amount NUMERIC NOT NULL CHECK (amount > 0),
        exchange_rate NUMERIC NOT NULL CHECK (exchange_rate > 0),
        amount_etb NUMERIC NOT NULL CHECK (amount_etb > 0),
        payment_method TEXT NOT NULL,
        reference TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        budget_allocation_id INTEGER,
        procurement_order_id INTEGER,
        budget_expenditure_id INTEGER
    )
"""

INVOICES_DDL = """
    CREATE TABLE IF NOT EXISTS finance.finance_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_number TEXT NOT NULL UNIQUE,
        invoice_type TEXT NOT NULL
            CHECK (invoice_type IN ('customer', 'supplier')),
        party_name TEXT NOT NULL,
        party_contact TEXT DEFAULT '',
        issue_date TEXT NOT NULL,
        due_date TEXT,
        currency TEXT NOT NULL DEFAULT 'ETB',
        total_amount NUMERIC NOT NULL CHECK (total_amount > 0),
        exchange_rate NUMERIC NOT NULL CHECK (exchange_rate > 0),
        total_etb NUMERIC NOT NULL CHECK (total_etb > 0),
        notes TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Unpaid'
            CHECK (status IN ('Unpaid', 'Partially Paid', 'Paid', 'Cancelled')),
        created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        budget_allocation_id INTEGER,
        procurement_order_id INTEGER
    )
"""

PAYMENTS_DDL = """
    CREATE TABLE IF NOT EXISTS finance.finance_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        payment_date TEXT NOT NULL,
        amount NUMERIC NOT NULL CHECK (amount > 0),
        amount_etb NUMERIC NOT NULL CHECK (amount_etb > 0),
        payment_method TEXT NOT NULL,
        reference TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES finance_invoices(id)
    )
"""

# An index on an attached table is named with the schema and points at the bare
# table name; SQLite rejects a schema prefix on the table side of CREATE INDEX.
INDEXES = (
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_tx_date"
    " ON finance_transactions(transaction_date)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_tx_type"
    " ON finance_transactions(transaction_type, transaction_date)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_tx_alloc"
    " ON finance_transactions(budget_allocation_id)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_tx_expenditure"
    " ON finance_transactions(budget_expenditure_id)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_inv_status"
    " ON finance_invoices(status)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_inv_issue"
    " ON finance_invoices(issue_date)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_inv_alloc"
    " ON finance_invoices(budget_allocation_id)",
    "CREATE INDEX IF NOT EXISTS finance.idx_finance_pay_invoice"
    " ON finance_payments(invoice_id)",
    "CREATE INDEX IF NOT EXISTS finance.idx_proc_req_status"
    " ON procurement_requests(status)",
    "CREATE INDEX IF NOT EXISTS finance.idx_proc_req_creator"
    " ON procurement_requests(requested_by, created_at)",
    "CREATE INDEX IF NOT EXISTS finance.idx_proc_req_department"
    " ON procurement_requests(department, id DESC)",
    "CREATE INDEX IF NOT EXISTS finance.idx_proc_order_status"
    " ON procurement_orders(status)",
    "CREATE INDEX IF NOT EXISTS finance.idx_proc_order_created"
    " ON procurement_orders(created_at)",
    "CREATE INDEX IF NOT EXISTS finance.idx_po_request"
    " ON procurement_orders(request_id)",
    "CREATE INDEX IF NOT EXISTS finance.idx_pr_items_request"
    " ON procurement_request_items(request_id)",
    "CREATE INDEX IF NOT EXISTS finance.idx_pr_delivery_order"
    " ON procurement_deliveries(order_id)",
)

# budget_expenditures stays in users.db. Its finance links become plain ids
# because a foreign key cannot point at another database.
BUDGET_EXPENDITURES_DDL = """
    CREATE TABLE {name} (
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
        finance_invoice_id INTEGER,
        finance_transaction_id INTEGER,
        FOREIGN KEY (allocation_id) REFERENCES budget_allocations(id)
    )
"""

# Purchasing sits with the money: a requisition commits a budget line and an
# order becomes an invoice and an expenditure, so the two live in one file.
# Nothing in these tables points at budget_allocations across databases - the
# allocation id is a plain column checked by the application.
SUPPLIERS_DDL = """
    CREATE TABLE IF NOT EXISTS finance.procurement_suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        contact_person TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        address TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""

REQUESTS_DDL = """
    CREATE TABLE IF NOT EXISTS finance.procurement_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_number TEXT NOT NULL UNIQUE,
        department TEXT NOT NULL DEFAULT 'general',
        item_name TEXT NOT NULL,
        quantity REAL NOT NULL CHECK (quantity > 0),
        unit TEXT NOT NULL DEFAULT 'pcs',
        estimated_cost REAL NOT NULL DEFAULT 0 CHECK (estimated_cost >= 0),
        supplier_id INTEGER,
        requested_by INTEGER NOT NULL,
        requested_by_name TEXT NOT NULL,
        justification TEXT NOT NULL DEFAULT '',
        notes TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Pending'
            CHECK (status IN ('Draft', 'Pending', 'Approved', 'Rejected')),
        submitted_at TEXT,
        approval_request_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        budget_allocation_id INTEGER,
        FOREIGN KEY (supplier_id) REFERENCES procurement_suppliers(id)
    )
"""

ORDERS_DDL = """
    CREATE TABLE IF NOT EXISTS finance.procurement_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT NOT NULL UNIQUE,
        supplier_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        quantity REAL NOT NULL CHECK (quantity > 0),
        unit TEXT NOT NULL DEFAULT 'pcs',
        unit_price REAL NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
        total_cost REAL NOT NULL DEFAULT 0 CHECK (total_cost >= 0),
        expected_date TEXT,
        status TEXT NOT NULL DEFAULT 'Ordered'
            CHECK (status IN (
                'Ordered', 'Partially Received', 'Received', 'Cancelled'
            )),
        created_by INTEGER NOT NULL,
        created_by_name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        request_id INTEGER,
        issued_at TEXT,
        budget_allocation_id INTEGER,
        invoiced_at TEXT,
        FOREIGN KEY (supplier_id) REFERENCES procurement_suppliers(id)
    )
"""

REQUEST_ITEMS_DDL = """
    CREATE TABLE IF NOT EXISTS finance.procurement_request_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        line_number INTEGER NOT NULL DEFAULT 1,
        item_name TEXT NOT NULL,
        quantity REAL NOT NULL CHECK (quantity > 0),
        unit TEXT NOT NULL DEFAULT 'pcs',
        unit_price REAL NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
        line_total REAL NOT NULL DEFAULT 0 CHECK (line_total >= 0),
        notes TEXT DEFAULT '',
        FOREIGN KEY (request_id) REFERENCES procurement_requests(id)
            ON DELETE CASCADE
    )
"""

DELIVERIES_DDL = """
    CREATE TABLE IF NOT EXISTS finance.procurement_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        delivery_date TEXT NOT NULL,
        quantity REAL NOT NULL CHECK (quantity > 0),
        notes TEXT NOT NULL DEFAULT '',
        received_by INTEGER,
        received_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES procurement_orders(id)
    )
"""

PROCUREMENT_DDL = (SUPPLIERS_DDL, REQUESTS_DDL, ORDERS_DDL,
                   REQUEST_ITEMS_DDL, DELIVERIES_DDL)


def ref(table):
    """The name to use in SQL for a finance table."""
    return "{}.{}".format(SCHEMA, table)


def attach(db):
    """Attach finance.db once per connection."""
    for row in db.execute("PRAGMA database_list"):
        if row[1] == SCHEMA:
            return
    db.execute("ATTACH DATABASE ? AS {}".format(SCHEMA), (str(DB_PATH),))


def table_names(db):
    """Finance tables that currently exist, as bare names."""
    attach(db)
    return {row[0] for row in db.execute(
        "SELECT name FROM {}.sqlite_master WHERE type = 'table'".format(SCHEMA))}


def init(db):
    """Create the finance database and move inline records into it."""
    attach(db)
    _enable_wal()
    for ddl in (TRANSACTIONS_DDL, INVOICES_DDL, PAYMENTS_DDL) + PROCUREMENT_DDL:
        db.execute(ddl)
    for index in INDEXES:
        db.execute(index)
    _migrate_inline_tables(db)


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
    """Copy finance and purchasing rows out of users.db, verify, then drop.

    A database built before this split still carries these tables inline. The
    copy keeps every id, so budget, approval and invoice links survive. The old
    tables are only removed after each new table holds at least as many rows as
    the old one; a shortfall aborts the whole boot transaction.
    """
    legacy = [table for table in TABLES if _has_table(db, "main", table)]
    if not legacy:
        return

    _strip_finance_foreign_keys(db)

    # Parents before children: items point at requests, deliveries at orders.
    for table in COPY_ORDER:
        if table not in legacy:
            continue
        target = _columns(db, SCHEMA, table)
        source = _columns(db, "main", table)
        shared = [name for name in source if name in target]
        if not shared:
            raise RuntimeError(
                "Finance migration found no shared columns for {}".format(table))
        db.execute(
            "INSERT OR IGNORE INTO {schema}.{table} ({cols})"
            " SELECT {cols} FROM main.{table}".format(
                schema=SCHEMA, table=table, cols=", ".join(shared)))
        if _count(db, SCHEMA, table) < _count(db, "main", table):
            raise RuntimeError(
                "Finance migration lost rows from {}; users.db was left intact"
                .format(table))

    for table in DROP_ORDER:
        if table in legacy:
            db.execute("DROP TABLE main.{}".format(table))


def _strip_finance_foreign_keys(db):
    """Rebuild budget_expenditures without its two cross-database-looking keys."""
    if not _has_table(db, "main", "budget_expenditures"):
        return
    row = db.execute(
        "SELECT sql FROM main.sqlite_master WHERE type='table'"
        " AND name='budget_expenditures'").fetchone()
    if row is None or "references finance_" not in (row[0] or "").lower():
        return

    old_columns = _columns(db, "main", "budget_expenditures")
    temp = "budget_expenditures__finance_split"
    db.execute("DROP TABLE IF EXISTS main.{}".format(temp))
    db.execute(BUDGET_EXPENDITURES_DDL.format(name=temp))
    wanted = _columns(db, "main", temp)
    shared = [name for name in old_columns if name in wanted]
    db.execute(
        "INSERT INTO main.{temp} ({cols}) SELECT {cols} FROM main.budget_expenditures"
        .format(temp=temp, cols=", ".join(shared)))
    db.execute("DROP TABLE main.budget_expenditures")
    db.execute("ALTER TABLE main.{} RENAME TO budget_expenditures".format(temp))
