"""Links between approvals, requisitions, budget and orders."""

from core.access import current_user
from core.config import STEP_LABEL
from datetime import datetime
from datetime import timezone
from services.budget import budget_commit
from services.budget import budget_release
from services.email import email_request_receipt
from services.notifications import notify_approval_step
from services.workflow import APPROVAL_STEPS
from services.workflow import account_position_level
from services.workflow import add_approval_action
from services.workflow import approval_path


def migrate_approval_links(db):
    """Remember which module record raised each request, and on which stage each
    decision was taken.

    Nullable columns and an index, so nothing that already exists changes. A request
    raised on the approvals screen simply leaves the two source columns empty.
    """
    columns = {row[1] for row in
               db.execute("PRAGMA table_info(approval_requests)").fetchall()}
    if "source_table" not in columns:
        db.execute("ALTER TABLE approval_requests ADD COLUMN source_table TEXT")
    if "source_id" not in columns:
        db.execute("ALTER TABLE approval_requests ADD COLUMN source_id INTEGER")
    if "approval_path" not in columns:
        db.execute("ALTER TABLE approval_requests ADD COLUMN approval_path TEXT"
                   " NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS idx_approval_source"
               " ON approval_requests(source_table, source_id)")

    actions = {row[1] for row in
               db.execute("PRAGMA table_info(approval_actions)").fetchall()}
    if "step" not in actions:
        db.execute("ALTER TABLE approval_actions ADD COLUMN step TEXT")
    if "previous_status" not in actions:
        db.execute("ALTER TABLE approval_actions ADD COLUMN previous_status TEXT")


def request_chain(row):
    """The ordered steps a stored request has to clear.

    A request opened by a newer build carries the chain it was opened with, so
    retuning the policy halfway through cannot shorten it or move a stage nobody
    signed. Rows that predate the column fall back to the live policy.
    """
    stored = (row["approval_path"] if "approval_path" in row.keys() else "") or ""
    if stored.strip():
        return [step for step in stored.split(",") if step in APPROVAL_STEPS]
    request_type = row["request_type"] if "request_type" in row.keys() else ""
    return approval_path(row["department"], float(row["amount"]), request_type)


LINKED_SOURCES = ("leave_requests", "procurement_requests")


def open_approval(db, source_table, source_id, title, request_type,
                  department, amount, description):
    """Raise the chain a module record has to clear, and remember the record.

    The module screens call this instead of keeping a second review status, so a
    request has one path, one history and one set of notifications wherever it was
    born. The chain is frozen onto the row for the same reason.
    """
    path = approval_path(department, amount, request_type)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    user = current_user()
    result = db.execute("""
        INSERT INTO approval_requests (
            title, request_type, department, amount, description,
            created_by, created_by_name, current_approver_step,
            status, created_at, updated_at, source_table, source_id, approval_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, ?)
    """, (
        title, request_type, department, amount, description,
        user["id"], user["full_name"], path[0], now, now,
        source_table, source_id, ",".join(path),
    ))
    request_id = result.lastrowid
    add_approval_action(db, request_id, "Submitted",
                        "Approval path: {}".format(" -> ".join(
                            STEP_LABEL.get(step, step) for step in path)),
                        step=path[0])
    notify_approval_step(db, request_id, path[0], title, request_type, amount,
                         exclude_user_ids=(user["id"],))
    request_row = db.execute("SELECT * FROM approval_requests WHERE id = ?",
                             (request_id,)).fetchone()
    if request_row is not None:
        email_request_receipt(db, request_row, user)
    return request_id


def linked_approval(db, source_table, source_id):
    """The chain a module record is travelling, newest first."""
    if source_table not in LINKED_SOURCES:
        return None
    return db.execute("""
        SELECT * FROM approval_requests
        WHERE source_table = ? AND source_id = ?
        ORDER BY id DESC LIMIT 1
    """, (source_table, source_id)).fetchone()


def sync_linked_status(db, row, status, comments, actor_name):
    """Mirror a chain decision onto the module record that raised it.

    Only a final decision moves the record. A return leaves the record pending and
    posts the reason, so the module screen shows why it came back.
    """
    table = row["source_table"] if "source_table" in row.keys() else None
    if table not in LINKED_SOURCES:
        return
    if status in ("Approved", "Rejected"):
        db.execute("UPDATE %s SET status = ? WHERE id = ? AND status IN ('Pending', 'Draft')"
                   % table, (status, row["source_id"]))
        if table == "leave_requests":
            db.execute("UPDATE leave_requests SET reviewed_by = ?, review_comment = ?"
                       " WHERE id = ?", (actor_name, comments, row["source_id"]))
        elif table == "procurement_requests":
            request = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                                 (row["source_id"],)).fetchone()
            if request is not None:
                allocation_id = (request["budget_allocation_id"]
                                 if "budget_allocation_id" in request.keys() else None)
                if status == "Rejected":
                    budget_release(db, "procurement_requests", request["id"])
                elif allocation_id:
                    budget_commit(
                        db, allocation_id, "procurement_requests", request["id"],
                        request["estimated_cost"],
                        "Requisition {}".format(request["request_number"]))
                if status == "Rejected":
                    db.execute("UPDATE finance.procurement_requests SET status = 'Rejected'"
                               " WHERE id = ?", (request["id"],))
    elif status == "Returned" and table == "leave_requests":
        db.execute("UPDATE leave_requests SET review_comment = ? WHERE id = ?",
                   (comments, row["source_id"]))
    elif status == "Returned" and table == "procurement_requests":
        budget_release(db, "procurement_requests", row["source_id"])


def can_manage_approval_policy(user):
    if not user:
        return False
    if user["role"] == "admin":
        return True
    # The CEO seat owns the policy; any other leadership position does not.
    return account_position_level(user) == 0
