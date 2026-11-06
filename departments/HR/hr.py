"""HR staff records, hiring and settings."""

from core.access import current_user
from core.access import effective_permissions
from core.access import login_required
from core.access import perm
from core.config import DB_PATH
from core.config import STEP_LABEL
from core.db import connect_db
from core.db import get_db
from core.formatting import today_name
from core.routing import route
from core.web import approvals_waiting_for
from database import database_config
from datetime import date
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import send_from_directory
from flask import url_for
from services.accounts import account_for_employee
from services.accounts import adopt_account_for_employee
from services.accounts import sync_employee_to_account
from services.audit import log_activity
from services.audit import change_set
from services.hr import apply_employee_uploads
from services.hr import can_view_documents
from services.hr import can_view_salary
from services.hr import create_employee_record
from services.hr import delete_employee_files
from services.hr import employee_display_name
from services.hr import employee_form_defaults
from services.hr import employee_upload_dir
from services.hr import read_employee_form
from services.hr import render_employee_page
from services.hr import stored_document_list
from services.hr import update_employee_record
from services.notifications import deliver_notification
from services.org import organization_snapshot
from services.recruitment import conversion_target
from services.recruitment import employee_seed_from_application
from services.recruitment import hire_right_problem
from services.recruitment import mark_application_hired
from services.recency import stamp as stamp_record
import sqlite3


@route("/admin/hr")
@perm("hr_employees", "hr_leave", "hr_attendance", "hr_departments",
      "hr_organization", "hr_reports")
def hr_dashboard():
    """The single HR dashboard: workforce figures plus every HR work queue."""
    db = get_db()
    user = current_user()
    granted = effective_permissions(user)

    employees = db.execute("""
        SELECT *
        FROM employees
        ORDER BY id DESC
    """).fetchall()

    total = len(employees)
    active = sum(
        employee["employment_status"] == "Active"
        for employee in employees
    )
    on_leave = sum(
        employee["employment_status"] == "On Leave"
        for employee in employees
    )

    today = date.today()
    attendance = db.execute("""
        SELECT COUNT(*) AS marked,
               COALESCE(SUM(status = 'Present'), 0) AS present,
               COALESCE(SUM(status = 'Late'), 0) AS late,
               COALESCE(SUM(status = 'Half Day'), 0) AS half_day,
               COALESCE(SUM(status = 'Absent'), 0) AS absent
        FROM attendance
        WHERE attendance_date = ?
    """, (today.isoformat(),)).fetchone()

    leave = db.execute("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(status = 'Pending'), 0) AS pending,
               COALESCE(SUM(status = 'Approved'), 0) AS approved
        FROM leave_requests
    """).fetchone()
    leave_queue = db.execute("""
        SELECT l.id, l.leave_type, l.start_date, l.end_date,
               l.days_requested, e.full_name, e.department
        FROM leave_requests AS l
        JOIN employees AS e ON e.id = l.employee_id
        WHERE l.status = 'Pending'
        ORDER BY l.start_date ASC, l.id ASC
        LIMIT 5
    """).fetchall()

    recruitment = {
        "vacancies": db.execute("""
            SELECT COUNT(*) AS n FROM job_vacancies WHERE status = 'Open'
        """).fetchone()["n"],
        "applicants": db.execute("""
            SELECT COUNT(*) AS n FROM job_applications
        """).fetchone()["n"],
        "interviewing": db.execute("""
            SELECT COUNT(*) AS n FROM job_applications
            WHERE status IN ('Shortlisted', 'Interview')
        """).fetchone()["n"],
    }

    training = db.execute("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(status = 'Completed'), 0) AS completed
        FROM employee_training
    """).fetchone()
    reviews = db.execute(
        "SELECT COUNT(*) AS n FROM performance_reviews"
    ).fetchone()["n"]

    month_key = today.strftime("%Y-%m")
    payroll = None
    if "payroll" in granted:
        payroll = db.execute("""
            SELECT COUNT(*) AS lines,
                   COALESCE(SUM(net_salary), 0) AS net,
                   COALESCE(SUM(payment_status = 'Pending'), 0) AS pending,
                   COALESCE(SUM(payment_status = 'Paid'), 0) AS paid
            FROM payroll
            WHERE payroll_month = ?
        """, (month_key,)).fetchone()

    approvals = []
    if "approvals" in granted:
        waiting = approvals_waiting_for(db, user)
        approvals = [{
            "id": row["id"],
            "title": row["title"],
            "type": row["request_type"],
            "department": row["department"],
            "amount": float(row["amount"] or 0),
            "by": row["created_by_name"],
            "step": STEP_LABEL.get(row["current_approver_step"],
                                   row["current_approver_step"]),
        } for row in waiting[:5]]
        approval_count = len(waiting)
    else:
        approval_count = 0

    ops = {
        "attendance": attendance,
        "unmarked": max(active - attendance["marked"], 0),
        "leave": leave,
        "leave_queue": leave_queue,
        "recruitment": recruitment,
        "training": training,
        "reviews": reviews,
        "payroll": payroll,
        "payroll_month": today.strftime("%B %Y"),
        "salary_visible": "payroll" in granted and can_view_salary(user),
        "approvals": approvals,
        "approval_count": approval_count,
    }

    return render_template(
        "HR/hr.html",
        employees=employees,
        total=total,
        active=active,
        on_leave=on_leave,
        ops=ops,
        org=organization_snapshot(db, user),
        today_name=today_name(),
    )


@route("/admin/hr/add", methods=["POST"])
@perm("hr_employees")
def add_employee():
    db = get_db()
    user = current_user()
    values, errors, uploads = read_employee_form(
        db,
        salary_allowed=can_view_salary(user),
        documents_allowed=can_view_documents(user),
    )
    application_id = (request.form.get("recruitment_application_id") or "").strip()
    hiring_from = None
    if application_id:
        # The hidden reference says which pipeline this record is meant to close.
        # It is checked before anything is written, so a stale or tampered form
        # cannot create a second story about a candidate the desk has not selected.
        if not application_id.isdigit():
            errors.append("The hiring reference on this form is not readable;"
                          " reload the candidate page and save again.")
        else:
            hiring_from, problem = conversion_target(db, int(application_id))
            if problem:
                errors.append(problem)
                hiring_from = None
            else:
                problem = hire_right_problem(db, user, hiring_from["vacancy_id"])
                if problem:
                    errors.append(problem)
                    hiring_from = None
                else:
                    values["recruitment_application_id"] = int(application_id)
    if errors:
        return render_employee_page(db, form_data=values, errors=errors,
                                    uploads=uploads, hiring_from=hiring_from)

    try:
        employee_id = create_employee_record(db, values)
        apply_employee_uploads(db, employee_id, values, uploads, errors=errors)
        # A record whose email already belongs to an unlinked account adopts it, so
        # signing somebody up in HR and giving them a sign-in is one job, not two.
        adopted = adopt_account_for_employee(db, employee_id)
        if hiring_from is not None:
            mark_application_hired(db, hiring_from, employee_id)
            notify_hire(db, hiring_from, values, user)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        log_activity("CREATE", "hr",
                     "Rejected duplicate employee registration ({})".format(
                         values["full_name"]),
                     outcome="FAILURE", record_type="employee")
        return render_employee_page(
            db, form_data=values, hiring_from=hiring_from,
            errors=["Employee code or email is already in use. Check the ID and"
                    " email, then save again."],
            uploads=uploads)

    log_activity("CREATE", "hr",
                 "Employee {} registered{}".format(
                     values["full_name"],
                     " from application #{}".format(hiring_from["application_id"])
                     if hiring_from is not None else ""),
                 record_type="employee", record_id=employee_id,
                 details={"adopted_account": adopted,
                          "recruitment_application_id": (
                              hiring_from["application_id"] if hiring_from else None),
                          "documents": len(values["education_documents"])
                          + len(values["supporting_documents"])})
    flash("{} was added to the employee directory.".format(values["full_name"]), "success")
    for problem in errors:
        flash(problem, "error")
    if adopted:
        flash("The account that already uses this email is now linked to the"
              " staff record.", "info")
    if hiring_from is not None:
        flash("The hiring pipeline is closed: the post's filled count and the"
              " candidate's record now point at each other.", "info")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=hiring_from["application_id"]))
    return redirect(url_for("employees", _anchor="directory"))


def notify_hire(db, target, values, user):
    """Tell the requesting desk and the candidate that the hire is now staff."""
    href = url_for("recruitment.applicant_detail",
                   application_id=target["application_id"])
    if target["created_by_id"] and target["created_by_id"] != user["id"]:
        deliver_notification(
            db, target["created_by_id"],
            "Hire registered: {}".format(target["full_name"]),
            "{} starts in {} as {}. Staff record {} was created by {}.".format(
                target["full_name"], target["department"], target["job_title"],
                values["employee_code"], user["full_name"]),
            href, category="recruitment", icon="check", tone="ok",
            dedupe_key="hire-registered-{}".format(target["application_id"]))
    if target["user_id"]:
        deliver_notification(
            db, target["user_id"],
            "Welcome aboard: {}".format(target["job_title"]),
            "Your hiring is complete and your staff record is registered. Contact"
            " the HR desk for the details.",
            href, category="recruitment", icon="check", tone="ok",
            dedupe_key="hire-welcome-{}".format(target["application_id"]))


@route("/admin/hr/employees")
@perm("hr_employees")
def employees():
    db = get_db()
    editing = None
    edit_id = request.args.get("edit", type=int)
    if edit_id is not None:
        editing = db.execute(
            "SELECT * FROM employees WHERE id = ?", (edit_id,)
        ).fetchone()
        if editing is None:
            flash("That employee no longer exists.", "error")
            return redirect(url_for("employees"))
    hiring_from = None
    form_data = None
    application_id = request.args.get("from_application", type=int)
    if application_id is not None and editing is None:
        # The desk opens the staff form already filled from the application, so a
        # hire is confirmed by checking a page, not by retyping a person.
        hiring_from, problem = conversion_target(db, application_id)
        if not problem:
            problem = hire_right_problem(db, current_user(), hiring_from["vacancy_id"])
        if problem:
            flash(problem, "error")
            return redirect(url_for("employees"))
        form_data = dict(employee_form_defaults(db))
        form_data.update(employee_seed_from_application(hiring_from))
        form_data["recruitment_application_id"] = hiring_from["application_id"]
    return render_employee_page(db, editing=editing, form_data=form_data,
                                hiring_from=hiring_from)


@route("/admin/hr/employees/edit/<int:employee_id>", methods=["GET", "POST"])
@perm("hr_employees")
def edit_employee(employee_id):
    db = get_db()
    employee = db.execute(
        "SELECT * FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()

    if employee is None:
        flash("Employee not found.", "error")
        return redirect(url_for("employees"))

    if request.method == "GET":
        return render_employee_page(db, editing=employee)

    user = current_user()
    values, errors, uploads = read_employee_form(
        db,
        employee=employee,
        salary_allowed=can_view_salary(user),
        documents_allowed=can_view_documents(user),
    )
    if errors:
        return render_employee_page(
            db, editing=employee, form_data=values, errors=errors, uploads=uploads)

    try:
        update_employee_record(db, employee_id, values)
        apply_employee_uploads(db, employee_id, values, uploads,
                               previous=employee, errors=errors)
        adopted = None
        if account_for_employee(db, employee_id) is None:
            adopted = adopt_account_for_employee(db, employee_id)
        # The staff file owns the person, so the sign-in record follows it. This
        # form does carry status now, but the value read before the update is what
        # the sync compares against for the disable and re-enable rules.
        changes = sync_employee_to_account(db, employee_id,
                                           employee["employment_status"])
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        log_activity("UPDATE", "hr",
                     "Rejected duplicate employee code or email ({})".format(
                         values["full_name"]),
                     outcome="FAILURE", record_type="employee", record_id=employee_id)
        return render_employee_page(
            db, editing=employee, form_data=values, uploads=uploads,
            errors=["Employee code or email is already in use. Check the ID and"
                    " email, then save again."])

    previous_values, updated_values = change_set(employee, {
        "full_name": values["full_name"],
        "phone": values["phone"], "email": values["email"],
        "department": values["department"],
        "job_title": values["job_title"],
        "employment_status": values["employment_status"],
        "work_location": values["work_location"],
    })
    log_activity("UPDATE", "hr",
                 "Employee {} edited{}".format(
                     values["full_name"],
                     " (account {})".format(", ".join(changes)) if changes else ""),
                 record_type="employee", record_id=employee_id,
                 details={"account_changes": changes,
                          "employment_status": values["employment_status"],
                          "documents": len(values["education_documents"])
                          + len(values["supporting_documents"])},
                 previous=previous_values, updated=updated_values)
    flash("{} was updated.".format(values["full_name"]), "success")
    for problem in errors:
        flash(problem, "error")
    for change in changes:
        flash("Sign-in record also changed {}.".format(change), "info")
    if adopted:
        flash("The account that already uses this email is now linked to the"
              " staff record.", "info")
    return redirect(url_for("employees", _anchor="directory"))


def employee_history_problem(db, employee_id):
    """Why a staff record that owns payroll or review rows has to be kept.

    Attendance and training sit in the same file as the record, so SQLite itself
    refuses the delete. Payroll and performance live in their own files, where the
    link is a plain id; without this check those rows would survive their owner.
    """
    for table in ("payroll", "performance_reviews"):
        if not database_config.table_exists(db, table):
            continue
        found = db.execute(
            "SELECT 1 FROM {} WHERE employee_id = ? LIMIT 1".format(
                database_config.qualified(table)), (employee_id,)).fetchone()
        if found is not None:
            return table
    return None


@route("/admin/hr/employees/delete/<int:employee_id>", methods=["POST"])
@perm("hr_employees")
def delete_employee(employee_id):
    db = get_db()
    employee = db.execute(
        "SELECT full_name FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    if employee is None:
        flash("That employee no longer exists.", "error")
        return redirect(url_for("employees"))
    # The sign-in outlives the staff record: only the link is dropped, and whether
    # that account can still open the system is a User Management decision.
    account = account_for_employee(db, employee_id)
    history = employee_history_problem(db, employee_id)
    if history is not None:
        log_activity("DELETE", "hr",
                     "Employee {} kept: it has {} history".format(
                         employee["full_name"], history.replace("_", " ")),
                     outcome="FAILURE", record_type="employee", record_id=employee_id)
        flash("That employee has attendance or payroll history, so the record is kept."
              " Set the employment status to Inactive instead.", "error")
        return redirect(url_for("employees"))
    try:
        if account is not None:
            db.execute("UPDATE users SET employee_id = NULL WHERE id = ?", (account["id"],))
            stamp_record(db, "users", account["id"])
        db.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        db.commit()
    except sqlite3.IntegrityError:
        # Attendance and payroll rows point at the record, so deleting it would take
        # somebody's work history with it. The rollback puts the link back as well.
        db.rollback()
        log_activity("DELETE", "hr",
                     "Employee {} kept: it has attendance or payroll history".format(
                         employee["full_name"]),
                     outcome="FAILURE", record_type="employee", record_id=employee_id)
        flash("That employee has attendance or payroll history, so the record is kept."
              " Set the employment status to Inactive instead.", "error")
        return redirect(url_for("employees"))
    # Only now is the private folder unreachable; a kept record keeps its files.
    delete_employee_files(employee_id)
    log_activity("DELETE", "hr",
                 "Employee {} removed{}".format(
                     employee["full_name"],
                     "; account {} unlinked".format(account["username"] or account["email"])
                     if account is not None else ""),
                 record_type="employee", record_id=employee_id)
    flash("Employee deleted successfully.", "success")
    if account is not None:
        flash("{} keeps their sign-in; the staff record link was removed.".format(
            account["full_name"]), "info")
    return redirect(url_for("employees", _anchor="directory"))


@route("/employees/<int:employee_id>/photo")
@login_required()
def employee_photo(employee_id):
    """Serve a stored profile photo without exposing the uploads folder."""
    db = get_db()
    employee = db.execute("""
        SELECT profile_photo, employment_status FROM employees WHERE id = ?
    """, (employee_id,)).fetchone()
    if employee is None:
        abort(404)
    stored = (employee["profile_photo"] or "").strip()
    if not stored:
        abort(404)
    viewer = current_user()
    if employee["employment_status"] != "Active" and not can_view_documents(viewer):
        abort(404)
    path = employee_upload_dir(employee_id) / employee_display_name(stored)
    if not path.is_file():
        abort(404)
    return send_from_directory(path.parent, path.name, max_age=3600)


@route("/admin/hr/employees/<int:employee_id>/documents/<path:filename>")
@perm("hr_employees")
def employee_document(employee_id, filename):
    if not can_view_documents(current_user()):
        abort(403)
    db = get_db()
    employee = db.execute("""
        SELECT education_documents, supporting_documents FROM employees WHERE id = ?
    """, (employee_id,)).fetchone()
    if employee is None:
        abort(404)
    documents = (stored_document_list(employee["education_documents"])
                 + stored_document_list(employee["supporting_documents"]))
    match = next((doc for doc in documents if doc["file"] == filename), None)
    path = employee_upload_dir(employee_id) / filename
    if match is None or not path.is_file():
        abort(404)
    return send_from_directory(path.parent, path.name, as_attachment=True,
                               download_name=match["name"])


hr_settings_bp = Blueprint("hr_settings", __name__)


DATABASE = DB_PATH


DEFAULT_SETTINGS = {
    "company_name": "Dereje Wood & Kitchen Cabinet",
    "company_email": "",
    "company_phone": "",
    "company_address": "",
    "currency": "ETB",
    "work_start": "08:00",
    "work_end": "17:00",
    "break_minutes": "60",
    "attendance_grace_minutes": "10",
    "overtime_enabled": "1",
    "payroll_day": "30",
}


def settings_db():
    return connect_db(DATABASE)


def init_hr_settings():
    with settings_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hr_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)

        conn.executemany("""
            INSERT OR IGNORE INTO hr_settings
                (setting_key, setting_value)
            VALUES (?, ?)
        """, list(DEFAULT_SETTINGS.items()))


@hr_settings_bp.route("/admin/hr/settings", methods=["GET", "POST"])
@perm("hr")
def index():
    init_hr_settings()

    if request.method == "POST":
        data = {
            "company_name": request.form.get(
                "company_name", ""
            ).strip(),
            "company_email": request.form.get(
                "company_email", ""
            ).strip(),
            "company_phone": request.form.get(
                "company_phone", ""
            ).strip(),
            "company_address": request.form.get(
                "company_address", ""
            ).strip(),
            "currency": request.form.get("currency", "ETB"),
            "work_start": request.form.get("work_start", ""),
            "work_end": request.form.get("work_end", ""),
            "break_minutes": request.form.get("break_minutes", ""),
            "attendance_grace_minutes": request.form.get(
                "attendance_grace_minutes", ""
            ),
            "overtime_enabled": (
                "1" if request.form.get("overtime_enabled") == "1"
                else "0"
            ),
            "payroll_day": request.form.get("payroll_day", ""),
        }

        if not data["company_name"]:
            flash("Company name is required.", "error")
            return redirect(url_for("hr_settings.index"))

        if not all([
            data["work_start"], data["work_end"],
            data["break_minutes"],
            data["attendance_grace_minutes"],
            data["payroll_day"]
        ]):
            flash("Please complete all required settings.", "error")
            return redirect(url_for("hr_settings.index"))

        try:
            from datetime import time

            start = time.fromisoformat(data["work_start"])
            end = time.fromisoformat(data["work_end"])

            if start >= end:
                raise ValueError("End time must be after start time.")

            break_minutes = int(data["break_minutes"])
            grace = int(data["attendance_grace_minutes"])
            payroll_day = int(data["payroll_day"])

            if not 0 <= break_minutes <= 240:
                raise ValueError("Break must be between 0 and 240 minutes.")

            if not 0 <= grace <= 120:
                raise ValueError("Grace period must be between 0 and 120.")

            if not 1 <= payroll_day <= 31:
                raise ValueError("Payroll day must be between 1 and 31.")

            if data["currency"] not in {"ETB", "USD", "EUR"}:
                raise ValueError("Select a supported currency.")

        except ValueError as exc:
            log_activity("UPDATE", "hr",
                         "HR settings rejected: {}".format(exc),
                         outcome="FAILURE", record_type="hr_setting")
            flash(str(exc) or "Please enter valid settings.", "error")
            return redirect(url_for("hr_settings.index"))

        with settings_db() as conn:
            conn.executemany("""
                INSERT INTO hr_settings (setting_key, setting_value)
                VALUES (?, ?)
                ON CONFLICT(setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
            """, list(data.items()))

        log_activity("UPDATE", "hr",
                     "HR settings saved ({} values)".format(len(data)),
                     record_type="hr_setting")
        flash("HR settings saved successfully.", "success")
        return redirect(url_for("hr_settings.index"))

    with settings_db() as conn:
        rows = conn.execute(
            "SELECT setting_key, setting_value FROM hr_settings"
        ).fetchall()

    settings = DEFAULT_SETTINGS.copy()
    settings.update({row["setting_key"]: row["setting_value"]
                     for row in rows})

    return render_template(
        "HR/hr_settings.html",
        settings=settings
    )


def register(app):
    """Attach this department's blueprints to the application."""
    from departments.HR.routes import hr_overtime_bp
    from departments.HR.permission import register_hr_template_helpers

    app.register_blueprint(hr_settings_bp)
    app.register_blueprint(hr_overtime_bp)
    register_hr_template_helpers(app)
