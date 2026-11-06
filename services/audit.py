"""Activity log, auditor table catalogue and auditor access helpers."""

from core.access import current_user
from core.access import perm
from core.config import DATE_PATTERN
from core.db import connect_db
from core.db import get_db
from core.formatting import money_etb
from database import database_config
from datetime import datetime
from datetime import timezone
from flask import abort
from flask import current_app
from flask import flash
from flask import g
from flask import has_app_context
from flask import has_request_context
from flask import request
from flask import url_for
import json
import sqlite3


# Columns added to audit_logs after the first release. They are what turns a
# line of text into a record an auditor can work from: who exactly, in which
# department, from what kind of device, and what a field held before an edit.
AUDIT_DETAIL_COLUMNS = (
    ("full_name", "TEXT"),
    ("department", "TEXT"),
    ("device_type", "TEXT"),
    ("os", "TEXT"),
    ("browser", "TEXT"),
    ("user_agent", "TEXT"),
    ("previous_json", "TEXT"),
    ("updated_json", "TEXT"),
)


# A details key containing any of these is never written to the log, whatever
# its value looks like; the credential columns a record may carry are covered
# by the auditor browser's own filter as well.
AUDIT_SECRET_WORDS = ("password", "passwd", "token", "secret", "hash", "cookie",
                      "csrf", "api_key", "apikey", "authorization", "credential")


def audit_department(db, user):
    """The name of the department the actor sits in, or "" when it is not set."""
    department_id = user["department_id"] if "department_id" in user.keys() else None
    if department_id:
        row = db.execute("SELECT name FROM departments WHERE id = ?",
                         (department_id,)).fetchone()
        if row is not None and row["name"]:
            return row["name"]
    employee_id = user["employee_id"] if "employee_id" in user.keys() else None
    if employee_id:
        row = db.execute("SELECT department FROM employees WHERE id = ?",
                         (employee_id,)).fetchone()
        if row is not None and row["department"]:
            return row["department"]
    return ""


def audit_actor():
    """(user_id, username, full_name, role, department) for the signed-in account.

    Read once per request: a single action can write several rows and they all
    describe the same person.
    """
    if has_app_context():
        cached = g.get("_audit_profile")
        if cached is not None:
            return cached
    user = current_user()
    if user is None:
        return None, "Anonymous", "Anonymous", "anonymous", ""
    full_name = user["full_name"]
    username = (user["username"] if "username" in user.keys() else None) or full_name
    try:
        department = audit_department(get_db(), user)
    except Exception:
        department = ""
    profile = (user["id"], username, full_name, user["role"], department)
    if has_app_context():
        g._audit_profile = profile
    return profile


def audit_device(user_agent):
    """What a User-Agent string reliably says: device class, OS and browser.

    Anything the header does not state is left blank rather than guessed - a
    browser can always hide itself, so a blank is the truth and a guess is not.
    """
    text = (user_agent or "").strip()
    if not text:
        return "", "", ""
    lowered = text.lower()
    if ("ipad" in lowered or "tablet" in lowered or "kindle" in lowered
            or "silk/" in lowered
            or ("android" in lowered and "mobile" not in lowered)):
        device_type = "Tablet"
    elif ("mobi" in lowered or "iphone" in lowered or "ipod" in lowered
          or "android" in lowered or "windows phone" in lowered):
        device_type = "Mobile"
    elif ("windows" in lowered or "macintosh" in lowered or "mac os x" in lowered
          or "linux" in lowered or "cros" in lowered or "x11" in lowered):
        device_type = "Desktop"
    else:
        device_type = "Other"

    operating_system = ""
    for marker, label in (
        ("windows phone", "Windows Phone"),
        ("windows nt 10", "Windows 10/11"),
        ("windows nt 6.3", "Windows 8.1"),
        ("windows nt 6.1", "Windows 7"),
        ("windows", "Windows"),
        ("android", "Android"),
        ("iphone", "iOS"),
        ("ipad", "iPadOS"),
        ("ipod", "iOS"),
        ("cros", "ChromeOS"),
        ("mac os x", "macOS"),
        ("macintosh", "macOS"),
        ("linux", "Linux"),
    ):
        if marker in lowered:
            operating_system = label
            break

    browser = ""
    for marker, label in (
        ("edg/", "Edge"), ("edga/", "Edge"), ("edgios", "Edge"),
        ("opr/", "Opera"), ("opera mini", "Opera"), ("opera", "Opera"),
        ("samsungbrowser", "Samsung Internet"),
        ("ucbrowser", "UC Browser"),
        ("firefox/", "Firefox"), ("fxios", "Firefox"),
        ("chrome/", "Chrome"), ("crios", "Chrome"),
        ("safari/", "Safari"),
    ):
        if marker in lowered:
            browser = label
            break
    return device_type, operating_system, browser


def scrub_audit(value):
    """Details with every credential-shaped key replaced before it is stored."""
    if isinstance(value, dict):
        return {key: ("[redacted]"
                      if any(word in str(key).lower() for word in AUDIT_SECRET_WORDS)
                      else scrub_audit(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_audit(item) for item in value]
    return value


def _audit_value(value):
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


def change_set(row, values):
    """(previous, updated) holding only the fields an edit really changed."""
    previous, updated = {}, {}
    for key, value in values.items():
        try:
            old = row[key]
        except (IndexError, KeyError):
            continue
        if old != value:
            previous[key] = _audit_value(old)
            updated[key] = _audit_value(value)
    return previous, updated


def ensure_audit_schema():
    """Add the detail columns, their indexes and the append-only guard.

    audit_logs is a record, not a table to be edited: the triggers refuse an
    UPDATE or DELETE from the application connection, so only whoever holds the
    database file itself can rewrite history.
    """
    db = connect_db()
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(audit_logs)")}
        if not columns:
            return
        for name, kind in AUDIT_DETAIL_COLUMNS:
            if name not in columns:
                db.execute("ALTER TABLE audit_logs ADD COLUMN {} {}".format(name, kind))
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_department"
                   " ON audit_logs(department)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_device"
                   " ON audit_logs(device_type)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_ip"
                   " ON audit_logs(ip_address)")
        db.executescript("""
            CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'audit_logs is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'audit_logs is append-only');
            END;
        """)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def log_activity(action, module, description, outcome="SUCCESS",
                 record_type=None, record_id=None, details=None,
                 previous=None, updated=None, username=None, actor=None):
    # Never pass passwords, hashes, session cookies or tokens in details; the
    # scrubber blanks any that slip through. `actor` and `username` name the
    # account a request is about when nobody is signed in yet, as on a failed
    # sign-in; the session always wins when it has an account.
    try:
        db = get_db()
        user_id, actor_username, full_name, role, department = audit_actor()
        if user_id is None and actor is not None:
            user_id = actor["id"]
            actor_username = actor["username"] or actor["full_name"]
            full_name = actor["full_name"]
            role = actor["role"]
            try:
                department = audit_department(db, actor)
            except Exception:
                department = ""
        elif user_id is None and username:
            actor_username = str(username)[:120]
        device_type, operating_system, browser = "", "", ""
        user_agent = ""
        ip_address = None
        if has_request_context():
            user_agent = (request.headers.get("User-Agent") or "")[:400]
            device_type, operating_system, browser = audit_device(user_agent)
            # The TCP peer, not a header a client can type; a proxy in front of
            # the app would have to be trusted explicitly for anything else.
            ip_address = request.remote_addr
        db.execute("""
            INSERT INTO audit_logs (
                occurred_at, user_id, username, full_name, role, department,
                action, module, description, record_type, record_id, ip_address,
                device_type, os, browser, user_agent, outcome, details_json,
                previous_json, updated_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            str(user_id) if user_id is not None else None,
            actor_username,
            full_name,
            role,
            department,
            str(action)[:100],
            str(module)[:100],
            str(description)[:1000],
            str(record_type)[:100] if record_type else None,
            str(record_id)[:200] if record_id is not None else None,
            ip_address,
            device_type,
            operating_system,
            browser,
            user_agent,
            outcome if outcome in ("SUCCESS", "FAILURE", "DENIED") else "SUCCESS",
            json.dumps(scrub_audit(details or {}), ensure_ascii=False, default=str),
            json.dumps(scrub_audit(previous or {}), ensure_ascii=False,
                       default=str) if previous else None,
            json.dumps(scrub_audit(updated or {}), ensure_ascii=False,
                       default=str) if updated else None,
        ))
        db.commit()
    except Exception:
        pass


def refused(wanted_roles, wanted_modules, user, view):
    """Record an access refusal before the 403 is raised."""
    scope = ", ".join(sorted(wanted_modules or wanted_roles)) or view.__name__
    log_activity("DENY", scope,
                 "Refused {} on {}".format(request.method, request.path),
                 outcome="DENIED",
                 details={"endpoint": request.endpoint, "view": view.__name__})


def audit_time(value):
    """Render an ISO audit stamp as a short local label."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return str(value)[:16].replace("T", " ")
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%d %b %H:%M")


def friendly_date(value):
    """Render a stored date as Jan 15, 2024."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return "{} {}, {}".format(moment.strftime("%b"), moment.day, moment.year)


def init_auditor_tables():
    db = connect_db()
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS audit_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                department TEXT NOT NULL CHECK (
                    department IN (
                        'Finance', 'Budget', 'Inventory',
                        'Procurement', 'HR', 'Payroll'
                    )
                ),
                review_period_start TEXT,
                review_period_end TEXT,
                status TEXT NOT NULL DEFAULT 'Open'
                    CHECK (status IN (
                        'Open', 'In Progress', 'Completed'
                    )),
                summary TEXT DEFAULT '',
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'Medium'
                    CHECK (risk_level IN (
                        'Low', 'Medium', 'High', 'Critical'
                    )),
                source_table TEXT DEFAULT '',
                source_record_id INTEGER,
                recommendation TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Open'
                    CHECK (status IN (
                        'Open', 'Under Review', 'Resolved', 'Closed'
                    )),
                management_response TEXT DEFAULT '',
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (review_id)
                    REFERENCES audit_reviews(id),
                FOREIGN KEY (created_by)
                    REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                record_id INTEGER,
                details TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_audit_findings_review
                ON audit_findings(review_id);

            CREATE INDEX IF NOT EXISTS idx_audit_findings_risk
                ON audit_findings(risk_level);
        """)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def auditor_required(view):
    """Read-only oversight: an administrator, or any account ticked for the audit
    module. The same guard the /admin/audit screens use, so nobody becomes an
    auditor by typing a URL."""
    return perm("audit")(view)


AUDITOR_RECORD_CAP = 200


AUDITOR_REPORT_CAP = 1000


AUDITOR_SECRET_COLUMN = ("password", "hash", "token", "secret")


AUDITOR_REVIEW_DEPARTMENTS = ("Finance", "Budget", "Inventory", "Procurement",
                              "HR", "Payroll")


AUDITOR_REVIEW_STATUS = ("Open", "In Progress", "Completed")


AUDITOR_FINDING_STATUS = ("Open", "Under Review", "Resolved", "Closed")


AUDITOR_RISK_LEVELS = ("Low", "Medium", "High", "Critical")


AUDITOR_RECORD_TABLES = (
    ("Finance", ("finance_transactions", "finance_invoices", "finance_payments")),
    ("Budget", ("budget_plans", "budget_requests", "budget_allocations",
                "budget_expenditures")),
    ("Inventory", ("inventory_items", "stock_movements")),
    ("Procurement", ("procurement_suppliers", "procurement_requests",
                     "procurement_request_items", "procurement_orders")),
    ("HR", ("employees", "attendance", "leave_requests", "job_vacancies",
            "job_applications", "performance_reviews", "employee_training")),
    ("Payroll", ("payroll",)),
    ("Oversight", ("approval_requests", "approval_actions", "audit_reviews",
                   "audit_findings", "audit_logs", "users")),
    ("Correspondence", ("letters",)),
    ("Marketing", ("marketing_plans", "marketing_campaigns", "marketing_leads",
                   "market_research", "digital_campaigns", "customer_feedback")),
    ("Projects", ("projects", "project_milestones", "project_tasks",
                  "project_members")),
)


AUDITOR_REPORTS = (
    {
        "key": "finance",
        "label": "Finance",
        "title": "Finance report",
        "subtitle": "Every money movement booked in the Finance module",
        "requires": "finance_transactions",
        "date_column": "transaction_date",
        "money": ("amount", "amount_etb"),
        "sql": ("SELECT id, transaction_date, transaction_type, category, party_name,"
                " payment_method, reference, currency, amount, amount_etb,"
                " updated_by_name, updated_at"
                " FROM finance.finance_transactions"),
        "columns": (("id", "Ref"), ("transaction_date", "Date"),
                    ("transaction_type", "Type"), ("category", "Category"),
                    ("party_name", "Counterparty"), ("payment_method", "Method"),
                    ("reference", "Reference"), ("currency", "Currency"),
                    ("amount", "Amount"), ("amount_etb", "Amount (ETB)"),
                    ("updated_by_name", "Last editor"),
                    ("updated_at", "Last modified")),
        "note": "Amounts are the figures as booked; Amount (ETB) is what the "
                "exchange rate at the time turned them into.",
    },
    {
        "key": "budget",
        "label": "Budget",
        "title": "Budget report",
        "subtitle": "Allocations, what has been drawn against them and what is left",
        "requires": "budget_allocations",
        "date_column": "created_at",
        "money": ("allocated_amount", "spent_amount", "remaining_amount"),
        "sql": ("SELECT a.id, a.allocation_name, a.department, p.plan_name,"
                " p.fiscal_year, a.currency, a.allocated_amount,"
                " (SELECT COALESCE(SUM(e.amount), 0) FROM budget_expenditures e"
                "  WHERE e.allocation_id = a.id AND e.currency = a.currency)"
                " AS spent_amount,"
                " a.allocated_amount - (SELECT COALESCE(SUM(e.amount), 0)"
                "  FROM budget_expenditures e"
                "  WHERE e.allocation_id = a.id AND e.currency = a.currency)"
                " AS remaining_amount, a.status, a.created_at,"
                " a.updated_by_name, a.updated_at"
                " FROM budget_allocations a"
                " LEFT JOIN budget_plans p ON p.id = a.plan_id"),
        "columns": (("id", "Ref"), ("allocation_name", "Allocation"),
                    ("department", "Department"), ("plan_name", "Plan"),
                    ("fiscal_year", "Year"), ("currency", "Currency"),
                    ("allocated_amount", "Allocated"), ("spent_amount", "Spent"),
                    ("remaining_amount", "Remaining"), ("status", "Status"),
                    ("created_at", "Opened"), ("updated_by_name", "Last editor"),
                    ("updated_at", "Last modified")),
        "note": "Spent is the sum of the expenditures recorded against the "
                "allocation, not a figure anyone maintains.",
    },
    {
        "key": "inventory",
        "label": "Inventory",
        "title": "Inventory report",
        "subtitle": "Stock on hand, its value and the lines below their minimum",
        "requires": "inventory_items",
        "date_column": "created_at",
        "money": ("unit_cost", "stock_value"),
        "sql": ("SELECT id, sku, name, category, unit, quantity, min_stock,"
                " unit_cost, quantity * unit_cost AS stock_value, location,"
                " supplier, active, created_at, updated_by_name, updated_at"
                " FROM inventory_items"),
        "columns": (("id", "Ref"), ("sku", "SKU"), ("name", "Item"),
                    ("category", "Category"), ("quantity", "On hand"),
                    ("unit", "Unit"), ("min_stock", "Minimum"),
                    ("unit_cost", "Unit cost"), ("stock_value", "Value"),
                    ("location", "Location"), ("active", "In use"),
                    ("updated_by_name", "Last editor"),
                    ("updated_at", "Last modified")),
        "note": "Value is quantity multiplied by unit cost as the item is "
                "recorded today.",
    },
    {
        "key": "procurement",
        "label": "Procurement Management",
        "title": "Procurement Management report",
        "subtitle": "Requisitions raised, what they cost and who signed them",
        "requires": "procurement_requests",
        "date_column": "recorded_on",
        "money": ("estimated_cost",),
        "sql": ("SELECT pr.id, pr.request_number, pr.department, pr.item_name,"
                " pr.quantity, pr.unit, pr.estimated_cost,"
                " s.name AS supplier_name, pr.requested_by_name, pr.status,"
                " COALESCE(pr.submitted_at, pr.created_at) AS recorded_on,"
                " pr.updated_by_name, pr.updated_at"
                " FROM finance.procurement_requests pr"
                " LEFT JOIN finance.procurement_suppliers s ON s.id = pr.supplier_id"),
        "columns": (("id", "Ref"), ("request_number", "Reference"),
                    ("department", "Department"), ("item_name", "Item"),
                    ("quantity", "Quantity"), ("unit", "Unit"),
                    ("estimated_cost", "Estimated (ETB)"),
                    ("supplier_name", "Supplier"),
                    ("requested_by_name", "Raised by"), ("status", "Status"),
                    ("recorded_on", "Date"), ("updated_by_name", "Last editor"),
                    ("updated_at", "Last modified")),
        "note": "The date shown is when the requisition was submitted; a draft "
                "that was never sent shows the day it was opened.",
    },
    {
        "key": "hr",
        "label": "HR",
        "title": "HR report",
        "subtitle": "The staff register as HR holds it",
        "requires": "employees",
        "date_column": "hire_date",
        "money": (),
        "sql": ("SELECT id, employee_code, full_name, department, job_title,"
                " hire_date, employment_status, updated_by_name, updated_at"
                " FROM employees"),
        "columns": (("id", "Ref"), ("employee_code", "Code"),
                    ("full_name", "Employee"), ("department", "Department"),
                    ("job_title", "Role"), ("hire_date", "Hired"),
                    ("employment_status", "Status"),
                    ("updated_by_name", "Last editor"),
                    ("updated_at", "Last modified")),
        "note": "Contact details and pay are deliberately not in this report - "
                "the record browser is where a named file is opened.",
    },
    {
        "key": "payroll",
        "label": "Payroll",
        "title": "Payroll report",
        "subtitle": "Each pay run recorded, its net figure and payment status",
        "requires": "payroll",
        "date_column": "created_at",
        "money": ("basic_salary", "allowances", "deductions", "net_salary"),
        "sql": ("SELECT p.id, e.full_name, e.employee_code, p.payroll_month,"
                " p.basic_salary, p.allowances, p.deductions, p.net_salary,"
                " p.payment_status, p.created_at"
                " FROM payroll p LEFT JOIN employees e ON e.id = p.employee_id"),
        "columns": (("id", "Ref"), ("full_name", "Employee"),
                    ("employee_code", "Code"), ("payroll_month", "Month"),
                    ("basic_salary", "Basic"), ("allowances", "Allowances"),
                    ("deductions", "Deductions"), ("net_salary", "Net"),
                    ("payment_status", "Payment"), ("created_at", "Recorded")),
        "note": "Figures are in ETB.",
    },
    {
        "key": "audit",
        "label": "Audit Findings",
        "title": "Audit findings",
        "subtitle": "Findings raised, their risk and how far the response has got",
        "requires": "audit_findings",
        "date_column": "created_at",
        "money": (),
        "sql": ("SELECT f.id, r.title AS review_title, r.department, f.title,"
                " f.risk_level, f.status, f.recommendation,"
                " f.management_response, u.full_name AS raised_by, f.created_at,"
                " f.updated_by_name, f.updated_at"
                " FROM audit_findings f"
                " LEFT JOIN audit_reviews r ON r.id = f.review_id"
                " LEFT JOIN users u ON u.id = f.created_by"),
        "columns": (("id", "Ref"), ("review_title", "Review"),
                    ("department", "Department"), ("title", "Finding"),
                    ("risk_level", "Risk"), ("status", "Status"),
                    ("recommendation", "Recommendation"),
                    ("management_response", "Management response"),
                    ("raised_by", "Raised by"), ("created_at", "Raised on"),
                    ("updated_by_name", "Last editor"),
                    ("updated_at", "Last modified")),
        "note": "A finding is written by the auditor alone; the recommendation "
                "and response columns are text the department supplies.",
    },
)


def auditor_table_names(db):
    """Every table an auditor may open, read across all module files."""
    return database_config.all_table_names()


def auditor_records_url(table, **kwargs):
    return url_for("auditor_records", table_name=table, **kwargs)


def auditor_record_groups(db, present):
    """The departments and the live tables an auditor may open in each."""
    groups = []
    for department, tables in AUDITOR_RECORD_TABLES:
        rows = []
        for table in tables:
            if table not in present:
                continue
            count = database_config.table_count(table)
            rows.append({"table": table,
                         "label": table.replace("_", " ").title(),
                         "count": count,
                         "href": auditor_records_url(table)})
        if rows:
            groups.append({"department": department, "tables": rows})
    return groups


def auditor_visible_columns(db, table):
    """Every column except the ones a credential could ever live in.

    A bare PRAGMA reads the main file only, so the table's owning module has to
    name its schema first. The registry already knows who owns what, which is
    how one rule covers the finance file, the letters file and every desk added
    after them.
    """
    schema = database_config.TABLE_SCHEMA.get(table)
    if schema and schema != "main":
        pragma = "{}.table_info({})".format(schema, table)
    else:
        pragma = "table_info({})".format(table)
    columns = [row[1] for row in db.execute("PRAGMA " + pragma)]
    return [name for name in columns
            if not any(word in name.lower() for word in AUDITOR_SECRET_COLUMN)]


def auditor_date_range():
    """The date window from the query string, validated before it is used."""
    start = (request.args.get("start_date") or "").strip()
    end = (request.args.get("end_date") or "").strip()
    for value in (start, end):
        if value and not DATE_PATTERN.fullmatch(value):
            abort(400, "Invalid date. Use YYYY-MM-DD.")
    if start and end and start > end:
        flash("The start date cannot be after the end date.", "error")
        start = end = ""
    return start, end


def auditor_report(key):
    for spec in AUDITOR_REPORTS:
        if spec["key"] == key:
            return spec
    return None


def auditor_report_tabs(db, present):
    """Each tab, whether its data exists yet, and how much of it there is."""
    tabs = []
    for spec in AUDITOR_REPORTS:
        available = spec["requires"] in present
        count = 0
        if available:
            try:
                count = db.execute(
                    "SELECT COUNT(*) FROM (%s)" % spec["sql"]).fetchone()[0]
            except sqlite3.Error:
                available = False
        tabs.append({"key": spec["key"], "label": spec["label"],
                     "available": available, "count": count})
    return tabs


def auditor_stamp_tabs(tabs, start="", end=""):
    """Give each tab the link the screen renders, date window and all."""
    for tab in tabs:
        tab["href"] = url_for("auditor_reports", report=tab["key"],
                              start_date=start or None, end_date=end or None)
    return tabs


def auditor_report_rows(db, spec, start, end, cap=None):
    """Run one whitelisted report, newest first, inside the date window."""
    column = spec["date_column"]
    conditions, params = [], []
    if start:
        conditions.append("d.%s >= ?" % column)
        params.append(start)
    if end:
        conditions.append("d.%s <= ?" % column)
        params.append(end + "T23:59:59" if column.endswith("at") else end)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit = cap or AUDITOR_REPORT_CAP
    sql = "SELECT * FROM (%s) d%s ORDER BY d.%s DESC LIMIT ?" % (
        spec["sql"], where, column)
    try:
        return db.execute(sql, params + [limit]).fetchall()
    except sqlite3.Error:
        return []


def auditor_display(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return "{:,.2f}".format(value)
    return str(value)


def auditor_cell(row, key, money):
    """One table cell, formatted here so a screen never computes a figure in JS."""
    value = row[key]
    if value is None:
        return ""
    if key in money:
        try:
            return money_etb(value)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return "{:,.2f}".format(value)
    return str(value)


def csv_safe(value):
    """One cell as the spreadsheet file receives it, never as a formula.

    Every exported column can carry text some account typed - a party name, a
    note, a job title - and a reader who opens the file in Excel would run a
    value starting with =, +, @ or a tab as a formula on their own machine. A
    leading apostrophe keeps the cell as text. Figures are left alone, so a
    negative amount still arrives as a number.
    """
    text = auditor_display(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(text.replace(",", "").replace("%", ""))
        except ValueError:
            return "'" + text
    return text


def auditor_csv(columns, rows):
    """A complete copy of the table for the spreadsheet, byte for byte what the
    screen shows. Written here rather than in the browser so an export cannot be
    a partial page of what the server already had."""
    lines = [",".join('"%s"' % label.replace('"', '""')
                      for _key, label in columns)]
    for row in rows:
        lines.append(",".join(
            '"%s"' % csv_safe(row[key]).replace('"', '""')
            for key, _label in columns))
    # the byte order mark is what makes Excel open a UTF-8 file as UTF-8
    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")


def auditor_csv_response(name, columns, rows):
    return current_app.response_class(
        auditor_csv(columns, rows),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="auditor_%s.csv"' % name})


def auditor_review_or_404(db, review_id):
    row = db.execute("SELECT * FROM audit_reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        abort(404)
    return row
