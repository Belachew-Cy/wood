"""Admin console, accounts, roles, settings and notifications."""

from core.access import current_user
from core.access import account_department_id
from core.access import account_role_keys
from core.access import custom_role_key
from core.access import effective_granted_permissions
from core.access import effective_permissions
from core.access import perm
from core.access import permission_modules
from core.access import role_choices
from core.access import role_catalogue
from core.access import role_granted_permissions
from core.access import role_is_active
from core.access import role_name_for
from core.access import role_permission_set
from core.access import user_extra_roles
from core.config import ALL_PERMISSION_CATEGORIES
from core.config import DASHBOARD_ENDPOINT
from core.config import DEPARTMENT_ACTIONS
from core.config import DEPARTMENT_PERMISSION_BY_KEY
from core.config import DEPARTMENT_PERMISSION_KEYS
from core.config import DEPARTMENT_SCOPES
from core.config import department_permission_key
from core.config import MODULES
from core.config import MODULE_LABEL
from core.config import PERMISSION_BY_KEY
from core.config import PERMISSION_HIRING_RIGHTS
from core.config import PERMISSION_KEYS
from core.config import RECRUITMENT_PERMISSION_KEYS
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
from departments.HR.hr import DEFAULT_SETTINGS
from departments.HR.hr import init_hr_settings
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
from services.department_access import apply_suggested
from services.department_access import bind_role
from services.department_access import create_standard_roles
from services.department_access import department_access_snapshot
from services.department_access import standard_role_groups
from services.department_access import standard_role_presets
from services.department_access import sync_all_standard_roles
from services.department_access import unbind_role
from services.email import delivery_summary
from services.email import mail_status
from services.email import recent_deliveries
from services.hr import can_view_documents
from services.hr import can_view_salary
from services.hr import create_employee_record
from services.hr import read_employee_form
from services.hr import stored_document_labels
from services.hr import update_employee_record
from services.letters import LETTER_TYPE_HINT
from services.letters import LETTER_TYPE_LABEL
from services.letters import LETTER_TYPES
from services.letters import LETTER_VIEW_BASE
from services.letters import LETTER_PERMISSION_LABEL
from services.letters import LETTER_PERMISSION_NOTE
from services.letters import LETTER_PERMISSIONS
from services.letters import letter_category_set
from services.letters import letter_permission_set
from services.letters import stored_letter_categories
from services.letters import stored_letter_permissions
from services.letters import stored_letter_role_permissions
from services.links import can_manage_approval_policy
from services.notifications import deliver_notification
from services.recruitment import HIRING_MODULE_RIGHTS
from services.recruitment import HIRING_RIGHTS
from services.recruitment import HIRING_RIGHT_LABEL
from services.recruitment import HIRING_ROLE_BASELINE
from services.recruitment import HIRING_VIEW_BASE
from services.recruitment import hiring_baseline
from services.recruitment import hiring_rights
from services.recruitment import role_rights
from services.recruitment import stored_role_rights
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
@perm("admin_access")
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
        actor = current_user()
        if actor["role"] != "admin":
            if user_id == actor["id"]:
                refuse_delegation(
                    "change to own letter permissions",
                    "You cannot change your own letter permissions. Ask another"
                    " administrator to do it.", "user", user_id)
                return redirect(url_for("admin_letter_permissions",
                                        user_id=user_id))
            guard = account_guard_problem(db, actor, target)
            if guard:
                refuse_delegation(
                    "change to letter permissions of {}".format(
                        target["full_name"]), guard, "user", user_id)
                return redirect(url_for("admin_letter_permissions",
                                        user_id=user_id))
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
        problem = delegation_problem(db, actor, set(), letters=selected)
        if problem:
            refuse_delegation(
                "letter grant to {}".format(target["full_name"]), problem,
                "user", user_id)
            return redirect(url_for("admin_letter_permissions",
                                    user_id=user_id))
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


@route("/admin/hiring-permissions/role/<role_key>", methods=["GET", "POST"])
@perm("admin_access")
def admin_hiring_role_permissions(role_key):
    """What a role is worth in the hiring desks, tick by tick.

    A role with no rows here falls back to the baseline the module has always
    carried, so nothing changes until an administrator writes a matrix - and a custom
    role the system never knew about gets its rights from this screen alone.
    """
    db = get_db()
    role = db.execute("SELECT * FROM roles WHERE key = ?", (role_key,)).fetchone()
    if role is None:
        abort(404)

    if request.method == "POST":
        actor = current_user()
        if actor["role"] != "admin":
            if role_key in set(account_role_keys(db, actor)):
                refuse_delegation(
                    "change to hiring rights of own role '{}'".format(
                        role["name"]),
                    "You cannot change the hiring rights of a role your own"
                    " account carries.", "role", role_key)
                return redirect(url_for(
                    "admin_hiring_role_permissions", role_key=role_key))
            if not department_manageable(db, actor, role["department_id"]):
                refuse_delegation(
                    "change to hiring rights of role '{}'".format(role["name"]),
                    "You can only manage roles in your own department and the"
                    " units below it.", "role", role_key)
                return redirect(url_for(
                    "admin_hiring_role_permissions", role_key=role_key))
        if request.form.get("action") == "reset":
            db.execute("DELETE FROM hiring_role_permissions WHERE role_key = ?",
                       (role_key,))
            db.commit()
            log_activity("UPDATE", "accounts",
                         "Hiring rights for the {} role cleared to defaults"
                         .format(role["name"]),
                         record_type="role", record_id=role_key)
            flash("The {} role is back on its defaults.".format(role["name"]),
                  "success")
            return redirect(url_for("admin_hiring_role_permissions", role_key=role_key))
        selected = set(request.form.getlist("rights"))
        if not selected.issubset(set(HIRING_RIGHTS)):
            abort(400)
        selected |= HIRING_VIEW_BASE
        problem = delegation_problem(db, actor, set(), rights=selected)
        if problem:
            refuse_delegation(
                "hiring grant to role '{}'".format(role["name"]), problem,
                "role", role_key)
            return redirect(url_for(
                "admin_hiring_role_permissions", role_key=role_key))
        db.execute("DELETE FROM hiring_role_permissions WHERE role_key = ?", (role_key,))
        for right in HIRING_RIGHTS:
            db.execute("INSERT INTO hiring_role_permissions (role_key, right, allowed)"
                       " VALUES (?, ?, ?)",
                       (role_key, right, 1 if right in selected else 0))
        db.commit()
        log_activity("UPDATE", "accounts",
                     "Hiring rights set for the {} role: {}".format(
                         role["name"], ", ".join(sorted(selected)) or "none"),
                     record_type="role", record_id=role_key,
                     details={"rights": sorted(selected)})
        flash("Hiring rights saved for the {} role.".format(role["name"]), "success")
        return redirect(url_for("admin_hiring_role_permissions", role_key=role_key))

    has_rows, stored = stored_role_rights(db, role_key)
    return render_template(
        "Admin/hiring_permissions.html",
        kind="role", target=role, target_name=role["name"],
        subtitle="{} role".format(role["name"]),
        back=url_for("admin_roles"),
        permissions=[(key, HIRING_RIGHT_LABEL[key]) for key in HIRING_RIGHTS],
        granted=(stored if has_rows else
                 set(HIRING_ROLE_BASELINE.get(role_key, ()))),
        using_defaults=not has_rows,
        blocked=set())


@route("/admin/hiring-permissions/<int:user_id>", methods=["GET", "POST"])
@perm("admin_access")
def admin_hiring_permissions(user_id):
    """The hiring rights one account holds, narrowed or widened beside its role.

    The ticks shown are what the account holds right now - its roles, its module
    ticks and, for a Manager or above, its position. Saving freezes that picture as
    the account's own grant; clearing the rows hands it back to its inheritance.
    """
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        abort(404)

    if request.method == "POST":
        actor = current_user()
        if actor["role"] != "admin":
            if user_id == actor["id"]:
                refuse_delegation(
                    "change to own hiring rights",
                    "You cannot change your own hiring rights. Ask another"
                    " administrator to do it.", "user", user_id)
                return redirect(url_for("admin_hiring_permissions",
                                        user_id=user_id))
            guard = account_guard_problem(db, actor, target)
            if guard:
                refuse_delegation(
                    "change to hiring rights of {}".format(target["full_name"]),
                    guard, "user", user_id)
                return redirect(url_for("admin_hiring_permissions",
                                        user_id=user_id))
        if request.form.get("action") == "reset":
            db.execute("DELETE FROM hiring_user_permissions WHERE user_id = ?",
                       (user_id,))
            db.commit()
            log_activity("UPDATE", "accounts",
                         "Hiring rights for {} cleared to inheritance".format(
                             target["full_name"]),
                         record_type="user", record_id=user_id)
            flash("{} is back on the rights their roles give.".format(
                target["full_name"]), "success")
            return redirect(url_for("admin_hiring_permissions", user_id=user_id))
        selected = set(request.form.getlist("rights"))
        if not selected.issubset(set(HIRING_RIGHTS)):
            abort(400)
        selected |= HIRING_VIEW_BASE
        problem = delegation_problem(db, actor, set(), rights=selected)
        if problem:
            refuse_delegation(
                "hiring grant to {}".format(target["full_name"]), problem,
                "user", user_id)
            return redirect(url_for("admin_hiring_permissions",
                                    user_id=user_id))
        for right in HIRING_RIGHTS:
            db.execute("""
                INSERT INTO hiring_user_permissions (user_id, right, allowed,
                                                     granted_by, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, right) DO UPDATE SET
                    allowed = excluded.allowed,
                    granted_by = excluded.granted_by,
                    updated_at = excluded.updated_at
            """, (user_id, right, 1 if right in selected else 0,
                  current_user()["id"]))
        db.commit()
        log_activity("UPDATE", "accounts",
                     "Hiring rights set for {}: {}".format(
                         target["full_name"],
                         ", ".join(sorted(selected)) or "none"),
                     record_type="user", record_id=user_id,
                     details={"rights": sorted(selected)})
        deliver_notification(
            db, user_id, "Hiring rights updated",
            "An administrator set your hiring desk rights to: {}."
            .format(", ".join(HIRING_RIGHT_LABEL[r] for r in sorted(selected))
                    or "none - the hiring screens are closed to you"),
            url_for("dashboard"), category="account", icon="shield", tone="info")
        db.commit()
        flash("Hiring rights saved for {}.".format(target["full_name"]), "success")
        return redirect(url_for("admin_hiring_permissions", user_id=user_id))

    role_name = role_name_for(target["role"], db)
    return render_template(
        "Admin/hiring_permissions.html",
        kind="user", target=target, target_name=target["full_name"],
        subtitle="{} role{}".format(role_name,
                                    " · administrator - every right is held"
                                    if target["role"] == "admin" else ""),
        back=url_for("admin_role_account", user_id=user_id),
        permissions=[(key, HIRING_RIGHT_LABEL[key]) for key in HIRING_RIGHTS],
        granted=set(hiring_rights(db, target)),
        using_defaults=not db.execute(
            "SELECT 1 FROM hiring_user_permissions WHERE user_id = ?",
            (user_id,)).fetchone(),
        blocked=(set(HIRING_RIGHTS) if target["role"] == "admin" else set()))


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

    db = get_db()
    employees = db.execute(
        "SELECT *, job_title AS position FROM employees ORDER BY employee_code"
    ).fetchall()

    # The System Integration desk is part of the admin desk, so its health is
    # summarised here instead of only behind its own sidebar entry.
    integration_stats = {"total": 0, "active": 0, "scheduled": 0, "open_errors": 0}
    integration_rows = []
    try:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN sync_enabled = 1 THEN 1 ELSE 0 END) AS scheduled
            FROM integrations
        """).fetchone()
        integration_stats = {
            "total": row["total"] or 0,
            "active": row["active"] or 0,
            "scheduled": row["scheduled"] or 0,
            "open_errors": db.execute(
                "SELECT COUNT(*) AS n FROM integration_errors WHERE resolved = 0"
            ).fetchone()["n"],
        }
        integration_rows = db.execute("""
            SELECT i.id, i.name, i.integration_type, i.status, i.sync_enabled,
                   i.sync_interval, i.last_sync_at, i.next_run_at,
                   (SELECT status FROM integration_connection_tests t
                       WHERE t.integration_id = i.id ORDER BY t.id DESC LIMIT 1)
                       AS last_test,
                   (SELECT COUNT(*) FROM integration_errors e
                       WHERE e.integration_id = i.id AND e.resolved = 0)
                       AS open_errors
            FROM integrations i
            ORDER BY i.name COLLATE NOCASE LIMIT 6
        """).fetchall()
    except sqlite3.Error:
        # A dashboard must still render on a database that has not been migrated.
        pass

    return render_template(
        "Admin/admin.html",
        projects=projects,
        employees=employees,
        integration_stats=integration_stats,
        integration_rows=integration_rows,
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
@perm("admin_users")
def admin_users():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")
        actor = current_user()

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
                problem = role_delegation_problem(db, actor, role)
                if problem:
                    refuse_delegation(
                        "creation of account {}".format(full_name[:80]),
                        problem, "user")
                    return redirect(url_for("admin_users"))
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
            guard = account_guard_problem(db, actor, target)
            role_guard = None
            if (target is not None and role in USER_ROLES
                    and role != target["role"]):
                role_guard = role_delegation_problem(db, actor, role)
            last_admin = (target is not None and target["role"] == "admin"
                          and last_active_admin(db, uid)
                          and (role != "admin" or status != "Active"))

            if target is None:
                flash("That account no longer exists.", "error")
            elif guard:
                refuse_delegation(
                    "edit of account {}".format(target["full_name"]),
                    guard, "user", uid)
                return redirect(url_for("admin_users"))
            elif role_guard:
                refuse_delegation(
                    "role change for {} to '{}'".format(
                        target["full_name"], role), role_guard, "user", uid)
                return redirect(url_for("admin_users"))
            elif last_admin:
                refuse_delegation(
                    "edit of account {}".format(target["full_name"]),
                    "This is the last active administrator. Promote or"
                    " activate another administrator first.", "user", uid)
                return redirect(url_for("admin_users"))
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
                        db.execute("DELETE FROM user_permission_grants"
                                   " WHERE user_id = ?", (uid,))
                        revoked = "; custom permission ticks cleared"
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
                target = db.execute("SELECT * FROM users WHERE id = ?",
                                    (uid,)).fetchone()
                guard = account_guard_problem(db, actor, target)
                if target is None:
                    flash("That account no longer exists.", "error")
                elif guard:
                    refuse_delegation(
                        "password reset for {}".format(target["full_name"]),
                        guard, "user", uid)
                    return redirect(url_for("admin_users"))
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
                skipped_guard = []
                for raw in request.form.getlist("ids"):
                    try:
                        uid = int(raw)
                    except (TypeError, ValueError):
                        continue
                    if uid == session.get("user_id"):
                        skipped_self += 1
                        continue
                    target = db.execute(
                        "SELECT * FROM users WHERE id = ?", (uid,)
                    ).fetchone()
                    if target is None:
                        continue
                    problem = account_guard_problem(db, actor, target)
                    if (not problem and status == "Disabled"
                            and target["role"] == "admin"
                            and last_active_admin(db, uid)):
                        problem = ("This is the last active administrator and"
                                   " cannot be disabled.")
                    if problem:
                        skipped_guard.append(target["full_name"])
                        log_activity(
                            "DENY", "accounts",
                            "Refused status change for {}: {}".format(
                                target["full_name"], problem),
                            outcome="DENIED", record_type="user", record_id=uid)
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
                if skipped_guard:
                    flash("{} account{} left unchanged: {}. Ask an"
                          " administrator with full access.".format(
                              len(skipped_guard),
                              "" if len(skipped_guard) == 1 else "s",
                              ", ".join(skipped_guard[:5])), "error")
                if not changed and not skipped_self and not skipped_guard:
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
            guard = account_guard_problem(db, actor, target)
            if target is None:
                flash("That account no longer exists.", "error")
            elif guard:
                refuse_delegation(
                    "{} of account {}".format(
                        "lock" if action == "lock_user" else "unlock",
                        target["full_name"]), guard, "user", uid)
                return redirect(url_for("admin_users"))
            elif action == "lock_user" and uid == session.get("user_id"):
                flash("You cannot lock your own account.", "error")
            elif action == "lock_user":
                if target["role"] == "admin" and last_active_admin(db, uid):
                    refuse_delegation(
                        "lock of account {}".format(target["full_name"]),
                        "This is the last active administrator and cannot be"
                        " locked.", "user", uid)
                    return redirect(url_for("admin_users"))
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
            guard = account_guard_problem(db, actor, target)
            if target is None:
                flash("That account no longer exists.", "error")
            elif uid == session.get("user_id"):
                flash("You cannot delete your own account.", "error")
            elif guard:
                refuse_delegation(
                    "deletion of account {}".format(target["full_name"]),
                    guard, "user", uid)
                return redirect(url_for("admin_users"))
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
                        "DELETE FROM user_permission_grants WHERE user_id = ?",
                        "DELETE FROM hiring_user_permissions WHERE user_id = ?",
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
@perm("admin_users")
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
@perm("admin_users")
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
                    problem = role_delegation_problem(
                        db, current_user(), fields["role"])
                    if problem:
                        reject(line, values, problem)
                        log_activity(
                            "DENY", "accounts",
                            "Refused import row {}: {}".format(line, problem),
                            outcome="DENIED", record_type="user",
                            details={"file": filename, "line": line,
                                     "email": fields["email"][:254]})
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


# The catalogue keys that open a module; the recruitment keys are answered by the
# hiring tables instead and never widen the module list. The department action
# rows carry their desk's module too, but they are stored separately so an
# ordinary catalogue save can never rewrite or erase the action matrix.
MODULE_PERMISSION_KEYS = tuple(
    key for key in PERMISSION_KEYS
    if PERMISSION_BY_KEY[key].get("modules")
    and key not in DEPARTMENT_PERMISSION_BY_KEY)


def permission_catalog(selected, recruitment_rights=(), locked_rights=(),
                       forced=(), letters=None):
    """The catalogue with each row's tick state for one role or account.

    Recruitment rows are drawn from the hiring rights they stand for: a row is
    ticked when every right behind it is held and marked partial when only some
    are, so a matrix an administrator narrowed elsewhere is shown as it stands.
    A right a module hands out on its own is locked, because unticking it could
    not take it away. Letter actions are appended to the letters category when a
    role's own matrix is being drawn; the account editor leaves them to the
    dedicated letter permissions screen.
    """
    selected = set(selected)
    recruitment_rights = set(recruitment_rights)
    locked_rights = set(locked_rights)
    forced = set(forced)
    categories = []
    for category in ALL_PERMISSION_CATEGORIES:
        rows = []
        open_count = 0
        for permission in category["permissions"]:
            rights = PERMISSION_HIRING_RIGHTS.get(permission["key"])
            row = dict(permission)
            row["kind"] = "recruitment" if rights else "module"
            if rights:
                held = [right for right in rights if right in recruitment_rights]
                row["checked"] = len(held) == len(rights)
                row["partial"] = bool(held) and not row["checked"]
                row["locked"] = all(right in locked_rights for right in rights)
            else:
                row["checked"] = (permission["key"] in selected
                                  or permission["key"] in forced)
                row["partial"] = False
                row["locked"] = permission["key"] in forced
            if row["checked"]:
                open_count += 1
            rows.append(row)
        if category["key"] == "letters" and letters is not None:
            for key in LETTER_PERMISSIONS:
                row = {
                    "key": key,
                    "label": LETTER_PERMISSION_LABEL[key],
                    "description": LETTER_PERMISSION_NOTE[key],
                    "kind": "letter",
                    "checked": key in letters,
                    "partial": False,
                    "locked": False,
                }
                if row["checked"]:
                    open_count += 1
                rows.append(row)
        categories.append({
            "key": category["key"], "label": category["label"],
            "icon": category["icon"], "note": category["note"],
            "permissions": rows, "open": open_count, "total": len(rows),
        })
    return categories


def department_matrix(selected, readonly=False, scope_users=None):
    """The 17 departments by ten standard actions grid, for one role or account.

    The ticks are the effective action rights, not just the stored rows, so a
    role that has never been given an explicit matrix shows the baseline its
    modules infer. Saving that grid stores every row explicitly, which is what
    pins the selection from then on.
    """
    selected = set(selected)
    scope_users = scope_users or {}
    scopes = []
    for scope in DEPARTMENT_SCOPES:
        cells = []
        for action in DEPARTMENT_ACTIONS:
            key = department_permission_key(scope["key"], action["key"])
            cells.append({
                "key": key,
                "label": action["label"],
                "short": action["short"],
                "checked": key in selected,
                "disabled": readonly,
            })
        scopes.append({
            "key": scope["key"],
            "label": scope["label"],
            "note": scope["note"],
            "users": scope_users.get(scope["key"]),
            "actions": cells,
        })
    return {
        "actions": [dict(action) for action in DEPARTMENT_ACTIONS],
        "scopes": scopes,
        "checked": sorted(key for key in selected
                          if key in DEPARTMENT_PERMISSION_BY_KEY),
        "open": sum(1 for key in selected if key in DEPARTMENT_PERMISSION_BY_KEY),
        "total": len(DEPARTMENT_PERMISSION_KEYS),
        "readonly": readonly,
    }


def department_scope_users(accounts):
    """How many active accounts can already act in each department desk."""
    counts = {scope["key"]: 0 for scope in DEPARTMENT_SCOPES}
    for account in accounts:
        user = account["user"]
        if user["status"] != "Active":
            continue
        granted = effective_granted_permissions(user)
        for scope in DEPARTMENT_SCOPES:
            if any(department_permission_key(scope["key"], action["key"])
                   in granted for action in DEPARTMENT_ACTIONS):
                counts[scope["key"]] += 1
    return counts


def submitted_department_actions(form):
    """The ticked action keys, or None when the form did not carry the grid.

    A screen that predates the grid - or a partial save - must leave the stored
    matrix exactly as it stands instead of writing an empty one.
    """
    if form.get("dept_matrix") != "1":
        return None
    return set(form.getlist("department_action")) & set(DEPARTMENT_PERMISSION_KEYS)


def department_manageable(db, actor, department_id):
    """Whether this actor may file or bind a role in that unit.

    An administrator manages the whole company. Everyone else is limited to
    their own unit and the units below it: the parent chain of the target has
    to reach the account's own department.
    """
    if actor["role"] == "admin":
        return True
    if not department_id:
        return False
    own = account_department_id(db, actor)
    if not own:
        return False
    seen = set()
    cursor = department_id
    while cursor and cursor not in seen:
        if cursor == own:
            return True
        seen.add(cursor)
        row = db.execute("SELECT parent_id FROM departments WHERE id = ?",
                         (cursor,)).fetchone()
        cursor = row["parent_id"] if row else None
    return False


def delegation_problem(db, actor, selected, department_actions=(), letters=(),
                       rights=()):
    """Why a non-admin may not hand out this set, or None when it is in bounds.

    A role editor can never hand out a permission, module, department action,
    letter action or hiring right it does not hold itself. This is the rule that
    stops a delegated editor from turning its own role into an administrator.
    """
    if actor is None or actor["role"] == "admin":
        return None
    held = effective_granted_permissions(actor)
    extra = (set(selected) - set(RECRUITMENT_PERMISSION_KEYS)) - held
    if extra:
        return "You cannot grant permissions you do not hold: {}.".format(
            ", ".join(sorted(extra)[:6]))
    extra_modules = (set(permission_modules(selected))
                     - effective_permissions(actor))
    if extra_modules:
        return "You cannot open modules you do not hold: {}.".format(
            ", ".join(MODULE_LABEL.get(module, module)
                      for module in sorted(extra_modules)))
    extra_department = set(department_actions) - held
    if extra_department:
        return "You cannot grant department actions you do not hold: {}.".format(
            ", ".join(sorted(extra_department)[:6]))
    extra_letters = set(letters) - set(letter_permission_set(actor))
    if extra_letters:
        return "You cannot grant letter actions you do not hold: {}.".format(
            ", ".join(sorted(extra_letters)))
    extra_rights = set(rights) - set(hiring_rights(db, actor))
    if extra_rights:
        return "You cannot grant hiring rights you do not hold: {}.".format(
            ", ".join(sorted(extra_rights)))
    return None


def role_delegation_problem(db, actor, role_key):
    """Whether handing out, editing or activating a role stays in bounds."""
    if actor is None or actor["role"] == "admin":
        return None
    granted = role_granted_permissions(db, role_key)
    modules = role_permission_set(role_key, db)
    rights = set(role_rights(db, role_key)) | role_module_hiring_rights(modules)
    return delegation_problem(
        db, actor, granted,
        department_actions=granted & set(DEPARTMENT_PERMISSION_KEYS),
        letters=role_letter_actions(db, role_key, modules),
        rights=rights)


def refuse_delegation(description, problem, record_type, record_id=None):
    """Record and show a refused access change."""
    log_activity("DENY", "accounts",
                 "Refused {}: {}".format(description, problem),
                 outcome="DENIED", record_type=record_type,
                 record_id=record_id)
    flash(problem, "error")


def account_guard_problem(db, actor, target):
    """Why a non-administrator may not change this account at all."""
    if actor is None or actor["role"] == "admin" or target is None:
        return None
    if target["role"] == "admin":
        return "Only an administrator can change an administrator account."
    over = set(effective_granted_permissions(target)) - set(
        effective_granted_permissions(actor))
    if over:
        return ("{} already holds access your account does not. An"
                " administrator has to make this change.".format(
                    target["full_name"]))
    return None


def last_active_admin(db, user_id):
    """True when demoting, disabling or locking this account leaves no admin."""
    row = db.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
        " AND status = 'Active' AND id <> ?", (user_id,)).fetchone()
    return not row["n"]


def role_hiring_rights(db, role_key, modules):
    """The hiring rights a role is worth once its module ticks are counted."""
    rights = set(role_rights(db, role_key))
    for module, module_rights in HIRING_MODULE_RIGHTS.items():
        if module in modules:
            rights |= module_rights
    return rights


def role_module_hiring_rights(modules):
    """The rights the role's modules hand out whatever its own matrix says."""
    rights = set()
    for module, module_rights in HIRING_MODULE_RIGHTS.items():
        if module in modules:
            rights |= module_rights
    return rights


def role_letter_actions(db, role_key, modules):
    """The letter actions a role's dialog opens on, stored matrix first."""
    has_rows, letters = stored_letter_role_permissions(db, role_key)
    if not has_rows:
        letters = set(LETTER_VIEW_BASE) if "letters" in modules else set()
    return letters


def desired_hiring_rights(selected, keep, current):
    """The hiring rights a submitted catalogue asks for.

    A ticked category grants every right behind it, an untouched partial row keeps
    the rights it already had, and a cleared row takes its rights away. Rights no
    category speaks for - the audit desk's read-only right - are left alone.
    """
    mapped = set()
    desired = set()
    for key, rights in PERMISSION_HIRING_RIGHTS.items():
        mapped |= set(rights)
        if key in selected:
            desired |= set(rights)
        elif key in keep:
            desired |= set(rights) & set(current)
    desired |= set(current) - mapped
    return desired


def write_role_letters(db, role_key, form):
    """Write the role's letter matrix from the ticks under the register heading."""
    letters = set(form.getlist("letter_permissions"))
    if not letters.issubset(set(LETTER_PERMISSIONS)):
        abort(400)
    if letters:
        letters |= LETTER_VIEW_BASE
    db.execute("DELETE FROM letter.letter_role_permissions WHERE role_key = ?",
               (role_key,))
    for permission in LETTER_PERMISSIONS:
        db.execute("INSERT INTO letter.letter_role_permissions"
                   " (role_key, permission, allowed) VALUES (?, ?, ?)",
                   (role_key, permission, 1 if permission in letters else 0))
    return letters


def save_role_permissions(db, role_key, form):
    """Persist the role dialog's catalogue ticks.

    The module list is derived from the ticked permissions, so the two can never
    disagree, and every module-backed key is stored as an explicit row - that is
    what makes a deliberately empty matrix read as empty rather than as a role
    nobody has configured. Recruitment rows are written through the hiring tables
    the desks already ask, and the department action grid through its own rows.
    """
    selected = set(form.getlist("permission")) & set(PERMISSION_KEYS)
    keep = set(form.getlist("recruitment_keep")) & set(RECRUITMENT_PERMISSION_KEYS)
    modules = [module for module in MODULES
               if module in permission_modules(selected)]
    previous_modules = [row["module"] for row in db.execute(
        "SELECT module FROM role_permissions WHERE role_key = ?", (role_key,))]
    rights = desired_hiring_rights(
        selected, keep, role_hiring_rights(db, role_key, previous_modules))
    module_keys = selected & set(MODULE_PERMISSION_KEYS)

    # The action matrix is written only when its own grid was submitted. An
    # absent marker keeps whatever is stored - including an explicit all-denied
    # matrix, which has rows but grants nothing.
    department_keys = submitted_department_actions(form)
    stored_department_rows = db.execute(
        "SELECT permission, allowed FROM role_permission_grants"
        " WHERE role_key = ? AND permission GLOB 'dept_*'",
        (role_key,)).fetchall()
    matrix_configured = bool(stored_department_rows)
    if department_keys is None:
        department_keys = {row["permission"] for row in stored_department_rows
                           if row["allowed"]}
    else:
        matrix_configured = True

    db.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
    for module in modules:
        db.execute("INSERT INTO role_permissions (role_key, module) VALUES (?, ?)",
                   (role_key, module))
    db.execute("DELETE FROM role_permission_grants WHERE role_key = ?", (role_key,))
    for key in MODULE_PERMISSION_KEYS:
        db.execute("INSERT INTO role_permission_grants (role_key, permission, allowed)"
                   " VALUES (?, ?, ?)",
                   (role_key, key, 1 if key in module_keys else 0))
    if matrix_configured:
        for key in DEPARTMENT_PERMISSION_KEYS:
            db.execute("INSERT INTO role_permission_grants"
                       " (role_key, permission, allowed) VALUES (?, ?, ?)",
                       (role_key, key, 1 if key in department_keys else 0))
    db.execute("DELETE FROM hiring_role_permissions WHERE role_key = ?", (role_key,))
    for right in HIRING_RIGHTS:
        db.execute("INSERT INTO hiring_role_permissions (role_key, right, allowed)"
                   " VALUES (?, ?, ?)",
                   (role_key, right, 1 if right in rights else 0))
    return selected, modules, rights, department_keys


@route("/admin/roles")
@perm("admin_roles")
def admin_roles():
    db = get_db()
    accounts = account_directory(db)
    catalogue = role_catalogue(db)
    role_names = {row["key"]: row["name"] for row in catalogue}
    binding_counts = {row["role_key"]: row["n"] for row in db.execute(
        "SELECT role_key, COUNT(*) AS n FROM department_roles"
        " GROUP BY role_key").fetchall()}
    department = department_access_snapshot(db)
    department_names = {row["id"]: row["name"]
                        for row in department["departments"]}

    extra_holders = {}
    for row in db.execute("""
        SELECT ur.role_key, u.id, u.full_name, u.status
        FROM user_roles AS ur JOIN users AS u ON u.id = ur.user_id
        ORDER BY u.full_name
    """).fetchall():
        extra_holders.setdefault(row["role_key"], []).append(row)

    scope_users = department_scope_users(accounts)

    roles = []
    for catalogue_row in catalogue:
        key, label = catalogue_row["key"], catalogue_row["name"]
        active = (bool(catalogue_row["active"])
                  if "active" in catalogue_row.keys() else True)
        row = db.execute("SELECT * FROM roles WHERE key = ?", (key,)).fetchone()
        modules = [entry["module"] for entry in db.execute(
            "SELECT module FROM role_permissions WHERE role_key = ?"
            " ORDER BY module", (key,)).fetchall()]
        holders = [a for a in accounts if a["user"]["role"] == key]
        letters = role_letter_actions(db, key, modules)
        granted = role_granted_permissions(db, key)
        catalog = permission_catalog(
            granted,
            role_hiring_rights(db, key, modules),
            role_module_hiring_rights(modules),
            letters=letters)
        department_selected = (set(DEPARTMENT_PERMISSION_KEYS)
                               if key == "admin" else
                               granted & set(DEPARTMENT_PERMISSION_KEYS))
        department_id = (int(row["department_id"] or 0)
                         if row is not None and "department_id" in row.keys()
                         else 0)
        is_standard = (bool(row["is_standard"])
                       if row is not None and "is_standard" in row.keys()
                       else False)
        roles.append({
            "role": key,
            "label": label,
            "description": row["description"] if row else "",
            "is_system": bool(row["is_system"]) if row else True,
            "is_standard": is_standard,
            "department_id": department_id,
            "department_name": (department_names.get(department_id, "Unassigned")
                                if department_id else "Company-wide"),
            "active": active,
            "defaults": modules,
            "letters": letters,
            "catalog": catalog,
            "dept_matrix": department_matrix(
                department_selected, readonly=(key == "admin"),
                scope_users=scope_users),
            "dept_open": len(department_selected),
            "dept_total": len(DEPARTMENT_PERMISSION_KEYS),
            "groups": [{"label": entry["label"], "open": entry["open"],
                        "total": entry["total"]}
                       for entry in catalog if entry["open"]],
            "permission_open": sum(entry["open"] for entry in catalog),
            "permission_total": sum(entry["total"] for entry in catalog),
            "landing": DASHBOARD_ENDPOINT.get(key, "dashboard"),
            "members": holders,
            "extras": extra_holders.get(key, []),
            "bindings": binding_counts.get(key, 0),
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

    standard_groups = standard_role_groups(db)
    standard_drift = sum(item["drift"] for group in standard_groups
                         for item in group["roles"])
    department["role_names"] = {row["key"]: row["name"] for row in db.execute(
        "SELECT key, name FROM roles").fetchall()}
    return render_template("Admin/roles.html", roles=roles, accounts=accounts,
                           overrides=overrides, modules=MODULES,
                           module_label=MODULE_LABEL, role_labels=role_names,
                           role_defaults=ROLE_PERMISSIONS,
                           new_catalog=permission_catalog(set(), letters=set()),
                           new_dept_matrix=department_matrix(
                               set(), scope_users=scope_users),
                           department_actions=DEPARTMENT_ACTIONS,
                           department_total=len(DEPARTMENT_PERMISSION_KEYS),
                           deviated=deviated, deviated_ids=deviated_ids,
                           department=department,
                           departments=department["departments"],
                           standard_groups=standard_groups,
                           standard_drift=standard_drift,
                           role_presets=standard_role_presets(db),
                           inactive_total=sum(not role["active"] for role in roles))


@route("/admin/department-access", methods=["GET", "POST"])
@perm("admin_roles")
def admin_department_access():
    """Roles bound to each unit of the organization, managed in one place."""
    db = get_db()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        actor = current_user()
        if actor["role"] != "admin" and action in (
                "create_standard_roles", "sync_standard_roles",
                "apply_suggested"):
            refuse_delegation(
                "bulk access change '{}'".format(action),
                "Only an administrator can create, restore or reapply the"
                " standard role charters.", "role")
            return redirect(url_for("admin_roles", _anchor="bindings"))
        try:
            if action == "create_standard_roles":
                created = create_standard_roles(db)
                db.commit()
                if created:
                    log_activity(
                        "CREATE", "accounts",
                        "Standard department roles created: {}".format(
                            ", ".join(created)),
                        record_type="role",
                        details={"roles": created})
                    flash("{} standard role{} created. Open Department"
                          " bindings and grant them to their units.".format(
                              len(created),
                              "" if len(created) == 1 else "s"),
                          "success")
                else:
                    flash("Every standard role already exists; nothing was"
                          " changed.", "info")
            elif action == "sync_standard_roles":
                summary = sync_all_standard_roles(db)
                db.commit()
                synced = summary["roles"]
                if synced:
                    log_activity(
                        "UPDATE", "accounts",
                        "Standard roles restored to their charter: {}".format(
                            ", ".join(item["name"] for item in synced)),
                        record_type="role",
                        details={"roles": [
                            {"role": item["role"],
                             **item["details"]} for item in synced]})
                    flash("{} standard role{} restored to the rights its"
                          " charter names. Nothing already stored was"
                          " removed.".format(
                              len(synced),
                              "" if len(synced) == 1 else "s"),
                          "success")
                else:
                    flash("Every standard role already carries the rights its"
                          " charter names.", "info")
            elif action == "apply_suggested":
                summary = apply_suggested(db)
                db.commit()
                added = summary["bindings_added"]
                missing = summary["units_missing"]
                log_activity(
                    "UPDATE", "accounts",
                    "Suggested department bindings applied: {} binding(s),"
                    " {} role(s) created".format(
                        len(added), len(summary["roles_created"])),
                    record_type="role",
                    details={"bindings": [
                        {"unit": item["unit"], "role": item["role_key"],
                         "level": item["level"]} for item in added],
                        "roles_created": summary["roles_created"],
                        "units_missing": missing})
                if added:
                    flash("{} binding{} applied. Accounts in those units pick"
                          " the roles up on their next request.".format(
                              len(added), "" if len(added) == 1 else "s"),
                          "success")
                else:
                    flash("Every suggested binding is already stored.", "info")
                if missing:
                    flash("No unit matched: {}. Publish the standard structure"
                          " on the hierarchy screen first.".format(
                              ", ".join(missing)), "error")
                parked = summary.get("roles_inactive") or []
                if parked:
                    flash("These roles are inactive, so their suggestions were"
                          " left unbound: {}. Activate them and apply again."
                          .format(", ".join(parked)), "error")
            elif action in ("bind", "unbind"):
                try:
                    department_id = int(request.form.get("department_id", "0"))
                except (TypeError, ValueError):
                    department_id = 0
                try:
                    position_level = int(request.form.get("position_level", "-1"))
                except (TypeError, ValueError):
                    position_level = -1
                role_key = (request.form.get("role_key") or "").strip()
                role = db.execute("SELECT name FROM roles WHERE key = ?",
                                  (role_key,)).fetchone()
                if action == "bind":
                    problem = role_delegation_problem(db, actor, role_key)
                    if problem:
                        refuse_delegation(
                            "binding of role '{}'".format(
                                role["name"] if role else role_key),
                            problem, "role", role_key)
                        return redirect(url_for("admin_roles",
                                                _anchor="bindings"))
                    if (actor["role"] != "admin"
                            and not department_manageable(
                                db, actor, department_id)):
                        refuse_delegation(
                            "binding of role '{}'".format(
                                role["name"] if role else role_key),
                            "You can only bind roles to your own department and"
                            " the units below it.", "role", role_key)
                        return redirect(url_for("admin_roles",
                                                _anchor="bindings"))
                    added = bind_role(db, department_id, role_key, position_level)
                    db.commit()
                    if added:
                        log_activity(
                            "UPDATE", "accounts",
                            "Role '{}' bound to {}".format(
                                role["name"] if role else role_key,
                                "the whole company" if not department_id
                                else "department #{}".format(department_id)),
                            record_type="role", record_id=role_key,
                            details={"department_id": department_id,
                                     "position_level": position_level})
                        flash("Role bound. Accounts already placed there carry"
                              " it from their next request.", "success")
                    else:
                        flash("That binding is already stored.", "info")
                else:
                    if (actor["role"] != "admin"
                            and not department_manageable(
                                db, actor, department_id)):
                        refuse_delegation(
                            "unbinding of role '{}'".format(
                                role["name"] if role else role_key),
                            "You can only manage bindings in your own"
                            " department and the units below it.", "role",
                            role_key)
                        return redirect(url_for("admin_roles",
                                                _anchor="bindings"))
                    removed = unbind_role(db, department_id, role_key,
                                          position_level)
                    db.commit()
                    if removed:
                        log_activity(
                            "DELETE", "accounts",
                            "Role '{}' unbound from {}".format(
                                role["name"] if role else role_key,
                                "the whole company" if not department_id
                                else "department #{}".format(department_id)),
                            record_type="role", record_id=role_key,
                            details={"department_id": department_id,
                                     "position_level": position_level})
                        flash("Binding removed.", "success")
                    else:
                        flash("That binding was no longer stored.", "info")
            else:
                flash("Unrecognised request.", "error")
        except ValueError as problem:
            db.rollback()
            flash(str(problem), "error")
        return redirect(url_for("admin_roles", _anchor="bindings"))

    # The binding panel is the Department bindings tab of Role & Permissions.
    return redirect(url_for("admin_roles", _anchor="bindings"))


def _role_department(db, value):
    """A submitted department id, refusing a unit that does not exist."""
    try:
        department_id = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError("Choose a department from the list.")
    if department_id and db.execute(
            "SELECT 1 FROM departments WHERE id = ?",
            (department_id,)).fetchone() is None:
        raise ValueError("That department does not exist.")
    return department_id


@post("/admin/roles/create")
@perm("admin_roles")
def admin_role_create():
    db = get_db()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()[:300]
    try:
        department_id = _role_department(
            db, request.form.get("department_id"))
    except ValueError as problem:
        flash(str(problem), "error")
        return redirect(url_for("admin_roles", _anchor="roles"))
    unit = db.execute("SELECT name FROM departments WHERE id = ?",
                      (department_id,)).fetchone() if department_id else None
    problem = None
    if len(name) < 2:
        problem = "Give the role a name of at least two characters."
    elif db.execute("SELECT 1 FROM roles WHERE lower(name) = lower(?)",
                    (name,)).fetchone():
        problem = "A role called {} already exists.".format(name)
    if problem:
        flash(problem, "error")
        return redirect(url_for("admin_roles", _anchor="roles"))

    actor = current_user()
    if not department_manageable(db, actor, department_id):
        refuse_delegation(
            "creation of role '{}'".format(name[:60]),
            "You can only create roles in your own department and the units"
            " below it.", "role")
        return redirect(url_for("admin_roles", _anchor="roles"))
    proposed = set(request.form.getlist("permission")) & set(PERMISSION_KEYS)
    proposed_rights = desired_hiring_rights(
        proposed,
        set(request.form.getlist("recruitment_keep"))
        & set(RECRUITMENT_PERMISSION_KEYS), set())
    problem = delegation_problem(
        db, actor, proposed,
        department_actions=submitted_department_actions(request.form) or set(),
        letters=set(request.form.getlist("letter_permissions"))
        & set(LETTER_PERMISSIONS),
        rights=proposed_rights)
    if problem:
        refuse_delegation("creation of role '{}'".format(name[:60]), problem,
                          "role")
        return redirect(url_for("admin_roles", _anchor="roles"))

    key = custom_role_key(db, name)
    db.execute("""
        INSERT INTO roles (key, name, description, landing, is_system,
                           is_standard, department_id)
        VALUES (?, ?, ?, 'dashboard', 0, 0, ?)
    """, (key, name[:60], description, department_id))
    selected, modules, rights, department_actions = save_role_permissions(
        db, key, request.form)
    letters = write_role_letters(db, key, request.form)
    db.commit()
    log_activity("CREATE", "accounts",
                 "Custom role '{}' created for {} with {} permission(s) across"
                 " {} module(s)".format(
                     name, unit["name"] if unit else "the whole company",
                     len(selected), len(modules)),
                 record_type="role", record_id=key,
                 details={"modules": modules, "description": description,
                          "department_id": department_id,
                          "permissions": sorted(selected),
                          "hiring_rights": sorted(rights),
                          "department_actions": sorted(department_actions),
                          "letter_permissions": sorted(letters)})
    flash("Role {} created{}. Assign it to an account on its module screen."
          .format(name, " for {}".format(unit["name"]) if unit else ""),
          "success")
    return redirect(url_for("admin_roles", _anchor="roles"))


@post("/admin/roles/<role_key>/update")
@perm("admin_roles")
def admin_role_update(role_key):
    db = get_db()
    role = db.execute("SELECT * FROM roles WHERE key = ?", (role_key,)).fetchone()
    if role is None:
        abort(404)
    is_standard = ("is_standard" in role.keys() and bool(role["is_standard"]))
    department_id = (int(role["department_id"] or 0)
                     if "department_id" in role.keys() else 0)
    if role["is_system"] or is_standard:
        # A built-in or standard role keeps the name and unit the charter gives
        # it, because the console files it there; its matrix stays editable.
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
            return redirect(url_for("admin_roles", _anchor="roles"))
    if role_key == "admin":
        flash("The Administrator role always holds every module.", "error")
        return redirect(url_for("admin_roles"))

    actor = current_user()
    if actor["role"] != "admin" and role_key in set(account_role_keys(db, actor)):
        refuse_delegation(
            "edit of role '{}'".format(role["name"]),
            "You cannot change a role your own account carries.", "role",
            role_key)
        return redirect(url_for("admin_roles", _anchor="roles"))
    if (not role["is_system"] and not is_standard
            and not department_manageable(db, actor, department_id)):
        refuse_delegation(
            "edit of role '{}'".format(role["name"]),
            "You can only manage roles in your own department and the units"
            " below it.", "role", role_key)
        return redirect(url_for("admin_roles", _anchor="roles"))
    proposed = set(request.form.getlist("permission")) & set(PERMISSION_KEYS)
    previous_modules = [row["module"] for row in db.execute(
        "SELECT module FROM role_permissions WHERE role_key = ?", (role_key,))]
    proposed_rights = desired_hiring_rights(
        proposed,
        set(request.form.getlist("recruitment_keep"))
        & set(RECRUITMENT_PERMISSION_KEYS),
        role_hiring_rights(db, role_key, previous_modules))
    problem = delegation_problem(
        db, actor, proposed,
        department_actions=submitted_department_actions(request.form) or set(),
        letters=set(request.form.getlist("letter_permissions"))
        & set(LETTER_PERMISSIONS),
        rights=proposed_rights)
    if problem:
        refuse_delegation("edit of role '{}'".format(role["name"]), problem,
                          "role", role_key)
        return redirect(url_for("admin_roles", _anchor="roles"))

    if role["is_system"] or is_standard:
        db.execute("UPDATE roles SET name = ?, description = ?,"
                   " updated_at = CURRENT_TIMESTAMP WHERE key = ?",
                   (name, description, role_key))
    else:
        try:
            department_id = _role_department(
                db, request.form.get("department_id", role["department_id"]))
        except ValueError as problem:
            flash(str(problem), "error")
            return redirect(url_for("admin_roles", _anchor="roles"))
        db.execute("UPDATE roles SET name = ?, description = ?,"
                   " department_id = ?, updated_at = CURRENT_TIMESTAMP"
                   " WHERE key = ?",
                   (name, description, department_id, role_key))
    selected, modules, rights, department_actions = save_role_permissions(
        db, role_key, request.form)
    letters = write_role_letters(db, role_key, request.form)
    holders = {row["id"] for row in db.execute(
        "SELECT id FROM users WHERE role = ? AND status = 'Active'", (role_key,))}
    holders |= {row["user_id"] for row in db.execute(
        "SELECT user_id FROM user_roles WHERE role_key = ?", (role_key,))}
    extra = ""
    if rights:
        extra = " It also carries {} hiring right(s).".format(len(rights))
    if letters:
        extra += " It carries {} letter action(s).".format(len(letters))
    if department_actions:
        extra += " It carries {} department action(s).".format(
            len(department_actions))
    if not modules and not rights:
        extra = " It grants no desk rights now."
    for user_id in holders:
        deliver_notification(
            db, user_id, "Role access updated",
            "The {} role now carries: {}. Your modules follow that list.".format(
                name, ", ".join(MODULE_LABEL.get(m, m) for m in modules) or "no modules")
            + extra,
            url_for("dashboard"), category="account", icon="shield", tone="info")
    db.commit()
    log_activity("UPDATE", "accounts",
                 "Role '{}' permissions set to {}".format(
                     name, ", ".join(modules) or "none"),
                 record_type="role", record_id=role_key,
                 details={"modules": modules, "permissions": sorted(selected),
                          "holders": len(holders),
                          "department_id": department_id,
                          "hiring_rights": sorted(rights),
                          "department_actions": sorted(department_actions),
                          "letter_permissions": sorted(letters)})
    flash("Role {} saved.".format(name), "success")
    return redirect(url_for("admin_roles", _anchor="roles"))


@post("/admin/roles/<role_key>/delete")
@perm("admin_roles")
def admin_role_delete(role_key):
    db = get_db()
    role = db.execute("SELECT * FROM roles WHERE key = ?", (role_key,)).fetchone()
    if role is None:
        abort(404)
    if role["is_system"]:
        flash("Built-in roles cannot be deleted.", "error")
        return redirect(url_for("admin_roles", _anchor="roles"))
    if "is_standard" in role.keys() and role["is_standard"]:
        flash("Standard roles cannot be deleted: the charter recreates them at"
              " the next start. Deactivate {} instead to stop it granting"
              " access.".format(role["name"]), "error")
        return redirect(url_for("admin_roles", _anchor="roles"))
    actor = current_user()
    if actor["role"] != "admin":
        if role_key in set(account_role_keys(db, actor)):
            refuse_delegation(
                "deletion of role '{}'".format(role["name"]),
                "You cannot delete a role your own account carries.", "role",
                role_key)
            return redirect(url_for("admin_roles", _anchor="roles"))
        if not department_manageable(db, actor, role["department_id"]):
            refuse_delegation(
                "deletion of role '{}'".format(role["name"]),
                "You can only manage roles in your own department and the units"
                " below it.", "role", role_key)
            return redirect(url_for("admin_roles", _anchor="roles"))
        problem = role_delegation_problem(db, actor, role_key)
        if problem:
            refuse_delegation(
                "deletion of role '{}'".format(role["name"]), problem, "role",
                role_key)
            return redirect(url_for("admin_roles", _anchor="roles"))
    primary = db.execute("SELECT COUNT(*) AS n FROM users WHERE role = ?",
                         (role_key,)).fetchone()["n"]
    extra = db.execute("SELECT COUNT(*) AS n FROM user_roles WHERE role_key = ?",
                       (role_key,)).fetchone()["n"]
    # A department binding is a holder too: deleting the role would quietly take
    # the desk away from everyone in that unit.
    bound = db.execute("SELECT COUNT(*) AS n FROM department_roles"
                       " WHERE role_key = ?", (role_key,)).fetchone()["n"]
    if primary or extra or bound:
        flash("Role {} still has {} account(s) and {} department"
              " binding(s). Remove it from them first.".format(
                  role["name"], primary + extra, bound), "error")
        return redirect(url_for("admin_roles"))
    db.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
    db.execute("DELETE FROM role_permission_grants WHERE role_key = ?", (role_key,))
    db.execute("DELETE FROM hiring_role_permissions WHERE role_key = ?", (role_key,))
    db.execute("DELETE FROM letter.letter_role_permissions WHERE role_key = ?",
               (role_key,))
    db.execute("DELETE FROM roles WHERE key = ?", (role_key,))
    db.commit()
    log_activity("DELETE", "accounts",
                 "Custom role '{}' deleted".format(role["name"]),
                 record_type="role", record_id=role_key)
    flash("Role {} deleted.".format(role["name"]), "success")
    return redirect(url_for("admin_roles"))


@post("/admin/roles/<role_key>/activation")
@perm("admin_roles")
def admin_role_activation(role_key):
    """Park or restore a custom role without deleting its matrix or bindings."""
    db = get_db()
    role = db.execute("SELECT * FROM roles WHERE key = ?", (role_key,)).fetchone()
    if role is None:
        abort(404)
    if role["is_system"]:
        flash("Built-in roles stay active because accounts land on them. Park a"
              " custom role instead.", "error")
        return redirect(url_for("admin_roles", _anchor="roles"))
    actor = current_user()
    if actor["role"] != "admin":
        standard = ("is_standard" in role.keys() and bool(role["is_standard"]))
        if not standard and not department_manageable(
                db, actor, role["department_id"]):
            refuse_delegation(
                "activation change for role '{}'".format(role["name"]),
                "You can only manage roles in your own department and the units"
                " below it.", "role", role_key)
            return redirect(url_for("admin_roles", _anchor="roles"))
        if request.form.get("active") == "1" and not role["active"]:
            problem = role_delegation_problem(db, actor, role_key)
            if problem:
                refuse_delegation(
                    "activation of role '{}'".format(role["name"]),
                    problem, "role", role_key)
                return redirect(url_for("admin_roles", _anchor="roles"))
    active = request.form.get("active") == "1"
    if bool(role["active"]) == active:
        flash("Role {} is already {}.".format(
            role["name"], "active" if active else "inactive"), "info")
        return redirect(url_for("admin_roles", _anchor="roles"))
    db.execute("UPDATE roles SET active = ?, updated_at = CURRENT_TIMESTAMP"
               " WHERE key = ?", (1 if active else 0, role_key))
    holders = {row["id"] for row in db.execute(
        "SELECT id FROM users WHERE role = ? AND status = 'Active'", (role_key,))}
    holders |= {row["user_id"] for row in db.execute(
        "SELECT user_id FROM user_roles WHERE role_key = ?", (role_key,))}
    for user_id in holders:
        deliver_notification(
            db, user_id, "Role access {}".format("restored" if active else "paused"),
            "The {} role has been {}. {}".format(
                role["name"], "activated" if active else "deactivated",
                "Its permissions apply from your next request." if active else
                "Its permissions and department bindings are ignored until an"
                " administrator activates it again."),
            url_for("dashboard"), category="account", icon="shield", tone="info")
    db.commit()
    log_activity("UPDATE", "accounts",
                 "Role '{}' {}".format(role["name"],
                                       "activated" if active else "deactivated"),
                 record_type="role", record_id=role_key,
                 details={"active": active})
    flash("Role {} {}. Its configured permissions and department bindings are"
          " kept; {}".format(
              role["name"], "activated" if active else "deactivated",
              "they apply from the next request." if active else
              "they are ignored until it is activated again."),
          "success")
    return redirect(url_for("admin_roles", _anchor="roles"))


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
                    "SELECT * FROM users WHERE status = 'Active'").fetchall():
                held = set(account_role_keys(db, account))
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
@perm("admin_access")
def admin_role_account(user_id):
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        abort(404)

    choices = role_choices(db)
    role_names = dict(choices)
    catalogue_names = {row["key"]: row["name"] for row in role_catalogue(db)}

    if request.method == "POST":
        actor = current_user()
        if actor["role"] != "admin" and user_id == actor["id"]:
            refuse_delegation(
                "change to own account access",
                "You cannot change your own permissions or roles. Ask another"
                " administrator to do it.", "user", user_id)
            abort(403)
        account_problem = account_guard_problem(db, actor, target)
        if account_problem:
            refuse_delegation(
                "change to account access for {}".format(target["full_name"]),
                account_problem, "user", user_id)
            return redirect(url_for("admin_role_account", user_id=user_id))

        action = (request.form.get("action") or "").strip()

        if action == "extra_roles":
            if target["role"] == "admin":
                flash("Administrators already hold every module.", "error")
                return redirect(url_for("admin_role_account", user_id=user_id))
            assignable = {key for key, _ in choices}.difference(
                {"admin", "member", target["role"]})
            current = set(user_extra_roles(db, user_id))
            # A parked role stays on the account and grants nothing, so it can
            # be untied here even though it is no longer offered for assignment.
            parked = {key for key in current if not role_is_active(db, key)}
            selected = set(request.form.getlist("extra_roles")) & (
                assignable | parked)
            if selected == current:
                flash("No additional role changed.", "info")
                return redirect(url_for("admin_role_account", user_id=user_id))
            for key in sorted(selected - current):
                problem = role_delegation_problem(db, actor, key)
                if problem:
                    refuse_delegation(
                        "assignment of role '{}' to {}".format(
                            catalogue_names.get(key, key), target["full_name"]),
                        problem, "user", user_id)
                    return redirect(url_for("admin_role_account",
                                            user_id=user_id))
            db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            for key in sorted(selected):
                db.execute("INSERT INTO user_roles (user_id, role_key) VALUES (?, ?)",
                           (user_id, key))
            deliver_notification(
                db, user_id, "Additional roles updated",
                "Your account now also carries: {}. Those roles widen the modules"
                " you can open; your home dashboard stays with your primary role."
                .format(", ".join(catalogue_names.get(key, key)
                                 for key in sorted(selected))
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
        selected = set(request.form.getlist("permission")) & set(PERMISSION_KEYS)
        keep = set(request.form.getlist("recruitment_keep")) \
            & set(RECRUITMENT_PERMISSION_KEYS)
        # Approvals come with a signing position and cannot be switched off here.
        approver = approver_desk_label(target, db)
        if approver:
            selected.add("module_approvals")

        modules = {module for module in MODULES
                   if module in permission_modules(
                       selected & set(MODULE_PERMISSION_KEYS))}
        defaults = set(role_permission_set(target["role"], db))
        for key in account_role_keys(db, target)[1:]:
            defaults |= role_permission_set(key, db)

        # Fine-grained deviations are stored against the combined role matrices,
        # so a role change later carries this account with it.
        baseline = set()
        for key in account_role_keys(db, target):
            baseline |= role_granted_permissions(db, key)
        department_actions = submitted_department_actions(request.form)
        current_rights = set(hiring_rights(db, target))
        desired = desired_hiring_rights(selected, keep, current_rights)
        granted_now = effective_granted_permissions(target)
        problem = delegation_problem(
            db, actor, set(selected) - granted_now,
            department_actions=(department_actions or set()) - granted_now,
            rights=set(desired) - current_rights)
        if problem:
            refuse_delegation(
                "permission change for {}".format(target["full_name"]),
                problem, "user", user_id)
            return redirect(url_for("admin_role_account", user_id=user_id))

        db.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
        for module in MODULES:
            if (module in modules) != (module in defaults):
                db.execute("""
                    INSERT INTO user_permissions (user_id, module, allowed, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, module, int(module in modules)))

        stored_department = {row["permission"]: row["allowed"] for row in db.execute(
            "SELECT permission, allowed FROM user_permission_grants"
            " WHERE user_id = ? AND permission GLOB 'dept_*'",
            (user_id,)).fetchall()}
        db.execute("DELETE FROM user_permission_grants WHERE user_id = ?", (user_id,))
        for key in MODULE_PERMISSION_KEYS:
            if (key in selected) != (key in baseline):
                db.execute("""
                    INSERT INTO user_permission_grants
                        (user_id, permission, allowed, granted_by, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, key, int(key in selected), current_user()["id"]))
        if department_actions is None:
            # No grid on the form: rewrite exactly what was stored, denials too.
            for key, allowed in stored_department.items():
                db.execute("""
                    INSERT INTO user_permission_grants
                        (user_id, permission, allowed, granted_by, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, key, allowed, current_user()["id"]))
        else:
            for key in DEPARTMENT_PERMISSION_KEYS:
                if (key in department_actions) != (key in baseline):
                    db.execute("""
                        INSERT INTO user_permission_grants
                            (user_id, permission, allowed, granted_by, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (user_id, key, int(key in department_actions),
                          current_user()["id"]))

        # Recruitment rows are the hiring rights behind them; the account's own
        # rows are deviations from what its roles, modules and seat already give.
        base_rights = set(hiring_baseline(db, target))
        db.execute("DELETE FROM hiring_user_permissions WHERE user_id = ?", (user_id,))
        for right in HIRING_RIGHTS:
            if (right in desired) != (right in base_rights):
                db.execute("""
                    INSERT INTO hiring_user_permissions
                        (user_id, right, allowed, granted_by, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, right, int(right in desired), current_user()["id"]))

        deliver_notification(
            db, user_id, "Permissions updated",
            "An administrator set your permissions to {}. Your dashboard, menus"
            " and notifications follow that list.".format(
                "{} permission(s) across {}".format(
                    len(selected),
                    ", ".join(MODULE_LABEL.get(m, m) for m in sorted(modules))
                    or "no module")),
            url_for("dashboard"), category="account",
            icon="shield", tone="info")
        db.commit()
        details = {"permissions": sorted(selected),
                   "modules": sorted(modules),
                   "hiring_rights": sorted(desired)}
        if department_actions is not None:
            details["department_actions"] = sorted(department_actions)
        log_activity("UPDATE", "accounts",
                     "Permissions set for {} ({} permissions, modules: {})".format(
                         target["full_name"], len(selected),
                         ", ".join(sorted(modules)) or "none"),
                     record_type="user", record_id=user_id, details=details)
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
    role_options = []
    for key, label in choices:
        modules = role_permission_set(key, db)
        if key not in ("admin", "member", target["role"]):
            role_options.append({
                "key": key,
                "label": label,
                "active": True,
                "modules": [MODULE_LABEL.get(module, module)
                            for module in sorted(modules)],
            })
    # A parked role keeps its assignment so reactivating it restores the
    # account's reach; it is listed only so it can be untied, never assigned.
    for key in sorted(extra_keys - {option["key"] for option in role_options}):
        if key in ("admin", "member", target["role"]):
            continue
        role_options.append({
            "key": key,
            "label": catalogue_names.get(key, key),
            "active": False,
            "modules": [],
        })

    approver_desk = (None if target["role"] == "admin"
                     else approver_desk_label(target, db))
    granted_keys = effective_granted_permissions(target)
    baseline_keys = set()
    for key in account_role_keys(db, target):
        baseline_keys |= role_granted_permissions(db, key)
    catalog = permission_catalog(
        granted_keys, hiring_rights(db, target),
        forced=("module_approvals",) if approver_desk else ())
    open_permissions = sum(entry["open"] for entry in catalog)
    total_permissions = sum(entry["total"] for entry in catalog)
    deviations = len(
        (granted_keys & set(MODULE_PERMISSION_KEYS))
        ^ (baseline_keys & set(MODULE_PERMISSION_KEYS)))
    deviations += len((granted_keys & set(DEPARTMENT_PERMISSION_KEYS))
                      ^ (baseline_keys & set(DEPARTMENT_PERMISSION_KEYS)))
    deviations += len(set(hiring_rights(db, target))
                      ^ set(hiring_baseline(db, target)))
    department_selected = (set(DEPARTMENT_PERMISSION_KEYS)
                           if target["role"] == "admin" else
                           granted_keys & set(DEPARTMENT_PERMISSION_KEYS))

    return render_template("Admin/role_account.html", target=target,
                           catalog=catalog,
                           dept_matrix=department_matrix(
                               department_selected,
                               readonly=(target["role"] == "admin")),
                           dept_open=len(department_selected),
                           dept_total=len(DEPARTMENT_PERMISSION_KEYS),
                           open_permissions=open_permissions,
                           total_permissions=total_permissions,
                           deviations=deviations,
                           extra_keys=extra_keys,
                           role_options=role_options,
                           modules=MODULES, module_label=MODULE_LABEL,
                           role_labels=role_names, assignment=assignment,
                           position_level_label=POSITION_LEVEL_LABEL,
                           approver_desk=approver_desk)


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
@perm("admin_config")
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
