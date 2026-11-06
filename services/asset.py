"""Asset management: the fixed-asset register, its categories and its custodian trail.

The company buys a machine, a laptop or a set of tools once and keeps using it
for years. This desk keeps one row per unit - what it is, what it cost, where it
sits and who is answerable for it - so the accountant who wrote the cheque and
the storekeeper holding the key read the same record.

The register does not build a second staff or department list. A custodian is an
account from the user master data and a department comes from the HR register;
both are checked against those desks when an allocation is written, because
SQLite cannot hold a foreign key across two files. Every write runs through the
validators below, so a form, a direct URL and a forged POST are answered in the
same words.
"""

from core.db import check_in
from core.db import connect_db
from core.validation import clean_amount
from core.validation import clean_choice
from core.validation import clean_date
from core.validation import clean_text
from datetime import date
from datetime import datetime
from datetime import timezone
import sqlite3


# ---------------------------------------------------------------- vocabularies
# One tuple per choice list. The screens draw their selects from these and the
# DDL repeats the same words in a CHECK, so a stale browser tab cannot invent a
# status the table would refuse.
ASSET_STATUSES = ("Available", "Assigned", "Maintenance", "Inactive")
ASSET_CONDITIONS = ("Excellent", "Good", "Fair", "Poor", "Damaged")
CATEGORY_STATUSES = ("Active", "Inactive")
ALLOCATION_STATUSES = ("Active", "Returned")

# The words a register row may hold. Inactive is deliberately absent: a unit is
# deactivated by its own action, not by an edit that quietly hides it from the
# live count.
LIVE_ASSET_STATUSES = ("Available", "Assigned", "Maintenance")


CATEGORY_DDL = """
    CREATE TABLE IF NOT EXISTS asset_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        parent_id INTEGER,
        status TEXT NOT NULL DEFAULT 'Active' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_by INTEGER,
        updated_by_name TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (parent_id) REFERENCES asset_categories(id)
    )
""".format(status=check_in("status", CATEGORY_STATUSES))


ASSET_DDL = """
    CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        category_id INTEGER NOT NULL,
        serial_number TEXT NOT NULL DEFAULT '',
        model_number TEXT NOT NULL DEFAULT '',
        manufacturer TEXT NOT NULL DEFAULT '',
        purchase_date TEXT,
        purchase_cost NUMERIC NOT NULL DEFAULT 0 CHECK (purchase_cost >= 0),
        warranty_expiry TEXT,
        supplier TEXT NOT NULL DEFAULT '',
        purchase_order_number TEXT NOT NULL DEFAULT '',
        procurement_order_id INTEGER,
        funding_source TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        condition TEXT NOT NULL DEFAULT 'Good' {condition},
        status TEXT NOT NULL DEFAULT 'Available' {status},
        department_id INTEGER,
        department_name TEXT NOT NULL DEFAULT '',
        custodian_user_id INTEGER,
        custodian_name TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_by INTEGER,
        updated_by_name TEXT NOT NULL DEFAULT '',
        CHECK ((purchase_date IS NULL OR warranty_expiry IS NULL)
               OR warranty_expiry >= purchase_date),
        FOREIGN KEY (category_id) REFERENCES asset_categories(id)
    )
""".format(condition=check_in("condition", ASSET_CONDITIONS),
           status=check_in("status", ASSET_STATUSES))


ALLOCATION_DDL = """
    CREATE TABLE IF NOT EXISTS asset_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id INTEGER NOT NULL,
        department_id INTEGER NOT NULL,
        department_name TEXT NOT NULL DEFAULT '',
        custodian_user_id INTEGER,
        custodian_name TEXT NOT NULL DEFAULT '',
        allocation_date TEXT NOT NULL,
        expected_return TEXT,
        returned_at TEXT,
        remarks TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Active' {status},
        allocated_by INTEGER,
        allocated_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((expected_return IS NULL)
               OR expected_return >= allocation_date),
        FOREIGN KEY (asset_id) REFERENCES assets(id)
    )
""".format(status=check_in("status", ALLOCATION_STATUSES))


ASSET_DDL_STATEMENTS = (CATEGORY_DDL, ASSET_DDL, ALLOCATION_DDL)


INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_asset_category"
    " ON asset_categories(parent_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_asset_status"
    " ON assets(status, department_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_category_id"
    " ON assets(category_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_department"
    " ON assets(department_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_custodian"
    " ON assets(custodian_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_allocation_asset"
    " ON asset_allocations(asset_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_asset_allocation_department"
    " ON asset_allocations(department_id, status)",
)


def ensure_asset_schema():
    """Create or top up every asset table. Safe to call on every boot."""
    db = connect_db()
    try:
        for statement in ASSET_DDL_STATEMENTS:
            db.execute(statement)
        asset_columns = {
            row[1] for row in db.execute("PRAGMA table_info(assets)").fetchall()}
        if "procurement_order_id" not in asset_columns:
            # The order lives in finance.db, so asset.db keeps its id without a
            # foreign key; the register checks it against the order desk.
            db.execute("ALTER TABLE assets ADD COLUMN procurement_order_id INTEGER")
        for statement in INDEX_STATEMENTS:
            db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------- codes
ASSET_CODE_PREFIX = "AST-"
ASSET_CODE_WIDTH = 5


def next_asset_code(db):
    """AST-00000 for the first unit, then one past the highest issued number.

    The register issues the tag rather than the form, so two units bought in the
    same week cannot carry a code the storekeeper is already using. A code typed
    by hand is still accepted; the sequence only counts AST numbers.
    """
    serial = -1
    for row in db.execute(
            "SELECT asset_code FROM assets WHERE asset_code LIKE ?",
            (ASSET_CODE_PREFIX + "%",)).fetchall():
        tail = (row["asset_code"] or "")[len(ASSET_CODE_PREFIX):]
        if tail.isdigit():
            serial = max(serial, int(tail))
    serial += 1
    candidate = "{}{:0{w}d}".format(ASSET_CODE_PREFIX, serial,
                                    w=ASSET_CODE_WIDTH)
    while db.execute("SELECT 1 FROM assets WHERE asset_code = ?",
                     (candidate,)).fetchone():
        serial += 1
        candidate = "{}{:0{w}d}".format(ASSET_CODE_PREFIX, serial,
                                        w=ASSET_CODE_WIDTH)
    return candidate


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ references
def clean_id(value, field="Record"):
    """The one number a select box or a URL carries, or nothing at all."""
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError("Choose a valid {}.".format(field))
    return int(text)


def department_ref(db, department_id):
    """An active unit from the HR register, looked up by id."""
    if department_id is None:
        return None
    row = db.execute(
        "SELECT id, name, is_active FROM departments WHERE id = ?",
        (department_id,)).fetchone()
    if row is None:
        raise ValueError("Choose a valid department.")
    if not row["is_active"]:
        raise ValueError("That department is no longer active.")
    return row


def account_ref(db, user_id, field="custodian"):
    """An active account from the user master data, looked up by id."""
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


def category_ref(db, category_id, require_active=True):
    """One asset category, optionally only while it is open for new units."""
    if category_id is None:
        raise ValueError("Asset category is required.")
    row = db.execute(
        "SELECT id, name, code, status FROM asset_categories WHERE id = ?",
        (category_id,)).fetchone()
    if row is None:
        raise ValueError("Choose a valid asset category.")
    if require_active and row["status"] != "Active":
        raise ValueError("That asset category is no longer active.")
    return row


# ------------------------------------------------------------------ validators
def category_values(form, db, exclude_id=None):
    """Every column a category form may set, validated and ready to write."""
    name = clean_text(form.get("name"), "Category name", 120, required=True)
    code = clean_text(form.get("code"), "Category code", 50,
                      required=True).upper()
    description = clean_text(form.get("description"), "Description", 500)
    parent_id = clean_id(form.get("parent_id"), "parent category")

    clash = db.execute(
        "SELECT name, code FROM asset_categories"
        " WHERE (lower(name) = lower(?) OR code = ?) AND id <> ?",
        (name, code, exclude_id or 0)).fetchone()
    if clash is not None:
        raise ValueError("Category name or code already exists.")

    parent = None
    if parent_id is not None:
        parent = db.execute(
            "SELECT id, name, parent_id, status FROM asset_categories"
            " WHERE id = ?", (parent_id,)).fetchone()
        if parent is None or parent["parent_id"] is not None:
            raise ValueError("Choose a top-level category as the parent.")
        if parent["status"] != "Active":
            raise ValueError("Activate the parent category first.")
        if exclude_id is not None:
            if parent["id"] == exclude_id:
                raise ValueError("A category cannot be its own parent.")
            children = db.execute(
                "SELECT 1 FROM asset_categories WHERE parent_id = ? LIMIT 1",
                (exclude_id,)).fetchone()
            if children is not None:
                raise ValueError("A category with subcategories cannot become"
                                 " one.")

    return {
        "name": name,
        "code": code,
        "description": description,
        "parent_id": parent["id"] if parent else None,
    }


def asset_values(form, db, exclude_id=None):
    """Every column an asset form may set, validated and ready to write.

    The code is issued by the register when the form leaves it blank and refused
    when it brings one another unit already carries, case aside.
    """
    name = clean_text(form.get("name"), "Asset name", 180, required=True)
    code = clean_text(form.get("asset_code"), "Asset code", 50).upper()
    if code:
        clash = db.execute(
            "SELECT 1 FROM assets WHERE asset_code = ? AND id <> ?",
            (code, exclude_id or 0)).fetchone()
        if clash is not None:
            raise ValueError("Asset code {} is already registered.".format(code))
    elif exclude_id is None:
        code = next_asset_code(db)

    category = category_ref(db, clean_id(form.get("category_id"),
                                         "asset category"))
    purchase_date = clean_date(form.get("purchase_date"), "Purchase date")
    warranty_expiry = clean_date(form.get("warranty_expiry"),
                                 "Warranty expiry")
    if purchase_date and warranty_expiry and warranty_expiry < purchase_date:
        raise ValueError("Warranty expiry cannot be before the purchase date.")

    return {
        "asset_code": code,
        "name": name,
        "description": clean_text(form.get("description"), "Description", 2000),
        "category_id": category["id"],
        "serial_number": clean_text(form.get("serial_number"),
                                    "Serial number", 100),
        "model_number": clean_text(form.get("model_number"), "Model", 100),
        "manufacturer": clean_text(form.get("manufacturer"), "Manufacturer", 120),
        "purchase_date": purchase_date,
        "purchase_cost": float(clean_amount(form.get("purchase_cost"),
                                            "Purchase cost")),
        "warranty_expiry": warranty_expiry,
        "supplier": clean_text(form.get("supplier"), "Supplier", 150),
        "purchase_order_number": clean_text(form.get("purchase_order_number"),
                                            "Purchase order", 100),
        "funding_source": clean_text(form.get("funding_source"),
                                     "Funding source", 100),
        "location": clean_text(form.get("location"), "Location", 200),
        "condition": clean_choice(form.get("condition"), "condition",
                                  ASSET_CONDITIONS, default="Good"),
        "notes": clean_text(form.get("notes"), "Notes", 2000),
    }


def allocation_values(form, db):
    """The asset, department and custodian an allocation form names."""
    asset_id = clean_id(form.get("asset_id"), "available asset")
    if asset_id is None:
        raise ValueError("Choose an available asset.")
    asset = db.execute(
        "SELECT id, asset_code, name, status FROM assets WHERE id = ?",
        (asset_id,)).fetchone()
    if asset is None:
        raise ValueError("Choose an available asset.")
    if asset["status"] != "Available":
        raise ValueError("Only an available asset can be allocated.")

    department = department_ref(db, clean_id(form.get("department_id"),
                                             "department"))
    if department is None:
        raise ValueError("Choose a department.")

    custodian = account_ref(db, clean_id(form.get("custodian_user_id"),
                                         "custodian"), "custodian")

    allocation_date = date.today().isoformat()
    expected_return = clean_date(form.get("expected_return"),
                                 "Expected return date")
    if expected_return and expected_return < allocation_date:
        raise ValueError("Expected return cannot be before the allocation"
                         " date.")

    return {
        "asset": asset,
        "department": department,
        "custodian": custodian,
        "allocation_date": allocation_date,
        "expected_return": expected_return,
        "remarks": clean_text(form.get("remarks"), "Remarks", 500),
    }


# --------------------------------------------------------------------- reads
ASSET_SELECT = """
    SELECT a.*, c.name AS category_name, c.code AS category_code
    FROM assets AS a
    LEFT JOIN asset_categories AS c ON c.id = a.category_id
"""


def asset_rows(db, status=None, category_id=None, department_id=None,
               search=None, include_inactive=False, limit=None):
    clauses, params = [], []
    if not include_inactive:
        clauses.append("a.status <> 'Inactive'")
    if status:
        clauses.append("a.status = ?")
        params.append(status)
    if category_id:
        clauses.append("a.category_id = ?")
        params.append(category_id)
    if department_id:
        clauses.append("a.department_id = ?")
        params.append(department_id)
    if search:
        clauses.append("(a.asset_code LIKE ? OR a.name LIKE ?"
                       " OR a.serial_number LIKE ? OR a.model_number LIKE ?"
                       " OR a.manufacturer LIKE ? OR a.location LIKE ?"
                       " OR a.department_name LIKE ?)")
        term = "%{}%".format(search)
        params.extend([term] * 7)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = ASSET_SELECT + where + (
        " ORDER BY CASE a.status WHEN 'Available' THEN 0"
        " WHEN 'Assigned' THEN 1 WHEN 'Maintenance' THEN 2 ELSE 3 END,"
        " a.name COLLATE NOCASE, a.id DESC")
    if limit:
        sql += " LIMIT {}".format(int(limit))
    return db.execute(sql, params).fetchall()


def asset_row(db, asset_id):
    return db.execute(ASSET_SELECT + " WHERE a.id = ?",
                      (asset_id,)).fetchone()


def category_rows(db, status=None, search=None):
    clauses, params = [], []
    if status:
        clauses.append("c.status = ?")
        params.append(status)
    if search:
        clauses.append("(c.name LIKE ? OR c.code LIKE ?)")
        term = "%{}%".format(search)
        params.extend([term, term])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return db.execute("""
        SELECT c.*, p.name AS parent_name,
               (SELECT COUNT(*) FROM asset_categories AS child
                 WHERE child.parent_id = c.id) AS child_count,
               (SELECT COUNT(*) FROM assets AS a
                 WHERE a.category_id = c.id) AS asset_count
        FROM asset_categories AS c
        LEFT JOIN asset_categories AS p ON p.id = c.parent_id
        {} ORDER BY c.name COLLATE NOCASE
    """.format(where), params).fetchall()


def category_row(db, category_id):
    return db.execute(
        "SELECT * FROM asset_categories WHERE id = ?",
        (category_id,)).fetchone()


def category_options(db, exclude_id=None, include_inactive=False):
    """The categories a picker may name, parents first."""
    clauses, params = [], []
    if not include_inactive:
        clauses.append("status = 'Active'")
    if exclude_id is not None:
        clauses.append("id <> ?")
        params.append(exclude_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return db.execute(
        "SELECT id, name, code, status FROM asset_categories {}"
        " ORDER BY CASE WHEN parent_id IS NULL THEN 0 ELSE 1 END,"
        " name COLLATE NOCASE".format(where), params).fetchall()


def department_options(db):
    """The active units an allocation may name, from the HR register."""
    try:
        return db.execute(
            "SELECT id, name FROM departments WHERE is_active = 1"
            " ORDER BY name COLLATE NOCASE").fetchall()
    except sqlite3.Error:
        # A database that has not grown the departments table yet answers with
        # an empty picker rather than failing the whole form.
        return []


def assignable_accounts(db):
    """The active accounts an asset may be handed to."""
    return db.execute("""
        SELECT id, username, full_name, role FROM users
        WHERE status = 'Active'
        ORDER BY full_name COLLATE NOCASE, username
    """).fetchall()


def allocation_rows(db, asset_id=None, department_id=None, status=None,
                    limit=None):
    clauses, params = [], []
    if asset_id is not None:
        clauses.append("al.asset_id = ?")
        params.append(asset_id)
    if department_id is not None:
        clauses.append("al.department_id = ?")
        params.append(department_id)
    if status:
        clauses.append("al.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = """
        SELECT al.*, a.asset_code, a.name AS asset_name,
               a.status AS asset_status
        FROM asset_allocations AS al
        JOIN assets AS a ON a.id = al.asset_id
        {} ORDER BY al.id DESC
    """.format(where)
    if limit:
        sql += " LIMIT {}".format(int(limit))
    return db.execute(sql, params).fetchall()


def allocation_row(db, allocation_id):
    return db.execute(
        "SELECT al.*, a.asset_code, a.name AS asset_name"
        " FROM asset_allocations AS al"
        " JOIN assets AS a ON a.id = al.asset_id WHERE al.id = ?",
        (allocation_id,)).fetchone()


def asset_summary(db):
    """The figures the dashboard and the register header count."""
    counts = {status: 0 for status in ASSET_STATUSES}
    value = 0.0
    for row in db.execute(
            "SELECT status, COUNT(*) AS total,"
            " COALESCE(SUM(purchase_cost), 0) AS value"
            " FROM assets GROUP BY status").fetchall():
        counts[row["status"]] = row["total"]
        # A deactivated unit keeps its history but no longer sits on the live
        # register, so it does not inflate the figure the desk carries.
        if row["status"] in LIVE_ASSET_STATUSES:
            value += float(row["value"] or 0)
    categories = db.execute(
        "SELECT COUNT(*) FROM asset_categories WHERE status = 'Active'"
    ).fetchone()[0]
    departments = db.execute(
        "SELECT COUNT(DISTINCT department_id) FROM assets"
        " WHERE department_id IS NOT NULL AND status <> 'Inactive'"
    ).fetchone()[0]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "live": sum(counts[status] for status in LIVE_ASSET_STATUSES),
        "value": value,
        "categories": categories,
        "departments": departments,
        "inactive": counts["Inactive"],
    }


def status_breakdown(db):
    return db.execute("""
        SELECT status, COUNT(*) AS total,
               COALESCE(SUM(purchase_cost), 0) AS value
        FROM assets GROUP BY status
        ORDER BY CASE status WHEN 'Available' THEN 0
          WHEN 'Assigned' THEN 1 WHEN 'Maintenance' THEN 2 ELSE 3 END
    """).fetchall()


def category_breakdown(db, limit=None):
    sql = """
        SELECT c.id, c.name, c.code,
               COUNT(a.id) AS total,
               COALESCE(SUM(a.purchase_cost), 0) AS value
        FROM asset_categories AS c
        LEFT JOIN assets AS a
          ON a.category_id = c.id AND a.status <> 'Inactive'
        WHERE c.status = 'Active'
        GROUP BY c.id
        ORDER BY total DESC, c.name COLLATE NOCASE
    """
    if limit:
        sql += " LIMIT {}".format(int(limit))
    return db.execute(sql).fetchall()


def department_summary(db):
    """How many units each unit holds, under the name HR uses today."""
    return db.execute("""
        SELECT a.department_id,
               COALESCE(d.name, a.department_name) AS department_name,
               COUNT(*) AS total,
               COALESCE(SUM(a.purchase_cost), 0) AS value,
               SUM(CASE WHEN a.status = 'Assigned' THEN 1 ELSE 0 END)
                 AS assigned
        FROM assets AS a
        LEFT JOIN departments AS d ON d.id = a.department_id
        WHERE a.department_id IS NOT NULL AND a.status <> 'Inactive'
        GROUP BY a.department_id, COALESCE(d.name, a.department_name)
        ORDER BY total DESC, department_name COLLATE NOCASE
    """).fetchall()


# --------------------------------------------------------------------- writes
def insert_category(db, values, user):
    result = db.execute("""
        INSERT INTO asset_categories
            (name, code, description, parent_id, status,
             created_by, created_by_name, updated_by, updated_by_name)
        VALUES (?, ?, ?, ?, 'Active', ?, ?, ?, ?)
    """, (values["name"], values["code"], values["description"],
          values["parent_id"], user["id"], user["full_name"],
          user["id"], user["full_name"]))
    return result.lastrowid


def update_category(db, category_id, values, user):
    db.execute("""
        UPDATE asset_categories
        SET name = ?, code = ?, description = ?, parent_id = ?,
            updated_at = ?, updated_by = ?, updated_by_name = ?
        WHERE id = ?
    """, (values["name"], values["code"], values["description"],
          values["parent_id"], _now(), user["id"], user["full_name"],
          category_id))


def set_category_status(db, category_id, status, user):
    db.execute("""
        UPDATE asset_categories
        SET status = ?, updated_at = ?, updated_by = ?, updated_by_name = ?
        WHERE id = ?
    """, (status, _now(), user["id"], user["full_name"], category_id))


def insert_asset(db, values, user):
    columns = list(values.keys()) + [
        "status", "created_by", "created_by_name",
        "updated_by", "updated_by_name"]
    params = [values[name] for name in values] + [
        "Available", user["id"], user["full_name"],
        user["id"], user["full_name"]]
    result = db.execute(
        "INSERT INTO assets ({}) VALUES ({})".format(
            ", ".join(columns), ", ".join("?" for _ in columns)), params)
    return result.lastrowid


def update_asset(db, asset_id, values, user):
    assignments = ", ".join("{} = ?".format(column) for column in values)
    params = [values[column] for column in values]
    db.execute("""
        UPDATE assets SET {}, updated_at = ?, updated_by = ?,
            updated_by_name = ? WHERE id = ?
    """.format(assignments), params + [_now(), user["id"],
                                       user["full_name"], asset_id])


def set_asset_status(db, asset_id, status, user):
    db.execute("""
        UPDATE assets SET status = ?, updated_at = ?, updated_by = ?,
            updated_by_name = ? WHERE id = ?
    """, (status, _now(), user["id"], user["full_name"], asset_id))


def allocate_asset(db, values, user):
    """Open a department allocation and move the unit to it in one write."""
    asset = values["asset"]
    department = values["department"]
    custodian = values["custodian"]
    result = db.execute("""
        INSERT INTO asset_allocations
            (asset_id, department_id, department_name,
             custodian_user_id, custodian_name, allocation_date,
             expected_return, remarks, status,
             allocated_by, allocated_by_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)
    """, (asset["id"], department["id"], department["name"],
          custodian["id"] if custodian else None,
          custodian["full_name"] if custodian else "",
          values["allocation_date"], values["expected_return"],
          values["remarks"], user["id"], user["full_name"]))
    db.execute("""
        UPDATE assets SET department_id = ?, department_name = ?,
            custodian_user_id = ?, custodian_name = ?, status = 'Assigned',
            updated_at = ?, updated_by = ?, updated_by_name = ?
        WHERE id = ?
    """, (department["id"], department["name"],
          custodian["id"] if custodian else None,
          custodian["full_name"] if custodian else "",
          _now(), user["id"], user["full_name"], asset["id"]))
    return result.lastrowid


def return_allocation(db, allocation_id, user):
    """Close one allocation and put the unit back on the shelf."""
    allocation = allocation_row(db, allocation_id)
    if allocation is None:
        return None
    if allocation["status"] != "Active":
        raise ValueError("That allocation has already been returned.")
    db.execute("""
        UPDATE asset_allocations SET status = 'Returned', returned_at = ?
        WHERE id = ?
    """, (_now(), allocation_id))
    db.execute("""
        UPDATE assets SET department_id = NULL, department_name = '',
            custodian_user_id = NULL, custodian_name = '',
            status = 'Available', updated_at = ?, updated_by = ?,
            updated_by_name = ? WHERE id = ?
    """, (_now(), user["id"], user["full_name"], allocation["asset_id"]))
    return allocation["asset_id"]
