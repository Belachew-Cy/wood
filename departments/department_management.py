"""Department Management under Admin Management.

One console over the units the company runs: their managers, positions and
the tasks a desk registers. Accounts and the roles and permissions that
reach them live on Role & Permissions, which reads and writes the same
tables this page does, so a unit registered here or a position retired is
one record every other screen follows. Every mutation is validated,
committed once and written to the audit trail with the unit it touched.
"""

from core.access import current_user
from core.access import perm
from core.db import get_db
from core.routing import post
from core.routing import route
from departments.admin import department_manageable
from departments.admin import refuse_delegation
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import log_activity
from services.department_admin import assign_manager
from services.department_admin import bind_department_role
from services.department_admin import create_department
from services.department_admin import create_position
from services.department_admin import delete_department
from services.department_admin import delete_position
from services.department_admin import department_management_snapshot
from services.department_admin import department_row
from services.department_admin import set_department_status
from services.department_admin import set_position_status
from services.department_admin import update_department
from services.department_admin import update_position
import sqlite3


ANCHORS = ("overview", "tasks", "positions")


def _anchor(value):
    value = (value or "").strip()
    return value if value in ANCHORS else ""


def _redirect(department_id, anchor=""):
    kwargs = {"department": department_id} if department_id else {}
    if anchor:
        kwargs["_anchor"] = anchor
    return redirect(url_for("admin_departments", **kwargs))


@route("/admin/departments")
@perm("admin_departments")
def admin_departments():
    db = get_db()
    data = department_management_snapshot(
        db, request.args.get("department", type=int))
    return render_template("Admin/departments.html", **data)


@post("/admin/departments/action")
@perm("admin_departments")
def admin_departments_action():
    db = get_db()
    action = (request.form.get("action") or "").strip()
    department_id = request.form.get("department_id", type=int)
    anchor = _anchor(request.form.get("anchor"))
    actor = current_user()
    if actor["role"] != "admin":
        if action == "create_department":
            parent_id = request.form.get("parent_id", type=int) or 0
            if not department_manageable(db, actor, parent_id):
                refuse_delegation(
                    "creation of a department",
                    "You can only create units under your own department.",
                    "department")
                return _redirect(None)
        elif department_id and not department_manageable(
                db, actor, department_id):
            refuse_delegation(
                "change to department #{}".format(department_id),
                "You can only manage your own department and the units below"
                " it.", "department", department_id)
            return _redirect(department_id)
    log_action = "UPDATE"
    record_type = "department"
    record_key = department_id
    status = "success"
    try:
        if not department_id and action != "create_department":
            raise ValueError("Choose a department first.")

        if action == "create_department":
            result = create_department(db, request.form)
            department_id = result["department_id"]
            record_key = department_id
            log_action = "CREATE"
            anchor = "overview"
        elif action == "update_department":
            result = update_department(db, department_id, request.form)
        elif action == "delete_department":
            result = delete_department(
                db, department_id, request.form.get("confirm_name"))
            log_action = "DELETE"
            anchor = ""
        elif action == "set_department_status":
            result = set_department_status(
                db, department_id, request.form.get("active") == "1")
            status = "success" if result.get("changed", True) else "info"
        elif action == "assign_manager":
            result = assign_manager(
                db, department_id, request.form.get("head_employee_id"))
        elif action == "create_position":
            result = create_position(db, department_id, request.form)
            record_type = "position"
            record_key = result["position_id"]
            log_action = "CREATE"
            anchor = "positions"
            # The Add position form can grant a role at the new seat's rung in
            # the same breath, so a position is never opened without its access.
            bind_key = (request.form.get("bind_role_key") or "").strip()
            if bind_key:
                bind_result = bind_department_role(db, department_id, {
                    "role_key": bind_key,
                    "position_level": request.form.get("level", "-1"),
                })
                result["notice"] = "{} {}".format(
                    result["notice"], bind_result["notice"])
        elif action == "update_position":
            result = update_position(db, department_id, request.form)
            record_type = "position"
            record_key = request.form.get("position_id")
            anchor = "positions"
        elif action == "set_position_status":
            result = set_position_status(db, department_id, request.form)
            record_type = "position"
            record_key = request.form.get("position_id")
            anchor = "positions"
        elif action == "delete_position":
            result = delete_position(
                db, department_id, request.form.get("position_id", type=int))
            record_type = "position"
            record_key = request.form.get("position_id")
            log_action = "DELETE"
            anchor = "positions"
        else:
            raise ValueError("Unrecognised department request.")

        db.commit()
        log_activity(log_action, "accounts", result["notice"],
                     record_type=record_type, record_id=record_key,
                     details=result.get("details") or {"action": action})
        flash(result["notice"], status)
    except ValueError as problem:
        db.rollback()
        flash(str(problem), "error")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("That change conflicts with an existing department, position or"
              " role. Review the values and try again.", "error")
    return _redirect(department_id, anchor)
