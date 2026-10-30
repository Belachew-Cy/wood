"""Admin console, accounts, roles, settings and notifications."""

from core.access import current_user
from core.access import custom_role_key
from core.access import effective_permissions
from core.access import perm
from core.access import role_choices
from core.access import role_name_for
from core.access import role_permission_set
from core.access import user_extra_roles
from core.config import DASHBOARD_ENDPOINT
from core.config import MODULES
from core.config import MODULE_LABEL
from core.config import ROLE_LABEL
from core.config import ROLE_PERMISSIONS
from core.config import STEP_LABEL
from core.config import USER_ROLES
from core.config import USER_STATUS
from core.db import get_db
from core.routing import post
from core.routing import route
from core.web import load_projects
from core.web import save_projects
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
from departments.hr import DEFAULT_SETTINGS
from departments.hr import init_hr_settings
from flask import abort
from flask import flash
from flask import redirect
from flask import Response
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from services.accounts import account_for_employee
from services.accounts import adopt_account_for_employee
from services.accounts import attach_account_to_record
from services.accounts import employee_link_problem
from services.accounts import employee_name
from services.accounts import generate_initial_password
from services.accounts import next_username
from services.accounts import staff_link_choice
from services.accounts import staff_records
from services.accounts import sync_employee_to_account
from services.audit import log_activity
from services.audit import change_set
from services.email import delivery_summary
from services.email import mail_status
from services.email import recent_deliveries
from services.hr import can_view_documents
from services.hr import can_view_salary
from services.hr import create_employee_record
from services.hr import read_employee_form
from services.hr import stored_document_labels
from services.hr import update_employee_record
from services.letters import LETTER_PERMISSION_LABEL
from services.letters import LETTER_PERMISSION_NOTE
from services.letters import LETTER_PERMISSIONS
from services.letters import LETTER_TYPE_HINT
from services.letters import LETTER_TYPE_LABEL
from services.letters import LETTER_TYPES
from services.letters import letter_category_set
from services.letters import stored_letter_categories
from services.letters import stored_letter_permissions
from services.links import can_manage_approval_policy
from services.notifications import deliver_notification
from services.org import POSITION_LEVEL_LABEL
from services.recency import stamp as stamp_record
from services.workflow import APPROVAL_DEPARTMENTS
from services.workflow import APPROVAL_STEPS
from services.workflow import approval_limits
from services.workflow import approval_path
from services.workflow import approval_route_map
from services.workflow import approver_desk_label
from services.workflow import get_approval_policy
from services.workflow import init_management_policy
from services.workflow import step_holders
from werkzeug.security import generate_password_hash
import csv
import io
import sqlite3

@route("/admin/letter-permissions/<int:user_id>", methods=["GET", "POST"])
@perm("accounts")
def admin_letter_permissions(user_id):
    """The letter actions and categories an account has been granted."""
    db = get_db()
    target = db.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)).fetchone()
    if target is None:
        abort(404)

    role_name = role_name_for(target["role"], db)
    if target["role"] == "admin":
        flash("Administrators already hold every letter action and category.",
              "info")
        return redirect(url_for("admin_users"))

    if request.method == "POST":
        if request.form.get("action") == "reset":
            db.execute("DELETE FROM letter.letter_permissions WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM letter.letter_category_permissions WHERE user_id = ?",
                       (user_id,))
            db.commit()
            log_activity("UPDATE", "accounts",
                         "Letter permissions for {} cleared".format(
                             target["full_name"]),
                         record_type="user", record_id=user_id)
            flash("Letter permissions cleared for {}.".format(target["full_name"]),
                  "success")
            return redirect(url_for("admin_letter_permissions", user_id=user_id))

        selected = set(request.form.getlist("permissions"))
        if not selected.issubset(LETTER_PERMISSIONS):
            abort(400)
        categories = set(request.form.getlist("categories"))
        if not categories.issubset(LETTER_TYPES):
            abort(400)
        if selected - {"letters_view"} or categories:
            # Every action is taken from inside the register, so the account
            # must be able to open what it was granted.
            selected.add("letters_view")
        # The account's tables are the grant in full: an unticked action or
        # category is withdrawn here, whatever any role would have implied.
        db.execute("DELETE FROM letter.letter_permissions WHERE user_id = ?", (user_id,))
        for permission in LETTER_PERMISSIONS:
            db.execute("INSERT INTO letter.letter_permissions (user_id, permission, allowed)"
                       " VALUES (?, ?, ?)",
                       (user_id, permission, 1 if permission in selected else 0))
        db.execute("DELETE FROM letter.letter_category_permissions WHERE user_id = ?",
                   (user_id,))
        for category in LETTER_TYPES:
            db.execute("INSERT INTO letter.letter_category_permissions"
                       " (user_id, category, allowed) VALUES (?, ?, ?)",
                       (user_id, category, 1 if category in categories else 0))
        db.commit()
        log_activity("UPDATE", "accounts",
                     "Letter permissions {} for {}".format(
                         "granted" if selected else "withdrawn",
                         target["full_name"]),
                     record_type="user", record_id=user_id,
                     details={"permissions": sorted(selected),
                              "categories": sorted(categories)})
        flash("Letter permissions updated." if selected
              else "No letter actions are granted to this account now.",
              "success")
        if selected & {"letters_create", "letters_edit", "letters_send"} \
                and not categories:
            flash("No letter category is ticked, so this account can open the"
                  " register but cannot file a letter yet.", "info")
        return redirect(url_for("admin_letter_permissions", user_id=user_id))

    overridden, granted = stored_letter_permissions(db, user_id)
    display = granted if overridden else set()
    categories_overridden, granted_categories = stored_letter_categories(
        db, user_id)
    return render_template(
        "Letter/permissions.html",
        target=target, role_label=role_name, granted=display,
        using_defaults=not overridden,
        permissions=[(key, LETTER_PERMISSION_LABEL[key])
                     for key in LETTER_PERMISSIONS],
        notes=LETTER_PERMISSION_NOTE,
        categories=[(key, LETTER_TYPE_LABEL[key], LETTER_TYPE_HINT[key])
                    for key in LETTER_TYPES],
        granted_categories=(granted_categories if categories_overridden
                            else set()),
        default_categories=(set() if categories_overridden
                            else letter_category_set(target)),
        categories_using_defaults=not categories_overridden)


@route("/admin", methods=["GET", "POST"])
@perm("accounts")
def admin():
    projects = load_projects()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            title = request.form.get("title", "").strip()
            category = request.form.get("category", "Interior")
            image = request.form.get("image", "").strip()
            if not title or not image:
                flash("Title and image path are required.", "error")
            else:
                new_id = max((p["id"] for p in projects), default=0) + 1
                projects.append({"id": new_id, "title": title, "category": category, "image": image})
                save_projects(projects)
                log_activity("CREATE", "projects",
                             "Project {} added to the portfolio".format(title),
                             record_type="project", record_id=new_id)
                flash("Project added.", "success")
        elif action == "delete":
            try:
                pid = int(request.form.get("id", ""))
            except ValueError:
                flash("Choose a project to delete.", "error")
                return redirect(url_for("admin"))
            projects = [p for p in projects if p["id"] != pid]
            save_projects(projects)
            log_activity("DELETE", "projects",
                         "Project #{} removed from the portfolio".format(pid),
                         record_type="project", record_id=pid)
            flash("Project deleted.", "success")
        elif action in ("add_employee", "save_employee"):
            employee_id = request.form.get("employee_id", "")
            target = None
            if action == "save_employee":
                try:
                    employee_id = int(employee_id)
                except ValueError:
                    flash("Choose an employee to update.", "error")
                    return redirect(url_for("admin"))
                target = get_db().execute(
                    "SELECT * FROM employees WHERE id = ?", (employee_id,)
                ).fetchone()
                if target is None:
                    flash("That employee no longer exists.", "error")
                    return redirect(url_for("admin"))
            db = get_db()
            user = current_user()
            values, errors, _uploads = read_employee_form(
                db, employee=target,
                salary_allowed=can_view_salary(user),
                documents_allowed=can_view_documents(user),
            )
            if target is not None:
                # The Admin dashboard no longer hosts a staff form, but a stale tab can
                # still post the old field set. Keep the details that form never had.
                for field in ("date_of_birth", "address", "emergency_contact_name",
                              "emergency_contact_phone", "employment_type", "manager_id",
                              "work_location", "education_level", "field_of_study",
                              "institution", "graduation_year", "certifications",
                              "work_experience", "notes"):
                    if field not in request.form:
                        values[field] = target[field]
                if "required_documents" not in request.form:
                    values["required_documents"] = stored_document_labels(
                        target["required_documents"])
            if errors:
                for problem in errors[:4]:
                    flash(problem, "error")
                if target is not None:
                    return redirect(url_for("admin", edit=employee_id))
                return redirect(url_for("admin"))
            changes = []
            adopted = None
            try:
                if target is None:
                    employee_id = create_employee_record(db, values)
                    adopted = adopt_account_for_employee(db, employee_id)
                else:
                    update_employee_record(db, employee_id, values)
                    # HR owns the person, so the account's name, contact details and
                    # sign-in follow the record. An Inactive employee costs their
                    # account the sign-in; coming back restores it only when the record
                    # disabled it, never when an administrator did.
                    changes = sync_employee_to_account(
                        db, employee_id, target["employment_status"])
                db.commit()
                log_activity("CREATE" if action == "add_employee" else "UPDATE", "hr",
                             "Directory {}: {}".format(
                                 "entry added" if action == "add_employee" else "entry edited",
                                 values["full_name"]),
                             record_type="employee", record_id=employee_id,
                             details={"employment_status": values["employment_status"],
                                      "account_changes": changes})
                flash(f"{values['full_name']} "
                      f"{'added to the employee directory' if target is None else 'updated'}.",
                      "success")
                if adopted:
                    flash("The account that already uses this email is now linked to"
                          " the staff record.", "info")
                for change in changes:
                    flash("Sign-in record also changed {}.".format(change), "info")
            except sqlite3.IntegrityError:
                db.rollback()
                log_activity("UPDATE", "hr",
                             "Directory save rejected: duplicate code or email ({})".format(
                                 values["full_name"]),
                             outcome="FAILURE", record_type="employee")
                flash("Employee code or email is already in use.", "error")
                if target is not None:
                    return redirect(url_for("admin", edit=employee_id))
            return redirect(url_for("admin"))
        elif action == "delete_employee":
            try:
                employee_id = int(request.form.get("employee_id", ""))
            except ValueError:
                flash("Choose an employee to delete.", "error")
                return redirect(url_for("admin"))
            db = get_db()
            target = db.execute(
                "SELECT full_name FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if target is None:
                flash("That employee no longer exists.", "error")
            else:
                # The sign-in outlives the record; only the link is dropped here.
                account = account_for_employee(db, employee_id)
                try:
                    if account is not None:
                        db.execute("UPDATE users SET employee_id = NULL WHERE id = ?",
                                   (account["id"],))
                        stamp_record(db, "users", account["id"])
                    db.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    log_activity("DELETE", "hr",
                                 "Directory entry {} kept: it has attendance or payroll"
                                 " history".format(target["full_name"]),
                                 outcome="FAILURE", record_type="employee",
                                 record_id=employee_id)
                    flash("That employee has attendance or payroll history, so the"
                          " record is kept. Set the status to Inactive instead.", "error")
                    return redirect(url_for("admin"))
                log_activity("DELETE", "hr",
                             "Directory entry {} removed{}".format(
                                 target["full_name"],
                                 "; account unlinked" if account is not None else ""),
                             record_type="employee", record_id=employee_id)
                flash(f"{target['full_name']} removed from the directory.", "success")
                if account is not None:
                    flash("{} keeps their sign-in; the staff record link was"
                          " removed.".format(account["full_name"]), "info")
            return redirect(url_for("admin"))
        else:
            flash("Unrecognised request.", "error")
        return redirect(url_for("admin"))

    employees = get_db().execute(
        "SELECT *, job_title AS position FROM employees ORDER BY employee_code"
    ).fetchall()
    return render_template(
        "Admin/admin.html",
        projects=projects,
        employees=employees,
    )


def account_directory(db):
    # The sign-in name, the employee link and the password gate travel with the row so
    # the screen can show what an account actually is, not just its name.
    rows = db.execute("""
        SELECT u.id, u.full_name, u.email, u.phone, u.role, u.status, u.last_login,
               u.username, u.employee_id, u.must_change_password,
               u.failed_login_count, u.lock_state, u.lock_until, u.locked_at,
               u.updated_by_name, u.updated_at,
               e.full_name AS employee_name,
               e.employee_code AS employee_code,
               e.employment_status AS employee_status,
               e.hire_date AS employee_hire_date,
               d.name AS department_name,
               p.title AS position_title,
               p.level AS position_level
        FROM users AS u
        LEFT JOIN employees AS e ON e.id = u.employee_id
        LEFT JOIN departments AS d ON d.id = u.department_id
        LEFT JOIN organizational_positions AS p ON p.id = u.position_id
        ORDER BY CASE u.status WHEN 'Active' THEN 0 ELSE 1 END, u.role, u.full_name
    """).fetchall()
    return [{"user": row, "granted": effective_permissions(row)} for row in rows]


@route("/admin/users", methods=["GET", "POST"])
@perm("accounts")
def admin_users():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_user":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "")
            status = request.form.get("status", "Active")
            employee_id, link_problem = staff_link_choice(db, request.form, None)
            if not full_name or "@" not in email:
                flash("Name and a valid email are required.", "error")
            elif password and len(password) < 8:
                flash("Use a password of at least 8 characters.", "error")
            elif role not in USER_ROLES:
                flash("Choose a valid role.", "error")
            elif status not in USER_STATUS:
                flash("Choose Active or Disabled.", "error")
            elif link_problem:
                flash(link_problem, "error")
            else:
                # A blank field means the system hands out the initial password; a typed
                # one is only ever a hand-over too. Either way the account is gated until
                # its owner replaces it, so no shared secret outlives the first sign-in.
                generated = not password
                if generated:
                    password = generate_initial_password()
                username = next_username(db)
                try:
                    created = db.execute("""
                        INSERT INTO users (full_name, email, phone, password_hash, role,
                                           status, username, must_change_password)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """, (full_name, email, phone, generate_password_hash(password),
                          role, status, username))
                except sqlite3.IntegrityError:
                    # The counter moved with the INSERT, so the rollback puts it back and
                    # the refused email costs no number.
                    db.rollback()
                    log_activity("CREATE", "accounts",
                                 "Account rejected: {} already exists".format(email),
                                 outcome="FAILURE", record_type="user")
                    flash("That email already has an account.", "error")
                else:
                    # The link is taken after the INSERT so a refused duplicate has
                    # already cost the username counter its number, and so the staff
                    # record is the only writer of the account's name and contact
                    # details from here on.
                    linked = attach_account_to_record(db, created.lastrowid, employee_id)
                    stamp_record(db, "users", created.lastrowid, current_user())
                    deliver_notification(
                        db, created.lastrowid, "Welcome to the workspace",
                        "Your account was created. Set a password of your own the"
                        " first time you sign in.",
                        url_for("notification_center"), category="account",
                        icon="user", tone="info")
                    db.commit()
                    session["handover"] = {
                        "full_name": full_name,
                        "username": username,
                        "password": password if generated else "",
                    }
                    log_activity("CREATE", "accounts",
                                 "Account {} created for {} as {}".format(
                                     username, full_name, role),
                                 record_type="user", record_id=created.lastrowid,
                                 details={"email": email, "role": role, "status": status,
                                          "username": username,
                                          "staff_record": employee_id,
                                          "account_changes": linked,
                                          "initial_password": "generated" if generated
                                          else "typed by the administrator"})
                    flash("{} can now sign in as {} with the username {}.".format(
                        full_name, role, username), "success")
                    if employee_id is not None:
                        flash("It is linked to the staff record for {}.".format(
                            employee_name(db, employee_id)), "info")

        elif action == "save_account":
            try:
                uid = int(request.form.get("id", ""))
            except ValueError:
                flash("Choose an account to update.", "error")
                return redirect(url_for("admin_users"))
            target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            role = request.form.get("role", "")
            status = request.form.get("status", "")
            employee_id, link_problem = (
                (None, "That account no longer exists.") if target is None
                else staff_link_choice(db, request.form, uid))

            if target is None:
                flash("That account no longer exists.", "error")
            elif role not in USER_ROLES or status not in USER_STATUS:
                flash("Choose a valid role and status.", "error")
            elif not full_name or "@" not in email:
                flash("Name and a valid email are required.", "error")
            elif link_problem:
                flash(link_problem, "error")
            elif uid == session.get("user_id") and (
                    role != target["role"] or status != target["status"]):
                flash("You cannot change your own role or disable your own account.", "error")
            else:
                previous_values, updated_values = change_set(target, {
                    "full_name": full_name, "email": email, "phone": phone,
                    "role": role, "status": status,
                })
                try:
                    db.execute("""
                        UPDATE users SET full_name = ?, email = ?, phone = ?, role = ?, status = ?
                        WHERE id = ?
                    """, (full_name, email, phone, role, status, uid))
                    # A role change drops the old account-specific ticks so the
                    # account starts from its new role's defaults. Editing
                    # anything else leaves Role Management's ticks untouched.
                    revoked = ""
                    if role != target["role"] and role != "admin":
                        db.execute("DELETE FROM user_permissions WHERE user_id = ?", (uid,))
                        revoked = "; custom module ticks cleared"
                    # The staff link is the one field an account edit can move. Taking a
                    # record hands its name, contact details and sign-in status to the
                    # account; dropping one leaves the account alone.
                    linked = []
                    if employee_id != target["employee_id"]:
                        linked = attach_account_to_record(db, uid, employee_id)
                    stamp_record(db, "users", uid, current_user())
                    if role != target["role"]:
                        deliver_notification(
                            db, uid, "Your role changed",
                            "You now sign in as {}. Your dashboard and modules follow"
                            " the new role.".format(ROLE_LABEL.get(role, role)),
                            url_for("dashboard"), category="account",
                            icon="shield", tone="info")
                    if status != target["status"]:
                        deliver_notification(
                            db, uid, "Account {}".format(status.lower()),
                            "An administrator set your account to {}.".format(status),
                            url_for("notification_center"), category="account",
                            icon="lock" if status == "Disabled" else "check",
                            tone="warn" if status == "Disabled" else "ok")
                    db.commit()
                    changes = [
                        "{} → {}".format(target[field], value)
                        for field, value in (
                            ("role", role), ("status", status), ("full_name", full_name),
                            ("email", email), ("phone", phone),
                        )
                        if target[field] != value
                    ]
                    if employee_id != target["employee_id"]:
                        changes.append("staff record {} → {}".format(
                            employee_name(db, target["employee_id"])
                            if target["employee_id"] is not None else "(none)",
                            employee_name(db, employee_id) if employee_id is not None
                            else "(none)"))
                        changes.extend(linked)
                    log_activity("UPDATE", "accounts",
                                 "Account {} edited ({})".format(
                                     full_name, "; ".join(changes) or "no field change"),
                                 record_type="user", record_id=uid,
                                 details={"permissions": "role defaults" if revoked else "kept",
                                          "staff_record": employee_id,
                                          "account_changes": linked},
                                 previous=previous_values, updated=updated_values)
                    flash("{} updated.".format(full_name), "success")
                    for change in linked:
                        flash("Sign-in record also changed {}.".format(change), "info")
                except sqlite3.IntegrityError:
                    db.rollback()
                    log_activity("UPDATE", "accounts",
                                 "Account edit rejected: {} already has an account".format(email),
                                 outcome="FAILURE", record_type="user", record_id=uid)
                    flash("That email already has an account.", "error")

        elif action == "set_password":
            try:
                uid = int(request.form.get("id", ""))
            except ValueError:
                flash("Choose an account to update.", "error")
                return redirect(url_for("admin_users"))
            password = request.form.get("password", "")
            if password and len(password) < 8:
                flash("Use a password of at least 8 characters.", "error")
            else:
                target = db.execute("SELECT full_name, username FROM users WHERE id = ?",
                                    (uid,)).fetchone()
                if target is None:
                    flash("That account no longer exists.", "error")
                else:
                    # A reset is a hand-over, not a password: the account gates itself
                    # again so nobody signs in on a credential a third person knows.
                    generated = not password
                    if generated:
                        password = generate_initial_password()
                    db.execute("""
                        UPDATE users SET password_hash = ?, must_change_password = 1,
                                         password_updated_at = NULL
                        WHERE id = ?
                    """, (generate_password_hash(password), uid))
                    stamp_record(db, "users", uid, current_user())
                    deliver_notification(
                        db, uid, "Password reset",
                        "An administrator set a new password for your account."
                        " You must choose your own at the next sign-in.",
                        url_for("account_password"), category="account",
                        icon="lock", tone="warn")
                    db.commit()
                    session["handover"] = {
                        "full_name": target["full_name"],
                        "username": target["username"] or target["full_name"],
                        "password": password if generated else "",
                    }
                    log_activity("UPDATE", "accounts",
                                 "Password reset for {}".format(target["full_name"]),
                                 record_type="user", record_id=uid,
                                 details={"reason": "Administrator set a new password",
                                          "initial_password": "generated" if generated
                                          else "typed by the administrator",
                                          "must_change_password": 1})
                    flash("New password set for {}. {}".format(
                        target["full_name"],
                        "It is shown once below, and they must replace it before the"
                        " dashboard opens." if generated else
                        "They must set a password of their own before the dashboard"
                        " opens."), "success")

        elif action == "bulk_status":
            status = request.form.get("status", "")
            if status not in USER_STATUS:
                flash("Choose Active or Disabled.", "error")
            else:
                changed = []
                skipped_self = 0
                for raw in request.form.getlist("ids"):
                    try:
                        uid = int(raw)
                    except (TypeError, ValueError):
                        continue
                    if uid == session.get("user_id"):
                        skipped_self += 1
                        continue
                    target = db.execute(
                        "SELECT full_name FROM users WHERE id = ?", (uid,)
                    ).fetchone()
                    if target is None:
                        continue
                    db.execute("UPDATE users SET status = ? WHERE id = ?", (status, uid))
                    stamp_record(db, "users", uid, current_user())
                    deliver_notification(
                        db, uid, "Account {}".format(status.lower()),
                        "An administrator set your account to {}.".format(status),
                        url_for("notification_center"), category="account",
                        icon="lock" if status == "Disabled" else "check",
                        tone="warn" if status == "Disabled" else "ok")
                    changed.append(target["full_name"])
                if changed:
                    db.commit()
                    log_activity("UPDATE", "accounts",
                                 "{} account{} set to {}".format(
                                     len(changed), "" if len(changed) == 1 else "s",
                                     status),
                                 record_type="user",
                                 details={"status": status, "accounts": changed})
                    flash("{} account{} set to {}.".format(
                        len(changed), "" if len(changed) == 1 else "s", status),
                        "success")
                if skipped_self:
                    flash("Your own account was left unchanged.", "info")
                if not changed and not skipped_self:
                    flash("No account was changed.", "error")

        elif action in ("lock_user", "unlock_user"):
            try:
                uid = int(request.form.get("id", ""))
            except (TypeError, ValueError):
                flash("Choose an account to update.", "error")
                return redirect(url_for("admin_users"))
            target = db.execute("SELECT * FROM users WHERE id = ?",
                                (uid,)).fetchone()
            previous = (target["lock_state"] or "unlocked") if target else "unlocked"
            if target is None:
                flash("That account no longer exists.", "error")
            elif action == "lock_user" and uid == session.get("user_id"):
                flash("You cannot lock your own account.", "error")
            elif action == "lock_user":
                if previous == "permanent":
                    flash("{} is already locked.".format(target["full_name"]), "info")
                else:
                    db.execute(
                        "UPDATE users SET lock_state = 'permanent',"
                        " lock_until = NULL, locked_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         uid))
                    stamp_record(db, "users", uid, current_user())
                    deliver_notification(
                        db, uid, "Account locked",
                        "An administrator locked your account. They must unlock it"
                        " before you can sign in again.",
                        url_for("login"), category="account", icon="lock", tone="bad")
                    db.commit()
                    log_activity(
                        "LOCK", "accounts",
                        "Account {} locked by an administrator".format(
                            target["full_name"]),
                        record_type="user", record_id=uid,
                        details={"source": "administrator", "lock": "permanent",
                                 "previous_lock": previous})
                    flash("{} is locked until an administrator unlocks it.".format(
                        target["full_name"]), "success")
            else:
                db.execute(
                    "UPDATE users SET lock_state = 'unlocked', lock_until = NULL,"
                    " locked_at = NULL, failed_login_count = 0 WHERE id = ?",
                    (uid,))
                stamp_record(db, "users", uid, current_user())
                if previous != "unlocked":
                    deliver_notification(
                        db, uid, "Account unlocked",
                        "An administrator unlocked your account. You can sign in"
                        " again.",
                        url_for("login"), category="account", icon="check", tone="ok")
                db.commit()
                log_activity(
                    "UNLOCK", "accounts",
                    "Account {} unlocked by an administrator".format(
                        target["full_name"]),
                    record_type="user", record_id=uid,
                    details={"source": "administrator",
                             "previous_lock": previous,
                             "failed_attempts": target["failed_login_count"] or 0})
                flash("{} can sign in again.".format(target["full_name"]), "success")
        elif action == "delete_user":
            try:
                uid = int(request.form.get("id", ""))
            except (TypeError, ValueError):
                flash("Choose an account to delete.", "error")
                return redirect(url_for("admin_users"))
            target = db.execute("SELECT * FROM users WHERE id = ?",
                                (uid,)).fetchone()
            if target is None:
                flash("That account no longer exists.", "error")
            elif uid == session.get("user_id"):
                flash("You cannot delete your own account.", "error")
            elif target["role"] == "admin":
                flash("Administrator accounts cannot be deleted. Disable the"
                      " account instead.", "error")
            else:
                # An account that authored or decided business records stays in
                # the system: the audit trail points at it by name, so deletion
                # is refused and disabling is offered instead.
                ownership_checks = (
                    ("letters it authored", """
                        SELECT COUNT(*) AS n FROM letter.letters WHERE created_by = ?"""),
                    ("approval requests it raised", """
                        SELECT COUNT(*) AS n FROM approval_requests WHERE created_by = ?"""),
                    ("approval decisions it made", """
                        SELECT COUNT(*) AS n FROM approval_actions WHERE actor_id = ?"""),
                    ("procurement requisitions", """
                        SELECT COUNT(*) AS n FROM finance.procurement_requests
                         WHERE requested_by = ?"""),
                    ("purchase orders", """
                        SELECT COUNT(*) AS n FROM finance.procurement_orders
                         WHERE created_by = ?"""),
                    ("invoices", """
                        SELECT COUNT(*) AS n FROM finance.finance_invoices
                         WHERE created_by = ?"""),
                    ("finance transactions", """
                        SELECT COUNT(*) AS n FROM finance.finance_transactions
                         WHERE created_by = ?"""),
                    ("budget plans", """
                        SELECT COUNT(*) AS n FROM budget_plans WHERE created_by = ?"""),
                    ("budget allocations", """
                        SELECT COUNT(*) AS n FROM budget_allocations WHERE created_by = ?"""),
                    ("announcements", """
                        SELECT COUNT(*) AS n FROM announcements WHERE created_by = ?"""),
                    ("audit reviews or findings", """
                        SELECT COUNT(*) AS n FROM audit_reviews WHERE created_by = ?
                        UNION ALL SELECT COUNT(*) FROM audit_findings WHERE created_by = ?"""),
                    ("stock movements", """
                        SELECT COUNT(*) AS n FROM stock_movements WHERE created_by = ?"""),
                )
                owned = [label for label, sql in ownership_checks
                         if db.execute(sql, (uid,) * sql.count("?")
                                       ).fetchone()["n"]]
                if owned:
                    flash("Cannot delete {}: the account owns {}. Disable it"
                          " instead — its history stays searchable.".format(
                              target["full_name"], ", ".join(owned)), "error")
                else:
                    # Per-account satellites go with the row; the audit log,
                    # approval trail and business records are not touched.
                    for sql in (
                        "DELETE FROM user_roles WHERE user_id = ?",
                        "DELETE FROM user_permissions WHERE user_id = ?",
                        "DELETE FROM password_reset_tokens WHERE user_id = ?",
                        "DELETE FROM password_reset_requests WHERE user_id = ?",
                        "DELETE FROM notifications WHERE user_id = ?",
                        "DELETE FROM email_deliveries WHERE user_id = ?",
                        "DELETE FROM login_attempts WHERE identifier IN"
                        " (SELECT username FROM users WHERE id = ?)",
                        "DELETE FROM letter.letter_permissions WHERE user_id = ?",
                        "DELETE FROM letter.letter_category_permissions WHERE user_id = ?",
                        "DELETE FROM letter.letter_deliveries WHERE user_id = ?",
                        "DELETE FROM letter.letter_reads WHERE user_id = ?",
                        "UPDATE employees SET user_id = NULL WHERE user_id = ?",
                    ):
                        db.execute(sql, (uid,))
                    db.execute("DELETE FROM users WHERE id = ?", (uid,))
                    db.commit()
                    log_activity(
                        "DELETE", "accounts",
                        "Account {} ({}) deleted by an administrator".format(
                            target["full_name"], target["username"]),
                        record_type="user", record_id=uid,
                        previous={
                            "full_name": target["full_name"],
                            "username": target["username"],
                            "email": target["email"],
                            "role": target["role"],
                            "status": target["status"],
                            "department_id": target["department_id"],
                            "position_id": target["position_id"],
                            "employee_id": target["employee_id"],
                        })
                    flash("Account {} was deleted.".format(target["full_name"]),
                          "success")
        else:
            flash("Unrecognised request.", "error")
        return redirect(url_for("admin_users"))

    accounts = account_directory(db)
    counts = {
        "total": len(accounts),
        "active": sum(a["user"]["status"] == "Active" for a in accounts),
        "disabled": sum(a["user"]["status"] != "Active" for a in accounts),
        "never": sum(not a["user"]["last_login"] for a in accounts),
        "gated": sum(bool(a["user"]["must_change_password"]) for a in accounts),
    }
    # The hand-over details are read out of the session, so the very next reload of
    # the screen no longer shows a password that has already been handed over.
    handover = session.pop("handover", None)
    return render_template("Admin/users.html", accounts=accounts, counts=counts,
                           handover=handover, staff=staff_records(db),
                           roles=USER_ROLES, statuses=USER_STATUS,
                           role_labels=ROLE_LABEL, module_label=MODULE_LABEL)


# ------------------------------------------------------------ user import
# A filled-in spreadsheet is the fastest way to start a team, but every row still
# becomes the same gated account the Add user form creates: a generated password
# shown once here, a welcome notification and, when the sheet names an HR code,
# the same staff link the form would have made.
IMPORT_COLUMNS = ("full_name", "email", "phone", "role", "status", "employee_code")
IMPORT_MAX_ROWS = 300
IMPORT_MAX_BYTES = 512 * 1024
IMPORT_HEADER_ALIASES = {
    "name": "full_name",
    "full name": "full_name",
    "employee name": "full_name",
    "e mail": "email",
    "email address": "email",
    "phone number": "phone",
    "telephone": "phone",
    "mobile": "phone",
    "staff code": "employee_code",
    "staff record": "employee_code",
    "employee code": "employee_code",
    "employee id": "employee_code",
}


def import_header_key(name):
    key = " ".join((name or "").strip().lower().replace("-", " ")
                   .replace("_", " ").split())
    key = IMPORT_HEADER_ALIASES.get(key, key)
    return key if key in IMPORT_COLUMNS else ""


def import_role(value):
    """The role key for a cell that may hold the key or the label, else None."""
    wanted = (value or "").strip().lower()
    if not wanted:
        return "employee"
    for role in USER_ROLES:
        if wanted in (role, ROLE_LABEL.get(role, role).lower()):
            return role
    return None


def import_status(value):
    wanted = (value or "").strip().lower()
    if not wanted:
        return "Active"
    for status in USER_STATUS:
        if status.lower() == wanted:
            return status
    return None


def read_import_file(upload):
    """The filled rows as (line number, values), or a problem the screen can show."""
    raw = upload.read(IMPORT_MAX_BYTES + 1)
    if not raw:
        return None, "That file is empty."
    if len(raw) > IMPORT_MAX_BYTES:
        return None, "Keep the file under {} KB.".format(IMPORT_MAX_BYTES // 1024)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "Save the file as UTF-8 CSV and upload it again."
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None:
        return None, "That file is empty."
    positions = {}
    for index, name in enumerate(header):
        key = import_header_key(name)
        if key and key not in positions:
            positions[key] = index
    missing = [column for column in ("full_name", "email") if column not in positions]
    if missing:
        return None, "The header row must include {}.".format(" and ".join(missing))
    rows = []
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        rows.append((reader.line_num, {
            column: (row[index].strip() if index < len(row) else "")
            for column, index in positions.items()}))
    if not rows:
        return None, "The file has no rows under its header."
    if len(rows) > IMPORT_MAX_ROWS:
        return None, "That file has {} rows; import at most {} at a time.".format(
            len(rows), IMPORT_MAX_ROWS)
    return rows, None


def import_row_values(db, values):
    """The account fields a row asks for, or the reason it cannot be created."""
    full_name = values.get("full_name", "")
    email = values.get("email", "").lower()
    phone = values.get("phone", "")
    role = import_role(values.get("role"))
    status = import_status(values.get("status"))
    code = values.get("employee_code", "").upper()

    if not full_name:
        return None, "Full name is missing."
    if len(full_name) > 120:
        return None, "Full name is longer than 120 characters."
    if "@" not in email:
        return None, "Enter a valid email address."
    if len(email) > 254:
        return None, "Email is longer than 254 characters."
    if len(phone) > 30:
        return None, "Phone is longer than 30 characters."
    if role is None:
        return None, "\"{}\" is not a role - use a role key from the template.".format(
            values.get("role", ""))
    if status is None:
        return None, "Status must be Active or Disabled."

    employee_id = None
    if code:
        record = db.execute(
            "SELECT id FROM employees WHERE upper(employee_code) = ?", (code,)
        ).fetchone()
        if record is None:
            return None, "No staff record has the code {}.".format(code)
        problem = employee_link_problem(db, record["id"], None)
        if problem:
            return None, problem
        employee_id = record["id"]

    if db.execute("SELECT id FROM users WHERE lower(email) = ?",
                  (email,)).fetchone() is not None:
        return None, "That email already has an account."

    return {"full_name": full_name, "email": email, "phone": phone,
            "role": role, "status": status, "employee_id": employee_id}, None


@route("/admin/users/import/template")
@perm("accounts")
def admin_user_import_template():
    """The blank sheet: the header plus two placeholder rows to type over."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(IMPORT_COLUMNS)
    writer.writerow(["Example Person One - replace or delete", "person.one@example.com",
                     "+251 911 234 567", "employee", "Active", ""])
    writer.writerow(["Example Person Two - replace or delete", "person.two@example.com",
                     "", "finance", "Active", ""])
    # Excel only reads a UTF-8 file as UTF-8 when it carries the byte-order mark.
    response = Response("\ufeff" + buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        'attachment; filename="user-import-template.csv"')
    return response


@route("/admin/users/import", methods=["GET", "POST"])
@perm("accounts")
def admin_user_import():
    db = get_db()
    result = None

    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Choose the filled-in CSV file to upload.", "error")
        else:
            filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1][:120]
            rows, problem = read_import_file(upload)
            if problem:
                log_activity("CREATE", "accounts",
                             "User import rejected: {}".format(problem),
                             outcome="FAILURE", record_type="user",
                             details={"file": filename})
                flash(problem, "error")
            else:
                result = {"file": filename, "created": [], "rejected": []}

                def reject(line, values, reason):
                    log_activity("CREATE", "accounts",
                                 "Account import row {} rejected: {}".format(line, reason),
                                 outcome="FAILURE", record_type="user",
                                 details={"file": filename, "line": line,
                                          "name": values.get("full_name", "")[:120],
                                          "email": values.get("email", "")[:254]})
                    result["rejected"].append({
                        "line": line,
                        "name": values.get("full_name", ""),
                        "email": values.get("email", ""),
                        "reason": reason,
                    })

                for line, values in rows:
                    fields, problem = import_row_values(db, values)
                    if problem:
                        reject(line, values, problem)
                        continue
                    # Same hand-over as Add user: the password is generated, shown in
                    # the result table and never stored, and the account is gated.
                    password = generate_initial_password()
                    username = next_username(db)
                    try:
                        created = db.execute("""
                            INSERT INTO users (full_name, email, phone, password_hash, role,
                                               status, username, must_change_password)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        """, (fields["full_name"], fields["email"], fields["phone"],
                              generate_password_hash(password), fields["role"],
                              fields["status"], username))
                    except sqlite3.IntegrityError:
                        db.rollback()
                        reject(line, values, "That email already has an account.")
                        continue
                    linked = attach_account_to_record(db, created.lastrowid,
                                                      fields["employee_id"])
                    stamp_record(db, "users", created.lastrowid, current_user())
                    deliver_notification(
                        db, created.lastrowid, "Welcome to the workspace",
                        "Your account was created. Set a password of your own the"
                        " first time you sign in.",
                        url_for("notification_center"), category="account",
                        icon="user", tone="info")
                    db.commit()
                    log_activity("CREATE", "accounts",
                                 "Account {} created for {} as {} (CSV import)".format(
                                     username, fields["full_name"], fields["role"]),
                                 record_type="user", record_id=created.lastrowid,
                                 details={"email": fields["email"],
                                          "role": fields["role"],
                                          "status": fields["status"],
                                          "username": username,
                                          "staff_record": fields["employee_id"],
                                          "account_changes": linked,
                                          "source": "csv import",
                                          "file": filename,
                                          "line": line,
                                          "initial_password": "generated"})
                    # A linked staff record owns the name and email, so the result
                    # table shows the account as it now stands, not the sheet's copy.
                    stored = db.execute(
                        "SELECT full_name, email FROM users WHERE id = ?",
                        (created.lastrowid,)).fetchone()
                    result["created"].append({
                        "line": line,
                        "full_name": stored["full_name"],
                        "email": stored["email"],
                        "username": username,
                        "password": password,
                        "role_key": fields["role"],
                        "role": ROLE_LABEL.get(fields["role"], fields["role"]),
                        "staff": employee_name(db, fields["employee_id"])
                                 if fields["employee_id"] is not None else "",
                    })

                if result["created"]:
                    flash("{} account{} created. Hand over the passwords below -"
                          " they are not stored.".format(
                              len(result["created"]),
                              "" if len(result["created"]) == 1 else "s"), "success")
                if result["rejected"]:
                    flash("{} row{} rejected. Each one is listed with its reason.".format(
                        len(result["rejected"]),
                        "" if len(result["rejected"]) == 1 else "s"), "error")

    return render_template("Admin/user_import.html", result=result,
                           roles=USER_ROLES, role_labels=ROLE_LABEL,
                           statuses=USER_STATUS, max_rows=IMPORT_MAX_ROWS,
                           max_kb=IMPORT_MAX_BYTES // 1024)


@route("/admin/roles")
@perm("accounts")
def admin_roles():
    db = get_db()
    accounts = account_directory(db)
    choices = role_choices(db)
    role_names = dict(choices)

    extra_holders = {}
    for row in db.execute("""
        SELECT ur.role_key, u.id, u.full_name, u.status
        FROM user_roles AS ur JOIN users AS u ON u.id = ur.user_id
        ORDER BY u.full_name
    """).fetchall():
        extra_holders.setdefault(row["role_key"], []).append(row)

    roles = []
    for key, label in choices:
        row = db.execute("SELECT * FROM roles WHERE key = ?", (key,)).fetchone()
        modules = [entry["module"] for entry in db.execute(
            "SELECT module FROM role_permissions WHERE role_key = ?"
            " ORDER BY module", (key,)).fetchall()]
        holders = [a for a in accounts if a["user"]["role"] == key]
        roles.append({
            "role": key,
            "label": label,
            "description": row["description"] if row else "",
            "is_system": bool(row["is_system"]) if row else True,
            "defaults": modules,
            "landing": DASHBOARD_ENDPOINT.get(key, "dashboard"),
            "members": holders,
            "extras": extra_holders.get(key, []),
            "customised": sum(
                bool(db.execute(
                    "SELECT COUNT(*) AS n FROM user_permissions WHERE user_id = ?",
                    (a["user"]["id"],)).fetchone()["n"]) for a in holders),
        })

    overrides = db.execute("""
        SELECT p.user_id, p.module, p.allowed, p.updated_at, u.full_name, u.role
        FROM user_permissions AS p JOIN users AS u ON u.id = p.user_id
        ORDER BY u.full_name, p.module
    """).fetchall()
    # The matrix marks a cell that differs from the role default, so the template
    # never has to work out set differences itself.
    deviated = {(row["user_id"], row["module"]) for row in overrides}
    deviated_ids = {row["user_id"] for row in overrides}

    return render_template("Admin/roles.html", roles=roles, accounts=accounts,
                           overrides=overrides, modules=MODULES,
                           module_label=MODULE_LABEL, role_labels=role_names,
                           role_defaults=ROLE_PERMISSIONS,
                           deviated=deviated, deviated_ids=deviated_ids)


@post("/admin/roles/create")
@perm("accounts")
def admin_role_create():
    db = get_db()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()[:300]
    modules = [module for module in MODULES
               if module in set(request.form.getlist("modules"))]
    problem = None
    if len(name) < 2:
        problem = "Give the role a name of at least two characters."
    elif db.execute("SELECT 1 FROM roles WHERE lower(name) = lower(?)",
                    (name,)).fetchone():
        problem = "A role called {} already exists.".format(name)
    if problem:
        flash(problem, "error")
        return redirect(url_for("admin_roles"))

    key = custom_role_key(db, name)
    db.execute("""
        INSERT INTO roles (key, name, description, landing, is_system)
        VALUES (?, ?, ?, 'dashboard', 0)
    """, (key, name[:60], description))
    for module in modules:
        db.execute("INSERT INTO role_permissions (role_key, module) VALUES (?, ?)",
                   (key, module))
    db.commit()
    log_activity("CREATE", "accounts",
                 "Custom role '{}' created with {} module(s)".format(
                     name, len(modules)),
                 record_type="role", record_id=key,
                 details={"modules": modules, "description": description})
    flash("Role {} created. Assign it to an account on its module screen.".format(name),
          "success")
    return redirect(url_for("admin_roles"))


@post("/admin/roles/<role_key>/update")
@perm("accounts")
def admin_role_update(role_key):
    db = get_db()
    role = db.execute("SELECT * FROM roles WHERE key = ?", (role_key,)).fetchone()
    if role is None:
        abort(404)
    if role["is_system"]:
        name = role["name"]
        description = request.form.get("description", role["description"]).strip()[:300]
    else:
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()[:300]
        taken = db.execute("SELECT 1 FROM roles WHERE lower(name) = lower(?)"
                           " AND key <> ?", (name, role_key)).fetchone()
        if len(name) < 2 or taken:
            flash("Give the custom role a unique name of at least two characters.",
                  "error")
            return redirect(url_for("admin_roles"))
    if role_key == "admin":
        flash("The Administrator role always holds every module.", "error")
        return redirect(url_for("admin_roles"))

    modules = [module for module in MODULES
               if module in set(request.form.getlist("modules"))]
    db.execute("UPDATE roles SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP"
               " WHERE key = ?", (name, description, role_key))
    db.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
    for module in modules:
        db.execute("INSERT INTO role_permissions (role_key, module) VALUES (?, ?)",
                   (role_key, module))
    holders = {row["id"] for row in db.execute(
        "SELECT id FROM users WHERE role = ? AND status = 'Active'", (role_key,))}
    holders |= {row["user_id"] for row in db.execute(
        "SELECT user_id FROM user_roles WHERE role_key = ?", (role_key,))}
    for user_id in holders:
        deliver_notification(
            db, user_id, "Role access updated",
            "The {} role now carries: {}. Your modules follow that list.".format(
                name, ", ".join(MODULE_LABEL.get(m, m) for m in modules) or "no modules"),
            url_for("dashboard"), category="account", icon="shield", tone="info")
    db.commit()
    log_activity("UPDATE", "accounts",
                 "Role '{}' modules set to {}".format(
                     name, ", ".join(modules) or "none"),
                 record_type="role", record_id=role_key,
                 details={"modules": modules, "holders": len(holders)})
    flash("Role {} saved.".format(name), "success")
    return redirect(url_for("admin_roles"))


@post("/admin/roles/<role_key>/delete")
@perm("accounts")
def admin_role_delete(role_key):
    db = get_db()
    role = db.execute("SELECT * FROM roles WHERE key = ?", (role_key,)).fetchone()
    if role is None:
        abort(404)
    if role["is_system"]:
        flash("Built-in roles cannot be deleted.", "error")
        return redirect(url_for("admin_roles"))
    primary = db.execute("SELECT COUNT(*) AS n FROM users WHERE role = ?",
                         (role_key,)).fetchone()["n"]
    extra = db.execute("SELECT COUNT(*) AS n FROM user_roles WHERE role_key = ?",
                       (role_key,)).fetchone()["n"]
    if primary or extra:
        flash("Role {} still has {} account(s). Remove it from them first.".format(
            role["name"], primary + extra), "error")
        return redirect(url_for("admin_roles"))
    db.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
    db.execute("DELETE FROM roles WHERE key = ?", (role_key,))
    db.commit()
    log_activity("DELETE", "accounts",
                 "Custom role '{}' deleted".format(role["name"]),
                 record_type="role", record_id=role_key)
    flash("Role {} deleted.".format(role["name"]), "success")
    return redirect(url_for("admin_roles"))


@route("/admin/notifications", methods=["GET", "POST"])
@perm("accounts")
def admin_notifications():
    """Compose an announcement and see what each one reached."""
    db = get_db()
    choices = role_choices(db)
    role_names = dict(choices)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        href = request.form.get("href", "").strip()
        selected = request.form.getlist("roles")
        everyone = "all" in selected
        audience = {role for role in selected if role in role_names}

        if not title or not body:
            flash("An announcement needs a title and a message.", "error")
        elif not everyone and not audience:
            flash("Choose everyone or at least one role.", "error")
        elif href and (not href.startswith("/") or href.startswith("//")):
            flash("The link must be an internal path such as /dashboard.", "error")
        else:
            roles = set(role_names) if everyone else audience
            created = db.execute("""
                INSERT INTO announcements
                    (title, body, href, audience, created_by, created_by_name)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (title, body, href,
                  "all" if everyone else ",".join(sorted(roles)),
                  session.get("user_id"), current_user()["full_name"]))
            delivered = 0
            for account in db.execute(
                    "SELECT id, role FROM users WHERE status = 'Active'").fetchall():
                held = {account["role"]} | set(user_extra_roles(db, account["id"]))
                if held & roles:
                    deliver_notification(
                        db, account["id"], title, body, href,
                        category="announcement", icon="mail", tone="info",
                        dedupe_key="announcement-{}".format(created.lastrowid))
                    delivered += 1
            db.commit()
            log_activity("CREATE", "accounts",
                         "Announcement sent to {} account{}".format(
                             delivered, "" if delivered == 1 else "s"),
                         record_type="announcement", record_id=created.lastrowid,
                         details={"audience": sorted(roles), "title": title})
            flash("Announcement delivered to {} account{}.".format(
                delivered, "" if delivered == 1 else "s"), "success")
            return redirect(url_for("admin_notifications"))

    rows = db.execute("""
        SELECT a.*,
               (SELECT COUNT(*) FROM notifications AS n
                WHERE n.dedupe_key = 'announcement-' || a.id) AS delivered
        FROM announcements AS a
        ORDER BY a.id DESC LIMIT 50
    """).fetchall()
    return render_template("Admin/notifications.html", announcements=rows,
                           roles=[key for key, _ in choices],
                           role_labels=role_names)


@route("/admin/roles/<int:user_id>", methods=["GET", "POST"])
@perm("accounts")
def admin_role_account(user_id):
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        abort(404)

    choices = role_choices(db)
    role_names = dict(choices)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "extra_roles":
            if target["role"] == "admin":
                flash("Administrators already hold every module.", "error")
                return redirect(url_for("admin_role_account", user_id=user_id))
            assignable = {key for key, _ in choices}.difference(
                {"admin", "member", target["role"]})
            selected = set(request.form.getlist("extra_roles")) & assignable
            current = set(user_extra_roles(db, user_id))
            if selected == current:
                flash("No additional role changed.", "info")
                return redirect(url_for("admin_role_account", user_id=user_id))
            db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            for key in sorted(selected):
                db.execute("INSERT INTO user_roles (user_id, role_key) VALUES (?, ?)",
                           (user_id, key))
            deliver_notification(
                db, user_id, "Additional roles updated",
                "Your account now also carries: {}. Those roles widen the modules"
                " you can open; your home dashboard stays with your primary role."
                .format(", ".join(role_names.get(key, key) for key in sorted(selected))
                        or "no additional role"),
                url_for("dashboard"), category="account", icon="shield", tone="info")
            db.commit()
            log_activity("UPDATE", "accounts",
                         "Additional roles set for {} ({})".format(
                             target["full_name"],
                             ", ".join(sorted(selected)) or "none"),
                         record_type="user", record_id=user_id,
                         details={"extra_roles": sorted(selected),
                                  "primary_role": target["role"]})
            flash("Additional roles saved for {}.".format(target["full_name"]),
                  "success")
            return redirect(url_for("admin_role_account", user_id=user_id))

        if target["role"] == "admin":
            flash("Administrators always hold every module.", "error")
            return redirect(url_for("admin_roles"))
        granted = set(request.form.getlist("permissions")) & set(MODULES)
        defaults = set(role_permission_set(target["role"], db))
        for key in user_extra_roles(db, user_id):
            defaults |= role_permission_set(key, db)
        db.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
        for module in MODULES:
            if (module in granted) != (module in defaults):
                db.execute("""
                    INSERT INTO user_permissions (user_id, module, allowed, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, module, int(module in granted)))
        deliver_notification(
            db, user_id, "Module access updated",
            "An administrator set your modules to: {}. Your dashboard and"
            " notifications follow that list.".format(
                ", ".join(MODULE_LABEL.get(m, m) for m in sorted(granted))
                or "none"),
            url_for("dashboard"), category="account",
            icon="shield", tone="info")
        db.commit()
        log_activity("UPDATE", "accounts",
                     "Module permissions set for {} ({})".format(
                         target["full_name"],
                         ", ".join(sorted(granted)) or "no modules"),
                     record_type="user", record_id=user_id,
                     details={"permissions": sorted(granted)})
        flash("Permissions saved for {}.".format(target["full_name"]), "success")
        return redirect(url_for("admin_roles"))

    assignment = db.execute("""
        SELECT d.name AS department_name, p.title AS position_title,
               p.level AS position_level
        FROM users AS u
        LEFT JOIN departments AS d ON d.id = u.department_id
        LEFT JOIN organizational_positions AS p ON p.id = u.position_id
        WHERE u.id = ?
    """, (user_id,)).fetchone()

    extra_keys = set(user_extra_roles(db, user_id))
    primary_defaults = set(role_permission_set(target["role"], db))
    extra_defaults = {}
    role_options = []
    baseline = set(primary_defaults)
    for key, label in choices:
        modules = role_permission_set(key, db)
        if key in extra_keys:
            extra_defaults[key] = sorted(modules)
            baseline |= modules
        if key not in ("admin", "member", target["role"]):
            role_options.append({
                "key": key,
                "label": label,
                "modules": [MODULE_LABEL.get(module, module)
                            for module in sorted(modules)],
            })

    return render_template("Admin/role_account.html", target=target,
                           granted=effective_permissions(target),
                           defaults=baseline, primary_defaults=primary_defaults,
                           extra_keys=extra_keys, extra_defaults=extra_defaults,
                           role_options=role_options,
                           modules=MODULES, module_label=MODULE_LABEL,
                           role_labels=role_names, assignment=assignment,
                           position_level_label=POSITION_LEVEL_LABEL,
                           approver_desk=None if target["role"] == "admin"
                           else approver_desk_label(target, db))


@route("/management/approval-settings", methods=["GET", "POST"])
@perm("approvals")
def management_approval_settings():
    user = current_user()

    if not can_manage_approval_policy(user):
        abort(403)

    init_management_policy()

    if request.method == "POST":
        try:
            manager_limit = Decimal(
                request.form.get("manager_limit", "").strip()
            )
            director_limit = Decimal(
                request.form.get("director_limit", "").strip()
            )

            if (
                not manager_limit.is_finite()
                or not director_limit.is_finite()
                or manager_limit < 0
                or director_limit <= manager_limit
            ):
                raise ValueError("Invalid approval limits")

        except (InvalidOperation, ValueError):
            flash(
                "Enter valid limits. The Director limit must exceed "
                "the Manager limit.",
                "error",
            )
            return redirect(url_for("management_approval_settings"))

        db = get_db()
        db.execute("""
            UPDATE management_approval_policy
            SET manager_limit = ?,
                director_limit = ?,
                updated_by = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            float(manager_limit),
            float(director_limit),
            user["full_name"],
        ))

        routes = approval_route_map()
        retuned = []
        empty_desks = []
        for department in APPROVAL_DEPARTMENTS:
            step = request.form.get("route_" + department, "").strip()
            if step not in APPROVAL_STEPS or step == routes[department]:
                continue
            db.execute("""
                INSERT INTO approval_routes (department, first_step, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(department) DO UPDATE SET
                    first_step = excluded.first_step,
                    updated_at = excluded.updated_at
            """, (department, step))
            retuned.append("%s: %s" % (department, step))
            if step_holders(step) == 0:
                empty_desks.append("%s -> %s" % (department, STEP_LABEL[step]))
        db.commit()

        log_activity(
            "UPDATE",
            "management",
            "Approval policy updated by " + user["full_name"],
            record_type="policy",
            details={
                "manager_limit": float(manager_limit),
                "director_limit": float(director_limit),
                "routes": retuned,
            },
        )

        flash("Approval policy updated successfully.", "success")
        if empty_desks:
            flash("No active account holds the desk for {} yet; requests from there "
                  "wait until an account is assigned a position at that "
                  "level.".format(", ".join(empty_desks)), "info")
        return redirect(url_for("management_approval_settings"))

    policy = get_approval_policy()
    routes = approval_route_map()

    # Show the chain each department would get under the saved numbers, at the
    # three amounts that matter, so the screen proves the rule instead of
    # restating it.
    probes = (max(policy["manager_limit"] - 1, 0),
              max(policy["director_limit"] - 1, 0),
              policy["director_limit"])
    chain_probe = {
        department: [
            [STEP_LABEL.get(step, step) for step in approval_path(department, amount)]
            for amount in probes
        ]
        for department in APPROVAL_DEPARTMENTS
    }

    return render_template(
        "management/approval_settings.html",
        policy=policy,
        routes=routes,
        desk_holders={step: step_holders(step) for step in APPROVAL_STEPS},
        chain_probe=chain_probe,
        probe_amounts=probes,
        departments=tuple(APPROVAL_DEPARTMENTS),
        steps=APPROVAL_STEPS,
        step_label=STEP_LABEL,
    )


COMPANY_KEYS = ("company_name", "company_email", "company_phone",
                "company_address", "currency")


@route("/admin/settings", methods=["GET", "POST"])
@perm("accounts")
def admin_settings():
    db = get_db()
    init_hr_settings()

    if request.method == "POST":
        values = {key: request.form.get(key, "").strip() for key in COMPANY_KEYS}
        if not values["company_name"]:
            flash("The company name cannot be blank.", "error")
            return redirect(url_for("admin_settings"))
        if values["company_email"] and "@" not in values["company_email"]:
            flash("Enter a valid company email or leave it blank.", "error")
            return redirect(url_for("admin_settings"))
        for key, value in values.items():
            db.execute("""
                INSERT INTO hr_settings (setting_key, setting_value) VALUES (?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value
            """, (key, value))
        db.commit()
        log_activity("UPDATE", "accounts", "Company profile updated",
                     record_type="setting", details=dict(values))
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))

    stored = {row["setting_key"]: row["setting_value"] for row in db.execute(
        "SELECT setting_key, setting_value FROM hr_settings").fetchall()}
    rules = {
        "Approval thresholds": "{:,.0f} ETB for two steps · {:,.0f} ETB for three".format(
            *approval_limits()),
        "Password policy": "Minimum 8 characters, scrypt hash, forced change on first sign-in",
        "Roles in use": ", ".join(ROLE_LABEL.get(role, role) for role in USER_ROLES),
        "Modules available": ", ".join(MODULE_LABEL.get(m, m) for m in MODULES),
    }
    return render_template("Admin/settings.html", stored=stored,
                           defaults=DEFAULT_SETTINGS, rules=rules,
                           mail=mail_status(),
                           mail_counts=delivery_summary(db),
                           deliveries=recent_deliveries(db, 8))
