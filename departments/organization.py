"""Organization page, departments and hierarchy."""

from core.access import current_user
from core.access import effective_permissions
from core.access import login_required
from core.access import perm
from core.db import get_db
from core.routing import route
from flask import abort
from flask import flash
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import log_activity
from services.hr import hr_department_options
from services.notifications import deliver_notification
from services.org import POSITION_LEVEL_LABEL
from services.org import clean_department_position
from services.org import hierarchy_columns
from services.org import hierarchy_db
from services.org import org_leadership_level
from services.org import organization_snapshot
import sqlite3


@route("/api/organizational-hierarchy")
def organizational_hierarchy_api():
    """Read-only endpoint used by the home page chart."""

    with hierarchy_db() as conn:
        employee_columns = hierarchy_columns(conn, "employees")

        required_columns = {
            "id", "full_name", "department", "job_title",
            "manager_id", "profile_photo", "employment_status"
        }

        if not required_columns.issubset(employee_columns):
            return jsonify({
                "employees": [],
                "message": "Employee database schema needs review."
            }), 500

        rows = conn.execute("""
            SELECT
                id,
                full_name,
                department,
                job_title,
                manager_id,
                profile_photo,
                employment_status
            FROM employees
            WHERE COALESCE(employment_status, 'Active') = 'Active'
            ORDER BY department, full_name
        """).fetchall()

    employees = [dict(row) for row in rows]

    return jsonify({
        "employees": employees,
        "count": len(employees)
    })


@route("/organization")
def organization():
    """The company chart. Staff see their own row highlighted; visitors get the
    same tree with sign-in roles left out. Admin and HR views inherit the public
    page so the structure, copy and chart stay identical across modules."""
    db = get_db()
    user = current_user()
    org = organization_snapshot(db, user, public=user is None)
    template = "organization.html"
    if user is not None:
        granted = effective_permissions(user)
        if "accounts" in granted:
            template = "Admin/organization.html"
        elif "hr" in granted:
            template = "HR/organization.html"
    return render_template(
        template,
        can_create_department=org_leadership_level(user, db) is not None,
        **org)


@route("/organization/departments", methods=["POST"])
@login_required()
def create_department():
    """Executives, directors and managers can open a new department from the
    organization chart; HR keeps the full editor on the hierarchy screen."""
    user = current_user()
    db = get_db()
    if org_leadership_level(user, db) is None:
        abort(403)
    name = request.form.get("department_name", "").strip()
    description = request.form.get("description", "").strip()
    position = clean_department_position(
        request.form.get("leadership_position", ""))
    if not name:
        flash("Department name is required.", "error")
        return redirect(url_for("organization") + "#departments")
    if not position:
        flash("Choose Executive, Director or Manager for the department's "
              "leadership position.", "error")
        return redirect(url_for("organization") + "#departments")
    with hierarchy_db() as conn:
        try:
            conn.execute("""
                INSERT INTO departments (name, description, leadership_position)
                VALUES (?, ?, ?)
            """, (name, description, position))
        except sqlite3.IntegrityError:
            flash("A department with that name already exists.", "error")
        else:
            log_activity("CREATE", "hr",
                         "Department '{}' created".format(name),
                         record_type="department",
                         details={"name": name, "leadership_position": position})
            flash("Department added successfully.", "success")
    return redirect(url_for("organization") + "#departments")


@route("/manage-hierarchy", methods=["GET", "POST"])
@perm("hr")
def manage_hierarchy():
    user = current_user()
    is_admin = "accounts" in effective_permissions(user)
    with hierarchy_db() as conn:

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            audit_note = None

            try:
                if action in ("set_department_status", "delete_department") and not is_admin:
                    raise ValueError(
                        "Only an administrator can change status or delete a department."
                    )

                if action == "add_department":
                    name = request.form.get(
                        "department_name", ""
                    ).strip()

                    description = request.form.get(
                        "description", ""
                    ).strip()

                    position = clean_department_position(
                        request.form.get("leadership_position", ""))

                    if not name:
                        flash("Department name is required.", "error")
                    elif not position:
                        flash(
                            "Choose Executive, Director or Manager for the "
                            "department's leadership position.",
                            "error"
                        )
                    else:
                        conn.execute("""
                            INSERT INTO departments
                                (name, description, leadership_position)
                            VALUES (?, ?, ?)
                        """, (name, description, position))

                        flash("Department added successfully.", "success")
                        audit_note = "Department '{}' added ({})".format(
                            name, position)

                elif action == "edit_department":
                    department_id = int(
                        request.form.get("department_id", "0")
                    )
                    name = request.form.get(
                        "department_name", ""
                    ).strip()
                    description = request.form.get(
                        "description", ""
                    ).strip()
                    position = clean_department_position(
                        request.form.get("leadership_position", ""))

                    current = conn.execute("""
                        SELECT name FROM departments WHERE id = ?
                    """, (department_id,)).fetchone()

                    if not name:
                        flash("Department name is required.", "error")
                    elif not position:
                        flash(
                            "Choose Executive, Director or Manager for the "
                            "department's leadership position.",
                            "error"
                        )
                    elif current is None:
                        flash("Department not found.", "error")
                    else:
                        conn.execute("""
                            UPDATE departments
                            SET name = ?, description = ?, leadership_position = ?
                            WHERE id = ?
                        """, (name, description, position, department_id))

                        # Employees store the department name, not its id.
                        if current["name"] != name:
                            conn.execute("""
                                UPDATE employees
                                SET department = ?
                                WHERE department = ?
                            """, (name, current["name"]))
                            # Budget rows keep the name beside the User
                            # Management id, so a rename has to reach both.
                            for table in ("budget_allocations",
                                          "budget_requests"):
                                conn.execute("""
                                    UPDATE {0}
                                    SET department = ?, department_id = ?
                                    WHERE department_id = ?
                                       OR department = ? COLLATE NOCASE
                                """.format(table),
                                    (name, department_id, department_id,
                                     current["name"]))

                        flash("Department updated.", "success")
                        audit_note = "Department '{}' updated".format(name)
                        audit_note = "Department '{}' updated".format(name)

                elif action == "set_department_status":
                    department_id = int(
                        request.form.get("department_id", "0")
                    )
                    active = 0 if request.form.get("active") == "0" else 1

                    department = conn.execute("""
                        SELECT name FROM departments WHERE id = ?
                    """, (department_id,)).fetchone()

                    if department is None:
                        flash("Department not found.", "error")
                    else:
                        conn.execute("""
                            UPDATE departments SET is_active = ? WHERE id = ?
                        """, (active, department_id))
                        flash(
                            "Department activated." if active
                            else "Department deactivated.",
                            "success"
                        )
                        audit_note = "Department '{}' {}".format(
                            department["name"],
                            "activated" if active else "deactivated")

                elif action == "add_position":
                    title = request.form.get(
                        "position_title", ""
                    ).strip()

                    department_id = request.form.get(
                        "position_department_id"
                    ) or None

                    level = request.form.get("level", "4")

                    if not title:
                        flash("Position title is required.", "error")
                    else:
                        conn.execute("""
                            INSERT INTO organizational_positions
                                (title, department_id, level)
                            VALUES (?, ?, ?)
                        """, (title, department_id, int(level)))

                        flash("Position added successfully.", "success")
                        audit_note = "Position '{}' added".format(title)

                elif action == "assign_employee":
                    employee_id = int(
                        request.form.get("employee_id", "0")
                    )
                    department_id = request.form.get(
                        "department_id"
                    ) or None
                    position_id = request.form.get(
                        "position_id"
                    ) or None
                    manager_id = request.form.get(
                        "manager_id"
                    ) or None

                    employee = conn.execute("""
                        SELECT id, user_id FROM employees WHERE id = ?
                    """, (employee_id,)).fetchone()

                    if not employee:
                        flash("Employee not found.", "error")
                    elif manager_id and int(manager_id) == employee_id:
                        flash(
                            "An employee cannot report to themselves.",
                            "error"
                        )
                    else:
                        # Validate manager and position references.
                        if manager_id:
                            manager = conn.execute("""
                                SELECT id FROM employees WHERE id = ?
                            """, (int(manager_id),)).fetchone()

                            if not manager:
                                raise ValueError(
                                    "Selected supervisor does not exist."
                                )

                        position = None
                        if position_id:
                            position = conn.execute("""
                                SELECT p.title, d.name AS department_name
                                FROM organizational_positions p
                                LEFT JOIN departments d
                                    ON d.id = p.department_id
                                WHERE p.id = ?
                            """, (int(position_id),)).fetchone()

                            if not position:
                                raise ValueError(
                                    "Selected position does not exist."
                                )

                        department = None
                        if department_id:
                            department = conn.execute("""
                                SELECT name FROM departments WHERE id = ?
                            """, (int(department_id),)).fetchone()

                            if not department:
                                raise ValueError(
                                    "Selected department does not exist."
                                )

                        # Prevent reporting cycles such as A -> B -> A.
                        current_manager = (
                            int(manager_id) if manager_id else None
                        )
                        seen = {employee_id}

                        while current_manager is not None:
                            if current_manager in seen:
                                raise ValueError(
                                    "This assignment would create a "
                                    "reporting cycle."
                                )

                            seen.add(current_manager)

                            row = conn.execute("""
                                SELECT manager_id FROM employees
                                WHERE id = ?
                            """, (current_manager,)).fetchone()

                            if not row or row["manager_id"] is None:
                                break

                            current_manager = int(row["manager_id"])

                        updates = {
                            "department": (
                                department["name"] if department else ""
                            ),
                            "job_title": (
                                position["title"] if position else ""
                            ),
                            "position_id": (
                                int(position_id) if position_id else None
                            ),
                            "manager_id": (
                                int(manager_id) if manager_id else None
                            )
                        }

                        conn.execute("""
                            UPDATE employees
                            SET department = ?,
                                job_title = ?,
                                position_id = ?,
                                manager_id = ?
                            WHERE id = ?
                        """, (
                            updates["department"],
                            updates["job_title"],
                            updates["position_id"],
                            updates["manager_id"],
                            employee_id
                        ))

                        # An account linked to this employee follows the same
                        # department and position, so approvals and the chart
                        # never disagree with the HR record.
                        linked_users = [row["id"] for row in conn.execute(
                            "SELECT id FROM users WHERE employee_id = ?",
                            (employee_id,)).fetchall()]
                        if employee["user_id"]:
                            linked_users.append(employee["user_id"])
                        for linked_id in dict.fromkeys(linked_users):
                            conn.execute("""
                                UPDATE users SET department_id = ?, position_id = ?
                                WHERE id = ?
                            """, (
                                department_id or None,
                                updates["position_id"],
                                linked_id,
                            ))

                        flash(
                            "Employee hierarchy updated successfully.",
                            "success"
                        )
                        audit_note = "Hierarchy updated for employee #{}".format(
                            employee_id)

                elif action == "assign_account":
                    account_id = int(request.form.get("user_id", "0"))
                    department_id = request.form.get("department_id") or None
                    position_id = request.form.get("position_id") or None

                    account = conn.execute("""
                        SELECT id, full_name, employee_id FROM users WHERE id = ?
                    """, (account_id,)).fetchone()
                    if account is None:
                        flash("Account not found.", "error")
                    else:
                        department = None
                        if department_id:
                            department = conn.execute("""
                                SELECT id, name FROM departments WHERE id = ?
                            """, (int(department_id),)).fetchone()
                            if department is None:
                                raise ValueError(
                                    "Selected department does not exist.")

                        position = None
                        if position_id:
                            position = conn.execute("""
                                SELECT id, title, department_id
                                FROM organizational_positions WHERE id = ?
                            """, (int(position_id),)).fetchone()
                            if position is None:
                                raise ValueError(
                                    "Selected position does not exist.")
                            # A position belongs to one department; assigning
                            # it also moves the account into that team.
                            if position["department_id"]:
                                department_id = position["department_id"]
                                department = conn.execute("""
                                    SELECT id, name FROM departments WHERE id = ?
                                """, (department_id,)).fetchone()

                        conn.execute("""
                            UPDATE users SET department_id = ?, position_id = ?
                            WHERE id = ?
                        """, (
                            int(department_id) if department_id else None,
                            int(position_id) if position_id else None,
                            account_id,
                        ))

                        if account["employee_id"]:
                            conn.execute("""
                                UPDATE employees
                                SET department = ?,
                                    position_id = ?,
                                    job_title = COALESCE(NULLIF(?, ''), job_title)
                                WHERE id = ?
                            """, (
                                department["name"] if department else "",
                                int(position_id) if position_id else None,
                                position["title"] if position else "",
                                account["employee_id"],
                            ))

                        assignment = "{}{}".format(
                            department["name"] if department else "No department",
                            " · {}".format(position["title"]) if position else "",
                        )
                        deliver_notification(
                            conn, account_id, "Department assignment updated",
                            "You are now placed as {} in the organization"
                            " structure.".format(assignment),
                            url_for("organization"), category="account",
                            icon="users", tone="info")
                        flash("Assignment saved for {}.".format(
                            account["full_name"]), "success")
                        audit_note = "Department assignment updated for {}".format(
                            account["full_name"])

                elif action == "delete_department":
                    department_id = int(
                        request.form.get("department_id", "0")
                    )

                    department = conn.execute("""
                        SELECT name FROM departments WHERE id = ?
                    """, (department_id,)).fetchone()

                    if department:
                        # Keep employee records. Clear matching department
                        # labels instead of deleting employees.
                        conn.execute("""
                            UPDATE employees
                            SET department = ''
                            WHERE department = ?
                        """, (department["name"],))

                        conn.execute("""
                            UPDATE organizational_positions
                            SET department_id = NULL
                            WHERE department_id = ?
                        """, (department_id,))

                        conn.execute("""
                            UPDATE users SET department_id = NULL
                            WHERE department_id = ?
                        """, (department_id,))

                        # Keep the budget history under its recorded name; the
                        # User Management link is what has to be released.
                        for table in ("budget_allocations", "budget_requests"):
                            conn.execute("""
                                UPDATE {0} SET department_id = NULL
                                WHERE department_id = ?
                            """.format(table), (department_id,))

                        conn.execute("""
                            DELETE FROM departments WHERE id = ?
                        """, (department_id,))

                        flash("Department removed.", "success")
                        audit_note = "Department '{}' removed".format(
                            department["name"])

                else:
                    flash("Unknown management action.", "error")

            except (ValueError, TypeError) as exc:
                flash(str(exc), "error")

            except sqlite3.IntegrityError:
                flash(
                    "That department or position already exists.",
                    "error"
                )

            if audit_note:
                conn.commit()
                log_activity(
                    {"add_department": "CREATE", "add_position": "CREATE",
                     "delete_department": "DELETE"}.get(action, "UPDATE"),
                    "hr", audit_note,
                    record_type="org_structure",
                    details={"action": action})

            return redirect(url_for("manage_hierarchy"))

        # Active departments first, as before, but the rows come from the
        # same HR list every other screen reads.
        departments = sorted(
            hr_department_options(conn),
            key=lambda row: (not row["is_active"],
                             (row["name"] or "").casefold()),
        )

        edit_department = None
        edit_department_id = request.args.get("edit_department", type=int)
        if edit_department_id:
            edit_department = conn.execute("""
                SELECT * FROM departments WHERE id = ?
            """, (edit_department_id,)).fetchone()

        positions = conn.execute("""
            SELECT p.*, d.name AS department_name
            FROM organizational_positions p
            LEFT JOIN departments d ON d.id = p.department_id
            ORDER BY p.level, p.title
        """).fetchall()

        employees = conn.execute("""
            SELECT e.id, e.full_name, e.department, e.job_title,
                   e.manager_id, e.position_id, d.id AS department_id,
                   (SELECT username FROM users
                     WHERE employee_id = e.id ORDER BY id LIMIT 1) AS username
            FROM employees e
            LEFT JOIN departments d ON d.name = e.department
            WHERE COALESCE(e.employment_status, 'Active') = 'Active'
            ORDER BY e.full_name
        """).fetchall()

        directory = conn.execute("""
            SELECT id, full_name FROM employees ORDER BY full_name
        """).fetchall()

        accounts = conn.execute("""
            SELECT u.id, u.full_name, u.username, u.role, u.status, u.employee_id,
                   u.department_id, d.name AS department_name,
                   u.position_id, p.title AS position_title,
                   p.level AS position_level
            FROM users AS u
            LEFT JOIN departments AS d ON d.id = u.department_id
            LEFT JOIN organizational_positions AS p ON p.id = u.position_id
            ORDER BY u.full_name
        """).fetchall()
        unassigned_accounts = [account for account in accounts
                               if not account["department_id"]]

        org = organization_snapshot(conn, current_user())

    return render_template(
        "manage_hierarchy.html",
        departments=departments,
        edit_department=edit_department,
        positions=positions,
        employees=employees,
        directory=directory,
        accounts=accounts,
        unassigned_accounts=unassigned_accounts,
        position_level_label=POSITION_LEVEL_LABEL,
        org=org
    )
