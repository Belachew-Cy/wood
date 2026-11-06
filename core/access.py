"""Sessions, permissions, CSRF, login throttling and request hooks."""

from core.config import COARSE_PERMISSION_COVERAGE
from core.config import DASHBOARD_ENDPOINT
from core.config import DASHBOARD_ROLES
from core.config import DEPARTMENT_ACTIONS
from core.config import DEPARTMENT_PERMISSION_BY_KEY
from core.config import DEPARTMENT_PERMISSION_KEYS
from core.config import DEPARTMENT_SCOPES
from core.config import MANAGE_PREFIXES
from core.config import MODULES
from core.config import MODULE_DASHBOARDS
from core.config import PERMISSION_BY_KEY
from core.config import PERMISSION_HIRING_RIGHTS
from core.config import PERMISSION_KEYS
from core.config import ROLE_LABEL
from core.config import ROLE_PERMISSIONS
from core.config import SIDEBAR_GROUP_ICONS
from core.config import SIDEBAR_NAV
from core.config import SIDEBAR_PARENT
from core.config import STAFF_GROUP_ROLES
from core.config import SUPER_ADMIN_NAV
from core.config import USER_ROLES
from core.config import default_role_permissions
from core.config import department_permission_key
from core.config import permission_closure
from core.db import ROLES_DDL
from core.db import ROLE_PERMISSIONS_DDL
from core.db import USER_ROLES_DDL
from core.db import DEPARTMENT_ROLES_DDL
from core.db import get_db
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from flask import abort
from flask import current_app
from flask import flash
from flask import g
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from functools import wraps
from markupsafe import Markup
import re
import secrets
import sqlite3


def init_roles(db):
    """Seed the role catalogue and per-role modules.

    Existing built-in roles keep whatever an administrator has changed; a role
    that is new to the database is seeded from ROLE_PERMISSIONS once. Custom
    roles are never touched by this migration.
    """
    db.execute(ROLES_DDL)
    db.execute(ROLE_PERMISSIONS_DDL)
    db.execute(USER_ROLES_DDL)
    db.execute(DEPARTMENT_ROLES_DDL)
    db.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_user"
               " ON user_roles(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_department_roles_unit"
               " ON department_roles (department_id)")
    # Roles gained an activation switch and a owning department after the first
    # databases were built. A parked role keeps its configured rights but
    # contributes none of them; the department groups the role on the console and
    # never widens its reach by itself.
    for column, definition in (
            ("active", "INTEGER NOT NULL DEFAULT 1"),
            ("department_id", "INTEGER NOT NULL DEFAULT 0"),
            ("is_standard", "INTEGER NOT NULL DEFAULT 0")):
        try:
            db.execute("SELECT {} FROM roles LIMIT 1".format(column))
        except sqlite3.Error:
            db.execute("ALTER TABLE roles ADD COLUMN {} {}".format(
                column, definition))
    for key in USER_ROLES:
        row = db.execute("SELECT key FROM roles WHERE key = ?", (key,)).fetchone()
        if row is None:
            db.execute("""
                INSERT INTO roles (key, name, description, landing, is_system)
                VALUES (?, ?, ?, ?, 1)
            """, (key, ROLE_LABEL.get(key, key.title()),
                  "Built-in {} role".format(ROLE_LABEL.get(key, key)),
                  DASHBOARD_ENDPOINT.get(key, "dashboard")))
            for module in ROLE_PERMISSIONS.get(key, ()):
                db.execute("INSERT INTO role_permissions (role_key, module)"
                           " VALUES (?, ?)", (key, module))
        elif not db.execute("SELECT 1 FROM role_permissions WHERE role_key = ?",
                            (key,)).fetchone():
            for module in ROLE_PERMISSIONS.get(key, ()):
                db.execute("INSERT INTO role_permissions (role_key, module)"
                           " VALUES (?, ?)", (key, module))


def role_catalogue(db=None):
    """Every role, system and custom, newest label rules included."""
    db = db or get_db()
    try:
        return db.execute("""
            SELECT r.*,
                   (SELECT COUNT(*) FROM role_permissions p
                     WHERE p.role_key = r.key) AS module_count,
                   (SELECT COUNT(*) FROM user_roles ur
                     WHERE ur.role_key = r.key) AS extra_holders
            FROM roles AS r
            ORDER BY r.is_system DESC,
                     CASE r.key WHEN 'admin' THEN 0 WHEN 'hr' THEN 1
                                WHEN 'finance' THEN 2 WHEN 'budget' THEN 3
                                WHEN 'procurement' THEN 4 WHEN 'payroll' THEN 5
                                WHEN 'inventory' THEN 6 WHEN 'auditor' THEN 7
                                WHEN 'employee' THEN 8 WHEN 'member' THEN 9
                                ELSE 10 END,
                     r.name COLLATE NOCASE
        """).fetchall()
    except sqlite3.Error:
        return []


def role_permission_set(role_key, db=None):
    """The modules a role grants, read from the editable catalogue."""
    db = db or get_db()
    try:
        if db.execute("SELECT 1 FROM roles WHERE key = ?", (role_key,)).fetchone():
            return {row["module"] for row in db.execute(
                "SELECT module FROM role_permissions WHERE role_key = ?",
                (role_key,)).fetchall()}
    except sqlite3.Error:
        pass
    return set(ROLE_PERMISSIONS.get(role_key, ()))


def role_name_for(role_key, db=None):
    db = db or get_db()
    try:
        row = db.execute("SELECT name FROM roles WHERE key = ?",
                         (role_key,)).fetchone()
        if row is not None:
            return row["name"]
    except sqlite3.Error:
        pass
    return ROLE_LABEL.get(role_key, role_key.replace("_", " ").title())


def user_extra_roles(db, user_id):
    try:
        return [row["role_key"] for row in db.execute(
            "SELECT role_key FROM user_roles WHERE user_id = ? ORDER BY role_key",
            (user_id,)).fetchall()]
    except sqlite3.Error:
        return []


def role_is_active(db, role_key):
    """A parked role keeps its configured matrix but hands nothing out."""
    try:
        row = db.execute("SELECT active FROM roles WHERE key = ?",
                         (role_key,)).fetchone()
    except sqlite3.Error:
        return True
    return row is None or bool(row["active"])


# A department binding may name the whole company instead of one unit, and may
# apply to every rung instead of one. Two sentinels keep both answers storable
# without a NULL in the primary key.
COMPANY_WIDE_DEPARTMENT = 0
ANY_POSITION_LEVEL = -1


def account_department_id(db, user):
    """The unit an account belongs to, direct assignment first.

    Organisation assigns the account itself, so its own department_id is the
    answer that matters. An account never assigned there is placed by the staff
    record it is linked to - its position's unit, then the name typed on the HR
    form - so a person still inherits their department's access.
    """
    if user is None:
        return None
    keys = set(user.keys())
    try:
        if "department_id" in keys and user["department_id"]:
            return int(user["department_id"])
        employee_id = user["employee_id"] if "employee_id" in keys else None
        if not employee_id:
            return None
        row = db.execute("""
            SELECT p.department_id
            FROM employees AS e
            LEFT JOIN organizational_positions AS p ON p.id = e.position_id
            WHERE e.id = ?
        """, (employee_id,)).fetchone()
        if row is not None and row["department_id"]:
            return int(row["department_id"])
        row = db.execute("SELECT department FROM employees WHERE id = ?",
                         (employee_id,)).fetchone()
        if row is None or not row["department"]:
            return None
        match = db.execute(
            "SELECT id FROM departments WHERE name = ? COLLATE NOCASE",
            (row["department"],)).fetchone()
        return int(match["id"]) if match is not None else None
    except sqlite3.Error:
        return None


def department_branch_ids(db, department_id):
    """One unit and every unit above it, walked once."""
    ids, seen = [], set()
    cursor = department_id
    while cursor and cursor not in seen:
        seen.add(cursor)
        ids.append(cursor)
        try:
            row = db.execute("SELECT parent_id FROM departments WHERE id = ?",
                             (cursor,)).fetchone()
        except sqlite3.Error:
            break
        if row is None:
            break
        cursor = row["parent_id"]
    return ids


def department_role_keys(db, user):
    """Roles bound to the account's unit, its parents, or the whole company.

    A binding on a directorate reaches the departments inside it, which is what
    makes one row enough at the top of a branch. A binding may name a rung, and
    then only an account whose position sits on that rung carries it; the CEO
    binding and each directorate's director role use that to stay on their own
    seats. Administrators are answered by their role alone, and the administrator
    role itself can never be bound. Cached for the request.
    """
    if user is None or user["role"] == "admin":
        return []
    cache = g.get("_department_role_cache")
    if cache is None:
        cache = {}
        g._department_role_cache = cache
    if user["id"] in cache:
        return list(cache[user["id"]])

    places = [COMPANY_WIDE_DEPARTMENT]
    department_id = account_department_id(db, user)
    if department_id:
        places.extend(department_branch_ids(db, department_id))
    marks = ", ".join("?" for _ in places)
    try:
        rows = db.execute(
            "SELECT b.role_key, b.position_level FROM department_roles AS b"
            " JOIN roles AS r ON r.key = b.role_key"
            " WHERE b.department_id IN ({}) AND COALESCE(r.active, 1) = 1"
            " ORDER BY b.department_id, b.role_key"
            .format(marks), tuple(places)).fetchall()
    except sqlite3.Error:
        rows = ()

    from services.workflow import account_position_level
    level = account_position_level(user, db)
    keys = []
    for row in rows:
        bound_level = row["position_level"]
        if bound_level is None:
            bound_level = ANY_POSITION_LEVEL
        if bound_level != ANY_POSITION_LEVEL and bound_level != level:
            continue
        key = row["role_key"]
        if key == "admin" or key in keys:
            continue
        keys.append(key)
    cache[user["id"]] = tuple(keys)
    return keys


def account_role_keys(db, user):
    """Every role key this account answers to: primary, additional, unit-bound.

    The primary role decides the landing dashboard; the rest widen its reach.
    Kept in one function so the permission matrix, the hiring baseline, the
    letter register and report scoping all read the same list.
    """
    if user is None:
        return []
    keys = [user["role"]]
    for key in user_extra_roles(db, user["id"]) + department_role_keys(db, user):
        if key not in keys and role_is_active(db, key):
            keys.append(key)
    return keys


def permission_modules(keys):
    """The module ticks a set of permission keys opens."""
    modules = set()
    for key in keys:
        modules |= set(PERMISSION_BY_KEY.get(key, {}).get("modules", ()))
    return modules


def stored_role_permissions(db, role_key):
    """(configured, keys) from the role's own fine-grained matrix.

    A role that has never been saved has no rows, and is answered by what its
    module ticks have always implied. Saving the role dialog writes a row for
    every catalogue permission, so a deliberately empty matrix is stored as a
    matrix rather than mistaken for "never configured".
    """
    try:
        rows = db.execute(
            "SELECT permission, allowed FROM role_permission_grants"
            " WHERE role_key = ?", (role_key,)).fetchall()
    except sqlite3.Error:
        return False, set()
    if not rows:
        return False, set()
    return True, {row["permission"] for row in rows
                  if row["allowed"] and row["permission"] in PERMISSION_BY_KEY}


def department_matrix_stored(db, role_key):
    """Whether this role has ever been given an explicit department matrix.

    The check reads the denied rows too: an all-empty matrix is a deliberate
    least-privilege answer, not a role nobody has configured yet.
    """
    try:
        return db.execute(
            "SELECT 1 FROM role_permission_grants WHERE role_key = ?"
            " AND permission GLOB 'dept_*' LIMIT 1",
            (role_key,)).fetchone() is not None
    except sqlite3.Error:
        return False


# The words that make a hand written permission row stand for a department verb.
# They are read from the key itself, so a new action key added to the catalogue
# is counted without a second table. The baseline is only a migration bridge:
# a role with an explicit department matrix is answered by its ticks alone.
DEPARTMENT_ACTION_WORDS = {
    "create": frozenset((
        "create", "add", "new", "register", "submit", "request", "raise",
        "issue", "receive", "post", "open", "generate", "publish", "apply",
        "planning", "plan", "allocation", "allocate", "payment", "record",
        "import", "employees", "leave", "attendance", "movements", "campaigns",
        "leads", "research", "feedback", "tasks", "milestones", "team", "pr",
        "po", "management", "processing")),
    "edit": frozenset((
        "edit", "update", "save", "set", "mark", "score", "status", "verify",
        "review", "manage", "processing", "process", "tracking", "assign",
        "close", "toggle", "restore", "activate", "schedule", "sync", "resolve",
        "submit", "payment", "allocate", "planning", "plan", "allocation",
        "register", "employees", "leave", "attendance", "salary", "grade",
        "allowance", "deduction", "statutory", "campaigns", "leads", "research",
        "feedback", "tasks", "milestones", "team", "items", "movements",
        "stocktake", "pr", "po", "role", "permission", "access", "settings",
        "config", "policy")),
    "delete": frozenset((
        "delete", "deactivate", "remove", "archive", "discard", "cancel",
        "void", "employees", "users", "register", "items", "pr", "po")),
    "approve": frozenset((
        "approve", "approval", "authorize", "authorise", "sign", "decide",
        "decision", "selection", "review", "leave")),
    "reject": frozenset((
        "reject", "return", "decline", "review", "leave")),
    "manage": frozenset((
        "management", "manage", "settings", "config", "configuration",
        "policy", "organization", "roles", "role", "users", "accounts",
        "account", "departments", "access", "permission", "grade",
        "statutory")),
}


# Scopes that share a module need to read only the keys that belong to them.
# Without this split an HR employee key would hand the attendance desk delete
# rights, and a marketing plan key would hand Customer Service edit rights.
def _department_scope_keys(scope_key, keys):
    if scope_key == "hr":
        return {key for key in keys
                if not any(word in key
                           for word in ("attendance", "leave", "overtime"))}
    if scope_key == "attendance":
        return {key for key in keys
                if any(word in key
                       for word in ("attendance", "leave", "overtime"))}
    if scope_key == "customer":
        return {key for key in keys
                if any(word in key
                       for word in ("feedback", "customers", "leads",
                                    "reports"))}
    if scope_key == "marketing":
        return {key for key in keys if "feedback" not in key}
    if scope_key == "operations":
        return set()
    return set(keys)


def _letter_baseline(action_key, actions):
    return {
        "view": bool(actions),
        "read": bool(actions),
        "create": "letters_create" in actions,
        "edit": bool(actions & {"letters_edit", "letters_send"}),
        "delete": "letters_delete" in actions,
        "approve": "letters_approve" in actions,
        "reject": "letters_approve" in actions,
        "export": "letters_download" in actions,
        "print": "letters_print" in actions,
        "manage": False,
    }.get(action_key, False)


def _recruitment_baseline(action_key, rights):
    if not rights:
        return False
    return {
        "view": True,
        "read": True,
        "create": bool(rights & {
            "vacancy_request", "applicant_register", "applicant_hire",
            "exam_author", "interview_book"}),
        "edit": bool(rights & {
            "vacancy_publish", "applicant_screen", "exam_author",
            "exam_publish", "interview_book", "interview_score",
            "interview_decide"}),
        "approve": "interview_decide" in rights,
        "reject": "interview_decide" in rights,
        "export": "hiring_report" in rights,
        "print": "hiring_report" in rights,
        "delete": False,
        "manage": False,
    }.get(action_key, False)


def department_action_baseline(db, role_key, granted_keys, modules=None,
                               letter_actions=None, hiring_rights=None):
    """The department verbs a role's existing permissions already imply.

    This is the only migration bridge: a role saved before the department
    matrix existed keeps exactly the reach its module ticks and action keys
    already gave it. A role with an explicit matrix never reaches this
    function, so least privilege applies from the first save onward. The preset
    dialog passes its letter actions and hiring rights in, because a bundle
    that has not been created yet has no rows to read them from.
    """
    modules = set(role_permission_set(role_key, db)
                  if modules is None else modules)
    keys = {key for key in granted_keys if key in PERMISSION_BY_KEY}
    if letter_actions is None:
        letter_actions = set()
        if "letters" in modules:
            from services.letters import LETTER_VIEW_BASE
            from services.letters import stored_letter_role_permissions
            has_rows, actions = stored_letter_role_permissions(db, role_key)
            letter_actions = set(actions) if has_rows else set(LETTER_VIEW_BASE)
    else:
        letter_actions = set(letter_actions)
    if hiring_rights is None:
        hiring_rights = set()
        if "hr" in modules:
            from services.recruitment import HIRING_MODULE_RIGHTS
            from services.recruitment import role_rights
            hiring_rights = set(role_rights(db, role_key))
            hiring_rights |= set(HIRING_MODULE_RIGHTS.get("hr", ()))
    else:
        hiring_rights = set(hiring_rights)

    granted = set()
    for scope in DEPARTMENT_SCOPES:
        scope_key = scope["key"]
        if not (set(scope["modules"]) & modules):
            continue
        scope_keys = _department_scope_keys(
            scope_key,
            {key for key in keys
             if set(PERMISSION_BY_KEY.get(key, {}).get("modules") or ())
             & set(scope["modules"])})
        words = set()
        for key in scope_keys:
            words |= set(key.split("_"))
        for action in DEPARTMENT_ACTIONS:
            action_key = action["key"]
            allow = action_key in ("view", "read", "export", "print")
            if scope_key == "operations":
                allow = action_key in ("view", "read", "export", "print")
            elif scope_key == "audit":
                allow = (action_key in ("view", "read", "export", "print")
                         or (action_key in ("create", "edit")
                             and role_key == "auditor"))
            elif scope_key == "letters":
                allow = _letter_baseline(action_key, letter_actions)
            elif scope_key == "recruitment":
                allow = _recruitment_baseline(action_key, hiring_rights)
                if "hr_reports" in scope_keys and action_key in (
                        "create", "edit", "export"):
                    allow = True
            elif scope_key in ("finance", "it"):
                allow = (action_key in ("view", "read", "export", "print")
                         or action_key in ("create", "edit", "delete", "manage"))
            elif words & DEPARTMENT_ACTION_WORDS.get(action_key, frozenset()):
                allow = True
            if allow:
                granted.add(department_permission_key(scope_key, action_key))
        # A signing or returning desk can always open and change the record it
        # signs; without that the queue would be visible but unusable.
        if (department_permission_key(scope_key, "approve") in granted
                or department_permission_key(scope_key, "reject") in granted):
            for action_key in ("view", "read", "edit"):
                granted.add(department_permission_key(scope_key, action_key))
    return granted


def role_granted_permissions(db, role_key):
    """The fine-grained permissions a role carries, matrix first, modules after."""
    configured, stored = stored_role_permissions(db, role_key)
    modules = role_permission_set(role_key, db)
    if configured:
        granted = permission_closure(stored)
        if not department_matrix_stored(db, role_key):
            granted |= department_action_baseline(db, role_key, granted, modules)
        return granted
    granted = set(default_role_permissions(role_key))
    granted |= default_recruitment_permissions(db, role_key)
    granted |= department_action_baseline(db, role_key, granted, modules)
    return permission_closure(granted)


def default_recruitment_permissions(db, role_key):
    """Recruitment rights a role's modules have always implied.

    Hiring actions live in their own vocabulary, so the module ticks that open a
    desk also decide which recruitment permissions start ticked. Roles without a
    hiring module start with none.
    """
    from services.recruitment import HIRING_MODULE_RIGHTS
    rights = set()
    for module in role_permission_set(role_key, db):
        rights |= HIRING_MODULE_RIGHTS.get(module, frozenset())
    return {key for key, needed in PERMISSION_HIRING_RIGHTS.items()
            if set(needed) <= rights}


def effective_granted_permissions(user):
    """Every fine-grained permission this account holds.

    The primary role and any additional roles contribute their matrices, the
    roles bound to the account's department contribute theirs, and the account's
    own rows are applied last in both directions, so one permission can be
    borrowed or withdrawn without touching a role. Admin holds the whole
    catalogue by definition. Cached for the request like effective_permissions.
    """
    if user is None:
        return set()
    if user["role"] == "admin":
        return set(PERMISSION_KEYS)
    cache = g.get("_permission_key_cache")
    if cache is None:
        cache = {}
        g._permission_key_cache = cache
    if user["id"] in cache:
        return set(cache[user["id"]])
    db = get_db()
    granted = set(role_granted_permissions(db, user["role"]))
    for role_key in account_role_keys(db, user)[1:]:
        granted |= role_granted_permissions(db, role_key)
    try:
        rows = db.execute(
            "SELECT permission, allowed FROM user_permission_grants"
            " WHERE user_id = ?", (user["id"],)).fetchall()
    except sqlite3.Error:
        rows = ()
    denied = set()
    for row in rows:
        if row["permission"] not in PERMISSION_BY_KEY:
            continue
        if row["allowed"]:
            # A borrowed coarse desk tick opens the same action rights its role
            # form would; the account's explicit denials are applied after it.
            if row["permission"] in COARSE_PERMISSION_COVERAGE:
                granted |= permission_closure({row["permission"]})
            else:
                granted.add(row["permission"])
        else:
            denied.add(row["permission"])
    granted -= denied
    cache[user["id"]] = frozenset(granted)
    return granted


def has_permission(key, user=None, db=None):
    """Whether the account holds one catalogue permission right now.

    Both layers have to agree: the key must be in the effective matrix and every
    module it names must be open. That is what makes an explicit module denial
    revoke the actions inside it without a second rule, and what keeps an
    approval right tied to the desk that holds it rather than to the queue alone.
    """
    if key not in PERMISSION_BY_KEY:
        return False
    account = current_user() if user is None else user
    if account is None:
        return False
    if account["role"] == "admin":
        return True
    if key not in effective_granted_permissions(account):
        return False
    modules = set(PERMISSION_BY_KEY[key].get("modules") or ())
    if modules and not modules <= effective_permissions(account):
        return False
    return True



def role_choices(db=None):
    """Keys and labels for every active role, built-in and custom.

    The primary role is picked from the built-in staff roles; a custom role is a
    permission bundle assigned on top of one, so it widens what an account can
    open without inventing a second landing page. A parked role is left out so
    it cannot be assigned or bound until it is activated again.
    """
    db = db or get_db()
    rows = [row for row in role_catalogue(db)
            if "active" not in row.keys() or row["active"]]
    if not rows:
        return [(key, ROLE_LABEL.get(key, key.title())) for key in USER_ROLES]
    return [(row["key"], row["name"]) for row in rows]


def custom_role_key(db, name):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32] or "custom_role"
    key, suffix = base, 2
    while db.execute("SELECT 1 FROM roles WHERE key = ?", (key,)).fetchone():
        key = "{}_{}".format(base[:28], suffix)
        suffix += 1
    return key


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    cached = g.get("_current_user")
    if cached is not None and cached["id"] == uid:
        return cached
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    g._current_user = user
    return user


def dashboard_url(role):
    endpoint = DASHBOARD_ENDPOINT.get(role)
    return url_for(endpoint) if endpoint else url_for("index")


def effective_permissions(user):
    """Modules this account may open: every assigned role, then its own ticks.

    The primary role is the one that decides the landing dashboard. Additional
    roles (including custom roles) and the roles bound to the account's
    department widen the module list, which is how one person can sit at, say,
    both the Budget and Finance desks without a second account. The result is
    cached for the request, so the sidebar, guards and notification fan-out all
    reuse one read instead of repeating the role joins per view.
    """
    if user is None:
        return set()
    if user["role"] == "admin":
        return set(MODULES)
    cache = g.get("_permission_cache")
    if cache is None:
        cache = {}
        g._permission_cache = cache
    if user["id"] in cache:
        return set(cache[user["id"]])
    db = get_db()
    granted = role_permission_set(user["role"], db)
    for role_key in account_role_keys(db, user)[1:]:
        granted |= role_permission_set(role_key, db)
    for row in db.execute(
        "SELECT module, allowed FROM user_permissions WHERE user_id = ?", (user["id"],)
    ):
        if row["allowed"]:
            granted.add(row["module"])
        else:
            granted.discard(row["module"])
    # A Manager, Director or CEO seat answers approval desks by itself; the
    # grant is derived from the position at read time so it follows every
    # assignment screen with nothing stored to drift. Imported lazily because
    # services.workflow already imports this module.
    from services.workflow import position_grants_approval
    if position_grants_approval(user, db):
        granted.add("approvals")
    cache[user["id"]] = frozenset(granted)
    return granted


def _nav_link(link):
    """A link dict; the two-tuples older rails use are accepted unchanged."""
    if isinstance(link, dict):
        return link
    return {"label": link[0], "target": link[1]}


def _link_href(link):
    params = link.get("params") or {}
    href = url_for(link["target"], **params) if params else url_for(link["target"])
    anchor = link.get("anchor")
    return "#".join((href, anchor)) if anchor else href


def _link_allowed(link, user, granted):
    """The group module opens the rail; a link may narrow it by one right more."""
    required = link.get("modules")
    if required and not (set(required) & granted):
        return False
    keys = link.get("permission") or link.get("permissions")
    if keys:
        if isinstance(keys, str):
            keys = (keys,)
        if not any(has_permission(key, user) for key in keys):
            return False
    roles = link.get("roles")
    if roles and user["role"] not in roles:
        if not (set(department_role_keys(get_db(), user)) & set(roles)):
            return False
    permission = link.get("letter")
    needs_categories = bool(link.get("letter_categories"))
    if permission or needs_categories:
        from services.letters import has_letter_permission
        from services.letters import letter_allowed_types
        try:
            if permission and not has_letter_permission(permission, user):
                return False
            if needs_categories and not letter_allowed_types(user):
                return False
        except sqlite3.Error:
            return False
    return True


def sidebar_groups(user, granted):
    """The left management sidebar for the current request.

    Only management paths get a sidebar, so the public pages keep their exact
    markup, and each group is dropped unless the account holds its module. Two
    groups answer to a per-account right instead of a module tick - the letters
    desk and the hiring desk - and a group with no module is open to every member
    of staff, except a staff shortcut limited to the roles that may open it.
    A destination is listed once: the earliest group in the rail wins, which is
    why the module groups sit above the staff shortcuts. A link may point at a
    filtered view of a shared page (params/anchor), and the view with the most
    matching parameters is the one marked current.
    """
    if user is None or not request.path.startswith(MANAGE_PREFIXES):
        return ()
    endpoint = SIDEBAR_PARENT.get(request.endpoint, request.endpoint)
    if user["role"] == "admin":
        nav = SUPER_ADMIN_NAV
    else:
        nav = SIDEBAR_NAV
        # One dashboard per rail. A module role lands on its own module dashboard
        # and its group already lists that screen, so only accounts without one
        # get the shared overview prepended at the top.
        if (DASHBOARD_ENDPOINT.get(user["role"]) not in MODULE_DASHBOARDS
                and user["role"] in DASHBOARD_ROLES
                and "budget" not in granted):
            # Dashboard is the first entry of the Budget group; accounts without
            # that module still get it as a module-less link at the top.
            nav = ((None, None, (("Dashboard", "dashboard"),)),) + tuple(nav)

    groups, seen = [], set()
    own_dashboard = DASHBOARD_ENDPOINT.get(user["role"]) in MODULE_DASHBOARDS
    for label, module, links in nav:
        if module == "letters":
            # Letter access is granted per account, not by a role tick, so the
            # rail entry follows the account's own grant. An audit seat on its own
            # still reaches the read-only Letter Audit link inside the group.
            from services.letters import letter_module_visible
            if not letter_module_visible(user) and "audit" not in granted:
                continue
        elif module == "hiring":
            # The hiring rail follows what the account is for in recruitment: the
            # HR desk, an audit seat and a department that raises its own requests.
            # A finance seat reads the money on its own desk instead, and a staff
            # account that only browses posts gets no rail it cannot open.
            from services.recruitment import recruitment_scope
            reach = recruitment_scope(get_db(), user)
            if reach is None or reach["level"] not in ("manage", "audit", "department"):
                continue
        elif module is not None and module not in granted:
            continue
        allowed_roles = STAFF_GROUP_ROLES.get(label)
        if allowed_roles is not None and user["role"] not in allowed_roles:
            continue
        rows = []
        for raw in links:
            link = _nav_link(raw)
            target = link["target"]
            params = link.get("params") or {}
            href = _link_href(link)
            if href in seen:
                continue
            # A module role already lands on its own dashboard; a second link to
            # the shared overview (the Budget group carries one) would be the
            # duplicate the account never needs.
            if target == "dashboard" and own_dashboard:
                continue
            if not _link_allowed(link, user, granted):
                continue
            rows.append({
                "label": link["label"],
                "href": href,
                "icon": link.get("icon", ""),
                "active": False,
                "_target": target,
                "_params": params,
                "_matches": target == endpoint and all(
                    request.args.get(key) == value
                    for key, value in params.items()),
            })
            seen.add(href)
        if rows:
            groups.append({
                "label": label,
                "icon": SIDEBAR_GROUP_ICONS.get(label, ""),
                "links": rows,
            })

    # Several submenus can share one page; only the closest view is current.
    best = -1
    for group in groups:
        for row in group["links"]:
            if row["_matches"]:
                best = max(best, len(row["_params"]))
    for group in groups:
        for row in group["links"]:
            row["active"] = row["_matches"] and len(row["_params"]) == best
            del row["_target"], row["_params"], row["_matches"]
    return tuple(groups)


def login_required(*roles):
    return guard(roles=roles)


def perm(*modules):
    return guard(modules=modules)


def guard(roles=(), modules=()):
    from services.audit import log_activity, refused
    wanted_roles = set(roles)
    wanted = set(modules)
    # A decorator may name module ticks and permission keys together; a permission
    # key answers on its own so one route family can be narrowed to one right.
    wanted_keys = {entry for entry in wanted if entry in PERMISSION_BY_KEY}
    wanted_modules = wanted - wanted_keys

    def allowed(user):
        if wanted_modules and (wanted_modules & effective_permissions(user)):
            return True
        if wanted_keys and any(has_permission(key, user) for key in wanted_keys):
            return True
        return not (wanted_modules or wanted_keys)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is not None and user["status"] != "Active":
                log_activity("DENY", "auth",
                             "Disabled account tried to open {}".format(request.path),
                             outcome="DENIED")
                session.clear()
                flash("That account has been disabled. Contact the administrator.", "error")
                return redirect(url_for("login"))
            if user is None:
                flash("Please log in to continue.", "info")
                return redirect(url_for("login", next=request.path))
            if wanted_roles and user["role"] not in wanted_roles:
                # A department binding says the account also carries that role,
                # so a role-gated route opens on the same terms as a module gate.
                if not (set(department_role_keys(get_db(), user)) & wanted_roles):
                    refused(wanted_roles, wanted, user, fn)
                    abort(403)
            if not allowed(user):
                refused(wanted_roles, wanted, user, fn)
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


CSRF_SESSION_KEY = "_csrf_token"


CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


CSRF_EXEMPT_ENDPOINTS = frozenset({"static"})


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token

def csrf_field():
    """The hidden input every POST form carries."""
    return Markup('<input type="hidden" name="csrf_token" value="{}">').format(
        csrf_token())


def enforce_csrf():
    from services.audit import log_activity
    if request.method in CSRF_SAFE_METHODS or request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return None
    expected = session.get(CSRF_SESSION_KEY, "")
    submitted = (request.form.get("csrf_token", "")
                 or request.headers.get("X-CSRF-Token", ""))
    if expected and submitted and secrets.compare_digest(str(expected), str(submitted)):
        return None
    log_activity("DENY", "auth",
                 "Blocked {} without a valid form token on {}".format(
                     request.method, request.path), outcome="DENIED")
    abort(400, description="Your form session expired or this request did not come"
                           " from a page in this system. Refresh and try again.")


LOGIN_MAX_ATTEMPTS = 5


LOGIN_IP_MAX_ATTEMPTS = 25


LOGIN_WINDOW_MINUTES = 15


RESET_MAX_PER_HOUR = 3


RESET_IP_MAX_PER_HOUR = 10


ACCOUNT_LOCK_TEMP_AFTER = 5


ACCOUNT_LOCK_PERMANENT_AFTER = 8


ACCOUNT_LOCK_MINUTES = 10


def login_retry_minutes(db, identifier, ip, include_identifier=True):
    """Minutes until this identifier/address may try again, or 0."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat(
        timespec="seconds")
    checks = []
    if include_identifier:
        checks.append(("identifier", (identifier or "").lower()[:254],
                       LOGIN_MAX_ATTEMPTS))
    checks.append(("ip", (ip or "")[:64], LOGIN_IP_MAX_ATTEMPTS))
    for column, value, limit in checks:
        row = db.execute(
            "SELECT COUNT(*) AS n, MIN(attempted_at) AS first FROM login_attempts"
            " WHERE {} = ? AND attempted_at >= ?".format(column),
            (value, cutoff)).fetchone()
        if row["n"] >= limit and row["first"]:
            try:
                oldest = datetime.fromisoformat(row["first"])
            except ValueError:
                oldest = now
            minutes = int((oldest + timedelta(minutes=LOGIN_WINDOW_MINUTES)
                           - now).total_seconds() // 60) + 1
            return max(1, min(minutes, LOGIN_WINDOW_MINUTES))
    return 0


def record_login_failure(db, identifier, ip):
    db.execute(
        "INSERT INTO login_attempts (identifier, ip, attempted_at) VALUES (?, ?, ?)",
        ((identifier or "").lower()[:254], (ip or "")[:64],
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    db.execute("DELETE FROM login_attempts WHERE attempted_at < ?",
               ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(
                   timespec="seconds"),))
    db.commit()


def clear_login_failures(db, identifier, ip):
    db.execute("DELETE FROM login_attempts WHERE identifier = ? OR ip = ?",
               ((identifier or "").lower()[:254], (ip or "")[:64]))


def login_lock_state(db, user):
    """Return (state, minutes_left) for an account, clearing an expired lock.

    state is "unlocked", "temporary" or "permanent". The users row is the only
    authority, so reloading the page or restarting the application cannot reset
    a lock. A temporary lock clears itself only once its stored expiry passes.
    """
    from services.audit import log_activity
    state = (user["lock_state"] or "unlocked").lower()
    if state == "temporary":
        try:
            until = datetime.fromisoformat(user["lock_until"] or "")
        except (TypeError, ValueError):
            until = None
        now = datetime.now(timezone.utc)
        if until is not None and until > now:
            seconds = (until - now).total_seconds()
            return "temporary", max(1, int((seconds + 59) // 60))
        db.execute(
            "UPDATE users SET lock_state = 'unlocked', lock_until = NULL,"
            " locked_at = NULL WHERE id = ?", (user["id"],))
        db.commit()
        log_activity(
            "UNLOCK", "accounts",
            "Temporary sign-in lock for {} expired automatically".format(user["email"]),
            record_type="user", record_id=user["id"],
            details={"source": "automatic", "lock": "temporary",
                     "failed_attempts": user["failed_login_count"] or 0})
        return "unlocked", 0
    if state == "permanent":
        return "permanent", 0
    return "unlocked", 0


def record_failed_sign_in(db, user):
    """Grow the failure streak and apply the lock ladder to the account row."""
    from services.audit import log_activity
    # Increment in SQL rather than from the row read at request start, so two
    # attempts arriving together cannot overwrite each other's count.
    db.execute("UPDATE users SET failed_login_count = failed_login_count + 1"
               " WHERE id = ?", (user["id"],))
    db.commit()
    attempts = db.execute(
        "SELECT failed_login_count FROM users WHERE id = ?",
        (user["id"],)).fetchone()["failed_login_count"] or 1
    now = datetime.now(timezone.utc)
    if attempts >= ACCOUNT_LOCK_PERMANENT_AFTER:
        db.execute(
            "UPDATE users SET failed_login_count = ?, lock_state = 'permanent',"
            " lock_until = NULL, locked_at = ? WHERE id = ?",
            (attempts, now.isoformat(timespec="seconds"), user["id"]))
        db.commit()
        log_activity(
            "LOCK", "accounts",
            "Account permanently locked after {} failed sign-in attempts".format(
                attempts),
            record_type="user", record_id=user["id"],
            details={"source": "automatic", "lock": "permanent",
                     "failed_attempts": attempts})
        return "permanent", 0
    if attempts == ACCOUNT_LOCK_TEMP_AFTER:
        # Exactly at the fifth failure, once per streak. The streak survives the
        # expired lock, so the sixth and seventh failures must fall through to an
        # ordinary refusal instead of re-arming another ten-minute lock.
        until = now + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
        db.execute(
            "UPDATE users SET failed_login_count = ?, lock_state = 'temporary',"
            " lock_until = ?, locked_at = ? WHERE id = ?",
            (attempts, until.isoformat(timespec="seconds"),
             now.isoformat(timespec="seconds"), user["id"]))
        db.commit()
        log_activity(
            "LOCK", "accounts",
            "Account temporarily locked for {} minutes after {} failed sign-in"
            " attempts".format(ACCOUNT_LOCK_MINUTES, attempts),
            record_type="user", record_id=user["id"],
            details={"source": "automatic", "lock": "temporary",
                     "failed_attempts": attempts,
                     "lock_until": until.isoformat(timespec="seconds")})
        return "temporary", ACCOUNT_LOCK_MINUTES
    return "unlocked", 0


def clear_account_lock(db, user_id):
    """A successful sign-in starts a fresh streak and drops any stale lock."""
    db.execute(
        "UPDATE users SET failed_login_count = 0, lock_state = 'unlocked',"
        " lock_until = NULL, locked_at = NULL WHERE id = ?", (user_id,))


def locked_login_response(db, user, state, minutes=0):
    """The sign-in page with the lock explained, carrying the HTTP status."""
    from services.audit import log_activity
    if state == "permanent":
        notice = {
            "title": "Account permanently locked",
            "body": "This account is locked after repeated failed sign-in attempts."
                    " An administrator must unlock it in User Management before"
                    " you can sign in again.",
        }
        log_activity(
            "LOGIN", "auth",
            "Sign-in refused for permanently locked account {}".format(user["email"]),
            outcome="DENIED", record_type="user", record_id=user["id"],
            details={"lock": "permanent"})
        response = current_app.make_response(
            render_template("login.html", lock_notice=notice))
        response.status_code = 423
        return response
    notice = {
        "title": "Account temporarily locked",
        "body": "Too many failed sign-in attempts. Try again in about {} minute{}."
                " If you cannot wait, ask an administrator to unlock it from"
                " User Management.".format(minutes, "" if minutes == 1 else "s"),
    }
    log_activity(
        "LOGIN", "auth",
        "Sign-in refused for temporarily locked account {}".format(user["email"]),
        outcome="DENIED", record_type="user", record_id=user["id"],
        details={"lock": "temporary", "retry_minutes": minutes})
    response = current_app.make_response(
        render_template("login.html", lock_notice=notice))
    response.status_code = 429
    response.headers["Retry-After"] = str(max(1, minutes) * 60)
    return response


def alert_login_lockout(db, identifier, ip):
    """One alert per address/identifier/hour, to the accounts desk."""
    from services.notifications import notify_permission
    window = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    notify_permission(
        db, "accounts", "Repeated failed sign-ins",
        "{} failed attempts for '{}' from {}.".format(
            LOGIN_MAX_ATTEMPTS, (identifier or "(blank)")[:80],
            ip or "an unknown address"),
        url_for("audit.audit_dashboard", outcome="DENIED"),
        category="security", icon="lock", tone="bad",
        dedupe_prefix="login-lockout-{}-{}".format(
            (identifier or "blank").lower()[:80], window))
    db.commit()


def end_locked_sessions():
    """A locked account loses any open session at its next request.

    Without this, an administrator's lock would only stop the next sign-in and
    the person already holding a session could keep working until it expired.
    """
    if request.endpoint in ("static", "login", "logout", "microsoft_login",
                            "microsoft_callback") or not session.get("user_id"):
        return None
    user = current_user()
    if user is None:
        return None
    state, _minutes = login_lock_state(get_db(), user)
    if state == "unlocked":
        return None
    session.clear()
    flash("Your account is permanently locked. An administrator must unlock it"
          " before you can sign in again." if state == "permanent" else
          "Your account is temporarily locked after failed sign-in attempts."
          " Try again in a few minutes.", "error")
    return redirect(url_for("login"))


PASSWORD_GATE_EXEMPT = ("login", "logout", "account_password", "static")


def hold_until_password_changed():
    from services.audit import log_activity
    if request.endpoint in PASSWORD_GATE_EXEMPT:
        return None
    if not request.path.startswith(MANAGE_PREFIXES + ("/member",)):
        return None
    user = current_user()
    # A Microsoft sign-in never touches the local password, so the "choose your own
    # password" gate would only lock out an account that has no local secret in use.
    if user is None or session.get("auth_provider") == "microsoft":
        return None
    if not user["must_change_password"]:
        return None
    if request.method != "GET":
        log_activity("DENY", "auth",
                     "Blocked {} for an account that has not changed its initial"
                     " password".format(request.path), outcome="DENIED",
                     record_type="user", record_id=user["id"])
    flash("Choose your own password before using the system.", "error")
    return redirect(url_for("account_password"))
