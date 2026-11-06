"""Directory screens, mounted under the one System Integration desk.

These are the same account-management screens that used to hang off
``/admin/directory``: connections, group-to-role mappings, preview/apply
synchronization and the accounts a directory owns. They stay guarded by the
``accounts`` module - the merger moved the address, not the access rule - while
the connection rows themselves are ordinary entries of the integration registry.

The old addresses still answer with a 307 so a bookmark or an open tab keeps
working, and the old endpoint names survive as redirects for any template that
has not been updated yet.
"""

from core.access import current_user
from core.access import perm
from core.access import role_choices
from core.config import directory_integration_enabled
from core.db import get_db
from core.routing import route
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.directory import DIRECTORY_KIND_LABEL
from services.directory import DIRECTORY_NO_PASSWORD
from services.directory import all_connections
from services.directory import apply_sync
from services.directory import clean_dn
from services.directory import connection_by_id
from services.directory import connection_summary
from services.directory import create_connection
from services.directory import delete_connection
from services.directory import delete_mapping
from services.directory import directory_available
from services.directory import directory_health
from services.directory import discover_groups_into
from services.directory import due_connections
from services.directory import get_run
from services.directory import managed_accounts
from services.directory import mappings
from services.directory import missing_provider
from services.directory import normalize_connection_form
from services.directory import plan_sync
from services.directory import recent_runs
from services.directory import record_connection_test
from services.directory import release_account
from services.directory import run_items
from services.directory import save_mapping
from services.directory import secret_mask
from services.directory import set_connection_enabled
from services.directory import set_mapping_enabled
from services.directory import update_connection

from .routes import integrations_bp


def closed_out():
    """A redirect when the module is switched off or its packages are missing."""
    if directory_integration_enabled() and directory_available():
        return None
    flash("Directory integration is not available on this installation.", "error")
    return redirect(url_for("admin_settings"))


def connection_or_404(connection_id):
    connection = connection_by_id(get_db(), connection_id)
    if connection is None:
        abort(404)
    return connection


def mapping_or_404(mapping_id):
    row = get_db().execute("""
        SELECT m.*, c.name AS connection_name
        FROM directory_group_mappings AS m
        JOIN directory_registry AS c ON c.id = m.connection_id
        WHERE m.id = ?
    """, (mapping_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def provider_notes(connection):
    """What the screens must warn about before any directory call is made."""
    notes = []
    missing = missing_provider(connection["kind"])
    if missing:
        notes.append("Install the {} package on this server to reach this"
                     " directory.".format(missing))
    if secret_mask(connection) != "Stored":
        notes.append("No credential is stored, so nothing can be read yet.")
    plain = (connection["kind"] == "ldap"
             and not (connection["server_url"] or "").lower().startswith("ldaps://")
             and not connection["use_starttls"])
    if plain:
        notes.append("This connection would send its bind credential in the clear.")
    if connection["kind"] == "entra" and not connection["allowed_domains"]:
        notes.append("No domain allow list is set, so every mailbox in the tenant is"
                     " in scope.")
    if not connection["enabled"]:
        notes.append("The connection is disabled; synchronization will skip it.")
    return notes


# ------------------------------------------------------------------ screens
@integrations_bp.route("/directory", methods=["GET", "POST"])
@perm("accounts")
def directory_connections():
    db = get_db()
    blocked = closed_out()
    if blocked is not None:
        return blocked

    if request.method == "POST":
        connection = connection_or_404(request.form.get("connection_id", 0))
        action = request.form.get("action", "")
        if action == "enabled":
            wanted = request.form.get("enabled") == "1"
            set_connection_enabled(db, connection, wanted)
            flash("{} is now {}.".format(connection["name"],
                                         "enabled" if wanted else "disabled"),
                  "success")
        elif action == "test":
            ok, message, _facts = record_connection_test(db, connection)
            flash(message, "success" if ok else "error")
        elif action == "delete":
            problem = delete_connection(db, connection)
            if problem:
                flash(problem, "error")
            else:
                flash("{} deleted.".format(connection["name"]), "success")
        return redirect(url_for("integrations.directory_connections"))

    rows = all_connections(db)
    return render_template("integrations/directory.html",
                           connections=rows,
                           health=directory_health(db),
                           runs=recent_runs(db, None, 8),
                           due={row["id"] for row in due_connections(db)},
                           summaries={row["id"]: connection_summary(db, row)
                                      for row in rows},
                           notes={row["id"]: provider_notes(row) for row in rows},
                           kind_label=DIRECTORY_KIND_LABEL)


@integrations_bp.route("/directory/connection", methods=["GET", "POST"])
@integrations_bp.route("/directory/connection/<int:connection_id>",
                       methods=["GET", "POST"])
@perm("accounts")
def directory_connection(connection_id=None):
    db = get_db()
    blocked = closed_out()
    if blocked is not None:
        return blocked
    connection = connection_or_404(connection_id) if connection_id else None
    kind = connection["kind"] if connection is not None else request.args.get(
        "kind", "ldap")

    if request.method == "POST":
        values, problems = normalize_connection_form(request.form, kind, connection)
        for problem in problems:
            flash(problem, "error")
        if not problems:
            if connection is None:
                created, problem = create_connection(db, values, current_user())
                if problem:
                    flash(problem, "error")
                else:
                    # A brand new connection is proved before it is trusted: a mistyped
                    # base DN or a wrong secret costs less to find here than during the
                    # first synchronization.
                    ok, message, _facts = record_connection_test(
                        db, connection_by_id(db, created))
                    flash("{} created. {}".format(values["name"], message),
                          "success" if ok else "error")
                    return redirect(url_for("integrations.directory_groups",
                                            connection_id=created))
            else:
                problem = update_connection(db, connection, values)
                if problem:
                    flash(problem, "error")
                else:
                    flash("Connection saved.", "success")
                    return redirect(url_for("integrations.directory_connections"))

    return render_template("integrations/directory_connection.html",
                           connection=connection,
                           kind=kind,
                           roles=role_choices(db),
                           secret_state=secret_mask(connection) if connection
                                         else "Not stored",
                           missing_package=missing_provider(kind),
                           kind_label=DIRECTORY_KIND_LABEL)


@integrations_bp.route("/directory/groups/<int:connection_id>",
                       methods=["GET", "POST"])
@perm("accounts")
def directory_groups(connection_id):
    db = get_db()
    blocked = closed_out()
    if blocked is not None:
        return blocked
    connection = connection_or_404(connection_id)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "discover":
            rows, problem = discover_groups_into(db, connection, current_user())
            if problem:
                flash(problem, "error")
            else:
                flash("Read {} group{} from the directory.".format(
                    len(rows), "" if len(rows) == 1 else "s"), "success")
        elif action == "map":
            key = request.form.get("group_key") or request.form.get("existing_key")
            problem = save_mapping(db, connection, clean_dn(key),
                                   request.form.get("group_name", "").strip(),
                                   request.form.get("role_key", "").strip(),
                                   request.form.get("nested") == "on",
                                   request.form.get("note", "").strip())
            flash(problem or "Mapping saved.", "error" if problem else "success")
        elif action == "unmap":
            mapping = mapping_or_404(request.form.get("mapping_id", 0))
            delete_mapping(db, mapping)
            flash("Mapping removed.", "success")
        elif action == "toggle":
            mapping = mapping_or_404(request.form.get("mapping_id", 0))
            wanted = request.form.get("enabled") == "1"
            set_mapping_enabled(db, mapping, wanted)
            flash("Mapping is now {}.".format("enabled" if wanted else "disabled"),
                  "success")
        return redirect(url_for("integrations.directory_groups",
                                connection_id=connection["id"]))

    stored = mappings(db, connection["id"])
    return render_template("integrations/directory_groups.html",
                           connection=connection,
                           stored=stored,
                           mapped_keys={row["group_key"]: row["role_key"]
                                        for row in stored
                                        if row["enabled"] and row["role_key"]},
                           roles=role_choices(db),
                           kind_label=DIRECTORY_KIND_LABEL[connection["kind"]],
                           notes=provider_notes(connection))


@integrations_bp.route("/directory/sync/<int:connection_id>",
                       methods=["GET", "POST"])
@integrations_bp.route("/directory/run/<int:run_id>", methods=["GET", "POST"])
@perm("accounts")
def directory_sync(connection_id=None, run_id=None):
    db = get_db()
    blocked = closed_out()
    if blocked is not None:
        return blocked
    run = get_run(db, run_id) if run_id else None
    if run is not None:
        connection = connection_by_id(db, run["connection_id"])
    else:
        connection = connection_or_404(connection_id)
    if connection is None:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "preview":
            preview_id, seen, problem = plan_sync(db, connection, current_user())
            if problem:
                flash(problem, "error")
            else:
                flash("Previewed {} account{} from the directory.".format(
                    seen, "" if seen == 1 else "s"), "success")
            return redirect(url_for("integrations.directory_sync",
                                    run_id=preview_id))
        if action == "apply":
            target = int(request.form.get("run_id") or run_id or 0)
            result, problem = apply_sync(db, connection, target, current_user())
            if problem:
                flash(problem, "error")
                return redirect(url_for("integrations.directory_sync",
                                        connection_id=connection["id"]))
            counts = result.get("counts", {})
            flash("Applied: {} created, {} linked, {} updated, {} roles granted,"
                  " {} disabled, {} restored.".format(
                      counts.get("create", 0), counts.get("link", 0),
                      counts.get("update", 0), counts.get("granted", 0),
                      counts.get("disabled", 0), counts.get("restored", 0)),
                  "success" if result.get("status") == "success" else "info")
            for row in result.get("handover", []):
                # An initial password exists once, here, and is never kept in a
                # readable place - the same hand-over the CSV import makes.
                flash("{} / {}: {} - hand this over, it is not stored.".format(
                    row["name"], row["username"], row["password"]), "info")
            return redirect(url_for("integrations.directory_sync",
                                    connection_id=connection["id"]))

    shown = run
    if shown is None:
        latest = recent_runs(db, connection["id"], 1)
        shown = latest[0] if latest else None
    items = run_items(db, shown["id"]) if shown is not None else []
    return render_template("integrations/directory_sync.html",
                           connection=connection,
                           run=shown,
                           items=items,
                           runs=recent_runs(db, connection["id"], 10),
                           summary=connection_summary(db, connection),
                           kind_label=DIRECTORY_KIND_LABEL[connection["kind"]],
                           notes=provider_notes(connection))


@integrations_bp.route("/directory/accounts", methods=["GET", "POST"])
@perm("accounts")
def directory_accounts():
    db = get_db()
    blocked = closed_out()
    if blocked is not None:
        return blocked

    if request.method == "POST":
        row = db.execute("SELECT * FROM users WHERE id = ?",
                         (request.form.get("user_id", 0),)).fetchone()
        if row is None or not row["directory_connection_id"]:
            flash("That account is not managed by a directory connection.", "error")
            return redirect(url_for("integrations.directory_accounts"))
        issued, problem = release_account(
            db, row, current_user(), request.form.get("reset") == "1")
        if problem:
            flash(problem, "error")
        else:
            flash("Account released from directory management.", "success")
            if issued:
                flash("Initial password for {}: {} - hand it over, it is not"
                      " stored.".format(row["email"], issued), "info")
        return redirect(url_for("integrations.directory_accounts"))

    selected = request.args.get("connection_id", type=int)
    rows = managed_accounts(db, selected)
    counts = {
        "domain_only": sum(1 for row in rows
                           if str(row["password_hash"] or "").startswith(
                               DIRECTORY_NO_PASSWORD)),
        "departed": sum(1 for row in rows if row["directory_departed"]),
        "disabled": sum(1 for row in rows if row["status"] != "Active"),
    }
    return render_template("integrations/directory_accounts.html",
                           accounts=rows,
                           counts=counts,
                           connections=all_connections(db),
                           selected=selected,
                           health=directory_health(db),
                           kind_label=DIRECTORY_KIND_LABEL)


# ---------------------------------------------------------- legacy addresses
def _legacy(target, **values):
    """307 to the merged address, keeping the method, body and query string."""
    location = url_for(target, **values)
    if request.query_string:
        location += "?" + request.query_string.decode()
    return redirect(location, code=307)


@route("/admin/directory", methods=["GET", "POST"])
@perm("accounts")
def admin_directory():
    return _legacy("integrations.directory_connections")


@route("/admin/directory/connection", methods=["GET", "POST"])
@route("/admin/directory/connection/<int:connection_id>",
       methods=["GET", "POST"])
@perm("accounts")
def admin_directory_connection(connection_id=None):
    if connection_id is None:
        return _legacy("integrations.directory_connection")
    return _legacy("integrations.directory_connection",
                   connection_id=connection_id)


@route("/admin/directory/groups/<int:connection_id>", methods=["GET", "POST"])
@perm("accounts")
def admin_directory_groups(connection_id):
    return _legacy("integrations.directory_groups", connection_id=connection_id)


@route("/admin/directory/sync/<int:connection_id>", methods=["GET", "POST"])
@route("/admin/directory/run/<int:run_id>", methods=["GET", "POST"])
@perm("accounts")
def admin_directory_sync(connection_id=None, run_id=None):
    if run_id is not None:
        return _legacy("integrations.directory_sync", run_id=run_id)
    return _legacy("integrations.directory_sync", connection_id=connection_id)


@route("/admin/directory/accounts", methods=["GET", "POST"])
@perm("accounts")
def admin_directory_accounts():
    return _legacy("integrations.directory_accounts")
