"""The HR attendance desk, and the payroll addresses that moved.

Attendance is recorded by HR, so its screen stays here. The standalone payroll
form below it used to write the legacy ``payroll`` table; the payroll desk under
``/payroll`` owns salaries, overtime and payslips now, so those addresses forward
there and nobody has to keep two payrolls in step.
"""

from datetime import date

import sqlite3
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for

from core.access import perm
from core.db import get_db
from core.routing import route
from services.audit import log_activity


@route("/admin/hr/attendance", methods=["GET", "POST"])
@perm("hr_attendance")
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


def valid_month(value):
    return (len(value) == 7 and value[4] == "-" and value[:4].isdigit()
            and value[5:].isdigit() and 1 <= int(value[5:]) <= 12)


def _payroll_month(value):
    month = (value or "").strip()
    return month if valid_month(month) else date.today().strftime("%Y-%m")


@route("/admin/hr/payroll", methods=["GET", "POST"])
@perm("payroll_processing")
def payroll():
    """The old entry form forwards to the payroll desk that owns the records."""

    month = _payroll_month(
        request.values.get("month", "") or request.values.get("payroll_month", ""))
    return redirect(url_for("payroll.payroll_processing", month=month))


@route("/admin/hr/payroll/<int:payroll_id>/status", methods=["POST"])
@perm("payroll_payment")
def update_payroll_status(payroll_id):
    """Payment is recorded on the payroll desk, against its own records."""

    flash("Record the payment from the payroll desk.", "warning")
    return redirect(url_for("payroll.payroll_processing"))


@route("/payroll")
@perm("payroll_management")
def payroll_dashboard():
    """The legacy dashboard address now opens the payroll desk."""

    month = _payroll_month(request.args.get("month", ""))
    return redirect(url_for("payroll.payroll_dashboard", month=month))


@route("/admin/hr/recruitment")
@perm("recruitment_vacancies")
def recruitment():
    """The hiring screens moved into the recruitment desks.

    The vacancy, applicant, examination and interview desks under /recruitment/hr
    write the same two tables, with the scope, eligibility, count and approval
    checks this screen never had. The old address still answers, but only to walk
    the visitor to the desk that owns the record now.
    """
    return redirect(url_for("recruitment.hr_vacancies"))
