"""HR overtime decisions, attendance-checked verification and reports.

The request row keeps one current state; every transition is also appended to
``hr_overtime_audit_logs`` in the same transaction so the audit page can show
who moved the request and when. Payroll reads only rows whose status is
``Approved`` and which have not been imported yet.
"""

from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
import sqlite3


STATUS_MANAGER = "Pending Manager"
STATUS_HR = "Pending HR"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"


def _decided_at():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_overtime_action(db, request_id, action, previous_status,
                        new_status, performed_by, comment=""):
    db.execute("""
        INSERT INTO hr_overtime_audit_logs (
            overtime_request_id, action, previous_status,
            new_status, performed_by, comment
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        request_id,
        action,
        previous_status,
        new_status,
        performed_by,
        (comment or "")[:1000],
    ))


def review_overtime_request(db, request_id, manager_user_id,
                            manager_employee_id, decision, comment):
    """Move a pending request to HR (approve) or Rejected (reject)."""
    decision = (decision or "").strip().lower()
    comment = (comment or "").strip()

    if decision not in ("approve", "reject"):
        raise ValueError("Invalid approval decision.")

    if not comment:
        raise ValueError("Please provide an approval comment.")

    overtime = db.execute("""
        SELECT r.id, r.employee_id, r.requested_hours, r.status,
               e.department, e.manager_id
        FROM hr_overtime_requests r
        JOIN employees e ON e.id = r.employee_id
        WHERE r.id = ?
    """, (request_id,)).fetchone()

    if not overtime:
        raise ValueError("Overtime request not found.")

    if overtime["status"] != STATUS_MANAGER:
        raise ValueError("This request has already been reviewed.")

    manager = db.execute("""
        SELECT id, department
        FROM employees
        WHERE id = ?
    """, (manager_employee_id,)).fetchone()

    if not manager:
        raise PermissionError("Manager employee record not found.")

    assigned_manager = overtime["manager_id"] == manager["id"]
    same_department = manager["department"] == overtime["department"]

    if not (assigned_manager or same_department):
        raise PermissionError(
            "You cannot review overtime outside your department."
        )

    if decision == "approve":
        new_status = STATUS_HR
        approved_hours = overtime["requested_hours"]
        action = "Manager Approval"
    else:
        new_status = STATUS_REJECTED
        approved_hours = 0
        action = "Manager Rejection"

    cursor = db.execute("""
        UPDATE hr_overtime_requests
        SET status = ?,
            approved_hours = ?,
            manager_id = ?,
            manager_comment = ?,
            manager_decided_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status = ?
    """, (
        new_status,
        approved_hours,
        manager["id"],
        comment,
        _decided_at(),
        request_id,
        STATUS_MANAGER,
    ))

    if cursor.rowcount != 1:
        db.rollback()
        raise ValueError("The request was already reviewed.")

    log_overtime_action(
        db,
        request_id,
        action,
        STATUS_MANAGER,
        new_status,
        manager_user_id,
        comment,
    )
    db.commit()
    return new_status


def verify_overtime_request(db, request_id, hr_user_id,
                            verified_hours, hr_comment):
    """Approve a manager-approved request once attendance backs it up."""
    comment = (hr_comment or "").strip()

    if not comment:
        raise ValueError("HR verification comment is required.")

    try:
        hours = Decimal(str(verified_hours))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid verified overtime amount.")

    if not hours.is_finite() or hours <= 0:
        raise ValueError("Verified hours must be greater than zero.")

    hours = hours.quantize(Decimal("0.01"))

    overtime = db.execute("""
        SELECT id, employee_id, overtime_date, requested_hours,
               approved_hours, status
        FROM hr_overtime_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    if not overtime:
        raise ValueError("Overtime request not found.")

    if overtime["status"] != STATUS_HR:
        raise ValueError(
            "Only manager-approved requests can be verified."
        )

    if hours > Decimal(str(overtime["approved_hours"])):
        raise ValueError(
            "Verified hours cannot exceed manager-approved hours."
        )

    if hours > Decimal(str(overtime["requested_hours"])):
        raise ValueError("Verified hours cannot exceed requested hours.")

    attendance = db.execute("""
        SELECT id, status
        FROM attendance
        WHERE employee_id = ?
          AND attendance_date = ?
    """, (
        overtime["employee_id"],
        overtime["overtime_date"],
    )).fetchone()

    if not attendance:
        raise ValueError(
            "No attendance record exists for this employee and date."
        )

    if attendance["status"] == "Absent":
        raise ValueError(
            "Attendance for this date is Absent; overtime cannot be verified."
        )

    cursor = db.execute("""
        UPDATE hr_overtime_requests
        SET approved_hours = ?,
            status = ?,
            attendance_id = ?,
            hr_user_id = ?,
            hr_comment = ?,
            hr_decided_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status = ?
    """, (
        str(hours),
        STATUS_APPROVED,
        attendance["id"],
        hr_user_id,
        comment,
        _decided_at(),
        request_id,
        STATUS_HR,
    ))

    if cursor.rowcount != 1:
        db.rollback()
        raise ValueError("The request was already verified or changed.")

    log_overtime_action(
        db,
        request_id,
        "HR Verification",
        STATUS_HR,
        STATUS_APPROVED,
        hr_user_id,
        comment,
    )
    db.commit()
    return True


def get_overtime_report(db, start_date, end_date):
    """Totals, department split and monthly split for one date range."""
    summary = db.execute("""
        SELECT
            COUNT(*) AS total_requests,
            COALESCE(SUM(requested_hours), 0) AS requested_hours,
            COALESCE(SUM(
                CASE WHEN status = 'Approved'
                    THEN approved_hours ELSE 0 END
            ), 0) AS approved_hours,
            COALESCE(SUM(
                CASE WHEN status = 'Approved'
                    THEN approved_hours ELSE 0 END
            ), 0) AS verified_hours,
            COALESCE(SUM(
                CASE WHEN status IN ('Pending Manager', 'Pending HR')
                    THEN 1 ELSE 0 END
            ), 0) AS pending_requests,
            COALESCE(SUM(
                CASE WHEN status = 'Rejected' THEN 1 ELSE 0 END
            ), 0) AS rejected_requests
        FROM hr_overtime_requests
        WHERE overtime_date BETWEEN ? AND ?
    """, (start_date, end_date)).fetchone()

    departments = db.execute("""
        SELECT
            e.department,
            COUNT(r.id) AS total_requests,
            COALESCE(SUM(r.requested_hours), 0) AS requested_hours,
            COALESCE(SUM(
                CASE WHEN r.status = 'Approved'
                    THEN r.approved_hours ELSE 0 END
            ), 0) AS approved_hours
        FROM hr_overtime_requests r
        JOIN employees e ON e.id = r.employee_id
        WHERE r.overtime_date BETWEEN ? AND ?
        GROUP BY e.department
        ORDER BY approved_hours DESC
    """, (start_date, end_date)).fetchall()

    monthly = db.execute("""
        SELECT
            substr(overtime_date, 1, 7) AS payroll_month,
            COUNT(*) AS total_requests,
            COALESCE(SUM(requested_hours), 0) AS requested_hours,
            COALESCE(SUM(
                CASE WHEN status = 'Approved'
                    THEN approved_hours ELSE 0 END
            ), 0) AS approved_hours
        FROM hr_overtime_requests
        WHERE overtime_date BETWEEN ? AND ?
        GROUP BY substr(overtime_date, 1, 7)
        ORDER BY payroll_month
    """, (start_date, end_date)).fetchall()

    return {
        "summary": summary,
        "departments": departments,
        "monthly": monthly,
    }


def get_overtime_payroll_cost_report(db, start_date, end_date):
    """What the payroll desk actually imported, by month and department."""
    try:
        return db.execute("""
            SELECT
                i.payroll_month,
                e.department,
                COUNT(DISTINCT i.id) AS overtime_requests,
                COALESCE(SUM(i.approved_hours), 0) AS imported_hours,
                COALESCE(SUM(i.amount), 0) AS overtime_cost
            FROM payroll_overtime_imports i
            JOIN hr_overtime_requests r
                ON r.id = i.overtime_request_id
            JOIN employees e
                ON e.id = i.employee_id
            WHERE i.overtime_date BETWEEN ? AND ?
            GROUP BY i.payroll_month, e.department
            ORDER BY i.payroll_month DESC, e.department
        """, (start_date, end_date)).fetchall()
    except sqlite3.Error:
        return []


def get_overtime_audit_logs(db, limit=500):
    return db.execute("""
        SELECT
            a.id,
            a.overtime_request_id,
            a.action,
            a.previous_status,
            a.new_status,
            a.comment,
            a.created_at,
            r.overtime_date,
            e.full_name,
            e.employee_code,
            COALESCE(u.full_name, a.performed_by) AS performed_by
        FROM hr_overtime_audit_logs a
        JOIN hr_overtime_requests r
            ON r.id = a.overtime_request_id
        JOIN employees e
            ON e.id = r.employee_id
        LEFT JOIN users u
            ON u.id = a.performed_by
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
