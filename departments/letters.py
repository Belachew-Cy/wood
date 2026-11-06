"""Letters and correspondence: the register, mailboxes and document actions."""

from core.access import current_user
from core.db import get_db
from datetime import datetime
from flask import Blueprint
from flask import Response
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from reports_export import letter_pdf_bytes
from services.audit import change_set
from services.audit import log_activity
from services.email import absolute_url
from services.email import send_accounts
from services.letters import LETTER_APPROVAL_LADDER
from services.letters import LETTER_APPROVAL_LEVEL_LABEL
from services.letters import LETTER_APPROVAL_LEVELS
from services.letters import LETTER_APPROVAL_STEP_LABEL
from services.letters import LETTER_EXTERNAL_TYPES
from services.letters import LETTER_KINDS
from services.letters import LETTER_KIND_LABEL
from services.letters import LETTER_OPENING_STATUS
from services.letters import LETTER_PENDING_STATUS
from services.letters import LETTER_RECIPIENT_MODES
from services.letters import LETTER_STATUSES
from services.letters import LETTER_STEP_PENDING
from services.letters import LETTER_TYPES
from services.letters import active_account
from services.letters import active_department
from services.letters import account_label
from services.letters import advance_letter_ladder
from services.letters import approval_step_holders
from services.letters import approval_step_rows
from services.letters import clear_inbox_deliveries
from services.letters import clear_letter_notifications
from services.letters import deliver_inbox
from services.letters import department_for_account
from services.letters import department_member_ids
from services.letters import has_letter_permission
from services.letters import letter_allowed_types
from services.letters import letter_approval_depth
from services.letters import letter_approval_rungs
from services.letters import letter_category_access
from services.letters import letter_category_access_for_id
from services.letters import letter_category_in_sql
from services.letters import letter_current_step
from services.letters import letter_kind_label
from services.letters import letter_needs_approval
from services.letters import letter_recipient_options
from services.letters import letter_module
from services.letters import letter_required
from services.letters import letter_requires_ladder
from services.letters import letter_signable_steps
from services.letters import letter_type_label
from services.letters import letter_visible_to
from services.letters import letter_visibility_sql
from services.letters import mark_letter_read
from services.letters import new_letter_reference
from services.letters import open_letter_ladder
from services.letters import record_outbox
from services.letters import return_letter_ladder
from services.letters import unread_letter_count
from services.notifications import deliver_notification
from services.recency import stamp as stamp_record


letters_bp = Blueprint("letters", __name__, url_prefix="/letters")


LETTER_SUBJECT_LIMIT = 250


LETTER_PARTY_LIMIT = 160


LETTER_BODY_LIMIT = 20000


LETTER_COMPANY = "Dereje Belay"
LETTER_COMPANY_LINE = "Wood & Kitchen Cabinet Finishing"


# How a rung of the approval route reads on the letter page.
LETTER_STEP_DECISION_TEXT = {"Not started": "Not started", "Pending": "Waiting",
                             "Approved": "Signed", "Returned": "Returned"}


LETTER_STEP_STATUS_KEY = {"Not started": "idle", "Pending": "pending",
                          "Approved": "signed", "Returned": "returned"}


def _step_name(step):
    """'Level 2 · Director' -> 'Director', for an ordinary sentence."""
    return LETTER_APPROVAL_STEP_LABEL.get(step, step).rsplit("·", 1)[-1].strip()


def _letter_ladder(letter):
    """Whether this row follows the Manager, Director and CEO route."""
    keys = letter.keys()
    return letter_requires_ladder(
        letter["letter_type"],
        letter["requires_approval"] if "requires_approval" in keys else 0)


def _log_letter(action, letter_id, description, outcome="SUCCESS",
                previous=None, updated=None, **details):
    """One letter event, in the central audit trail. The action name is what the
    Audit Console and the Activity Logs screen group by."""
    log_activity(action, "letters", description, outcome=outcome,
                 record_type="letter", record_id=letter_id, details=details,
                 previous=previous, updated=updated)


def _letter_details(letter_type, reference_no, subject, sender=None,
                    recipient=None, kind=None):
    return {"letter_type": letter_type, "letter_kind": kind or "",
            "reference_no": reference_no, "subject": subject,
            "sender": sender, "recipient": recipient}


def _letter_flags():
    """The action ticks the current account holds, for the templates."""
    can_create = has_letter_permission("letters_create")
    return {
        "can_create": can_create,
        "can_compose": can_create and bool(letter_allowed_types(current_user())),
        "can_edit": has_letter_permission("letters_edit"),
        "can_send": has_letter_permission("letters_send"),
        "can_approve": has_letter_permission("letters_approve"),
        "can_print": has_letter_permission("letters_print"),
        "can_download": has_letter_permission("letters_download"),
        "can_delete": has_letter_permission("letters_delete"),
    }


def _form_context(letter_types):
    """Everything the create/edit form needs to show only usable choices."""
    return {
        "letter_types": [(value, letter_type_label(value))
                         for value in letter_types],
        "letter_modes": LETTER_RECIPIENT_MODES,
        "letter_kinds": LETTER_KINDS,
        "letter_kind_labels": LETTER_KIND_LABEL,
        "approval_levels": [(level, LETTER_APPROVAL_LEVEL_LABEL[level])
                            for level in LETTER_APPROVAL_LEVELS],
    }


def _require_category(category):
    """Refuse a category this account was never granted, and record the try."""
    user = current_user()
    if category in LETTER_TYPES and letter_category_access(user, category):
        return
    log_activity("DENY", "letters",
                 "Refused to file a {} on {}".format(
                     letter_type_label(category), request.path),
                 outcome="DENIED",
                 details={"category": category, "endpoint": request.endpoint})
    abort(403, description="This account has not been granted {} access.".format(
        letter_type_label(category)))


def _visible_letter_or_404(db, letter_id):
    letter = db.execute("SELECT * FROM letter.letters WHERE id = ?",
                        (letter_id,)).fetchone()
    if letter is None or not letter_visible_to(db, letter, current_user()):
        abort(404)
    return letter


def _default_values(letter_types):
    return {
        "letter_type": letter_types[0] if letter_types else "official",
        "letter_kind": "",
        "subject": "", "sender": "", "recipient": "", "letter_date": "",
        "body": "", "recipient_mode": "user", "recipient_user_id": "",
        "recipient_department_id": "", "requires_approval": True,
        "approval_level": LETTER_APPROVAL_LEVELS[-1],
    }


def _stored_values(letter):
    """The form as it should open for an existing letter."""
    values = _default_values((letter["letter_type"],))
    values.update({
        "letter_type": letter["letter_type"],
        "letter_kind": letter["letter_kind"] or "",
        "subject": letter["subject"] or "",
        "sender": letter["sender"] or "",
        "recipient": letter["recipient"] or "",
        "letter_date": letter["letter_date"] or "",
        "body": letter["body"] or "",
        "recipient_mode": ("user" if letter["recipient_user_id"]
                           else "department" if letter["recipient_department_id"]
                           else "external"),
        "recipient_user_id": str(letter["recipient_user_id"] or ""),
        "recipient_department_id": str(letter["recipient_department_id"] or ""),
        "requires_approval": bool(letter["requires_approval"]
                                  if "requires_approval" in letter.keys() else 0),
        "approval_level": letter_approval_depth(letter),
    })
    return values


def _sender_line():
    """The signed-in account's name and HR department, ready for the form."""
    department = department_for_account(get_db(), current_user())
    return account_label(current_user(),
                         department["name"] if department else None)


def _stored_kind(letter_type, submitted):
    """Keep a submitted kind only when it belongs to the chosen category."""
    kinds = LETTER_KINDS.get(letter_type, ())
    return submitted if submitted in kinds else ""


def _subject_from_body(body):
    """The first filled line of the letter text, as a stand-in subject."""
    for line in (body or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line[:LETTER_SUBJECT_LIMIT]
    return ""


def _read_letter_form(letter_type, letter=None):
    """(values, addressing, problem) for one submitted letter form.

    The sender comes from the signed-in account and its HR department, or from
    the form for registered incoming mail. Every addressee is checked against
    the accounts and the departments table before it is stored, and an account
    addressee must be able to open the category as well.
    """
    db = get_db()
    user = current_user()
    body = request.form.get("body", "").strip()
    # The subject repeats the first line of the letter, so it is optional: the
    # writer may give a shorter title, or let the letter text title itself.
    subject = (request.form.get("subject", "").strip()
               or _subject_from_body(body))
    raw_level = request.form.get("approval_level", "").strip()
    values = {
        "letter_type": letter_type,
        "letter_kind": request.form.get("letter_kind", "").strip(),
        "subject": subject,
        "sender": request.form.get("sender", "").strip(),
        "recipient": request.form.get("recipient", "").strip(),
        "letter_date": request.form.get("letter_date", "").strip(),
        "body": body,
        "approval_level": LETTER_APPROVAL_LEVELS[-1],
        "recipient_mode": request.form.get("recipient_mode", "").strip(),
        "recipient_user_id": request.form.get("recipient_user_id", "").strip(),
        "recipient_department_id":
            request.form.get("recipient_department_id", "").strip(),
        # Only official mail carries the sign-off ladder; the other categories
        # keep their own release rule and ignore the tick entirely.
        "requires_approval": (letter_type == "official"
                              and bool(request.form.get("requires_approval"))),
    }
    addressing = {
        "sender_user_id": None, "sender_department_id": None,
        "sender_label": "", "recipient_user_id": None,
        "recipient_department_id": None, "recipient_label": "",
    }

    if not values["subject"]:
        return values, addressing, (
            "Write the letter text or give it a subject.")
    if len(values["subject"]) > LETTER_SUBJECT_LIMIT:
        return values, addressing, "Keep the subject within {} characters.".format(
            LETTER_SUBJECT_LIMIT)
    if len(values["body"]) > LETTER_BODY_LIMIT:
        return values, addressing, "Keep the letter body within {} characters.".format(
            LETTER_BODY_LIMIT)
    if not values["letter_date"]:
        return values, addressing, "A letter needs its date."
    try:
        datetime.strptime(values["letter_date"], "%Y-%m-%d")
    except ValueError:
        return values, addressing, "Give the letter date as a real calendar day."

    kinds = LETTER_KINDS.get(letter_type, ())
    if kinds and values["letter_kind"] not in kinds:
        return values, addressing, "Choose the subject of this {}.".format(
            letter_type_label(letter_type).lower())
    values["letter_kind"] = _stored_kind(letter_type, values["letter_kind"])

    if values["requires_approval"]:
        if raw_level and (not raw_level.isdigit()
                          or int(raw_level) not in LETTER_APPROVAL_LEVELS):
            return values, addressing, (
                "Choose how far this letter must be signed: up to the"
                " Manager, the Director or the CEO.")
        values["approval_level"] = (int(raw_level) if raw_level
                                    else len(LETTER_APPROVAL_LADDER))

    if letter_type == "incoming":
        if not values["sender"]:
            return values, addressing, "Say who sent the incoming letter."
        if len(values["sender"]) > LETTER_PARTY_LIMIT:
            return values, addressing, "Keep the sender within {} characters.".format(
                LETTER_PARTY_LIMIT)
        addressing["sender_label"] = values["sender"]
    else:
        if letter is not None:
            addressing["sender_user_id"] = letter["sender_user_id"] or letter["created_by"]
            addressing["sender_department_id"] = letter["sender_department_id"]
            addressing["sender_label"] = letter["sender"] or _sender_line()
        else:
            department = department_for_account(db, user)
            addressing["sender_user_id"] = user["id"]
            addressing["sender_department_id"] = department["id"] if department else None
            addressing["sender_label"] = account_label(
                user, department["name"] if department else None)
        values["sender"] = addressing["sender_label"]

    modes = LETTER_RECIPIENT_MODES.get(letter_type, ())
    if modes == ("external",):
        # Mail that leaves the building always carries a free-text addressee;
        # the internal picker that the form also posts is ignored here.
        mode = "external"
    else:
        mode = values["recipient_mode"] or modes[0]
        if mode not in modes:
            return values, addressing, "Choose an addressee that fits this letter type."
    values["recipient_mode"] = mode

    if mode == "external":
        if not values["recipient"]:
            return values, addressing, "Name the external recipient."
        if len(values["recipient"]) > LETTER_PARTY_LIMIT:
            return values, addressing, "Keep the recipient within {} characters.".format(
                LETTER_PARTY_LIMIT)
        addressing["recipient_label"] = values["recipient"]
    elif mode == "user":
        account = active_account(db, values["recipient_user_id"])
        if account is None:
            return values, addressing, "Choose an active staff member from the list."
        if not letter_category_access_for_id(db, account["id"], letter_type):
            return values, addressing, (
                "That account cannot open {}. Choose another recipient"
                " or ask the administrator to grant the category.").format(
                    letter_type_label(letter_type))
        department = department_for_account(db, account)
        addressing["recipient_user_id"] = account["id"]
        addressing["recipient_label"] = account_label(
            account, department["name"] if department else None)
        values["recipient"] = addressing["recipient_label"]
    else:
        department = active_department(db, values["recipient_department_id"])
        if department is None:
            return values, addressing, "Choose an active department from the list."
        addressing["recipient_department_id"] = department["id"]
        addressing["recipient_label"] = department["name"]
        values["recipient"] = addressing["recipient_label"]

    return values, addressing, None


def _notify_recipients(db, letter_id, subject, reference_no, sender_label,
                       category, recipient_user_id=None,
                       recipient_department_id=None):
    """Put the new letter in the bell of everyone whose inbox receives it."""
    if recipient_user_id:
        recipients = [recipient_user_id]
    elif recipient_department_id:
        recipients = department_member_ids(db, recipient_department_id, category)
    else:
        return
    href = url_for("letters.detail", letter_id=letter_id)
    for user_id in recipients:
        deliver_notification(
            db, user_id, "New letter: {}".format(subject),
            "{} · {}".format(sender_label or "A colleague", reference_no),
            href, category="letters", icon="mail", tone="neutral",
            dedupe_key="letter-{}".format(letter_id))


def _letter_stamp(db, letter_id):
    row = db.execute("SELECT updated_at FROM letter.letters WHERE id = ?",
                     (letter_id,)).fetchone()
    return (row["updated_at"] if row is not None else "") or ""


def _email_step_holders(db, letter, step, subject, intro, extra=()):
    """Mail the rung that has to sign, after the same category check as the bell."""
    accounts = [account for account in approval_step_holders(db, step)
                if letter_category_access_for_id(db, account["id"],
                                                 letter["letter_type"])]
    if not accounts:
        return
    details = ["Reference: {}".format(letter["reference_no"]),
               "From: {}".format(letter["sender"] or LETTER_COMPANY)]
    if letter["recipient"]:
        details.append("Addressee: {}".format(letter["recipient"]))
    details.append("Stage: {}".format(
        LETTER_APPROVAL_STEP_LABEL.get(step, step)))
    details.extend(extra)
    send_accounts(
        db, accounts, subject, intro, details,
        event_key="letter-{}-{}-{}".format(letter["id"], step,
                                           _letter_stamp(db, letter["id"])),
        category="letters", exclude_ids=(letter["created_by"],),
        action_url=absolute_url(url_for("letters.detail", letter_id=letter["id"],
                                        _external=True)),
        action_label="Open the letter", related_type="letter",
        related_id=letter["id"])


def _email_author(db, letter, event, subject, intro, extra=()):
    """Mail the author after an approval, a return or a final signature."""
    # The full row: the mailbox layer reads status, email and employee_id.
    account = db.execute(
        "SELECT * FROM users WHERE id = ? AND status = 'Active'",
        (letter["created_by"],)).fetchone()
    if account is None:
        return
    details = ["Reference: {}".format(letter["reference_no"]),
               "Subject: {}".format(letter["subject"])]
    details.extend(extra)
    send_accounts(
        db, [account], subject, intro, details,
        event_key="letter-{}-author-{}-{}".format(
            letter["id"], event, _letter_stamp(db, letter["id"])),
        category="letters",
        action_url=absolute_url(url_for("letters.detail", letter_id=letter["id"],
                                        _external=True)),
        action_label="Open the letter", related_type="letter",
        related_id=letter["id"])


def _notify_step_holders(db, letter_id, step, subject, reference_no,
                         category=None):
    """Ask every account holding the open rung to sign the letter.

    A rung holder can only sign what it can open, so an account without the
    letter's category is left out of the bell rather than sent to a 404.
    """
    href = url_for("letters.detail", letter_id=letter_id)
    for account in approval_step_holders(db, step):
        if (category is not None
                and not letter_category_access_for_id(db, account["id"],
                                                      category)):
            continue
        deliver_notification(
            db, account["id"],
            "Letter waiting for your signature: {}".format(subject),
            "{} · {}".format(reference_no, LETTER_APPROVAL_STEP_LABEL[step]),
            href, category="letters", icon="mail", tone="warn",
            dedupe_key="letter-{}-{}".format(letter_id, step), reopen=True)


def _sync_recipient_notifications(db, letter_id, subject, reference_no,
                                  sender_label, category, recipient_user_id=None,
                                  recipient_department_id=None):
    """Keep bell entries in step with a registered letter's new addressee.

    A notification follows the mail: an account that no longer receives the
    letter loses the entry, so its link cannot lead to a letter it may not open.
    """
    if recipient_user_id:
        keep = {recipient_user_id}
    elif recipient_department_id:
        keep = set(department_member_ids(db, recipient_department_id, category))
    else:
        keep = set()
    for row in db.execute(
            "SELECT id, user_id FROM notifications WHERE category = 'letters'"
            " AND dedupe_key = ?", ("letter-{}".format(letter_id),)).fetchall():
        if row["user_id"] not in keep:
            db.execute("DELETE FROM notifications WHERE id = ?", (row["id"],))
    _notify_recipients(db, letter_id, subject, reference_no, sender_label,
                       category, recipient_user_id, recipient_department_id)


def _like(term):
    escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return "%" + escaped + "%"


@letters_bp.route("/")
@letter_module
@letter_required("letters_view")
def dashboard():
    db = get_db()
    user = current_user()
    search = request.args.get("q", "").strip()
    letter_type = request.args.get("type", "").strip()
    status = request.args.get("status", "").strip()
    allowed_types = letter_allowed_types(user)

    visibility, visibility_params = letter_visibility_sql(db, user, alias="l")
    query = "SELECT l.* FROM letter.letters AS l WHERE " + (visibility or "1 = 1")
    params = list(visibility_params)
    if search:
        query += (" AND (l.reference_no LIKE ? ESCAPE '\\'"
                  " OR l.subject LIKE ? ESCAPE '\\'"
                  " OR l.sender LIKE ? ESCAPE '\\'"
                  " OR l.recipient LIKE ? ESCAPE '\\')")
        params.extend([_like(search)] * 4)
    if letter_type in LETTER_TYPES:
        query += " AND l.letter_type = ?"
        params.append(letter_type)
    if status in LETTER_STATUSES:
        query += " AND l.status = ?"
        params.append(status)
    query += " ORDER BY l.letter_date DESC, l.id DESC"

    letters = db.execute(query, params).fetchall()

    # The signatures this account owes right now: letters sitting on a rung it
    # answers, drawn through the same visibility guard as the register.
    signable = letter_signable_steps(user, db)
    awaiting_signature = []
    if signable:
        awaiting_signature = db.execute(
            "SELECT l.* FROM letter.letters AS l WHERE ({guard})"
            " AND l.status = ? AND l.approval_step IN ({marks})"
            " AND l.created_by != ? ORDER BY l.updated_at DESC, l.id DESC".format(
                guard=visibility or "1 = 1",
                marks=", ".join("?" for _ in signable)),
            list(visibility_params) + [LETTER_PENDING_STATUS]
            + list(signable) + [user["id"]]).fetchall()

    counts = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN l.letter_type = 'official' THEN 1 ELSE 0 END) AS official,
               SUM(CASE WHEN l.letter_type = 'incoming' THEN 1 ELSE 0 END) AS incoming,
               SUM(CASE WHEN l.letter_type = 'outgoing' THEN 1 ELSE 0 END) AS outgoing,
               SUM(CASE WHEN l.status = 'Approved' THEN 1 ELSE 0 END) AS approved
        FROM letter.letters AS l
        WHERE {}
    """.format(visibility or "1 = 1"), visibility_params).fetchone()

    return render_template(
        "Letter/index.html", letters=letters, counts=counts, search=search,
        selected_type=letter_type, selected_status=status,
        letter_types=allowed_types, letter_statuses=LETTER_STATUSES,
        awaiting_signature=awaiting_signature,
        step_labels=LETTER_APPROVAL_STEP_LABEL,
        unread=unread_letter_count(db, user), **_letter_flags())


@letters_bp.route("/new", methods=["GET", "POST"])
@letter_module
@letter_required("letters_create")
def create():
    allowed_types = letter_allowed_types(current_user())
    if not allowed_types:
        abort(403, description="No letter category is open to this account."
                               " Ask an administrator for a category grant.")
    if request.method == "POST":
        letter_type = request.form.get("letter_type", "").strip()
        if letter_type not in LETTER_TYPES:
            abort(400, description="Choose a letter category from the list.")
        _require_category(letter_type)
        values, addressing, problem = _read_letter_form(letter_type)
        if problem:
            log_activity("CREATE", "letters",
                         "Rejected a new {} letter: {}".format(letter_type, problem),
                         outcome="FAILURE",
                         details={"letter_type": letter_type, "problem": problem})
            flash(problem, "error")
            accounts, departments = letter_recipient_options(get_db())
            return render_template(
                "Letter/form.html", letter=None, values=values,
                sender_display=_sender_line(), accounts=accounts,
                departments=departments,
                **_form_context(allowed_types)), 400

        db = get_db()
        reference_no = new_letter_reference(db)
        created = db.execute("""
            INSERT INTO letter.letters (reference_no, letter_type, letter_kind, subject,
                                 sender, recipient, letter_date, body, status,
                                 created_by, sender_user_id, sender_department_id,
                                 recipient_user_id, recipient_department_id,
                                 requires_approval, approval_depth)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (reference_no, letter_type, values["letter_kind"],
              values["subject"], addressing["sender_label"],
              addressing["recipient_label"], values["letter_date"],
              values["body"], LETTER_OPENING_STATUS[letter_type],
              current_user()["id"], addressing["sender_user_id"],
              addressing["sender_department_id"],
              addressing["recipient_user_id"],
              addressing["recipient_department_id"],
              1 if values["requires_approval"] else 0,
              values["approval_level"]))
        letter_id = created.lastrowid
        if letter_type == "incoming":
            # Incoming mail is already in the building: it lands in the
            # recipient's inbox the moment it is registered.
            deliver_inbox(db, letter_id, addressing["recipient_user_id"],
                          addressing["recipient_department_id"])
            _notify_recipients(db, letter_id, values["subject"], reference_no,
                               addressing["sender_label"], letter_type,
                               addressing["recipient_user_id"],
                               addressing["recipient_department_id"])
        else:
            record_outbox(db, letter_id, addressing["sender_user_id"])
        stamp_record(db, "letters", letter_id)
        db.commit()
        _log_letter(
            "CREATE", letter_id,
            "Created {} {} for {}".format(
                letter_type_label(letter_type).lower(), reference_no,
                addressing["recipient_label"] or "an external addressee"),
            **_letter_details(letter_type, reference_no, values["subject"],
                              addressing["sender_label"],
                              addressing["recipient_label"],
                              values["letter_kind"]))
        flash("Letter {} created.".format(reference_no), "success")
        return redirect(url_for("letters.detail", letter_id=letter_id))

    accounts, departments = letter_recipient_options(get_db())
    return render_template(
        "Letter/form.html", letter=None, values=_default_values(allowed_types),
        sender_display=_sender_line(), accounts=accounts,
        departments=departments, **_form_context(allowed_types))


@letters_bp.route("/inbox")
@letter_module
@letter_required("letters_view")
def inbox():
    db = get_db()
    user = current_user()
    department = department_for_account(db, user)
    department_id = department["id"] if department else -1
    scope, scope_params = letter_category_in_sql(user, "l")
    letters = db.execute("""
        SELECT l.*, r.read_at AS read_at,
               CASE WHEN r.read_at IS NULL THEN 1 ELSE 0 END AS unread
        FROM letter.letters AS l
        LEFT JOIN letter.letter_reads AS r
               ON r.letter_id = l.id AND r.user_id = ?
        WHERE ({scope})
          AND EXISTS (
            SELECT 1 FROM letter.letter_deliveries AS d
            WHERE d.letter_id = l.id AND d.folder = 'inbox'
              AND (d.user_id = ? OR d.department_id = ?))
        ORDER BY l.letter_date DESC, l.id DESC
    """.format(scope=scope),
        (user["id"],) + scope_params + (user["id"], department_id)).fetchall()
    return render_template("Letter/mailbox.html", folder="inbox",
                           letters=letters, unread=unread_letter_count(db, user),
                           department=department, **_letter_flags())


@letters_bp.route("/outbox")
@letter_module
@letter_required("letters_view")
def outbox():
    db = get_db()
    user = current_user()
    scope, scope_params = letter_category_in_sql(user, "l")
    letters = db.execute("""
        SELECT l.*
        FROM letter.letters AS l
        WHERE ({scope})
          AND EXISTS (
            SELECT 1 FROM letter.letter_deliveries AS d
            WHERE d.letter_id = l.id AND d.folder = 'outbox'
              AND d.user_id = ?)
        ORDER BY l.letter_date DESC, l.id DESC
    """.format(scope=scope),
        scope_params + (user["id"],)).fetchall()
    return render_template("Letter/mailbox.html", folder="outbox",
                           letters=letters, unread=unread_letter_count(db, user),
                           department=department_for_account(db, user),
                           **_letter_flags())


@letters_bp.route("/<int:letter_id>")
@letter_module
@letter_required("letters_view")
def detail(letter_id):
    db = get_db()
    user = current_user()
    letter = _visible_letter_or_404(db, letter_id)
    department = department_for_account(db, user)
    department_id = department["id"] if department else -1
    delivered = db.execute(
        "SELECT 1 FROM letter.letter_deliveries WHERE letter_id = ?"
        " AND folder = 'inbox' AND (user_id = ? OR department_id = ?)",
        (letter_id, user["id"], department_id)).fetchone() is not None
    if delivered and mark_letter_read(db, letter_id, user["id"]):
        _log_letter(
            "RECEIVE", letter_id,
            "Received letter {} from {}".format(
                letter["reference_no"], letter["sender"] or "an external sender"),
            **_letter_details(letter["letter_type"], letter["reference_no"],
                              letter["subject"], letter["sender"],
                              letter["recipient"], letter["letter_kind"]))
        db.commit()
    deliveries = db.execute("""
        SELECT d.user_id, u.full_name, d.department_id, dep.name AS department_name
        FROM letter.letter_deliveries AS d
        LEFT JOIN users AS u ON u.id = d.user_id
        LEFT JOIN departments AS dep ON dep.id = d.department_id
        WHERE d.letter_id = ? AND d.folder = 'inbox'
        ORDER BY d.id
    """, (letter_id,)).fetchall()
    approver = None
    if letter["approved_by"]:
        approver = db.execute(
            "SELECT full_name FROM users WHERE id = ?",
            (letter["approved_by"],)).fetchone()
    creator = db.execute("SELECT full_name FROM users WHERE id = ?",
                         (letter["created_by"],)).fetchone()

    status = letter["status"]
    external = letter["letter_type"] in LETTER_EXTERNAL_TYPES
    approval_needed = letter_needs_approval(letter) and status != "Approved"
    ladder = _letter_ladder(letter)
    current_step = letter_current_step(letter)
    signable = current_step in letter_signable_steps(user, db)
    is_author = user["id"] == letter["created_by"] or user["role"] == "admin"
    letter_route = []
    if ladder:
        rows = {row["step"]: row for row in approval_step_rows(db, letter_id)}
        open_for_signature = status in ("Draft", LETTER_PENDING_STATUS)
        for step in letter_approval_rungs(letter):
            row = rows.get(step)
            decision = row["decision"] if row is not None else "Not started"
            holders = ""
            if decision == LETTER_STEP_PENDING and open_for_signature:
                holders = ", ".join(account["full_name"] for account
                                    in approval_step_holders(db, step)
                                    if account["id"] != letter["created_by"])
            letter_route.append({
                "step": step,
                "label": LETTER_APPROVAL_STEP_LABEL[step],
                "status_label": LETTER_STEP_DECISION_TEXT[decision],
                "status_key": LETTER_STEP_STATUS_KEY[decision],
                "actor_name": row["actor_name"] if row is not None else "",
                "decided_at": row["decided_at"] if row is not None else None,
                "comments": row["comments"] if row is not None else "",
                "holder_names": holders,
                "current": step == current_step,
            })
    flags = _letter_flags()
    flags.update({
        "letter_external": external,
        "letter_ladder": ladder,
        "letter_route": letter_route,
        "letter_approval_level_label":
            LETTER_APPROVAL_LEVEL_LABEL[letter_approval_depth(letter)],
        "letter_step_labels": LETTER_APPROVAL_STEP_LABEL,
        "current_step": current_step,
        "can_edit_here": flags["can_edit"] and status == "Draft",
        "can_submit_here": (ladder and status == "Draft" and is_author
                            and flags["can_create"]),
        "can_approve_here": (flags["can_approve"] and not ladder
                             and letter["letter_type"] != "incoming"
                             and status == "Draft"),
        "can_sign_here": (signable and status == LETTER_PENDING_STATUS
                          and letter["created_by"] != user["id"]),
        "can_return_here": (signable and status == LETTER_PENDING_STATUS
                            and letter["created_by"] != user["id"]),
        "can_send_here": (flags["can_send"]
                          and letter["letter_type"] != "incoming"
                          and status in ("Draft", "Approved")
                          and not approval_needed),
    })
    return render_template("Letter/detail.html", letter=letter, approver=approver,
                           creator=creator,
                           deliveries=deliveries,
                           **flags)


@letters_bp.route("/<int:letter_id>/edit", methods=["GET", "POST"])
@letter_module
@letter_required("letters_edit")
def edit(letter_id):
    db = get_db()
    letter = _visible_letter_or_404(db, letter_id)
    # An approved or sent letter is the record of what was signed; it stays as
    # it was approved. Only a draft is still being written.
    if letter["status"] != "Draft":
        abort(403, description="Only a draft can be edited.")

    if request.method == "POST":
        values, addressing, problem = _read_letter_form(letter["letter_type"],
                                                        letter)
        if problem:
            log_activity("UPDATE", "letters",
                         "Rejected an update to letter {}: {}".format(
                             letter["reference_no"], problem),
                         outcome="FAILURE", record_type="letter",
                         record_id=letter_id,
                         details={"reference_no": letter["reference_no"],
                                  "problem": problem})
            flash(problem, "error")
            accounts, departments = letter_recipient_options(db)
            return render_template(
                "Letter/form.html", letter=letter, values=values,
                sender_display=letter["sender"] or _sender_line(),
                accounts=accounts, departments=departments,
                **_form_context((letter["letter_type"],))), 400
        previous_values, updated_values = change_set(letter, {
            "subject": values["subject"], "body": values["body"],
            "sender": addressing["sender_label"],
            "recipient": addressing["recipient_label"],
            "letter_date": values["letter_date"],
        })
        db.execute("""
            UPDATE letter.letters
            SET letter_kind = ?, subject = ?, sender = ?, recipient = ?,
                letter_date = ?, body = ?, sender_user_id = ?,
                sender_department_id = ?, recipient_user_id = ?,
                recipient_department_id = ?, requires_approval = ?,
                approval_depth = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (values["letter_kind"], values["subject"],
              addressing["sender_label"], addressing["recipient_label"],
              values["letter_date"], values["body"],
              addressing["sender_user_id"],
              addressing["sender_department_id"],
              addressing["recipient_user_id"],
              addressing["recipient_department_id"],
              1 if values["requires_approval"] else 0,
              values["approval_level"], letter_id))
        if letter["letter_type"] == "incoming":
            # Registered mail follows its recipient: move the inbox copy too.
            clear_inbox_deliveries(db, letter_id)
            deliver_inbox(db, letter_id, addressing["recipient_user_id"],
                          addressing["recipient_department_id"])
            _sync_recipient_notifications(
                db, letter_id, values["subject"], letter["reference_no"],
                addressing["sender_label"], letter["letter_type"],
                addressing["recipient_user_id"],
                addressing["recipient_department_id"])
        stamp_record(db, "letters", letter_id)
        db.commit()
        _log_letter(
            "UPDATE", letter_id,
            "Updated letter {} for {}".format(letter["reference_no"],
                                              addressing["recipient_label"]),
            **_letter_details(letter["letter_type"], letter["reference_no"],
                              values["subject"], addressing["sender_label"],
                              addressing["recipient_label"],
                              values["letter_kind"]),
            previous=previous_values, updated=updated_values)
        flash("Letter {} updated.".format(letter["reference_no"]), "success")
        return redirect(url_for("letters.detail", letter_id=letter_id))

    accounts, departments = letter_recipient_options(db)
    return render_template(
        "Letter/form.html", letter=letter, values=_stored_values(letter),
        sender_display=letter["sender"] or _sender_line(),
        accounts=accounts, departments=departments,
        **_form_context((letter["letter_type"],)))


@letters_bp.post("/<int:letter_id>/approve")
@letter_module
@letter_required("letters_approve")
def approve(letter_id):
    db = get_db()
    user = current_user()
    letter = _visible_letter_or_404(db, letter_id)

    def refuse(message):
        log_activity("APPROVE", "letters",
                     "Refused to approve letter {}: {}".format(
                         letter["reference_no"], message),
                     outcome="FAILURE", record_type="letter",
                     record_id=letter_id,
                     details={"reference_no": letter["reference_no"],
                              "problem": message})
        abort(400, description=message)

    if letter["letter_type"] == "incoming":
        refuse("Incoming correspondence is recorded, not approved.")
    if _letter_ladder(letter):
        refuse("This letter follows the Manager, Director and CEO approval route.")
    if letter["status"] != "Draft":
        refuse("Only a draft can be approved.")

    db.execute("""
        UPDATE letter.letters
        SET status = 'Approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (user["id"], letter_id))
    if letter["created_by"] != user["id"]:
        deliver_notification(
            db, letter["created_by"],
            "Letter approved: {}".format(letter["subject"]),
            "{} · approved by {}".format(letter["reference_no"], user["full_name"]),
            url_for("letters.detail", letter_id=letter_id),
            category="letters", icon="check", tone="success",
            dedupe_key="letter-{}-approved".format(letter_id))
        _email_author(
            db, letter, "approved",
            "Letter approved: {}".format(letter["subject"]),
            "\"{}\" was approved by {}.".format(letter["subject"],
                                                 user["full_name"]),
            extra=["Approved by: {}".format(user["full_name"])])
    stamp_record(db, "letters", letter_id, user)
    db.commit()
    _log_letter(
        "APPROVE", letter_id,
        "Approved letter {} for {}".format(
            letter["reference_no"],
            letter["recipient"] or "an external addressee"),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    flash("Letter {} approved.".format(letter["reference_no"]), "success")
    return redirect(url_for("letters.detail", letter_id=letter_id))


@letters_bp.post("/<int:letter_id>/submit")
@letter_module
@letter_required("letters_create")
def submit(letter_id):
    """Open the approval route: the draft goes to its Manager first."""
    db = get_db()
    user = current_user()
    letter = _visible_letter_or_404(db, letter_id)

    def refuse(message):
        log_activity("SUBMIT", "letters",
                     "Refused to submit letter {} for approval: {}".format(
                         letter["reference_no"], message),
                     outcome="FAILURE", record_type="letter",
                     record_id=letter_id,
                     details={"reference_no": letter["reference_no"],
                              "problem": message})
        abort(400, description=message)

    if not _letter_ladder(letter):
        refuse("This letter does not follow the approval route.")
    if letter["status"] != "Draft":
        refuse("Only a draft can be submitted for approval.")
    if user["id"] != letter["created_by"] and user["role"] != "admin":
        refuse("Only the author can submit this letter for approval.")

    rungs = letter_approval_rungs(letter)
    open_letter_ladder(db, letter_id, len(rungs))
    _notify_step_holders(db, letter_id, rungs[0], letter["subject"],
                         letter["reference_no"], letter["letter_type"])
    _email_step_holders(
        db, letter, rungs[0],
        "Letter awaiting your signature: {}".format(letter["subject"]),
        "\"{}\" was submitted for approval and now needs your signature at the"
        " {} desk.".format(letter["subject"], _step_name(rungs[0])))
    stamp_record(db, "letters", letter_id, user)
    db.commit()
    _log_letter(
        "SUBMIT", letter_id,
        "Submitted letter {} for approval: {}".format(
            letter["reference_no"],
            ", ".join(_step_name(step) for step in rungs)),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    flash("Letter {} sent to the {} for the first signature.".format(
        letter["reference_no"], _step_name(rungs[0])), "success")
    return redirect(url_for("letters.detail", letter_id=letter_id))


@letters_bp.post("/<int:letter_id>/sign")
@letter_module
@letter_required("letters_approve")
def sign(letter_id):
    """Sign the rung that is open and hand the letter to the next one."""
    db = get_db()
    user = current_user()
    letter = _visible_letter_or_404(db, letter_id)
    comments = request.form.get("comments", "").strip()[:1000]

    def refuse(message):
        log_activity("APPROVE", "letters",
                     "Refused to sign letter {}: {}".format(
                         letter["reference_no"], message),
                     outcome="FAILURE", record_type="letter",
                     record_id=letter_id,
                     details={"reference_no": letter["reference_no"],
                              "problem": message})
        abort(400, description=message)

    step = letter_current_step(letter)
    if step is None:
        refuse("This letter is not waiting for an approval signature.")
    if letter["created_by"] == user["id"]:
        refuse("The author cannot sign off their own letter.")
    if step not in letter_signable_steps(user, db):
        refuse("Only a {} can sign this letter at this stage.".format(
            _step_name(step)))

    following = advance_letter_ladder(db, letter_id, step, user, comments)
    if following:
        _notify_step_holders(db, letter_id, following, letter["subject"],
                             letter["reference_no"], letter["letter_type"])
        _email_step_holders(
            db, letter, following,
            "Letter awaiting your signature: {}".format(letter["subject"]),
            "Letter {} was signed by the {} and now needs your signature at the"
            " {} desk.".format(letter["reference_no"], _step_name(step),
                               _step_name(following)),
            extra=["Signed by: {}".format(user["full_name"])])
    elif letter["created_by"] != user["id"]:
        deliver_notification(
            db, letter["created_by"],
            "Letter approved: {}".format(letter["subject"]),
            "{} · signed by the {}; ready to send.".format(
                letter["reference_no"], _step_name(step)),
            url_for("letters.detail", letter_id=letter_id),
            category="letters", icon="check", tone="success",
            dedupe_key="letter-{}-approved".format(letter_id), reopen=True)
        _email_author(
            db, letter, "approved",
            "Letter approved: {}".format(letter["subject"]),
            "\"{}\" has cleared its approval route and is ready to send.".format(
                letter["subject"]),
            extra=["Approved at the {}".format(_step_name(step)),
                   "Signed by: {}".format(user["full_name"])])
    stamp_record(db, "letters", letter_id, user)
    db.commit()
    _log_letter(
        "APPROVE", letter_id,
        "Signed letter {} at {} ({}){}".format(
            letter["reference_no"], LETTER_APPROVAL_STEP_LABEL[step],
            "approved, ready to send" if following is None
            else "next: {}".format(_step_name(following)),
            ": " + comments if comments else ""),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    if following:
        flash("Signed at the {}. Letter {} now waits for the {}.".format(
            _step_name(step), letter["reference_no"], _step_name(following)),
            "success")
    else:
        flash("Signed by the {}. Letter {} is approved and ready to send.".format(
            _step_name(step), letter["reference_no"]), "success")
    return redirect(url_for("letters.detail", letter_id=letter_id))


@letters_bp.post("/<int:letter_id>/return")
@letter_module
@letter_required("letters_approve")
def return_letter(letter_id):
    """Send the letter back to its author with a note; it reopens as a draft."""
    db = get_db()
    user = current_user()
    letter = _visible_letter_or_404(db, letter_id)
    comments = request.form.get("comments", "").strip()[:1000]

    def refuse(message):
        log_activity("RETURN", "letters",
                     "Refused to return letter {}: {}".format(
                         letter["reference_no"], message),
                     outcome="FAILURE", record_type="letter",
                     record_id=letter_id,
                     details={"reference_no": letter["reference_no"],
                              "problem": message})
        abort(400, description=message)

    step = letter_current_step(letter)
    if step is None:
        refuse("This letter is not waiting for an approval signature.")
    if letter["created_by"] == user["id"]:
        refuse("The author cannot return their own letter.")
    if step not in letter_signable_steps(user, db):
        refuse("Only a {} can return this letter at this stage.".format(
            _step_name(step)))
    if not comments:
        refuse("Say why the letter is going back to its author.")

    return_letter_ladder(db, letter_id, step, user, comments)
    if letter["created_by"] != user["id"]:
        deliver_notification(
            db, letter["created_by"],
            "Letter returned: {}".format(letter["subject"]),
            "{} · returned by the {}: {}".format(
                letter["reference_no"], _step_name(step), comments),
            url_for("letters.detail", letter_id=letter_id),
            category="letters", icon="mail", tone="warn",
            dedupe_key="letter-{}-returned".format(letter_id), reopen=True)
        _email_author(
            db, letter, "returned",
            "Letter returned for revision: {}".format(letter["subject"]),
            "Letter {} was returned by the {} and reopened as a draft.".format(
                letter["reference_no"], _step_name(step)),
            extra=["Returned by: {}".format(user["full_name"]),
                   "Comment: {}".format(comments)])
    stamp_record(db, "letters", letter_id, user)
    db.commit()
    _log_letter(
        "RETURN", letter_id,
        "Returned letter {} from the {} to its author: {}".format(
            letter["reference_no"], LETTER_APPROVAL_STEP_LABEL[step], comments),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    flash("Letter {} returned to its author and reopened as a draft.".format(
        letter["reference_no"]), "success")
    return redirect(url_for("letters.detail", letter_id=letter_id))


@letters_bp.post("/<int:letter_id>/send")
@letter_module
@letter_required("letters_send")
def send(letter_id):
    db = get_db()
    letter = _visible_letter_or_404(db, letter_id)

    def refuse(message):
        log_activity("SEND", "letters",
                     "Refused to send letter {}: {}".format(
                         letter["reference_no"], message),
                     outcome="FAILURE", record_type="letter",
                     record_id=letter_id,
                     details={"reference_no": letter["reference_no"],
                              "problem": message})
        abort(400, description=message)

    if letter["letter_type"] == "incoming":
        refuse("Incoming correspondence is recorded, not sent.")
    if letter["status"] not in ("Draft", "Approved"):
        refuse("Only a draft or an approved letter can be sent.")
    if letter_needs_approval(letter) and letter["status"] != "Approved":
        refuse("This letter must be approved before it can be sent.")

    delivered_label = ""
    if letter["letter_type"] not in LETTER_EXTERNAL_TYPES:
        # Re-check the addressee, which may have changed since the draft.
        if letter["recipient_user_id"]:
            account = active_account(db, letter["recipient_user_id"])
            if account is None:
                refuse("The recipient account is no longer active."
                       " Edit the letter and choose another one.")
            if not letter_category_access_for_id(db, account["id"],
                                                 letter["letter_type"]):
                refuse("The recipient can no longer open {}."
                       " Edit the letter and choose another one.".format(
                           letter_type_label(letter["letter_type"])))
        elif letter["recipient_department_id"]:
            department = active_department(db, letter["recipient_department_id"])
            if department is None:
                refuse("The recipient department is no longer active."
                       " Edit the letter and choose another one.")
        else:
            refuse("This letter has no recipient yet."
                   " Edit it and choose one before sending.")
        deliver_inbox(db, letter_id, letter["recipient_user_id"],
                      letter["recipient_department_id"])
        _notify_recipients(db, letter_id, letter["subject"],
                           letter["reference_no"], letter["sender"],
                           letter["letter_type"], letter["recipient_user_id"],
                           letter["recipient_department_id"])
        delivered_label = letter["recipient"] or "the recipient"

    record_outbox(db, letter_id, letter["sender_user_id"] or letter["created_by"])
    db.execute("UPDATE letter.letters SET status = 'Sent', sent_at = CURRENT_TIMESTAMP,"
               " updated_at = CURRENT_TIMESTAMP WHERE id = ?", (letter_id,))
    stamp_record(db, "letters", letter_id)
    db.commit()
    _log_letter(
        "SEND", letter_id,
        "Sent letter {} to {}".format(
            letter["reference_no"],
            delivered_label or letter["recipient"] or "an external addressee"),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    if delivered_label:
        flash("Letter {} sent and delivered to {}.".format(
            letter["reference_no"], delivered_label), "success")
    else:
        flash("Letter {} marked as sent.".format(letter["reference_no"]),
              "success")
    return redirect(url_for("letters.detail", letter_id=letter_id))


@letters_bp.get("/<int:letter_id>/print")
@letter_module
@letter_required("letters_print")
def print_letter(letter_id):
    """The letter as it goes on the company sheet; every print is recorded."""
    db = get_db()
    letter = _visible_letter_or_404(db, letter_id)
    approver = None
    if letter["approved_by"]:
        approver = db.execute("SELECT full_name FROM users WHERE id = ?",
                              (letter["approved_by"],)).fetchone()
    _log_letter(
        "PRINT", letter_id,
        "Printed letter {}".format(letter["reference_no"]),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    db.commit()
    return render_template("Letter/print.html", letter=letter, approver=approver)


@letters_bp.get("/<int:letter_id>/download")
@letter_module
@letter_required("letters_download")
def download(letter_id):
    """The letter as a PDF attachment; the download is recorded before it leaves."""
    db = get_db()
    letter = _visible_letter_or_404(db, letter_id)
    meta = [
        ("Reference", letter["reference_no"]),
        ("Date", letter["letter_date"]),
        ("Category", " - ".join(value for value in (
            letter_type_label(letter["letter_type"]),
            letter_kind_label(letter["letter_type"], letter["letter_kind"]))
            if value)),
        ("From", letter["sender"] or "-"),
        ("To", letter["recipient"] or "-"),
        ("Subject", letter["subject"]),
        ("Status", letter["status"]),
    ]
    pdf = letter_pdf_bytes(
        LETTER_COMPANY, LETTER_COMPANY_LINE, meta,
        letter["body"] or "No letter body has been recorded.",
        ["Authorized signature: ______________________________",
         "Date: __________________"])
    _log_letter(
        "DOWNLOAD", letter_id,
        "Downloaded letter {}".format(letter["reference_no"]),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    db.commit()
    filename = "{}.pdf".format(letter["reference_no"])
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition":
                             'attachment; filename="{}"'.format(filename),
                             "Cache-Control": "no-store"})


@letters_bp.post("/<int:letter_id>/delete")
@letter_module
@letter_required("letters_delete")
def delete(letter_id):
    db = get_db()
    letter = _visible_letter_or_404(db, letter_id)
    # The register row goes; the audit trail keeps the permanent record. Any
    # bell entry pointing at the letter goes too, so no link leads nowhere.
    clear_letter_notifications(db, letter_id)
    db.execute("DELETE FROM letter.letters WHERE id = ?", (letter_id,))
    db.commit()
    _log_letter(
        "DELETE", letter_id,
        "Deleted letter {}".format(letter["reference_no"]),
        **_letter_details(letter["letter_type"], letter["reference_no"],
                          letter["subject"], letter["sender"],
                          letter["recipient"], letter["letter_kind"]))
    flash("Letter {} deleted.".format(letter["reference_no"]), "success")
    return redirect(url_for("letters.dashboard"))


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(letters_bp)
