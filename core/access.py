"""Sessions, permissions, CSRF, login throttling and request hooks."""

from core.config import DASHBOARD_ENDPOINT
from core.config import DASHBOARD_ROLES
from core.config import MANAGE_PREFIXES
from core.config import MODULES
from core.config import MODULE_DASHBOARDS
from core.config import ROLE_LABEL
from core.config import ROLE_PERMISSIONS
from core.config import SIDEBAR_NAV
from core.config import SIDEBAR_PARENT
from core.config import STAFF_GROUP_ROLES
from core.config import SUPER_ADMIN_NAV
from core.config import USER_ROLES
from core.db import ROLES_DDL
from core.db import ROLE_PERMISSIONS_DDL
from core.db import USER_ROLES_DDL
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
    db.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_user"
               " ON user_roles(user_id)")
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


def role_choices(db=None):
    """Keys and labels for every role, built-in and custom.

    The primary role is picked from the built-in staff roles; a custom role is a
    permission bundle assigned on top of one, so it widens what an account can
    open without inventing a second landing page.
    """
    db = db or get_db()
    rows = role_catalogue(db)
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
    roles (including custom roles) widen the module list, which is how one person
    can sit at, say, both the Budget and Finance desks without a second account.
    The result is cached for the request, so the sidebar, guards and notification
    fan-out all reuse one read instead of repeating the role joins per view.
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
    for role_key in user_extra_roles(db, user["id"]):
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


def sidebar_groups(user, granted):
    """The left management sidebar for the current request.

    Only management paths get a sidebar, so the public pages keep their exact
    markup, and each group is dropped unless the account holds its module. A
    group with no module is open to every member of staff, except a staff
    shortcut limited to the roles that may open it. A destination is listed once:
    when a staff shortcut and a module group both point at the same screen, the
    module group's entry replaces the shortcut.
    """
    if user is None or not request.path.startswith(MANAGE_PREFIXES):
        return ()
    active = SIDEBAR_PARENT.get(request.endpoint, request.endpoint)
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
    for label, module, links in reversed(nav):
        if module == "letters":
            # Letter access is granted per account, not by a role tick, so the
            # rail entry follows the account's own grant.
            from services.letters import letter_module_visible
            if not letter_module_visible(user):
                continue
        elif module is not None and module not in granted:
            continue
        allowed_roles = STAFF_GROUP_ROLES.get(label)
        if allowed_roles is not None and user["role"] not in allowed_roles:
            continue
        rows = []
        for text, target in links:
            if target in seen:
                continue
            # A module role already lands on its own dashboard; a second link to
            # the shared overview (the Budget group carries one) would be the
            # duplicate the account never needs.
            if target == "dashboard" and own_dashboard:
                continue
            rows.append({"label": text, "href": url_for(target),
                         "active": target == active})
            seen.add(target)
        if rows:
            groups.append({"label": label, "links": rows})
    groups.reverse()
    return tuple(groups)


def login_required(*roles):
    return guard(roles=roles)


def perm(*modules):
    return guard(modules=modules)


def guard(roles=(), modules=()):
    from services.audit import log_activity, refused
    wanted_roles, wanted_modules = set(roles), set(modules)

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
                refused(wanted_roles, wanted_modules, user, fn)
                abort(403)
            if wanted_modules and not (wanted_modules & effective_permissions(user)):
                refused(wanted_roles, wanted_modules, user, fn)
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
