"""The project desk: the register, the team behind each job, its milestones and tasks.

A project is a piece of work the company has promised to a client - a kitchen
installation, a set of wardrobes, a shop fit-out. The desk keeps one row per job,
assigns the accounts already on the user master data, hangs the milestones and the
tasks off it, and sends the job up the shared approval chain. The same chain the
approvals desk answers moves the project from Draft to In Progress on a signature
or to Cancelled on a refusal, and reserves the budget allocation through the
helper a requisition uses - so the project card, the budget card and the approval
trail cannot tell three different stories about one job.

Every view checks the right it needs before it reads a row, every form runs
through the validators in ``services/project.py``, and every write stamps the
record and leaves one audit line with the fields that actually moved.
"""

from core.access import current_user
from core.access import has_permission
from core.access import perm
from core.config import STEP_LABEL
from core.db import get_db
from core.validation import clean_choice
from datetime import date
from datetime import datetime
from datetime import timezone
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import auditor_csv_response
from services.audit import change_set
from services.audit import log_activity
from services.budget import budget_problem_for
from services.links import linked_approval
from services.links import open_approval
from services.notifications import notify_approval_step
from services.notifications import retire_approval_notifications
from services.project import MILESTONE_STATUSES
from services.project import PROJECT_CURRENCIES
from services.project import PROJECT_MANUAL_STATUSES
from services.project import PROJECT_PRIORITIES
from services.project import PROJECT_STATUSES
from services.project import TASK_STATUSES
from services.project import TEAM_ROLES
from services.project import account_ref
from services.project import add_member
from services.project import allocation_options
from services.project import archive_project as archive_project_row
from services.project import assignable_accounts
from services.project import clean_id
from services.project import department_options
from services.project import insert_milestone
from services.project import insert_project
from services.project import insert_task
from services.project import link_approval
from services.project import member_rows
from services.project import milestone_rows
from services.project import milestone_values
from services.project import project_budget_position
from services.project import project_row
from services.project import project_rows
from services.project import project_summary
from services.project import project_values
from services.project import remove_member
from services.project import set_project_status
from services.project import status_breakdown
from services.project import task_rows
from services.project import task_values
from services.project import unarchive_project
from services.project import update_milestone
from services.project import update_project
from services.project import update_task
from services.project import upcoming_milestones
from services.project import workload_by_manager
from services.recency import stamp as stamp_record
from services.workflow import APPROVAL_DEPARTMENTS
from services.workflow import add_approval_action
from services.workflow import approval_path
import sqlite3


project_bp = Blueprint("project", __name__, url_prefix="/project")


@project_bp.context_processor
def project_template_context():
    """The vocabularies every project screen draws its selects from."""
    return {
        "project_statuses": PROJECT_STATUSES,
        "project_priorities": PROJECT_PRIORITIES,
        "milestone_statuses": MILESTONE_STATUSES,
        "task_statuses": TASK_STATUSES,
        "team_roles": TEAM_ROLES,
        "project_currencies": PROJECT_CURRENCIES,
        "manual_statuses": PROJECT_MANUAL_STATUSES,
        "approval_departments": tuple(APPROVAL_DEPARTMENTS),
        "step_label": STEP_LABEL,
    }


# ------------------------------------------------------------------- helpers
def guarded_project(db, project_id, include_archived=True):
    """One job or a 404; an archived row is only reachable by its own screens."""
    row = project_row(db, project_id, include_archived=include_archived)
    if row is None:
        abort(404)
    return row


def guarded_milestone(db, milestone_id):
    row = milestone_rows(db, milestone_id=milestone_id)
    if not row:
        abort(404)
    return row[0]


def guarded_task(db, task_id):
    row = task_rows(db, task_id=task_id)
    if not row:
        abort(404)
    return row[0]


def form_or_row(form, row, name):
    """A posted field, or the stored one when the form did not carry it."""
    value = form.get(name)
    if value is None:
        value = row[name] if name in row.keys() else None
    return value


def approval_title(values):
    return "Project {} - {}".format(values["project_code"], values["project_name"])


def approval_description(values):
    """What the signing desks read: who it is for, where, and when it lands."""
    parts = []
    if values.get("client_name"):
        parts.append("Client: {}".format(values["client_name"]))
    if values.get("location"):
        parts.append("Site: {}".format(values["location"]))
    if values.get("department_name"):
        parts.append("Desk: {}".format(values["department_name"]))
    if values.get("start_date") or values.get("end_date"):
        parts.append("Delivery {} to {}".format(values.get("start_date") or "—",
                                                values.get("end_date") or "—"))
    if values.get("description"):
        parts.append(values["description"])
    return "; ".join(parts)


def budget_problem(db, allocation_id, amount, project_id=None):
    if not allocation_id:
        return None
    return budget_problem_for(db, allocation_id, amount, "projects", project_id)


def revise_linked_chain(db, chain, values):
    """Re-read a returned project's figures on the chain it is travelling.

    The approval history stays, but the amount, the title and the path are read
    again: a budget pushed over a threshold has to earn the band it now falls in,
    so the extra desks sign it. A return has stopped the chain already, so
    nothing that was approved is rewritten.
    """
    path = approval_path(values["department"], values["budget"], "Project")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute("""
        UPDATE approval_requests
        SET title = ?, department = ?, amount = ?, description = ?,
            status = 'Pending', current_approver_step = ?, approval_path = ?,
            updated_at = ?
        WHERE id = ?
    """, (approval_title(values), values["department"], values["budget"],
          approval_description(values), path[0], ",".join(path), now,
          chain["id"]))
    add_approval_action(
        db, chain["id"], "Resubmitted",
        "Figures revised: {:,.2f} {}. Approval path: {}".format(
            values["budget"], values["currency"],
            " -> ".join(STEP_LABEL.get(step, step) for step in path)),
        step=path[0], previous_status=chain["status"])
    retire_approval_notifications(db, chain["id"])
    notify_approval_step(db, chain["id"], path[0], approval_title(values),
                         "Project", values["budget"],
                         exclude_user_ids=(current_user()["id"],))


# ------------------------------------------------------------------ overview
@project_bp.route("/")
@perm("project_dashboard")
def dashboard():
    """The desk at a glance: the register by status, who is carrying what."""
    db = get_db()
    return render_template(
        "project/dashboard.html",
        summary=project_summary(db),
        breakdown=status_breakdown(db),
        workload=workload_by_manager(db),
        upcoming=upcoming_milestones(db, 30, 8),
        recent=project_rows(db, limit=8),
        today=date.today().isoformat(),
        can_register=has_permission("project_register"),
    )


# ------------------------------------------------------------------ register
@project_bp.route("/projects")
@perm("project_register", "project_approval", "project_dashboard")
def projects():
    """The register. The approval desk reads the same rows, filtered to Pending."""
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in PROJECT_STATUSES:
        status = ""
    search = (request.args.get("search") or "").strip()
    department = (request.args.get("department") or "").strip()
    archived = request.args.get("archived") == "1" \
        and has_permission("project_register")
    rows = project_rows(db, status or None, search or None,
                        int(department) if department.isdigit() else None,
                        include_archived=archived)
    return render_template(
        "project/projects.html",
        projects=rows, status=status, search=search,
        department=department, archived=archived,
        departments=department_options(db),
        can_register=has_permission("project_register"),
        can_approve=has_permission("project_approval"))


@project_bp.route("/projects/new", methods=["GET", "POST"])
@perm("project_register")
def create_project():
    db = get_db()
    if request.method == "POST":
        try:
            values = project_values(request.form, db)
            problem = budget_problem(db, values["budget_allocation_id"],
                                     values["budget"])
            if problem:
                raise ValueError(problem)
            user = current_user()
            project_id = insert_project(db, values, user)
            stamp_record(db, "projects", project_id, user)
            db.commit()
            log_activity(
                "CREATE", "project",
                "Project '{}' registered as a draft ({} {})".format(
                    values["project_code"], values["budget"], values["currency"]),
                record_type="project", record_id=project_id, updated=values)
            flash("Project {} registered as a draft. Submit it to the approval"
                  " desk before work starts.".format(values["project_code"]),
                  "success")
            return redirect(url_for("project.project_detail",
                                    project_id=project_id))
        except ValueError as error:
            flash(str(error), "error")
    return render_template(
        "project/project_form.html", project=None,
        departments=department_options(db),
        allocations=allocation_options(db),
        managers=assignable_accounts(db))


@project_bp.route("/projects/<int:project_id>")
@perm("project_register", "project_approval", "project_team",
      "project_milestones", "project_tasks", "project_dashboard")
def project_detail(project_id):
    db = get_db()
    project = guarded_project(db, project_id)
    return render_template(
        "project/project_detail.html",
        project=project,
        members=member_rows(db, project_id=project_id),
        milestones=milestone_rows(db, project_id=project_id),
        tasks=task_rows(db, project_id=project_id),
        chain=linked_approval(db, "projects", project_id),
        budget=project_budget_position(db, project),
        accounts=assignable_accounts(db),
        today=date.today().isoformat(),
        can_register=has_permission("project_register"),
        can_approve=has_permission("project_approval"),
        can_team=has_permission("project_team"),
        can_milestones=has_permission("project_milestones"),
        can_tasks=has_permission("project_tasks"),
        can_requisition=has_permission("procurement_pr"))


@project_bp.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@perm("project_register")
def edit_project(project_id):
    db = get_db()
    project = guarded_project(db, project_id)
    chain = linked_approval(db, "projects", project_id)
    # A draft is still being written; a returned project comes back for a
    # revision. A project sitting with the approval desk is read-only here.
    rewritable = project["status"] == "Draft" or (
        project["status"] == "Pending" and chain is not None
        and chain["status"] == "Returned")
    if request.method == "POST":
        if not rewritable:
            flash("A project that is with the approval desk can no longer be"
                  " rewritten.", "error")
            return redirect(url_for("project.project_detail",
                                    project_id=project_id))
        try:
            values = project_values(request.form, db, exclude_id=project_id)
            problem = budget_problem(db, values["budget_allocation_id"],
                                     values["budget"], project_id)
            if problem:
                raise ValueError(problem)
            user = current_user()
            update_project(db, project_id, values)
            stamp_record(db, "projects", project_id, user)
            if chain is not None and chain["status"] == "Returned":
                revise_linked_chain(db, chain, values)
            db.commit()
            previous, updated = change_set(project, values)
            log_activity("UPDATE", "project",
                         "Project '{}' revised".format(values["project_code"]),
                         record_type="project", record_id=project_id,
                         previous=previous, updated=updated)
            flash("Project updated.", "success")
            return redirect(url_for("project.project_detail",
                                    project_id=project_id))
        except ValueError as error:
            flash(str(error), "error")
    return render_template(
        "project/project_form.html", project=project,
        departments=department_options(db),
        allocations=allocation_options(db),
        managers=assignable_accounts(db))


@project_bp.route("/projects/<int:project_id>/submit", methods=["POST"])
@perm("project_register")
def submit_project(project_id):
    """Send a draft up the shared chain and remember which row raised it."""
    db = get_db()
    project = guarded_project(db, project_id)
    if project["status"] != "Draft":
        flash("Only a draft is submitted for a decision.", "info")
        return redirect(url_for("project.project_detail", project_id=project_id))
    problem = budget_problem(db, project["budget_allocation_id"],
                             project["budget"], project_id)
    if problem:
        flash(problem, "error")
        return redirect(url_for("project.project_detail", project_id=project_id))
    values = {
        "project_code": project["project_code"],
        "project_name": project["project_name"],
        "client_name": project["client_name"],
        "location": project["location"],
        "description": project["description"],
        "department": project["department"],
        "department_name": project["department_name"],
        "start_date": project["start_date"],
        "end_date": project["end_date"],
        "budget": float(project["budget"] or 0),
        "currency": project["currency"],
    }
    request_id = open_approval(
        db, "projects", project_id, approval_title(values), "Project",
        values["department"], values["budget"],
        approval_description(values))
    link_approval(db, project_id, request_id)
    stamp_record(db, "projects", project_id)
    db.commit()
    log_activity(
        "UPDATE", "project",
        "Project '{}' submitted for approval (#{})".format(
            project["project_code"], request_id),
        record_type="project", record_id=project_id,
        previous={"status": project["status"]}, updated={"status": "Pending"})
    flash("Project sent to the approval desk.", "success")
    return redirect(url_for("project.project_detail", project_id=project_id))


@project_bp.route("/projects/<int:project_id>/status", methods=["POST"])
@perm("project_register")
def set_status(project_id):
    """The register's own moves once the work is running."""
    db = get_db()
    project = guarded_project(db, project_id)
    allowed = {
        "Draft": ("Cancelled",),
        "In Progress": ("On Hold", "Completed", "Cancelled"),
        "On Hold": ("In Progress", "Completed", "Cancelled"),
    }.get(project["status"], ())
    try:
        status = clean_choice(request.form.get("status"), "Status",
                              PROJECT_MANUAL_STATUSES)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("project.project_detail", project_id=project_id))
    if status not in allowed:
        flash("A {} project cannot be moved to {} from this screen.".format(
            project["status"].lower(), status.lower()), "error")
        return redirect(url_for("project.project_detail", project_id=project_id))
    set_project_status(db, project_id, status)
    stamp_record(db, "projects", project_id)
    db.commit()
    log_activity("UPDATE", "project",
                 "Project '{}' moved to {}".format(project["project_code"],
                                                   status),
                 record_type="project", record_id=project_id,
                 previous={"status": project["status"]},
                 updated={"status": status})
    flash("Project marked {}.".format(status.lower()), "success")
    return redirect(url_for("project.project_detail", project_id=project_id))


@project_bp.route("/projects/<int:project_id>/archive", methods=["POST"])
@perm("project_register")
def archive_project(project_id):
    db = get_db()
    project = guarded_project(db, project_id, include_archived=True)
    if project["is_archived"]:
        flash("That project is already archived.", "info")
        return redirect(url_for("project.projects"))
    archive_project_row(db, project_id)
    stamp_record(db, "projects", project_id)
    db.commit()
    log_activity("ARCHIVE", "project",
                 "Project '{}' archived".format(project["project_code"]),
                 record_type="project", record_id=project_id,
                 previous={"is_archived": 0}, updated={"is_archived": 1})
    flash("Project archived.", "success")
    return redirect(url_for("project.projects"))


@project_bp.route("/projects/<int:project_id>/restore", methods=["POST"])
@perm("project_register")
def restore_project(project_id):
    db = get_db()
    project = guarded_project(db, project_id, include_archived=True)
    if not project["is_archived"]:
        flash("That project is not archived.", "info")
        return redirect(url_for("project.projects", archived="1"))
    unarchive_project(db, project_id)
    stamp_record(db, "projects", project_id)
    db.commit()
    log_activity("RESTORE", "project",
                 "Project '{}' restored to the register".format(
                     project["project_code"]),
                 record_type="project", record_id=project_id,
                 previous={"is_archived": 1}, updated={"is_archived": 0})
    flash("Project restored.", "success")
    return redirect(url_for("project.projects"))


# ---------------------------------------------------------------------- team
@project_bp.route("/projects/<int:project_id>/members", methods=["POST"])
@perm("project_team")
def add_project_member(project_id):
    db = get_db()
    project = guarded_project(db, project_id)
    try:
        account = account_ref(db, clean_id(request.form.get("user_id"), "account"),
                              "account")
        if account is None:
            raise ValueError("Choose an account from the staff list.")
        team_role = clean_choice(request.form.get("team_role"), "Team role",
                                 TEAM_ROLES, default="Team Member")
        user = current_user()
        add_member(db, project_id, account, team_role, user)
        stamp_record(db, "projects", project_id, user)
        db.commit()
        log_activity("UPDATE", "project",
                     "{} added to project '{}' as {}".format(
                         account["full_name"], project["project_code"], team_role),
                     record_type="project", record_id=project_id,
                     updated={"username": account["username"],
                              "team_role": team_role})
        flash("{} assigned to the project.".format(account["full_name"]),
              "success")
    except ValueError as error:
        flash(str(error), "error")
    except sqlite3.IntegrityError:
        flash("That account is already on this project.", "error")
    return redirect(url_for("project.project_detail", project_id=project_id))


@project_bp.route("/projects/<int:project_id>/members/<int:member_id>/remove",
                  methods=["POST"])
@perm("project_team")
def remove_project_member(project_id, member_id):
    db = get_db()
    project = guarded_project(db, project_id)
    member = db.execute(
        "SELECT * FROM project_members WHERE id = ? AND project_id = ?",
        (member_id, project_id)).fetchone()
    if member is None:
        abort(404)
    remove_member(db, member_id)
    stamp_record(db, "projects", project_id)
    db.commit()
    log_activity("UPDATE", "project",
                 "{} released from project '{}'".format(
                     member["member_name"] or member["username"],
                     project["project_code"]),
                 record_type="project", record_id=project_id,
                 previous={"username": member["username"],
                           "team_role": member["team_role"]})
    flash("Team member released.", "success")
    return redirect(url_for("project.project_detail", project_id=project_id))


# ---------------------------------------------------------------- milestones
@project_bp.route("/milestones")
@perm("project_milestones", "project_register")
def milestones():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in MILESTONE_STATUSES:
        status = ""
    project = (request.args.get("project") or "").strip()
    search = (request.args.get("search") or "").strip()
    rows = milestone_rows(db,
                          project_id=int(project) if project.isdigit() else None,
                          search=search or None)
    if status:
        rows = [row for row in rows if row["status"] == status]
    return render_template(
        "project/milestones.html", milestones=rows, projects=project_rows(db),
        status=status, search=search, project=project,
        today=date.today().isoformat(),
        can_milestones=has_permission("project_milestones"))


@project_bp.route("/projects/<int:project_id>/milestones", methods=["POST"])
@perm("project_milestones")
def new_milestone(project_id):
    db = get_db()
    project = guarded_project(db, project_id)
    try:
        form = request.form.to_dict()
        form.setdefault("status", "Pending")
        values = milestone_values(form)
        user = current_user()
        milestone_id = insert_milestone(db, project_id, values, user)
        stamp_record(db, "project_milestones", milestone_id, user)
        stamp_record(db, "projects", project_id, user)
        db.commit()
        log_activity("CREATE", "project",
                     "Milestone '{}' set on project '{}' for {}".format(
                         values["milestone_name"], project["project_code"],
                         values["due_date"]),
                     record_type="project_milestone", record_id=milestone_id,
                     updated=values)
        flash("Milestone recorded.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("project.project_detail", project_id=project_id))


@project_bp.route("/milestones/<int:milestone_id>", methods=["POST"])
@perm("project_milestones")
def update_project_milestone(milestone_id):
    db = get_db()
    milestone = guarded_milestone(db, milestone_id)
    try:
        form = request.form.to_dict()
        form.setdefault("milestone_name", milestone["milestone_name"])
        form.setdefault("due_date", milestone["due_date"])
        form.setdefault("description", milestone["description"])
        values = milestone_values(form)
        update_milestone(db, milestone_id, values)
        stamp_record(db, "project_milestones", milestone_id)
        stamp_record(db, "projects", milestone["project_id"])
        db.commit()
        previous, updated = change_set(milestone, values)
        log_activity("UPDATE", "project",
                     "Milestone '{}' marked {}".format(
                         milestone["milestone_name"], values["status"]),
                     record_type="project_milestone", record_id=milestone_id,
                     previous=previous, updated=updated)
        flash("Milestone updated.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(request.referrer
                    or url_for("project.project_detail",
                               project_id=milestone["project_id"]))


# --------------------------------------------------------------------- tasks
@project_bp.route("/tasks")
@perm("project_tasks")
def tasks():
    """The tasks handed to an account, or the whole board when it opens wider."""
    db = get_db()
    user = current_user()
    mine = request.args.get("mine", "1") != "0"
    status = (request.args.get("status") or "").strip()
    if status and status not in TASK_STATUSES:
        status = ""
    overdue = request.args.get("overdue") == "1"
    search = (request.args.get("search") or "").strip()
    project = (request.args.get("project") or "").strip()
    rows = task_rows(db,
                     project_id=int(project) if project.isdigit() else None,
                     user_id=user["id"] if mine else None,
                     status=status or None, only_overdue=overdue,
                     search=search or None)
    return render_template(
        "project/tasks.html", tasks=rows, mine=mine, status=status,
        overdue=overdue, search=search, project=project,
        projects=project_rows(db), today=date.today().isoformat())


@project_bp.route("/projects/<int:project_id>/tasks", methods=["POST"])
@perm("project_tasks")
def new_task(project_id):
    db = get_db()
    project = guarded_project(db, project_id)
    try:
        form = request.form.to_dict()
        form.setdefault("status", "Not Started")
        values = task_values(form, db, project_id)
        user = current_user()
        task_id = insert_task(db, project_id, values, user)
        stamp_record(db, "project_tasks", task_id, user)
        stamp_record(db, "projects", project_id, user)
        db.commit()
        log_activity("CREATE", "project",
                     "Task '{}' raised on project '{}'".format(
                         values["task_title"], project["project_code"]),
                     record_type="project_task", record_id=task_id,
                     updated=values)
        flash("Task added to the project.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("project.project_detail", project_id=project_id))


@project_bp.route("/tasks/<int:task_id>", methods=["POST"])
@perm("project_tasks")
def update_project_task(task_id):
    """Move a task along its status and progress without rewriting the brief."""
    db = get_db()
    task = guarded_task(db, task_id)
    try:
        form = request.form.to_dict()
        fields = ("task_title", "description", "milestone_id", "assigned_user_id",
                  "start_date", "due_date", "status", "priority", "progress")
        payload = {name: form_or_row(form, task, name) for name in fields}
        payload["assigned_user_id"] = clean_id(payload["assigned_user_id"],
                                               "assignee")
        values = task_values(payload, db, task["project_id"])
        update_task(db, task_id, values)
        stamp_record(db, "project_tasks", task_id)
        stamp_record(db, "projects", task["project_id"])
        db.commit()
        previous, updated = change_set(task, values)
        log_activity("UPDATE", "project",
                     "Task '{}' marked {}".format(values["task_title"],
                                                  values["status"]),
                     record_type="project_task", record_id=task_id,
                     previous=previous, updated=updated)
        flash("Task updated.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(request.referrer
                    or url_for("project.project_detail",
                               project_id=task["project_id"]))


# ------------------------------------------------------------------- reports
@project_bp.route("/reports")
@perm("project_reports")
def reports():
    db = get_db()
    return render_template(
        "project/reports.html",
        summary=project_summary(db),
        breakdown=status_breakdown(db),
        workload=workload_by_manager(db),
        upcoming=upcoming_milestones(db, 60, 20),
        projects=project_rows(db),
        today=date.today().isoformat())


EXPORT_COLUMNS = {
    "projects": (("project_code", "Code"), ("project_name", "Project"),
                 ("client_name", "Client"), ("location", "Site"),
                 ("department_name", "Desk"), ("status", "Status"),
                 ("priority", "Priority"), ("manager_name", "Manager"),
                 ("start_date", "Start"), ("end_date", "End"),
                 ("budget", "Budget"), ("currency", "Currency"),
                 ("progress", "Progress %"), ("open_tasks", "Open tasks"),
                 ("created_by_name", "Raised by"),
                 ("updated_at", "Last modified")),
    "milestones": (("milestone_name", "Milestone"), ("project_code", "Project"),
                   ("project_name", "Job"), ("due_date", "Due"),
                   ("completed_date", "Completed"), ("status", "Status"),
                   ("created_by_name", "Set by"),
                   ("updated_at", "Last modified")),
    "tasks": (("task_title", "Task"), ("project_code", "Project"),
              ("project_name", "Job"), ("milestone_name", "Milestone"),
              ("assigned_name", "Assigned to"), ("status", "Status"),
              ("priority", "Priority"), ("progress", "Progress %"),
              ("start_date", "Start"), ("due_date", "Due"),
              ("created_by_name", "Raised by"),
              ("updated_at", "Last modified")),
}

# The register behind each export; a signed-off file still has to be raised by a
# seat that holds that desk, so the CSV is not a side door around a boundary.
EXPORT_DESK_RIGHT = {
    "projects": "project_register",
    "milestones": "project_milestones",
    "tasks": "project_tasks",
}


@project_bp.route("/reports/<kind>.csv")
@perm("project_reports")
def export(kind):
    columns = EXPORT_COLUMNS.get(kind)
    desk_right = EXPORT_DESK_RIGHT.get(kind)
    if columns is None or desk_right is None:
        abort(404)
    if not has_permission(desk_right):
        flash("That register is not on your desk.", "error")
        return redirect(url_for("project.reports"))
    db = get_db()
    if kind == "projects":
        rows = project_rows(db, include_archived=True)
    elif kind == "milestones":
        rows = milestone_rows(db)
    else:
        rows = task_rows(db)
    log_activity("EXPORT", "project",
                 "Project {} exported ({} rows)".format(kind, len(rows)))
    db.commit()
    return auditor_csv_response("project_{}_{}".format(
        kind, date.today().isoformat()), columns, rows)


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(project_bp)
