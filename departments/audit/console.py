"""Audit console, auditor screens and oversight."""

from core.access import current_user
from core.access import perm
from core.config import DATE_PATTERN
from core.db import get_db
from core.db import qualified_table
from core.routing import route
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import AUDITOR_FINDING_STATUS
from services.audit import AUDITOR_RECORD_CAP
from services.audit import AUDITOR_RECORD_TABLES
from services.audit import AUDITOR_REPORTS
from services.audit import AUDITOR_REVIEW_DEPARTMENTS
from services.audit import AUDITOR_REVIEW_STATUS
from services.audit import AUDITOR_RISK_LEVELS
from services.audit import audit_time
from services.audit import auditor_cell
from services.audit import auditor_csv_response
from services.audit import auditor_date_range
from services.audit import auditor_display
from services.audit import auditor_record_groups
from services.audit import auditor_records_url
from services.audit import auditor_report
from services.audit import auditor_report_rows
from services.audit import auditor_report_tabs
from services.audit import auditor_required
from services.audit import auditor_review_or_404
from services.audit import auditor_stamp_tabs
from services.audit import auditor_table_names
from services.audit import auditor_visible_columns
from services.audit import log_activity
from services.letters import LETTER_STATUSES
from services.letters import LETTER_TYPE_LABEL
from services.letters import LETTER_TYPES
from services.letters import letter_kind_label
from services.letters import letter_type_label
from services.recency import stamp as stamp_record
import json


audit_bp = Blueprint("audit", __name__, url_prefix="/admin/audit")


AUDIT_LOG_EXPORT_CAP = 5000

AUDIT_DEVICE_TYPES = ("Desktop", "Tablet", "Mobile", "Other")

AUDIT_EXPORT_COLUMNS = (
    ("occurred_at", "Time"), ("username", "Username"),
    ("full_name", "Full name"), ("role", "Role"),
    ("department", "Department"), ("action", "Action"),
    ("module", "Module"), ("description", "What happened"),
    ("record_type", "Record type"), ("record_id", "Record"),
    ("ip_address", "IP address"), ("device_type", "Device"),
    ("os", "Operating system"), ("browser", "Browser"),
    ("outcome", "Outcome"), ("previous_json", "Previous values"),
    ("updated_json", "Updated values"),
)


def audit_log_filters():
    """The trail's filters, read once so the screen and its export agree."""
    args = request.args
    filters = {
        "q": args.get("q", "").strip()[:200],
        "username": args.get("username", "").strip()[:100],
        "department": args.get("department", "").strip()[:100],
        "action": args.get("action", "").strip()[:100],
        "module": args.get("module", "").strip()[:100],
        "outcome": args.get("outcome", "").strip(),
        "device_type": args.get("device_type", "").strip()[:40],
        "ip_address": args.get("ip_address", "").strip()[:64],
        "start_date": args.get("start_date", "").strip(),
        "end_date": args.get("end_date", "").strip(),
    }
    for value in (filters["start_date"], filters["end_date"]):
        if value and not DATE_PATTERN.fullmatch(value):
            abort(400, "Invalid date. Use YYYY-MM-DD.")

    conditions, params = [], []
    if filters["q"]:
        like = "%{}%".format(filters["q"])
        conditions.append("(username LIKE ? OR full_name LIKE ? OR description LIKE ?"
                          " OR record_type LIKE ? OR record_id LIKE ?)")
        params.extend([like] * 5)
    if filters["username"]:
        # Old rows carry the full name in username, new ones carry the sign-in
        # name, so the one box searches both.
        like = "%{}%".format(filters["username"])
        conditions.append("(username LIKE ? OR full_name LIKE ?)")
        params.extend([like, like])
    if filters["department"]:
        conditions.append("department = ?")
        params.append(filters["department"])
    if filters["action"]:
        conditions.append("action = ?")
        params.append(filters["action"])
    if filters["module"]:
        conditions.append("module = ?")
        params.append(filters["module"])
    if filters["outcome"] in ("SUCCESS", "FAILURE", "DENIED"):
        conditions.append("outcome = ?")
        params.append(filters["outcome"])
    if filters["device_type"]:
        conditions.append("device_type = ?")
        params.append(filters["device_type"])
    if filters["ip_address"]:
        conditions.append("ip_address LIKE ?")
        params.append("%{}%".format(filters["ip_address"]))
    if filters["start_date"]:
        conditions.append("occurred_at >= ?")
        params.append(filters["start_date"] + "T00:00:00")
    if filters["end_date"]:
        conditions.append("occurred_at <= ?")
        params.append(filters["end_date"] + "T23:59:59")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return filters, where, params


@audit_bp.route("/export.csv")
@perm("admin_logs")
def audit_export():
    """The filtered trail as a spreadsheet, written by the server."""
    filters, where, params = audit_log_filters()
    db = get_db()
    rows = db.execute(
        "SELECT * FROM audit_logs" + where + " ORDER BY id DESC LIMIT ?",
        params + [AUDIT_LOG_EXPORT_CAP]).fetchall()
    log_activity("EXPORT", "audit",
                 "Activity log exported ({} row{})".format(
                     len(rows), "" if len(rows) == 1 else "s"),
                 record_type="audit_log",
                 details={"filters": filters, "rows": len(rows)})
    return auditor_csv_response("activity_log", AUDIT_EXPORT_COLUMNS, rows)


@audit_bp.route("/")
@perm("admin_logs")
def audit_dashboard():
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    per_page = 50

    filters, where, params = audit_log_filters()
    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM audit_logs" + where, params).fetchone()[0]
    logs = db.execute(
        "SELECT * FROM audit_logs" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    entries = []
    for row in logs:
        try:
            before = json.loads(row["previous_json"] or "{}")
            after = json.loads(row["updated_json"] or "{}")
        except (TypeError, ValueError):
            before, after = {}, {}
        entries.append({
            "log": row,
            "changes": [
                "{}: {} → {}".format(str(key).replace("_", " "),
                                     before.get(key, "—"), value)
                for key, value in after.items()
            ],
        })

    stats = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN action = 'LOGIN' AND outcome = 'SUCCESS'
                        THEN 1 ELSE 0 END) AS logins,
               SUM(CASE WHEN action = 'LOGIN' AND outcome = 'FAILURE'
                        THEN 1 ELSE 0 END) AS failed_logins,
               SUM(CASE WHEN outcome = 'DENIED' THEN 1 ELSE 0 END) AS denied
        FROM audit_logs
    """).fetchone()
    modules = db.execute("SELECT DISTINCT module FROM audit_logs ORDER BY module").fetchall()
    actions = db.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action").fetchall()
    departments = [row[0] for row in db.execute(
        "SELECT DISTINCT department FROM audit_logs"
        " WHERE department IS NOT NULL AND department <> ''"
        " ORDER BY department")]

    module_rows = [{
        "label": row["label"],
        "value": row["value"],
        "display": "{} event{}".format(row["value"], "" if row["value"] == 1 else "s"),
    } for row in db.execute("""
        SELECT module AS label, COUNT(*) AS value FROM audit_logs
        GROUP BY module ORDER BY value DESC LIMIT 6
    """).fetchall()]

    attention = [{
        "icon": "lock" if row["outcome"] == "DENIED" else "warn",
        "tone": "bad" if row["outcome"] == "DENIED" else "warn",
        "title": "{} · {}".format(row["action"].title(), row["description"]),
        "meta": "{} ({}){}, from {}".format(
            row["full_name"] or row["username"], row["role"],
            ", " + row["department"] if row["department"] else "",
            row["ip_address"] or "no IP"),
        "href": url_for("audit.audit_dashboard", username=row["username"],
                        outcome=row["outcome"]),
        "time": audit_time(row["occurred_at"]),
    } for row in db.execute("""
        SELECT * FROM audit_logs WHERE outcome IN ('FAILURE', 'DENIED')
        ORDER BY id DESC LIMIT 6
    """).fetchall()]
    if not attention:
        attention.append({
            "icon": "check", "tone": "ok",
            "title": "No refusals or failures recorded",
            "meta": "Every logged action so far finished successfully.",
            "time": "Clear",
        })

    return render_template(
        "Admin/audit_logs.html",
        logs=entries,
        stats=stats,
        modules=modules,
        actions=actions,
        departments=departments,
        devices=AUDIT_DEVICE_TYPES,
        module_rows=module_rows,
        attention=attention,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        total=total,
        filters=filters,
    )


@route("/management/overview")
@perm("audit", "accounts")
def management_overview():
    db = get_db()
    month = date.today().strftime("%Y-%m")
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")

    counts = {
        "accounts": db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
        "disabled": db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE status <> 'Active'"
        ).fetchone()["n"],
        "employees": db.execute("SELECT COUNT(*) AS n FROM employees").fetchone()["n"],
        "employees_active": db.execute(
            "SELECT COUNT(*) AS n FROM employees WHERE employment_status = 'Active'"
        ).fetchone()["n"],
        "vacancies": db.execute("SELECT COUNT(*) AS n FROM job_vacancies").fetchone()["n"],
        "applications": db.execute(
            "SELECT COUNT(*) AS n FROM job_applications WHERE status = 'New'"
        ).fetchone()["n"],
        "items": db.execute(
            "SELECT COUNT(*) AS n FROM inventory_items WHERE active = 1"
        ).fetchone()["n"],
        "low_stock": db.execute("""
            SELECT COUNT(*) AS n FROM inventory_items
            WHERE active = 1 AND quantity <= min_stock
        """).fetchone()["n"],
        "suppliers": db.execute(
            "SELECT COUNT(*) AS n FROM finance.procurement_suppliers"
        ).fetchone()["n"],
        "purchase_requests": db.execute(
            "SELECT COUNT(*) AS n FROM finance.procurement_requests WHERE status = 'Pending'"
        ).fetchone()["n"],
        "open_orders": db.execute("""
            SELECT COUNT(*) AS n FROM finance.procurement_orders
            WHERE status IN ('Ordered', 'Partially Received')
        """).fetchone()["n"],
        "approvals_pending": db.execute(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE status = 'Pending'"
        ).fetchone()["n"],
        "payroll_pending": db.execute(
            "SELECT COUNT(*) AS n FROM payroll WHERE payment_status = 'Pending'"
        ).fetchone()["n"],
    }

    money = {
        "income": db.execute("""
            SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income'
                                     THEN amount_etb ELSE 0 END), 0) AS total
            FROM finance.finance_transactions
        """).fetchone()["total"],
        "expenses": db.execute("""
            SELECT COALESCE(SUM(CASE WHEN transaction_type = 'expense'
                                     THEN amount_etb ELSE 0 END), 0) AS total
            FROM finance.finance_transactions
        """).fetchone()["total"],
        "payroll_month": db.execute(
            "SELECT COALESCE(SUM(net_salary), 0) AS total FROM payroll WHERE payroll_month = ?",
            (month,),
        ).fetchone()["total"],
        "procurement_spend": db.execute("""
            SELECT COALESCE(SUM(total_cost), 0) AS total FROM finance.procurement_orders
            WHERE status IN ('Ordered', 'Partially Received', 'Received')
        """).fetchone()["total"],
        "receivables": db.execute("""
            SELECT COALESCE(SUM(CASE WHEN i.invoice_type = 'customer'
                                     THEN i.total_etb - COALESCE(p.paid, 0) END), 0) AS total
            FROM finance.finance_invoices AS i
            LEFT JOIN (
                SELECT invoice_id, SUM(amount_etb) AS paid
                FROM finance.finance_payments GROUP BY invoice_id
            ) AS p ON p.invoice_id = i.id
            WHERE i.status != 'Cancelled'
        """).fetchone()["total"],
        "payables": db.execute("""
            SELECT COALESCE(SUM(CASE WHEN i.invoice_type = 'supplier'
                                     THEN i.total_etb - COALESCE(p.paid, 0) END), 0) AS total
            FROM finance.finance_invoices AS i
            LEFT JOIN (
                SELECT invoice_id, SUM(amount_etb) AS paid
                FROM finance.finance_payments GROUP BY invoice_id
            ) AS p ON p.invoice_id = i.id
            WHERE i.status != 'Cancelled'
        """).fetchone()["total"],
    }

    departments = db.execute("""
        SELECT department, COUNT(*) AS headcount
        FROM employees
        GROUP BY department
        ORDER BY headcount DESC, department
        LIMIT 6
    """).fetchall()

    stock = db.execute("""
        SELECT name, category, quantity, min_stock, unit
        FROM inventory_items WHERE active = 1
        ORDER BY quantity - min_stock ASC
        LIMIT 6
    """).fetchall()

    attention = []
    if counts["low_stock"]:
        attention.append({
            "icon": "box", "tone": "bad",
            "title": "{} item(s) at or below reorder level".format(counts["low_stock"]),
            "meta": "Covered by stock records, but no purchase order has been raised.",
            "href": url_for("inventory.dashboard"), "time": "Review",
        })
    if counts["purchase_requests"] or counts["approvals_pending"]:
        attention.append({
            "icon": "file", "tone": "warn",
            "title": "{} purchase request(s) and {} approval(s) waiting".format(
                counts["purchase_requests"], counts["approvals_pending"]),
            "meta": "Buying cannot proceed until these are answered.",
            "href": url_for("approvals.dashboard"), "time": "Decide",
        })
    if counts["payroll_pending"]:
        attention.append({
            "icon": "cash", "tone": "warn",
            "title": "{} payroll line(s) still marked Pending".format(counts["payroll_pending"]),
            "meta": "Run Payroll shows which months are unsettled.",
            "href": url_for("payroll"), "time": "Settle",
        })
    denied = db.execute("""
        SELECT COUNT(*) AS n FROM audit_logs
        WHERE outcome IN ('FAILURE', 'DENIED') AND occurred_at >= ?
    """, (week_ago,)).fetchone()["n"]
    if denied:
        attention.append({
            "icon": "lock", "tone": "bad",
            "title": "{} failed or denied action(s) in the last 7 days".format(denied),
            "meta": "Filtered view of the audit trail.",
            "href": url_for("audit.audit_dashboard", outcome="FAILURE"), "time": "Inspect",
        })
    if not attention:
        attention.append({
            "icon": "check", "tone": "ok",
            "title": "Nothing is blocking the workshop",
            "meta": "Stock, buying, approvals and payroll are all clear.",
            "time": "Clear",
        })

    events = db.execute("""
        SELECT username, role, action, module, description, outcome, occurred_at
        FROM audit_logs
        ORDER BY id DESC
        LIMIT 12
    """).fetchall()

    activity = [{
        "title": event["description"],
        "meta": "{} · {} · {}".format(event["username"], event["role"], event["module"]),
        "time": audit_time(event["occurred_at"]),
        "href": url_for("audit.audit_dashboard", username=event["username"]),
    } for event in events]
    if not activity:
        activity.append({
            "icon": "inbox", "tone": "",
            "title": "The audit trail is still empty",
            "meta": "Events start recording as soon as accounts use the modules.",
            "href": url_for("audit.audit_dashboard"), "time": "Info",
        })

    flow_rows = [
        {"label": "Income", "value": float(money["income"]),
         "display": "{:,.2f}".format(float(money["income"]))},
        {"label": "Expenses", "value": float(money["expenses"]),
         "display": "{:,.2f}".format(float(money["expenses"]))},
        {"label": "Payroll net ({})".format(month), "value": float(money["payroll_month"]),
         "display": "{:,.2f}".format(float(money["payroll_month"]))},
        {"label": "Open purchase orders", "value": float(money["procurement_spend"]),
         "display": "{:,.2f}".format(float(money["procurement_spend"]))},
        {"label": "Receivables", "value": float(money["receivables"]),
         "display": "{:,.2f}".format(float(money["receivables"]))},
        {"label": "Payables", "value": float(money["payables"]),
         "display": "{:,.2f}".format(float(money["payables"]))},
    ]

    department_rows = [{
        "label": row["department"],
        "value": row["headcount"],
        "display": "{} person{}".format(row["headcount"], "" if row["headcount"] == 1 else "s"),
    } for row in departments]

    return render_template(
        "management/overview.html",
        counts=counts,
        money=money,
        departments=departments,
        department_rows=department_rows,
        stock=stock,
        activity=activity,
        attention=attention,
        flow_rows=flow_rows,
        month=month,
    )


@route("/admin/password-security")
@perm("admin_accounts")
def admin_password_security():
    db = get_db()
    rows = db.execute("""
        SELECT id, full_name, email, role, status, last_login, password_hash
        FROM users
        ORDER BY role, full_name
    """).fetchall()

    results = []
    for row in rows:
        method = str(row["password_hash"]).split(":", 1)[0]
        if method == "scrypt":
            quality = "Current"
        elif method == "pbkdf2":
            quality = "Legacy rounds"
        else:
            quality = "Unrecognised"
        results.append({
            "name": row["full_name"],
            "email": row["email"],
            "role": row["role"],
            "status": row["status"],
            "last_login": row["last_login"],
            "method": method or "none",
            "quality": quality,
        })

    counts = {
        "total": len(results),
        "current": sum(item["quality"] == "Current" for item in results),
        "legacy": sum(item["quality"] != "Current" for item in results),
        "never_logged_in": sum(not item["last_login"] for item in results),
        "disabled": sum(item["status"] != "Active" for item in results),
    }

    return render_template("Admin/password_security.html", users=results, counts=counts)


@route("/auditor/")
@auditor_required
def auditor_dashboard():
    db = get_db()
    present = auditor_table_names(db)
    has_reviews = "audit_reviews" in present
    has_findings = "audit_findings" in present

    stats = {"reviews": 0, "open_reviews": 0, "findings": 0,
             "open_findings": 0, "high_risk": 0, "events": 0, "refusals": 0}
    if has_reviews:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status <> 'Completed' THEN 1 ELSE 0 END), 0)
                       AS open_total
            FROM audit_reviews""").fetchone()
        stats["reviews"], stats["open_reviews"] = row["total"], row["open_total"]
    if has_findings:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status IN ('Open', 'Under Review')
                            THEN 1 ELSE 0 END), 0) AS open_total,
                   COALESCE(SUM(CASE WHEN risk_level IN ('High', 'Critical')
                            THEN 1 ELSE 0 END), 0) AS raised
            FROM audit_findings""").fetchone()
        stats["findings"], stats["open_findings"], stats["high_risk"] = (
            row["total"], row["open_total"], row["raised"])
    has_logs = "audit_logs" in present
    if has_logs:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN outcome IN ('DENIED', 'FAILURE')
                            THEN 1 ELSE 0 END), 0) AS bad
            FROM audit_logs""").fetchone()
        stats["events"], stats["refusals"] = row["total"], row["bad"]

    reviews = db.execute("""
        SELECT r.*, COUNT(f.id) AS finding_count
        FROM audit_reviews r
        LEFT JOIN audit_findings f ON f.review_id = r.id
        GROUP BY r.id ORDER BY r.id DESC LIMIT 8""").fetchall() if has_reviews else []
    findings = db.execute("""
        SELECT f.*, r.department, r.title AS review_title
        FROM audit_findings f
        LEFT JOIN audit_reviews r ON r.id = f.review_id
        ORDER BY f.id DESC LIMIT 8""").fetchall() if has_findings else []

    attention = [{
        "icon": "lock" if row["outcome"] == "DENIED" else "warn",
        "tone": "bad" if row["outcome"] == "DENIED" else "warn",
        "title": "{} · {}".format(row["action"].title(), row["description"]),
        "meta": "{} ({}), {}".format(row["username"], row["role"], row["module"]),
        "href": url_for("audit.audit_dashboard", username=row["username"],
                        outcome=row["outcome"]),
        "time": audit_time(row["occurred_at"]),
    } for row in db.execute("""
        SELECT * FROM audit_logs WHERE outcome IN ('DENIED', 'FAILURE')
        ORDER BY id DESC LIMIT 6""").fetchall()] if has_logs else []
    if not attention:
        attention.append({
            "icon": "check", "tone": "ok",
            "title": "Nothing refused or failing",
            "meta": "Every logged action so far finished successfully.",
            "time": "Clear"})

    recent_activity, correspondence = [], []
    if has_logs:
        recent_activity = [{
            "icon": "lock" if row["outcome"] == "DENIED" else "arrow",
            "tone": "bad" if row["outcome"] in ("DENIED", "FAILURE") else "ok",
            "title": row["description"],
            "meta": "{} · {} · {}".format(row["username"], row["role"],
                                          row["module"]),
            "href": url_for("audit.audit_dashboard", username=row["username"],
                            module=row["module"]),
            "time": audit_time(row["occurred_at"]),
        } for row in db.execute("""
            SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8""").fetchall()]
        for row in db.execute("""
                SELECT * FROM audit_logs WHERE module = 'letters'
                ORDER BY id DESC LIMIT 8""").fetchall():
            try:
                details = json.loads(row["details_json"] or "{}")
            except (TypeError, ValueError):
                details = {}
            correspondence.append({
                "time": audit_time(row["occurred_at"]),
                "action": row["action"],
                "reference": details.get("reference_no") or "",
                "subject": details.get("subject") or "",
                "username": row["username"],
                "role": row["role"],
                "outcome": row["outcome"],
            })

    return render_template(
        "Auditor/dashboard.html",
        stats=stats,
        groups=auditor_record_groups(db, present),
        reviews=reviews,
        findings=findings,
        attention=attention,
        activity=recent_activity,
        correspondence=correspondence,
        tabs=auditor_stamp_tabs(auditor_report_tabs(db, present)),
        has_reviews=has_reviews,
        has_findings=has_findings,
    )


@route("/auditor/reviews", methods=["GET", "POST"])
@auditor_required
def auditor_reviews():
    db = get_db()
    if "audit_reviews" not in auditor_table_names(db):
        # A database that has not run the new boot yet has nowhere to write to.
        flash("The audit tables are not in this database yet. Restart the "
              "application so they can be created.", "error")
        return render_template("Auditor/reviews.html", departments=(),
                               reviews=(), statuses=(), values=None)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        department = request.form.get("department", "").strip()
        summary = request.form.get("summary", "").strip()
        period_start = (request.form.get("period_start") or "").strip()
        period_end = (request.form.get("period_end") or "").strip()
        error = None
        if not title:
            error = "Give the review a title."
        elif len(title) > 200:
            error = "Keep the review title under 200 characters."
        elif department not in AUDITOR_REVIEW_DEPARTMENTS:
            error = "Choose the department the review covers."
        for value in (period_start, period_end):
            if value and not DATE_PATTERN.fullmatch(value):
                error = "Enter the period as YYYY-MM-DD."
        if period_start and period_end and period_start > period_end:
            error = "The period cannot end before it starts."
        if error:
            log_activity("DENY", "audit",
                         "Rejected an audit review: {}".format(error),
                         outcome="FAILURE")
            flash(error, "error")
            return render_template("Auditor/reviews.html",
                                   departments=AUDITOR_REVIEW_DEPARTMENTS,
                                   statuses=AUDITOR_REVIEW_STATUS,
                                   reviews=db.execute("""
                                       SELECT r.*, COUNT(f.id) AS finding_count
                                       FROM audit_reviews r
                                       LEFT JOIN audit_findings f ON f.review_id = r.id
                                       GROUP BY r.id ORDER BY r.id DESC""").fetchall(),
                                   values=request.form)

        user = current_user()
        result = db.execute("""
            INSERT INTO audit_reviews (title, department, review_period_start,
                                       review_period_end, status, summary,
                                       created_by, created_at)
            VALUES (?, ?, ?, ?, 'Open', ?, ?, ?)
        """, (title, department, period_start, period_end, summary, user["id"],
              datetime.now(timezone.utc).isoformat(timespec="seconds")))
        stamp_record(db, "audit_reviews", result.lastrowid, user)
        db.commit()
        log_activity("CREATE", "audit",
                     "Audit review #{} opened on {}".format(result.lastrowid, department),
                     record_type="audit_review", record_id=result.lastrowid)
        flash("Audit review created.", "success")
        return redirect(url_for("auditor_findings", review_id=result.lastrowid))

    return render_template(
        "Auditor/reviews.html",
        departments=AUDITOR_REVIEW_DEPARTMENTS,
        statuses=AUDITOR_REVIEW_STATUS,
        reviews=db.execute("""
            SELECT r.*, COUNT(f.id) AS finding_count
            FROM audit_reviews r
            LEFT JOIN audit_findings f ON f.review_id = r.id
            GROUP BY r.id ORDER BY r.id DESC""").fetchall(),
        values=None,
    )


@route("/auditor/reviews/<int:review_id>/status", methods=["POST"])
@auditor_required
def auditor_review_status(review_id):
    db = get_db()
    review = auditor_review_or_404(db, review_id)
    status = request.form.get("status", "").strip()
    if status not in AUDITOR_REVIEW_STATUS:
        abort(400)
    db.execute("UPDATE audit_reviews SET status = ? WHERE id = ?", (status, review_id))
    stamp_record(db, "audit_reviews", review_id)
    db.commit()
    log_activity("UPDATE", "audit",
                 "Audit review #{} marked {}".format(review_id, status),
                 record_type="audit_review", record_id=review_id)
    flash("Review marked {}.".format(status.lower()), "success")
    return redirect(url_for("auditor_reviews"))


@route("/auditor/reviews/<int:review_id>", methods=["GET", "POST"])
@auditor_required
def auditor_findings(review_id):
    db = get_db()
    review = auditor_review_or_404(db, review_id)
    values = None

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        risk_level = request.form.get("risk_level", "Medium").strip()
        recommendation = request.form.get("recommendation", "").strip()
        error = None
        if not title:
            error = "Give the finding a title."
        elif len(title) > 200:
            error = "Keep the finding title under 200 characters."
        elif not description:
            error = "A finding has to say what was found, with its evidence."
        elif risk_level not in AUDITOR_RISK_LEVELS:
            error = "Choose the risk level."
        if error:
            log_activity("DENY", "audit",
                         "Rejected a finding on review {}: {}".format(
                             review_id, error),
                         outcome="FAILURE", record_type="audit_review",
                         record_id=review_id)
            flash(error, "error")
            values = request.form
        else:
            user = current_user()
            result = db.execute("""
                INSERT INTO audit_findings (review_id, title, description, risk_level,
                                            recommendation, status, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, 'Open', ?, ?)
            """, (review_id, title, description, risk_level, recommendation,
                  user["id"], datetime.now(timezone.utc).isoformat(timespec="seconds")))
            stamp_record(db, "audit_findings", result.lastrowid, user)
            db.commit()
            log_activity("CREATE", "audit",
                         "Finding #{} raised on review {}".format(result.lastrowid,
                                                                  review_id),
                         record_type="audit_finding", record_id=result.lastrowid)
            flash("Audit finding recorded.", "success")
            return redirect(url_for("auditor_findings", review_id=review_id))

    return render_template(
        "Auditor/findings.html",
        review=review,
        findings=db.execute(
            "SELECT * FROM audit_findings WHERE review_id = ? ORDER BY id DESC",
            (review_id,)).fetchall(),
        risk_levels=AUDITOR_RISK_LEVELS,
        statuses=AUDITOR_FINDING_STATUS,
        values=values,
    )


@route("/auditor/findings/status", methods=["POST"])
@auditor_required
def auditor_finding_status():
    db = get_db()
    try:
        finding_id = int(request.form.get("finding_id", ""))
    except ValueError:
        abort(400)
    row = db.execute("SELECT * FROM audit_findings WHERE id = ?", (finding_id,)).fetchone()
    if row is None:
        abort(404)
    status = request.form.get("status", "").strip()
    response = request.form.get("management_response", "").strip()
    if status not in AUDITOR_FINDING_STATUS:
        abort(400)
    db.execute("""
        UPDATE audit_findings SET status = ?, management_response = ? WHERE id = ?
    """, (status, response or row["management_response"], finding_id))
    stamp_record(db, "audit_findings", finding_id)
    db.commit()
    log_activity("UPDATE", "audit",
                 "Finding #{} marked {}".format(finding_id, status),
                 record_type="audit_finding", record_id=finding_id)
    flash("Finding marked {}.".format(status.lower()), "success")
    return redirect(url_for("auditor_findings", review_id=row["review_id"]))


@route("/auditor/records/<table_name>")
@auditor_required
def auditor_records(table_name):
    """One department table, read-only, newest first."""
    allowed = {table for _department, tables in AUDITOR_RECORD_TABLES
               for table in tables}
    db = get_db()
    if table_name not in allowed or table_name not in auditor_table_names(db):
        abort(404)

    columns = auditor_visible_columns(db, table_name)
    start, end = auditor_date_range()
    # The window is applied only when the table keeps a date worth filtering;
    # an auditor who asks for a range on a table without one gets the rows.
    date_column = next((name for name in ("created_at", "updated_at", "occurred_at",
                                          "attendance_date", "transaction_date",
                                          "hire_date", "period_start")
                        if name in columns), None)
    conditions, params = [], []
    if start and date_column:
        conditions.append("%s >= ?" % date_column)
        params.append(start)
    if end and date_column:
        conditions.append("%s <= ?" % date_column)
        params.append(end + "T23:59:59" if date_column.endswith("at") else end)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    order = " ORDER BY id DESC" if "id" in columns else ""
    rows = db.execute("SELECT %s FROM %s%s%s LIMIT ?" % (
        ", ".join(columns), qualified_table(table_name), where, order),
        params + [AUDITOR_RECORD_CAP]).fetchall()

    if request.args.get("export") == "csv":
        log_activity("EXPORT", "audit",
                     "Downloaded {} record(s) from {}".format(len(rows), table_name),
                     record_type=table_name)
        return auditor_csv_response(table_name,
                                    [(name, name.replace("_", " ").title())
                                     for name in columns], rows)

    total = db.execute("SELECT COUNT(*) FROM %s%s"
                       % (qualified_table(table_name), where),
                       params).fetchone()[0]
    labels = [(name, name.replace("_", " ").title()) for name in columns]
    return render_template(
        "Auditor/records.html",
        table_name=table_name,
        label=table_name.replace("_", " ").title(),
        table_columns=labels,
        rows=[[auditor_display(row[name]) for name in columns] for row in rows],
        total=total,
        capped=total > len(rows),
        date_column=date_column,
        start_date=start,
        end_date=end,
        csv_url=auditor_records_url(table_name, export="csv",
                                     start_date=start, end_date=end),
        back_url=url_for("auditor_dashboard"),
        filters_url=auditor_records_url(table_name),
    )


@route("/auditor/reports")
@auditor_required
def auditor_reports():
    """The report centre: one tab of real module data per request."""
    db = get_db()
    present = auditor_table_names(db)
    start, end = auditor_date_range()
    tabs = auditor_stamp_tabs(auditor_report_tabs(db, present), start, end)
    wanted = (request.args.get("report") or "").strip()
    spec = auditor_report(wanted)
    if spec is None or spec["requires"] not in present:
        spec = next((item for item in AUDITOR_REPORTS
                     if item["requires"] in present), None)
    if spec is None:
        return render_template("Auditor/reports.html", report=None, tabs=tabs,
                               start_date="", end_date="",
                               back_url=url_for("auditor_dashboard"))

    rows = auditor_report_rows(db, spec, start, end)
    if request.args.get("export") == "csv":
        log_activity("EXPORT", "audit",
                     "Downloaded the {} report ({} rows)".format(
                         spec["key"], len(rows)),
                     record_type="audit_report")
        return auditor_csv_response(spec["key"], spec["columns"], rows)

    cells = [[auditor_cell(row, key, spec["money"]) for key, _label in spec["columns"]]
             for row in rows]
    total = db.execute("SELECT COUNT(*) FROM (%s)" % spec["sql"]).fetchone()[0]
    return render_template(
        "Auditor/reports.html",
        report=spec, table_columns=spec["columns"], rows=cells, tabs=tabs,
        count=len(cells), total=total,
        start_date=start, end_date=end,
        report_url=url_for("auditor_reports"),
        csv_url=url_for("auditor_reports", report=spec["key"], export="csv",
                         start_date=start, end_date=end),
        tabs_url=url_for("auditor_reports"),
        back_url=url_for("auditor_dashboard"),
    )


@route("/auditor/reports/finance")
@auditor_required
def auditor_finance_reports():
    """The URL this console shipped with, now the finance tab of the report centre."""
    return redirect(url_for("auditor_reports", report="finance"))


@route("/auditor/letters")
@auditor_required
def auditor_letters():
    """The letter register, read-only, with every recorded letter action.

    Nothing here writes: the auditor reads the letters as they stand and the
    audit trail rows behind them, never the letter body or a credential. The
    scope follows the account's audit access, so an auditor reads every
    category and every department, and one letter's history can be pulled up
    on its own.
    """
    db = get_db()
    start, end = auditor_date_range()
    term = (request.args.get("q") or "").strip()[:120]
    kind = (request.args.get("type") or "").strip()
    status = (request.args.get("status") or "").strip()
    try:
        letter_id = int(request.args.get("letter") or 0)
    except (TypeError, ValueError):
        letter_id = 0

    conditions, params = [], []
    like = ""
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = "%" + escaped + "%"
        conditions.append(
            "(l.reference_no LIKE ? ESCAPE '\\' OR l.subject LIKE ? ESCAPE '\\'"
            " OR l.sender LIKE ? ESCAPE '\\' OR l.recipient LIKE ? ESCAPE '\\')")
        params.extend([like] * 4)
    if kind in LETTER_TYPES:
        conditions.append("l.letter_type = ?")
        params.append(kind)
    if status in LETTER_STATUSES:
        conditions.append("l.status = ?")
        params.append(status)
    if letter_id:
        conditions.append("l.id = ?")
        params.append(letter_id)
    if start:
        conditions.append("l.letter_date >= ?")
        params.append(start)
    if end:
        conditions.append("l.letter_date <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = db.execute("SELECT COUNT(*) FROM letter.letters AS l" + where,
                       params).fetchone()[0]
    letters = db.execute("""
        SELECT l.*, u.username AS created_by_username,
               u.full_name AS created_by_name,
               ap.full_name AS approved_by_name
        FROM letter.letters AS l
        LEFT JOIN users AS u ON u.id = l.created_by
        LEFT JOIN users AS ap ON ap.id = l.approved_by
        {where}
        ORDER BY l.letter_date DESC, l.id DESC LIMIT ?
    """.format(where=where), params + [AUDITOR_RECORD_CAP]).fetchall()

    counts = db.execute("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN letter_type = 'official' THEN 1 ELSE 0 END), 0)
                   AS official,
               COALESCE(SUM(CASE WHEN letter_type = 'incoming' THEN 1 ELSE 0 END), 0)
                   AS incoming,
               COALESCE(SUM(CASE WHEN letter_type = 'outgoing' THEN 1 ELSE 0 END), 0)
                   AS outgoing,
               COALESCE(SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END), 0)
                   AS approved,
               COALESCE(SUM(CASE WHEN status = 'Sent' THEN 1 ELSE 0 END), 0)
                   AS sent
        FROM letter.letters""").fetchone()

    act_conditions, act_params = [], []
    if term:
        act_conditions.append("(a.description LIKE ? ESCAPE '\\'"
                              " OR a.details_json LIKE ? ESCAPE '\\'"
                              " OR a.username LIKE ? ESCAPE '\\')")
        act_params.extend([like] * 3)
    if letter_id:
        act_conditions.append("a.record_type = 'letter' AND a.record_id = ?")
        act_params.append(str(letter_id))
    if kind in LETTER_TYPES:
        act_conditions.append("a.details_json LIKE ? ESCAPE '\\'")
        act_params.append('%"letter_type": "{}"%'.format(kind))
    if start:
        act_conditions.append("a.occurred_at >= ?")
        act_params.append(start + "T00:00:00")
    if end:
        act_conditions.append("a.occurred_at <= ?")
        act_params.append(end + "T23:59:59")
    act_where = " WHERE a.module = 'letters'"
    if act_conditions:
        act_where += " AND " + " AND ".join(act_conditions)
    events = []
    for row in db.execute("""
            SELECT * FROM audit_logs AS a{where}
            ORDER BY a.id DESC LIMIT 60""".format(where=act_where),
            act_params).fetchall():
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, ValueError):
            details = {}
        events.append({
            "time": audit_time(row["occurred_at"]),
            "action": row["action"],
            "username": row["username"],
            "role": row["role"],
            "outcome": row["outcome"],
            "description": row["description"],
            "reference": details.get("reference_no") or "",
            "subject": details.get("subject") or "",
            "letter_type": details.get("letter_type") or "",
            "letter_type_label": (letter_type_label(details["letter_type"])
                                  if details.get("letter_type") else ""),
            "kind_label": letter_kind_label(details.get("letter_type"),
                                            details.get("letter_kind")),
            "record_id": row["record_id"],
            "letter_id": (row["record_id"]
                          if row["record_type"] == "letter" else 0),
        })

    active_letter = next((letter for letter in letters
                          if letter_id and letter["id"] == letter_id), None)
    return render_template(
        "Auditor/letters.html",
        letters=letters, total=total, capped=total > len(letters), counts=counts,
        events=events, term=term, selected_type=kind, selected_status=status,
        letter_types=LETTER_TYPES, letter_type_labels=LETTER_TYPE_LABEL,
        letter_statuses=LETTER_STATUSES, active_letter=active_letter,
        selected_letter=letter_id,
        start_date=start, end_date=end,
        raw_url=url_for("auditor_records", table_name="letters"),
        trail_url=url_for("audit.audit_dashboard", module="letters"),
    )


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(audit_bp)
