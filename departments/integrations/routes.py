"""The System Integration desk: the screens an administrator uses.

One registry of the external systems this company connects to - Active Directory
or LDAP, a REST service, mail, SMS, a biometric device or an ERP - each with its
endpoint, its sealed credential, its connection tests, its synchronization
schedule and its own history. Everything the desk reads and writes is in the
application database (``database.py``) and every step an account takes here is
written to the shared activity log under module ``integrations``, so the audit
console sees this desk exactly like it sees every other desk.

Access is the module tick and nothing else: no view trusts a hidden button, a
link or a form. The same ``@perm("integrations")`` refuses the direct URL, the
POST and the history screen in one breath, and CSRF is checked globally before
any of them runs.
"""
import sqlite3
from urllib.parse import urlparse

from core.access import current_user
from core.access import effective_permissions
from core.access import perm
from core.db import get_db
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import log_activity

from .connection_test import test_connection
from .database import AD_TYPE
from .database import CREDENTIAL_LABEL
from .database import CREDENTIAL_NAMES
from .database import INTEGRATION_STATUS
from .database import INTEGRATION_TYPES
from .database import MAX_SYNC_INTERVAL
from .database import MIN_SYNC_INTERVAL
from .database import clean_interval
from .database import clean_name
from .database import clean_text
from .database import credential_names_set
from .database import stored_integration
from .database import type_is_known
from .mapping import ad_departments
from .mapping import department_choices
from .mapping import mapping_rows
from .mapping import prepare_employee_mappings
from .mapping import save_department_mapping
from .security import store_credential
from .synchronization import has_adapter
from .synchronization import iso
from .synchronization import next_run_at
from .synchronization import run_integration_sync

integrations_bp = Blueprint("integrations", __name__, url_prefix="/integrations")

DASHBOARD_ACTIVITY = 10
MONITORED_ERRORS = 20
MONITORED_RUNS = 15
CONNECTION_HISTORY = 100
ACTIVITY_LOG = 200


# ------------------------------------------------------------------ helpers
def integration_or_404(db, integration_id):
    """The registry row, or a 404 that never says which ids exist."""
    row = stored_integration(db, integration_id)
    if row is None:
        abort(404)
    return row


def error_or_404(db, error_id):
    row = db.execute("SELECT * FROM integration_errors WHERE id = ?",
                     (error_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def directory_profile(db, integration_id):
    """The directory profile this registry row owns, or None."""
    from services.directory import connection_by_integration
    return connection_by_integration(db, integration_id)


def require_directory_right(profile):
    """Keep a directory row behind the ``accounts`` module, as before the merger.

    The integration tick opens the registry and its generic connections; a row
    that provisions and disables local accounts still answers to the account
    administrators alone, whichever screen is used to reach it.
    """
    if profile is not None and "accounts" not in effective_permissions(current_user()):
        abort(403)
    return profile


def registry_rows(db):
    """Every connection with the counts and the last result its row shows.

    The counts arrive in the one query rather than per row, and the derived flags
    are computed here so a template never decides on its own what may be clicked.
    """
    rows = db.execute("""
        SELECT i.*,
               (SELECT COUNT(*) FROM integration_credentials c
                   WHERE c.integration_id = i.id) AS credential_count,
               (SELECT COUNT(*) FROM integration_errors e
                   WHERE e.integration_id = i.id AND e.resolved = 0) AS open_errors,
               (SELECT COUNT(*) FROM integration_ad_users u
                   WHERE u.integration_id = i.id) AS ad_accounts,
               (SELECT status FROM integration_connection_tests t
                   WHERE t.integration_id = i.id ORDER BY t.id DESC LIMIT 1)
                   AS last_test,
               (SELECT message FROM integration_connection_tests t
                   WHERE t.integration_id = i.id ORDER BY t.id DESC LIMIT 1)
                   AS last_test_message,
               (SELECT tested_at FROM integration_connection_tests t
                   WHERE t.integration_id = i.id ORDER BY t.id DESC LIMIT 1)
                   AS last_test_at,
               (SELECT c.id FROM directory_connections c
                 WHERE c.integration_id = i.id) AS directory_id
        FROM integrations i
        ORDER BY i.name COLLATE NOCASE
    """).fetchall()

    now = iso()
    cards = []
    for row in rows:
        card = dict(row)
        card["can_sync"] = bool(card["directory_id"]) or has_adapter(
            row["integration_type"])
        card["is_ad"] = row["integration_type"] == AD_TYPE
        card["is_directory"] = bool(card["directory_id"])
        card["due"] = bool(card["can_sync"] and card["sync_enabled"]
                           and card["status"] == "active" and card["next_run_at"]
                           and card["next_run_at"] <= now)
        cards.append(card)
    return cards


def schedule_fields(row, interval, enabled, has_directory=False):
    """(interval, enabled, warning) for the two schedule values a form posts.

    A schedule is kept only for a type the desk can actually run: a generic
    directory staging adapter, or a row that owns a directory profile and is
    therefore carried by the directory engine. Switching automatic runs on for a
    REST endpoint would fill the log with synchronizations that can only ever
    fail, so the answer is saved as off with the reason.
    """
    if enabled and not has_directory and not has_adapter(row["integration_type"]):
        return interval, False, (
            "Automatic synchronization is available for directory connections only,"
            " so the connection was saved with it switched off.")
    return interval, enabled, ""


def form_values(form):
    """The registry fields, cleaned, straight off a posted form."""
    return {
        "name": clean_name(form.get("name")),
        "integration_type": (form.get("integration_type") or "").strip(),
        "description": clean_text(form.get("description"), 500),
        "endpoint": clean_text(form.get("endpoint"), 300),
        "username": clean_text(form.get("username"), 300),
        "search_base": clean_text(form.get("search_base"), 512),
    }


def form_problem(values):
    """Why a posted registry form cannot be saved, or "" when it can."""
    if not values["name"]:
        return "Give the connection a name."
    if not type_is_known(values["integration_type"]):
        return "Choose one of the listed integration types."
    if values["integration_type"] == AD_TYPE and not values["endpoint"]:
        return "An Active Directory connection needs its ldaps:// endpoint."
    return ""


def form_context():
    """The values every registry form template needs, whatever it posts."""
    return {"integration_types": INTEGRATION_TYPES,
            "statuses": INTEGRATION_STATUS,
            "credential_label": CREDENTIAL_LABEL,
            "ad_type": AD_TYPE,
            "min_interval": MIN_SYNC_INTERVAL,
            "max_interval": MAX_SYNC_INTERVAL}


def same_site_back(fallback):
    """Where a completed action returns to: the referring screen on this site.

    The Referer header is client-controlled, so only a path or this exact host is
    followed; anything else would let a crafted header bounce an administrator
    to another site after a test or a synchronization.
    """
    referrer = request.referrer or ""
    if not referrer:
        return fallback
    parts = urlparse(referrer)
    if not parts.scheme and not parts.netloc:
        return referrer if referrer.startswith("/") and not referrer.startswith("//") \
            else fallback
    if parts.scheme in ("http", "https") and parts.netloc == request.host:
        return referrer
    return fallback


# ------------------------------------------------------------------- screens
@integrations_bp.route("/")
@perm("integrations")
def dashboard():
    """The registry: one row per connection and everything that needs a look."""
    db = get_db()
    cards = registry_rows(db)
    stats = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN sync_enabled = 1 THEN 1 ELSE 0 END) AS scheduled,
               SUM(CASE WHEN last_sync_at IS NOT NULL THEN 1 ELSE 0 END) AS synced
        FROM integrations
    """).fetchone()
    open_errors = db.execute(
        "SELECT COUNT(*) AS n FROM integration_errors WHERE resolved = 0"
    ).fetchone()["n"]

    attention = []
    for card in cards:
        if card["open_errors"]:
            attention.append({
                "icon": "warn", "tone": "warn",
                "title": "{} has {} unresolved error{}".format(
                    card["name"], card["open_errors"],
                    "" if card["open_errors"] == 1 else "s"),
                "meta": card["last_test_message"] or "Opened by a failed run.",
                "time": "Monitor",
                "href": url_for("integrations.monitoring")})
        elif card["status"] == "active" and card["last_test"] == "failed":
            attention.append({
                "icon": "network", "tone": "warn",
                "title": "{} is active but the last test failed".format(card["name"]),
                "meta": card["last_test_message"],
                "time": "History",
                "href": url_for("integrations.connection_history",
                                integration_id=card["id"])})
        elif card["status"] == "active" and not card["credential_count"]:
            attention.append({
                "icon": "lock", "tone": "warn",
                "title": "{} is active with no credential stored".format(card["name"]),
                "meta": "A bind or an authenticated call will fail until one is set.",
                "time": "Credentials",
                "href": url_for("integrations.edit",
                                integration_id=card["id"])})
        elif card["status"] == "active" and not card["last_test_at"]:
            attention.append({
                "icon": "search", "tone": "",
                "title": "{} has never been tested".format(card["name"]),
                "meta": "Test the endpoint before anyone relies on this connection.",
                "time": "Check",
                "href": url_for("integrations.edit", integration_id=card["id"])})

    activity = db.execute("""
        SELECT occurred_at, username, action, description, outcome, record_id
        FROM audit_logs
        WHERE module = 'integrations'
        ORDER BY id DESC LIMIT ?
    """, (DASHBOARD_ACTIVITY,)).fetchall()

    return render_template("integrations/dashboard.html", cards=cards, stats=stats,
                           open_errors=open_errors, attention=attention,
                           activity=activity)


@integrations_bp.route("/add", methods=["GET", "POST"])
@perm("integrations")
def add():
    """Register a connection; its credentials are stored on its own screen."""
    db = get_db()
    if request.method == "POST":
        values = form_values(request.form)
        problem = form_problem(values)
        if problem:
            flash(problem, "error")
            return render_template("integrations/add.html", form=values,
                                   **form_context())

        try:
            cursor = db.execute("""
                INSERT INTO integrations
                    (name, integration_type, description, endpoint, username,
                     search_base, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (values["name"], values["integration_type"], values["description"],
                  values["endpoint"], values["username"], values["search_base"],
                  current_user()["id"]))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("A connection called {} is already registered.".format(
                values["name"]), "error")
            return render_template("integrations/add.html", form=values,
                                   **form_context())

        integration_id = cursor.lastrowid
        db.commit()
        log_activity("CREATE", "integrations",
                     "Registered the {} connection {}.".format(
                         values["integration_type"], values["name"]),
                     record_type="integration", record_id=integration_id,
                     details={"integration_type": values["integration_type"],
                              "endpoint": values["endpoint"]})
        flash("{} is registered. Store its credential and test the endpoint before "
              "activating it.".format(values["name"]), "success")
        return redirect(url_for("integrations.edit", integration_id=integration_id))

    return render_template("integrations/add.html", form={}, **form_context())


@integrations_bp.route("/edit/<int:integration_id>", methods=["GET", "POST"])
@perm("integrations")
def edit(integration_id):
    """One connection: its settings, its schedule and its stored credentials.

    The form on this screen writes the identity fields only. The schedule and the
    credential each have their own route, so a saved form can never switch
    automatic synchronization off by leaving its fields out.
    """
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))

    if request.method == "POST":
        values = form_values(request.form)
        problem = form_problem(values)
        if problem:
            flash(problem, "error")
            return redirect(url_for("integrations.edit",
                                    integration_id=integration_id))
        if profile is not None and values["integration_type"] != row["integration_type"]:
            flash("A directory connection keeps its type; change it from the directory"
                  " screen if the profile itself must move.", "error")
            return redirect(url_for("integrations.edit",
                                    integration_id=integration_id))

        previous = {"name": row["name"],
                    "integration_type": row["integration_type"],
                    "endpoint": row["endpoint"], "username": row["username"],
                    "search_base": row["search_base"],
                    "description": row["description"]}
        try:
            db.execute("""
                UPDATE integrations
                SET name = ?, integration_type = ?, description = ?, endpoint = ?,
                    username = ?, search_base = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (values["name"], values["integration_type"], values["description"],
                  values["endpoint"], values["username"], values["search_base"],
                  integration_id))
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash("A connection called {} is already registered.".format(
                values["name"]), "error")
            return redirect(url_for("integrations.edit",
                                    integration_id=integration_id))

        log_activity("UPDATE", "integrations",
                     "Updated the connection {}.".format(values["name"]),
                     record_type="integration", record_id=integration_id,
                     previous=previous, updated=values)
        flash("Connection settings saved.", "success")
        return redirect(url_for("integrations.edit", integration_id=integration_id))

    return render_template("integrations/edit.html", row=row,
                           stored=sorted(credential_names_set(db, integration_id)),
                           credential_names=CREDENTIAL_NAMES,
                           directory=profile,
                           can_sync=bool(profile) or has_adapter(
                               row["integration_type"]),
                           **form_context())


@integrations_bp.route("/credentials/<int:integration_id>", methods=["POST"])
@perm("integrations")
def credentials(integration_id):
    """Seal one credential, or forget it. The value never comes back to a screen."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    require_directory_right(directory_profile(db, integration_id))
    name = (request.form.get("credential_name") or "").strip()

    if name not in CREDENTIAL_NAMES:
        flash("That is not one of the credentials this desk stores.", "error")
        return redirect(url_for("integrations.edit", integration_id=integration_id))

    back = redirect(url_for("integrations.edit", integration_id=integration_id))

    if request.form.get("action") == "clear":
        removed = db.execute("""
            DELETE FROM integration_credentials
            WHERE integration_id = ? AND credential_name = ?
        """, (integration_id, name)).rowcount
        db.commit()
        if removed:
            log_activity("DELETE", "integrations",
                         "Removed the stored {} from {}.".format(
                             CREDENTIAL_LABEL[name], row["name"]),
                         record_type="integration", record_id=integration_id,
                         details={"credential": name})
            flash("The stored {} was removed.".format(CREDENTIAL_LABEL[name]), "info")
        else:
            flash("No {} was stored on this connection.".format(
                CREDENTIAL_LABEL[name]), "info")
        return back

    if not store_credential(db, integration_id, name,
                            request.form.get("credential_value") or ""):
        flash("Type the {} you want stored.".format(CREDENTIAL_LABEL[name]), "error")
        return back

    db.commit()
    # Only the name of the credential is logged; the audit scrubber would blank a
    # value that slipped in, but this view never hands one over in the first place.
    log_activity("UPDATE", "integrations",
                 "Stored a new {} for {}.".format(CREDENTIAL_LABEL[name], row["name"]),
                 record_type="integration", record_id=integration_id,
                 details={"credential": name})
    flash("{} saved. It is stored sealed and cannot be read back.".format(
        CREDENTIAL_LABEL[name]), "success")
    return back


@integrations_bp.route("/toggle/<int:integration_id>", methods=["POST"])
@perm("integrations")
def toggle(integration_id):
    """Activate or stand a connection down; a stopped connection never syncs."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    require_directory_right(directory_profile(db, integration_id))
    status = "inactive" if row["status"] == "active" else "active"
    db.execute("""
        UPDATE integrations
        SET status = ?, next_run_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (status, next_run_at(dict(row, status=status)), integration_id))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "{} set to {}.".format(row["name"], status),
                 record_type="integration", record_id=integration_id,
                 previous={"status": row["status"]}, updated={"status": status})
    flash("{} is {}.".format(row["name"], status), "success")
    return redirect(url_for("integrations.dashboard"))


@integrations_bp.route("/delete/<int:integration_id>", methods=["POST"])
@perm("integrations")
def delete(integration_id):
    """Remove a connection and the history that only made sense beside it."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))

    if profile is not None:
        from services.directory import delete_connection as delete_directory_connection
        problem = delete_directory_connection(db, profile)
        if problem:
            flash(problem, "error")
            return redirect(url_for("integrations.dashboard"))
        flash("{} was deleted with its directory profile, its credential and its"
              " history.".format(row["name"]), "success")
        return redirect(url_for("integrations.dashboard"))

    # Credentials, directory accounts and mappings go with the row by themselves
    # (ON DELETE CASCADE); the three history tables keep orphan rows under
    # ON DELETE SET NULL, so they are cleared here rather than left unexplained.
    for table in ("integration_connection_tests", "integration_sync_logs",
                  "integration_errors"):
        db.execute("DELETE FROM {} WHERE integration_id = ?".format(table),
                   (integration_id,))
    db.execute("DELETE FROM integrations WHERE id = ?", (integration_id,))
    db.commit()

    log_activity("DELETE", "integrations",
                 "Deleted the {} connection ({}).".format(row["name"],
                                                           row["integration_type"]),
                 details={"integration_type": row["integration_type"],
                          "endpoint": row["endpoint"]})
    flash("{} was deleted, with its stored credential and its history.".format(
        row["name"]), "success")
    return redirect(url_for("integrations.dashboard"))


@integrations_bp.route("/logs")
@perm("integrations")
def logs():
    """What this desk did, read from the same activity log the audit console uses."""
    db = get_db()
    rows = db.execute("""
        SELECT occurred_at, username, role, action, description, outcome,
               record_id, ip_address
        FROM audit_logs
        WHERE module = 'integrations'
        ORDER BY id DESC LIMIT ?
    """, (ACTIVITY_LOG,)).fetchall()
    return render_template("integrations/logs.html", rows=rows, limit=ACTIVITY_LOG)


# ------------------------------------------------------- connection testing
@integrations_bp.route("/test-connection/<int:integration_id>", methods=["POST"])
@perm("integrations")
def check_connection(integration_id):
    """Ask the endpoint whether it answers, and record what it said."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))

    if profile is not None:
        from services.directory import record_connection_test
        ok, message, _facts = record_connection_test(db, profile)
        flash("{}: {}".format(row["name"], message), "success" if ok else "error")
        return redirect(same_site_back(url_for("integrations.dashboard")))

    ok, message, elapsed = test_connection(db, row)
    db.execute("""
        INSERT INTO integration_connection_tests
            (integration_id, status, message, response_time_ms)
        VALUES (?, ?, ?, ?)
    """, (integration_id, "success" if ok else "failed", message[:500], elapsed))
    db.commit()
    log_activity("TEST", "integrations",
                 "Tested the {} connection: {}.".format(
                     row["name"], "reached" if ok else "not reached"),
                 outcome="SUCCESS" if ok else "FAILED",
                 record_type="integration", record_id=integration_id,
                 details={"message": message[:300], "response_time_ms": elapsed})
    if ok:
        flash("Reached {} in {} ms.".format(row["name"], elapsed), "success")
    else:
        flash(message, "error")
    return redirect(same_site_back(url_for("integrations.dashboard")))


@integrations_bp.route("/connection-history/<int:integration_id>")
@perm("integrations")
def connection_history(integration_id):
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))
    tests = db.execute("""
        SELECT status, message, response_time_ms, tested_at
        FROM integration_connection_tests
        WHERE integration_id = ?
        ORDER BY id DESC LIMIT ?
    """, (integration_id, CONNECTION_HISTORY)).fetchall()
    summary = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS passed
        FROM integration_connection_tests WHERE integration_id = ?
    """, (integration_id,)).fetchone()
    return render_template("integrations/connection_history.html", row=row,
                           directory=profile, tests=tests, summary=summary)


# ------------------------------------------------------------ synchronization
@integrations_bp.route("/sync/<int:integration_id>", methods=["POST"])
@perm("integrations")
def sync(integration_id):
    """Run one synchronization now for a connection that has an adapter."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))
    if not (profile is not None or has_adapter(row["integration_type"])):
        flash("{} has no data adapter, so there is nothing to synchronize. Test "
              "the connection instead.".format(row["integration_type"]), "info")
        return redirect(url_for("integrations.dashboard"))

    ok, message = run_integration_sync(db, row, sync_type="manual")
    if profile is None:
        log_activity("SYNC", "integrations",
                     "Synchronized {}: {}.".format(
                         row["name"], "completed" if ok else "failed"),
                     outcome="SUCCESS" if ok else "FAILED",
                     record_type="integration", record_id=integration_id,
                     details={"message": message[:300]})
    flash("{}: {}".format(row["name"], message), "success" if ok else "error")
    return redirect(url_for("integrations.dashboard"))


@integrations_bp.route("/schedule/<int:integration_id>", methods=["POST"])
@perm("integrations")
def schedule(integration_id):
    """Switch automatic synchronization on or off for one connection."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    require_directory_right(directory_profile(db, integration_id))

    interval = clean_interval(request.form.get("sync_interval"))
    if interval is None:
        flash("Choose an interval between {} and {} minutes.".format(
            MIN_SYNC_INTERVAL, MAX_SYNC_INTERVAL), "error")
        return redirect(url_for("integrations.edit", integration_id=integration_id))

    interval, enabled, note = schedule_fields(
        row, interval, request.form.get("sync_enabled") == "1",
        has_directory=directory_profile(db, integration_id) is not None)
    if note:
        flash(note, "info")

    db.execute("""
        UPDATE integrations
        SET sync_enabled = ?, sync_interval = ?, next_run_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (1 if enabled else 0, interval,
          next_run_at(dict(row, sync_enabled=1 if enabled else 0,
                           sync_interval=interval)), integration_id))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "{} synchronization {} at {} minutes.".format(
                     row["name"], "scheduled" if enabled else "stopped", interval),
                 record_type="integration", record_id=integration_id,
                 previous={"sync_enabled": row["sync_enabled"],
                           "sync_interval": row["sync_interval"]},
                 updated={"sync_enabled": 1 if enabled else 0,
                          "sync_interval": interval})
    flash("Automatic synchronization now runs every {} minutes.".format(interval)
          if enabled else
          "Automatic synchronization stopped; the next run would have been every "
          "{} minutes.".format(interval), "success")
    return redirect(url_for("integrations.edit", integration_id=integration_id))


# ---------------------------------------------------------------- monitoring
@integrations_bp.route("/monitoring")
@perm("integrations")
def monitoring():
    """Health of the whole desk: runs, results and the errors still open."""
    db = get_db()
    stats = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN sync_enabled = 1 THEN 1 ELSE 0 END) AS scheduled
        FROM integrations
    """).fetchone()
    test_stats = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM integration_connection_tests
    """).fetchone()
    run_stats = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS still_running
        FROM integration_sync_logs
        WHERE started_at >= datetime('now', '-30 days')
    """).fetchone()
    errors = db.execute("""
        SELECT e.*, i.name AS integration_name
        FROM integration_errors e
        LEFT JOIN integrations i ON i.id = e.integration_id
        ORDER BY e.resolved, e.id DESC LIMIT ?
    """, (MONITORED_ERRORS,)).fetchall()
    runs = db.execute("""
        SELECT l.*, i.name AS integration_name
        FROM integration_sync_logs l
        LEFT JOIN integrations i ON i.id = l.integration_id
        ORDER BY l.id DESC LIMIT ?
    """, (MONITORED_RUNS,)).fetchall()
    return render_template("integrations/monitoring.html", stats=stats,
                           test_stats=test_stats, run_stats=run_stats,
                           cards=registry_rows(db), errors=errors, runs=runs)


@integrations_bp.route("/errors/<int:error_id>/resolve", methods=["POST"])
@perm("integrations")
def resolve_error(error_id):
    db = get_db()
    error = error_or_404(db, error_id)
    if error["resolved"]:
        flash("That error was already marked resolved.", "info")
        return redirect(url_for("integrations.monitoring"))

    db.execute("UPDATE integration_errors SET resolved = 1 WHERE id = ?",
               (error["id"],))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Marked an {} error on integration {} as resolved.".format(
                     error["error_type"] or "integration",
                     error["integration_id"] or "a removed connection"),
                 record_id=error["integration_id"],
                 details={"error_id": error["id"],
                          "error_message": (error["error_message"] or "")[:300]})
    flash("Error marked as resolved.", "success")
    return redirect(url_for("integrations.monitoring"))


# ------------------------------------------------------- AD mapping desk
@integrations_bp.route("/ad-mapping/<int:integration_id>")
@perm("integrations")
def ad_mapping(integration_id):
    """Directory accounts read so far, and what they mean for HR records."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))
    if profile is not None:
        flash("This connection is managed on its directory screens; the staging"
              " desk belongs to registry-only Active Directory rows.", "info")
        return redirect(url_for("integrations.directory_sync",
                                connection_id=profile["id"]))
    if row["integration_type"] != AD_TYPE:
        flash("Only an Active Directory / LDAP connection has a mapping desk.", "info")
        return redirect(url_for("integrations.edit", integration_id=integration_id))

    users = mapping_rows(db, integration_id)
    return render_template("integrations/ad_mapping.html", row=row, users=users,
                           departments=ad_departments(db, integration_id),
                           choices=department_choices(db),
                           accounts=len(users),
                           active_accounts=sum(1 for u in users if u["is_active"]),
                           pending=sum(1 for u in users
                                       if u["mapping_status"] != "mapped"),
                           mapped_departments=sum(1 for u in users
                                                  if u["hr_department_name"]))


@integrations_bp.route("/ad-sync/<int:integration_id>", methods=["POST"])
@perm("integrations")
def ad_sync(integration_id):
    """Read the directory under a search base and store what it returns."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    profile = require_directory_right(directory_profile(db, integration_id))
    if profile is not None:
        flash("Use the directory screen to synchronize this connection.", "info")
        return redirect(url_for("integrations.directory_sync",
                                connection_id=profile["id"]))
    if row["integration_type"] != AD_TYPE:
        abort(404)

    search_base = clean_text(request.form.get("search_base"), 512)
    if not search_base:
        flash("Enter the search base to read, for example "
              "OU=Staff,DC=example,DC=local.", "error")
        return redirect(url_for("integrations.ad_mapping",
                                integration_id=integration_id))

    if search_base != (row["search_base"] or ""):
        # The base an administrator just used becomes the scheduled base: a run
        # from the worker has nobody to ask what it should search.
        db.execute("""
            UPDATE integrations SET search_base = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (search_base, integration_id))
        db.commit()
        row = integration_or_404(db, integration_id)

    ok, message = run_integration_sync(db, row, sync_type="AD_USER_SYNC")
    log_activity("SYNC", "integrations",
                 "Read Active Directory {} from {}: {}.".format(
                     search_base, row["name"], "stored" if ok else "failed"),
                 outcome="SUCCESS" if ok else "FAILED",
                 record_type="integration", record_id=integration_id,
                 details={"search_base": search_base, "message": message[:300]})
    flash(message, "success" if ok else "error")
    return redirect(url_for("integrations.ad_mapping",
                            integration_id=integration_id))


@integrations_bp.route("/ad-mapping/<int:integration_id>/prepare", methods=["POST"])
@perm("integrations")
def ad_prepare(integration_id):
    """Open a pending employee mapping for every directory account that has none.

    It creates no employee and changes no existing match: it only records which
    accounts are waiting for a person to decide.
    """
    db = get_db()
    row = integration_or_404(db, integration_id)
    created = prepare_employee_mappings(db, integration_id)
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Prepared {} pending employee mapping(s) for {}.".format(
                     created, row["name"]),
                 record_type="integration", record_id=integration_id,
                 details={"created": created})
    flash("{} pending employee mapping(s) prepared. No employee record was "
          "touched.".format(created), "success")
    return redirect(url_for("integrations.ad_mapping",
                            integration_id=integration_id))


@integrations_bp.route("/ad-mapping/<int:integration_id>/departments",
                       methods=["POST"])
@perm("integrations")
def ad_departments_save(integration_id):
    """Say which company department each directory department string means."""
    db = get_db()
    row = integration_or_404(db, integration_id)
    decided = []
    for department in ad_departments(db, integration_id):
        name = department["ad_department"]
        choice = request.form.get("department_{}".format(name))
        if choice is None:
            continue
        try:
            save_department_mapping(db, integration_id, name, choice or None)
        except ValueError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("integrations.ad_mapping",
                                    integration_id=integration_id))
        decided.append((name, choice or "unmapped"))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Saved {} department mapping(s) for {}.".format(
                     len(decided), row["name"]),
                 record_type="integration", record_id=integration_id,
                 details={"mappings": ["{} -> {}".format(n, c) for n, c in decided]})
    flash("Department mappings saved." if decided
          else "Nothing was chosen, so no mapping changed.", "success")
    return redirect(url_for("integrations.ad_mapping",
                            integration_id=integration_id))
