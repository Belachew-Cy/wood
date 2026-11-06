"""HR overtime permissions.

The permission keys themselves live in ``core.config``; this module only maps
the overtime ones to labels and gives the overtime routes one guard that speaks
the same vocabulary as the rest of the application.
"""

from core.access import current_user
from core.access import guard
from core.access import has_permission


HR_OVERTIME_PERMISSIONS = {
    "hr_overtime_view": "View Overtime",
    "hr_overtime_create": "Submit Overtime",
    "hr_overtime_approve": "Approve Overtime",
    "hr_overtime_verify": "Verify Overtime",
    "hr_overtime_policy": "Manage Overtime Policy",
    "hr_overtime_payroll": "Send Overtime to Payroll",
}


def has_hr_permission(permission):
    """Whether the signed-in account holds one overtime permission now."""
    user = current_user()
    if user is None or user["status"] != "Active":
        return False
    return has_permission(permission, user)


def require_hr_permission(permission):
    """Route decorator: sign-in, active account and the named right."""
    return guard(modules=(permission,))


def register_hr_template_helpers(app):
    @app.context_processor
    def inject_hr_permissions():
        return {
            "has_hr_permission": has_hr_permission,
            "hr_overtime_permissions": HR_OVERTIME_PERMISSIONS,
        }
