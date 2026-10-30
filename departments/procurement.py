"""Suppliers, requisitions, orders and deliveries."""

from core.access import current_user
from core.access import effective_permissions
from core.access import guard
from core.access import perm
from core.config import ORDER_STATUSES
from core.config import PR_REQUESTER_ROLES
from core.config import PURCHASE_UNITS
from core.config import STEP_LABEL
from core.db import get_db
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
from services.audit import change_set
from services.audit import log_activity
from services.budget import budget_active_allocations
from services.budget import budget_allocation
from services.budget import budget_available
from services.budget import budget_commit
from services.budget import budget_committed_amount
from services.budget import budget_department_key
from services.budget import budget_problem_for
from services.budget import budget_release
from services.email import absolute_url
from services.email import send_accounts
from services.links import linked_approval
from services.links import open_approval
from services.links import request_chain
from services.notifications import deliver_notification
from services.notifications import notify_approval_step
from services.notifications import notify_permission
from services.notifications import retire_approval_notifications
from services.procurement import parse_requisition_lines
from services.procurement import procurement_granted
from services.procurement import requisition_header
from services.procurement import requisition_lines
from services.procurement import requisition_total
from services.procurement import save_requisition_lines
from services.recency import stamp as stamp_record
from services.workflow import APPROVAL_DEPARTMENTS
from services.workflow import add_approval_action
from services.workflow import approval_acted_ids
from services.workflow import approval_path
from services.workflow import can_view_request
from services.workflow import chain_trail
from services.workflow import pr_policy


procurement_bp = Blueprint("procurement", __name__, url_prefix="/procurement")


@procurement_bp.context_processor
def procurement_template_context():
    policy = pr_policy()
    return {"purchase_units": PURCHASE_UNITS,
            "requisition_departments": tuple(APPROVAL_DEPARTMENTS),
            "step_label": STEP_LABEL,
            "pr_manager_limit": policy["manager_limit"],
            "pr_director_limit": policy["director_limit"],
            "pr_route_mode": policy["pr_route_mode"]}


def requisition_number_text(value):
    """10 rather than 10.0 - the quantity typed is the quantity read back."""
    try:
        return "{:g}".format(float(value or 0))
    except (TypeError, ValueError):
        return str(value)


def requisition_department(user):
    """The department a requisition is raised for: the staff record first, then the
    account's own module role, then general."""
    employee_id = user["employee_id"] if "employee_id" in user.keys() else None
    if employee_id:
        row = get_db().execute("SELECT department FROM employees WHERE id = ?",
                               (employee_id,)).fetchone()
        if row is not None:
            value = (row["department"] or "").strip().lower()
            if value in APPROVAL_DEPARTMENTS:
                return value
    return user["role"] if user["role"] in APPROVAL_DEPARTMENTS else "general"


def requisition_suppliers(db):
    return db.execute("SELECT id, name FROM finance.procurement_suppliers ORDER BY name").fetchall()


def requisition_chain(db, row):
    return linked_approval(db, "procurement_requests", row["id"])


def requisition_editable(row, chain):
    """A requisition can be changed while it is a draft, or once a stage has sent it
    back for revision. From the moment it is with an approver the items and the
    amounts are frozen, which is what stops a figure being edited past a desk that
    already signed a different one."""
    if row["status"] in ("Draft", "Returned"):
        return True
    return (row["status"] == "Pending" and chain is not None
            and chain["status"] == "Returned")


def requisition_row_editable(row):
    """The same rule read off the listing query, which already joins the chain."""
    if row["status"] in ("Draft", "Returned"):
        return True
    return (row["status"] == "Pending"
            and row["approval_id"] is not None
            and row["approval_status"] == "Returned")


def requisition_state(row, chain):
    """The one line that tells the requester where their requisition stands."""
    if row["status"] == "Draft":
        return "Draft"
    if chain is None:
        return row["status"]
    return requisition_chain_label(chain["status"], chain["current_approver_step"])


def requisition_view_state(row):
    if row["status"] == "Draft":
        return "Draft"
    if row["approval_id"] is None:
        return row["status"]
    return requisition_chain_label(row["approval_status"], row["approval_step"])


def requisition_chain_label(status, step):
    if status == "Returned":
        return "Returned"
    if status == "Pending":
        return "With the {}".format(STEP_LABEL.get(step, step))
    return status


def requisition_visible_to(db, row, chain, user):
    """The requester, the purchasing desk and whoever the chain has asked."""
    if row["requested_by"] == user["id"]:
        return True
    if "procurement" in effective_permissions(user):
        return True
    if chain is None:
        return False
    return can_view_request(chain, user, approval_acted_ids(db, user["id"]))


def requisition_workflow_note(row, chain):
    """One sentence that reads the route for whoever opens the requisition."""
    if chain is None:
        if row["status"] == "Draft":
            return ("This requisition is still a draft. Submitting it starts the "
                    "Manager, Director or CEO route its total earns.")
        return ("No approval chain is recorded for this requisition: it was raised "
                "before purchase requests were routed through approvals.")
    if chain["status"] == "Pending":
        return ("The route was fixed when the requisition was submitted, so each "
                "desk above signs in turn whatever the policy says today. A "
                "purchase order can be placed once the last desk approves.")
    if chain["status"] == "Approved":
        return ("Every desk on this route signed. The requisition is released for "
                "a purchase order.")
    if chain["status"] == "Rejected":
        return "A desk rejected this requisition, so the route is closed."
    return ("A desk sent this requisition back for revision. Its author can revise "
            "and resubmit, and the route starts again at its first desk.")


def requisition_description(db, row):
    """What the approver reads on the chain: the justification and every line."""
    body = (row["justification"] or "").strip()
    lines = requisition_lines(db, row["id"])
    if not lines:
        return "{} | {} {} {} | {:,.2f} ETB".format(
            body, requisition_number_text(row["quantity"]), row["unit"],
            row["item_name"], float(row["estimated_cost"] or 0)).strip()
    detail = "\n".join("- {} {} @ {:,.2f} = {:,.2f} ETB".format(
        requisition_number_text(line["quantity"]), line["item_name"],
        float(line["unit_price"]), float(line["line_total"])) for line in lines)
    return "{}\n\n{}\nTotal: {:,.2f} ETB".format(
        body, detail, requisition_total(lines)).strip()[:2000]


def echo_requisition_lines(form):
    """The repeating block exactly as typed, so a refused save redraws the work."""
    names = form.getlist("line_name[]")
    quantities = form.getlist("line_quantity[]")
    units = form.getlist("line_unit[]")
    prices = form.getlist("line_price[]")
    lines = []
    for index, name in enumerate(names):
        lines.append({
            "item_name": name or "",
            "quantity": quantities[index] if index < len(quantities) else "",
            "unit": (units[index] if index < len(units) else "") or "pcs",
            "unit_price": prices[index] if index < len(prices) else "",
            "line_total": 0.0,
        })
    return lines


def capture_requisition(db, form, final):
    """Read and validate the capture form, returning (values, problem).

    The lines are what the total - and therefore the approval band - is worked out
    from, so a blank or unusable line is refused here rather than quietly becoming a
    zero that routes the request to a cheaper desk.
    """
    department = (form.get("department", "") or "").strip().lower()
    justification = (form.get("justification", "") or "").strip()
    supplier_value = (form.get("supplier_id", "") or "").strip()
    allocation_value = (form.get("budget_allocation_id", "") or "").strip()
    values = {"department": department, "justification": justification,
              "notes": (form.get("notes", "") or "").strip(),
              "supplier_id": supplier_value, "budget_allocation_id": allocation_value,
              "lines": [], "total": 0.0}
    if department not in APPROVAL_DEPARTMENTS:
        return values, "Choose the department the materials are for."
    if allocation_value:
        if not allocation_value.isdigit():
            return values, "Select a valid budget allocation."
        allocation = budget_allocation(db, int(allocation_value))
        if allocation is None:
            return values, "Select a valid budget allocation."
        if budget_department_key(allocation["department"]) != budget_department_key(department):
            return values, ("That allocation belongs to {}. Choose one for {} or leave "
                            "the field empty.".format(allocation["department"],
                                                      department.title()))
    elif final:
        live = [row for row in budget_active_allocations(db)
                if budget_department_key(row["department"])
                == budget_department_key(department)]
        if live:
            return values, ("Choose the budget allocation that funds this department "
                            "before submitting.")
    supplier_id = None
    if supplier_value:
        if not supplier_value.isdigit():
            return values, "Select a valid supplier."
        supplier_id = int(supplier_value)
        if db.execute("SELECT id FROM finance.procurement_suppliers WHERE id = ?",
                      (supplier_id,)).fetchone() is None:
            return values, "Select a valid supplier."
    try:
        lines = parse_requisition_lines(form)
    except ValueError as error:
        values["lines"] = echo_requisition_lines(form)
        return values, str(error)
    values["lines"] = lines
    values["supplier_id"] = supplier_id
    values["budget_allocation_id"] = (int(allocation_value)
                                      if allocation_value else None)
    values["total"] = requisition_total(lines)
    if final and not justification:
        return values, "Say why the items are needed before submitting."
    return values, None


def write_requisition(db, values, status, submitted_at):
    """The header row plus its lines. The legacy single-line columns are rewritten
    from the line rows, which is what keeps every older screen reading a sane item,
    quantity and total."""
    item_name, quantity, unit = requisition_header(values["lines"])
    user = current_user()
    cursor = db.execute("""
        INSERT INTO finance.procurement_requests (
            request_number, department, item_name, quantity, unit, estimated_cost,
            supplier_id, requested_by, requested_by_name, justification, notes,
            status, submitted_at, budget_allocation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        procurement_number(db, "procurement_requests", "request_number", "PRDB"),
        values["department"], item_name, quantity, unit, values["total"],
        values["supplier_id"], user["id"], user["full_name"],
        values["justification"], values["notes"], status, submitted_at,
        values.get("budget_allocation_id"),
    ))
    request_id = cursor.lastrowid
    save_requisition_lines(db, request_id, values["lines"])
    return db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                      (request_id,)).fetchone()


def revise_requisition(db, row, values, status, submitted_at):
    item_name, quantity, unit = requisition_header(values["lines"])
    db.execute("""
        UPDATE finance.procurement_requests
        SET department = ?, item_name = ?, quantity = ?, unit = ?, estimated_cost = ?,
            supplier_id = ?, justification = ?, notes = ?, status = ?, submitted_at = ?,
            budget_allocation_id = ?
        WHERE id = ?
    """, (
        values["department"], item_name, quantity, unit, values["total"],
        values["supplier_id"], values["justification"], values["notes"],
        status, submitted_at, values.get("budget_allocation_id"), row["id"],
    ))
    save_requisition_lines(db, row["id"], values["lines"])


def open_requisition_chain(db, row, total):
    """Put a submitted requisition onto the approval chain and remember the link.

    The requisition keeps no second review status of its own: the chain it opens is
    the only record of who has to sign, so a request has one path and one history
    wherever it was raised.
    """
    chain_id = open_approval(
        db, "procurement_requests", row["id"],
        "Requisition {} - {}".format(row["request_number"], row["item_name"]),
        "Purchase", row["department"], total, requisition_description(db, row))
    db.execute("UPDATE finance.procurement_requests SET approval_request_id = ? WHERE id = ?",
               (chain_id, row["id"]))
    return chain_id


def resubmit_requisition_chain(db, chain, row, total):
    """Send a returned requisition back through the chain with its revised figures.

    The history stays, but the amount, the description and the chain are re-read: a
    total pushed over a threshold has to earn the band it now falls in, so the extra
    desks sign it. A return has already stopped the chain, so nothing that was
    approved is rewritten.
    """
    path = approval_path(row["department"], total, "Purchase")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = abs(float(chain["amount"]) - float(total)) >= 0.005
    db.execute("""
        UPDATE approval_requests
        SET title = ?, department = ?, amount = ?, description = ?, status = 'Pending',
            current_approver_step = ?, approval_path = ?, updated_at = ?
        WHERE id = ?
    """, (
        "Requisition {} - {}".format(row["request_number"], row["item_name"]),
        row["department"], total, requisition_description(db, row),
        path[0], ",".join(path), now, chain["id"],
    ))
    note = ("Resubmitted with a revised total of {:,.2f} ETB. Approval path: {}".format(
        total, " -> ".join(STEP_LABEL.get(step, step) for step in path))
        if changed else "Revised and resubmitted by its requester.")
    add_approval_action(db, chain["id"], "Resubmitted", note, step=path[0],
                        previous_status=chain["status"])
    retire_approval_notifications(db, chain["id"])
    notify_approval_step(db, chain["id"], path[0], chain["title"],
                         chain["request_type"], total,
                         exclude_user_ids=(current_user()["id"],))
    return path, changed


def render_requisition_form(db, item=None, values=None, total=0.0):
    """The capture form, for a new requisition and for revising a draft or a return."""
    user = current_user()
    lines = []
    if values is None:
        if item is None:
            values = {"department": requisition_department(user), "justification": "",
                      "notes": "", "supplier_id": "", "budget_allocation_id": ""}
        else:
            values = {"department": item["department"],
                      "justification": item["justification"] or "",
                      "notes": item["notes"] or "",
                      "supplier_id": item["supplier_id"] or "",
                      "budget_allocation_id": item["budget_allocation_id"] or ""}
            lines = list(requisition_lines(db, item["id"]))
            total = requisition_total(lines)
    else:
        lines = list(values.get("lines") or [])
        values.setdefault("budget_allocation_id", "")
    if not lines:
        lines = [{"item_name": "", "quantity": "", "unit": "pcs", "unit_price": "",
                  "line_total": 0.0} for _ in range(3)]
    live_allocations = budget_active_allocations(db)
    budget_required = any(
        budget_department_key(row["department"])
        == budget_department_key(values.get("department")) for row in live_allocations)
    return render_template("Procurm/requisition_form.html", item=item, values=values,
                           lines=lines, total=float(total),
                           suppliers=requisition_suppliers(db),
                           budget_allocations=live_allocations,
                           budget_required=budget_required)


def reserve_requisition_budget(db, row, total):
    """Reserve the requisition's total once it enters the approval chain.

    A revision can move the total or the allocation, so the reservation follows
    the row that was saved. A requisition with no allocation (raised before any
    money was allocated for its department) is left unreserved rather than blocked.
    """
    allocation_id = row["budget_allocation_id"] if "budget_allocation_id" in row.keys() else None
    if not allocation_id:
        budget_release(db, "procurement_requests", row["id"])
        return None
    problem = budget_problem_for(db, allocation_id, total,
                                 "procurement_requests", row["id"])
    if problem:
        return problem
    budget_commit(db, allocation_id, "procurement_requests", row["id"], total,
                  "Requisition {}".format(row["request_number"]))
    return None


def requisition_rows(db):
    """Every requisition with its line count, its total and the stage of its chain."""
    return db.execute("""
        SELECT r.*, s.name AS supplier_name,
               a.id AS approval_id, a.status AS approval_status,
               a.current_approver_step AS approval_step,
               (SELECT COUNT(*) FROM finance.procurement_request_items AS i
                 WHERE i.request_id = r.id) AS line_count,
               (SELECT COALESCE(SUM(i.line_total), 0) FROM finance.procurement_request_items AS i
                 WHERE i.request_id = r.id) AS lines_total
        FROM finance.procurement_requests AS r
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = r.supplier_id
        LEFT JOIN approval_requests AS a ON a.id = r.approval_request_id
        ORDER BY CASE r.status WHEN 'Draft' THEN 0 WHEN 'Pending' THEN 1 ELSE 2 END,
                 r.id DESC
        LIMIT 200
    """).fetchall()


@procurement_bp.route("/requisitions")
@guard(roles=PR_REQUESTER_ROLES)
def requisitions():
    db = get_db()
    user = current_user()
    rows = requisition_rows(db)
    mine = [row for row in rows if row["requested_by"] == user["id"]]
    states = {row["id"]: requisition_view_state(row) for row in rows}
    counts = {
        "total": len(mine),
        "drafts": sum(row["status"] == "Draft" for row in mine),
        "waiting": sum(row["status"] == "Pending" and states[row["id"]] != "Returned"
                       for row in mine),
        "returned": sum(states[row["id"]] == "Returned" for row in mine),
        "approved": sum(row["status"] == "Approved" for row in mine),
        "value": sum(float(row["estimated_cost"] or 0) for row in mine
                     if row["status"] in ("Pending", "Approved")),
    }
    return render_template("Procurm/requisitions.html", mine=mine,
                           desk=rows if "procurement" in effective_permissions(user)
                           else [], counts=counts, states=states,
                           editable={row["id"]: requisition_row_editable(row)
                                     for row in rows})


@procurement_bp.route("/requisitions/new", methods=["GET", "POST"])
@guard(roles=PR_REQUESTER_ROLES)
def requisition_new():
    db = get_db()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action not in ("draft", "submit"):
            flash("Choose Save draft or Submit for approval.", "error")
            return render_requisition_form(db)
        values, problem = capture_requisition(db, request.form, action == "submit")
        if problem:
            flash(problem, "error")
            return render_requisition_form(db, values=values, total=values["total"])
        if action == "submit" and values.get("budget_allocation_id"):
            problem = budget_problem_for(db, values["budget_allocation_id"],
                                         values["total"])
            if problem:
                flash(problem, "error")
                return render_requisition_form(db, values=values, total=values["total"])

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = write_requisition(db, values,
                                "Pending" if action == "submit" else "Draft",
                                now if action == "submit" else None)
        chain_id = None
        if action == "submit":
            chain_id = open_requisition_chain(db, row, values["total"])
            reserve_requisition_budget(db, row, values["total"])
        stamp_record(db, "procurement_requests", row["id"])
        db.commit()
        log_activity("CREATE", "procurement",
                     "Requisition {} {} for {:,.2f} ETB".format(
                         row["request_number"],
                         "submitted" if chain_id else "saved as a draft",
                         values["total"]),
                     record_type="purchase_request", record_id=row["request_number"],
                     details={"department": values["department"],
                              "lines": len(values["lines"]),
                              "approval_request_id": chain_id})
        if chain_id is None:
            flash("Requisition {} saved as a draft. Nothing is sent until you "
                  "submit it.".format(row["request_number"]), "success")
        else:
            flash("Requisition {} submitted for approval.".format(
                row["request_number"]), "success")
        return redirect(url_for("procurement.requisitions"))

    return render_requisition_form(db)


@procurement_bp.route("/requisitions/<int:request_id>")
@guard(roles=PR_REQUESTER_ROLES)
def requisition_detail(request_id):
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                     (request_id,)).fetchone()
    if row is None:
        abort(404)
    chain = requisition_chain(db, row)
    acted = approval_acted_ids(db, user["id"])
    # The requester, the purchasing desk and whoever the chain has asked may read
    # the requisition and its route; nobody else learns what another department
    # is buying.
    if not requisition_visible_to(db, row, chain, user):
        log_activity("DENY", "procurement",
                     "Attempt to open requisition #{} raised by {}".format(
                         request_id, row["requested_by_name"]),
                     outcome="DENIED", record_type="purchase_request",
                     record_id=request_id)
        abort(403)

    lines = requisition_lines(db, request_id)
    total = requisition_total(lines) if lines else float(row["estimated_cost"] or 0)
    supplier = (db.execute("SELECT name FROM finance.procurement_suppliers WHERE id = ?",
                           (row["supplier_id"],)).fetchone()
                if row["supplier_id"] else None)
    allocation = (budget_allocation(db, row["budget_allocation_id"])
                  if row["budget_allocation_id"] else None)
    orders = db.execute("""
        SELECT o.*, s.name AS supplier_name
        FROM finance.procurement_orders AS o
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        WHERE o.request_id = ?
        ORDER BY o.id DESC
    """, (request_id,)).fetchall()
    return render_template(
        "Procurm/requisition_detail.html",
        request_row=row, lines=lines, total=total,
        supplier_name=supplier["name"] if supplier is not None else None,
        allocation=allocation,
        allocation_available=(budget_available(db, allocation["id"])
                              if allocation is not None else None),
        orders=orders, chain=chain,
        trail=chain_trail(db, chain, request_chain(chain)) if chain else [],
        state=requisition_state(row, chain),
        workflow_note=requisition_workflow_note(row, chain),
        can_change=(row["requested_by"] == user["id"]
                    and requisition_editable(row, chain)),
        can_open_order=bool({"procurement", "finance", "audit"}
                            & effective_permissions(user)),
        can_open_approval=(chain is not None
                           and bool({"approvals", "audit"}
                                    & effective_permissions(user))
                           and can_view_request(chain, user, acted)))


@procurement_bp.route("/requisitions/<int:request_id>/edit", methods=["GET", "POST"])
@guard(roles=PR_REQUESTER_ROLES)
def requisition_edit(request_id):
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                     (request_id,)).fetchone()
    if row is None:
        abort(404)
    if row["requested_by"] != user["id"]:
        log_activity("DENY", "procurement",
                     "Attempt to change requisition #{} raised by {}".format(
                         request_id, row["requested_by_name"]),
                     outcome="DENIED", record_type="purchase_request",
                     record_id=request_id)
        abort(403)

    chain = requisition_chain(db, row)
    if not requisition_editable(row, chain):
        flash("Requisition {} is {} and can no longer be changed here. Only the "
              "approver whose stage it has reached can move it.".format(
                  row["request_number"], requisition_state(row, chain)), "error")
        return redirect(url_for("procurement.requisitions"))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action not in ("draft", "submit"):
            flash("Choose Save draft or Submit for approval.", "error")
            return render_requisition_form(db, item=row)
        values, problem = capture_requisition(db, request.form, action == "submit")
        if problem:
            flash(problem, "error")
            return render_requisition_form(db, item=row, values=values,
                                           total=values["total"])
        if action == "submit" and values.get("budget_allocation_id"):
            problem = budget_problem_for(db, values["budget_allocation_id"],
                                         values["total"], "procurement_requests",
                                         request_id)
            if problem:
                flash(problem, "error")
                return render_requisition_form(db, item=row, values=values,
                                               total=values["total"])

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        previous_values, updated_values = change_set(row, {
            "department": values["department"],
            "justification": values["justification"],
            "notes": values["notes"],
            "estimated_cost": values["total"],
            "status": "Pending" if action == "submit" else "Draft",
        })
        revise_requisition(db, row, values,
                           "Pending" if action == "submit" else "Draft",
                           row["submitted_at"] or (now if action == "submit" else None))
        revised = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                             (request_id,)).fetchone()
        if action == "submit":
            if chain is None:
                open_requisition_chain(db, revised, values["total"])
            else:
                resubmit_requisition_chain(db, chain, revised, values["total"])
            reserve_requisition_budget(db, revised, values["total"])
        else:
            budget_release(db, "procurement_requests", request_id)
        stamp_record(db, "procurement_requests", request_id, user)
        db.commit()
        log_activity("UPDATE", "procurement",
                     "Requisition {} {}".format(
                         row["request_number"],
                         "resubmitted" if action == "submit" else "revised as a draft"),
                     record_type="purchase_request", record_id=row["request_number"],
                     details={"previous_amount": float(row["estimated_cost"] or 0),
                              "amount": values["total"],
                              "lines": len(values["lines"])},
                     previous=previous_values, updated=updated_values)
        flash("Requisition {} submitted for approval.".format(row["request_number"])
              if action == "submit" else
              "Requisition {} kept as a draft.".format(row["request_number"]), "success")
        return redirect(url_for("procurement.requisitions"))

    return render_requisition_form(db, item=row)


@procurement_bp.route("/requisitions/<int:request_id>/submit", methods=["POST"])
@guard(roles=PR_REQUESTER_ROLES)
def requisition_submit(request_id):
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                     (request_id,)).fetchone()
    if row is None:
        abort(404)
    if row["requested_by"] != user["id"]:
        log_activity("DENY", "procurement",
                     "Attempt to submit requisition #{} raised by {}".format(
                         request_id, row["requested_by_name"]),
                     outcome="DENIED", record_type="purchase_request",
                     record_id=request_id)
        abort(403)
    if row["status"] != "Draft":
        flash("That requisition has already been submitted.", "error")
        return redirect(url_for("procurement.requisitions"))
    if not (row["justification"] or "").strip():
        flash("Say why the items are needed before submitting.", "error")
        return redirect(url_for("procurement.requisition_edit", request_id=request_id))

    total = requisition_total(requisition_lines(db, request_id))
    allocation_id = (row["budget_allocation_id"]
                     if "budget_allocation_id" in row.keys() else None)
    live = [item for item in budget_active_allocations(db)
            if budget_department_key(item["department"])
            == budget_department_key(row["department"])]
    if not allocation_id and live:
        flash("Choose the budget allocation that funds {} before submitting."
              .format(row["department"].title()), "error")
        return redirect(url_for("procurement.requisition_edit", request_id=request_id))
    if allocation_id:
        problem = budget_problem_for(db, allocation_id, total,
                                     "procurement_requests", request_id)
        if problem:
            flash(problem, "error")
            return redirect(url_for("procurement.requisition_edit", request_id=request_id))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    moved = db.execute("UPDATE finance.procurement_requests SET status = 'Pending',"
                       " submitted_at = ? WHERE id = ? AND status = 'Draft'",
                       (now, request_id))
    if moved.rowcount == 0:
        db.rollback()
        flash("That requisition has already been submitted.", "error")
        return redirect(url_for("procurement.requisitions"))
    chain_id = open_requisition_chain(db, row, total)
    reserve_requisition_budget(db, row, total)
    stamp_record(db, "procurement_requests", request_id, user)
    db.commit()
    log_activity("UPDATE", "procurement",
                 "Requisition {} submitted for {:,.2f} ETB".format(
                     row["request_number"], total),
                 record_type="purchase_request", record_id=row["request_number"],
                 details={"approval_request_id": chain_id})
    flash("Requisition {} submitted for approval.".format(row["request_number"]),
          "success")
    return redirect(url_for("procurement.requisitions"))


@procurement_bp.route("/requisitions/<int:request_id>/discard", methods=["POST"])
@guard(roles=PR_REQUESTER_ROLES)
def requisition_discard(request_id):
    """Throw away a draft nobody has seen yet. A submitted requisition is never
    deleted here - it stays on the record with its decision and its reason."""
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                     (request_id,)).fetchone()
    if row is None:
        abort(404)
    if row["requested_by"] != user["id"]:
        log_activity("DENY", "procurement",
                     "Attempt to discard requisition #{} raised by {}".format(
                         request_id, row["requested_by_name"]),
                     outcome="DENIED", record_type="purchase_request",
                     record_id=request_id)
        abort(403)
    if row["status"] != "Draft":
        flash("A submitted requisition is kept on the record. The decision belongs to "
              "the approver it is with.", "error")
        return redirect(url_for("procurement.requisitions"))

    budget_release(db, "procurement_requests", request_id)
    db.execute("DELETE FROM finance.procurement_request_items WHERE request_id = ?",
               (request_id,))
    db.execute("DELETE FROM finance.procurement_requests WHERE id = ?", (request_id,))
    db.commit()
    log_activity("DELETE", "procurement",
                 "Draft requisition {} discarded".format(row["request_number"]),
                 record_type="purchase_request", record_id=row["request_number"])
    flash("Draft requisition {} discarded.".format(row["request_number"]), "success")
    return redirect(url_for("procurement.requisitions"))


@procurement_bp.route("/")
@perm("procurement", "audit")
def dashboard():
    db = get_db()

    stats = {
        "suppliers": db.execute(
            "SELECT COUNT(*) AS n FROM finance.procurement_suppliers").fetchone()["n"],
        "requests": db.execute(
            "SELECT COUNT(*) AS n FROM finance.procurement_requests").fetchone()["n"],
        "pending": db.execute(
            "SELECT COUNT(*) AS n FROM finance.procurement_requests WHERE status = 'Pending'"
        ).fetchone()["n"],
        "drafts": db.execute(
            "SELECT COUNT(*) AS n FROM finance.procurement_requests WHERE status = 'Draft'"
        ).fetchone()["n"],
        "orders": db.execute("SELECT COUNT(*) AS n FROM finance.procurement_orders").fetchone()["n"],
        "open_orders": db.execute("""
            SELECT COUNT(*) AS n FROM finance.procurement_orders
            WHERE status IN ('Ordered', 'Partially Received')
        """).fetchone()["n"],
        "spend": db.execute("""
            SELECT COALESCE(SUM(total_cost), 0) AS total FROM finance.procurement_orders
            WHERE status IN ('Ordered', 'Partially Received', 'Received')
        """).fetchone()["total"],
    }

    suppliers = db.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM finance.procurement_orders o
                 WHERE o.supplier_id = s.id) AS order_count,
               (SELECT COALESCE(SUM(o.total_cost), 0) FROM finance.procurement_orders o
                 WHERE o.supplier_id = s.id AND o.status <> 'Cancelled') AS ordered_value
        FROM finance.procurement_suppliers AS s
        ORDER BY s.id DESC
    """).fetchall()

    requests_list = db.execute("""
        SELECT r.*, s.name AS supplier_name,
               a.id AS approval_id, a.status AS approval_status,
               a.current_approver_step AS approval_step
        FROM finance.procurement_requests AS r
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = r.supplier_id
        LEFT JOIN approval_requests AS a ON a.id = r.approval_request_id
        ORDER BY CASE r.status WHEN 'Draft' THEN 0 WHEN 'Pending' THEN 1 ELSE 2 END,
                 r.id DESC
        LIMIT 40
    """).fetchall()

    orders = db.execute("""
        SELECT o.*, s.name AS supplier_name, a.allocation_name
        FROM finance.procurement_orders AS o
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        LEFT JOIN budget_allocations AS a ON a.id = o.budget_allocation_id
        ORDER BY CASE o.status WHEN 'Cancelled' THEN 1 ELSE 0 END, o.id DESC
        LIMIT 40
    """).fetchall()

    allocations = []
    for row in budget_active_allocations(db):
        allocations.append({
            "id": row["id"],
            "name": row["allocation_name"],
            "department": row["department"],
            "plan": row["plan_name"],
            "available": budget_available(db, row["id"]),
        })

    approved_requests = db.execute("""
        SELECT r.id, r.request_number, r.item_name, r.department,
               r.estimated_cost, r.budget_allocation_id
        FROM finance.procurement_requests AS r
        WHERE r.status = 'Approved'
          AND NOT EXISTS (
              SELECT 1 FROM finance.procurement_orders AS o
              WHERE o.request_id = r.id AND o.status <> 'Cancelled')
        ORDER BY r.id DESC
    """).fetchall()

    by_supplier = db.execute("""
        SELECT s.name AS label, COALESCE(SUM(o.total_cost), 0) AS value
        FROM finance.procurement_orders AS o
        JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        WHERE o.status <> 'Cancelled'
        GROUP BY s.id
        ORDER BY value DESC
        LIMIT 6
    """).fetchall()

    supplier_rows = [{
        "label": row["label"],
        "value": float(row["value"]),
        "display": "{:,.2f} ETB".format(float(row["value"])),
    } for row in by_supplier]

    pending = [row for row in requests_list if row["status"] == "Pending"]
    activity = [{
        "icon": "file", "tone": "warn",
        "title": "{} · {}".format(row["request_number"], row["item_name"]),
        "meta": "{} {} · estimated {:,.2f} ETB".format(
            row["quantity"], row["unit"], float(row["estimated_cost"])),
        "time": "Awaiting",
    } for row in pending[:6]]
    if not activity:
        activity.append({
            "icon": "check", "tone": "ok",
            "title": "No purchase request is waiting",
            "meta": "Approved requests are the ones that become purchase orders.",
            "time": "Clear",
        })

    return render_template(
        "Procurm/dashboard.html",
        stats=stats,
        suppliers=suppliers,
        requests_list=requests_list,
        orders=orders,
        by_supplier=by_supplier,
        supplier_rows=supplier_rows,
        activity=activity,
        units=PURCHASE_UNITS,
        order_statuses=ORDER_STATUSES,
        budget_allocations=allocations,
        approved_requests=approved_requests,
        can_manage=procurement_granted(),
    )


def procurement_number(db, table, column, prefix):
    """The next reference in the series: PRDB00001, PODB00001, ..."""
    highest = 0
    rows = db.execute("SELECT \"{}\" FROM {}".format(column, table)).fetchall()
    for (reference,) in rows:
        reference = reference or ""
        suffix = reference[len(prefix):]
        if reference.startswith(prefix) and suffix.isdigit():
            highest = max(highest, int(suffix))
    return "{}{:05d}".format(prefix, highest + 1)


def procurement_amounts(form, cost_field):
    quantity = float(form.get("quantity", "0"))
    cost = float(form.get(cost_field, "0") or 0)
    if quantity <= 0 or cost < 0:
        raise ValueError
    return quantity, cost


@procurement_bp.route("/suppliers/add", methods=["POST"])
@perm("procurement")
def add_supplier():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Supplier name is required.", "error")
        return redirect(url_for("procurement.dashboard"))

    db = get_db()
    supplier = db.execute("""
        INSERT INTO finance.procurement_suppliers
            (name, contact_person, phone, email, address)
        VALUES (?, ?, ?, ?, ?)
    """, (
        name,
        request.form.get("contact_person", "").strip(),
        request.form.get("phone", "").strip(),
        request.form.get("email", "").strip(),
        request.form.get("address", "").strip(),
    ))
    stamp_record(db, "procurement_suppliers", supplier.lastrowid)
    db.commit()
    log_activity("CREATE", "procurement", "Supplier added: {}".format(name),
                 record_type="supplier")
    flash("Supplier added successfully.", "success")
    return redirect(url_for("procurement.dashboard"))


@procurement_bp.route("/requests/add", methods=["POST"])
@perm("procurement")
def add_request():
    item_name = request.form.get("item_name", "").strip()
    unit = (request.form.get("unit", "pcs").strip() or "pcs")

    try:
        quantity, estimated_cost = procurement_amounts(request.form, "estimated_cost")
    except (TypeError, ValueError):
        flash("Enter a valid quantity and estimated cost.", "error")
        return redirect(url_for("procurement.dashboard"))

    if not item_name:
        flash("Material or item name is required.", "error")
        return redirect(url_for("procurement.dashboard"))

    supplier_value = request.form.get("supplier_id", "").strip()
    supplier_id = int(supplier_value) if supplier_value else None

    db = get_db()
    if supplier_id is not None and not db.execute(
        "SELECT id FROM finance.procurement_suppliers WHERE id = ?", (supplier_id,)
    ).fetchone():
        flash("Please select a valid supplier.", "error")
        return redirect(url_for("procurement.dashboard"))

    user = current_user()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    department = requisition_department(user)
    justification = request.form.get("justification", "").strip()
    if not justification:
        justification = request.form.get("notes", "").strip()
    allocation_value = request.form.get("budget_allocation_id", "").strip()
    allocation_id = int(allocation_value) if allocation_value.isdigit() else None
    live = [item for item in budget_active_allocations(db)
            if budget_department_key(item["department"])
            == budget_department_key(department)]
    if allocation_id is None and live:
        flash("Choose the budget allocation that funds this request.", "error")
        return redirect(url_for("procurement.dashboard"))
    if allocation_id is not None:
        problem = budget_problem_for(db, allocation_id, estimated_cost)
        if problem:
            flash(problem, "error")
            return redirect(url_for("procurement.dashboard"))
    values = {
        "department": department, "justification": justification,
        "notes": request.form.get("notes", "").strip(), "supplier_id": supplier_id,
        "budget_allocation_id": allocation_id,
        "total": round(estimated_cost, 2),
        "lines": [{"item_name": item_name, "quantity": quantity, "unit": unit,
                   "unit_price": (round(estimated_cost / quantity, 2) if quantity
                                  else 0.0),
                   "line_total": round(estimated_cost, 2)}],
    }
    row = write_requisition(db, values, "Pending", now)
    request_number = row["request_number"]
    chain_id = open_requisition_chain(db, row, values["total"])
    reserve_requisition_budget(db, row, values["total"])
    stamp_record(db, "procurement_requests", row["id"])
    db.commit()
    log_activity("CREATE", "procurement",
                 "Purchase request {} raised for {}".format(request_number, item_name),
                 record_type="purchase_request", record_id=request_number,
                 details={"department": department,
                          "approval_request_id": chain_id})
    flash("Purchase request submitted for approval.", "success")
    return redirect(url_for("procurement.dashboard"))


@procurement_bp.route("/requests/<int:request_id>/status", methods=["POST"])
@perm("procurement")
def update_request_status(request_id):
    status = request.form.get("status", "")
    if status not in ("Approved", "Rejected"):
        abort(400)

    db = get_db()
    row = db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                     (request_id,)).fetchone()
    if row is None:
        flash("Request not found.", "error")
        return redirect(url_for("procurement.dashboard"))
    chain = requisition_chain(db, row)
    if chain is not None:
        log_activity("DENY", "procurement",
                     "Attempt to decide requisition #{} outside its approval chain"
                     .format(request_id),
                     outcome="DENIED", record_type="purchase_request",
                     record_id=request_id)
        flash("Requisition {} is on the approval chain at the {} stage, so it is "
              "approved there and not here.".format(
                  row["request_number"], STEP_LABEL.get(
                      chain["current_approver_step"],
                      chain["current_approver_step"])), "error")
        return redirect(url_for("procurement.dashboard"))
    result = db.execute("""
        UPDATE finance.procurement_requests SET status = ?
        WHERE id = ? AND status = 'Pending'
    """, (status, request_id))
    if result.rowcount == 0:
        db.rollback()
        flash("Request not found or already processed.", "error")
    else:
        deliver_notification(
            db, row["requested_by"],
            "Purchase request {}".format(status.lower()),
            "Request #{} was {}.".format(request_id, status.lower()),
            url_for("procurement.requisitions"), category="procurement",
            icon="check" if status == "Approved" else "warn",
            tone="ok" if status == "Approved" else "bad")
        stamp_record(db, "procurement_requests", request_id)
        db.commit()
        log_activity("UPDATE", "procurement",
                     "Purchase request #{} {}".format(request_id, status.lower()),
                     record_type="purchase_request", record_id=request_id,
                     previous={"status": row["status"]},
                     updated={"status": status})
        flash("Request {}.".format(status.lower()), "success")
    return redirect(url_for("procurement.dashboard"))


@procurement_bp.route("/orders/add", methods=["POST"])
@perm("procurement")
def add_order():
    item_name = request.form.get("item_name", "").strip()
    unit = (request.form.get("unit", "pcs").strip() or "pcs")
    try:
        supplier_id = int(request.form.get("supplier_id", "0"))
        quantity, unit_price = procurement_amounts(request.form, "unit_price")
    except (TypeError, ValueError):
        flash("Enter valid order details.", "error")
        return redirect(url_for("procurement.dashboard"))

    if not item_name:
        flash("Item name is required.", "error")
        return redirect(url_for("procurement.dashboard"))

    db = get_db()
    if not db.execute(
        "SELECT id FROM finance.procurement_suppliers WHERE id = ?", (supplier_id,)
    ).fetchone():
        flash("Select a valid supplier before creating an order.", "error")
        return redirect(url_for("procurement.dashboard"))

    request_value = request.form.get("request_id", "").strip()
    request_id = int(request_value) if request_value.isdigit() else None
    linked_request = None
    if request_id is not None:
        linked_request = db.execute(
            "SELECT * FROM finance.procurement_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if linked_request is None or linked_request["status"] != "Approved":
            flash("Choose an approved requisition, or place a direct order.", "error")
            return redirect(url_for("procurement.dashboard"))
        already = db.execute("""
            SELECT order_number FROM finance.procurement_orders
            WHERE request_id = ? AND status <> 'Cancelled'
        """, (request_id,)).fetchone()
        if already is not None:
            flash("Requisition {} already has purchase order {}."
                  .format(linked_request["request_number"], already["order_number"]),
                  "error")
            return redirect(url_for("procurement.dashboard"))

    allocation_value = request.form.get("budget_allocation_id", "").strip()
    allocation_id = int(allocation_value) if allocation_value.isdigit() else None
    if allocation_id is None and linked_request is not None:
        allocation_id = (linked_request["budget_allocation_id"]
                         if "budget_allocation_id" in linked_request.keys() else None)
    live = budget_active_allocations(db)
    if allocation_id is None and live:
        flash("Choose the budget allocation that funds this purchase order.", "error")
        return redirect(url_for("procurement.dashboard"))
    total_cost = round(quantity * unit_price, 2)
    if allocation_id is not None:
        problem = budget_problem_for(
            db, allocation_id, total_cost,
            "procurement_requests" if request_id is not None else None,
            request_id)
        if problem:
            flash(problem, "error")
            return redirect(url_for("procurement.dashboard"))

    user = current_user()
    order_number = procurement_number(db, "procurement_orders", "order_number", "PODB")
    cursor = db.execute("""
        INSERT INTO finance.procurement_orders (
            order_number, supplier_id, item_name, quantity, unit, unit_price,
            total_cost, expected_date, status, created_by, created_by_name,
            request_id, budget_allocation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Ordered', ?, ?, ?, ?)
    """, (
        order_number, supplier_id, item_name, quantity, unit, unit_price,
        total_cost,
        request.form.get("expected_date", "").strip() or None,
        user["id"], user["full_name"], request_id, allocation_id,
    ))
    if request_id is not None:
        budget_release(db, "procurement_requests", request_id, status="Converted")
    if allocation_id is not None:
        budget_commit(db, allocation_id, "procurement_orders", cursor.lastrowid,
                      total_cost, "Purchase order {}".format(order_number))
    stamp_record(db, "procurement_orders", cursor.lastrowid, user)
    db.commit()
    order_row = db.execute("""
        SELECT o.*, s.name AS supplier_name
        FROM finance.procurement_orders AS o
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        WHERE o.id = ?
    """, (cursor.lastrowid,)).fetchone()
    if order_row is not None:
        details = ["Item: {}".format(item_name),
                   "Quantity: {:g} {}".format(quantity, unit),
                   "Total: {:,.2f} ETB".format(total_cost)]
        if linked_request is not None:
            details.append("Requisition: {}".format(
                linked_request["request_number"]))
        email_order_event(
            db, order_row, "placed",
            "Purchase order {} placed".format(order_number),
            "Purchase order {} was issued to {}.".format(
                order_number, order_row["supplier_name"] or "a supplier"),
            details)
    log_activity("CREATE", "procurement",
                 "Purchase order {} placed for {}".format(order_number, item_name),
                 record_type="purchase_order", record_id=order_number,
                 details={"request_id": request_id,
                          "budget_allocation_id": allocation_id})
    flash("Purchase order created successfully.", "success")
    return redirect(url_for("procurement.dashboard"))


@procurement_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@perm("procurement")
def update_order_status(order_id):
    status = request.form.get("status", "")
    if status not in ORDER_STATUSES:
        abort(400)

    db = get_db()
    order = db.execute("SELECT * FROM finance.procurement_orders WHERE id = ?",
                       (order_id,)).fetchone()
    if order is None:
        flash("Order not found.", "error")
        return redirect(url_for("procurement.dashboard"))
    allocation_id = (order["budget_allocation_id"]
                     if "budget_allocation_id" in order.keys() else None)
    if status == "Cancelled":
        budget_release(db, "procurement_orders", order_id)
    elif order["status"] == "Cancelled" and allocation_id:
        problem = budget_problem_for(db, allocation_id, order["total_cost"],
                                     "procurement_orders", order_id)
        if problem:
            flash(problem, "error")
            return redirect(url_for("procurement.dashboard"))
        budget_commit(db, allocation_id, "procurement_orders", order_id,
                      order["total_cost"],
                      "Purchase order {}".format(order["order_number"]))
    result = db.execute(
        "UPDATE finance.procurement_orders SET status = ? WHERE id = ?", (status, order_id)
    )
    if result.rowcount == 0:
        db.rollback()
        flash("Order not found.", "error")
    else:
        stamp_record(db, "procurement_orders", order_id)
        db.commit()
        if status != order["status"]:
            fresh = db.execute("SELECT * FROM finance.procurement_orders"
                               " WHERE id = ?", (order_id,)).fetchone()
            if fresh is not None:
                email_order_event(
                    db, fresh, "status-{}".format(status),
                    "Purchase order {} is now {}".format(
                        fresh["order_number"], status),
                    "The status of {} changed from {} to {}.".format(
                        fresh["order_number"], order["status"], status),
                    ["Item: {}".format(fresh["item_name"]),
                     "Total: {:,.2f} ETB".format(float(fresh["total_cost"] or 0))])
        log_activity("UPDATE", "procurement",
                     "Order #{} marked {}".format(order_id, status),
                     record_type="purchase_order", record_id=order_id,
                     previous={"status": order["status"]},
                     updated={"status": status})
        flash("Order status updated.", "success")
    return redirect(url_for("procurement.dashboard"))


def order_workflow_note(order, chain):
    """Who signed for this order: its requisition's route, or its issuer."""
    if order["request_id"] is None:
        return ("This order was placed directly by {} without a requisition, so "
                "no approval route applies to it.".format(
                    order["created_by_name"] or "its issuer"))
    if chain is None:
        return ("The requisition behind this order carries no approval chain on "
                "record, so the sign-off cannot be shown.")
    return ("Requisition {} cleared the route above before this order was placed. "
            "The order itself carries no further signatures.".format(
                order["request_number"]))


def po_audience(db, order):
    """The purchasing desk, the requester and the hands that cleared the chain."""
    account_ids = {order["created_by"]} if order["created_by"] else set()
    if order["request_id"]:
        request_row = db.execute(
            "SELECT requested_by FROM finance.procurement_requests WHERE id = ?",
            (order["request_id"],)).fetchone()
        if request_row is not None and request_row["requested_by"]:
            account_ids.add(request_row["requested_by"])
        chain = linked_approval(db, "procurement_requests", order["request_id"])
        if chain is not None:
            account_ids |= {row["actor_id"] for row in db.execute(
                "SELECT DISTINCT actor_id FROM approval_actions"
                " WHERE request_id = ? AND actor_id IS NOT NULL",
                (chain["id"],)).fetchall()}
    return [account for account in
            db.execute("SELECT * FROM users WHERE status = 'Active'").fetchall()
            if account["id"] in account_ids
            or "procurement" in effective_permissions(account)]


def email_order_event(db, order, event, subject, intro, details=()):
    """Mail an order update to the purchasing desk and everyone it concerns."""
    send_accounts(
        db, po_audience(db, order), subject, intro, details,
        event_key="po-{}-{}".format(order["id"], event),
        category="procurement", exclude_ids=(current_user()["id"],),
        action_url=absolute_url(url_for("procurement.order_detail",
                                        order_id=order["id"], _external=True)),
        action_label="Open the purchase order",
        related_type="purchase_order", related_id=order["id"])


def finance_accounts(db):
    """Active accounts that sit at the finance desk."""
    return [account for account in
            db.execute("SELECT * FROM users WHERE status = 'Active'").fetchall()
            if "finance" in effective_permissions(account)]


@procurement_bp.route("/orders/<int:order_id>")
@perm("procurement", "finance", "audit")
def order_detail(order_id):
    db = get_db()
    order = db.execute("""
        SELECT o.*, s.name AS supplier_name, s.contact_person, s.phone AS supplier_phone,
               a.allocation_name, a.department AS allocation_department,
               a.currency AS allocation_currency,
               p.plan_name, r.request_number
        FROM finance.procurement_orders AS o
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        LEFT JOIN budget_allocations AS a ON a.id = o.budget_allocation_id
        LEFT JOIN budget_plans AS p ON p.id = a.plan_id
        LEFT JOIN finance.procurement_requests AS r ON r.id = o.request_id
        WHERE o.id = ?
    """, (order_id,)).fetchone()
    if order is None:
        abort(404)
    deliveries = db.execute("""
        SELECT * FROM finance.procurement_deliveries WHERE order_id = ?
        ORDER BY delivery_date DESC, id DESC
    """, (order_id,)).fetchall()
    received = round(sum(float(row["quantity"]) for row in deliveries), 2)
    invoices = db.execute("""
        SELECT i.*,
               COALESCE((SELECT SUM(p.amount_etb) FROM finance.finance_payments p
                          WHERE p.invoice_id = i.id), 0) AS paid_etb
        FROM finance.finance_invoices AS i
        WHERE i.procurement_order_id = ?
        ORDER BY i.id DESC
    """, (order_id,)).fetchall()
    available = (budget_available(db, order["budget_allocation_id"])
                 if order["budget_allocation_id"] else None)
    committed = (budget_committed_amount(db, order["budget_allocation_id"])
                 if order["budget_allocation_id"] else None)
    chain = (linked_approval(db, "procurement_requests", order["request_id"])
             if order["request_id"] else None)
    user = current_user()
    linked_request = (db.execute("SELECT * FROM finance.procurement_requests WHERE id = ?",
                                 (order["request_id"],)).fetchone()
                      if order["request_id"] else None)
    can_open_requisition = (linked_request is not None
                            and requisition_visible_to(db, linked_request, chain, user))
    return render_template(
        "Procurm/order_detail.html", order=order, deliveries=deliveries,
        received=received, remaining=max(float(order["quantity"]) - received, 0),
        invoices=invoices, available=available, committed=committed,
        chain=chain,
        trail=chain_trail(db, chain, request_chain(chain)) if chain else [],
        workflow_note=order_workflow_note(order, chain),
        can_open_requisition=can_open_requisition,
        can_invoice=("finance" in effective_permissions(user)),
        can_deliver=("procurement" in effective_permissions(user)),
        today=date.today().isoformat())


@procurement_bp.route("/orders/<int:order_id>/deliveries", methods=["POST"])
@perm("procurement")
def add_delivery(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM finance.procurement_orders WHERE id = ?",
                       (order_id,)).fetchone()
    if order is None:
        abort(404)
    if order["status"] == "Cancelled":
        flash("A cancelled order cannot receive deliveries.", "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    try:
        quantity = float(request.form.get("quantity", ""))
        if quantity <= 0:
            raise ValueError
    except (TypeError, ValueError):
        flash("Enter a delivered quantity greater than zero.", "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    received = db.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS total FROM finance.procurement_deliveries"
        " WHERE order_id = ?", (order_id,)).fetchone()["total"]
    if float(received) + quantity > float(order["quantity"]) + 0.005:
        flash("That delivery exceeds the {:.2f} {} ordered.".format(
            float(order["quantity"]), order["unit"]), "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    delivery_date = request.form.get("delivery_date", "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(delivery_date)
    except ValueError:
        flash("Enter a valid delivery date.", "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    user = current_user()
    cursor = db.execute("""
        INSERT INTO finance.procurement_deliveries (
            order_id, delivery_date, quantity, notes, received_by, received_by_name
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (order_id, delivery_date, quantity,
          request.form.get("notes", "").strip()[:500],
          user["id"], user["full_name"]))
    total_received = float(received) + quantity
    new_status = ("Received" if total_received >= float(order["quantity"]) - 0.005
                  else "Partially Received")
    db.execute("UPDATE finance.procurement_orders SET status = ? WHERE id = ?",
               (new_status, order_id))
    stamp_record(db, "procurement_deliveries", cursor.lastrowid, user)
    stamp_record(db, "procurement_orders", order_id, user)
    db.commit()
    log_activity("CREATE", "procurement",
                 "Delivery of {:g} {} recorded against {}".format(
                     quantity, order["unit"], order["order_number"]),
                 record_type="purchase_delivery", record_id=cursor.lastrowid,
                 details={"order_id": order_id, "status": new_status})
    flash("Delivery recorded; order is now {}.".format(new_status.lower()), "success")
    return redirect(url_for("procurement.order_detail", order_id=order_id))


@procurement_bp.route("/orders/<int:order_id>/invoice", methods=["POST"])
@perm("finance")
def invoice_order(order_id):
    db = get_db()
    order = db.execute("""
        SELECT o.*, s.name AS supplier_name, a.allocation_name, a.currency AS allocation_currency
        FROM finance.procurement_orders AS o
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        LEFT JOIN budget_allocations AS a ON a.id = o.budget_allocation_id
        WHERE o.id = ?
    """, (order_id,)).fetchone()
    if order is None:
        abort(404)
    if order["status"] == "Cancelled":
        flash("A cancelled order cannot be invoiced.", "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    existing = db.execute(
        "SELECT invoice_number FROM finance.finance_invoices WHERE procurement_order_id = ?",
        (order_id,)).fetchone()
    if existing is not None:
        flash("Invoice {} is already recorded for that order.".format(
            existing["invoice_number"]), "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    allocation_id = order["budget_allocation_id"]
    amount = round(float(order["total_cost"] or 0), 2)
    if amount <= 0:
        flash("An order with no value cannot be invoiced.", "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    if allocation_id:
        problem = budget_problem_for(db, allocation_id, amount,
                                     "procurement_orders", order_id)
        if problem:
            flash(problem, "error")
            return redirect(url_for("procurement.order_detail", order_id=order_id))
    invoice_number = (request.form.get("invoice_number", "").strip()
                      or "SINV-{}".format(order["order_number"]))
    if db.execute("SELECT 1 FROM finance.finance_invoices WHERE invoice_number = ?",
                  (invoice_number,)).fetchone():
        flash("Invoice number {} is already in use.".format(invoice_number), "error")
        return redirect(url_for("procurement.order_detail", order_id=order_id))
    user = current_user()
    invoice = db.execute("""
        INSERT INTO finance.finance_invoices (
            invoice_number, invoice_type, party_name, party_contact,
            issue_date, due_date, currency, total_amount, exchange_rate,
            total_etb, notes, status, created_by, budget_allocation_id,
            procurement_order_id
        ) VALUES (?, 'supplier', ?, ?, ?, ?, 'ETB', ?, 1, ?, ?, 'Unpaid', ?, ?, ?)
    """, (invoice_number, order["supplier_name"] or "", "",
          date.today().isoformat(),
          request.form.get("due_date", "").strip() or None,
          str(amount), str(amount),
          "Supplier invoice against {}".format(order["order_number"]),
          str(user["id"]), allocation_id, order_id))
    if allocation_id:
        expenditure = db.execute("""
            INSERT INTO budget_expenditures (
                allocation_id, description, amount, currency, reference,
                recorded_by, recorded_by_name, procurement_order_id,
                finance_invoice_id
            ) VALUES (?, ?, ?, 'ETB', ?, ?, ?, ?, ?)
        """, (allocation_id,
              "Supplier invoice {} for {}".format(invoice_number,
                                                  order["item_name"]),
              str(amount), invoice_number, user["id"], user["full_name"],
              order_id, invoice.lastrowid))
        budget_release(db, "procurement_orders", order_id, status="Converted")
        transaction = db.execute("""
            INSERT INTO finance.finance_transactions (
                transaction_date, transaction_type, category, description,
                party_name, currency, amount, exchange_rate, amount_etb,
                payment_method, reference, created_by, budget_allocation_id,
                budget_expenditure_id, procurement_order_id
            ) VALUES (?, 'expense', 'Procurement', ?, ?, 'ETB', ?, 1, ?,
                      'Other', ?, ?, ?, ?, ?)
        """, (date.today().isoformat(), "Supplier invoice against {}".format(
            order["order_number"]), order["supplier_name"] or "",
            str(amount), str(amount), invoice_number, str(user["id"]),
            allocation_id, expenditure.lastrowid, order_id))
        db.execute(
            "UPDATE budget_expenditures SET finance_transaction_id = ?"
            " WHERE id = ?", (transaction.lastrowid, expenditure.lastrowid))
    db.execute("UPDATE finance.procurement_orders SET invoiced_at = ? WHERE id = ?",
               (datetime.now(timezone.utc).isoformat(timespec="seconds"), order_id))
    stamp_record(db, "procurement_orders", order_id, user)
    stamp_record(db, "finance_invoices", invoice.lastrowid, user)
    if order["created_by"]:
        deliver_notification(
            db, order["created_by"], "Supplier invoice recorded",
            "{} has invoice {} for {:,.2f} ETB.".format(
                order["order_number"], invoice_number, amount),
            url_for("procurement.order_detail", order_id=order_id),
            category="procurement", icon="file", tone="info")
    notify_permission(
        db, "finance", "Supplier invoice ready for payment",
        "{} · {:,.2f} ETB · recorded against {}.".format(
            invoice_number, amount, order["order_number"]),
        url_for("finance.invoices"), category="finance", icon="cash", tone="warn",
        exclude_user_ids=(user["id"],))
    db.commit()
    order_row = db.execute("""
        SELECT o.*, s.name AS supplier_name
        FROM finance.procurement_orders AS o
        LEFT JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
        WHERE o.id = ?
    """, (order_id,)).fetchone()
    if order_row is not None:
        details = ["Invoice: {}".format(invoice_number),
                   "Amount: {:,.2f} ETB".format(amount),
                   "Supplier: {}".format(order_row["supplier_name"] or "the supplier")]
        email_order_event(
            db, order_row, "invoiced-{}".format(invoice_number),
            "Invoice recorded against {}".format(order_row["order_number"]),
            "Supplier invoice {} was recorded for {}.".format(
                invoice_number, order_row["order_number"]),
            details)
        # The order audience already heard about this invoice; only the finance
        # hands outside it get the payment notice, once.
        order_heads = {account["id"] for account in po_audience(db, order_row)}
        send_accounts(
            db, [account for account in finance_accounts(db)
                 if account["id"] not in order_heads],
            "Supplier invoice ready for payment",
            "Invoice {} for {:,.2f} ETB is recorded against {} and can be paid.".format(
                invoice_number, amount, order_row["order_number"]),
            details[:2],
            event_key="po-{}-invoice-finance".format(order_id),
            category="finance", exclude_ids=(user["id"],),
            action_url=absolute_url(url_for("finance.invoices", _external=True)),
            action_label="Open invoices",
            related_type="purchase_order", related_id=order_id)
    log_activity("CREATE", "finance",
                 "Supplier invoice {} recorded for order {} ({:,.2f} ETB)".format(
                     invoice_number, order["order_number"], amount),
                 record_type="finance_invoice", record_id=invoice.lastrowid,
                 details={"order_id": order_id, "budget_allocation_id": allocation_id})
    flash("Supplier invoice {} recorded against the order.".format(invoice_number),
          "success")
    return redirect(url_for("procurement.order_detail", order_id=order_id))


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(procurement_bp)
