"""Server-side enforcement of the department action matrix.

Every department answers the same ten verbs. This module decides which verb a
matched route asks for and refuses the request before the view runs when the
account does not hold it, so a typed URL meets the same gate as a hidden button.
The existing route guards still run: this is one more layer, not a replacement.
"""

from flask import abort
from flask import current_app
from flask import request

from core.access import current_user
from core.access import has_permission
from core.config import department_permission_key


# Path prefixes, most specific first. A route outside these prefixes falls back
# to the module that defines its view; shared routes (reports, approvals, auth,
# dashboards, self-service) are exempt because their own guards scope them.
SCOPE_RULES = (
    ("/admin/hr/attendance", "attendance"),
    ("/admin/hr/leave", "attendance"),
    ("/admin/hr/overtime", "attendance"),
    ("/admin/hr/training-performance", "recruitment"),
    ("/admin/hr/recruitment", "recruitment"),
    ("/admin/hr/payroll", "payroll"),
    ("/admin/hr", "hr"),
    ("/admin/audit", "audit"),
    ("/admin/directory", "it"),
    ("/admin/hiring-permissions", "admin"),
    ("/admin/letter-permissions", "admin"),
    ("/admin/password-security", "admin"),
    ("/admin/users", "admin"),
    ("/admin/roles", "admin"),
    ("/admin/departments", "admin"),
    ("/admin/department-access", "admin"),
    ("/admin/settings", "admin"),
    ("/admin/notifications", "admin"),
    ("/admin", "admin"),
    ("/auditor", "audit"),
    ("/management/overview", "operations"),
    ("/manage-hierarchy", "hr"),
    ("/finance", "finance"),
    ("/budget", "budget"),
    ("/payroll", "payroll"),
    ("/procurement", "procurement"),
    ("/assets", "asset"),
    ("/inventory", "inventory"),
    ("/project", "project"),
    ("/marketing/feedback", "customer"),
    ("/marketing/customers", "customer"),
    ("/marketing", "marketing"),
    ("/integrations", "it"),
    ("/letters", "letters"),
    ("/recruitment", "recruitment"),
)


MODULE_SCOPES = {
    "departments.HR.hr": "hr",
    "departments.HR.reports": "hr",
    "departments.HR.routes": "attendance",
    "departments.payroll.payroll": "payroll",
    "departments.payroll.routes": "payroll",
    "departments.finance.finance": "finance",
    "departments.budget": "budget",
    "departments.procurement.procurement": "procurement",
    "departments.asset_management": "asset",
    "departments.inventory": "inventory",
    "departments.inventory.stock_control": "inventory",
    "departments.project": "project",
    "departments.marketing": "marketing",
    "departments.audit.console": "audit",
    "departments.letters": "letters",
    "departments.recruitment": "recruitment",
    "departments.integrations.routes": "it",
    "departments.integrations.directory": "it",
    "departments.admin": "admin",
    "departments.department_management": "admin",
    "departments.organization": "hr",
}


EXEMPT_EXACT = frozenset((
    "/", "/about", "/contact", "/process", "/services", "/login", "/logout",
    "/member", "/dashboard", "/dashboard/", "/management/approval-settings",
))


# Shared and self-service surfaces. Their own guards already decide who may
# read them (report_access, can_decide_request, letter ownership, hiring
# rights), so a department verb would only duplicate that rule.
EXEMPT_PREFIXES = (
    "/static/", "/auth/", "/notifications", "/account/", "/forgot-password",
    "/reset-password", "/search", "/member", "/dashboard", "/reports",
    "/admin/reports", "/project.json", "/projects", "/organization", "/api/",
    "/approvals", "/payroll/my-", "/payroll/payslip/",
    "/recruitment/apply", "/recruitment/exam/", "/recruitment/vacancies",
    "/recruitment/my/",
)


EXPORT_WORDS = ("export", "download", ".csv", ".xlsx", ".pdf", "template",
                "barcode", "qr.png")


def scope_for_request():
    """The department scope a matched route belongs to, or None when shared."""
    path = request.path or "/"
    for prefix, scope in SCOPE_RULES:
        if path == prefix or path.startswith(prefix + "/") or (
                prefix.endswith("/hr") and path.startswith(prefix + "/")):
            return scope
    endpoint = request.endpoint or ""
    view = current_app.view_functions.get(endpoint)
    module = getattr(view, "__module__", "") if view else ""
    return MODULE_SCOPES.get(module)


def action_for_request(method):
    """The department verb a request asks for.

    Reads and views use different verbs so a role can be given the department
    lists without its record pages, and mutations are read from the endpoint,
    path and posted action name. The default for an unknown mutation is edit:
    it is the narrowest verb that can still change a record.
    """
    text = "{} {}".format(request.endpoint or "", request.path).lower()
    rule = request.url_rule.rule if request.url_rule is not None else ""
    if method in ("GET", "HEAD", "OPTIONS"):
        if "print" in text or "/label" in text:
            return "print"
        if any(word in text for word in EXPORT_WORDS):
            return "export"
        if "approve" in text:
            return "approve"
        if "reject" in text:
            return "reject"
        if any(word in text for word in ("/new", "create", "/add", "import")):
            return "create"
        if "edit" in text:
            return "edit"
        if ("detail" in text or "records/" in text or "salary" in text
                or "payslip" in text or "<int:" in rule):
            return "read"
        return "view"

    form_action = (request.form.get("action") or "").strip().lower()
    probe = "{} {}".format(text, form_action)
    if any(word in probe for word in
           ("delete", "remove", "archive", "deactivate", "discard")):
        return "delete"
    # An asset return is a hand-back, not a rejection; only the letter register
    # and an explicit return action use that verb.
    if any(word in probe for word in ("reject", "decline")) or (
            "return" in probe and ("/letters" in probe
                                   or form_action == "return")):
        return "reject"
    if any(word in probe for word in
           ("approve", "authorise", "authorize", "sign", "decision", "decide")):
        return "approve"
    if any(word in probe for word in
           ("settings", "config", "policy", "activation", "permission",
            "roles", "access")):
        return "manage"
    if any(word in probe for word in
           ("/new", "create", "/add", "submit", "register", "apply",
            "generate", "publish", "import")):
        return "create"
    return "edit"


def _scope_label(scope):
    from core.config import DEPARTMENT_SCOPE_BY_KEY
    entry = DEPARTMENT_SCOPE_BY_KEY.get(scope)
    return entry["label"] if entry else scope


def _record(action, scope):
    """Leave a trail for a sensitive read, export or print."""
    from services.audit import log_activity
    try:
        log_activity(action.upper(), scope,
                     "{} {} on {}".format(action.title(), request.method,
                                          request.path),
                     details={"endpoint": request.endpoint,
                              "department": _scope_label(scope)})
    except Exception:  # noqa: BLE001 - a trail failure must not break the page
        pass


def enforce_department_actions():
    """Refuse a department route the account's matrix does not open."""
    if request.method in ("HEAD", "OPTIONS"):
        return None
    path = request.path or "/"
    if path in EXEMPT_EXACT or any(path.startswith(prefix)
                                   for prefix in EXEMPT_PREFIXES):
        return None
    user = current_user()
    if user is None or user["status"] != "Active" or user["role"] == "admin":
        return None
    scope = scope_for_request()
    if not scope:
        return None
    action = action_for_request(request.method)
    key = department_permission_key(scope, action)
    if has_permission(key, user):
        if action in ("read", "export", "print"):
            _record(action, scope)
        return None
    from services.audit import log_activity
    try:
        log_activity("DENY", scope,
                     "Refused {} {} (needs {})".format(
                         request.method, path, key),
                     outcome="DENIED",
                     details={"endpoint": request.endpoint,
                              "required": key,
                              "department": _scope_label(scope)})
    except Exception:  # noqa: BLE001
        pass
    abort(403)
