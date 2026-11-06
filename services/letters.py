"""Letters and correspondence: schema, numbering and permission checks.

One register serves every department. Inside it a letter belongs to a category -
general correspondence (official, incoming, outgoing), internal memoranda, the
HR, Finance and Procurement series, contract and legal letters, and confidential
mail. An account reaches the register only through the letter actions an
administrator grants it, and only sees the categories it was granted or its role
and department normally file. Administrators hold everything; nothing is
inherited from a role without that default.
"""

from core.access import current_user
from core.access import account_role_keys
from core.db import get_db
from flask import abort
from flask import g
from flask import redirect
from flask import request
from flask import url_for
from functools import wraps
from letters_db import LETTER_TYPES
import sqlite3
from services.hr import hr_department_options


LETTER_TYPE_LABEL = {
    "official": "Official letter",
    "incoming": "Incoming correspondence",
    "outgoing": "Outgoing correspondence",
    "memo": "Internal memorandum",
    "hr": "HR & employee letter",
    "finance": "Finance letter",
    "procurement": "Procurement & supplier letter",
    "legal": "Contract & legal letter",
    "confidential": "Confidential letter",
}


LETTER_TYPE_HINT = {
    "official": "General company letters, addressed to an account or a department.",
    "incoming": "Mail received from outside and registered at the front desk.",
    "outgoing": "Mail leaving the company to an external addressee.",
    "memo": "Internal memoranda, read across the departments a memo is routed to.",
    "hr": "Appointment, promotion, transfer, leave, warning and other staff letters.",
    "finance": "Payment requests, financial correspondence and invoice clarification.",
    "procurement": "Supplier correspondence, quotations, purchase orders and delivery letters.",
    "legal": "Contracts and legal notices; never granted by department, only by name.",
    "confidential": "Sensitive letters; only the accounts ticked here may open them.",
}


LETTER_STATUSES = ("Draft", "Pending approval", "Approved", "Registered",
                   "Sent", "Received", "Archived")


# Every letter carries the house series, LDB00001 upwards, whatever its category.
LETTER_REFERENCE_PREFIX = "LDB"


# The narrower subject each operational category carries. General correspondence
# has no second level, so the form shows only the category for those.
LETTER_KINDS = {
    "hr": ("appointment", "promotion", "transfer", "leave", "warning", "other"),
    "finance": ("payment-request", "financial-correspondence",
                "invoice-clarification", "other"),
    "procurement": ("supplier-correspondence", "quotation-request",
                    "purchase-order", "delivery", "other"),
    "legal": ("contract", "legal-notice", "other"),
}


LETTER_KIND_LABEL = {
    "hr": {"appointment": "Appointment", "promotion": "Promotion",
           "transfer": "Transfer", "leave": "Leave",
           "warning": "Warning", "other": "Other staff letter"},
    "finance": {"payment-request": "Payment request",
                "financial-correspondence": "Financial correspondence",
                "invoice-clarification": "Invoice clarification",
                "other": "Other finance letter"},
    "procurement": {"supplier-correspondence": "Supplier correspondence",
                    "quotation-request": "Quotation request",
                    "purchase-order": "Purchase order",
                    "delivery": "Delivery letter",
                    "other": "Other supplier letter"},
    "legal": {"contract": "Contract", "legal-notice": "Legal notice",
              "other": "Other legal letter"},
}


# Roles that keep an eye on the whole register rather than only their own mail.
LETTER_OVERSIGHT_ROLES = ("admin", "auditor")


# Which addressee forms each letter type accepts. Mail that leaves the company
# carries a free-text recipient; everything else is routed internally to an
# account or a department.
LETTER_RECIPIENT_MODES = {
    "official": ("user", "department"),
    "incoming": ("user", "department"),
    "memo": ("user", "department"),
    "hr": ("user", "department"),
    "finance": ("user", "department"),
    "procurement": ("user", "department"),
    "confidential": ("user", "department"),
    "outgoing": ("external",),
    "legal": ("external",),
}


# Types that never become an inbox delivery: the addressee is outside.
LETTER_EXTERNAL_TYPES = ("outgoing", "legal")


# Contract and confidential mail is released only once an approver has signed
# it off; the other categories can be sent straight from a draft.
LETTER_APPROVAL_REQUIRED = ("legal", "confidential")


# An official letter can carry a three-rung sign-off ladder: the author files
# it, then the Manager signs, then the Director, then the CEO. Each rung is held
# by an account whose own HR position sits at that exact level, and a rung opens
# only when the one below it has signed.
LETTER_APPROVAL_LADDER = ("manager", "director", "ceo")


LETTER_APPROVAL_STEP_LEVEL = {"manager": 3, "director": 2, "ceo": 0}


LETTER_APPROVAL_STEP_LABEL = {"manager": "Level 1 · Manager",
                              "director": "Level 2 · Director",
                              "ceo": "Level 3 · CEO"}


# How far up the ladder the author wants the letter signed. Level 1 stops at
# the Manager, level 2 adds the Director, level 3 runs to the CEO.
LETTER_APPROVAL_LEVELS = (1, 2, 3)


LETTER_APPROVAL_LEVEL_LABEL = {
    1: "Level 1 · up to Manager",
    2: "Level 2 · up to Director",
    3: "Level 3 · up to CEO",
}


LETTER_PENDING_STATUS = "Pending approval"


LETTER_STEP_APPROVED = "Approved"
LETTER_STEP_RETURNED = "Returned"
LETTER_STEP_PENDING = "Pending"


LETTER_OPENING_STATUS = {letter_type: "Draft" for letter_type in LETTER_TYPES}
LETTER_OPENING_STATUS["incoming"] = "Received"


# Categories every account that can open the register may file. The operational
# series follow the role and department below, and the two restricted kinds are
# granted by name only.
LETTER_GENERAL_CATEGORIES = ("official", "incoming", "outgoing", "memo")


LETTER_CATEGORY_ROLE = {"hr": "hr", "finance": "finance",
                        "procurement": "procurement"}


LETTER_CATEGORY_DEPARTMENT = {
    "hr": ("hr", "human resource", "personnel"),
    "finance": ("finance", "account"),
    "procurement": ("procurement", "purchas", "supply"),
}


LETTER_PERMISSIONS = ("letters_view", "letters_create", "letters_edit",
                      "letters_send", "letters_approve", "letters_print",
                      "letters_download", "letters_delete")


LETTER_PERMISSION_LABEL = {
    "letters_view": "View letters",
    "letters_create": "Create and register letters",
    "letters_edit": "Edit letters",
    "letters_send": "Send letters",
    "letters_approve": "Approve letters",
    "letters_print": "Print letters",
    "letters_download": "Download letters",
    "letters_delete": "Delete letters",
}


LETTER_PERMISSION_NOTE = {
    "letters_view": "Open the register, read a letter and see its audit trail.",
    "letters_create": "Write letters in the categories this account may file.",
    "letters_edit": "Change the details and body of a letter that is still a draft.",
    "letters_send": "Send or register a letter and hand it to its addressees.",
    "letters_approve": "Sign off a draft; contract and confidential mail needs it before sending.",
    "letters_print": "Print a letter on the company letterhead; each print is logged.",
    "letters_download": "Download the letter as a PDF; each download is logged.",
    "letters_delete": "Remove a letter from the register; the audit trail keeps the record.",
}


# An administrator holds every action. Everyone else is granted letter actions by
# name, either on the Letter permissions screen for one account or in the role
# dialog for every account that carries a role. An account with no grant of either
# kind reads the register only when its role carries the Letters module tick.
LETTER_ROLE_BASELINE = frozenset(("letters_view",))

# Every action is taken from inside the register, so a grant that names an action
# without the right to open it would strand it: saving a role always carries this
# along, exactly as the hiring desk carries its two read rights.
LETTER_VIEW_BASE = frozenset(("letters_view",))


def letter_type_label(letter_type):
    return LETTER_TYPE_LABEL.get(letter_type, (letter_type or "").title())


def letter_kind_label(letter_type, kind):
    if not kind:
        return ""
    return LETTER_KIND_LABEL.get(letter_type, {}).get(
        kind, kind.replace("-", " ").title())


def stored_letter_permissions(db, user_id):
    """(has_overrides, granted) from the per-account table alone."""
    rows = db.execute(
        "SELECT permission, allowed FROM letter.letter_permissions WHERE user_id = ?",
        (user_id,)).fetchall()
    if not rows:
        return False, set()
    return True, {row["permission"] for row in rows if row["allowed"]}


def stored_letter_role_permissions(db, role_key):
    """(has_rows, granted) for one role's letter matrix."""
    rows = db.execute(
        "SELECT permission, allowed FROM letter.letter_role_permissions"
        " WHERE role_key = ?", (role_key,)).fetchall()
    if not rows:
        return False, set()
    return True, {row["permission"] for row in rows
                  if row["allowed"] and row["permission"] in LETTER_PERMISSIONS}


def letter_role_grants(db, role_keys):
    """Letter actions every role this account carries grants by name."""
    held = set()
    for role_key in role_keys:
        if not role_key:
            continue
        try:
            has_rows, granted = stored_letter_role_permissions(db, role_key)
        except sqlite3.Error:
            continue
        if has_rows:
            held |= granted
    return held


def letter_permission_set(user):
    """Every letter action this account holds.

    An administrator holds all of them. Everyone else holds exactly what was
    granted on the Letter permissions screen for that one account. With no
    per-account rows the role matrices apply: the Letters module tick alone
    opens the register, and any action a carried role names is added to it.
    """
    if user is None:
        return frozenset()
    if user["role"] == "admin":
        return frozenset(LETTER_PERMISSIONS)
    cache = g.get("_letter_permission_cache")
    if cache is None:
        cache = {}
        g._letter_permission_cache = cache
    if user["id"] in cache:
        return cache[user["id"]]

    db = get_db()
    try:
        stored = db.execute(
            "SELECT permission, allowed FROM letter.letter_permissions WHERE user_id = ?",
            (user["id"],)).fetchall()
    except sqlite3.Error:
        stored = ()
    if stored:
        held = {row["permission"] for row in stored
                if row["allowed"] and row["permission"] in LETTER_PERMISSIONS}
    else:
        from core.access import effective_permissions
        held = (set(LETTER_ROLE_BASELINE)
                if "letters" in effective_permissions(user) else set())
        held |= letter_role_grants(db, account_role_keys(db, user))
    # A Manager, Director or CEO seat may sign ladders by itself; derived from
    # the position so the right follows the seat rather than a stored row.
    from services.workflow import position_grants_approval
    if position_grants_approval(user, db):
        held.add("letters_approve")

    result = frozenset(held)
    cache[user["id"]] = result
    return result


def has_letter_permission(permission, user=None):
    if permission not in LETTER_PERMISSIONS:
        return False
    account = current_user() if user is None else user
    if account is None:
        return False
    return permission in letter_permission_set(account)


def letter_module_visible(user):
    """True when the account may open the letters register at all."""
    try:
        return has_letter_permission("letters_view", user)
    except sqlite3.Error:
        return False


def letter_required(permission):
    """Protect one letter action; @letter_module guards the module itself."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login", next=request.path))
            if not has_letter_permission(permission, user):
                from services.audit import log_activity
                log_activity("DENY", "letters",
                             "Refused {} on {}".format(request.method, request.path),
                             outcome="DENIED",
                             details={"permission": permission,
                                      "endpoint": request.endpoint})
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def letter_module(view):
    """Open the register only to an account that was granted letter access.

    The grant is the account's own, so a role's module tick is not needed and a
    role without one is not enough on its own.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login", next=request.path))
        if not letter_module_visible(user):
            from services.audit import log_activity
            log_activity("DENY", "letters",
                         "Refused {} on {}".format(request.method, request.path),
                         outcome="DENIED",
                         details={"permission": "letters_view",
                                  "endpoint": request.endpoint})
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def new_letter_reference(db):
    """The next reference in the house series: LDB00001, LDB00002, ..."""
    highest = 0
    for (reference,) in db.execute("SELECT reference_no FROM letter.letters").fetchall():
        reference = reference or ""
        suffix = reference[len(LETTER_REFERENCE_PREFIX):]
        if reference.startswith(LETTER_REFERENCE_PREFIX) and suffix.isdigit():
            highest = max(highest, int(suffix))
    return "{}{:05d}".format(LETTER_REFERENCE_PREFIX, highest + 1)


# ------------------------------------------------------------ master data
def department_for_account(db, user):
    """The account's department from HR master data, or None.

    The account assignment wins; an account that only has an employee record
    still inherits that employee's department.
    """
    if user is None:
        return None
    keys = user.keys()
    department_id = user["department_id"] if "department_id" in keys else None
    if department_id:
        row = db.execute(
            "SELECT id, name FROM departments WHERE id = ? AND is_active = 1",
            (department_id,)).fetchone()
        if row is not None:
            return row
    employee_id = user["employee_id"] if "employee_id" in keys else None
    if employee_id:
        employee = db.execute(
            "SELECT department FROM employees WHERE id = ?"
            " AND employment_status <> 'Inactive'", (employee_id,)).fetchone()
    else:
        employee = db.execute(
            "SELECT department FROM employees WHERE user_id = ?"
            " AND employment_status <> 'Inactive' ORDER BY id LIMIT 1",
            (user["id"],)).fetchone()
    name = (employee["department"] if employee is not None else "") or ""
    if not name.strip():
        return None
    return db.execute(
        "SELECT id, name FROM departments WHERE name = ? COLLATE NOCASE"
        " AND is_active = 1", (name.strip(),)).fetchone()


def active_account(db, user_id):
    """An active sign-in account, or None; the addressee picker's check."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.execute(
        "SELECT id, full_name, department_id FROM users"
        " WHERE id = ? AND status = 'Active'", (user_id,)).fetchone()


def active_department(db, department_id):
    try:
        department_id = int(department_id)
    except (TypeError, ValueError):
        return None
    return db.execute(
        "SELECT id, name FROM departments WHERE id = ? AND is_active = 1",
        (department_id,)).fetchone()


def letter_recipient_options(db):
    """The addressees a letter may be routed to: active accounts and departments."""
    accounts = db.execute("""
        SELECT u.id, u.full_name, u.role, u.department_id,
               d.name AS department_name
        FROM users AS u
        LEFT JOIN departments AS d ON d.id = u.department_id
        WHERE u.status = 'Active'
        ORDER BY u.full_name COLLATE NOCASE
    """).fetchall()
    departments = hr_department_options(db, active_only=True)
    return accounts, departments


def account_label(account, department_name=None):
    name = (account["full_name"] or "").strip()
    if department_name:
        return "{} — {}".format(name, department_name)
    return name


def department_member_ids(db, department_id, category=None):
    """Accounts that belong to a department, for notifications and checks.

    With a category, only members who may open that kind of letter are listed,
    so a notification never points at a letter its reader cannot see.
    """
    members = []
    for account in db.execute(
            "SELECT * FROM users WHERE status = 'Active'").fetchall():
        department = department_for_account(db, account)
        if department is None or department["id"] != department_id:
            continue
        if category is not None and category not in letter_category_set(account):
            continue
        members.append(account["id"])
    return members


# ------------------------------------------------------------ categories
def _default_categories(db, user):
    """The categories this account's role and department normally file."""
    held = set(LETTER_GENERAL_CATEGORIES)
    role = (user["role"] or "").strip()
    for category, role_key in LETTER_CATEGORY_ROLE.items():
        if role == role_key:
            held.add(category)
    department = department_for_account(db, user)
    department_name = (department["name"] if department is not None else "").lower()
    for category, tokens in LETTER_CATEGORY_DEPARTMENT.items():
        if any(token in department_name for token in tokens):
            held.add(category)
    return held


def stored_letter_categories(db, user_id):
    """(has_overrides, granted) from the category table alone."""
    rows = db.execute(
        "SELECT category, allowed FROM letter.letter_category_permissions"
        " WHERE user_id = ?", (user_id,)).fetchall()
    if not rows:
        return False, set()
    return True, {row["category"] for row in rows
                  if row["allowed"] and row["category"] in LETTER_TYPES}


def letter_category_set(user):
    """Every letter category this account may open.

    An administrator holds all of them. Everyone else holds exactly what was
    granted on the Letter permissions screen; an account with no category grants
    yet falls back to the general series plus its role's own series. Contract and
    confidential mail is never part of that fallback: it is granted by name.
    """
    if user is None:
        return frozenset()
    if user["role"] == "admin":
        return frozenset(LETTER_TYPES)
    cache = g.get("_letter_category_cache")
    if cache is None:
        cache = {}
        g._letter_category_cache = cache
    if user["id"] in cache:
        return cache[user["id"]]

    db = get_db()
    try:
        stored = db.execute(
            "SELECT category, allowed FROM letter.letter_category_permissions"
            " WHERE user_id = ?", (user["id"],)).fetchall()
    except sqlite3.Error:
        stored = ()
    if stored:
        held = {row["category"] for row in stored
                if row["allowed"] and row["category"] in LETTER_TYPES}
    else:
        held = _default_categories(db, user)

    result = frozenset(held)
    cache[user["id"]] = result
    return result


def letter_category_access(user, category):
    if category not in LETTER_TYPES:
        return False
    return category in letter_category_set(user)


def letter_category_access_for_id(db, user_id, category):
    """Whether another account may open that category; the addressee check."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    account = db.execute(
        "SELECT * FROM users WHERE id = ? AND status = 'Active'",
        (user_id,)).fetchone()
    if account is None:
        return False
    return letter_category_access(account, category)


def letter_allowed_types(user):
    """The categories this account may file, in dropdown order."""
    held = letter_category_set(user)
    return tuple(value for value in LETTER_TYPES if value in held)


def letter_category_in_sql(user, alias="l"):
    """(sql, params) that narrows a letters query to the account's categories."""
    categories = sorted(letter_category_set(user))
    if not categories:
        return "0 = 1", ()
    marks = ", ".join("?" for _ in categories)
    return "{}.letter_type IN ({})".format(alias, marks), tuple(categories)


# --------------------------------------------------------------- delivery
def add_delivery(db, letter_id, folder, user_id=None, department_id=None):
    """File one letter into an account's or a department's inbox or outbox."""
    if folder not in ("inbox", "outbox"):
        raise ValueError("folder must be inbox or outbox")
    if user_id:
        db.execute(
            "INSERT OR IGNORE INTO letter.letter_deliveries (letter_id, user_id, folder)"
            " VALUES (?, ?, ?)", (letter_id, user_id, folder))
    if department_id:
        db.execute(
            "INSERT OR IGNORE INTO letter.letter_deliveries"
            " (letter_id, department_id, folder) VALUES (?, ?, ?)",
            (letter_id, department_id, folder))


def clear_inbox_deliveries(db, letter_id):
    db.execute("DELETE FROM letter.letter_deliveries WHERE letter_id = ?"
               " AND folder = 'inbox'", (letter_id,))


def deliver_inbox(db, letter_id, user_id=None, department_id=None):
    add_delivery(db, letter_id, "inbox", user_id=user_id,
                 department_id=department_id)


def record_outbox(db, letter_id, sender_user_id):
    add_delivery(db, letter_id, "outbox", user_id=sender_user_id)


def mark_letter_read(db, letter_id, user_id):
    """Record the first open; True only on that first read."""
    cursor = db.execute("INSERT OR IGNORE INTO letter.letter_reads (letter_id, user_id)"
                        " VALUES (?, ?)", (letter_id, user_id))
    return cursor.rowcount == 1


def clear_letter_notifications(db, letter_id):
    """Drop every bell entry that points at a letter which no longer exists."""
    stem = "letter-{}".format(letter_id)
    db.execute("DELETE FROM notifications WHERE dedupe_key = ?"
               " OR dedupe_key LIKE ?", (stem, stem + "-%"))


def unread_letter_count(db, user):
    if user is None:
        return 0
    department = department_for_account(db, user)
    department_id = department["id"] if department else -1
    scope, scope_params = letter_category_in_sql(user, "l")
    return db.execute("""
        SELECT COUNT(*) FROM letter.letters AS l
        WHERE ({scope})
          AND EXISTS (
            SELECT 1 FROM letter.letter_deliveries AS d
            WHERE d.letter_id = l.id AND d.folder = 'inbox'
              AND (d.user_id = ? OR d.department_id = ?))
          AND NOT EXISTS (
            SELECT 1 FROM letter.letter_reads AS r
            WHERE r.letter_id = l.id AND r.user_id = ?)
    """.format(scope=scope),
        scope_params + (user["id"], department_id, user["id"])).fetchone()[0]


# --------------------------------------------------------- approval ladder
def letter_requires_ladder(letter_type, requires_approval=0):
    """Official mail can be held for the Manager, Director and CEO sign-off."""
    return letter_type == "official" and bool(int(requires_approval or 0))


def letter_needs_approval(letter):
    """Every sign-off a letter still owes before it may leave the building."""
    if letter["letter_type"] in LETTER_APPROVAL_REQUIRED:
        return True
    keys = letter.keys()
    return letter_requires_ladder(
        letter["letter_type"],
        letter["requires_approval"] if "requires_approval" in keys else 0)


def letter_approval_depth(letter):
    """How many rungs this letter walks: 1 Manager, 2 Director, 3 CEO."""
    keys = letter.keys()
    try:
        depth = int(letter["approval_depth"] or 0) if "approval_depth" in keys else 0
    except (TypeError, ValueError):
        depth = 0
    return depth if depth in LETTER_APPROVAL_LEVELS else len(LETTER_APPROVAL_LADDER)


def letter_approval_rungs(letter):
    """The rungs of this letter's route, in the order they sign."""
    return LETTER_APPROVAL_LADDER[:letter_approval_depth(letter)]


def letter_signable_steps(user, db=None):
    """The rungs this account may sign right now.

    A rung belongs to the exact position level it names - a Director signs the
    Director rung and nothing else, even though the approval chain of a
    requisition is more forgiving. An administrator keeps every rung so a
    database whose accounts have no seats yet still closes its ladders.
    """
    if user is None:
        return ()
    if user["role"] == "admin":
        return LETTER_APPROVAL_LADDER
    if "letters_approve" not in letter_permission_set(user):
        return ()
    cache = g.get("_letter_signable_cache")
    if cache is None:
        cache = {}
        g._letter_signable_cache = cache
    if user["id"] in cache:
        return cache[user["id"]]

    from services.workflow import account_position_level
    level = account_position_level(user, db)
    steps = tuple(step for step in LETTER_APPROVAL_LADDER
                  if level is not None
                  and LETTER_APPROVAL_STEP_LEVEL[step] == level)
    cache[user["id"]] = steps
    return steps


def letter_current_step(letter):
    """The rung waiting for a signature, or None when no ladder is running."""
    if letter["status"] != LETTER_PENDING_STATUS:
        return None
    keys = letter.keys()
    return letter["approval_step"] if "approval_step" in keys else None


def approval_step_holders(db, step):
    """Active accounts that hold this rung right now, for the bell entry."""
    return [account for account in
            db.execute("SELECT * FROM users WHERE status = 'Active'").fetchall()
            if step in letter_signable_steps(account, db)]


def approval_step_rows(db, letter_id):
    """The rungs of one letter in ladder order."""
    return db.execute(
        "SELECT * FROM letter.letter_approval_steps WHERE letter_id = ?"
        " ORDER BY position", (letter_id,)).fetchall()


def open_letter_ladder(db, letter_id, depth=3):
    """Start the ladder, or restart it after a return, on the Manager rung.

    Only the rungs the author asked for are created: a letter sent up to the
    Director closes there and never reaches the CEO.
    """
    rungs = LETTER_APPROVAL_LADDER[:depth if depth in LETTER_APPROVAL_LEVELS
                                   else len(LETTER_APPROVAL_LADDER)]
    db.execute("DELETE FROM letter.letter_approval_steps WHERE letter_id = ?",
               (letter_id,))
    for position, step in enumerate(rungs):
        db.execute(
            "INSERT INTO letter.letter_approval_steps (letter_id, step, position)"
            " VALUES (?, ?, ?)", (letter_id, step, position))
    db.execute("""
        UPDATE letter.letters SET status = ?, approval_step = ?,
            approval_depth = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (LETTER_PENDING_STATUS, rungs[0], len(rungs), letter_id))


def advance_letter_ladder(db, letter_id, step, user, comments=""):
    """Record one signature; open the next rung or approve the letter.

    Returns the rung that opens next, or None when the last rung of this
    letter's route has signed and it is Approved and ready to be sent.
    """
    actor_id = user["id"] if user is not None else None
    actor_name = user["full_name"] if user is not None else "Unknown"
    db.execute("""
        UPDATE letter.letter_approval_steps
        SET decision = ?, actor_id = ?, actor_name = ?, comments = ?,
            decided_at = CURRENT_TIMESTAMP
        WHERE letter_id = ? AND step = ?
    """, (LETTER_STEP_APPROVED, actor_id, actor_name, comments.strip(),
          letter_id, step))
    letter = db.execute("SELECT * FROM letter.letters WHERE id = ?",
                        (letter_id,)).fetchone()
    rungs = letter_approval_rungs(letter) if letter is not None \
        else LETTER_APPROVAL_LADDER
    index = LETTER_APPROVAL_LADDER.index(step)
    following = tuple(rung for rung in LETTER_APPROVAL_LADDER[index + 1:]
                      if rung in rungs)[:1]
    if following:
        db.execute("""
            UPDATE letter.letters SET approval_step = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (following[0], letter_id))
        return following[0]
    db.execute("""
        UPDATE letter.letters
        SET status = 'Approved', approval_step = NULL, approved_by = ?,
            approved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (actor_id, letter_id))
    return None


def return_letter_ladder(db, letter_id, step, user, comments=""):
    """Send the letter back to its author; it reopens as an editable draft."""
    actor_id = user["id"] if user is not None else None
    actor_name = user["full_name"] if user is not None else "Unknown"
    db.execute("""
        UPDATE letter.letter_approval_steps
        SET decision = ?, actor_id = ?, actor_name = ?, comments = ?,
            decided_at = CURRENT_TIMESTAMP
        WHERE letter_id = ? AND step = ?
    """, (LETTER_STEP_RETURNED, actor_id, actor_name, comments.strip(),
          letter_id, step))
    db.execute("""
        UPDATE letter.letters SET status = 'Draft', approval_step = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (letter_id,))


# ------------------------------------------------------------- visibility
def _letter_oversight(db, user):
    if user is None:
        return False
    if user["role"] in LETTER_OVERSIGHT_ROLES:
        return True
    return bool(set(account_role_keys(db, user)) & set(LETTER_OVERSIGHT_ROLES))


def letter_visibility_sql(db, user, alias="letters"):
    """(sql, params) that narrows a letters query to what this account may read.

    Oversight reads the whole register; everyone else reads the letters in the
    categories they hold that they sent, received, or that were routed to their
    department.
    """
    if user is None:
        return "0 = 1", ()
    if _letter_oversight(db, user):
        return "", ()
    categories = sorted(letter_category_set(user))
    if not categories:
        return "0 = 1", ()
    department = department_for_account(db, user)
    department_id = department["id"] if department else -1
    user_id = user["id"]
    marks = ", ".join("?" for _ in categories)
    # Every desk the letter's route passes through keeps reading it, so a
    # signature request does not dead-end once the route moves past its rung.
    signable = letter_signable_steps(user, db)
    route_sql, route_params = "", ()
    if signable:
        route_sql = (" OR EXISTS (SELECT 1 FROM letter.letter_approval_steps AS lr"
                     " WHERE lr.letter_id = {a}.id AND lr.step IN ({}))"
                     .format(", ".join("?" for _ in signable), a=alias))
        route_params = tuple(signable)
    sql = ("{a}.letter_type IN ({marks})"
           " AND ({a}.created_by = ? OR {a}.sender_user_id = ?"
           " OR {a}.recipient_user_id = ? OR {a}.sender_department_id = ?"
           " OR {a}.recipient_department_id = ?"
           " OR EXISTS (SELECT 1 FROM letter.letter_deliveries AS ld"
           " WHERE ld.letter_id = {a}.id"
           " AND (ld.user_id = ? OR ld.department_id = ?))"
           " OR EXISTS (SELECT 1 FROM letter.letter_approval_steps AS ls"
           " WHERE ls.letter_id = {a}.id AND ls.actor_id = ?)"
           "{route})").format(a=alias, marks=marks, route=route_sql)
    return sql, tuple(categories) + (
        user_id, user_id, user_id, department_id, department_id, user_id,
        department_id, user_id) + route_params


def letter_visible_to(db, letter, user):
    """Can this account open that letter?"""
    if user is None:
        return False
    if _letter_oversight(db, user):
        return True
    if letter["letter_type"] not in letter_category_set(user):
        return False
    user_id = user["id"]
    if user_id in (letter["created_by"], letter["sender_user_id"],
                   letter["recipient_user_id"]):
        return True
    department = department_for_account(db, user)
    department_id = department["id"] if department else -1
    if department_id in (letter["sender_department_id"],
                         letter["recipient_department_id"]):
        return True
    signable = letter_signable_steps(user, db)
    if signable and db.execute(
            "SELECT 1 FROM letter.letter_approval_steps WHERE letter_id = ?"
            " AND step IN ({})".format(", ".join("?" for _ in signable)),
            (letter["id"],) + tuple(signable)).fetchone() is not None:
        # A rung of this letter's route is this account's to sign - or was,
        # before the route moved on - so the letter stays readable to that desk.
        return True
    if db.execute("SELECT 1 FROM letter.letter_approval_steps WHERE letter_id = ?"
                  " AND actor_id = ?",
                  (letter["id"], user_id)).fetchone() is not None:
        return True
    return db.execute(
        "SELECT 1 FROM letter.letter_deliveries WHERE letter_id = ?"
        " AND (user_id = ? OR department_id = ?)",
        (letter["id"], user_id, department_id)).fetchone() is not None
