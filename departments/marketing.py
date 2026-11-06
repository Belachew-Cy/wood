"""The marketing desk: plans, campaigns, digital pushes, leads, research, feedback.

Six registers, one module. A plan is raised by the desk that holds the plan right
and signed by the desk that holds the approval right, so the two links in the rail
open the same list with a different filter rather than a second screen. A campaign
hangs from a plan, a digital push hangs from a campaign, and a lead is the person a
channel brought in. A customer is not stored here at all: it is read from the
invoices Finance has already issued, so the two desks can never name different
customers.

Nothing on these screens trusts a hidden button. Every view asks the server what
this account may reach before it reads a row, every form runs through the
validators in ``services/marketing.py``, and every write stamps who changed the
record and writes one audit line with the fields that actually moved - so the
direct URL, the forged POST and the sidebar link are refused in the same breath.
"""

from core.access import current_user
from core.access import has_permission
from core.access import perm
from core.db import get_db
from datetime import date
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
from services.marketing import CAMPAIGN_STATUSES
from services.marketing import CHANNELS
from services.marketing import DIGITAL_PLATFORMS
from services.marketing import DIGITAL_STATUSES
from services.marketing import FEEDBACK_STATUSES
from services.marketing import FEEDBACK_TYPES
from services.marketing import LEAD_SOURCES
from services.marketing import LEAD_STATUSES
from services.marketing import MARKETING_CURRENCIES
from services.marketing import PLAN_STATUSES
from services.marketing import RESEARCH_TYPES
from services.marketing import account_name
from services.marketing import campaign_options
from services.marketing import campaign_performance
from services.marketing import campaign_rows
from services.marketing import clean_account
from services.marketing import clean_amount
from services.marketing import clean_choice
from services.marketing import clean_date
from services.marketing import clean_email
from services.marketing import clean_period
from services.marketing import clean_phone
from services.marketing import clean_rating
from services.marketing import clean_status
from services.marketing import clean_text
from services.marketing import customer_rows
from services.marketing import digital_rows
from services.marketing import feedback_rows
from services.marketing import insert_campaign
from services.marketing import insert_digital
from services.marketing import insert_feedback
from services.marketing import insert_lead
from services.marketing import insert_plan
from services.marketing import insert_research
from services.marketing import lead_rows
from services.marketing import marketing_activity
from services.marketing import marketing_channel_report
from services.marketing import marketing_feedback_report
from services.marketing import marketing_pipeline
from services.marketing import marketing_row
from services.marketing import marketing_summary
from services.marketing import plan_options
from services.marketing import plan_rows
from services.marketing import refuse_duplicate
from services.marketing import refuse_duplicate_lead
from services.marketing import research_rows
from services.marketing import set_status
from services.marketing import update_campaign
from services.marketing import update_digital
from services.marketing import update_feedback
from services.marketing import update_lead
from services.marketing import update_plan
from services.marketing import update_research
from services.notifications import deliver_notification
from services.notifications import notify_right_holders
from services.recency import stamp as stamp_record

marketing_bp = Blueprint("marketing", __name__, url_prefix="/marketing")


# The columns an export hands over, per desk. Kept beside the routes so a new
# field cannot join a list and stay out of the file.
# The columns an export hands over, per desk, as (field, header) pairs so the
# house writer in services/audit.py can build the file. Kept beside the routes so
# a new field cannot join a list and stay out of the export.
EXPORT_COLUMNS = {
    "plans": (("id", "ID"), ("title", "Plan title"), ("status", "Status"),
              ("start_date", "Start"), ("end_date", "End"), ("budget", "Budget"),
              ("currency", "Currency"), ("campaign_count", "Campaigns"),
              ("created_by_name", "Raised by"),
              ("updated_at", "Last modified")),
    "campaigns": (("id", "ID"), ("name", "Campaign"),
                  ("plan_title", "Marketing plan"), ("channel", "Channel"),
                  ("target_audience", "Target audience"), ("status", "Status"),
                  ("start_date", "Start"), ("end_date", "End"),
                  ("budget", "Budget"), ("currency", "Currency"),
                  ("digital_count", "Digital pushes"),
                  ("created_by_name", "Raised by"),
                  ("updated_at", "Last modified")),
    "leads": (("id", "ID"), ("name", "Lead"), ("phone", "Phone"),
              ("email", "Email"), ("product_interest", "Product interest"),
              ("source", "Source"), ("status", "Status"),
              ("assigned_to_name", "Assigned to"),
              ("follow_up_date", "Follow-up"),
              ("created_by_name", "Registered by"),
              ("updated_at", "Last modified")),
    "research": (("id", "ID"), ("title", "Title"),
                 ("research_type", "Study type"),
                 ("market_segment", "Market segment"),
                 ("research_date", "Research date"),
                 ("prepared_by_name", "Prepared by"),
                 ("updated_at", "Last modified")),
    "digital": (("id", "ID"), ("name", "Push"), ("campaign_name", "Campaign"),
                 ("platform", "Platform"), ("status", "Status"),
                 ("start_date", "Start"), ("end_date", "End"),
                 ("budget", "Budget"), ("currency", "Currency"),
                 ("created_by_name", "Raised by"),
                 ("updated_at", "Last modified")),
    "feedback": (("id", "ID"), ("customer_name", "Customer"),
                 ("contact", "Contact"), ("feedback_type", "Type"),
                 ("rating", "Rating"), ("status", "Status"),
                 ("assigned_name", "Assigned to"), ("created_at", "Recorded"),
                 ("updated_at", "Last modified")),
    "customers": (("name", "Customer"), ("contact", "Contact"),
                  ("invoice_count", "Invoices"),
                  ("total_amount", "Total invoiced"), ("total_etb", "Total ETB"),
                  ("first_order", "First order"), ("last_order", "Latest order"),
                  ("open_invoices", "Open invoices")),
}


# The right that owns the rows behind each export. The reports desk may read the
# figures of every register, but it does not inherit the register itself: a
# signed-off CSV of leads still has to be raised by a seat that holds the lead
# right, so the export cannot become a side door around a desk boundary.
EXPORT_DESK_RIGHT = {
    "plans": "marketing_plans",
    "campaigns": "marketing_campaigns",
    "digital": "marketing_campaigns",
    "leads": "marketing_leads",
    "customers": "marketing_leads",
    "research": "marketing_research",
    "feedback": "marketing_feedback",
}


@marketing_bp.context_processor
def marketing_template_context():
    """The vocabularies every marketing form draws its selects from."""
    return {
        "plan_statuses": PLAN_STATUSES,
        "campaign_statuses": CAMPAIGN_STATUSES,
        "digital_statuses": DIGITAL_STATUSES,
        "lead_statuses": LEAD_STATUSES,
        "feedback_statuses": FEEDBACK_STATUSES,
        "feedback_types": FEEDBACK_TYPES,
        "research_types": RESEARCH_TYPES,
        "channels": CHANNELS,
        "lead_sources": LEAD_SOURCES,
        "digital_platforms": DIGITAL_PLATFORMS,
        "currencies": MARKETING_CURRENCIES,
    }


def marketing_accounts(db):
    """The seats a lead or a complaint may be handed to."""
    return db.execute(
        "SELECT id, full_name, username FROM users WHERE status = 'Active'"
        " ORDER BY full_name, username").fetchall()


def guarded_row(db, table, record_id):
    """One row or a 404, with the label the screen already uses."""
    row = marketing_row(db, table, record_id)
    if row is None:
        abort(404)
    return row


def refused_write(error):
    """One answer for a bad form, whichever desk raised it."""
    flash(str(error), "error")


# ------------------------------------------------------------------- overview
@marketing_bp.route("/")
@perm("marketing_dashboard")
def dashboard():
    """The desk at a glance: what is open, what is overdue, what was last filed."""
    db = get_db()
    summary = marketing_summary(db)
    return render_template(
        "marketing/dashboard.html",
        summary=summary,
        pipeline=marketing_pipeline(db),
        activity=marketing_activity(db),
        channels=marketing_channel_report(db),
        can_see=lambda key: has_permission(key),
        today=date.today().isoformat(),
    )


# --------------------------------------------------------------------- plans
# The list answers for both rights: the desk that raises a plan and the desk that
# signs it read the same rows, and the buttons each one is shown follow the right
# the server already checked.
@marketing_bp.route("/plans")
@perm("marketing_plans", "marketing_approval")
def plans():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in PLAN_STATUSES:
        status = ""
    search = (request.args.get("search") or "").strip()
    return render_template(
        "marketing/plans.html",
        plans=plan_rows(db, status or None, search or None),
        status=status, search=search,
        can_manage=has_permission("marketing_plans"),
        can_approve=has_permission("marketing_approval"))


def plan_values(form, db, exclude=None):
    values = {
        "title": clean_text(form.get("title"), "Plan title", 150, required=True),
        "description": clean_text(form.get("description"), "Description", 2000),
    }
    values["start_date"], values["end_date"] = clean_period(
        form.get("start_date"), form.get("end_date"))
    values["budget"] = clean_amount(form.get("budget"), "Plan budget")
    values["currency"] = clean_choice(form.get("currency"), "Currency",
                                      MARKETING_CURRENCIES, default="ETB")
    refuse_duplicate(db, "marketing_plans", "title", values["title"], exclude,
                     "marketing plan")
    return values


@marketing_bp.route("/plans/create", methods=["GET", "POST"])
@perm("marketing_plans")
def create_plan():
    db = get_db()
    if request.method == "POST":
        try:
            values = plan_values(request.form, db)
            values["status"] = "Draft"
            user = current_user()
            plan_id = insert_plan(db, values, user)
            stamp_record(db, "marketing_plans", plan_id, user)
            db.commit()
            log_activity("CREATE", "marketing",
                         "Marketing plan '{}' created ({} {})".format(
                             values["title"], values["budget"],
                             values["currency"]),
                         record_type="marketing_plan", record_id=plan_id,
                         updated=values)
            flash("Marketing plan created as a draft. It authorises nothing"
                  " until the approval desk signs it.", "success")
            return redirect(url_for("marketing.plans"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/plan_form.html", plan=None,
                           can_approve=False)


@marketing_bp.route("/plans/<int:plan_id>", methods=["GET", "POST"])
@perm("marketing_plans", "marketing_approval")
def edit_plan(plan_id):
    db = get_db()
    plan = guarded_row(db, "marketing_plans", plan_id)
    if request.method == "POST":
        # The page opens for either right - the desk that raises a plan and the
        # desk that signs it read the same record - but only the first may
        # rewrite it. A forged POST from a signing seat stops here.
        if not has_permission("marketing_plans"):
            flash("The approval desk reads a plan; it does not rewrite one.",
                  "error")
            return redirect(url_for("marketing.edit_plan", plan_id=plan_id))
        try:
            values = plan_values(request.form, db, exclude=plan_id)
            # A signed or closed plan is history: the right to revise a plan does
            # not reach one that has already been answered.
            if plan["status"] not in ("Draft", "Pending"):
                raise ValueError(
                    "A {} plan can no longer be edited.".format(
                        plan["status"].lower()))
            values["status"] = plan["status"]
            user = current_user()
            update_plan(db, plan_id, values)
            stamp_record(db, "marketing_plans", plan_id, user)
            db.commit()
            previous, updated = change_set(plan, values)
            log_activity("UPDATE", "marketing",
                         "Marketing plan '{}' revised".format(values["title"]),
                         record_type="marketing_plan", record_id=plan_id,
                         previous=previous, updated=updated)
            flash("Marketing plan updated.", "success")
            return redirect(url_for("marketing.plans"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/plan_form.html", plan=plan,
                           can_approve=has_permission("marketing_approval"))


@marketing_bp.route("/plans/<int:plan_id>/decision", methods=["POST"])
@perm("marketing_approval")
def decide_plan(plan_id):
    """Approve or refuse a plan, tell the desk that raised it, and log the move."""
    db = get_db()
    plan = guarded_row(db, "marketing_plans", plan_id)
    if plan["status"] not in ("Draft", "Pending"):
        flash("That plan has already been answered.", "info")
        return redirect(url_for("marketing.plans"))
    decision = (request.form.get("decision") or "").strip()
    if decision not in ("Approved", "Rejected"):
        flash("Choose whether to approve or refuse the plan.", "error")
        return redirect(url_for("marketing.plans"))
    comment = clean_text(request.form.get("comment"), "Comment", 500)
    user = current_user()
    set_status(db, "marketing_plans", plan_id, decision)
    stamp_record(db, "marketing_plans", plan_id, user)
    deliver_notification(
        db, plan["created_by"],
        "Marketing plan {}{}".format("approved" if decision == "Approved"
                                     else "refused", ""),
        "'{}' now sits at {}. {}".format(plan["title"], decision.lower(),
                                         comment).strip(),
        href=url_for("marketing.plans"), category="approval", icon="check",
        tone="ok" if decision == "Approved" else "bad",
        dedupe_key="marketing-plan-decision-{}".format(plan_id))
    db.commit()
    log_activity("APPROVE" if decision == "Approved" else "REJECT", "marketing",
                 "Marketing plan '{}' {}".format(plan["title"],
                                                  decision.lower()),
                 record_type="marketing_plan", record_id=plan_id,
                 previous={"status": plan["status"]},
                 updated={"status": decision, "comment": comment})
    flash("Plan {}.".format(decision.lower()), "success")
    return redirect(url_for("marketing.plans"))


@marketing_bp.route("/plans/<int:plan_id>/submit", methods=["POST"])
@perm("marketing_plans")
def submit_plan(plan_id):
    """Send a draft up for a decision, and wake the approval desk."""
    db = get_db()
    plan = guarded_row(db, "marketing_plans", plan_id)
    if plan["status"] != "Draft":
        flash("Only a draft is submitted for a decision.", "info")
        return redirect(url_for("marketing.plans"))
    set_status(db, "marketing_plans", plan_id, "Pending")
    stamp_record(db, "marketing_plans", plan_id, current_user())
    notify_right_holders(
        db, "marketing_approval",
        "Marketing plan awaiting approval",
        "'{}' ({} {}) was raised by {}.".format(
            plan["title"], plan["budget"], plan["currency"],
            current_user()["full_name"]),
        href=url_for("marketing.plans", status="Pending"),
        category="approval", icon="check", tone="info",
        exclude_user_ids=(current_user()["id"],),
        dedupe_prefix="marketing-plan-pending-{}".format(plan_id))
    db.commit()
    log_activity("UPDATE", "marketing",
                 "Marketing plan '{}' submitted for approval".format(
                     plan["title"]),
                 record_type="marketing_plan", record_id=plan_id,
                 previous={"status": plan["status"]},
                 updated={"status": "Pending"})
    flash("Plan sent to the approval desk.", "success")
    return redirect(url_for("marketing.plans"))


@marketing_bp.route("/plans/<int:plan_id>/close", methods=["POST"])
@perm("marketing_plans", "marketing_approval")
def close_plan(plan_id):
    db = get_db()
    plan = guarded_row(db, "marketing_plans", plan_id)
    if plan["status"] == "Closed":
        flash("That plan is already closed.", "info")
        return redirect(url_for("marketing.plans"))
    set_status(db, "marketing_plans", plan_id, "Closed")
    stamp_record(db, "marketing_plans", plan_id, current_user())
    db.commit()
    log_activity("CLOSE", "marketing",
                 "Marketing plan '{}' closed".format(plan["title"]),
                 record_type="marketing_plan", record_id=plan_id,
                 previous={"status": plan["status"]},
                 updated={"status": "Closed"})
    flash("Plan closed.", "success")
    return redirect(url_for("marketing.plans"))


# ------------------------------------------------------------------ campaigns
@marketing_bp.route("/campaigns")
@perm("marketing_campaigns")
def campaigns():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in CAMPAIGN_STATUSES:
        status = ""
    search = (request.args.get("search") or "").strip()
    plan = (request.args.get("plan") or "").strip()
    return render_template(
        "marketing/campaigns.html",
        campaigns=campaign_rows(db, status or None,
                                plan if plan.isdigit() else None,
                                search or None),
        plans=plan_options(db), status=status, search=search,
        plan_id=int(plan) if plan.isdigit() else "")


def campaign_values(form, db, exclude=None):
    values = {
        "name": clean_text(form.get("name"), "Campaign name", 150,
                           required=True),
        "description": clean_text(form.get("description"), "Description", 2000),
        "channel": clean_choice(form.get("channel"), "Channel", CHANNELS),
        "target_audience": clean_text(form.get("target_audience"),
                                      "Target audience", 200),
    }
    plan_id = clean_account(form.get("plan_id"), "Marketing plan")
    if plan_id is not None and marketing_row(db, "marketing_plans",
                                             plan_id) is None:
        raise ValueError("Choose a marketing plan that is on the register.")
    values["plan_id"] = plan_id
    values["start_date"], values["end_date"] = clean_period(
        form.get("start_date"), form.get("end_date"),
        start_required=False, end_required=False)
    values["budget"] = clean_amount(form.get("budget"), "Campaign budget")
    values["currency"] = clean_choice(form.get("currency"), "Currency",
                                      MARKETING_CURRENCIES, default="ETB")
    refuse_duplicate(db, "marketing_campaigns", "name", values["name"], exclude,
                     "campaign")
    return values


@marketing_bp.route("/campaigns/create", methods=["GET", "POST"])
@perm("marketing_campaigns")
def create_campaign():
    db = get_db()
    if request.method == "POST":
        try:
            values = campaign_values(request.form, db)
            values["status"] = "Planned"
            user = current_user()
            campaign_id = insert_campaign(db, values, user)
            stamp_record(db, "marketing_campaigns", campaign_id, user)
            db.commit()
            log_activity("CREATE", "marketing",
                         "Campaign '{}' registered on the {} channel".format(
                             values["name"], values["channel"] or "open"),
                         record_type="marketing_campaign",
                         record_id=campaign_id, updated=values)
            flash("Campaign created.", "success")
            return redirect(url_for("marketing.campaigns"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/campaign_form.html", campaign=None,
                           plans=plan_options(db))


@marketing_bp.route("/campaigns/<int:campaign_id>", methods=["GET", "POST"])
@perm("marketing_campaigns")
def edit_campaign(campaign_id):
    db = get_db()
    campaign = guarded_row(db, "marketing_campaigns", campaign_id)
    if request.method == "POST":
        try:
            values = campaign_values(request.form, db, exclude=campaign_id)
            values["status"] = clean_status(request.form.get("status"),
                                            "Status", CAMPAIGN_STATUSES)
            user = current_user()
            update_campaign(db, campaign_id, values)
            stamp_record(db, "marketing_campaigns", campaign_id, user)
            db.commit()
            previous, updated = change_set(campaign, values)
            log_activity("UPDATE", "marketing",
                         "Campaign '{}' revised".format(values["name"]),
                         record_type="marketing_campaign",
                         record_id=campaign_id, previous=previous,
                         updated=updated)
            flash("Campaign updated.", "success")
            return redirect(url_for("marketing.campaigns"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/campaign_form.html", campaign=campaign,
                           plans=plan_options(db))


# -------------------------------------------------------------------- leads
@marketing_bp.route("/leads")
@perm("marketing_leads")
def leads():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in LEAD_STATUSES:
        status = ""
    follow_up = (request.args.get("follow_up") or "").strip()
    if follow_up not in ("overdue", "today"):
        follow_up = ""
    search = (request.args.get("search") or "").strip()
    return render_template(
        "marketing/leads.html",
        leads=lead_rows(db, status or None, search or None, follow_up or None),
        status=status, search=search, follow_up=follow_up,
        accounts=marketing_accounts(db))


def lead_values(form, db, exclude=None, status=None):
    values = {
        "name": clean_text(form.get("name"), "Lead name", 150, required=True),
        "phone": clean_phone(form.get("phone"), "Phone"),
        "email": clean_email(form.get("email"), "Email"),
        "product_interest": clean_text(form.get("product_interest"),
                                       "Product interest", 150),
        "source": clean_choice(form.get("source"), "Source", LEAD_SOURCES),
        "notes": clean_text(form.get("notes"), "Notes", 2000),
        "follow_up_date": clean_date(form.get("follow_up_date"),
                                     "Follow-up date"),
    }
    if not values["phone"] and not values["email"]:
        raise ValueError("A lead needs a phone number or an email address so"
                         " the desk can reach them.")
    assigned = clean_account(form.get("assigned_to"), "Assigned account")
    values["assigned_to"] = assigned
    values["assigned_to_name"] = account_name(db, assigned)
    values["status"] = clean_status(status, "Status", LEAD_STATUSES)
    refuse_duplicate_lead(db, values["phone"], values["email"], exclude)
    return values


@marketing_bp.route("/leads/create", methods=["GET", "POST"])
@perm("marketing_leads")
def create_lead():
    db = get_db()
    if request.method == "POST":
        try:
            values = lead_values(request.form, db,
                                 status=request.form.get("status") or "New")
            user = current_user()
            lead_id = insert_lead(db, values, user)
            stamp_record(db, "marketing_leads", lead_id, user)
            db.commit()
            log_activity("CREATE", "marketing",
                         "Lead '{}' registered from {}".format(
                             values["name"], values["source"] or "a walk-in"),
                         record_type="marketing_lead", record_id=lead_id,
                         updated=values)
            flash("Lead registered.", "success")
            return redirect(url_for("marketing.leads"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/lead_form.html", lead=None,
                           accounts=marketing_accounts(db))


@marketing_bp.route("/leads/<int:lead_id>", methods=["GET", "POST"])
@perm("marketing_leads")
def edit_lead(lead_id):
    db = get_db()
    lead = guarded_row(db, "marketing_leads", lead_id)
    if request.method == "POST":
        try:
            values = lead_values(request.form, db, exclude=lead_id,
                                 status=request.form.get("status"))
            user = current_user()
            update_lead(db, lead_id, values)
            stamp_record(db, "marketing_leads", lead_id, user)
            db.commit()
            previous, updated = change_set(lead, values)
            log_activity("UPDATE", "marketing",
                         "Lead '{}' revised".format(values["name"]),
                         record_type="marketing_lead", record_id=lead_id,
                         previous=previous, updated=updated)
            flash("Lead updated.", "success")
            return redirect(url_for("marketing.leads"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/lead_form.html", lead=lead,
                           accounts=marketing_accounts(db))


@marketing_bp.route("/leads/<int:lead_id>/status", methods=["POST"])
@perm("marketing_leads")
def mark_lead(lead_id):
    """The one-button moves on the pipeline: contacted, won, lost."""
    db = get_db()
    lead = guarded_row(db, "marketing_leads", lead_id)
    status = (request.form.get("status") or "").strip()
    try:
        status = clean_status(status, "Status", LEAD_STATUSES)
    except ValueError:
        flash("Choose one of the listed lead statuses.", "error")
        return redirect(url_for("marketing.leads"))
    if status == lead["status"]:
        return redirect(url_for("marketing.leads"))
    set_status(db, "marketing_leads", lead_id, status)
    stamp_record(db, "marketing_leads", lead_id, current_user())
    db.commit()
    log_activity("UPDATE", "marketing",
                 "Lead '{}' moved to {}".format(lead["name"], status),
                 record_type="marketing_lead", record_id=lead_id,
                 previous={"status": lead["status"]},
                 updated={"status": status})
    flash("Lead marked {}.".format(status.lower()), "success")
    return redirect(request.referrer or url_for("marketing.leads"))


# -------------------------------------------------------------------- research
@marketing_bp.route("/research")
@perm("marketing_research")
def research():
    db = get_db()
    search = (request.args.get("search") or "").strip()
    return render_template("marketing/research.html",
                           research_items=research_rows(db, search or None),
                           search=search)


def research_values(form, db, exclude=None):
    values = {
        "title": clean_text(form.get("title"), "Title", 150, required=True),
        "research_type": clean_choice(form.get("research_type"),
                                      "Research type", RESEARCH_TYPES),
        "market_segment": clean_text(form.get("market_segment"),
                                     "Market segment", 150),
        "findings": clean_text(form.get("findings"), "Findings", 4000),
        "research_date": clean_date(form.get("research_date"),
                                    "Research date", required=True),
    }
    refuse_duplicate(db, "market_research", "title", values["title"], exclude,
                     "research record")
    return values


@marketing_bp.route("/research/create", methods=["GET", "POST"])
@perm("marketing_research")
def create_research():
    db = get_db()
    if request.method == "POST":
        try:
            values = research_values(request.form, db)
            user = current_user()
            research_id = insert_research(db, values, user)
            stamp_record(db, "market_research", research_id, user)
            db.commit()
            log_activity("CREATE", "marketing",
                         "Research '{}' recorded ({})".format(
                             values["title"], values["research_type"]
                             or "unspecified study"),
                         record_type="market_research", record_id=research_id,
                         updated=values)
            flash("Research record created.", "success")
            return redirect(url_for("marketing.research"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/research_form.html", item=None)


@marketing_bp.route("/research/<int:research_id>", methods=["GET", "POST"])
@perm("marketing_research")
def edit_research(research_id):
    db = get_db()
    item = guarded_row(db, "market_research", research_id)
    if request.method == "POST":
        try:
            values = research_values(request.form, db, exclude=research_id)
            user = current_user()
            update_research(db, research_id, values)
            stamp_record(db, "market_research", research_id, user)
            db.commit()
            previous, updated = change_set(item, values)
            log_activity("UPDATE", "marketing",
                         "Research '{}' revised".format(values["title"]),
                         record_type="market_research", record_id=research_id,
                         previous=previous, updated=updated)
            flash("Research record updated.", "success")
            return redirect(url_for("marketing.research"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/research_form.html", item=item)


# -------------------------------------------------------------- digital pushes
@marketing_bp.route("/digital")
@perm("marketing_campaigns")
def digital():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in DIGITAL_STATUSES:
        status = ""
    search = (request.args.get("search") or "").strip()
    return render_template(
        "marketing/digital_marketing.html",
        digital_campaigns=digital_rows(db, status or None, search or None),
        campaigns=campaign_options(db), status=status, search=search)


def digital_values(form, db, exclude=None):
    values = {
        "name": clean_text(form.get("name"), "Push name", 150, required=True),
        "platform": clean_choice(form.get("platform"), "Platform",
                                 DIGITAL_PLATFORMS),
        "content": clean_text(form.get("content"), "Content", 4000),
    }
    campaign_id = clean_account(form.get("campaign_id"), "Campaign")
    if campaign_id is not None and marketing_row(db, "marketing_campaigns",
                                                 campaign_id) is None:
        raise ValueError("Choose a campaign that is on the register.")
    values["campaign_id"] = campaign_id
    values["start_date"], values["end_date"] = clean_period(
        form.get("start_date"), form.get("end_date"),
        start_required=False, end_required=False)
    values["budget"] = clean_amount(form.get("budget"), "Budget")
    values["currency"] = clean_choice(form.get("currency"), "Currency",
                                      MARKETING_CURRENCIES, default="ETB")
    refuse_duplicate(db, "digital_campaigns", "name", values["name"], exclude,
                     "digital campaign")
    return values


@marketing_bp.route("/digital/create", methods=["GET", "POST"])
@perm("marketing_campaigns")
def create_digital():
    db = get_db()
    if request.method == "POST":
        try:
            values = digital_values(request.form, db)
            values["status"] = "Planned"
            user = current_user()
            digital_id = insert_digital(db, values, user)
            stamp_record(db, "digital_campaigns", digital_id, user)
            db.commit()
            log_activity("CREATE", "marketing",
                         "Digital push '{}' planned on {}".format(
                             values["name"], values["platform"] or "an open"
                             " channel"),
                         record_type="digital_campaign", record_id=digital_id,
                         updated=values)
            flash("Digital campaign created.", "success")
            return redirect(url_for("marketing.digital"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/digital_form.html", item=None,
                           campaigns=campaign_options(db))


@marketing_bp.route("/digital/<int:digital_id>", methods=["GET", "POST"])
@perm("marketing_campaigns")
def edit_digital(digital_id):
    db = get_db()
    item = guarded_row(db, "digital_campaigns", digital_id)
    if request.method == "POST":
        try:
            values = digital_values(request.form, db, exclude=digital_id)
            values["status"] = clean_status(request.form.get("status"),
                                            "Status", DIGITAL_STATUSES)
            user = current_user()
            update_digital(db, digital_id, values)
            stamp_record(db, "digital_campaigns", digital_id, user)
            db.commit()
            previous, updated = change_set(item, values)
            log_activity("UPDATE", "marketing",
                         "Digital push '{}' revised".format(values["name"]),
                         record_type="digital_campaign", record_id=digital_id,
                         previous=previous, updated=updated)
            flash("Digital campaign updated.", "success")
            return redirect(url_for("marketing.digital"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/digital_form.html", item=item,
                           campaigns=campaign_options(db))


# ------------------------------------------------------------------ feedback
@marketing_bp.route("/feedback")
@perm("marketing_feedback")
def feedback():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in FEEDBACK_STATUSES:
        status = ""
    search = (request.args.get("search") or "").strip()
    return render_template(
        "marketing/feedback.html",
        feedback_items=feedback_rows(db, status or None, search or None),
        status=status, search=search, accounts=marketing_accounts(db))


def feedback_values(form, db, exclude=None, status=None, resolution=""):
    values = {
        "customer_name": clean_text(form.get("customer_name"),
                                    "Customer name", 150),
        "contact": clean_text(form.get("contact"), "Contact", 150),
        "feedback_type": clean_choice(form.get("feedback_type"),
                                      "Feedback type", FEEDBACK_TYPES,
                                      default="Inquiry"),
        "description": clean_text(form.get("description"), "Description", 2000,
                                  required=True),
        "rating": clean_rating(form.get("rating")),
        "resolution": clean_text(resolution, "Resolution", 2000),
    }
    if values["feedback_type"] == "Complaint" and not (
            values["customer_name"] or values["contact"]):
        raise ValueError("A complaint needs a customer name or a contact so"
                         " the desk can answer it.")
    assigned = clean_account(form.get("assigned_to"), "Assigned account")
    values["assigned_to"] = assigned
    values["assigned_to_name"] = account_name(db, assigned)
    values["status"] = clean_status(status, "Status", FEEDBACK_STATUSES)
    if values["status"] in ("Resolved", "Closed") and not values["resolution"]:
        raise ValueError("Write what settled the matter before closing it.")
    return values


@marketing_bp.route("/feedback/create", methods=["GET", "POST"])
@perm("marketing_feedback")
def create_feedback():
    db = get_db()
    if request.method == "POST":
        try:
            values = feedback_values(
                request.form, db, status=request.form.get("status") or "Pending")
            user = current_user()
            feedback_id = insert_feedback(db, values, user)
            stamp_record(db, "customer_feedback", feedback_id, user)
            db.commit()
            log_activity("CREATE", "marketing",
                         "{} from '{}' recorded".format(
                             values["feedback_type"],
                             values["customer_name"] or "an unnamed customer"),
                         record_type="customer_feedback",
                         record_id=feedback_id, updated=values)
            flash("Feedback recorded.", "success")
            return redirect(url_for("marketing.feedback"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/feedback_form.html", item=None,
                           accounts=marketing_accounts(db))


@marketing_bp.route("/feedback/<int:feedback_id>", methods=["GET", "POST"])
@perm("marketing_feedback")
def edit_feedback(feedback_id):
    db = get_db()
    item = guarded_row(db, "customer_feedback", feedback_id)
    if request.method == "POST":
        try:
            values = feedback_values(
                request.form, db, status=request.form.get("status"),
                resolution=request.form.get("resolution", ""))
            user = current_user()
            update_feedback(db, feedback_id, values)
            stamp_record(db, "customer_feedback", feedback_id, user)
            db.commit()
            previous, updated = change_set(item, values)
            log_activity("UPDATE", "marketing",
                         "Feedback #{} revised".format(feedback_id),
                         record_type="customer_feedback",
                         record_id=feedback_id, previous=previous,
                         updated=updated)
            flash("Feedback updated.", "success")
            return redirect(url_for("marketing.feedback"))
        except ValueError as error:
            refused_write(error)
    return render_template("marketing/feedback_form.html", item=item,
                           accounts=marketing_accounts(db))


# ----------------------------------------------------------------- customers
@marketing_bp.route("/customers")
@perm("marketing_leads")
def customers():
    """Who the company has billed, read from Finance rather than re-registered."""
    db = get_db()
    search = (request.args.get("search") or "").strip()
    return render_template("marketing/customers.html",
                           customers=customer_rows(db, search or None),
                           search=search)


# ------------------------------------------------------------------- reports
@marketing_bp.route("/reports")
@perm("marketing_reports")
def reports():
    db = get_db()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    try:
        start = clean_date(start, "From date") or ""
        end = clean_date(end, "To date") or ""
    except ValueError as error:
        refused_write(error)
        return redirect(url_for("marketing.reports"))
    return render_template(
        "marketing/reports.html",
        summary=marketing_summary(db),
        channels=marketing_channel_report(db),
        pipeline=marketing_pipeline(db),
        feedback=marketing_feedback_report(db),
        performance=campaign_performance(db, start or None, end or None),
        start=start, end=end)


# A list screen and its export answer with the same reader and the same filters,
# so a CSV never carries rows the desk cannot see on screen.
EXPORT_READERS = {
    "plans": lambda db, status, search: plan_rows(db, status, search),
    "campaigns": lambda db, status, search: campaign_rows(db, status, None,
                                                          search),
    "leads": lambda db, status, search: lead_rows(db, status, search),
    "research": lambda db, status, search: research_rows(db, search),
    "digital": lambda db, status, search: digital_rows(db, status, search),
    "feedback": lambda db, status, search: feedback_rows(db, status, search),
    "customers": lambda db, status, search: customer_rows(db, search),
}


@marketing_bp.route("/reports/<kind>.csv")
@perm("marketing_reports")
def export(kind):
    """One desk's table as the file the desk hands over."""
    columns = EXPORT_COLUMNS.get(kind)
    reader = EXPORT_READERS.get(kind)
    if columns is None or reader is None:
        abort(404)
    desk_right = EXPORT_DESK_RIGHT.get(kind)
    if desk_right and not has_permission(desk_right):
        flash("That register is not on your desk.", "error")
        return redirect(url_for("marketing.reports"))
    db = get_db()
    status = (request.args.get("status") or "").strip()
    search = (request.args.get("search") or "").strip()
    rows = reader(db, status or None, search or None)
    log_activity("EXPORT", "marketing",
                 "Marketing {} exported ({} rows)".format(kind, len(rows)))
    db.commit()
    return auditor_csv_response("marketing_{}_{}".format(
        kind, date.today().isoformat()), columns, rows)


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(marketing_bp)
