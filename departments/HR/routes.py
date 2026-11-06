"""HR overtime: submission, manager decision, HR verification and reports."""

from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
import sqlite3

from flask import Blueprint
from flask import current_app
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for

from core.access import current_user
from core.db import get_db
from services.audit import log_activity
from services.notifications import deliver_notification

from .overtime_services import calculate_overtime_hours
from .permission import require_hr_permission
from .services import get_overtime_audit_logs
from .services import get_overtime_payroll_cost_report
from .services import get_overtime_report
from .services import log_overtime_action
from .services import review_overtime_request
from .services import verify_overtime_request


hr_overtime_bp = Blueprint("hr", __name__, url_prefix="/admin/hr")

OVERTIME_SETTING_DEFAULTS = {
    "overtime_enabled": "1",
    "overtime_multiplier": "1.5",
}

PENDING_STATUSES = ("Pending Manager", "Pending HR")


def _current_employee(db):
    """The employee record behind the signed-in account, if there is one."""
    user = current_user()
    if user is None:
        return None, None

    employee = db.execute("""
        SELECT *
        FROM employees
        WHERE user_id = ?
        ORDER BY id
        LIMIT 1
    """, (user["id"],)).fetchone()

    if employee is None:
        linked = db.execute("""
            SELECT employee_id
            FROM users
            WHERE id = ?
        """, (user["id"],)).fetchone()
        if linked and linked["employee_id"]:
            employee = db.execute("""
                SELECT *
                FROM employees
                WHERE id = ?
            """, (linked["employee_id"],)).fetchone()

    return user, employee


def _overtime_settings(db):
    settings = dict(OVERTIME_SETTING_DEFAULTS)
    try:
        rows = db.execute("""
            SELECT setting_key, setting_value
            FROM hr_settings
            WHERE setting_key IN ('overtime_enabled', 'overtime_multiplier')
        """).fetchall()
    except sqlite3.Error:
        rows = []
    for row in rows:
        if row["setting_value"]:
            settings[row["setting_key"]] = row["setting_value"]
    return settings


def _account_for_employee(db, employee_id):
    row = db.execute("""
        SELECT user_id
        FROM employees
        WHERE id = ?
    """, (employee_id,)).fetchone()
    if row and row["user_id"]:
        return row["user_id"]
    row = db.execute("""
        SELECT id
        FROM users
        WHERE employee_id = ?
        ORDER BY id
        LIMIT 1
    """, (employee_id,)).fetchone()
    return row["id"] if row else None


def _notify_employee(db, employee_id, title, body, tone="neutral"):
    user_id = _account_for_employee(db, employee_id)
    if not user_id:
        return
    deliver_notification(
        db,
        user_id,
        title,
        body,
        url_for("hr.overtime_dashboard"),
        category="hr",
        icon="clock",
        tone=tone,
    )


@hr_overtime_bp.route("/overtime", methods=["GET"])
@require_hr_permission("hr_overtime_view")
def overtime_dashboard():
    db = get_db()
    user, employee = _current_employee(db)
    settings = _overtime_settings(db)

    query = """
        SELECT
            r.id, r.overtime_date, r.start_time, r.end_time,
            r.break_minutes, r.requested_hours, r.approved_hours,
            r.multiplier, r.reason, r.status,
            r.manager_comment, r.hr_comment,
            e.full_name, e.employee_code, e.department
        FROM hr_overtime_requests r
        JOIN employees e ON e.id = r.employee_id
    """

    if employee is not None:
        requests_list = db.execute(
            query + """
            WHERE r.employee_id = ?
            ORDER BY r.overtime_date DESC, r.id DESC
            """,
            (employee["id"],),
        ).fetchall()
    elif user["role"] == "admin":
        requests_list = db.execute(
            query + """
            ORDER BY r.overtime_date DESC, r.id DESC
            LIMIT 100
            """
        ).fetchall()
    else:
        requests_list = []

    summary = {
        "total": len(requests_list),
        "pending": sum(
            row["status"] in PENDING_STATUSES for row in requests_list
        ),
        "approved": sum(
            row["status"] == "Approved" for row in requests_list
        ),
        "approved_hours": sum(
            float(row["approved_hours"] or 0)
            for row in requests_list
            if row["status"] == "Approved"
        ),
    }

    return render_template(
        "HR/overtime_dashboard.html",
        overtime_requests=requests_list,
        employee=employee,
        summary=summary,
        overtime_enabled=settings["overtime_enabled"] == "1",
    )


@hr_overtime_bp.route("/overtime/request", methods=["POST"])
@require_hr_permission("hr_overtime_create")
def submit_overtime_request():
    db = get_db()
    user, employee = _current_employee(db)

    if employee is None:
        flash(
            "Your account is not linked to an employee record, so overtime "
            "cannot be requested.",
            "warning",
        )
        return redirect(url_for("hr.overtime_dashboard"))

    settings = _overtime_settings(db)
    if settings["overtime_enabled"] != "1":
        flash("Overtime requests are currently disabled by HR.", "warning")
        return redirect(url_for("hr.overtime_dashboard"))

    overtime_date = request.form.get("overtime_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    reason = request.form.get("reason", "").strip()

    try:
        break_minutes = int(request.form.get("break_minutes", "0") or 0)
        day = date.fromisoformat(overtime_date)

        if day > date.today():
            raise ValueError(
                "Overtime cannot be requested for a future date."
            )

        if not 0 <= break_minutes <= 720:
            raise ValueError("Break minutes must be between 0 and 720.")

        if len(reason) < 5:
            raise ValueError(
                "Please provide a clear reason for the overtime."
            )

        requested_hours = calculate_overtime_hours(
            start_time,
            end_time,
            break_minutes,
        )

        if requested_hours <= 0:
            raise ValueError("Overtime hours must be greater than zero.")

        multiplier = Decimal(settings["overtime_multiplier"])
        if multiplier <= 0:
            multiplier = Decimal("1.5")

    except (ValueError, TypeError, InvalidOperation) as exc:
        flash(str(exc) or "Please enter valid overtime details.", "danger")
        return redirect(url_for("hr.overtime_dashboard"))

    existing = db.execute("""
        SELECT id
        FROM hr_overtime_requests
        WHERE employee_id = ?
          AND overtime_date = ?
          AND status != 'Rejected'
        LIMIT 1
    """, (employee["id"], overtime_date)).fetchone()

    if existing:
        flash(
            "An overtime request already exists for that date.",
            "warning",
        )
        return redirect(url_for("hr.overtime_dashboard"))

    cursor = db.execute("""
        INSERT INTO hr_overtime_requests (
            employee_id, overtime_date, start_time, end_time,
            break_minutes, requested_hours, approved_hours,
            multiplier, reason, status, created_by,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 'Pending Manager', ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        employee["id"],
        overtime_date,
        start_time,
        end_time,
        break_minutes,
        str(requested_hours),
        str(multiplier),
        reason,
        user["id"],
    ))

    request_id = cursor.lastrowid
    log_overtime_action(
        db,
        request_id,
        "Submitted",
        None,
        "Pending Manager",
        user["id"],
        reason,
    )
    db.commit()

    log_activity(
        "CREATE",
        "hr",
        "Overtime requested for {} ({} hours)".format(
            overtime_date, requested_hours
        ),
        record_type="overtime_request",
        record_id=request_id,
    )

    if employee["manager_id"]:
        manager_user = _account_for_employee(db, employee["manager_id"])
        if manager_user:
            deliver_notification(
                db,
                manager_user,
                "Overtime request awaiting your approval",
                "{} requested {} hours on {}.".format(
                    employee["full_name"], requested_hours, overtime_date
                ),
                url_for("hr.overtime_approvals"),
                category="hr",
                icon="clock",
                tone="warning",
                dedupe_key="overtime-manager-{}".format(request_id),
            )

    flash(
        "Your overtime request was submitted for manager approval.",
        "success",
    )
    return redirect(url_for("hr.overtime_dashboard"))


@hr_overtime_bp.route("/overtime/approvals", methods=["GET"])
@require_hr_permission("hr_overtime_approve")
def overtime_approvals():
    db = get_db()
    user, employee = _current_employee(db)

    query = """
        SELECT
            r.id, r.overtime_date, r.start_time, r.end_time,
            r.break_minutes, r.requested_hours, r.reason, r.status,
            e.full_name, e.employee_code, e.department
        FROM hr_overtime_requests r
        JOIN employees e ON e.id = r.employee_id
        WHERE r.status = 'Pending Manager'
    """

    if user["role"] == "admin":
        requests_list = db.execute(
            query + " ORDER BY r.overtime_date DESC, r.id DESC"
        ).fetchall()
    elif employee is not None:
        requests_list = db.execute(
            query + """
              AND (e.manager_id = ? OR e.department = ?)
            ORDER BY r.overtime_date DESC, r.id DESC
            """,
            (employee["id"], employee["department"]),
        ).fetchall()
    else:
        requests_list = []
        flash(
            "Your account is not linked to an employee record, so there is "
            "no department queue to review.",
            "warning",
        )

    return render_template(
        "HR/overtime_approvals.html",
        overtime_requests=requests_list,
    )


@hr_overtime_bp.route(
    "/overtime/approve/<int:request_id>",
    methods=["POST"],
)
@require_hr_permission("hr_overtime_approve")
def approve_overtime(request_id):
    db = get_db()
    user, employee = _current_employee(db)

    if employee is None:
        flash(
            "Your account is not linked to an employee record, so it cannot "
            "decide overtime requests.",
            "warning",
        )
        return redirect(url_for("hr.overtime_approvals"))

    decision = request.form.get("decision", "").strip()
    comment = request.form.get("comment", "").strip()

    try:
        new_status = review_overtime_request(
            db=db,
            request_id=request_id,
            manager_user_id=user["id"],
            manager_employee_id=employee["id"],
            decision=decision,
            comment=comment,
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("hr.overtime_approvals"))

    request_row = db.execute("""
        SELECT employee_id, overtime_date, requested_hours
        FROM hr_overtime_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    log_activity(
        "APPROVE" if decision == "approve" else "REJECT",
        "hr",
        "Overtime request #{} moved to {}".format(request_id, new_status),
        record_type="overtime_request",
        record_id=request_id,
    )

    if request_row:
        if decision == "approve":
            title = "Overtime request approved by your manager"
        else:
            title = "Overtime request rejected by your manager"
        _notify_employee(
            db,
            request_row["employee_id"],
            title,
            "{} - {}. {}".format(
                request_row["overtime_date"],
                request_row["requested_hours"],
                comment,
            ),
            tone="success" if decision == "approve" else "danger",
        )

    flash(
        "Overtime request successfully {}.".format(
            "approved" if decision == "approve" else "rejected"
        ),
        "success",
    )
    return redirect(url_for("hr.overtime_approvals"))


@hr_overtime_bp.route("/overtime/hr-verification", methods=["GET"])
@require_hr_permission("hr_overtime_verify")
def overtime_hr_verification():
    db = get_db()

    pending_requests = db.execute("""
        SELECT
            r.id, r.overtime_date, r.start_time, r.end_time,
            r.break_minutes, r.requested_hours, r.approved_hours,
            r.reason, r.status,
            e.full_name, e.employee_code, e.department,
            a.status AS attendance_status,
            a.check_in, a.check_out
        FROM hr_overtime_requests r
        JOIN employees e ON e.id = r.employee_id
        LEFT JOIN attendance a
            ON a.employee_id = r.employee_id
           AND a.attendance_date = r.overtime_date
        WHERE r.status = 'Pending HR'
        ORDER BY r.overtime_date DESC, r.id DESC
    """).fetchall()

    return render_template(
        "HR/overtime_hr_verification.html",
        overtime_requests=pending_requests,
    )


@hr_overtime_bp.route(
    "/overtime/verify/<int:request_id>",
    methods=["POST"],
)
@require_hr_permission("hr_overtime_verify")
def verify_overtime(request_id):
    db = get_db()
    user = current_user()
    verified_hours = request.form.get("verified_hours", "").strip()
    hr_comment = request.form.get("hr_comment", "").strip()

    try:
        verify_overtime_request(
            db=db,
            request_id=request_id,
            hr_user_id=user["id"],
            verified_hours=verified_hours,
            hr_comment=hr_comment,
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("hr.overtime_hr_verification"))
    except Exception:
        db.rollback()
        current_app.logger.exception(
            "Unexpected error during overtime verification"
        )
        flash(
            "Unable to verify this overtime request. Please try again.",
            "danger",
        )
        return redirect(url_for("hr.overtime_hr_verification"))

    request_row = db.execute("""
        SELECT employee_id, overtime_date, approved_hours
        FROM hr_overtime_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    log_activity(
        "VERIFY",
        "hr",
        "Overtime request #{} verified at {} hours".format(
            request_id, verified_hours
        ),
        record_type="overtime_request",
        record_id=request_id,
    )

    if request_row:
        _notify_employee(
            db,
            request_row["employee_id"],
            "Overtime accepted by HR",
            "{} - {} hours are ready for payroll.".format(
                request_row["overtime_date"],
                request_row["approved_hours"],
            ),
            tone="success",
        )

    flash(
        "Overtime verified and approved for payroll.",
        "success",
    )
    return redirect(url_for("hr.overtime_hr_verification"))


@hr_overtime_bp.route("/overtime/reports", methods=["GET"])
@require_hr_permission("hr_overtime_view")
def overtime_reports():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    if not start_date:
        start_date = date.today().replace(day=1).isoformat()
    if not end_date:
        end_date = date.today().isoformat()

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError("Start date cannot be after end date.")
    except ValueError:
        flash("Please select a valid date range.", "warning")
        return redirect(url_for("hr.overtime_reports"))

    db = get_db()
    report = get_overtime_report(db, start_date, end_date)
    payroll_costs = get_overtime_payroll_cost_report(
        db, start_date, end_date
    )

    return render_template(
        "HR/overtime_reports.html",
        report=report,
        payroll_costs=payroll_costs,
        start_date=start_date,
        end_date=end_date,
    )


@hr_overtime_bp.route("/overtime/audit", methods=["GET"])
@require_hr_permission("hr_overtime_view")
def overtime_audit():
    db = get_db()
    logs = get_overtime_audit_logs(db)
    return render_template("HR/overtime_audit.html", logs=logs)
