"""Attendance, payroll runs, statuses and recruitment."""

from core.access import perm
from core.db import get_db
from core.routing import route
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import log_activity
from services.notifications import deliver_notification
import sqlite3


@route("/admin/hr/attendance", methods=["GET", "POST"])
@perm("hr")
def attendance():
    db = get_db()

    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        attendance_date = request.form.get("attendance_date", "").strip()
        status = request.form.get("status", "").strip()
        check_in = request.form.get("check_in", "").strip() or None
        check_out = request.form.get("check_out", "").strip() or None
        notes = request.form.get("notes", "").strip()

        allowed_statuses = {"Present", "Absent", "Late", "Half Day"}

        try:
            employee_id = int(employee_id)
            date.fromisoformat(attendance_date)

            if status not in allowed_statuses:
                raise ValueError("Invalid attendance status")

            employee = db.execute(
                "SELECT id FROM employees WHERE id = ?",
                (employee_id,)
            ).fetchone()

            if employee is None:
                flash("Please select a valid employee.", "error")
                return redirect(url_for("attendance"))

            # Validate time values and their order
            for time_value in (check_in, check_out):
                if time_value:
                    date_time = time_value.split(":")
                    if (
                        len(date_time) != 2
                        or not date_time[0].isdigit()
                        or not date_time[1].isdigit()
                        or not (0 <= int(date_time[0]) <= 23)
                        or not (0 <= int(date_time[1]) <= 59)
                    ):
                        raise ValueError("Invalid time")

            if check_in and check_out and check_out < check_in:
                raise ValueError("Check-out must not be earlier than check-in")

        except (ValueError, TypeError):
            flash("Check the employee, date, status, and time fields.", "error")
            return redirect(url_for("attendance"))

        try:
            db.execute("""
                INSERT INTO attendance (
                    employee_id, attendance_date, status,
                    check_in, check_out, notes
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                employee_id, attendance_date, status,
                check_in, check_out, notes
            ))

            db.commit()
            log_activity("CREATE", "hr",
                         "Attendance {} recorded for employee #{} on {}".format(
                             status, employee_id, attendance_date),
                         record_type="employee", record_id=employee_id)
            flash("Attendance recorded successfully.", "success")

        except sqlite3.IntegrityError:
            db.rollback()
            log_activity("CREATE", "hr",
                         "Duplicate attendance entry rejected for employee #{} on {}".format(
                             employee_id, attendance_date),
                         outcome="FAILURE", record_type="employee", record_id=employee_id)
            flash(
                "Attendance already exists for this employee on this date.",
                "error"
            )

        return redirect(url_for(
            "attendance",
            date=attendance_date
        ))

    selected_date = request.args.get("date", date.today().isoformat())

    try:
        date.fromisoformat(selected_date)
    except ValueError:
        selected_date = date.today().isoformat()

    employees = db.execute("""
        SELECT id, employee_code, full_name, department
        FROM employees
        WHERE employment_status = 'Active'
        ORDER BY full_name
    """).fetchall()

    records = db.execute("""
        SELECT
            a.id,
            a.employee_id,
            a.attendance_date,
            a.status,
            a.check_in,
            a.check_out,
            a.notes,
            e.employee_code,
            e.full_name,
            e.department
        FROM attendance AS a
        JOIN employees AS e ON e.id = a.employee_id
        WHERE a.attendance_date = ?
        ORDER BY e.full_name
    """, (selected_date,)).fetchall()

    total = len(records)
    present = sum(r["status"] == "Present" for r in records)
    absent = sum(r["status"] == "Absent" for r in records)
    late = sum(r["status"] == "Late" for r in records)
    half_day = sum(r["status"] == "Half Day" for r in records)

    return render_template(
        "HR/attendance.html",
        employees=employees,
        records=records,
        selected_date=selected_date,
        total=total,
        present=present,
        absent=absent,
        late=late,
        half_day=half_day
    )


@route("/admin/hr/payroll", methods=["GET", "POST"])
@perm("payroll")
def payroll():
    db = get_db()

    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        payroll_month = request.form.get("payroll_month", "").strip()
        basic_raw = request.form.get("basic_salary", "").strip()
        allowances_raw = request.form.get("allowances", "0").strip() or "0"
        deductions_raw = request.form.get("deductions", "0").strip() or "0"
        notes = request.form.get("notes", "").strip()

        try:
            employee_id = int(employee_id)

            # Validate YYYY-MM
            if (
                len(payroll_month) != 7
                or payroll_month[4] != "-"
                or not payroll_month[:4].isdigit()
                or not payroll_month[5:].isdigit()
                or not 1 <= int(payroll_month[5:]) <= 12
            ):
                raise ValueError("Invalid payroll month")

            basic = Decimal(basic_raw)
            allowances = Decimal(allowances_raw)
            deductions = Decimal(deductions_raw)

            amounts = (basic, allowances, deductions)
            if any(not amount.is_finite() or amount < 0 for amount in amounts):
                raise ValueError("Amounts must be non-negative numbers")

            # Round monetary values to two decimal places
            basic = basic.quantize(Decimal("0.01"))
            allowances = allowances.quantize(Decimal("0.01"))
            deductions = deductions.quantize(Decimal("0.01"))

            net = (basic + allowances - deductions).quantize(
                Decimal("0.01")
            )

            if net < 0:
                flash("Deductions cannot exceed basic salary plus allowances.", "error")
                return redirect(url_for("payroll", month=payroll_month))

            employee = db.execute(
                "SELECT id FROM employees WHERE id = ?",
                (employee_id,)
            ).fetchone()

            if employee is None:
                flash("Please select a valid employee.", "error")
                return redirect(url_for("payroll"))

        except (ValueError, InvalidOperation, TypeError):
            flash("Enter a valid employee, month, and salary amounts.", "error")
            return redirect(url_for("payroll"))

        try:
            db.execute("""
                INSERT INTO payroll (
                    employee_id, payroll_month, basic_salary,
                    allowances, deductions, net_salary, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                employee_id,
                payroll_month,
                str(basic),
                str(allowances),
                str(deductions),
                str(net),
                notes
            ))

            db.commit()
            log_activity("CREATE", "payroll",
                         "Payroll run created for employee #{} for {} (net {})".format(
                             employee_id, payroll_month, net),
                         record_type="payroll", record_id=employee_id)
            flash("Payroll record created successfully.", "success")

        except sqlite3.IntegrityError:
            db.rollback()
            log_activity("CREATE", "payroll",
                         "Duplicate payroll rejected for employee #{} for {}".format(
                             employee_id, payroll_month),
                         outcome="FAILURE", record_type="payroll", record_id=employee_id)
            flash(
                "A payroll record already exists for this employee and month.",
                "error"
            )

        return redirect(url_for("payroll", month=payroll_month))

    selected_month = request.args.get("month", date.today().strftime("%Y-%m"))

    # Validate month query parameter
    try:
        if (
            len(selected_month) != 7
            or selected_month[4] != "-"
            or not selected_month[:4].isdigit()
            or not selected_month[5:].isdigit()
            or not 1 <= int(selected_month[5:]) <= 12
        ):
            raise ValueError
    except ValueError:
        selected_month = date.today().strftime("%Y-%m")

    employees = db.execute("""
        SELECT id, employee_code, full_name, department, job_title
        FROM employees
        WHERE employment_status = 'Active'
        ORDER BY full_name
    """).fetchall()

    records = db.execute("""
        SELECT
            p.id, p.payroll_month, p.basic_salary, p.allowances,
            p.deductions, p.net_salary, p.payment_status, p.notes,
            e.employee_code, e.full_name, e.department
        FROM payroll AS p
        JOIN employees AS e ON e.id = p.employee_id
        WHERE p.payroll_month = ?
        ORDER BY e.full_name
    """, (selected_month,)).fetchall()

    total_basic = sum(float(r["basic_salary"]) for r in records)
    total_allowances = sum(float(r["allowances"]) for r in records)
    total_deductions = sum(float(r["deductions"]) for r in records)
    total_net = sum(float(r["net_salary"]) for r in records)
    paid_count = sum(r["payment_status"] == "Paid" for r in records)
    pending_count = sum(r["payment_status"] == "Pending" for r in records)

    return render_template(
        "HR/payroll.html",
        employees=employees,
        records=records,
        selected_month=selected_month,
        total_basic=total_basic,
        total_allowances=total_allowances,
        total_deductions=total_deductions,
        total_net=total_net,
        paid_count=paid_count,
        pending_count=pending_count
    )


@route("/admin/hr/payroll/<int:payroll_id>/status", methods=["POST"])
@perm("payroll")
def update_payroll_status(payroll_id):
    db = get_db()
    status = request.form.get("payment_status", "").strip()

    if status not in {"Pending", "Paid"}:
        flash("Invalid payment status.", "error")
        return redirect(url_for("payroll"))

    record = db.execute("""
        SELECT p.payroll_month, p.net_salary, e.full_name,
               u.id AS account_id
        FROM payroll AS p
        JOIN employees AS e ON e.id = p.employee_id
        LEFT JOIN users AS u ON u.employee_id = e.id
        WHERE p.id = ?
    """, (payroll_id,)).fetchone()

    result = db.execute(
        "UPDATE payroll SET payment_status = ? WHERE id = ?",
        (status, payroll_id)
    )

    if result.rowcount == 0:
        db.rollback()
        log_activity("UPDATE", "payroll",
                     "Payroll #{} not found for status change".format(payroll_id),
                     outcome="FAILURE", record_type="payroll", record_id=payroll_id)
        flash("Payroll record not found.", "error")
    else:
        if record is not None and record["account_id"] and status == "Paid":
            deliver_notification(
                db, record["account_id"], "Payslip paid",
                "Your {} payslip of {:,.2f} ETB has been marked paid.".format(
                    record["payroll_month"], float(record["net_salary"])),
                url_for("notification_center"), category="payroll",
                icon="cash", tone="ok")
        db.commit()
        log_activity("UPDATE", "payroll",
                     "Payroll #{} marked {}".format(payroll_id, status),
                     record_type="payroll", record_id=payroll_id)
        flash("Payroll payment status updated.", "success")

    return redirect(url_for("payroll"))


def valid_month(value):
    return (len(value) == 7 and value[4] == "-" and value[:4].isdigit()
            and value[5:].isdigit() and 1 <= int(value[5:]) <= 12)


@route("/payroll")
@perm("payroll")
def payroll_dashboard():
    month = request.args.get("month", "").strip()
    if not valid_month(month):
        month = date.today().strftime("%Y-%m")
    db = get_db()

    records = db.execute("""
        SELECT p.id, p.basic_salary, p.allowances, p.deductions, p.net_salary,
               p.payment_status, e.employee_code, e.full_name, e.department
        FROM payroll AS p
        JOIN employees AS e ON e.id = p.employee_id
        WHERE p.payroll_month = ?
        ORDER BY e.full_name
    """, (month,)).fetchall()

    months = db.execute("""
        SELECT payroll_month AS month, COUNT(*) AS lines,
               SUM(net_salary) AS net, SUM(payment_status = 'Paid') AS paid
        FROM payroll
        GROUP BY payroll_month
        ORDER BY payroll_month DESC
        LIMIT 6
    """).fetchall()

    gross = sum(float(r["basic_salary"]) + float(r["allowances"]) for r in records)
    deductions = sum(float(r["deductions"]) for r in records)
    net = sum(float(r["net_salary"]) for r in records)
    paid = sum(r["payment_status"] == "Paid" for r in records)

    return render_template(
        "payroll/dashboard.html",
        month=month,
        records=records,
        months=months,
        gross=gross,
        deductions=deductions,
        net=net,
        paid=paid,
        pending=len(records) - paid,
    )


@route("/admin/hr/recruitment", methods=["GET", "POST"])
@perm("hr")
def recruitment():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        # Create a job vacancy
        if action == "add_vacancy":
            job_title = request.form.get("job_title", "").strip()
            department = request.form.get("department", "").strip()
            job_type = request.form.get("job_type", "Full-time").strip()
            location = request.form.get("location", "").strip()
            description = request.form.get("description", "").strip()
            requirements = request.form.get("requirements", "").strip()
            closing_date = request.form.get("closing_date", "").strip()

            try:
                openings = int(request.form.get("openings", "1"))
            except (TypeError, ValueError):
                openings = 0

            if not job_title or not department:
                flash("Job title and department are required.", "error")
            elif openings < 1:
                flash("Number of openings must be at least 1.", "error")
            else:
                cursor = db.execute("""
                    INSERT INTO job_vacancies (
                        job_title, department, job_type, location,
                        description, requirements, openings, closing_date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_title, department, job_type, location,
                    description, requirements, openings,
                    closing_date or None
                ))
                db.commit()
                log_activity("CREATE", "hr",
                             "Vacancy opened: {} ({})".format(job_title, department),
                             record_type="job_vacancy", record_id=cursor.lastrowid)
                flash("Job vacancy created successfully.", "success")

            return redirect(url_for("recruitment"))

        # Register an applicant
        elif action == "add_applicant":
            vacancy_id = request.form.get("vacancy_id", type=int)
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            education = request.form.get("education", "").strip()
            experience = request.form.get("experience", "").strip()
            cover_letter = request.form.get("cover_letter", "").strip()

            vacancy = None
            if vacancy_id:
                vacancy = db.execute("""
                    SELECT id FROM job_vacancies
                    WHERE id = ? AND status = 'Open'
                """, (vacancy_id,)).fetchone()

            if not vacancy:
                flash("Please select an open job vacancy.", "error")
            elif not full_name or not email or not phone:
                flash("Name, email, and phone are required.", "error")
            else:
                cursor = db.execute("""
                    INSERT INTO job_applications (
                        vacancy_id, full_name, email, phone,
                        education, experience, cover_letter
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    vacancy_id, full_name, email, phone,
                    education, experience, cover_letter
                ))
                db.commit()
                log_activity("CREATE", "hr",
                             "Applicant registered: {} for vacancy #{}".format(
                                 full_name, vacancy_id),
                             record_type="job_application", record_id=cursor.lastrowid)
                flash("Applicant registered successfully.", "success")

            return redirect(url_for("recruitment"))

    # Filters
    search = request.args.get("search", "").strip()
    selected_vacancy = request.args.get("vacancy_id", type=int)

    # Dashboard statistics
    vacancy_stats = db.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'Open' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status = 'Closed' THEN 1 ELSE 0 END) AS closed_count
        FROM job_vacancies
    """).fetchone()

    application_stats = db.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'Applied' THEN 1 ELSE 0 END) AS applied,
            SUM(CASE WHEN status = 'Shortlisted' THEN 1 ELSE 0 END) AS shortlisted,
            SUM(CASE WHEN status = 'Interview' THEN 1 ELSE 0 END) AS interviews,
            SUM(CASE WHEN status = 'Hired' THEN 1 ELSE 0 END) AS hired
        FROM job_applications
    """).fetchone()

    vacancies = db.execute("""
        SELECT v.*,
               COUNT(a.id) AS applicant_count
        FROM job_vacancies v
        LEFT JOIN job_applications a ON a.vacancy_id = v.id
        GROUP BY v.id
        ORDER BY v.created_at DESC
    """).fetchall()

    sql = """
        SELECT a.*, v.job_title, v.department
        FROM job_applications a
        JOIN job_vacancies v ON v.id = a.vacancy_id
        WHERE 1 = 1
    """
    params = []

    if search:
        sql += """
            AND (
                a.full_name LIKE ?
                OR a.email LIKE ?
                OR a.phone LIKE ?
                OR v.job_title LIKE ?
            )
        """
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern])

    if selected_vacancy:
        sql += " AND a.vacancy_id = ?"
        params.append(selected_vacancy)

    sql += " ORDER BY a.applied_at DESC"

    applications = db.execute(sql, params).fetchall()

    return render_template(
        "HR/recruitment.html",
        vacancies=vacancies,
        applications=applications,
        vacancy_stats=vacancy_stats,
        application_stats=application_stats,
        search=search,
        selected_vacancy=selected_vacancy
    )


@route(
    "/admin/hr/recruitment/application/<int:application_id>/status",
    methods=["POST"]
)
@perm("hr")
def update_application_status(application_id):
    allowed_statuses = {
        "Applied", "Shortlisted", "Interview",
        "Selected", "Rejected", "Hired"
    }

    status = request.form.get("status", "").strip()

    if status not in allowed_statuses:
        log_activity("UPDATE", "hr",
                     "Invalid application status {!r} rejected".format(status),
                     outcome="FAILURE", record_type="job_application",
                     record_id=application_id)
        flash("Invalid application status.", "error")
        return redirect(url_for("recruitment"))

    db = get_db()

    result = db.execute("""
        UPDATE job_applications
        SET status = ?
        WHERE id = ?
    """, (status, application_id))

    db.commit()

    if result.rowcount:
        log_activity("UPDATE", "hr",
                     "Application #{} moved to {}".format(application_id, status),
                     record_type="job_application", record_id=application_id)
        flash("Application status updated.", "success")
    else:
        log_activity("UPDATE", "hr",
                     "Application #{} not found".format(application_id),
                     outcome="FAILURE", record_type="job_application",
                     record_id=application_id)
        flash("Application not found.", "error")

    return redirect(url_for("recruitment"))


@route(
    "/admin/hr/recruitment/vacancy/<int:vacancy_id>/status",
    methods=["POST"]
)
@perm("hr")
def update_vacancy_status(vacancy_id):
    status = request.form.get("status", "").strip()

    if status not in {"Open", "Closed"}:
        log_activity("UPDATE", "hr",
                     "Invalid vacancy status {!r} rejected".format(status),
                     outcome="FAILURE", record_type="job_vacancy", record_id=vacancy_id)
        flash("Invalid vacancy status.", "error")
        return redirect(url_for("recruitment"))

    db = get_db()
    result = db.execute("""
        UPDATE job_vacancies
        SET status = ?
        WHERE id = ?
    """, (status, vacancy_id))
    db.commit()

    if result.rowcount:
        log_activity("UPDATE", "hr",
                     "Vacancy #{} marked {}".format(vacancy_id, status),
                     record_type="job_vacancy", record_id=vacancy_id)
        flash("Vacancy status updated.", "success")
    else:
        flash("Vacancy not found.", "error")

    return redirect(url_for("recruitment"))
