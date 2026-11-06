"""Public pages, shared dashboard, notifications, search and HTTP hardening."""

from core.access import current_user
from core.access import account_role_keys
from core.access import dashboard_url
from core.access import effective_granted_permissions
from core.access import effective_permissions
from core.access import has_permission
from core.access import login_required
from core.access import role_catalogue
from core.access import role_name_for
from core.access import sidebar_groups
from core.access import user_extra_roles
from core.config import DASHBOARD_ROLES
from core.config import MANAGE_PREFIXES
from core.config import PROJECTS_PATH
from core.config import ROLE_LABEL
from core.config import STEP_LABEL
from core.config import ms_login_enabled
from core.config import print_department_name
from core.db import get_db
from core.db import qualified_table
from core.formatting import greeting
from core.formatting import money_etb
from core.formatting import month_short
from core.formatting import recent_month_keys
from core.formatting import today_name
from core.routing import get
from core.routing import post
from core.routing import route
from datetime import datetime
from datetime import timezone
from flask import abort
from flask import current_app
from flask import flash
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from markupsafe import Markup
from services.audit import audit_time
from services.audit import auditor_table_names
from services.audit import log_activity
from services.budget import budget_overview
from services.links import can_manage_approval_policy
from services.notifications import notification_summary
from services.notifications import sync_notifications
from services.org import organization_snapshot
from services.search import SEARCH_SCOPES
from services.search import search_href
from services.search import search_scope_where
from services.search import search_title
from services.workflow import approval_acted_ids
from services.workflow import can_decide_request
from services.workflow import can_view_request
import io
import json
import qrcode
import qrcode.image.svg
import reporting
import sqlite3


def print_qr(value):
    """A self-contained SVG QR for the printed reports."""
    image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage,
                        box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    # The XML declaration belongs in a standalone file, not inside HTML.
    return Markup(buffer.getvalue().decode("utf-8").split("?>", 1)[-1].strip())


def inject_user():
    try:
        db = get_db()
        user = current_user()
        if user is not None and user["status"] != "Active":
            user = None
        permissions = effective_permissions(user) if user else set()
        # What the account may actually use, not just what its matrices name: a
        # tick whose module is closed cannot drive a button either.
        granted_keys = ({key for key in effective_granted_permissions(user)
                         if has_permission(key, user)} if user else set())
        groups = sidebar_groups(user, permissions)
        # The bell follows the account across every page, public ones included.
        notes, note_count = notification_summary(db, user) if user else ([], 0)
        catalogue = role_catalogue(db)
        role_names = {row["key"]: row["name"] for row in catalogue}
        for key, label in ROLE_LABEL.items():
            role_names.setdefault(key, label)
        extra_roles = []
        if user is not None:
            extra_roles = [(key, role_names.get(key, key))
                           for key in user_extra_roles(db, user["id"])]
        print_department = print_department_name(
            user, role_names.get(user["role"]) if user else None)
        return {"user": user, "dashboard_home": dashboard_url(user["role"]) if user else None,
                "role_label": role_names.get(user["role"], "Account") if user else None,
                "extra_roles": extra_roles,
                "role_names": role_names,
                "can_manage_policy": can_manage_approval_policy(user),
                "permissions": permissions,
                "granted_permissions": granted_keys,
                "sidebar_groups": groups,
                "notifications": notes,
                "notification_count": note_count,
                "ms_login_enabled": ms_login_enabled(),
                "print_department": print_department,
                "print_date": datetime.now().strftime("%d %b %Y, %H:%M")}
    except sqlite3.Error:
        # The error page has to render even when the database is the thing that
        # failed, so the shell falls back to a signed-out view.
        return {"user": None, "dashboard_home": None, "role_label": None,
                "extra_roles": [], "role_names": dict(ROLE_LABEL),
                "can_manage_policy": False, "permissions": set(),
                "granted_permissions": set(),
                "sidebar_groups": (), "notifications": [],
                "notification_count": 0,
                "ms_login_enabled": ms_login_enabled(),
                "print_department": print_department_name(None),
                "print_date": datetime.now().strftime("%d %b %Y, %H:%M")}


def load_projects():
    return json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))


def save_projects(projects):
    PROJECTS_PATH.write_text(
        json.dumps(projects, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


PAGES = {
    "index": "index.html",
    "about": "about.html",
    "services": "services.html",
    "projects": "projects.html",
    "process": "process.html",
    "contact": "contact.html",
}


def page_route(endpoint):
    template = PAGES[endpoint]

    def view():
        if endpoint == "index":
            # The Home page carries the company structure, so it reads the same
            # snapshot the Organization page and the dashboards use.
            user = current_user()
            org = organization_snapshot(get_db(), user, public=user is None)
            return render_template(template, **org)
        return render_template(template)

    view.__name__ = endpoint
    return view


@get("/project.json")
def projects_api():
    return jsonify(load_projects())


MODULE_TILES = (
    ("hr", "HR Management", "Employee records, leave and attendance",
     "hr_dashboard", "user"),
    ("finance", "Finance Management", "Income, expenses and invoices",
     "finance.dashboard", "cash"),
    ("budget", "Budget Management", "Plans, allocations and spending",
     "budget.plan_list", "chart"),
    ("payroll", "Payroll Management", "Salary, deductions and benefits",
     "payroll_dashboard", "file"),
    ("inventory", "Inventory & Stock", "Items, stock levels and movements",
     "inventory.dashboard", "box"),
    ("procurement", "Procurement Management", "Suppliers, requests and purchase orders",
     "procurement.dashboard", "inbox"),
    ("accounts", "Administration", "Accounts, roles and access control",
     "admin_users", "lock"),
    ("integrations", "System Integration", "External systems, credentials and sync",
     "integrations.dashboard", "network"),
    ("reports", "Reports", "Department reports, exports and audit views",
     "reports_centre", "chart"),
)


QUICK_ACTIONS = (
    ("hr", "Manage Employees", "employees", "user"),
    ("approvals", "New Request", "approvals.new_request", "file"),
    ("finance", "Record Transaction", "finance.transactions", "cash"),
    ("budget", "Create Budget Plan", "budget.create_plan", "file"),
    ("budget", "Submit Budget Request", "budget.create_request", "chart"),
    ("budget", "Review Budget Requests", "budget.request_list", "check"),
    ("budget", "Record Expenditure", "budget.create_expenditure", "cash"),
    ("budget", "Allocations", "budget.allocations", "box"),
    ("budget", "Expenditure Register", "budget.expenditures", "inbox"),
    ("payroll", "Run Payroll", "payroll_dashboard", "calendar"),
    ("inventory", "Stock In / Out", "inventory.stock_movement", "box",
     "inventory_movements"),
    ("inventory", "Open Stocktake", "inventory.stocktakes", "clipboard",
     "inventory_stocktake"),
    ("inventory", "Reorder Alerts", "inventory.alerts", "box",
     "inventory_alerts"),
    ("inventory", "Stock Reports", "inventory.reports", "chart",
     "inventory_reports"),
    ("procurement", "Raise Purchase Request", "procurement.requisition_new", "inbox"),
    ("audit", "Activity Logs", "audit.audit_dashboard", "clock"),
    ("accounts", "User Management", "admin_users", "lock"),
    ("integrations", "Register A Connection", "integrations.add", "network"),
)


@route("/dashboard")
@login_required(*DASHBOARD_ROLES)
def dashboard():
    user = current_user()
    if user["role"] == "hr":
        # HR ran two dashboards with the same workforce figures. The module page
        # now carries that whole overview, so this address forwards to it.
        return redirect(url_for("hr_dashboard"))
    db = get_db()
    granted = effective_permissions(user)
    budget = budget_overview(db) if "budget" in granted else None
    months = recent_month_keys(6)
    since = months[0] + "-01"
    this_month = months[-1]

    # --- KPI tiles, one per module the account holds -------------------------
    tiles = []
    if granted & {"hr", "accounts"}:
        total = db.execute("SELECT COUNT(*) AS n FROM employees").fetchone()["n"]
        joined = db.execute(
            "SELECT COUNT(*) AS n FROM employees WHERE substr(hire_date, 1, 7) >= ?",
            (months[0],),
        ).fetchone()["n"]
        tiles.append({
            "label": "Total Employees", "value": total, "icon": "users",
            "note": "{} joined since {}".format(joined, month_short(months[0])),
            "tone": "up" if joined else "flat",
            "href": url_for("employees"),
        })
    if "finance" in granted:
        income = db.execute("""
            SELECT COALESCE(SUM(amount_etb), 0) AS n FROM finance.finance_transactions
            WHERE transaction_type = 'income' AND transaction_date >= ?
        """, (since,)).fetchone()["n"]
        spend = db.execute("""
            SELECT COALESCE(SUM(amount_etb), 0) AS n FROM finance.finance_transactions
            WHERE transaction_type = 'expense' AND transaction_date >= ?
        """, (since,)).fetchone()["n"]
        tiles.append({
            "label": "Income (ETB)", "value": money_etb(income), "icon": "cash",
            "note": "{} spent in the period".format(money_etb(spend)),
            "tone": "down" if float(spend) > float(income) else "up",
            "href": url_for("finance.transactions"),
        })
    if "approvals" in granted:
        queue = approvals_waiting_for(db, user)
        value = sum(float(row["amount"]) for row in queue)
        tiles.append({
            "label": "Pending Approvals", "value": len(queue), "icon": "check",
            "note": "{:,.2f} ETB awaiting a decision".format(value) if queue
                    else "Nothing awaits your decision",
            "tone": "down" if queue else "flat",
            "href": url_for("approvals.dashboard"),
        })
    if "inventory" in granted:
        low = db.execute("""
            SELECT COUNT(*) AS n FROM inventory_items
            WHERE active = 1 AND quantity <= min_stock
        """).fetchone()["n"]
        items = db.execute(
            "SELECT COUNT(*) AS n FROM inventory_items WHERE active = 1"
        ).fetchone()["n"]
        tiles.append({
            "label": "Low Stock Items", "value": low, "icon": "box",
            "note": "of {} stocked items".format(items),
            "tone": "down" if low else "flat",
            "href": (url_for("inventory.alerts")
                     if has_permission("inventory_alerts", user)
                     else url_for("inventory.dashboard")),
        })
    if "payroll" in granted:
        pending = db.execute(
            "SELECT COUNT(*) AS n FROM payroll_records"
            " WHERE payment_status = 'Pending'"
        ).fetchone()["n"]
        cost = db.execute(
            "SELECT COALESCE(SUM(net_salary), 0) AS n FROM payroll_records"
            " WHERE payroll_month = ?", (this_month,),
        ).fetchone()["n"]
        tiles.append({
            "label": "Payslips Pending", "value": pending, "icon": "file",
            "note": "{} ETB booked for {}".format(money_etb(cost),
                                                  month_short(this_month)),
            "tone": "down" if pending else "flat",
            "href": url_for("payroll.payroll_dashboard"),
        })
    if "procurement" in granted:
        open_orders = db.execute("""
            SELECT COUNT(*) AS n FROM finance.procurement_orders
            WHERE status IN ('Ordered', 'Partially Received')
        """).fetchone()["n"]
        committed = db.execute("""
            SELECT COALESCE(SUM(total_cost), 0) AS n FROM finance.procurement_orders
            WHERE status IN ('Ordered', 'Partially Received', 'Received')
        """).fetchone()["n"]
        tiles.append({
            "label": "Open Purchase Orders", "value": open_orders, "icon": "inbox",
            "note": "{:,.2f} ETB committed".format(float(committed)),
            "tone": "flat",
            "href": url_for("procurement.dashboard"),
        })
    if "audit" in granted:
        refused = db.execute("""
            SELECT COUNT(*) AS n FROM audit_logs
            WHERE outcome IN ('FAILURE', 'DENIED')
              AND occurred_at >= datetime('now', '-7 days')
        """).fetchone()["n"]
        tiles.append({
            "label": "Refusals This Week", "value": refused, "icon": "lock",
            "note": "Failed or denied attempts",
            "tone": "down" if refused else "flat",
            "href": url_for("audit.audit_dashboard", outcome="DENIED"),
        })
    if "accounts" in granted:
        accounts_row = db.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'Active' THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN must_change_password = 1 THEN 1 ELSE 0 END) AS gated
            FROM users
        """).fetchone()
        total_accounts = int(accounts_row["total"] or 0)
        active_accounts = int(accounts_row["active"] or 0)
        tiles.append({
            "label": "Sign-in Accounts", "value": total_accounts,
            "icon": "lock",
            "note": "{} active · {} awaiting a password change".format(
                active_accounts, int(accounts_row["gated"] or 0)),
            "tone": "up" if active_accounts == total_accounts else "down",
            "href": url_for("admin_users"),
        })
        role_rows = db.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN is_system = 0 THEN 1 ELSE 0 END)"
            " AS custom, SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) AS parked"
            " FROM roles").fetchone()
        extra_assignments = db.execute(
            "SELECT COUNT(*) AS n FROM user_roles").fetchone()["n"]
        tiles.append({
            "label": "Roles", "value": int(role_rows["total"] or 0),
            "icon": "users",
            "note": "{} custom · {} parked · {} additional assignment(s)".format(
                int(role_rows["custom"] or 0), int(role_rows["parked"] or 0),
                extra_assignments),
            "tone": "flat", "href": url_for("admin_roles"),
        })

    # --- charts, ranked so the account's own work comes first ----------------
    # The approval rows this account may open, shared by the chart and the queue.
    acted = approval_acted_ids(db, user["id"])
    visible = []
    if "approvals" in granted:
        visible = [row for row in db.execute("""
            SELECT * FROM approval_requests
            ORDER BY CASE status WHEN 'Pending' THEN 0 WHEN 'Returned' THEN 1 ELSE 2 END,
                     updated_at DESC LIMIT 300
        """).fetchall() if can_view_request(row, user, acted)]

    charts = []
    if "finance" in granted:
        income_map = {row["bucket"]: float(row["n"]) for row in db.execute("""
            SELECT substr(transaction_date, 1, 7) AS bucket,
                   COALESCE(SUM(amount_etb), 0) AS n
            FROM finance.finance_transactions
            WHERE transaction_type = 'income' AND transaction_date >= ?
            GROUP BY bucket
        """, (since,)).fetchall()}
        charts.append({
            "kind": "columns", "title": "Income by month",
            "hint": "Last {} months".format(len(months)),
            "rows": [{
                "label": month_short(key), "value": income_map.get(key, 0.0),
                "display": money_etb(income_map.get(key, 0.0)),
            } for key in months],
        })
        charts.append({
            "kind": "bars", "title": "Where the money went",
            "hint": "Expenses since {}".format(month_short(months[0])),
            "rows": [{
                "label": row["category"], "value": float(row["n"]),
                "display": money_etb(row["n"]),
            } for row in db.execute("""
                SELECT category, SUM(amount_etb) AS n FROM finance.finance_transactions
                WHERE transaction_type = 'expense' AND transaction_date >= ?
                GROUP BY category ORDER BY n DESC LIMIT 6
            """, (since,)).fetchall()],
        })
    if "hr" in granted or "accounts" in granted:
        charts.append({
            "kind": "columns", "title": "Headcount by department",
            "hint": "Employee records",
            "rows": [{
                "label": row["department"], "value": row["n"],
                "display": "{} employee{}".format(row["n"], "" if row["n"] == 1 else "s"),
            } for row in db.execute("""
                SELECT department, COUNT(*) AS n FROM employees
                GROUP BY department ORDER BY n DESC, department LIMIT 6
            """).fetchall()],
        })
    if "approvals" in granted:
        status_rows = []
        for status in ("Pending", "Returned", "Approved", "Rejected"):
            n = sum(row["status"] == status for row in visible)
            status_rows.append({
                "label": status, "value": n,
                "display": "{} request{}".format(n, "" if n == 1 else "s"),
            })
        charts.append({"kind": "bars", "title": "Requests by status",
                       "hint": "Only what your account can open",
                       "rows": status_rows})
    if "procurement" in granted:
        charts.append({
            "kind": "bars", "title": "Ordered value by supplier",
            "hint": "Excludes cancelled orders",
            "rows": [{
                "label": row["label"], "value": float(row["n"]),
                "display": money_etb(row["n"]),
            } for row in db.execute("""
                SELECT s.name AS label, SUM(o.total_cost) AS n
                FROM finance.procurement_orders AS o
                JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
                WHERE o.status != 'Cancelled'
                GROUP BY o.supplier_id ORDER BY n DESC LIMIT 6
            """).fetchall()],
        })
    if "inventory" in granted:
        charts.append({
            "kind": "bars", "title": "Stock value by category",
            "hint": "Active items",
            "rows": [{
                "label": row["category"], "value": float(row["n"]),
                "display": money_etb(row["n"]),
            } for row in db.execute("""
                SELECT category, SUM(quantity * unit_cost) AS n FROM inventory_items
                WHERE active = 1 GROUP BY category ORDER BY n DESC LIMIT 6
            """).fetchall()],
        })
    if "payroll" in granted:
        charts.append({
            "kind": "columns", "title": "Payroll by month",
            "hint": "Net salary booked",
            "rows": [{
                "label": month_short(row["payroll_month"]), "value": float(row["n"]),
                "display": money_etb(row["n"]),
            } for row in db.execute("""
                SELECT payroll_month, SUM(net_salary) AS n FROM payroll
                GROUP BY payroll_month ORDER BY payroll_month DESC LIMIT 6
            """).fetchall()][::-1],
        })
    charts = [chart for chart in charts if chart["rows"]][:2]

    # --- activity and decisions ---------------------------------------------
    attention = []
    if "approvals" in granted:
        for row in approvals_waiting_for(db, user)[:4]:
            attention.append({
                "icon": "clock", "tone": "warn", "title": row["title"],
                "meta": "{} · {:,.2f} ETB · your step".format(
                    row["request_type"], float(row["amount"])),
                "href": url_for("approvals.detail", request_id=row["id"]),
                "time": "Awaiting you",
            })
    if not attention:
        attention.append({
            "icon": "check", "tone": "ok",
            "title": "Nothing is waiting on you",
            "meta": "Requests routed to a module you hold appear here.",
            "time": "Clear",
        })

    trail = []
    if granted & {"audit", "accounts"}:
        trail = [{
            "icon": "arrow", "tone": "ok",
            "title": row["description"],
            "meta": "{} · {} · {}".format(row["username"], row["role"],
                                          row["module"]),
            "href": url_for("audit.audit_dashboard", username=row["username"]),
            "time": audit_time(row["occurred_at"]),
        } for row in db.execute("""
            SELECT * FROM audit_logs ORDER BY id DESC LIMIT 6
        """).fetchall()]
    if not trail:
        trail.append({
            "icon": "check", "tone": "ok", "title": "No activity recorded yet",
            "meta": "Sign-ins, edits and decisions appear here.",
            "time": "",
        })

    # --- tables ---------------------------------------------------------------
    employees = []
    if granted & {"hr", "accounts"}:
        employees = [{
            "name": row["full_name"], "code": row["employee_code"],
            "department": row["department"], "position": row["job_title"],
            "status": row["employment_status"],
            "href": url_for("edit_employee", employee_id=row["id"])
                    if "hr" in granted else url_for("admin"),
        } for row in db.execute("""
            SELECT id, employee_code, full_name, department, job_title,
                   employment_status
            FROM employees ORDER BY id DESC LIMIT 5
        """).fetchall()]

    accounts_recent = [{
        "name": row["full_name"], "username": row["username"] or "—",
        "role": role_name_for(row["role"], db), "status": row["status"],
        "last_login": audit_time(row["last_login"]) if row["last_login"] else "Never",
        "gated": bool(row["must_change_password"]),
        "href": url_for("admin_role_account", user_id=row["id"]),
    } for row in db.execute("""
        SELECT id, full_name, username, role, status, last_login,
               must_change_password
        FROM users ORDER BY id DESC LIMIT 6
    """).fetchall()] if "accounts" in granted else []

    requests = [{
        "title": row["title"], "type": row["request_type"],
        "department": row["department"], "amount": money_etb(row["amount"]),
        "by": row["created_by_name"], "status": row["status"],
        "step": STEP_LABEL.get(row["current_approver_step"],
                               row["current_approver_step"]),
        "href": url_for("approvals.detail", request_id=row["id"]),
    } for row in visible[:6]]

    stock = [{
        "name": row["name"], "sku": row["sku"], "category": row["category"],
        "quantity": row["quantity"], "min_stock": row["min_stock"],
        "unit": row["unit"], "href": url_for("inventory.items"),
    } for row in db.execute("""
        SELECT name, sku, category, quantity, min_stock, unit FROM inventory_items
        WHERE active = 1 ORDER BY quantity - min_stock ASC LIMIT 6
    """).fetchall()] if "inventory" in granted else []

    modules = dashboard_modules(db, user, granted)

    actions = []
    for module, label, endpoint, icon, *needed in QUICK_ACTIONS:
        # A shortcut may narrow its module to one right so a desk is never
        # offered a button the guard would refuse.
        if module not in granted:
            continue
        if needed and not has_permission(needed[0], user):
            continue
        actions.append({"label": label, "icon": icon, "href": url_for(endpoint)})

    return render_template(
        "dashboard.html", user=user, granted=granted, tiles=tiles,
        charts=charts, attention=attention, trail=trail, employees=employees,
        accounts_recent=accounts_recent,
        requests=requests, stock=stock, modules=modules, actions=actions,
        budget=budget, greeting=greeting(), today_name=today_name(),
        org=organization_snapshot(db, user),
    )


def tile_count(db, module):
    table = {
        "hr": "SELECT COUNT(*) AS n FROM employees",
        "finance": "SELECT COUNT(*) AS n FROM finance.finance_transactions",
        "budget": "SELECT COUNT(*) AS n FROM budget_allocations WHERE status = 'Active'",
        "payroll": "SELECT COUNT(*) AS n FROM payroll",
        "inventory": "SELECT COUNT(*) AS n FROM inventory_items WHERE active = 1",
        "procurement": "SELECT COUNT(*) AS n FROM finance.procurement_orders",
        "accounts": "SELECT COUNT(*) AS n FROM users",
        "audit": "SELECT COUNT(*) AS n FROM audit_logs",
        "integrations": "SELECT COUNT(*) AS n FROM integrations",
    }.get(module)
    if table is None:
        return 0
    try:
        return db.execute(table).fetchone()["n"]
    except sqlite3.Error:
        return 0


def dashboard_modules(db, user, granted):
    """The module tiles this account can use, plus the report centre.

    The report tile is not a module: every signed-in member of staff has some
    reports, so its count is the number the catalogue says this account can
    actually open rather than rows in a table.
    """
    rows = []
    for module, label, note, endpoint, icon in MODULE_TILES:
        if module == "reports":
            ctx = reporting.context(db, user, account_role_keys(db, user)[1:])
            count = len([item for item in reporting.catalogue(
                ctx, granted, auditor_table_names(db)) if item["available"]])
        elif module in granted:
            count = tile_count(db, module)
        else:
            continue
        rows.append({"label": label, "note": note, "icon": icon,
                     "href": url_for(endpoint), "count": count})
    return rows


def approvals_waiting_for(db, user):
    """Pending requests whose current step this account is allowed to answer."""
    acted = approval_acted_ids(db, user["id"])
    rows = db.execute("""
        SELECT * FROM approval_requests WHERE status = 'Pending'
        ORDER BY updated_at DESC LIMIT 100
    """).fetchall()
    return [row for row in rows
            if can_view_request(row, user, acted) and can_decide_request(row, user)]


@get("/notifications")
@login_required()
def notification_center():
    db = get_db()
    user = current_user()
    sync_notifications(user)
    unread_only = request.args.get("filter") == "unread"
    sql = "SELECT * FROM notifications WHERE user_id = ?"
    if unread_only:
        sql += " AND read_at IS NULL"
    sql += " ORDER BY created_at DESC, id DESC LIMIT 300"
    rows = db.execute(sql, (user["id"],)).fetchall()
    unread = db.execute("""
        SELECT COUNT(*) AS n FROM notifications
        WHERE user_id = ? AND read_at IS NULL
    """, (user["id"],)).fetchone()["n"]
    total = db.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ?",
                       (user["id"],)).fetchone()["n"]
    return render_template("notifications.html", rows=rows, unread=unread,
                           total=total, unread_only=unread_only)


@get("/notifications/<int:notification_id>/open")
@login_required()
def open_notification(notification_id):
    db = get_db()
    user = current_user()
    row = db.execute(
        "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, user["id"])
    ).fetchone()
    if row is None:
        abort(404)
    if row["read_at"] is None:
        db.execute("UPDATE notifications SET read_at = ? WHERE id = ?",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    row["id"]))
        db.commit()
    target = row["href"] or ""
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("notification_center")
    return redirect(target)


@post("/notifications/read-all")
@login_required()
def read_all_notifications():
    db = get_db()
    user = current_user()
    changed = db.execute(
        "UPDATE notifications SET read_at = ?"
        " WHERE user_id = ? AND read_at IS NULL",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), user["id"])
    ).rowcount
    db.commit()
    flash("{} notification{} marked as read.".format(
        changed, "" if changed == 1 else "s"),
        "success" if changed else "info")
    target = request.form.get("next", "")
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("notification_center")
    return redirect(target)


@route("/search")
@login_required()
def search():
    term = request.args.get("q", "").strip()
    groups, total = [], 0
    if len(term) >= 2:
        db = get_db()
        granted = effective_permissions(current_user())
        like = "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
        for modules, label, table, columns, *needed in SEARCH_SCOPES:
            if table == "letters":
                # Letters open on an account grant, which no role module tick
                # implies, so the scope is gated on the grant itself.
                from services.letters import letter_module_visible
                if not letter_module_visible(current_user()):
                    continue
            elif not (set(modules) & granted):
                continue
            # A scope may name the action rights that open its rows, so a hit is
            # never offered to an account whose guard would refuse the page.
            elif needed and not any(has_permission(key, current_user())
                                    for key in needed[0]):
                continue
            where = " LIKE ? ESCAPE '\\' OR ".join(columns) + " LIKE ? ESCAPE '\\'"
            params = [like] * len(columns)
            scope_guard, scope_params = search_scope_where(table, db,
                                                           current_user())
            if scope_guard:
                where = "({}) AND {}".format(where, scope_guard)
                params.extend(scope_params)
            rows = db.execute(
                'SELECT * FROM {} WHERE {} ORDER BY rowid DESC LIMIT 6'.format(
                    qualified_table(table), where),
                params,
            ).fetchall()
            if not rows:
                continue
            total += len(rows)
            groups.append({
                "label": label, "rows": [{
                    "title": search_title(row),
                    "meta": " · ".join(str(row[c]) for c in columns[1:3]
                                       if row[c] not in (None, "")),
                    "href": search_href(table, row),
                } for row in rows],
            })
    return render_template("search.html", term=term, groups=groups, total=total)


def upload_too_large(_e):
    flash("That upload is too large. Keep the profile photo under 3 MB and each"
          " document under 5 MB.", "error")
    if session.get("user_id"):
        return redirect(url_for("employees"))
    return redirect(url_for("login"))


def forbidden(_e):
    return render_template("403.html"), 403


def bad_request(error):
    message = getattr(error, "description", "") or (
        "The request could not be completed. Refresh the page and try again.")
    return render_template("error.html", code=400,
                           heading="Request not completed",
                           message=message), 400


def not_found(_e):
    return render_template("error.html", code=404,
                           heading="Page not found",
                           message="That address does not match anything in the"
                                   " system. Check the link or use the menu."), 404


def method_not_allowed(_e):
    return render_template("error.html", code=405,
                           heading="Action not allowed",
                           message="This page does not accept that kind of request."), 405


def server_error(error):
    original = getattr(error, "original_exception", error)
    log_activity("ERROR", "system",
                 "Unhandled {} on {}".format(type(original).__name__, request.path),
                 outcome="FAILURE")
    return render_template("error.html", code=500,
                           heading="Something went wrong",
                           message="The action was not completed. An administrator"
                                   " can review the audit trail for the details."), 500


CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "form-action 'self' https://formspree.io",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "media-src 'self' data: https:",
    "connect-src 'self' https://formspree.io",
))


NO_STORE_PREFIXES = MANAGE_PREFIXES + ("/member", "/account", "/notifications")


def secure_response(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy",
                       "geolocation=(), camera=(), microphone=(), payment=()")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if request.is_secure or current_app.config["SESSION_COOKIE_SECURE"]:
        headers.setdefault("Strict-Transport-Security",
                           "max-age=31536000; includeSubDomains")
    # Management pages carry another person's records and a live form token;
    # neither belongs in a shared cache or the back button after sign-out.
    if response.mimetype == "text/html" and (
            request.endpoint == "login"
            or (session.get("user_id")
                and request.path.startswith(NO_STORE_PREFIXES))):
        headers["Cache-Control"] = "no-store"
    return response


def register_public_routes(app):
    """Mount the six public pages on the application."""
    for name in PAGES:
        app.add_url_rule("/" if name == "index" else f"/{name}",
                         name, page_route(name))
