"""Approval queue, request detail and decisions."""

from core.access import current_user
from core.access import perm
from core.config import STEP_LABEL
from core.db import get_db
from datetime import datetime
from datetime import timezone
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import log_activity
from services.links import request_chain
from services.links import sync_linked_status
from services.notifications import notify_approval_step
from services.notifications import notify_request_creator
from services.notifications import retire_approval_notifications
from services.recency import stamp as stamp_record
from services.workflow import APPROVAL_DEPARTMENTS
from services.workflow import REQUEST_TYPES
from services.workflow import add_approval_action
from services.workflow import already_signed
from services.workflow import approval_acted_ids
from services.workflow import approval_limits
from services.workflow import approval_path
from services.workflow import can_decide_request
from services.workflow import can_view_request


approvals_bp = Blueprint("approvals", __name__, url_prefix="/approvals")


@approvals_bp.context_processor
def approval_template_context():
    two_step, three_step = approval_limits()
    return {"request_types": REQUEST_TYPES,
            "departments": tuple(APPROVAL_DEPARTMENTS),
            "step_label": STEP_LABEL,
            "two_step": two_step,
            "three_step": three_step}


@approvals_bp.route("/")
@perm("approvals", "audit")
def dashboard():
    db = get_db()
    user = current_user()
    acted = approval_acted_ids(db, user["id"])

    rows = db.execute("""
        SELECT * FROM approval_requests
        ORDER BY CASE status
                     WHEN 'Pending' THEN 0 WHEN 'Returned' THEN 1 ELSE 2 END,
                 updated_at DESC
        LIMIT 300
    """).fetchall()

    visible = [row for row in rows if can_view_request(row, user, acted)]

    # The PR and PO rails open the same queue narrowed to the purchase chains:
    # a pending view for the requisitions waiting on a signature, and the
    # approved view for the purchases that may now become purchase orders.
    selected_type = (request.args.get("type") or "").strip()
    if selected_type not in REQUEST_TYPES:
        selected_type = ""
    selected_status = (request.args.get("status") or "").strip()
    if selected_status not in ("Pending", "Returned", "Approved", "Rejected"):
        selected_status = ""
    if selected_type:
        visible = [row for row in visible if row["request_type"] == selected_type]
    if selected_status:
        visible = [row for row in visible if row["status"] == selected_status]

    decidable = {row["id"] for row in visible if can_decide_request(row, user)}

    counts = {
        "total": len(visible),
        "pending": sum(row["status"] == "Pending" for row in visible),
        "approved": sum(row["status"] == "Approved" for row in visible),
        "rejected": sum(row["status"] == "Rejected" for row in visible),
        "returned": sum(row["status"] == "Returned" for row in visible),
        "mine": sum(row["created_by"] == user["id"] for row in visible),
        "for_me": len(decidable),
    }
    waiting_value = sum(float(row["amount"]) for row in visible
                        if row["status"] in ("Pending", "Returned"))

    queue = [row for row in visible if row["id"] in decidable]
    activity = [{
        "icon": "clock", "tone": "warn",
        "title": row["title"],
        "meta": "{} · {:,.2f} ETB · your step".format(
            row["request_type"], float(row["amount"])),
        "href": url_for("approvals.detail", request_id=row["id"]),
        "time": "Awaiting you",
    } for row in queue[:6]]
    if not activity:
        activity.append({
            "icon": "check", "tone": "ok",
            "title": "Nothing is waiting on your approval",
            "meta": "Requests routed to a module you hold appear here.",
            "time": "Clear",
        })

    returned = [{
        "icon": "warn", "tone": "bad",
        "title": row["title"],
        "meta": "{} · {:,.2f} ETB".format(row["request_type"], float(row["amount"])),
        "href": url_for("approvals.detail", request_id=row["id"]),
        "time": "Revise",
    } for row in visible if row["status"] == "Returned"][:6]

    status_rows = []
    for status in ("Pending", "Returned", "Approved", "Rejected"):
        n = sum(row["status"] == status for row in visible)
        status_rows.append({"label": status, "value": n,
                            "display": "{} request{}".format(n, "" if n == 1 else "s")})

    return render_template(
        "approval/Dashboard.html",
        requests_list=visible,
        queue=queue,
        decidable=decidable,
        counts=counts,
        waiting_value=waiting_value,
        activity=activity,
        returned=returned,
        status_rows=status_rows,
        selected_type=selected_type,
        selected_status=selected_status,
    )


@approvals_bp.route("/new", methods=["GET", "POST"])
@perm("approvals")
def new_request():
    user = current_user()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        request_type = request.form.get("request_type", "").strip()
        department = request.form.get("department", "").strip().lower()
        description = request.form.get("description", "").strip()

        try:
            amount = float(request.form.get("amount", "0") or 0)
            if amount < 0:
                raise ValueError
        except (TypeError, ValueError):
            flash("Enter a valid, non-negative amount.", "error")
            return redirect(url_for("approvals.new_request"))

        if not title or not description or request_type not in REQUEST_TYPES \
                or department not in APPROVAL_DEPARTMENTS:
            flash("Please complete all required fields correctly.", "error")
            return redirect(url_for("approvals.new_request"))

        # A leave request carries no financial value.
        if request_type == "Leave":
            amount = 0

        path = approval_path(department, amount, request_type)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db = get_db()
        result = db.execute("""
            INSERT INTO approval_requests (
                title, request_type, department, amount, description,
                created_by, created_by_name, current_approver_step,
                status, created_at, updated_at, approval_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)
        """, (
            title, request_type, department, amount, description,
            user["id"], user["full_name"], path[0], now, now, ",".join(path),
        ))
        request_id = result.lastrowid
        add_approval_action(db, request_id, "Submitted",
                            "Approval path: {}".format(
                                " → ".join(STEP_LABEL.get(step, step) for step in path)),
                            step=path[0], previous_status="Draft")
        notify_approval_step(db, request_id, path[0], title, request_type, amount,
                             exclude_user_ids=(user["id"],))
        stamp_record(db, "approval_requests", request_id, user)
        db.commit()
        log_activity("CREATE", "approvals",
                     "Approval request #{} raised: {}".format(request_id, title),
                     record_type="approval_request", record_id=request_id)
        flash("Your request has been submitted for approval.", "success")
        return redirect(url_for("approvals.detail", request_id=request_id))

    return render_template("procurm/requistions.html")


@approvals_bp.route("/<int:request_id>")
@perm("approvals", "audit")
def detail(request_id):
    db = get_db()
    user = current_user()
    row = db.execute(
        "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None:
        abort(404)

    acted = approval_acted_ids(db, user["id"])
    if not can_view_request(row, user, acted):
        abort(403)

    timeline = db.execute("""
        SELECT * FROM approval_actions
        WHERE request_id = ?
        ORDER BY action_at ASC, id ASC
    """, (request_id,)).fetchall()

    return render_template(
        "approval/detail.html",
        item=row,
        actions=timeline,
        path=request_chain(row),
        can_decide=can_decide_request(row, user),
        can_resubmit=row["status"] == "Returned" and row["created_by"] == user["id"],
    )


@approvals_bp.route("/<int:request_id>/decision", methods=["POST"])
@perm("approvals")
def decide(request_id):
    decision = request.form.get("decision", "").strip().lower()
    comments = request.form.get("comments", "").strip()
    user = current_user()

    if decision not in ("approve", "reject", "return"):
        abort(400)
    if decision in ("reject", "return") and not comments:
        flash("Please provide a comment for a rejection or a return.", "error")
        return redirect(url_for("approvals.detail", request_id=request_id))

    db = get_db()
    row = db.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        abort(404)
    if not can_decide_request(row, user):
        log_activity("DENY", "approvals",
                     "Attempt to decide request #{} without holding the step".format(
                         request_id),
                     outcome="DENIED", record_type="approval_request", record_id=request_id)
        abort(403)

    # The chain the request was opened with, not the live policy: retuning the limits
    # halfway through cannot add a desk nobody was asked to sign, or drop one that
    # already did.
    path = request_chain(row)
    step = row["current_approver_step"]
    if already_signed(db, request_id, user["id"], step):
        log_activity("DENY", "approvals",
                     "Duplicate attempt to sign request #{} at the {} stage".format(
                         request_id, STEP_LABEL.get(step, step)),
                     outcome="DENIED", record_type="approval_request", record_id=request_id)
        flash("You have already answered this stage of the request. It is with the {}."
              .format(STEP_LABEL.get(step, step)), "error")
        return redirect(url_for("approvals.detail", request_id=request_id))

    if decision == "reject":
        status, next_step, action = "Rejected", step, "Rejected"
    elif decision == "return":
        status, next_step, action = "Returned", step, "Returned"
    else:
        index = path.index(step) if step in path else len(path) - 1
        if index >= len(path) - 1:
            status, next_step, action = "Approved", path[index], "Approved"
        else:
            status, next_step, action = "Pending", path[index + 1], "Step approved"

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute("""
        UPDATE approval_requests
        SET status = ?, current_approver_step = ?, updated_at = ?
        WHERE id = ?
    """, (status, next_step, now, request_id))
    add_approval_action(db, request_id, action, comments, step=step,
                        previous_status=row["status"])
    # The desk that has answered is no longer asked; the next one is.
    retire_approval_notifications(db, request_id)
    if status == "Pending":
        notify_approval_step(db, request_id, next_step, row["title"],
                             row["request_type"], row["amount"],
                             exclude_user_ids=(user["id"], row["created_by"]))
        notify_request_creator(
            db, row, "Request advanced",
            "\"{}\" moved to the {} desk.".format(
                row["title"], STEP_LABEL.get(next_step, next_step)),
            tone="info", icon="arrow",
            suffix="advanced-{}".format(next_step))
    else:
        outcome = "\"{}\" was {} by {}.".format(
            row["title"], status.lower(), user["full_name"])
        if comments:
            outcome += " Comment: {}".format(comments)
        notify_request_creator(
            db, row, "Request {}".format(status.lower()), outcome,
            tone="ok" if status == "Approved" else "bad",
            icon="check" if status == "Approved" else "warn",
            suffix="final-{}".format(status))
    # A decision at the last desk settles the record that raised the chain, so the
    # requisition is released for a purchase order by the same action that approved
    # it and never by a second status kept on the module screen.
    sync_linked_status(db, row, status, comments, user["full_name"])
    stamp_record(db, "approval_requests", request_id, user)
    if (row["source_table"] if "source_table" in row.keys() else None) \
            == "procurement_requests" and row["source_id"]:
        stamp_record(db, "procurement_requests", row["source_id"], user)
    db.commit()
    log_activity("UPDATE", "approvals",
                 "Request #{} marked {}".format(request_id, status),
                 record_type="approval_request", record_id=request_id)
    flash("Request {}.".format(status.lower()), "success")
    return redirect(url_for("approvals.detail", request_id=request_id))


@approvals_bp.route("/<int:request_id>/resubmit", methods=["POST"])
@perm("approvals")
def resubmit(request_id):
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        abort(404)
    if row["created_by"] != user["id"] or row["status"] != "Returned":
        abort(403)

    first = request_chain(row)[0]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute("""
        UPDATE approval_requests
        SET status = 'Pending', current_approver_step = ?, updated_at = ?
        WHERE id = ?
    """, (first, now, request_id))
    add_approval_action(db, request_id, "Resubmitted", "Resubmitted by its creator.",
                        step=first, previous_status=row["status"])
    retire_approval_notifications(db, request_id)
    notify_approval_step(db, request_id, first, row["title"], row["request_type"],
                         row["amount"], exclude_user_ids=(user["id"],))
    stamp_record(db, "approval_requests", request_id, user)
    db.commit()
    log_activity("UPDATE", "approvals",
                 "Request #{} resubmitted".format(request_id),
                 record_type="approval_request", record_id=request_id)
    flash("Request resubmitted.", "success")
    return redirect(url_for("approvals.detail", request_id=request_id))


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(approvals_bp)
