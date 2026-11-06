"""Payroll desk permissions, answered by the application's role matrix.

The payroll screens name the fine-grained keys the matrix defines; this module
only adapts those keys to the blueprint's decorators and templates, so a desk's
rights are decided in one place for every module.
"""

from core.access import current_user
from core.access import guard
from core.access import has_permission


PAYROLL_PERMISSIONS = {
    "payroll_dashboard": "View Payroll Dashboard",
    "payroll_employees": "View Employee Payroll Profiles",
    "payroll_employee_create": "Create Employee Payroll Profiles",
    "payroll_employee_edit": "Edit Employee Payroll Profiles",
    "payroll_salary_view": "View Salary Details",
    "payroll_grade_view": "View Salary Grades",
    "payroll_grade_create": "Create Salary Grades",
    "payroll_grade_edit": "Edit Salary Grades",
    "payroll_grade_delete": "Deactivate Salary Grades",
    "payroll_salary_create": "Create Salary Structures",
    "payroll_salary_edit": "Edit Salary Structures",
    "payroll_salary_deactivate": "Toggle Salary Structures",
    "payroll_allowance_view": "View Allowances",
    "payroll_allowance_create": "Create Allowances",
    "payroll_allowance_edit": "Edit Allowances",
    "payroll_allowance_delete": "Toggle Allowances",
    "payroll_deduction_view": "View Deductions",
    "payroll_deduction_create": "Create Deductions",
    "payroll_deduction_edit": "Edit Deductions",
    "payroll_deduction_delete": "Toggle Deductions",
    "payroll_history_view": "View Salary History",
    "payroll_process": "Run Monthly Payroll",
    "payroll_submit": "Submit Payroll",
    "payroll_approve": "Approve Payroll",
    "payroll_reject": "Reject Payroll",
    "payroll_payment": "Pay Salaries",
    "payroll_own_payslip": "Own Payslip",
    "payroll_reports": "View Payroll Reports",
    "payroll_statutory_view": "View Statutory Rules",
    "payroll_statutory_create": "Create Statutory Rules",
    "hr_overtime_payroll": "Send Overtime to Payroll",
}


def has_payroll_permission(permission):
    """Whether the signed-in account holds one payroll right right now."""
    user = current_user()
    if user is None:
        return False
    return has_permission(permission, user)


def require_payroll_permission(permission):
    """Protect a payroll route with the matrix, refusals included in the log."""
    return guard(modules=(permission,))


def register_payroll_template_helpers(app):
    @app.context_processor
    def inject_payroll_permissions():
        return {
            "has_payroll_permission": has_payroll_permission,
            "payroll_permissions": PAYROLL_PERMISSIONS,
        }
