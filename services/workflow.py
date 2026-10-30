"""Approval policy, routing, steps and decisions."""

from core.access import current_user
from core.access import effective_permissions
from core.config import POSITION_STEP_LEVEL
from core.config import PR_DIRECTOR_LIMIT
from core.config import PR_MANAGER_LIMIT
from core.config import PR_ROUTE_AMOUNT
from core.config import PR_ROUTE_MODES
from core.config import PR_ROUTE_SEQUENTIAL
from core.config import STEP_LABEL
from core.db import get_db
from datetime import datetime
from datetime import timezone
import sqlite3


APPROVAL_STEPS = ("hr", "finance", "payroll", "inventory", "procurement",
                  "manager", "director", "ceo", "management")


APPROVAL_DEPARTMENTS = {
    "hr": "hr",
    "finance": "finance",
    "payroll": "payroll",
    "inventory": "inventory",
    "procurement": "procurement",
    "production": "management",
    "sales": "management",
    "general": "management",
}


REQUEST_TYPES = ("Purchase", "Expense", "Leave", "Stock Adjustment",
                 "Payroll", "Project", "Other")


APPROVAL_TWO_STEPS = PR_MANAGER_LIMIT


APPROVAL_THREE_STEPS = PR_DIRECTOR_LIMIT


LEGACY_APPROVAL_TWO_STEPS = 25000


LEGACY_APPROVAL_THREE_STEPS = 100000


MANAGEMENT_POLICY_DDL = """
    CREATE TABLE IF NOT EXISTS management_approval_policy (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        manager_limit REAL NOT NULL,
        director_limit REAL NOT NULL,
        updated_by TEXT NOT NULL DEFAULT 'system',
        updated_at TEXT
    )
"""


APPROVAL_ROUTES_DDL = """
    CREATE TABLE IF NOT EXISTS approval_routes (
        department TEXT PRIMARY KEY,
        first_step TEXT NOT NULL,
        updated_at TEXT
    )
"""


def account_position_level(account, db=None):
    """The HR position level an account carries, or None when it has no seat.

    The account's own position wins. A linked employee row is the fallback, so a
    position set in HR answers an approval desk before the account is assigned.
    """
    if account is None:
        return None
    owns = db is None
    if owns:
        db = get_db()

    def read(sql, value):
        try:
            row = db.execute(sql, (value,)).fetchone()
        except sqlite3.Error:
            return None
        return int(row["level"]) if row and row["level"] is not None else None

    keys = set(account.keys())
    if "position_id" in keys and account["position_id"]:
        level = read("SELECT level FROM organizational_positions WHERE id = ?",
                     account["position_id"])
        if level is not None:
            return level
    if "employee_id" in keys and account["employee_id"]:
        return read("""
            SELECT p.level FROM employees AS e
            JOIN organizational_positions AS p ON p.id = e.position_id
            WHERE e.id = ?
        """, account["employee_id"])
    return None


# Only these seats carry a signing rung of their own. An Executive (level 1)
# outranks Directors on the chart but no ladder ends at that seat, so it signs
# nothing by itself; Staff (level 4) never signs.
APPROVER_DESK_LABEL = {3: "Manager", 2: "Director", 0: "CEO"}


def approval_position_level(account, db=None):
    """The signing level the account's seat carries, or None for non-signing seats."""
    level = account_position_level(account, db)
    return level if level in APPROVER_DESK_LABEL else None


def position_grants_approval(account, db=None):
    """Whether the account's position activates approver rights by itself.

    Read straight from the org seat at request time, so the grant follows every
    place a position is set - hierarchy, HR employee form, imports - with no
    stored row to drift out of sync, and nothing a permission-table edit can fake.
    """
    return approval_position_level(account, db) is not None


def approver_desk_label(account, db=None):
    """Which desk the position signs, for screens and notices."""
    level = approval_position_level(account, db)
    return APPROVER_DESK_LABEL.get(level) if level is not None else None


def init_management_policy(db=None):
    """Create the policy row, seeded to the two constants above."""
    owns = db is None
    if owns:
        db = get_db()
    db.execute(MANAGEMENT_POLICY_DDL)
    if db.execute("SELECT id FROM management_approval_policy WHERE id = 1").fetchone() is None:
        db.execute(
            "INSERT INTO management_approval_policy (id, manager_limit, director_limit)"
            " VALUES (1, ?, ?)",
            (float(APPROVAL_TWO_STEPS), float(APPROVAL_THREE_STEPS)))
        if owns:
            db.commit()


def migrate_approval_policy(db=None):
    """Carry the requisition bands and the routing policy an admin can switch.

    The column is added without a CHECK because SQLite cannot add a constrained
    column, so the view validates the mode instead. The two numbers are retuned to
    the requisition bands only while the row is still the untouched seed - once an
    administrator has set their own limits, nothing here overwrites them.
    """
    owns = db is None
    if owns:
        db = get_db()
    init_management_policy(db)
    columns = {row[1] for row in db.execute(
        "PRAGMA table_info(management_approval_policy)").fetchall()}
    if "pr_route_mode" not in columns:
        db.execute("ALTER TABLE management_approval_policy"
                   " ADD COLUMN pr_route_mode TEXT NOT NULL DEFAULT 'amount'")
    untouched = db.execute("""
        SELECT manager_limit, director_limit FROM management_approval_policy
        WHERE id = 1 AND updated_by = 'system' AND updated_at IS NULL
    """).fetchone()
    if untouched is not None and (
            float(untouched["manager_limit"]) == float(LEGACY_APPROVAL_TWO_STEPS)
            and float(untouched["director_limit"]) == float(LEGACY_APPROVAL_THREE_STEPS)):
        db.execute("UPDATE management_approval_policy"
                   " SET manager_limit = ?, director_limit = ? WHERE id = 1",
                   (float(PR_MANAGER_LIMIT), float(PR_DIRECTOR_LIMIT)))
    if owns:
        db.commit()


def pr_policy(db=None):
    """The requisition bands and the routing policy, with a forgiving read.

    A database that predates either column reads as the shipped defaults rather than
    raising, which is what lets the boot migrations and the harness call
    approval_path() before the tables exist.
    """
    owns = db is None
    if owns:
        db = get_db()
    try:
        row = db.execute("""
            SELECT manager_limit, director_limit,
                   COALESCE(pr_route_mode, 'amount') AS pr_route_mode
            FROM management_approval_policy WHERE id = 1
        """).fetchone()
    except sqlite3.Error:
        row = None
    if row is None:
        return {"manager_limit": float(PR_MANAGER_LIMIT),
                "director_limit": float(PR_DIRECTOR_LIMIT),
                "pr_route_mode": PR_ROUTE_AMOUNT}
    mode = row["pr_route_mode"] if "pr_route_mode" in row.keys() else PR_ROUTE_AMOUNT
    if mode not in PR_ROUTE_MODES:
        mode = PR_ROUTE_AMOUNT
    return {"manager_limit": float(row["manager_limit"]),
            "director_limit": float(row["director_limit"]),
            "pr_route_mode": mode}


def pr_path(amount, policy=None):
    """The chain a requisition of this size has to clear.

    'amount' is the band rule: a Manager signs below the manager limit, a Director
    from there up to and including the director limit a Manager signs and then a
    Director does, and the CEO signs above it. 'sequential' is the other policy an
    organisation can choose, where every requisition walks the whole chain whatever
    its value.
    """
    policy = policy or pr_policy()
    if policy["pr_route_mode"] == PR_ROUTE_SEQUENTIAL:
        return ["manager", "director", "ceo"]
    if float(amount) > policy["director_limit"]:
        return ["ceo"]
    if float(amount) >= policy["manager_limit"]:
        return ["manager", "director"]
    return ["manager"]


def init_approval_routes(db=None):
    """Create the department routing table, seeded from APPROVAL_DEPARTMENTS."""
    owns = db is None
    if owns:
        db = get_db()
    db.execute(APPROVAL_ROUTES_DDL)
    if db.execute("SELECT COUNT(*) FROM approval_routes").fetchone()[0] == 0:
        for department, step in APPROVAL_DEPARTMENTS.items():
            db.execute("INSERT INTO approval_routes (department, first_step)"
                       " VALUES (?, ?)", (department, step))
        if owns:
            db.commit()


def get_approval_policy():
    """The policy row. A database that predates the table reads as the seed."""
    try:
        row = get_db().execute("""
            SELECT manager_limit, director_limit, updated_by, updated_at,
                   COALESCE(pr_route_mode, 'amount') AS pr_route_mode
            FROM management_approval_policy WHERE id = 1
        """).fetchone()
    except sqlite3.Error:
        row = None
    if row is None:
        return {"manager_limit": float(APPROVAL_TWO_STEPS),
                "director_limit": float(APPROVAL_THREE_STEPS),
                "updated_by": "system", "updated_at": None,
                "pr_route_mode": PR_ROUTE_AMOUNT}
    mode = row["pr_route_mode"] if "pr_route_mode" in row.keys() else PR_ROUTE_AMOUNT
    return {"manager_limit": float(row["manager_limit"]),
            "director_limit": float(row["director_limit"]),
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
            "pr_route_mode": mode or PR_ROUTE_AMOUNT}


def approval_route_map():
    """department -> step that opens the chain, from the routes table."""
    routes = dict(APPROVAL_DEPARTMENTS)
    try:
        rows = get_db().execute(
            "SELECT department, first_step FROM approval_routes").fetchall()
    except sqlite3.Error:
        return routes
    for row in rows:
        if row["first_step"] in APPROVAL_STEPS:
            routes[row["department"]] = row["first_step"]
    return routes


def approval_limits():
    """The live thresholds.

    approval_path() is also reached from code that runs outside a request - the
    boot migrations and the harness - where there is no connection to read, so
    fall back to the seed values rather than raising.
    """
    try:
        policy = get_approval_policy()
    except (AttributeError, RuntimeError):
        return float(APPROVAL_TWO_STEPS), float(APPROVAL_THREE_STEPS)
    return policy["manager_limit"], policy["director_limit"]


def approval_path(department, amount, request_type=""):
    """The ordered steps a request of this size has to clear.

    A requisition follows the leadership chain: Manager, Director or CEO by amount,
    or all three in order when the organisation has chosen that policy. Both the
    limits
    and the policy are read from the policy tables, so an administrator retunes the
    chain on /management/approval-settings instead of in Python.

    Any other request type keeps the module chain it has always had: the
    department's own desk first, then Finance, then Management as the amount rises.
    A request that is already in flight carries the chain it was opened with, which
    is what keeps a mid-flight retune from moving a stage nobody signed.
    """
    if (request_type or "") == "Purchase":
        return pr_path(amount)

    two_step, three_step = approval_limits()
    first = approval_route_map().get(department, "management")
    if amount >= three_step:
        steps = [first, "finance", "management"]
    elif amount >= two_step:
        steps = [first, "finance"]
    else:
        steps = [first]

    path = []
    for step in steps:
        if step not in path:
            path.append(step)
    return path


def holds_step(user, step, db=None):
    if user is None:
        return False
    ceiling = POSITION_STEP_LEVEL.get(step)
    if ceiling is not None:
        # an administrator keeps answering the Management and CEO desks so a
        # database whose accounts have no positions yet still closes its chains
        if step in ("management", "ceo") and user["role"] == "admin":
            return True
        level = account_position_level(user, db)
        return level is not None and level <= ceiling
    return step in effective_permissions(user)


def step_holder_names(step, db=None, exclude_id=None):
    """Who answers this desk right now, in the order the org lists them."""
    owns = db is None
    if owns:
        db = get_db()
    rows = db.execute(
        "SELECT * FROM users WHERE status = 'Active' ORDER BY full_name").fetchall()
    return [row["full_name"] for row in rows
            if row["id"] != exclude_id and holds_step(row, step, db)]


def step_holders(step):
    """How many active accounts can sign at this desk right now."""
    rows = get_db().execute(
        "SELECT * FROM users WHERE status = 'Active'").fetchall()
    return sum(1 for row in rows if holds_step(row, step))


def approval_acted_ids(db, user_id):
    return {row["request_id"] for row in db.execute(
        "SELECT DISTINCT request_id FROM approval_actions WHERE actor_id = ?", (user_id,)
    )}


def can_view_request(row, user, acted):
    return (user["role"] == "admin" or row["created_by"] == user["id"]
            or row["id"] in acted
            or (row["status"] == "Pending"
                and holds_step(user, row["current_approver_step"])))


def can_decide_request(row, user):
    return (row["status"] == "Pending" and row["created_by"] != user["id"]
            and holds_step(user, row["current_approver_step"]))


def add_approval_action(db, request_id, action, comments="", step=None,
                        previous_status=None):
    """One row per decision: who, what, when, what they said, and from where.

    The stage and the status the request was in when it moved are recorded so the
    trail answers "who signed which stage" rather than only "who did something".
    """
    user = current_user()
    db.execute("""
        INSERT INTO approval_actions (
            request_id, actor_id, actor_name, actor_role, action, comments, action_at,
            step, previous_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        request_id,
        user["id"] if user is not None else None,
        user["full_name"] if user is not None else "Unknown",
        user["role"] if user is not None else "anonymous",
        action,
        comments.strip(),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        step,
        previous_status,
    ))


def already_signed(db, request_id, user_id, step):
    """Has this account already answered this exact stage?

    A second click on the same stage is a duplicate approval whatever the button on
    the screen says, so it is refused from the trail rather than from the status.
    Only the decisions of the chain's current run count: once a request has been sent
    back and resubmitted the desk has to answer the revised figures again, and a
    refusal there would strand the request on the stage it is already at.
    """
    return db.execute("""
        SELECT id FROM approval_actions
        WHERE request_id = ? AND actor_id = ? AND step = ?
          AND action IN ('Approved', 'Step approved', 'Rejected', 'Returned')
          AND id > COALESCE((SELECT MAX(id) FROM approval_actions
                             WHERE request_id = ? AND action = 'Resubmitted'), 0)
        LIMIT 1
    """, (request_id, user_id, step, request_id)).fetchone() is not None


CHAIN_STATE_TEXT = {
    "signed": "Signed",
    "rejected": "Rejected",
    "returned": "Returned",
    "waiting": "Waiting for a signature",
    "idle": "Next in line",
}


def chain_trail(db, request, path):
    """Who signs each rung of a request's chain, and where it stands.

    The chain is the one the request was opened with, so a retuned policy cannot
    redraw a route somebody already signed. Only decisions from the current run
    count: once a request is sent back and resubmitted its desks have to answer
    the revised figures again. A rung still waiting is listed together with the
    active accounts that hold it, which is what tells a reader who the next
    signature belongs to.
    """
    rows = db.execute("""
        SELECT * FROM approval_actions WHERE request_id = ? ORDER BY id ASC
    """, (request["id"],)).fetchall()
    cutoff = 0
    for row in rows:
        if row["action"] == "Resubmitted":
            cutoff = row["id"]
    decisions = {}
    for row in rows:
        if row["id"] <= cutoff or not row["step"]:
            continue
        if row["action"] in ("Approved", "Step approved", "Rejected", "Returned"):
            decisions[row["step"]] = row
    current = request["current_approver_step"]
    pending = request["status"] == "Pending"
    trail = []
    for step in path:
        decision = decisions.get(step)
        if decision is not None:
            state = ("rejected" if decision["action"] == "Rejected"
                     else "returned" if decision["action"] == "Returned"
                     else "signed")
        else:
            state = "waiting" if pending and step == current else "idle"
        trail.append({
            "step": step,
            "label": STEP_LABEL.get(step, step),
            "state": state,
            "state_label": ("Not reached"
                            if state == "idle"
                            and request["status"] in ("Rejected", "Returned")
                            else CHAIN_STATE_TEXT[state]),
            "actor_name": decision["actor_name"] if decision is not None else "",
            "actor_role": decision["actor_role"] if decision is not None else "",
            "decided_at": decision["action_at"] if decision is not None else None,
            "comments": decision["comments"] if decision is not None else "",
            "current": state == "waiting",
            # The requester cannot sign their own requisition, so their name is
            # not offered as the hand that will close the desk.
            "holders": (step_holder_names(step, db,
                                          exclude_id=request["created_by"])
                        if decision is None and pending else []),
        })
    return trail
