"""Budget plans, allocations, requests and reports."""

from core.access import current_user
from core.access import perm
from core.db import get_db
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import log_activity
from services.budget import BUDGET_CURRENCIES
from services.budget import budget_allocation
from services.budget import budget_allocation_rows
from services.budget import budget_amount
from services.budget import budget_department_match
from services.budget import budget_department_names
from services.budget import budget_departments_defined
from services.budget import budget_post_finance_expenditure
from services.budget import budget_problem_for
from services.budget import budget_spendable_allocations
from services.notifications import deliver_notification
from services.notifications import notify_permission
from services.recency import stamp as stamp_record


budget_bp = Blueprint("budget", __name__, url_prefix="/budget")


@budget_bp.context_processor
def budget_template_context():
    return {"budget_currencies": BUDGET_CURRENCIES}


@budget_bp.route("/")
@perm("budget")
def dashboard():
    """The budget overview now lives on the shared dashboard."""
    return redirect(url_for("dashboard"))


@budget_bp.route("/plans")
@perm("budget")
def plan_list():
    plans = get_db().execute("""
        SELECT p.*,
               (SELECT COALESCE(SUM(a.allocated_amount), 0)
                  FROM budget_allocations a WHERE a.plan_id = p.id)
                 AS allocated_amount,
               (SELECT COALESCE(SUM(e.amount), 0)
                  FROM budget_expenditures e
                  JOIN budget_allocations a ON a.id = e.allocation_id
                 WHERE a.plan_id = p.id) AS spent_amount
        FROM budget_plans AS p
        ORDER BY p.fiscal_year DESC, p.id DESC
    """).fetchall()
    return render_template("budget/plan_list.html", plans=plans)


@budget_bp.route("/plans/create", methods=["GET", "POST"])
@perm("budget")
def create_plan():
    if request.method == "POST":
        try:
            name = request.form.get("plan_name", "").strip()
            if not name:
                raise ValueError("A plan name is required.")
            try:
                year = int(request.form.get("fiscal_year", ""))
            except (TypeError, ValueError):
                raise ValueError("Enter a valid fiscal year.")
            if year < 2000 or year > 2200:
                raise ValueError("Enter a fiscal year between 2000 and 2200.")
            currency = request.form.get("currency", "ETB").upper()
            if currency not in BUDGET_CURRENCIES:
                raise ValueError("Choose a supported currency.")
            total = budget_amount(request.form.get("total_budget", ""),
                                  "Total budget")
            if total <= 0:
                raise ValueError("The total budget must be greater than zero.")
            db = get_db()
            user = current_user()
            cursor = db.execute("""
                INSERT INTO budget_plans (
                    plan_name, fiscal_year, description, total_budget,
                    currency, status, created_by, created_by_name
                ) VALUES (?, ?, ?, ?, ?, 'Draft', ?, ?)
            """, (name[:150], year, request.form.get("description", "").strip(),
                  str(total), currency, user["id"], user["full_name"]))
            stamp_record(db, "budget_plans", cursor.lastrowid, user)
            db.commit()
            log_activity("CREATE", "budget",
                         "Budget plan '{}' created for {} ({} {})".format(
                             name, year, total, currency),
                         record_type="budget_plan", record_id=cursor.lastrowid)
            flash("Budget plan created as a draft. Activate it before allocating money.",
                  "success")
            return redirect(url_for("dashboard"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("budget/create_plan.html")


@budget_bp.route("/plans/<int:plan_id>/status", methods=["POST"])
@perm("budget")
def set_plan_status(plan_id):
    status = request.form.get("status", "")
    if status not in ("Active", "Approved", "Closed"):
        abort(400)
    db = get_db()
    plan = db.execute("SELECT * FROM budget_plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        abort(404)
    if plan["status"] == "Closed":
        flash("A closed plan cannot be reopened.", "error")
        return redirect(url_for("budget.plan_list"))
    if status == "Active" and plan["status"] not in ("Draft", "Approved"):
        flash("That plan is already active.", "error")
        return redirect(url_for("budget.plan_list"))
    if status == "Closed":
        open_commitments = db.execute("""
            SELECT COUNT(*) AS n FROM budget_commitments c
            JOIN budget_allocations a ON a.id = c.allocation_id
            WHERE a.plan_id = ? AND c.status = 'Open'
        """, (plan_id,)).fetchone()["n"]
        if open_commitments:
            flash("Release or invoice the {} open commitment(s) before closing this "
                  "plan.".format(open_commitments), "error")
            return redirect(url_for("budget.plan_list"))
    db.execute("UPDATE budget_plans SET status = ?, updated_at = CURRENT_TIMESTAMP"
               " WHERE id = ?", (status, plan_id))
    stamp_record(db, "budget_plans", plan_id)
    db.commit()
    log_activity("UPDATE", "budget",
                 "Budget plan '{}' marked {}".format(plan["plan_name"], status),
                 record_type="budget_plan", record_id=plan_id,
                 previous={"status": plan["status"]},
                 updated={"status": status})
    flash("Budget plan marked {}.".format(status.lower()), "success")
    return redirect(url_for("budget.plan_list"))


@budget_bp.route("/allocations")
@perm("budget")
def allocations():
    return render_template("budget/allocations.html",
                           allocations=budget_allocation_rows(get_db()),
                           allocation_create_endpoint="budget.create_allocation")


@budget_bp.route("/allocations/create", methods=["GET", "POST"])
@perm("budget")
def create_allocation():
    db = get_db()
    if request.method == "POST":
        try:
            name = request.form.get("allocation_name", "").strip()
            department = request.form.get("department", "").strip()
            plan_id = request.form.get("plan_id", "").strip()
            currency = request.form.get("currency", "ETB").upper()
            if not name:
                raise ValueError("An allocation name is required.")
            if not department:
                raise ValueError("Choose the department this allocation belongs to.")
            department_row = budget_department_match(db, department)
            if department_row is None and budget_departments_defined(db):
                raise ValueError("Choose a department from User Management.")
            department = department_row["name"] if department_row else department[:120]
            department_id = department_row["id"] if department_row else None
            if currency not in BUDGET_CURRENCIES:
                raise ValueError("Choose a supported currency.")
            try:
                plan_id = int(plan_id)
            except (TypeError, ValueError):
                raise ValueError("Choose the budget plan to allocate from.")
            amount = budget_amount(request.form.get("allocated_amount", ""),
                                   "Allocated amount")
            if amount <= 0:
                raise ValueError("The allocated amount must be greater than zero.")
            plan = db.execute("SELECT * FROM budget_plans WHERE id = ?",
                              (plan_id,)).fetchone()
            if plan is None:
                raise ValueError("Choose a valid budget plan.")
            if plan["status"] not in ("Active", "Approved"):
                raise ValueError("Activate the budget plan before allocating from it.")
            if plan["currency"] != currency:
                raise ValueError("The allocation currency must match the plan currency.")
            already = db.execute("""
                SELECT COALESCE(SUM(allocated_amount), 0) AS total
                FROM budget_allocations WHERE plan_id = ?
            """, (plan_id,)).fetchone()["total"]
            if Decimal(str(already)) + amount > Decimal(str(plan["total_budget"])):
                raise ValueError(
                    "The plan only has {:,.2f} {} unallocated.".format(
                        float(plan["total_budget"]) - float(already), currency))
            user = current_user()
            cursor = db.execute("""
                INSERT INTO budget_allocations (
                    plan_id, allocation_name, department, department_id, currency,
                    allocated_amount, status, notes, created_by, created_by_name
                ) VALUES (?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?)
            """, (plan_id, name[:150], department, department_id, currency,
                  str(amount), request.form.get("notes", "").strip()[:1000],
                  user["id"], user["full_name"]))
            stamp_record(db, "budget_allocations", cursor.lastrowid, user)
            db.commit()
            log_activity("CREATE", "budget",
                         "Allocation '{}' of {} {} created for {}".format(
                             name, amount, currency, department),
                         record_type="budget_allocation", record_id=cursor.lastrowid,
                         details={"plan_id": plan_id})
            flash("Allocation created successfully.", "success")
            return redirect(url_for("budget.allocations"))
        except ValueError as exc:
            flash(str(exc), "error")
    plans = db.execute("""
        SELECT * FROM budget_plans WHERE status IN ('Active', 'Approved')
        ORDER BY fiscal_year DESC, id DESC
    """).fetchall()
    return render_template("budget/create_allocation.html", plans=plans,
                           departments=budget_department_names(db))


@budget_bp.route("/requests")
@perm("budget")
def request_list():
    rows = get_db().execute("""
        SELECT r.*, p.plan_name,
               COALESCE(NULLIF(r.requested_by_name, ''), u.full_name)
                   AS requester_name
        FROM budget_requests AS r
        LEFT JOIN budget_plans AS p ON p.id = r.plan_id
        LEFT JOIN users AS u ON u.id = r.requested_by
        ORDER BY CASE r.status WHEN 'Pending' THEN 0 ELSE 1 END, r.id DESC
    """).fetchall()
    return render_template("budget/requests.html", requests=rows)


@budget_bp.route("/requests/create", methods=["GET", "POST"])
@perm("budget")
def create_request():
    db = get_db()
    if request.method == "POST":
        try:
            title = request.form.get("request_title", "").strip()
            department = request.form.get("department", "").strip()
            justification = request.form.get("justification", "").strip()
            currency = request.form.get("currency", "ETB").upper()
            if not title or not department or not justification:
                raise ValueError("Title, department and justification are required.")
            department_row = budget_department_match(db, department)
            if department_row is None and budget_departments_defined(db):
                raise ValueError("Choose a department from User Management.")
            department = department_row["name"] if department_row else department[:120]
            department_id = department_row["id"] if department_row else None
            if currency not in BUDGET_CURRENCIES:
                raise ValueError("Choose a supported currency.")
            amount = budget_amount(request.form.get("requested_amount", ""),
                                   "Requested amount")
            if amount <= 0:
                raise ValueError("The requested amount must be greater than zero.")
            plan_value = request.form.get("plan_id", "").strip()
            plan_id = int(plan_value) if plan_value else None
            if plan_id is not None:
                plan = db.execute("SELECT * FROM budget_plans WHERE id = ?",
                                  (plan_id,)).fetchone()
                if plan is None:
                    raise ValueError("Choose a valid budget plan.")
                if plan["currency"] != currency:
                    raise ValueError("The request currency must match the plan.")
            user = current_user()
            cursor = db.execute("""
                INSERT INTO budget_requests (
                    request_title, department, department_id, justification,
                    requested_amount, currency, status, plan_id, requested_by,
                    requested_by_name
                ) VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)
            """, (title[:180], department, department_id, justification[:3000],
                  str(amount), currency, plan_id, user["id"], user["full_name"]))
            stamp_record(db, "budget_requests", cursor.lastrowid, user)
            notify_permission(
                db, "budget", "Budget request waiting: {}".format(title[:150]),
                "{} · {:,.2f} {} requested by {}.".format(
                    department, float(amount), currency, user["full_name"]),
                url_for("budget.request_list"), category="budget", icon="clock",
                tone="warn", exclude_user_ids=(user["id"],))
            db.commit()
            log_activity("CREATE", "budget",
                         "Budget request '{}' submitted for {} {}".format(
                             title, amount, currency),
                         record_type="budget_request", record_id=cursor.lastrowid)
            flash("Budget request submitted for review.", "success")
            return redirect(url_for("budget.request_list"))
        except ValueError as exc:
            flash(str(exc), "error")
    plans = db.execute("""
        SELECT * FROM budget_plans WHERE status IN ('Active', 'Approved')
        ORDER BY fiscal_year DESC, id DESC
    """).fetchall()
    user = current_user()
    default_department = ""
    if user is not None and user["department_id"]:
        row = db.execute("SELECT name FROM departments WHERE id = ?",
                         (user["department_id"],)).fetchone()
        if row is not None:
            default_department = row["name"]
    return render_template("budget/create_request.html", plans=plans,
                           departments=budget_department_names(db),
                           default_department=default_department)


@budget_bp.route("/requests/<int:request_id>/decision", methods=["POST"])
@perm("budget")
def decide_request(request_id):
    db = get_db()
    row = db.execute("SELECT * FROM budget_requests WHERE id = ?",
                     (request_id,)).fetchone()
    if row is None:
        abort(404)
    decision = request.form.get("decision", "").strip().title()
    comment = request.form.get("comment", "").strip()
    if decision not in ("Approved", "Rejected"):
        flash("The decision must be Approved or Rejected.", "error")
        return redirect(url_for("budget.request_list"))
    if row["status"] != "Pending":
        flash("That request has already been reviewed.", "error")
        return redirect(url_for("budget.request_list"))
    if decision == "Rejected" and not comment:
        flash("A reason is required when rejecting a request.", "error")
        return redirect(url_for("budget.request_list"))
    user = current_user()
    if row["requested_by"] and int(row["requested_by"]) == int(user["id"]):
        flash("A budget request cannot be approved or rejected by the person"
              " who raised it.", "error")
        return redirect(url_for("budget.request_list"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute("""
        UPDATE budget_requests
        SET status = ?, review_comment = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ? AND status = 'Pending'
    """, (decision, comment[:2000], user["full_name"], now, request_id))
    db.execute("""
        INSERT INTO budget_approvals (
            request_id, approver_id, approver_name, decision, comment, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (request_id, user["id"], user["full_name"], decision,
          comment[:2000], now))
    stamp_record(db, "budget_requests", request_id, user)
    if row["requested_by"]:
        deliver_notification(
            db, row["requested_by"],
            "Budget request {}".format(decision.lower()),
            "'{}' was {} by {}.".format(row["request_title"], decision.lower(),
                                        user["full_name"]),
            url_for("budget.request_list"), category="budget",
            icon="check" if decision == "Approved" else "warn",
            tone="ok" if decision == "Approved" else "bad")
    db.commit()
    log_activity("UPDATE", "budget",
                 "Budget request #{} {}".format(request_id, decision.lower()),
                 record_type="budget_request", record_id=request_id,
                 details={"comment": comment[:500]})
    flash("Request {}.".format(decision.lower()), "success")
    return redirect(url_for("budget.request_list"))


@budget_bp.route("/expenditures")
@perm("budget")
def expenditures():
    rows = get_db().execute("""
        SELECT e.*, a.allocation_name, a.department,
               COALESCE(NULLIF(e.recorded_by_name, ''), u.full_name)
                 AS recorded_by_display,
               i.invoice_number AS finance_invoice_number
        FROM budget_expenditures AS e
        LEFT JOIN budget_allocations AS a ON a.id = e.allocation_id
        LEFT JOIN users AS u ON u.id = e.recorded_by
        LEFT JOIN finance.finance_invoices AS i ON i.id = e.finance_invoice_id
        ORDER BY e.id DESC LIMIT 400
    """).fetchall()
    return render_template("budget/expenditures.html", expenditures=rows)


@budget_bp.route("/expenditures/create", methods=["GET", "POST"])
@perm("budget")
def create_expenditure():
    db = get_db()
    if request.method == "POST":
        try:
            try:
                allocation_id = int(request.form.get("allocation_id", ""))
            except (TypeError, ValueError):
                raise ValueError("Choose the allocation that covers this expense.")
            description = request.form.get("description", "").strip()
            if not description:
                raise ValueError("An expense description is required.")
            currency = request.form.get("currency", "ETB").upper()
            if currency not in BUDGET_CURRENCIES:
                raise ValueError("Choose a supported currency.")
            amount = budget_amount(request.form.get("amount", ""), "Expense amount")
            if amount <= 0:
                raise ValueError("The expense amount must be greater than zero.")
            allocation = budget_allocation(db, allocation_id)
            if allocation is None:
                raise ValueError("Choose a valid allocation.")
            if allocation["currency"] != currency:
                raise ValueError("The expense currency must match the allocation.")
            problem = budget_problem_for(db, allocation_id, amount)
            if problem:
                raise ValueError(problem)
            user = current_user()
            cursor = db.execute("""
                INSERT INTO budget_expenditures (
                    allocation_id, description, amount, currency, reference,
                    recorded_by, recorded_by_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (allocation_id, description[:1000], str(amount), currency,
                  request.form.get("reference", "").strip()[:100],
                  user["id"], user["full_name"]))
            stamp_record(db, "budget_expenditures", cursor.lastrowid, user)
            try:
                budget_post_finance_expenditure(
                    db, cursor.lastrowid, user["id"],
                    request.form.get("exchange_rate", "").strip() or None)
            except ValueError:
                db.rollback()
                raise
            db.commit()
            log_activity("CREATE", "budget",
                         "Expenditure of {} {} recorded against '{}'".format(
                             amount, currency, allocation["allocation_name"]),
                         record_type="budget_expenditure",
                         record_id=cursor.lastrowid,
                         details={"allocation_id": allocation_id})
            flash("Expenditure recorded and posted to Finance.", "success")
            return redirect(url_for("budget.expenditures"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("budget/create_expenditure.html",
                           allocations=budget_spendable_allocations(db))


@budget_bp.route("/reports")
@perm("budget")
def reports():
    db = get_db()
    fiscal_years = [row["fiscal_year"] for row in db.execute("""
        SELECT DISTINCT fiscal_year FROM budget_plans ORDER BY fiscal_year DESC
    """)]
    selected_fiscal_year = request.args.get("fiscal_year", "").strip()
    if selected_fiscal_year and selected_fiscal_year.isdigit():
        selected_fiscal_year = int(selected_fiscal_year)
    elif selected_fiscal_year:
        selected_fiscal_year = None
    selected_currency = request.args.get("currency", "ETB").upper()
    if selected_currency not in BUDGET_CURRENCIES:
        selected_currency = "ETB"

    plan_filter = " WHERE currency = ?"
    params = [selected_currency]
    if selected_fiscal_year:
        plan_filter += " AND fiscal_year = ?"
        params.append(selected_fiscal_year)
    plans = db.execute("SELECT * FROM budget_plans" + plan_filter, params).fetchall()
    plan_ids = [row["id"] for row in plans]
    if plan_ids:
        placeholders = ",".join("?" for _ in plan_ids)
        report_plans = db.execute("""
            SELECT p.*,
                   (SELECT COALESCE(SUM(a.allocated_amount), 0)
                      FROM budget_allocations a WHERE a.plan_id = p.id)
                     AS allocated_amount,
                   (SELECT COALESCE(SUM(e.amount), 0)
                      FROM budget_expenditures e
                      JOIN budget_allocations a ON a.id = e.allocation_id
                     WHERE a.plan_id = p.id) AS spent_amount
            FROM budget_plans AS p
            WHERE p.id IN ({}) ORDER BY p.fiscal_year DESC, p.id DESC
        """.format(placeholders), plan_ids).fetchall()
        allocated_total = float(db.execute("""
            SELECT COALESCE(SUM(allocated_amount), 0) AS total
            FROM budget_allocations WHERE plan_id IN ({})
        """.format(placeholders), plan_ids).fetchone()["total"])
        spent_total = float(db.execute("""
            SELECT COALESCE(SUM(e.amount), 0) AS total
            FROM budget_expenditures e
            JOIN budget_allocations a ON a.id = e.allocation_id
            WHERE a.plan_id IN ({})
        """.format(placeholders), plan_ids).fetchone()["total"])
        allocation_count = db.execute("""
            SELECT COUNT(*) AS n FROM budget_allocations
            WHERE plan_id IN ({})
        """.format(placeholders), plan_ids).fetchone()["n"]
        expenditure_count = db.execute("""
            SELECT COUNT(*) AS n FROM budget_expenditures e
            JOIN budget_allocations a ON a.id = e.allocation_id
            WHERE a.plan_id IN ({})
        """.format(placeholders), plan_ids).fetchone()["n"]
    else:
        report_plans = []
        allocated_total = spent_total = 0.0
        allocation_count = expenditure_count = 0
    total_budget = float(sum(float(row["total_budget"] or 0) for row in plans))
    pending_request_count = db.execute(
        "SELECT COUNT(*) AS n FROM budget_requests WHERE status = 'Pending'"
    ).fetchone()["n"]
    utilization = round((spent_total / total_budget * 100) if total_budget else 0, 1)
    return render_template(
        "budget/reports.html", fiscal_years=fiscal_years,
        selected_fiscal_year=selected_fiscal_year,
        selected_currency=selected_currency, total_budget=total_budget,
        total_allocated=allocated_total, total_spent=spent_total,
        remaining_budget=total_budget - spent_total, utilization=utilization,
        utilization_width=min(utilization, 100),
        report_plans=report_plans, plan_count=len(plans),
        allocation_count=allocation_count,
        expenditure_count=expenditure_count,
        pending_request_count=pending_request_count)


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(budget_bp)
