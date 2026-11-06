"""Wood & Kitchen management system: the application entry point.

Every screen lives in a department package under ``departments/``; behaviour
shared between departments sits in ``core/`` and ``services/``. This module
builds the Flask app, attaches the request hooks, mounts the routers and
blueprints, and runs the schema maintenance the startup needs.
"""
import os
from datetime import timedelta

from flask import Flask

import core.auth  # noqa: F401 - importing registers the sign-in routes
from core.access import csrf_field
from core.access import csrf_token
from core.access import end_locked_sessions
from core.access import enforce_csrf
from core.access import hold_until_password_changed
from core.config import SESSION_HOURS
from core.config import env_flag
from core.config import load_app_secret
from core.db import backup_db_command
from core.db import check_db_command
from core.db import close_db
from core.db import init_db
from core.db import init_indexes
from core.formatting import money_etb
from core.routing import router
from core.web import bad_request
from core.web import forbidden
from core.web import inject_user
from core.web import method_not_allowed
from core.web import not_found
from core.web import print_qr
from core.web import register_public_routes
from core.web import secure_response
from core.web import server_error
from core.web import upload_too_large
from departments import register_blueprints
from departments.integrations import integrations_bp
from departments.integrations.database import ensure_integrations_schema
from departments.integrations.scheduler import integration_scheduler_command
from departments.payroll import init_payroll
from departments.payroll import init_payroll_schema
from departments.recruitment import recruitment_bp
from services.audit import audit_time
from services.audit import ensure_audit_schema
from services.audit import friendly_date
from services.audit import init_auditor_tables
from services.asset import ensure_asset_schema
from services.department_guard import enforce_department_actions
from services.directory import ensure_directory_schema
from services.directory import sync_directory_command
from services.email import flush_pending_emails
from services.letters import has_letter_permission
from services.letters import letter_kind_label
from services.letters import letter_type_label
from services.marketing import ensure_marketing_schema
from services.org import ensure_hierarchy_schema
from services.project import ensure_project_schema
from services.recency import ensure_recency_schema
from services.recruitment import ensure_hiring_schema
from services.recruitment import ensure_recruitment_schema

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config.update(
    # The employee form carries a photo plus two batches of documents; the limit
    # stops an oversized body before any of it is parsed.
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_flag("WK_SECURE_COOKIES"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
)

app.secret_key = load_app_secret()

@app.context_processor
def inject_letter_permissions():
    return {"has_letter_permission": has_letter_permission}


# ------------------------------------------------------------------- hooks
app.teardown_appcontext(close_db)
app.context_processor(inject_user)
app.after_request(secure_response)


def send_queued_mail(response):
    """Mail a route queued leaves after its write is committed, never before."""
    if response.status_code < 400:
        flush_pending_emails()
    return response


app.after_request(send_queued_mail)

# Order matters: CSRF is checked before the session gates run.
app.before_request(enforce_csrf)
app.before_request(end_locked_sessions)
app.before_request(hold_until_password_changed)
# The department matrix is the last gate before the view, so a locked session
# or a pending password change still wins, and every route that reaches a view
# has passed the same server-side action check whether or not a button existed.
app.before_request(enforce_department_actions)

for code, handler in ((400, bad_request), (403, forbidden),
                      (404, not_found), (405, method_not_allowed),
                      (413, upload_too_large), (500, server_error)):
    app.register_error_handler(code, handler)


# ---------------------------------------------------- template environment
app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["csrf_field"] = csrf_field
app.jinja_env.globals["print_qr"] = print_qr
app.jinja_env.globals["letter_type_label"] = letter_type_label
app.jinja_env.globals["letter_kind_label"] = letter_kind_label
app.jinja_env.filters["audit_time"] = audit_time
app.jinja_env.filters["friendly_date"] = friendly_date

if "money" not in app.jinja_env.filters:
    # The budget screens and the auditor money columns ask for one filter that
    # already exists under another name.
    app.jinja_env.filters["money"] = money_etb

# ------------------------------------------------------------------ routes
register_public_routes(app)
router.register(app)
register_blueprints(app)
init_payroll(app)
app.register_blueprint(recruitment_bp)
app.register_blueprint(integrations_bp)


# --------------------------------------------------------------- commands
app.cli.command("backup-db")(backup_db_command)
app.cli.command("check-db")(check_db_command)
app.cli.command("sync-directory")(sync_directory_command)
app.cli.command("integration-scheduler")(integration_scheduler_command)


# ---------------------------------------------------------------- startup
init_db()
ensure_hierarchy_schema()
ensure_recruitment_schema()
ensure_hiring_schema()
init_auditor_tables()
ensure_audit_schema()
ensure_marketing_schema()
ensure_project_schema()
ensure_asset_schema()
ensure_recency_schema()
ensure_directory_schema()
ensure_integrations_schema()
init_payroll_schema(app)
init_indexes()

if __name__ == "__main__":
    app.run(host=os.environ.get("WK_HOST", "127.0.0.1"),
            port=int(os.environ.get("WK_PORT", "5000")),
            debug=env_flag("WK_DEBUG"))
