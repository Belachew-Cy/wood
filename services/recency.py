"""Who last changed a record, and when.

Every table a screen can edit carries the same three columns. A write stamps
them with the signed-in account after its own INSERT or UPDATE and before the
commit, so the stamp and the change land or roll back together. The creator
columns are never touched: who made a record and who last touched it are two
different facts.
"""

from core.access import current_user
from core.db import connect_db
from core.db import qualified_table
from database import database_config
from datetime import datetime
from datetime import timezone
from flask import has_request_context


# table -> the column holding the creator's id and name, when the table has one.
# The pair seeds the stamp of rows written before this feature existed.
RECORD_TABLES = {
    "users": (None, None),
    "employees": (None, None),
    "budget_plans": ("created_by", "created_by_name"),
    "budget_allocations": ("created_by", "created_by_name"),
    "budget_requests": ("requested_by", "requested_by_name"),
    "budget_expenditures": ("recorded_by", "recorded_by_name"),
    "inventory_items": (None, None),
    "approval_requests": ("created_by", "created_by_name"),
    "audit_reviews": ("created_by", None),
    "audit_findings": ("created_by", None),
    "procurement_suppliers": (None, None),
    "procurement_requests": ("requested_by", "requested_by_name"),
    "procurement_orders": ("created_by", "created_by_name"),
    "procurement_deliveries": ("received_by", "received_by_name"),
    "finance_transactions": ("created_by", None),
    "finance_invoices": ("created_by", None),
    "finance_payments": ("created_by", None),
    "letters": ("created_by", None),
    # A vacancy names the account that raised it, so the stamp can start from the
    # creator; an application is written by the candidate, so it starts blank.
    "job_vacancies": ("created_by_id", "created_by_name"),
    "job_applications": (None, None),
    # The exam and interview desks stamp their rows the same way; without these
    # entries the columns are never created and the stamp fails on write.
    "recruitment_exams": ("created_by_id", "created_by_name"),
    "recruitment_interviews": ("created_by_id", "created_by_name"),
    # Every marketing desk stamps the same way; a research record names the person
    # who prepared it rather than who signed in, so its creator pair differs.
    "marketing_plans": ("created_by", "created_by_name"),
    "marketing_campaigns": ("created_by", "created_by_name"),
    "marketing_leads": ("created_by", "created_by_name"),
    "market_research": ("prepared_by", "prepared_by_name"),
    "digital_campaigns": ("created_by", "created_by_name"),
    "customer_feedback": ("created_by", "created_by_name"),
    # The project register stamps the job and everything hanging off it, so the
    # detail page can say who last touched a milestone or a task.
    "projects": ("created_by", "created_by_name"),
    "project_milestones": ("created_by", "created_by_name"),
    "project_tasks": ("created_by", "created_by_name"),
}

RECENCY_COLUMNS = (("updated_by", "INTEGER"), ("updated_by_name", "TEXT"),
                   ("updated_at", "TEXT"))


def _ref(table):
    return qualified_table(table)


def _schema(table):
    ref = _ref(table)
    return ref.split(".")[0] if "." in ref else "main"


def _columns(db, table):
    return {row[1] for row in db.execute(
        "PRAGMA %s.table_info(%s)" % (_schema(table), table))}


def _add_missing(db, table):
    present = _columns(db, table)
    added = False
    for name, kind in RECENCY_COLUMNS:
        if name not in present:
            db.execute("ALTER TABLE %s ADD COLUMN %s %s"
                       % (_ref(table), name, kind))
            added = True
    return added


def _seed(db, table, creator_id, creator_name):
    """Give rows written before this feature a stamp: their creator, at creation."""
    present = _columns(db, table)
    sets = []
    if "updated_at" in present and "created_at" in present:
        sets.append("updated_at = COALESCE(updated_at, created_at)")
    if creator_id and creator_id in present:
        sets.append("updated_by = COALESCE(updated_by, %s)" % creator_id)
    if creator_name and creator_name in present:
        sets.append("updated_by_name = COALESCE(updated_by_name, %s)"
                    % creator_name)
    if sets:
        db.execute("UPDATE %s SET %s" % (_ref(table), ", ".join(sets)))


def ensure_recency_schema():
    """Add the stamp columns to every record table and seed old rows once."""
    db = connect_db()
    try:
        for table, (creator_id, creator_name) in RECORD_TABLES.items():
            if not database_config.table_exists(db, table):
                continue
            if _add_missing(db, table):
                _seed(db, table, creator_id, creator_name)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def stamp(db, table, record_id, user=None, name=None):
    """Mark one record as just changed by the signed-in account."""
    if record_id is None:
        return
    if user is None and name is None and has_request_context():
        user = current_user()
    user_id = user["id"] if user is not None else None
    editor = name or (user["full_name"] if user is not None else "System")
    db.execute(
        "UPDATE %s SET updated_by = ?, updated_by_name = ?, updated_at = ?"
        " WHERE id = ?" % _ref(table),
        (user_id, editor, datetime.now(timezone.utc).isoformat(timespec="seconds"),
         record_id))

