"""Sign-in, sign-out, password change, Microsoft Entra ID and member landing."""

from core.access import CSRF_SESSION_KEY
from core.access import RESET_IP_MAX_PER_HOUR
from core.access import RESET_MAX_PER_HOUR
from core.access import alert_login_lockout
from core.access import clear_account_lock
from core.access import clear_login_failures
from core.access import csrf_token
from core.access import current_user
from core.access import dashboard_url
from core.access import locked_login_response
from core.access import login_lock_state
from core.access import login_required
from core.access import login_retry_minutes
from core.access import record_failed_sign_in
from core.access import record_login_failure
from core.config import MS_ALLOWED_DOMAINS
from core.config import MS_AUTHORITY
from core.config import MS_CLIENT_ID
from core.config import MS_CLIENT_SECRET
from core.config import MS_REDIRECT_URI
from core.config import MS_SCOPES
from core.config import MS_STATE_TTL_SECONDS
from core.config import PASSWORD_MIN_LENGTH
from core.config import RESET_TOKEN_MINUTES
from core.config import ms_login_enabled
from core.db import get_db
from core.routing import get
from core.routing import route
from core.web import load_projects
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from flask import abort
from flask import current_app
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from services.accounts import password_problem
from services.audit import log_activity
from services.email import reset_token_row
from services.email import send_password_reset
from services.notifications import notify_permission
from services.recency import stamp as stamp_record
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash
try:
    import msal
except ImportError:  # Microsoft sign-in stays hidden until the package is present.
    msal = None
import secrets
import time


def ms_redirect_uri():
    # The value must match the redirect URI registered on the app registration.
    return MS_REDIRECT_URI or url_for("microsoft_callback", _external=True)


def safe_next_path(value):
    """A destination handed in by the browser must stay on this site."""
    value = (value or "").strip()
    return value if value.startswith("/") and not value.startswith("//") else ""


def ms_client():
    # The auth-code flow lives in the signed session cookie, so this app still
    # needs no token cache between requests.
    return msal.ConfidentialClientApplication(
        MS_CLIENT_ID, authority=MS_AUTHORITY,
        client_credential=MS_CLIENT_SECRET)


def ms_email_from_claims(claims):
    for key in ("preferred_username", "email", "upn"):
        value = str(claims.get(key) or "").strip().lower()
        if value:
            return value
    return ""


@get("/auth/microsoft")
def microsoft_login():
    """Send the browser to the tenant's sign-in page."""
    if not ms_login_enabled():
        flash("Microsoft sign-in is not available on this site.", "error")
        return redirect(url_for("login"))
    try:
        flow = ms_client().initiate_auth_code_flow(
            list(MS_SCOPES), redirect_uri=ms_redirect_uri(),
            state=secrets.token_urlsafe(32))
    except Exception:
        log_activity("LOGIN", "auth", "Could not start Microsoft sign-in",
                     outcome="FAILURE", details={"method": "microsoft"})
        flash("Microsoft sign-in could not start. Try again, or use your password.",
              "error")
        return redirect(url_for("login"))
    # MSAL generates the nonce and PKCE verifier inside the flow; keeping the whole
    # dict in the signed session is what lets the callback prove this browser
    # started the sign-in and that the id token answers it.
    session["ms_flow"] = flow
    session["ms_started_at"] = int(time.time())
    session["ms_next"] = safe_next_path(request.args.get("next"))
    return redirect(flow["auth_uri"])


@get("/auth/microsoft/callback")
def microsoft_callback():
    flow = session.pop("ms_flow", None)
    started_at = session.pop("ms_started_at", 0)
    next_path = safe_next_path(session.pop("ms_next", ""))

    def refuse(message, description, details=None, outcome="DENIED"):
        details = dict({"method": "microsoft"}, **(details or {}))
        log_activity("LOGIN", "auth", description, outcome=outcome, details=details)
        flash(message, "error")
        return redirect(url_for("login"))

    if not ms_login_enabled():
        return refuse("Microsoft sign-in is not available on this site.",
                      "Microsoft callback reached an unconfigured site",
                      outcome="FAILURE")
    if not flow or not started_at:
        return refuse("That sign-in link has expired. Please start again.",
                      "Microsoft callback without a pending sign-in",
                      outcome="FAILURE")
    try:
        elapsed = int(time.time()) - int(started_at)
    except (TypeError, ValueError):
        elapsed = MS_STATE_TTL_SECONDS + 1
    if elapsed > MS_STATE_TTL_SECONDS:
        return refuse("That sign-in took too long. Please start again.",
                      "Microsoft sign-in expired before the callback",
                      outcome="FAILURE")
    if request.args.get("error"):
        return refuse("Microsoft sign-in was cancelled. You can try again.",
                      "Microsoft returned an error before sign-in completed",
                      outcome="FAILURE",
                      details={"error": str(request.args.get("error"))[:80]})
    try:
        result = ms_client().acquire_token_by_auth_code_flow(flow, request.args)
    except ValueError:
        # Raised for a state mismatch or a malformed response - both mean the
        # callback did not belong to this browser session.
        return refuse("That sign-in could not be verified. Please start again.",
                      "Microsoft callback failed state or token validation",
                      outcome="FAILURE")
    if not result or result.get("error"):
        return refuse("Microsoft could not verify your account. Please try again.",
                      "Microsoft did not return a valid token",
                      outcome="FAILURE",
                      details={"error": str((result or {}).get("error"))[:80]})

    claims = result.get("id_token_claims") or {}
    object_id = str(claims.get("oid") or "").strip()
    tenant_id = str(claims.get("tid") or "").strip()
    email = ms_email_from_claims(claims)
    if not object_id or not email:
        return refuse("Microsoft did not return a usable account identity.",
                      "Microsoft token lacked an object id or email",
                      outcome="FAILURE")
    if MS_ALLOWED_DOMAINS and email.rsplit("@", 1)[-1] not in MS_ALLOWED_DOMAINS:
        return refuse("That Microsoft account is not part of this organization.",
                      "Microsoft sign-in refused for a domain outside the allow list",
                      outcome="DENIED", details={"email": email[:120]})

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE ms_object_id = ? ORDER BY id LIMIT 1",
        (object_id,)).fetchone()
    if user is None:
        # First Microsoft sign-in: adopt the local account that already carries this
        # mailbox. A tenant-signed token is the proof that the address belongs to
        # the person, so this link needs no separate confirmation.
        candidate = db.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE ORDER BY id LIMIT 1",
            (email,)).fetchone()
        if candidate is None:
            return refuse("No account here matches that Microsoft account. Ask an"
                          " administrator to create or link one.",
                          "Microsoft sign-in refused for an unknown account",
                          outcome="DENIED", details={"email": email[:120]})
        if candidate["ms_object_id"] and candidate["ms_object_id"] != object_id:
            return refuse("This account is already linked to another Microsoft"
                          " account. Ask an administrator for help.",
                          "Microsoft sign-in refused for an account linked elsewhere",
                          outcome="DENIED", details={"user": candidate["id"]})
        db.execute(
            "UPDATE users SET ms_object_id = ?, ms_tenant_id = ?, ms_linked_at = ?"
            " WHERE id = ?",
            (object_id, tenant_id or None,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             candidate["id"]))
        db.commit()
        log_activity("UPDATE", "auth",
                     "Linked the Microsoft account for {}".format(candidate["email"]),
                     record_type="user", record_id=candidate["id"],
                     details={"method": "microsoft"})
        user = db.execute("SELECT * FROM users WHERE id = ?",
                          (candidate["id"],)).fetchone()

    if user["status"] != "Active":
        log_activity("LOGIN", "auth",
                     "Microsoft sign-in refused for disabled account {}".format(email),
                     outcome="DENIED", record_type="user", record_id=user["id"])
        return refuse("That account has been disabled. Contact the administrator.",
                      "Microsoft sign-in refused for a disabled account",
                      outcome="DENIED", details={"user": user["id"]})

    state, minutes = login_lock_state(db, user)
    if state == "permanent":
        return refuse("Your account is permanently locked. An administrator must"
                      " unlock it in User Management before you can sign in again.",
                      "Microsoft sign-in refused for a permanently locked account",
                      details={"user": user["id"], "lock": "permanent"})
    if state == "temporary":
        return refuse("Your account is temporarily locked after failed sign-in"
                      " attempts. Try again in about {} minute{}.".format(
                          minutes, "" if minutes == 1 else "s"),
                      "Microsoft sign-in refused for a temporarily locked account",
                      details={"user": user["id"], "lock": "temporary",
                               "retry_minutes": minutes})

    clear_login_failures(db, email, request.remote_addr or "")
    clear_account_lock(db, user["id"])
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["auth_provider"] = "microsoft"
    db.execute("UPDATE users SET last_login = ? WHERE id = ?",
               (datetime.now().strftime("%Y-%m-%d %H:%M"), user["id"]))
    db.commit()
    log_activity("LOGIN", "auth", "Signed in with Microsoft",
                 record_type="user", record_id=user["id"],
                 details={"method": "microsoft"})
    flash(f"Welcome back, {user['full_name']}!", "success")
    if next_path:
        return redirect(next_path)
    return redirect(dashboard_url(user["role"]))


@route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        ip = request.remote_addr or ""

        # Either the account's email or its minted DB1000 username opens the door. Both
        # halves are indexed equality tests, so the lookup stays a fast seek rather than
        # a wrapped column. The username half ignores case only so that 'db1000' typed
        # on a phone still finds DB1000; emails are stored lower-cased already.
        user = db.execute(
            "SELECT * FROM users WHERE email = ? OR username = ? COLLATE NOCASE"
            " ORDER BY id LIMIT 1",
            (identifier.lower(), identifier)).fetchone()

        # The per-address limit stays in front of every attempt. For a known account
        # the per-identifier allowance is replaced by the lock ladder on the row, so
        # the two policies never fight over the same failure.
        # The audit row must name the account whose attempt this was, whether or
        # not that account exists, signed in, or got as far as the password check.
        attempt_label = ((user["username"] or identifier) if user is not None
                         else (identifier or None))
        retry = login_retry_minutes(db, identifier, ip,
                                    include_identifier=user is None)
        if retry:
            log_activity("LOGIN", "auth",
                         "Sign-in blocked by throttle for {}".format(
                             identifier or "(blank)"),
                         outcome="DENIED", details={"retry_minutes": retry},
                         username=None if user is not None else attempt_label,
                         actor=user)
            alert_login_lockout(db, identifier, ip)
            flash("Too many sign-in attempts. Try again in about {} minute{}.".format(
                retry, "" if retry == 1 else "s"), "error")
            response = current_app.make_response(render_template("login.html"))
            response.status_code = 429
            response.headers["Retry-After"] = str(retry * 60)
            return response

        if user is not None:
            state, minutes = login_lock_state(db, user)
            if state != "unlocked":
                return locked_login_response(db, user, state, minutes)

        if user is None or not check_password_hash(user["password_hash"], password):
            record_login_failure(db, identifier, ip)
            log_activity("LOGIN", "auth",
                         "Failed sign-in attempt for {}".format(identifier or "(blank)"),
                         outcome="FAILURE", details={"identifier": identifier},
                         username=None if user is not None else attempt_label,
                         actor=user)
            if user is not None:
                state, minutes = record_failed_sign_in(db, user)
                if state != "unlocked":
                    return locked_login_response(db, user, state, minutes)
            flash("Incorrect username or password.", "error")
        elif user["status"] != "Active":
            log_activity("LOGIN", "auth",
                         "Sign-in refused for disabled account {}".format(identifier),
                         outcome="DENIED", record_type="user", record_id=user["id"],
                         actor=user)
            flash("That account has been disabled. Contact the administrator.", "error")
        else:
            clear_login_failures(db, identifier, ip)
            clear_account_lock(db, user["id"])
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["auth_provider"] = "password"
            db.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), user["id"]),
            )
            db.commit()
            log_activity("LOGIN", "auth", "Signed in",
                         record_type="user", record_id=user["id"])
            if user["must_change_password"]:
                # The handed-over password is not a secret yet, so the welcome waits
                # until the account has chosen its own. This runs before ?next= so a
                # gated account cannot be steered anywhere else.
                flash("Welcome, {}. Choose your own password to continue.".format(
                    user["full_name"]), "info")
                return redirect(url_for("account_password"))
            flash(f"Welcome back, {user['full_name']}!", "success")
            nxt = safe_next_path(request.args.get("next"))
            if nxt:
                return redirect(nxt)
            return redirect(dashboard_url(user["role"]))
    return render_template("login.html")


@route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Take a reset request without revealing whether the account exists.

    There is no mail server in this deployment, so an administrator issues the
    temporary password from User Management after checking the person's identity.
    """
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()[:254]
        if not identifier:
            flash("Enter the email or username on your account.", "error")
            return render_template("forgot_password.html", sent=False)
        db = get_db()
        ip = (request.remote_addr or "")[:64]
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=1)).isoformat(timespec="seconds")
        key = identifier.lower()
        recent = db.execute("""
            SELECT (SELECT COUNT(*) FROM password_reset_requests
                     WHERE identifier = ? AND requested_at >= ?) AS by_identifier,
                   (SELECT COUNT(*) FROM password_reset_requests
                     WHERE ip = ? AND requested_at >= ?) AS by_ip
        """, (key, cutoff, ip, cutoff)).fetchone()
        user = db.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE"
            " OR username = ? COLLATE NOCASE ORDER BY id LIMIT 1",
            (key, identifier)).fetchone()
        if (recent["by_identifier"] < RESET_MAX_PER_HOUR
                and recent["by_ip"] < RESET_IP_MAX_PER_HOUR):
            db.execute(
                "INSERT INTO password_reset_requests"
                " (identifier, user_id, ip, requested_at) VALUES (?, ?, ?, ?)",
                (key, user["id"] if user else None, ip,
                 now.isoformat(timespec="seconds")))
            db.execute("DELETE FROM password_reset_requests WHERE requested_at < ?",
                       ((now - timedelta(days=7)).isoformat(timespec="seconds"),))
            if user is not None and user["status"] == "Active":
                outcome, reason = send_password_reset(db, user, ip)
                if outcome == "sent":
                    log_activity(
                        "RESET", "auth",
                        "Password reset link emailed to {}".format(user["email"]),
                        record_type="user", record_id=user["id"],
                        details={"channel": "email",
                                 "expires_minutes": RESET_TOKEN_MINUTES})
                else:
                    # No mailbox can take the link, so the accounts desk still owns
                    # the request and issues a temporary password by hand.
                    notify_permission(
                        db, "accounts", "Password reset requested",
                        "{} asked for a password reset and no email was sent ({})."
                        " Check their identity, then set a temporary password in"
                        " User Management.".format(user["full_name"], reason),
                        url_for("admin_users"), category="security", icon="lock",
                        tone="warn",
                        dedupe_prefix="password-reset-{}-{}".format(
                            user["id"], now.strftime("%Y-%m-%d-%H")))
                    log_activity(
                        "RESET", "auth",
                        "Password reset requested for {}; no email sent".format(
                            user["email"]), outcome="FAILURE",
                        record_type="user", record_id=user["id"],
                        details={"channel": "email", "reason": reason[:200]})
            else:
                log_activity("RESET", "auth",
                             "Password reset requested for an unknown or disabled"
                             " account", outcome="FAILURE",
                             details={"identifier": identifier[:120]})
            db.commit()
        else:
            log_activity("RESET", "auth", "Password reset request throttled",
                         outcome="DENIED",
                         details={"identifier": identifier[:120]})
        flash("If that account exists, a reset link has been sent to its registered"
              " email. If no mailbox is on file, an administrator has been notified."
              " The link expires after {} minutes.".format(RESET_TOKEN_MINUTES),
              "info")
        return redirect(url_for("forgot_password", sent=1))
    return render_template("forgot_password.html",
                           sent=request.args.get("sent") == "1")


@route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Choose a new password from the single-use link that was emailed."""
    db = get_db()
    row = reset_token_row(db, token)
    if row is None:
        # A spent, revoked or unknown link answers as an error on GET too, so a
        # refresh or a back button cannot present it as a working form.
        return render_template("reset_password.html", state="invalid",
                               minimum=PASSWORD_MIN_LENGTH,
                               expires_minutes=RESET_TOKEN_MINUTES), 400

    if request.method == "POST":
        chosen = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        problem = password_problem(chosen)
        if problem:
            flash(problem, "error")
        elif chosen != confirm:
            flash("The two new passwords do not match.", "error")
        elif check_password_hash(row["password_hash"], chosen):
            flash("Choose a password this account has not used before.", "error")
        else:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.execute("""
                UPDATE users SET password_hash = ?, must_change_password = 0,
                                 password_updated_at = ?
                WHERE id = ?
            """, (generate_password_hash(chosen),
                  datetime.now().strftime("%Y-%m-%d %H:%M"), row["user_id"]))
            # The link that was just used, and every other link still open for
            # this account, stop working the moment one of them succeeds.
            db.execute("UPDATE password_reset_tokens SET used_at = ?"
                       " WHERE user_id = ? AND used_at IS NULL",
                       (now, row["user_id"]))
            clear_login_failures(db, row["email"], request.remote_addr or "")
            clear_account_lock(db, row["user_id"])
            stamp_record(db, "users", row["user_id"], name=row["full_name"])
            db.commit()
            log_activity("RESET", "auth",
                         "Password reset completed with an emailed link",
                         record_type="user", record_id=row["user_id"],
                         details={"channel": "email"})
            flash("Password changed. Sign in with the new one.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password.html", state="form", account=row,
                           minimum=PASSWORD_MIN_LENGTH,
                           expires_minutes=RESET_TOKEN_MINUTES)


@get("/logout")
def logout():
    if session.get("user_id"):
        log_activity("LOGOUT", "auth", "Signed out",
                     record_type="user", record_id=session["user_id"])
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@route("/account/password", methods=["GET", "POST"])
@login_required()
def account_password():
    """Replace your own password - forced on the first sign-in, open to every role."""
    db = get_db()
    user = current_user()
    forced = bool(user["must_change_password"])

    if request.method == "POST":
        submitted = request.form.get("csrf_token", "")
        expected = session.get(CSRF_SESSION_KEY, "")
        if not expected or not secrets.compare_digest(submitted, expected):
            abort(400, description="Invalid or expired form token. Refresh and try again.")
        current = request.form.get("current_password", "")
        chosen = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        problem = password_problem(chosen)

        if not check_password_hash(user["password_hash"], current):
            flash("That is not your current password.", "error")
        elif problem:
            flash(problem, "error")
        elif chosen != confirm:
            flash("The two new passwords do not match.", "error")
        elif check_password_hash(user["password_hash"], chosen):
            flash("Choose a password this account has not used before.", "error")
        else:
            db.execute("""
                UPDATE users SET password_hash = ?, must_change_password = 0,
                                 password_updated_at = ?
                WHERE id = ?
            """, (generate_password_hash(chosen),
                  datetime.now().strftime("%Y-%m-%d %H:%M"), user["id"]))
            stamp_record(db, "users", user["id"], user)
            db.commit()
            log_activity("UPDATE", "auth",
                         "Password changed ({})".format(
                             "first sign-in" if forced else "voluntary"),
                         record_type="user", record_id=user["id"],
                         details={"forced": bool(forced)})
            flash("Password changed. You are in as {}.".format(
                user["full_name"]), "success")
            return redirect(dashboard_url(user["role"]))

    return render_template("account/password.html",
                           forced=forced,
                           minimum=PASSWORD_MIN_LENGTH,
                           csrf_value=csrf_token(),
                           account=user)


@route("/member")
@login_required("member", "employee")
def member():
    user = current_user()
    db = get_db()
    employee = None
    if user["employee_id"]:
        employee = db.execute(
            "SELECT * FROM employees WHERE id = ?", (user["employee_id"],)
        ).fetchone()
    return render_template("member.html", projects=load_projects()[:4],
                           employee=employee)
