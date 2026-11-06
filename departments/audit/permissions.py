
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Permissions

Integrates with the existing shared RBAC system.

IMPORTANT:
Configure the existing RBAC checker in configure_rbac()
using your actual shared authorization function.
"""

from functools import wraps
from flask import abort, current_app, g


# -------------------------------------------------------
# AUDIT PERMISSIONS
# -------------------------------------------------------

AUDIT_PERMISSIONS = {
    "VIEW": "audit.view",
    "CREATE": "audit.create",
    "ASSIGN": "audit.assign",
    "MANAGE": "audit.manage",
    "REVIEW": "audit.review",
    "APPROVE": "audit.approve",
    "EXPORT": "audit.export",
    "MANAGE_CHECKLISTS": "audit.manage_checklists",
}


# -------------------------------------------------------
# EXISTING RBAC ADAPTER
# -------------------------------------------------------

_RBAC_CHECKER = None


def configure_rbac(checker):
    """
    Connect the existing shared RBAC permission function.

    Expected checker signature:

        checker(user, permission) -> bool

    Example:

        configure_rbac(existing_has_permission)
    """

    global _RBAC_CHECKER

    if not callable(checker):
        raise TypeError(
            "RBAC checker must be callable."
        )

    _RBAC_CHECKER = checker


def get_rbac_checker():
    """
    Return the configured checker.

    The checker can also be supplied through Flask config:

        AUDIT_RBAC_CHECKER
    """

    checker = current_app.config.get(
        "AUDIT_RBAC_CHECKER"
    )

    return checker or _RBAC_CHECKER


# -------------------------------------------------------
# CURRENT USER
# -------------------------------------------------------

def get_current_user():
    """
    Retrieve the authenticated user.

    Supports Flask-Login and an existing user stored
    in Flask's g object.
    """

    try:
        from flask_login import current_user

        if current_user.is_authenticated:
            return current_user

    except (ImportError, RuntimeError):
        pass

    return getattr(g, "user", None)


# -------------------------------------------------------
# PERMISSION CHECK
# -------------------------------------------------------

def has_audit_permission(permission, user=None):
    """
    Check an audit permission using the existing RBAC system.

    Fail closed if no shared RBAC checker is configured.
    """

    user = user or get_current_user()

    if user is None:
        return False

    checker = get_rbac_checker()

    if checker is None:
        current_app.logger.error(
            "Audit RBAC checker is not configured."
        )
        return False

    try:
        return bool(
            checker(user, permission)
        )

    except Exception:
        current_app.logger.exception(
            "Audit permission check failed: %s",
            permission
        )
        return False


# -------------------------------------------------------
# PERMISSION DECORATOR
# -------------------------------------------------------

def require_audit_permission(permission):
    """
    Protect an Audit Management route.

    Usage:

        @audit_bp.route("/dashboard")
        @require_audit_permission("audit.view")
        def dashboard():
            ...
    """

    def decorator(view_function):

        @wraps(view_function)
        def wrapped(*args, **kwargs):

            if not has_audit_permission(permission):
                abort(403)

            return view_function(
                *args,
                **kwargs
            )

        return wrapped

    return decorator


# -------------------------------------------------------
# AUDIT PERMISSION HELPERS
# -------------------------------------------------------

def can_view_audit(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["VIEW"],
        user
    )


def can_create_audit(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["CREATE"],
        user
    )


def can_assign_auditors(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["ASSIGN"],
        user
    )


def can_manage_audit(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["MANAGE"],
        user
    )


def can_review_findings(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["REVIEW"],
        user
    )


def can_approve_audit_reports(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["APPROVE"],
        user
    )


def can_export_audit_reports(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["EXPORT"],
        user
    )


def can_manage_checklists(user=None):
    return has_audit_permission(
        AUDIT_PERMISSIONS["MANAGE_CHECKLISTS"],
        user
    )


# -------------------------------------------------------
# END OF AUDIT PERMISSIONS
# -------------------------------------------------------
