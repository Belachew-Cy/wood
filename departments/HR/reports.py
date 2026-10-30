"""HR reports, leave review and training performance."""

from core.access import perm
from core.db import get_db
from core.routing import route
from datetime import date
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from services.audit import log_activity
from services.notifications import deliver_notification


@route("/admin/hr/reports", methods=["GET"])
@perm("hr")
def hr_reports():
    db = get_db()

    # Default reporting period: current month
    today = date.today()
    start_date = request.args.get(
        "start_date", today.replace(day=1).isoformat()
    )
    end_date = request.args.get("end_date", today.isoformat())

    # Validate date filters
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        if start > end:
            raise ValueError("Start date must not be after end date.")

    except ValueError:
        flash("Please select a valid date range.", "error")
        return redirect(url_for("hr_reports"))

    # Employee overview
    employee_stats = db.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN employment_status = 'Active'
                THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN employment_status != 'Active'
                THEN 1 ELSE 0 END) AS other_status
        FROM employees
    """).fetchone()

    # Employees hired during the selected period
    new_hires = db.execute("""
        SELECT id, employee_code, full_name, department,
               job_title, hire_date, employment_status
        FROM employees
        WHERE hire_date >= ? AND hire_date <= ?
        ORDER BY hire_date DESC
    """, (start_date, end_date)).fetchall()

    # Attendance overview
    attendance_stats = db.execute("""
        SELECT
            COUNT(*) AS total_records,
            SUM(CASE WHEN status = 'Present'
                THEN 1 ELSE 0 END) AS present,
            SUM(CASE WHEN status = 'Absent'
                THEN 1 ELSE 0 END) AS absent,
            SUM(CASE WHEN status = 'Late'
                THEN 1 ELSE 0 END) AS late,
            SUM(CASE WHEN status = 'Half Day'
                THEN 1 ELSE 0 END) AS half_day
        FROM attendance
        WHERE attendance_date BETWEEN ? AND ?
    """, (start_date, end_date)).fetchone()

    attendance_by_department = db.execute("""
        SELECT
            e.department,
            COUNT(a.id) AS total_records,
            SUM(CASE WHEN a.status = 'Present'
                THEN 1 ELSE 0 END) AS present,
            SUM(CASE WHEN a.status = 'Absent'
                THEN 1 ELSE 0 END) AS absent,
            SUM(CASE WHEN a.status = 'Late'
                THEN 1 ELSE 0 END) AS late
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        WHERE a.attendance_date BETWEEN ? AND ?
        GROUP BY e.department
        ORDER BY e.department
    """, (start_date, end_date)).fetchall()

    # Payroll summary: payroll_month is stored as YYYY-MM
    start_month = start_date[:7]
    end_month = end_date[:7]

    payroll_stats = db.execute("""
        SELECT
            COUNT(*) AS records,
            COALESCE(SUM(net_salary), 0) AS total_net,
            COALESCE(SUM(
                CASE WHEN payment_status = 'Paid'
                     THEN net_salary ELSE 0 END
            ), 0) AS paid_amount,
            COALESCE(SUM(
                CASE WHEN payment_status = 'Pending'
                     THEN net_salary ELSE 0 END
            ), 0) AS pending_amount,
            SUM(CASE WHEN payment_status = 'Paid'
                THEN 1 ELSE 0 END) AS paid_count,
            SUM(CASE WHEN payment_status = 'Pending'
                THEN 1 ELSE 0 END) AS pending_count
        FROM payroll
        WHERE payroll_month BETWEEN ? AND ?
    """, (start_month, end_month)).fetchone()

    payroll_by_month = db.execute("""
        SELECT
            payroll_month,
            COUNT(*) AS employee_count,
            COALESCE(SUM(net_salary), 0) AS total_net,
            COALESCE(SUM(
                CASE WHEN payment_status = 'Paid'
                     THEN net_salary ELSE 0 END
            ), 0) AS paid_amount,
            COALESCE(SUM(
                CASE WHEN payment_status = 'Pending'
                     THEN net_salary ELSE 0 END
            ), 0) AS pending_amount
        FROM payroll
        WHERE payroll_month BETWEEN ? AND ?
        GROUP BY payroll_month
        ORDER BY payroll_month DESC
    """, (start_month, end_month)).fetchall()

    # Recruitment summary
    recruitment_stats = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM job_vacancies
             WHERE status = 'Open') AS open_vacancies,
            (SELECT COUNT(*) FROM job_applications
             WHERE date(applied_at) BETWEEN ? AND ?) AS applicants,
            (SELECT COUNT(*) FROM job_applications
             WHERE status = 'Hired'
             AND date(applied_at) BETWEEN ? AND ?) AS hired,
            (SELECT COUNT(*) FROM job_applications
             WHERE status = 'Shortlisted'
             AND date(applied_at) BETWEEN ? AND ?) AS shortlisted
    """, (
        start_date, end_date,
        start_date, end_date,
        start_date, end_date
    )).fetchone()

    return render_template(
        "HR/hr_reports.html",
        start_date=start_date,
        end_date=end_date,
        employee_stats=employee_stats,
        new_hires=new_hires,
        attendance_stats=attendance_stats,
        attendance_by_department=attendance_by_department,
        payroll_stats=payroll_stats,
        payroll_by_month=payroll_by_month,
        recruitment_stats=recruitment_stats
    )


@route("/admin/hr/leave", methods=["GET", "POST"])
@perm("hr")
def leave_management():
    db = get_db()

    if request.method == "POST":
        try:
            employee_id = int(request.form.get("employee_id", ""))
            leave_type = request.form.get("leave_type", "").strip()
            start_date = date.fromisoformat(
                request.form.get("start_date", "")
            )
            end_date = date.fromisoformat(
                request.form.get("end_date", "")
            )
            reason = request.form.get("reason", "").strip()

            allowed_types = {
                "Annual", "Sick", "Maternity", "Paternity",
                "Unpaid", "Emergency", "Other"
            }

            employee = db.execute(
                "SELECT id FROM employees WHERE id = ?",
                (employee_id,)
            ).fetchone()

            if not employee:
                raise ValueError("Please select a valid employee.")

            if leave_type not in allowed_types:
                raise ValueError("Please select a valid leave type.")

            if end_date < start_date:
                raise ValueError("End date cannot be before start date.")

            if not reason:
                raise ValueError("Please enter the reason for leave.")

            # Calendar days; weekends and holidays are included.
            days_requested = (end_date - start_date).days + 1

            cursor = db.execute("""
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date,
                    days_requested, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                employee_id, leave_type, start_date.isoformat(),
                end_date.isoformat(), days_requested, reason
            ))

            db.commit()

            log_activity(
                "CREATE", "hr",
                "Leave request submitted for employee #{}".format(employee_id),
                record_type="leave_request",
                record_id=cursor.lastrowid
            )
            flash("Leave request submitted successfully.", "success")

        except (ValueError, TypeError):
            db.rollback()
            flash(
                "Check the employee, leave type, dates, and reason.",
                "error"
            )

        return redirect(url_for("leave_management"))

    employees = db.execute("""
        SELECT id, employee_code, full_name, department
        FROM employees
        WHERE employment_status = 'Active'
        ORDER BY full_name
    """).fetchall()

    requests_list = db.execute("""
        SELECT l.*, e.full_name, e.employee_code, e.department,
               COALESCE(reviewer.full_name, l.reviewed_by) AS reviewed_by_name
        FROM leave_requests AS l
        JOIN employees AS e ON e.id = l.employee_id
        LEFT JOIN users AS reviewer
            ON CAST(reviewer.id AS TEXT) = l.reviewed_by
        ORDER BY
            CASE l.status
                WHEN 'Pending' THEN 0
                ELSE 1
            END,
            l.created_at DESC
    """).fetchall()

    stats = {
        "total": db.execute(
            "SELECT COUNT(*) AS n FROM leave_requests"
        ).fetchone()["n"],
        "pending": db.execute(
            "SELECT COUNT(*) AS n FROM leave_requests WHERE status='Pending'"
        ).fetchone()["n"],
        "approved": db.execute(
            "SELECT COUNT(*) AS n FROM leave_requests WHERE status='Approved'"
        ).fetchone()["n"],
        "rejected": db.execute(
            "SELECT COUNT(*) AS n FROM leave_requests WHERE status='Rejected'"
        ).fetchone()["n"],
    }

    return render_template(
        "HR/leave_management.html",
        employees=employees,
        requests=requests_list,
        stats=stats,
        today=date.today().isoformat()
    )


@route(
    "/admin/hr/leave/<int:leave_id>/review",
    methods=["POST"]
)
@perm("hr")
def review_leave_request(leave_id):
    status = request.form.get("status", "").strip()
    comment = request.form.get("review_comment", "").strip()

    if status not in {"Approved", "Rejected"}:
        flash("Choose Approved or Rejected.", "error")
        return redirect(url_for("leave_management"))

    db = get_db()

    leave = db.execute("""
        SELECT id, employee_id, status
        FROM leave_requests
        WHERE id = ?
    """, (leave_id,)).fetchone()

    if not leave:
        flash("Leave request not found.", "error")
        return redirect(url_for("leave_management"))

    if leave["status"] != "Pending":
        flash("This leave request has already been reviewed.", "error")
        return redirect(url_for("leave_management"))

    reviewer_row = db.execute(
        "SELECT full_name FROM users WHERE id = ?",
        (session.get("user_id"),)
    ).fetchone()
    reviewer = (reviewer_row["full_name"] if reviewer_row else "") or "HR"

    db.execute("""
        UPDATE leave_requests
        SET status = ?, reviewed_by = ?, review_comment = ?
        WHERE id = ? AND status = 'Pending'
    """, (status, reviewer, comment, leave_id))

    account = db.execute(
        "SELECT id, full_name FROM users WHERE employee_id = ? LIMIT 1",
        (leave["employee_id"],)
    ).fetchone()
    if account is not None:
        deliver_notification(
            db, account["id"], "Leave request {}".format(status.lower()),
            "Your leave request #{} was {} by {}.".format(
                leave_id, status.lower(), reviewer),
            url_for("notification_center"), category="hr",
            icon="calendar" if status == "Approved" else "warn",
            tone="ok" if status == "Approved" else "bad")

    db.commit()

    log_activity(
        "UPDATE", "hr",
        "Leave request #{} {}".format(leave_id, status.lower()),
        record_type="leave_request",
        record_id=leave_id
    )

    flash("Leave request {}.".format(status.lower()), "success")
    return redirect(url_for("leave_management"))


@route(
    "/admin/hr/training-performance",
    methods=["GET", "POST"]
)
@perm("hr")
def training_performance():
    db = get_db()

    if request.method == "POST":
        form_type = request.form.get("form_type", "").strip()

        try:
            employee_id = int(request.form.get("employee_id", ""))

            employee = db.execute(
                "SELECT id FROM employees WHERE id = ?",
                (employee_id,)
            ).fetchone()

            if not employee:
                raise ValueError("Please select a valid employee.")

            if form_type == "training":
                course_title = request.form.get(
                    "course_title", ""
                ).strip()
                provider = request.form.get("provider", "").strip()
                start_date = date.fromisoformat(
                    request.form.get("training_start", "")
                )
                end_raw = request.form.get("training_end", "").strip()
                end_date = date.fromisoformat(end_raw) if end_raw else None
                status = request.form.get("training_status", "Planned")
                certificate = request.form.get("certificate", "").strip()
                notes = request.form.get("training_notes", "").strip()

                if not course_title:
                    raise ValueError("Course title is required.")

                if end_date and end_date < start_date:
                    raise ValueError("Training end date cannot precede start date.")

                if status not in {
                    "Planned", "In Progress", "Completed", "Cancelled"
                }:
                    raise ValueError("Invalid training status.")

                cursor = db.execute("""
                    INSERT INTO employee_training (
                        employee_id, course_title, provider, start_date,
                        end_date, status, certificate, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    employee_id, course_title, provider,
                    start_date.isoformat(),
                    end_date.isoformat() if end_date else None,
                    status, certificate, notes
                ))

                record_type = "employee_training"
                message = "Training record saved successfully."

            elif form_type == "performance":
                review_date = date.fromisoformat(
                    request.form.get("review_date", "")
                )
                review_period = request.form.get(
                    "review_period", ""
                ).strip()
                rating = int(request.form.get("rating", ""))
                strengths = request.form.get("strengths", "").strip()
                improvements = request.form.get("improvements", "").strip()
                goals = request.form.get("goals", "").strip()
                reviewer = request.form.get("reviewer", "").strip()

                if not review_period or not reviewer:
                    raise ValueError("Review period and reviewer are required.")

                if rating not in range(1, 6):
                    raise ValueError("Rating must be between 1 and 5.")

                cursor = db.execute("""
                    INSERT INTO performance_reviews (
                        employee_id, review_date, review_period, rating,
                        strengths, improvements, goals, reviewer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    employee_id, review_date.isoformat(), review_period,
                    rating, strengths, improvements, goals, reviewer
                ))

                record_type = "performance_review"
                message = "Performance review saved successfully."

            else:
                raise ValueError("Choose a valid record type.")

            db.commit()

            log_activity(
                "CREATE", "hr", message,
                record_type=record_type,
                record_id=cursor.lastrowid
            )
            flash(message, "success")

        except (ValueError, TypeError):
            db.rollback()
            flash(
                "Please check the employee and all required fields.",
                "error"
            )

        return redirect(url_for("training_performance"))

    employees = db.execute("""
        SELECT id, employee_code, full_name, department, job_title
        FROM employees
        ORDER BY full_name
    """).fetchall()

    training_records = db.execute("""
        SELECT t.*, e.full_name, e.employee_code
        FROM employee_training AS t
        JOIN employees AS e ON e.id = t.employee_id
        ORDER BY t.start_date DESC, t.id DESC
    """).fetchall()

    performance_records = db.execute("""
        SELECT p.*, e.full_name, e.employee_code
        FROM performance_reviews AS p
        JOIN employees AS e ON e.id = p.employee_id
        ORDER BY p.review_date DESC, p.id DESC
    """).fetchall()

    stats = {
        "training": db.execute(
            "SELECT COUNT(*) AS n FROM employee_training"
        ).fetchone()["n"],
        "completed": db.execute("""
            SELECT COUNT(*) AS n FROM employee_training
            WHERE status = 'Completed'
        """).fetchone()["n"],
        "reviews": db.execute(
            "SELECT COUNT(*) AS n FROM performance_reviews"
        ).fetchone()["n"],
        "average_rating": db.execute("""
            SELECT ROUND(AVG(rating), 1) AS average
            FROM performance_reviews
        """).fetchone()["average"] or 0,
    }

    return render_template(
        "HR/training_performance.html",
        employees=employees,
        training_records=training_records,
        performance_records=performance_records,
        stats=stats,
        today=date.today().isoformat()
    )
