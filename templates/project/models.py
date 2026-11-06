
from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint
from extensions import db


class Project(db.Model):
    __tablename__ = "projects"
    __table_args__ = {"schema": "project_management"}

    id = db.Column(db.Integer, primary_key=True)
    project_code = db.Column(db.String(50), unique=True, nullable=False)
    project_name = db.Column(db.String(200), nullable=False)
    client_name = db.Column(db.String(200))
    location = db.Column(db.String(250))
    description = db.Column(db.Text)

    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    budget = db.Column(db.Numeric(14, 2), default=0)
    status = db.Column(db.String(30), nullable=False, default="Planning")
    priority = db.Column(db.String(20), nullable=False, default="Medium")

    # References to existing User Master Data.
    # No duplicate user or employee table is created.
    manager_user_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.Integer, nullable=False)
    updated_by = db.Column(db.Integer)

    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "budget >= 0",
            name="ck_project_budget_nonnegative"
        ),
        CheckConstraint(
            "status IN ('Planning', 'Active', 'On Hold', 'Completed', 'Cancelled')",
            name="ck_project_status"
        ),
        CheckConstraint(
            "priority IN ('Low', 'Medium', 'High', 'Critical')",
            name="ck_project_priority"
        ),
        {"schema": "project_management"}
    )


class ProjectMember(db.Model):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "user_id",
            name="uq_project_member"
        ),
        {"schema": "project_management"}
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project_management.projects.id", ondelete="CASCADE"),
        nullable=False
    )
    user_id = db.Column(db.Integer, nullable=False)
    team_role = db.Column(db.String(100), default="Team Member")
    assigned_by = db.Column(db.Integer, nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)


class ProjectMilestone(db.Model):
    __tablename__ = "project_milestones"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Pending', 'In Progress', 'Completed', 'Delayed')",
            name="ck_milestone_status"
        ),
        {"schema": "project_management"}
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project_management.projects.id", ondelete="CASCADE"),
        nullable=False
    )
    milestone_name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    due_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)
    status = db.Column(db.String(30), nullable=False, default="Pending")
    created_by = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ProjectTask(db.Model):
    __tablename__ = "project_tasks"
    __table_args__ = (
        CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_task_progress"
        ),
        CheckConstraint(
            "status IN ('Pending', 'In Progress', 'Completed', 'Blocked')",
            name="ck_task_status"
        ),
        CheckConstraint(
            "priority IN ('Low', 'Medium', 'High', 'Critical')",
            name="ck_task_priority"
        ),
        {"schema": "project_management"}
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project_management.projects.id", ondelete="CASCADE"),
        nullable=False
    )
    task_title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # Uses the existing User Master Data user ID.
    assigned_user_id = db.Column(db.Integer)

    start_date = db.Column(db.Date)
    due_date = db.Column(db.Date)

    status = db.Column(db.String(30), nullable=False, default="Pending")
    priority = db.Column(db.String(20), nullable=False, default="Medium")
    progress = db.Column(db.Integer, nullable=False, default=0)

    created_by = db.Column(db.Integer, nullable=False)
    updated_by = db.Column(db.Integer)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
