
from datetime import datetime
from functools import wraps

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
)
from flask_login import login_required, current_user

from . import project_bp
from .models import (
    Project,
    ProjectMember,
    ProjectMilestone,
    ProjectTask,
)
from .services import (
    ProjectServiceError,
    create_project,
    update_project,
    add_project_member,
    create_milestone,
    create_task,
    get_project_progress,
    archive_project,
)


# --------------------------------------------------
# AUTHENTICATION AND PERMISSION CHECKING
# --------------------------------------------------

def project_permission(permission_name):
    """
    Temporary integration point for the existing permission system.

    Connect current_user.has_permission() to the actual permission
    checker in your application before enabling this module.
    """

    def decorator(view_function):
        @wraps(view_function)
        @login_required
        def wrapped_view(*args, **kwargs):
            checker = getattr(current_user, "has_permission", None)

            # Fail closed: do not grant access if permission checking
            # is not configured.
            if not callable(checker):
                abort(403)

            if not checker(permission_name):
                abort(403)

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator


def get_current_user_id():
    try:
        return int(current_user.get_id())
    except (TypeError, ValueError):
        abort(403)


def parse_date(value, field_name):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ProjectServiceError(
            f"Invalid {field_name}. Use YYYY-MM-DD format."
        )


def form_data():
    """Convert submitted HTML form dates into Python date objects."""

    data = request.form.to_dict()

    date_fields = {
        "start_date": "start date",
        "end_date": "end date",
        "due_date": "due date",
    }

    for field, label in date_fields.items():
        if field in data:
            data[field] = parse_date(data[field], label)

    return data


# --------------------------------------------------
# PROJECT DASHBOARD
# --------------------------------------------------

@project_bp.route("/")
@project_permission("project.view")
def dashboard():
    projects = Project.query.filter_by(
        is_archived=False
    ).order_by(Project.created_at.desc()).all()

    total_projects = len(projects)

    active_projects = sum(
        project.status == "Active" for project in projects
    )

    completed_projects = sum(
        project.status == "Completed" for project in projects
    )

    pending_tasks = ProjectTask.query.join(
        Project,
        ProjectTask.project_id == Project.id
    ).filter(
        Project.is_archived.is_(False),
        ProjectTask.status == "Pending"
    ).count()

    return render_template(
        "dashboard.html",
        projects=projects,
        total_projects=total_projects,
        active_projects=active_projects,
        completed_projects=completed_projects,
        pending_tasks=pending_tasks,
    )


# --------------------------------------------------
# PROJECT LIST
# --------------------------------------------------

@project_bp.route("/projects")
@project_permission("project.view")
def project_list():
    projects = Project.query.filter_by(
        is_archived=False
    ).order_by(Project.created_at.desc()).all()

    return render_template(
        "projects.html",
        projects=projects,
    )


# --------------------------------------------------
# CREATE PROJECT
# --------------------------------------------------

@project_bp.route("/projects/new", methods=["GET", "POST"])
@project_permission("project.create")
def new_project():

    if request.method == "POST":
        try:
            data = form_data()

            project = create_project(
                data=data,
                current_user_id=get_current_user_id(),
            )

            flash("Project created successfully.", "success")

            return redirect(
                url_for(
                    "project_management.project_detail",
                    project_id=project.id,
                )
            )

        except ProjectServiceError as error:
            flash(str(error), "danger")

    return render_template("project_form.html", project=None)


# --------------------------------------------------
# PROJECT DETAILS
# --------------------------------------------------

@project_bp.route("/projects/<int:project_id>")
@project_permission("project.view")
def project_detail(project_id):

    project = Project.query.filter_by(
        id=project_id,
        is_archived=False,
    ).first_or_404()

    members = ProjectMember.query.filter_by(
        project_id=project.id
    ).all()

    milestones = ProjectMilestone.query.filter_by(
        project_id=project.id
    ).order_by(ProjectMilestone.due_date.asc()).all()

    tasks = ProjectTask.query.filter_by(
        project_id=project.id
    ).order_by(ProjectTask.created_at.desc()).all()

    progress = get_project_progress(project.id)

    return render_template(
        "project_detail.html",
        project=project,
        members=members,
        milestones=milestones,
        tasks=tasks,
        progress=progress,
    )


# --------------------------------------------------
# UPDATE PROJECT
# --------------------------------------------------

@project_bp.route(
    "/projects/<int:project_id>/edit",
    methods=["GET", "POST"],
)
@project_permission("project.edit")
def edit_project(project_id):

    project = Project.query.filter_by(
        id=project_id,
        is_archived=False,
    ).first_or_404()

    if request.method == "POST":
        try:
            data = form_data()

            update_project(
                project_id=project_id,
                data=data,
                current_user_id=get_current_user_id(),
            )

            flash("Project updated successfully.", "success")

            return redirect(
                url_for(
                    "project_management.project_detail",
                    project_id=project_id,
                )
            )

        except ProjectServiceError as error:
            flash(str(error), "danger")

    return render_template(
        "project_form.html",
        project=project,
    )


# --------------------------------------------------
# ARCHIVE PROJECT
# --------------------------------------------------

@project_bp.route(
    "/projects/<int:project_id>/archive",
    methods=["POST"],
)
@project_permission("project.archive")
def archive_project_route(project_id):

    try:
        archive_project(
            project_id=project_id,
            current_user_id=get_current_user_id(),
        )

        flash("Project archived successfully.", "success")

    except ProjectServiceError as error:
        flash(str(error), "danger")

    return redirect(
        url_for("project_management.project_list")
    )


# --------------------------------------------------
# ADD PROJECT MEMBER
# --------------------------------------------------

@project_bp.route(
    "/projects/<int:project_id>/members/add",
    methods=["POST"],
)
@project_permission("project.assign_members")
def add_member(project_id):

    try:
        user_id = int(request.form.get("user_id", ""))

        add_project_member(
            project_id=project_id,
            user_id=user_id,
            team_role=request.form.get("team_role", "Team Member"),
            assigned_by=get_current_user_id(),
        )

        flash("Project member assigned successfully.", "success")

    except (ValueError, TypeError):
        flash("Please select a valid user.", "danger")

    except ProjectServiceError as error:
        flash(str(error), "danger")

    return redirect(
        url_for(
            "project_management.project_detail",
            project_id=project_id,
        )
    )


# --------------------------------------------------
# CREATE MILESTONE
# --------------------------------------------------

@project_bp.route(
    "/projects/<int:project_id>/milestones/new",
    methods=["GET", "POST"],
)
@project_permission("project.milestone.create")
def new_milestone(project_id):

    project = Project.query.filter_by(
        id=project_id,
        is_archived=False,
    ).first_or_404()

    if request.method == "POST":
        try:
            data = request.form.to_dict()

            if data.get("due_date"):
                data["due_date"] = parse_date(
                    data["due_date"],
                    "milestone due date",
                )

            create_milestone(
                project_id=project_id,
                data=data,
                current_user_id=get_current_user_id(),
            )

            flash("Milestone created successfully.", "success")

            return redirect(
                url_for(
                    "project_management.project_detail",
                    project_id=project_id,
                )
            )

        except ProjectServiceError as error:
            flash(str(error), "danger")

    return render_template(
        "milestones.html",
        project=project,
    )

from datetime import date

milestones = ProjectMilestone.query.filter_by(
    project_id=project_id
).order_by(ProjectMilestone.due_date.asc()).all()

return render_template(
    "milestones.html",
    project=project,
    milestones=milestones,
    today=date.today(),
)
# --------------------------------------------------
# CREATE TASK
# --------------------------------------------------

@project_bp.route(
    "/projects/<int:project_id>/tasks/new",
    methods=["GET", "POST"],
)
@project_permission("project.task.create")
def new_task(project_id):

    project = Project.query.filter_by(
        id=project_id,
        is_archived=False,
    ).first_or_404()

    if request.method == "POST":
        try:
            data = form_data()

            if data.get("assigned_user_id"):
                data["assigned_user_id"] = int(
                    data["assigned_user_id"]
                )
            else:
                data["assigned_user_id"] = None

            create_task(
                project_id=project_id,
                data=data,
                current_user_id=get_current_user_id(),
            )

            flash("Task created successfully.", "success")

            return redirect(
                url_for(
                    "project_management.project_detail",
                    project_id=project_id,
                )
            )

        except (ValueError, TypeError):
            flash("Please enter valid task information.", "danger")

        except ProjectServiceError as error:
            flash(str(error), "danger")

    return render_template(
        "tasks.html",
        project=project,
    )
