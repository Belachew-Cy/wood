"""Deployment settings, roles, permissions and fixed vocabulary."""
from typing import Any

from flask import request
from pathlib import Path
import os
import re
import secrets


try:
    import msal
except ImportError:  # Microsoft sign-in stays hidden until the package is present.
    msal = None


# This module sits one level below the project root, next to the database files,
# templates and uploads it points at.
BASE_DIR = Path(__file__).resolve().parent.parent


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


from database import database_config


DB_PATH = database_config.DB_PATH


PROJECTS_PATH = BASE_DIR / "project.json"


KEY_PATH = Path(os.environ.get(
    "WK_SECRET_KEY_FILE", str(BASE_DIR / ".secret_key"))).resolve()


BACKUP_DIR = database_config.BACKUP_DIR


def load_app_secret():
    """The signing secret: WK_SECRET_KEY, the key file, or a fresh one written to it.

    Sessions and the sealing of stored directory credentials both start from this
    one value, so it is resolved in exactly one place. A caller must never print
    it or pass it into a template.
    """
    secret = os.environ.get("WK_SECRET_KEY", "").strip()
    if secret:
        return secret
    if KEY_PATH.exists():
        stored = KEY_PATH.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    secret = secrets.token_hex(32)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(secret, encoding="utf-8")
    try:
        KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return secret


# Directory integration. The read timeouts are short on purpose: a synchronization
# runs inside a request, and an unreachable controller must give up before the
# administrator's browser does.
try:
    DIRECTORY_LDAP_TIMEOUT = max(
        2, min(int(os.environ.get("WK_DIRECTORY_TIMEOUT", "10")), 60))
except ValueError:
    DIRECTORY_LDAP_TIMEOUT = 10


DIRECTORY_GRAPH_TIMEOUT = DIRECTORY_LDAP_TIMEOUT


try:
    DIRECTORY_MAX_USERS = max(
        1, min(int(os.environ.get("WK_DIRECTORY_MAX_USERS", "2000")), 20000))
except ValueError:
    DIRECTORY_MAX_USERS = 2000


DIRECTORY_MAX_MEMBERS = DIRECTORY_MAX_USERS


# Per connection, so the preview history an administrator can still read does not
# grow without limit on a busy scheduler.
DIRECTORY_RETAINED_RUNS = 40


# The whole integration can be switched off without a code change, which is what
# an installation wants on the day its domain controller is being rebuilt.
DIRECTORY_ENABLED = env_flag("WK_DIRECTORY_ENABLED", True)


MS_CLIENT_ID = os.environ.get("WK_MS_CLIENT_ID", "").strip()


MS_CLIENT_SECRET = os.environ.get("WK_MS_CLIENT_SECRET", "").strip()


MS_TENANT_ID = os.environ.get("WK_MS_TENANT_ID", "").strip()


MS_AUTHORITY = (os.environ.get("WK_MS_AUTHORITY", "").strip()
                or ("https://login.microsoftonline.com/{}".format(MS_TENANT_ID)
                    if MS_TENANT_ID else ""))


MS_REDIRECT_URI = os.environ.get("WK_MS_REDIRECT_URI", "").strip()


MS_SCOPES = ("User.Read",)


MS_ALLOWED_DOMAINS = tuple(
    part.strip().lower()
    for part in os.environ.get("WK_MS_ALLOWED_DOMAINS", "").split(",")
    if part.strip())


MS_STATE_TTL_SECONDS = 600


# Outbound mail. An installation with no mail host still records every message it
# would have sent, so the delivery log answers what happened without a mailbox.
MAIL_HOST = os.environ.get("WK_MAIL_HOST", "").strip()


try:
    MAIL_PORT = max(1, min(int(os.environ.get("WK_MAIL_PORT", "587")), 65535))
except ValueError:
    MAIL_PORT = 587


MAIL_USERNAME = os.environ.get("WK_MAIL_USERNAME", "").strip()


MAIL_PASSWORD = os.environ.get("WK_MAIL_PASSWORD", "")


MAIL_SECURITY = os.environ.get("WK_MAIL_SECURITY", "starttls").strip().lower()


if MAIL_SECURITY not in ("starttls", "ssl", "none"):
    MAIL_SECURITY = "starttls"


MAIL_FROM = os.environ.get("WK_MAIL_FROM", "").strip() or MAIL_USERNAME


MAIL_FROM_NAME = (os.environ.get("WK_MAIL_FROM_NAME", "").strip()
                  or "Dereje Belay Wood & Kitchen")


MAIL_SIGNATURE = "Dereje Belay Wood & Kitchen Cabinet Finishing"


MAIL_TIMEOUT_SECONDS = 10


# The address printed in an email link. Left blank, the request's own host is used,
# which is right for a single-site install and for the development server.
APP_BASE_URL = os.environ.get("WK_APP_BASE_URL", "").strip().rstrip("/")


try:
    RESET_TOKEN_MINUTES = max(
        5, min(int(os.environ.get("WK_RESET_TOKEN_MINUTES", "60")), 1440))
except ValueError:
    RESET_TOKEN_MINUTES = 60


USER_ROLES = ("admin", "hr", "finance", "budget", "procurement", "payroll",
              "inventory", "auditor", "employee", "member")


DASHBOARD_ENDPOINT = {
    "admin": "dashboard",
    "hr": "hr_dashboard",
    "finance": "finance.dashboard",
    "budget": "dashboard",
    "procurement": "procurement.dashboard",
    "payroll": "payroll.payroll_dashboard",
    "inventory": "inventory.dashboard",
    "auditor": "auditor_dashboard",
    "employee": "member",
    "member": "member",
}


MODULE_DASHBOARDS = frozenset(
    endpoint for endpoint in DASHBOARD_ENDPOINT.values()
    if endpoint not in ("dashboard", "member", "auditor_dashboard"))


DASHBOARD_ROLES = ("admin", "hr", "finance", "budget", "payroll", "inventory",
                   "procurement", "auditor")


ROLE_LABEL = {
    "admin": "Administrator",
    "hr": "HR",
    "finance": "Finance",
    "budget": "Budget",
    "procurement": "Procurement",
    "payroll": "Payroll",
    "inventory": "Inventory",
    "auditor": "Auditor",
    "employee": "Employee",
    "member": "My Account",
}


MODULES = ("hr", "finance", "budget", "payroll", "inventory", "asset",
           "accounts", "procurement", "approvals", "audit", "letters",
           "integrations", "marketing", "project")


MODULE_LABEL = {
    "hr": "HR module",
    "finance": "Finance module",
    "budget": "Budget module",
    "payroll": "Payroll module",
    "inventory": "Inventory & stock module",
    "asset": "Asset management module",
    "accounts": "User & role management",
    "procurement": "Procurement Management module",
    "approvals": "Approval workflow",
    "audit": "Audit trail (read only)",
    "letters": "Letters & correspondence",
    "integrations": "System integrations",
    "marketing": "Marketing & customer outreach",
    "project": "Project delivery",
}


ROLE_PERMISSIONS = {
    "admin": MODULES,
    # No role is handed Letters on its own: the administrator grants the module
    # and its actions, which is why it is absent from the defaults below. The
    # same holds for integrations - a connection can read the domain and hold a
    # service credential, so it is ticked per role rather than assumed.
    "hr": ("hr", "payroll", "approvals"),
    "finance": ("finance", "approvals"),
    "budget": ("budget", "approvals"),
    "procurement": ("procurement", "approvals"),
    "payroll": ("payroll", "approvals"),
    "inventory": ("inventory", "procurement", "approvals"),
    "auditor": ("audit",),
    "employee": (),
    "member": (),
}


# --------------------------------------------------------------------------
# Permission catalogue. The six operator-facing categories group the rights an
# administrator hands out; the final group carries the modules that have no
# finer vocabulary. A permission names the module(s) that open with it, so one
# tick is enough to make the screens behind it reachable, and the same names are
# what the role dialog, the account editor, the guards and the sidebar ask.
# Recruitment permissions are answered by the hiring desks' own rights, so they
# carry "hiring_rights" instead of a module and never widen the module list.
# --------------------------------------------------------------------------
PERMISSION_CATEGORIES = (
    {
        "key": "hr",
        "label": "HR Permissions",
        "icon": "users",
        "note": "People records, leave, attendance and the organization chart.",
        "permissions": (
            {"key": "hr_employees", "label": "Employee Management",
             "description": "Add, edit and archive staff records, photos and documents.",
             "modules": ("hr",)},
            {"key": "hr_leave", "label": "Leave Management",
             "description": "Review, record and report leave requests.",
             "modules": ("hr",)},
            {"key": "hr_attendance", "label": "Attendance Management",
             "description": "Daily attendance, check-in and check-out.",
             "modules": ("hr",)},
            {"key": "hr_departments", "label": "Department Management",
             "description": "Create departments and assign their heads.",
             "modules": ("hr",)},
            {"key": "hr_organization", "label": "Organization Structure and Hierarchy",
             "description": "Positions, reporting lines and the organization chart.",
             "modules": ("hr",)},
            {"key": "hr_reports", "label": "Employee Reports",
             "description": "Workforce, attendance, leave and performance reports.",
             "modules": ("hr",)},
        ),
    },
    {
        "key": "admin",
        "label": "Admin Permissions",
        "icon": "shield",
        "note": "Accounts, roles, configuration and the audit trail.",
        "permissions": (
            {"key": "admin_users", "label": "User Management",
             "description": "Create, edit, import and remove sign-in accounts.",
             "modules": ("accounts",)},
            {"key": "admin_departments", "label": "Department Management",
             "description": "Register units, their managers, positions and members.",
             "modules": ("accounts",)},
            {"key": "admin_roles", "label": "Role and Permission Management",
             "description": "Build roles, set what they carry and bind them to departments.",
             "modules": ("accounts",)},
            {"key": "admin_accounts", "label": "Account Management",
             "description": "Passwords, account status, locks and sign-in security.",
             "modules": ("accounts",)},
            {"key": "admin_config", "label": "System Configuration",
             "description": "Company profile, settings and integration setup.",
             "modules": ("accounts",)},
            {"key": "module_integrations", "label": "System Integration",
             "description": "External systems, credentials and sync.",
             "modules": ("integrations",)},
            {"key": "admin_logs", "label": "Activity Logs and Audit Logs",
             "description": "Activity log, audit trail and system usage reports.",
             "modules": ("audit",)},
            {"key": "admin_access", "label": "Access Control",
             "description": "Assign roles and per-account permission overrides.",
             "modules": ("accounts",)},
        ),
    },
    {
        "key": "procurement",
        "label": "Procurement Permissions",
        "icon": "handshake",
        "note": "Requisitions, purchase orders, approvals and tracking.",
        "permissions": (
            {"key": "procurement_pr", "label": "Purchase Requisition (PR)",
             "description": "Raise, revise and submit purchase requisitions.",
             "modules": ("procurement",)},
            {"key": "procurement_po", "label": "Purchase Order (PO)",
             "description": "Create purchase orders, deliveries and invoices.",
             "modules": ("procurement",)},
            {"key": "procurement_approval", "label": "Procurement Approval",
             "description": "Sign requisition and purchase-order approval steps.",
             "modules": ("procurement", "approvals")},
            {"key": "procurement_tracking", "label": "Procurement Tracking",
             "description": "Open deliveries and the order status board.",
             "modules": ("procurement",)},
            {"key": "procurement_reports", "label": "Procurement Reports",
             "description": "Requisition, order and supplier reports.",
             "modules": ("procurement",)},
        ),
    },
    {
        "key": "asset",
        "label": "Asset Management Permissions",
        "icon": "box",
        "note": "The fixed-asset register, its categories and department custody.",
        "permissions": (
            {"key": "asset_dashboard", "label": "Asset Overview",
             "description": "The asset dashboard and its figures.",
             "modules": ("asset",)},
            {"key": "asset_register", "label": "Asset Register",
             "description": "Register, revise and deactivate a unit.",
             "modules": ("asset",)},
            {"key": "asset_categories", "label": "Asset Categories",
             "description": "Build the category tree a unit is filed under.",
             "modules": ("asset",)},
            {"key": "asset_allocation", "label": "Department Allocation",
             "description": "Allocate units to departments and record returns.",
             "modules": ("asset",)},
        ),
    },
    {
        "key": "budget",
        "label": "Budget Permissions",
        "icon": "chart",
        "note": "Plans, allocations, spending and the budget approval chain.",
        "permissions": (
            {"key": "budget_management", "label": "Budget Management",
             "description": "The budget dashboard and plan overview.",
             "modules": ("budget",)},
            {"key": "budget_planning", "label": "Budget Planning",
             "description": "Create and revise budget plans and requests.",
             "modules": ("budget",)},
            {"key": "budget_allocation", "label": "Budget Allocation",
             "description": "Allocate funds to departments and projects.",
             "modules": ("budget",)},
            {"key": "budget_approval", "label": "Budget Approval",
             "description": "Decide budget requests and plan approvals.",
             "modules": ("budget", "approvals")},
            {"key": "budget_monitoring", "label": "Budget Monitoring",
             "description": "Record expenditure and watch remaining balances.",
             "modules": ("budget",)},
            {"key": "budget_reports", "label": "Budget Reports",
             "description": "Allocation and expenditure reports.",
             "modules": ("budget",)},
        ),
    },
    {
        "key": "payroll",
        "label": "Payroll Permissions",
        "icon": "cash",
        "note": "Payroll runs, salary information and payroll approval.",
        "permissions": (
            {"key": "payroll_management", "label": "Payroll Management",
             "description": "The payroll dashboard and payroll history.",
             "modules": ("payroll",)},
            {"key": "payroll_processing", "label": "Salary Processing",
             "description": "Generate payslips and mark them paid.",
             "modules": ("payroll",)},
            {"key": "payroll_salary_info", "label": "Employee Salary Information",
             "description": "See salary amounts on staff records.",
             "modules": ("payroll",)},
            {"key": "payroll_approval", "label": "Payroll Approval",
             "description": "Decide payroll approval steps.",
             "modules": ("payroll", "approvals")},
            {"key": "payroll_reports", "label": "Payroll Reports",
             "description": "Payroll register and salary reports.",
             "modules": ("payroll",)},

            # The payroll desk checks these action rights individually, so a
            # payroll officer can run the month without being able to edit the
            # salary structure behind it.
            {"key": "payroll_dashboard", "label": "Payroll Dashboard",
             "description": "The payroll desk overview and its month totals.",
             "modules": ("payroll",)},
            {"key": "payroll_employees", "label": "Payroll Profiles",
             "description": "Open the employee payroll profile register.",
             "modules": ("payroll",)},
            {"key": "payroll_employee_create", "label": "Create Payroll Profile",
             "description": "Put an employee on the payroll.",
             "modules": ("payroll",)},
            {"key": "payroll_employee_edit", "label": "Edit Payroll Profile",
             "description": "Change an employee's salary grade or structure.",
             "modules": ("payroll",)},
            {"key": "payroll_salary_view", "label": "View Salary Details",
             "description": "Read a salary structure and an employee's salary.",
             "modules": ("payroll",)},
            {"key": "payroll_grade_view", "label": "View Salary Grades",
             "description": "Read the salary grade scale.",
             "modules": ("payroll",)},
            {"key": "payroll_grade_create", "label": "Create Salary Grade",
             "description": "Add a salary grade.",
             "modules": ("payroll",)},
            {"key": "payroll_grade_edit", "label": "Edit Salary Grade",
             "description": "Revise a salary grade.",
             "modules": ("payroll",)},
            {"key": "payroll_grade_delete", "label": "Deactivate Salary Grade",
             "description": "Park a salary grade out of use.",
             "modules": ("payroll",)},
            {"key": "payroll_salary_create", "label": "Create Salary Structure",
             "description": "Add a basic-salary structure.",
             "modules": ("payroll",)},
            {"key": "payroll_salary_edit", "label": "Edit Salary Structure",
             "description": "Revise a basic-salary structure.",
             "modules": ("payroll",)},
            {"key": "payroll_salary_deactivate", "label": "Toggle Salary Structure",
             "description": "Activate or park a salary structure.",
             "modules": ("payroll",)},
            {"key": "payroll_allowance_view", "label": "View Allowances",
             "description": "Read allowance types and assignments.",
             "modules": ("payroll",)},
            {"key": "payroll_allowance_create", "label": "Create Allowance",
             "description": "Add an allowance type.",
             "modules": ("payroll",)},
            {"key": "payroll_allowance_edit", "label": "Edit Allowance",
             "description": "Revise an allowance type.",
             "modules": ("payroll",)},
            {"key": "payroll_allowance_delete", "label": "Toggle Allowance",
             "description": "Activate or park an allowance type.",
             "modules": ("payroll",)},
            {"key": "payroll_deduction_view", "label": "View Deductions",
             "description": "Read deduction types and assignments.",
             "modules": ("payroll",)},
            {"key": "payroll_deduction_create", "label": "Create Deduction",
             "description": "Add a deduction type.",
             "modules": ("payroll",)},
            {"key": "payroll_deduction_edit", "label": "Edit Deduction",
             "description": "Revise a deduction type.",
             "modules": ("payroll",)},
            {"key": "payroll_deduction_delete", "label": "Toggle Deduction",
             "description": "Activate or park a deduction type.",
             "modules": ("payroll",)},
            {"key": "payroll_history_view", "label": "View Salary History",
             "description": "Read salary change records.",
             "modules": ("payroll",)},
            {"key": "payroll_process", "label": "Run Monthly Payroll",
             "description": "Generate a month's payroll records.",
             "modules": ("payroll",)},
            {"key": "payroll_submit", "label": "Submit Payroll",
             "description": "Send a generated month for approval.",
             "modules": ("payroll",)},
            {"key": "payroll_approve", "label": "Approve Payroll",
             "description": "Approve a submitted payroll month.",
             "modules": ("payroll", "approvals")},
            {"key": "payroll_reject", "label": "Reject Payroll",
             "description": "Return a submitted payroll month.",
             "modules": ("payroll", "approvals")},
            {"key": "payroll_payment", "label": "Pay Salaries",
             "description": "Mark a payroll line paid and record the reference.",
             "modules": ("payroll",)},
            {"key": "payroll_own_payslip", "label": "Own Payslip",
             "description": "Open the signed-in employee's own payslips.",
             "modules": ("payroll",)},
            {"key": "payroll_statutory_view", "label": "View Statutory Rules",
             "description": "Read tax and pension rules.",
             "modules": ("payroll",)},
            {"key": "payroll_statutory_create", "label": "Create Statutory Rule",
             "description": "Add a tax or pension rule.",
             "modules": ("payroll",)},

            # Overtime crosses the two desks: HR verifies the hours, payroll
            # pays them. The import right needs both modules open.
            {"key": "hr_overtime_view", "label": "View Overtime",
             "description": "Read an employee's overtime requests.",
             "modules": ("hr",)},
            {"key": "hr_overtime_create", "label": "Submit Overtime",
             "description": "Request overtime for a working day.",
             "modules": ("hr",)},
            {"key": "hr_overtime_approve", "label": "Approve Overtime",
             "description": "Approve or reject a manager decision.",
             "modules": ("hr",)},
            {"key": "hr_overtime_verify", "label": "Verify Overtime",
             "description": "Check approved overtime against attendance.",
             "modules": ("hr",)},
            {"key": "hr_overtime_policy", "label": "Overtime Policy",
             "description": "Manage the standard hours and multiplier.",
             "modules": ("hr",)},
            {"key": "hr_overtime_payroll", "label": "Send Overtime to Payroll",
             "description": "Import HR-verified overtime into a payroll month.",
             "modules": ("hr", "payroll")},
        ),
    },
    {
        "key": "recruitment",
        "label": "Examination and Vacancy Permissions",
        "icon": "briefcase",
        "note": "Vacancies, candidates, examinations and recruitment reports.",
        "permissions": (
            {"key": "recruitment_exams", "label": "Examination Management",
             "description": "Write papers and their questions.",
             "hiring_rights": ("exam_view", "exam_author")},
            {"key": "recruitment_exam_scheduling", "label": "Examination Scheduling",
             "description": "Publish and close a paper.",
             "hiring_rights": ("exam_publish",)},
            {"key": "recruitment_candidate_registration", "label": "Candidate Registration",
             "description": "Register walk-in applicants.",
             "hiring_rights": ("applicant_register",)},
            {"key": "recruitment_exam_results", "label": "Examination Results",
             "description": "Read examination results.",
             "hiring_rights": ("exam_result",)},
            {"key": "recruitment_vacancies", "label": "Vacancy Management",
             "description": "Read vacancies and raise a vacancy request.",
             "hiring_rights": ("vacancy_view", "vacancy_request")},
            {"key": "recruitment_job_posting", "label": "Job Posting",
             "description": "Publish and close a job post.",
             "hiring_rights": ("vacancy_publish",)},
            {"key": "recruitment_applicants", "label": "Applicant Management",
             "description": "Read candidates and screen their applications.",
             "hiring_rights": ("applicant_view", "applicant_screen")},
            {"key": "recruitment_selection", "label": "Recruitment and Selection",
             "description": "Book interviews, score panels and register hires.",
             "hiring_rights": ("applicant_hire", "interview_view", "interview_book",
                               "interview_score", "interview_decide")},
            {"key": "recruitment_reports", "label": "Recruitment Reports",
             "description": "Vacancy and hiring reports.",
             "hiring_rights": ("hiring_report",)},
        ),
    },
    {
        "key": "marketing",
        "label": "Marketing Permissions",
        "icon": "megaphone",
        "note": "Campaign plans, leads, research, digital pushes and feedback.",
        "permissions": (
            {"key": "marketing_dashboard", "label": "Marketing Overview",
             "description": "The marketing dashboard and its figures.",
             "modules": ("marketing",)},
            {"key": "marketing_plans", "label": "Marketing Plans",
             "description": "Raise and revise a marketing plan and its budget.",
             "modules": ("marketing",)},
            {"key": "marketing_approval", "label": "Marketing Plan Approval",
             "description": "Approve or refuse a plan a desk has raised.",
             "modules": ("marketing",)},
            {"key": "marketing_campaigns", "label": "Campaigns and Channels",
             "description": "Run the campaign register behind a plan.",
             "modules": ("marketing",)},
            {"key": "marketing_leads", "label": "Leads and Customers",
             "description": "Register interest and work the follow-up list.",
             "modules": ("marketing",)},
            {"key": "marketing_research", "label": "Market Research",
             "description": "Record studies, segments and their findings.",
             "modules": ("marketing",)},
            {"key": "marketing_feedback", "label": "Customer Feedback",
             "description": "Log what a customer reported and close it out.",
             "modules": ("marketing",)},
            {"key": "marketing_reports", "label": "Marketing Reports",
             "description": "Campaign, pipeline and feedback reports and exports.",
             "modules": ("marketing",)},
        ),
    },
    {
        "key": "project",
        "label": "Project Management Permissions",
        "icon": "briefcase",
        "note": "Client jobs, the team behind each one, its milestones and tasks.",
        "permissions": (
            {"key": "project_dashboard", "label": "Project Overview",
             "description": "The project dashboard and its figures.",
             "modules": ("project",)},
            {"key": "project_register", "label": "Project Register",
             "description": "Raise and revise a project, its budget and its dates.",
             "modules": ("project",)},
            {"key": "project_approval", "label": "Project Approval",
             "description": "Send a project to the approval chain and read its"
                            " decisions.",
             "modules": ("project", "approvals")},
            {"key": "project_team", "label": "Project Teams",
             "description": "Assign and release the staff working on a project.",
             "modules": ("project",)},
            {"key": "project_milestones", "label": "Milestones",
             "description": "Set the dates a job has to hold and mark them done.",
             "modules": ("project",)},
            {"key": "project_tasks", "label": "Project Tasks",
             "description": "Raise work, hand it out and report its progress.",
             "modules": ("project",)},
            {"key": "project_reports", "label": "Project Reports",
             "description": "Delivery, workload and budget reports and exports.",
             "modules": ("project",)},
        ),
    },
)


# The modules with no finer vocabulary keep one checkbox each, grouped by the
# business they serve. Approvals sits here as well as behind the approval
# permissions: a desk that only reviews leave or expenses still needs the shared
# queue. Integration is not here - it opens credentials, so it sits in Admin.
MODULE_PERMISSION_CATEGORIES = (
    {
        "key": "finance",
        "label": "Finance Permissions",
        "icon": "wallet",
        "note": "Income, expenses, invoices and payments.",
        "permissions": (
            {"key": "module_finance", "label": "Finance Management",
             "description": "Income, expenses, invoices and payments.",
             "modules": ("finance",)},
        ),
    },
    {
        "key": "inventory",
        "label": "Inventory and Stock Permissions",
        "icon": "cart",
        "note": "The stock register, movements, physical counts, reorder"
                " alerts and reports.",
        "permissions": (
            # The coarse tick is kept because roles saved before the action
            # rights existed store only this row; it covers all of them.
            {"key": "module_inventory", "label": "Whole Inventory Desk",
             "description": "Every stock screen and action below, as one tick.",
             "modules": ("inventory",)},
            {"key": "inventory_dashboard", "label": "Inventory Overview",
             "description": "The stock dashboard and its figures.",
             "modules": ("inventory",)},
            {"key": "inventory_items", "label": "Items and Catalogue",
             "description": "Register, revise and deactivate a stock item, and"
                            " print its label.",
             "modules": ("inventory",)},
            {"key": "inventory_movements", "label": "Stock Movements",
             "description": "Receive or issue stock and read its history.",
             "modules": ("inventory",)},
            {"key": "inventory_stocktake", "label": "Stocktake Counting",
             "description": "Open a physical count, record quantities, submit it"
                            " and post an approved correction.",
             "modules": ("inventory",)},
            {"key": "inventory_stocktake_approve", "label": "Stocktake Approval",
             "description": "Approve or return a count another desk submitted.",
             "modules": ("inventory",)},
            {"key": "inventory_alerts", "label": "Reorder Alerts",
             "description": "Items at or below their reorder level.",
             "modules": ("inventory",)},
            {"key": "inventory_reports", "label": "Stock Reports",
             "description": "Stock, valuation and movement reports and their"
                            " exports.",
             "modules": ("inventory",)},
        ),
    },
    {
        "key": "letters",
        "label": "Letters and Correspondence Permissions",
        "icon": "mail",
        "note": "The letter register and the actions a desk may take in it.",
        "permissions": (
            {"key": "module_letters", "label": "Letter Register",
             "description": "Open the register and its workflow.",
             "modules": ("letters",)},
        ),
    },
    {
        "key": "audit",
        "label": "Audit and Oversight Permissions",
        "icon": "monitor",
        "note": "Read-only oversight of every desk, its reviews and findings.",
        "permissions": (
            {"key": "module_audit", "label": "Audit & Oversight Desk",
             "description": "The read-only audit console, departmental"
                            " reviews, findings and oversight reports.",
             "modules": ("audit",)},
        ),
    },
    {
        "key": "common",
        "label": "Common Permissions",
        "icon": "clipboard",
        "note": "The shared approval queue.",
        "permissions": (
            {"key": "module_approvals", "label": "Approval Workflow",
             "description": "The shared approval queue and request form.",
             "modules": ("approvals",)},
        ),
    },
)


ALL_PERMISSION_CATEGORIES = PERMISSION_CATEGORIES + MODULE_PERMISSION_CATEGORIES


PERMISSION_LIST = tuple(
    permission
    for category in ALL_PERMISSION_CATEGORIES
    for permission in category["permissions"])


PERMISSION_KEYS: tuple[Any, ...] = tuple(permission["key"] for permission in PERMISSION_LIST)


PERMISSION_BY_KEY = {permission["key"]: permission for permission in PERMISSION_LIST}


# --------------------------------------------------------------------------
# Department actions. Every department answers the same ten verbs, so an
# administrator reads one row per department instead of learning a separate
# vocabulary per desk. The keys carry the scope and the verb
# (dept_finance_create) and name the module that opens with them, exactly like
# the catalogue above. They stay outside PERMISSION_LIST on purpose: the desk
# catalogue keeps its hand written descriptions, while these are drawn as a
# compact matrix in the role and account editors. They are still full
# permission keys for the guards, account overrides and the audit trail.
# --------------------------------------------------------------------------
DEPARTMENT_ACTIONS = (
    {"key": "create", "label": "Create", "short": "C",
     "description": "Add records, raise requests and submit work."},
    {"key": "read", "label": "Read", "short": "R",
     "description": "Open one record, its detail page, file or API answer."},
    {"key": "view", "label": "View", "short": "V",
     "description": "Dashboards, lists and reports for the department."},
    {"key": "edit", "label": "Edit", "short": "E",
     "description": "Change an existing record, its status or its details."},
    {"key": "delete", "label": "Delete", "short": "D",
     "description": "Remove, archive or deactivate a record when policy allows."},
    {"key": "approve", "label": "Approve", "short": "A",
     "description": "Sign a request or close a review in the department."},
    {"key": "reject", "label": "Reject", "short": "X",
     "description": "Return, decline or reject a request with a reason."},
    {"key": "export", "label": "Export", "short": "Xp",
     "description": "Download the department's records as Excel, PDF or CSV."},
    {"key": "print", "label": "Print", "short": "P",
     "description": "Print a record, label, payslip or letter."},
    {"key": "manage", "label": "Manage", "short": "M",
     "description": "Department settings, policy, roles and configuration."},
)


DEPARTMENT_SCOPES = (
    {"key": "admin", "label": "Administration and User Management",
     "modules": ("accounts",),
     "note": "Accounts, roles, units, settings and the access screens."},
    {"key": "hr", "label": "Human Resources (HR)",
     "modules": ("hr",),
     "note": "People records, documents, overtime, HR reports and settings."},
    {"key": "finance", "label": "Finance Management",
     "modules": ("finance",),
     "note": "Income, expenses, invoices and payments."},
    {"key": "budget", "label": "Budget Management",
     "modules": ("budget",),
     "note": "Plans, allocations, expenditure and budget requests."},
    {"key": "payroll", "label": "Payroll Management",
     "modules": ("payroll",),
     "note": "Salary records, payroll runs, payslips and statutory rules."},
    {"key": "procurement", "label": "Procurement Management",
     "modules": ("procurement",),
     "note": "Requisitions, purchase orders, suppliers and deliveries."},
    {"key": "asset", "label": "Property and Asset Management",
     "modules": ("asset",),
     "note": "The asset register, categories and allocations."},
    {"key": "inventory", "label": "Inventory and Warehouse Management",
     "modules": ("inventory",),
     "note": "Stock items, movements, counts, alerts and stock reports."},
    {"key": "project", "label": "Project Management",
     "modules": ("project",),
     "note": "Projects, milestones, tasks, teams and delivery reports."},
    {"key": "marketing", "label": "Sales and Marketing",
     "modules": ("marketing",),
     "note": "Plans, campaigns, leads, research and marketing reports."},
    {"key": "customer", "label": "Customer Service",
     "modules": ("marketing",),
     "note": "Customer feedback, cases and the customer directory."},
    {"key": "operations", "label": "Business Development and Operations",
     "modules": ("audit",),
     "note": "The management overview and cross-desk operating picture."},
    {"key": "it", "label": "Information Technology (IT)",
     "modules": ("integrations",),
     "note": "External systems, directory connections, credentials and sync."},
    {"key": "audit", "label": "Audit Management",
     "modules": ("audit",),
     "note": "The audit trail, departmental reviews, findings and oversight."},
    {"key": "letters", "label": "Letter Management",
     "modules": ("letters",),
     "note": "The letter register, its workflow and correspondence."},
    {"key": "recruitment", "label": "Recruitment and Training",
     "modules": ("hr",),
     "note": "Vacancies, applicants, examinations, interviews and training."},
    {"key": "attendance", "label": "Attendance and Leave",
     "modules": ("hr",),
     "note": "Daily attendance, leave requests and their review."},
)


def department_permission_key(scope, action):
    return "dept_{}_{}".format(scope, action)


DEPARTMENT_PERMISSION_LIST = tuple(
    {
        "key": department_permission_key(scope["key"], action["key"]),
        "label": "{}: {}".format(scope["label"], action["label"]),
        "description": action["description"],
        "modules": scope["modules"],
        "department_scope": scope["key"],
        "department_action": action["key"],
    }
    for scope in DEPARTMENT_SCOPES
    for action in DEPARTMENT_ACTIONS)


DEPARTMENT_PERMISSION_BY_KEY = {
    permission["key"]: permission for permission in DEPARTMENT_PERMISSION_LIST}


DEPARTMENT_PERMISSION_KEYS = tuple(DEPARTMENT_PERMISSION_BY_KEY)


DEPARTMENT_SCOPE_BY_KEY = {scope["key"]: scope for scope in DEPARTMENT_SCOPES}


# The guards, the account overrides and the role dialog all resolve a department
# key through the same mapping, so it joins the catalogue rather than shadowing
# it. PERMISSION_LIST stays the hand written desk catalogue: the standard role
# bundles and the "never configured" defaults must not inherit these rows.
PERMISSION_BY_KEY = {**PERMISSION_BY_KEY, **DEPARTMENT_PERMISSION_BY_KEY}


PERMISSION_KEYS = tuple(PERMISSION_BY_KEY)


# A coarse desk tick stands for every action right that desk defines. Inventory
# is the only category with both sizes: roles saved before the action rights
# existed still store the single module tick and must keep opening the desk.
COARSE_PERMISSION_COVERAGE = {"module_inventory": "inventory"}


def permission_closure(keys):
    """Widen a coarse desk tick to the action rights it stands for.

    Department verbs are deliberately left out: they are answered by the
    matrix, so a coarse tick may not re-add one an administrator cleared.
    """
    keys = set(keys)
    for coarse, module in COARSE_PERMISSION_COVERAGE.items():
        if coarse in keys:
            keys |= {key for key, entry in PERMISSION_BY_KEY.items()
                     if module in (entry.get("modules") or ())
                     and key not in DEPARTMENT_PERMISSION_BY_KEY}
    return keys


PERMISSION_HIRING_RIGHTS = {
    permission["key"]: tuple(permission["hiring_rights"])
    for permission in PERMISSION_LIST if permission.get("hiring_rights")}


RECRUITMENT_PERMISSION_KEYS = tuple(PERMISSION_HIRING_RIGHTS)


# A role with nothing saved yet is drawn with every permission its module ticks
# have always implied, so the first save cannot narrow a desk by accident. A
# permission that names several modules needs all of them: an approval right
# belongs to its own desk, not to every role that happens to hold the queue.
# Recruitment rights name hiring actions instead of modules, so they are derived
# from the hiring baseline rather than granted to every role that names no module.
def default_role_permissions(role_key):
    modules = set(ROLE_PERMISSIONS.get(role_key, ()))
    return frozenset(
        permission["key"] for permission in PERMISSION_LIST
        if not permission.get("hiring_rights")
        and set(permission.get("modules", ())) <= modules)


USER_STATUS = ("Active", "Disabled")


USERNAME_PREFIX = "DB"


USERNAME_FIRST = 1000


PR_REQUESTER_ROLES = tuple(role for role in USER_ROLES
                           if role not in ("member", "auditor"))


PASSWORD_MIN_LENGTH = 8


INITIAL_PASSWORD_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"


INITIAL_PASSWORD_LOWER = "abcdefghjkmnpqrstuvwxyz"


INITIAL_PASSWORD_DIGITS = "23456789"


INITIAL_PASSWORD_LENGTH = 12


PR_MANAGER_LIMIT = 10000


PR_DIRECTOR_LIMIT = 50000


PR_ROUTE_AMOUNT = "amount"


PR_ROUTE_SEQUENTIAL = "sequential"


PR_ROUTE_MODES = (PR_ROUTE_AMOUNT, PR_ROUTE_SEQUENTIAL)


EMPLOYEE_ACCOUNT_STATUS = {"Active": "Active", "On Leave": "Active",
                           "Inactive": "Disabled"}


# --------------------------------------------------------------------------
# The left management rail. Groups follow the desks the company runs; the five
# operational groups sit first and the remaining module groups follow. A group
# is dropped unless the account holds its module, and one link can narrow that
# further: "modules" needs one of the named ticks, "roles" names the seats
# allowed, and "letter" is a per-account letter action. "params"/"anchor" point
# several submenus at distinct views of the one central page rather than cloning
# it, so a group never needs a second dashboard of its own.
# --------------------------------------------------------------------------
SIDEBAR_NAV = (
    ("Admin Management", "accounts", (
        {"label": "Admin Dashboard", "target": "admin", "icon": "chart",
         "permissions": ("admin_users", "admin_departments", "admin_roles",
                         "admin_accounts", "admin_config", "admin_access")},
        {"label": "User Management", "target": "admin_users", "icon": "users",
         "permission": "admin_users"},
        {"label": "Department Management", "target": "admin_departments",
         "icon": "briefcase", "permission": "admin_departments"},
        {"label": "Role & Permissions", "target": "admin_roles", "icon": "shield",
         "permission": "admin_roles"},
        {"label": "Announcements", "target": "admin_notifications", "icon": "megaphone"},
        {"label": "System Integration", "target": "integrations.dashboard",
         "icon": "network", "modules": ("integrations",)},
    )),
    # The integration desk belongs to Admin Management, but a role may hold the
    # integration tick without the accounts tick and then that group is not
    # drawn. This label-less fallback keeps the desk on that rail too; an
    # account that already sees the link above deduplicates it by href, so the
    # group renders nothing and the rail still carries the module once.
    (None, "integrations", (
        {"label": "System Integration", "target": "integrations.dashboard",
         "icon": "network"},
    )),
    # One procurement module. Requisitions and orders stay separate records and
    # separate screens, but they are reached from this one group: the register
    # raises a PR, the shared approval queue carries both chains, the board
    # carries orders and their delivery state, and reports/history read the same
    # tables. The group itself is module-less, so a requisitioner, an approver
    # and an audit seat each see exactly the links their own rights open.
    ("Procurement Management", None, (
        {"label": "Purchase Requisitions (PR)", "target": "procurement.requisitions",
         "icon": "file", "roles": PR_REQUESTER_ROLES},
        {"label": "Purchase Orders (PO)", "target": "procurement.dashboard",
         "icon": "cart", "modules": ("procurement", "audit"),
         "anchor": "purchase-orders"},
        {"label": "PR Approval Workflow", "target": "approvals.dashboard",
         "icon": "clipboard", "modules": ("approvals", "audit"),
         "params": {"type": "Purchase", "status": "Pending"}},
        {"label": "PO Approval Workflow", "target": "approvals.dashboard",
         "icon": "check", "modules": ("approvals", "audit"),
         "params": {"type": "Purchase", "status": "Approved"}},
        {"label": "Procurement Tracking and Status", "target": "procurement.dashboard",
         "icon": "truck", "modules": ("procurement", "audit"),
         "params": {"order_status": "open"}, "anchor": "purchase-orders"},
        {"label": "Procurement Reports", "target": "reports_centre",
         "icon": "chart", "modules": ("procurement",),
         "permission": "procurement_reports",
         "anchor": "reports-procurement-management"},
        {"label": "Procurement History", "target": "procurement.dashboard",
         "icon": "clock", "modules": ("procurement", "audit"),
         "params": {"view": "history"}, "anchor": "purchase-requests"},
    )),
    # The letter register, its workflow and the read-only auditor view. Actions
    # beyond viewing follow each account's own letter grants, so a link is only
    # offered when the server would accept it.
    ("Letter Management", "letters", (
        {"label": "Create Letter", "target": "letters.create", "icon": "file",
         "letter": "letters_create", "letter_categories": True},
        {"label": "Incoming Letters", "target": "letters.inbox", "icon": "inbox",
         "letter": "letters_view"},
        {"label": "Outgoing Letters", "target": "letters.outbox", "icon": "mail",
         "letter": "letters_view"},
        {"label": "Letter Approval Workflow", "target": "letters.dashboard",
         "icon": "check", "params": {"view": "approvals"},
         "anchor": "awaiting-signature", "letter": "letters_approve"},
        {"label": "Letter Status and Tracking", "target": "letters.dashboard",
         "icon": "target", "letter": "letters_view"},
        {"label": "Letter History and Archives", "target": "letters.dashboard",
         "icon": "clock", "params": {"status": "Archived"},
         "letter": "letters_view"},
        {"label": "Letter Audit", "target": "auditor_letters", "icon": "shield",
         "modules": ("audit",)},
    )),
    ("HR Management", "hr", (
        {"label": "HR Dashboard", "target": "hr_dashboard", "icon": "chart",
         "permissions": ("hr_employees", "hr_leave", "hr_attendance",
                         "hr_departments", "hr_organization", "hr_reports")},
        {"label": "Employees", "target": "employees", "icon": "users",
         "permission": "hr_employees"},
        {"label": "Attendance", "target": "attendance", "icon": "calendar",
         "permission": "hr_attendance"},
        {"label": "Recruitment Desk", "target": "recruitment.hr_vacancies",
         "icon": "briefcase"},
        {"label": "Leave Requests", "target": "leave_management", "icon": "calendar",
         "permission": "hr_leave"},
        {"label": "Overtime", "target": "hr.overtime_dashboard", "icon": "clock",
         "permissions": ("hr_overtime_view", "hr_overtime_create")},
        {"label": "Overtime Approvals", "target": "hr.overtime_approvals",
         "icon": "check", "permission": "hr_overtime_approve"},
        {"label": "Overtime Verification",
         "target": "hr.overtime_hr_verification", "icon": "clipboard",
         "permission": "hr_overtime_verify"},
        {"label": "Overtime Reports", "target": "hr.overtime_reports",
         "icon": "chart", "permission": "hr_overtime_view"},
        {"label": "Overtime Audit", "target": "hr.overtime_audit",
         "icon": "shield", "permission": "hr_overtime_view"},
        {"label": "Training & Performance", "target": "training_performance",
         "icon": "graduation", "permission": "hr_reports"},
        {"label": "Organization", "target": "organization", "icon": "network",
         "permission": "hr_organization"},
        {"label": "Hierarchy Setup", "target": "manage_hierarchy", "icon": "network",
         "permission": "hr_organization"},
        {"label": "HR Settings", "target": "hr_settings.index", "icon": "gear"},
    )),
    ("Finance Management", "finance", (
        {"label": "Finance Dashboard", "target": "finance.dashboard", "icon": "chart"},
        {"label": "Income & Expenses", "target": "finance.transactions", "icon": "wallet"},
        {"label": "Invoices & Payments", "target": "finance.invoices", "icon": "cash"},
        {"label": "Recruitment Budget", "target": "recruitment.finance_desk",
         "icon": "wallet"},
    )),
    ("Budget Management", "budget", (
        {"label": "Dashboard", "target": "dashboard", "icon": "chart",
         "permissions": ("budget_management", "budget_planning",
                         "budget_allocation", "budget_approval",
                         "budget_monitoring", "budget_reports")},
        {"label": "Budget Plans", "target": "budget.plan_list", "icon": "file",
         "permission": "budget_planning"},
        {"label": "Allocations", "target": "budget.allocations", "icon": "target",
         "permission": "budget_allocation"},
        {"label": "Budget Requests", "target": "budget.request_list", "icon": "clipboard",
         "permissions": ("budget_planning", "budget_approval")},
        {"label": "Expenditures", "target": "budget.expenditures", "icon": "cash",
         "permission": "budget_monitoring"},
    )),
    ("Payroll Management", "payroll", (
        {"label": "Payroll Dashboard", "target": "payroll.payroll_dashboard",
         "icon": "chart", "permission": "payroll_dashboard"},
        {"label": "Run Payroll", "target": "payroll.payroll_processing",
         "icon": "cash", "permission": "payroll_process"},
        {"label": "Payroll Profiles", "target": "payroll.employee_profiles",
         "icon": "users", "permission": "payroll_employees"},
        {"label": "Salary Grades", "target": "payroll.salary_grades",
         "icon": "target", "permission": "payroll_grade_view"},
        {"label": "Salary Structures", "target": "payroll.salary_structures",
         "icon": "briefcase", "permission": "payroll_salary_view"},
        {"label": "Allowances", "target": "payroll.allowances",
         "icon": "wallet", "permission": "payroll_allowance_view"},
        {"label": "Deductions", "target": "payroll.deductions",
         "icon": "cash", "permission": "payroll_deduction_view"},
        {"label": "Salary History", "target": "payroll.salary_history",
         "icon": "clock", "permission": "payroll_history_view"},
        {"label": "Statutory Rules", "target": "payroll.statutory_rules",
         "icon": "shield", "permission": "payroll_statutory_view"},
        {"label": "Payroll Reports", "target": "payroll.payroll_reports",
         "icon": "chart", "permission": "payroll_reports"},
        {"label": "My Payslips", "target": "payroll.my_payslips",
         "icon": "file", "permission": "payroll_own_payslip"},
    )),
    ("Inventory & Stock", "inventory", (
        {"label": "Inventory Dashboard", "target": "inventory.dashboard", "icon": "chart",
         "permission": "inventory_dashboard"},
        {"label": "Inventory Items", "target": "inventory.items", "icon": "box",
         "permission": "inventory_items"},
        {"label": "Stock In / Out", "target": "inventory.stock_movement", "icon": "truck",
         "permission": "inventory_movements"},
        {"label": "Movement History", "target": "inventory.history", "icon": "clock",
         "permission": "inventory_movements"},
        {"label": "Stocktake", "target": "inventory.stocktakes", "icon": "clipboard",
         "permissions": ("inventory_stocktake", "inventory_stocktake_approve")},
        {"label": "Reorder Alerts", "target": "inventory.alerts", "icon": "box",
         "permission": "inventory_alerts"},
        {"label": "Stock Reports", "target": "inventory.reports", "icon": "chart",
         "permission": "inventory_reports"},
    )),
    # The fixed-asset register beside stock: one module, four rights. The register
    # and the allocation screen read the same rows, and every link is filtered by
    # its own right so a custodian desk never sees a button the server refuses.
    ("Asset Management", "asset", (
        {"label": "Asset Dashboard", "target": "asset.dashboard", "icon": "chart",
         "permission": "asset_dashboard"},
        {"label": "Asset Register", "target": "asset.assets", "icon": "box",
         "permission": "asset_register"},
        {"label": "Asset Categories", "target": "asset.categories", "icon": "file",
         "permissions": ("asset_categories", "asset_register")},
        {"label": "Department Assets", "target": "asset.departments",
         "icon": "users",
         "permissions": ("asset_allocation", "asset_register", "asset_dashboard")},
        {"label": "Allocate Asset", "target": "asset.allocate",
         "icon": "briefcase", "permission": "asset_allocation"},
    )),
    # One marketing module, six desks. A plan is raised by whoever holds the plan
    # right and signed by whoever holds the approval right, so the two links point
    # at the same list with different filters rather than a second screen. A
    # customer is read from the invoices Finance already keeps, so that link opens
    # a register, not a form.
    ("Marketing Management", "marketing", (
        {"label": "Marketing Dashboard", "target": "marketing.dashboard",
         "icon": "chart", "permission": "marketing_dashboard"},
        {"label": "Marketing Plans", "target": "marketing.plans", "icon": "file",
         "permission": "marketing_plans"},
        {"label": "Plan Approval", "target": "marketing.plans", "icon": "check",
         "params": {"status": "Pending"}, "permission": "marketing_approval"},
        {"label": "Campaigns", "target": "marketing.campaigns", "icon": "megaphone",
         "permission": "marketing_campaigns"},
        {"label": "Digital Marketing", "target": "marketing.digital", "icon": "network",
         "permission": "marketing_campaigns"},
        {"label": "Leads", "target": "marketing.leads", "icon": "users",
         "permission": "marketing_leads"},
        {"label": "Customers", "target": "marketing.customers", "icon": "target",
         "permission": "marketing_leads"},
        {"label": "Market Research", "target": "marketing.research", "icon": "clipboard",
         "permission": "marketing_research"},
        {"label": "Customer Feedback", "target": "marketing.feedback", "icon": "mail",
         "permission": "marketing_feedback"},
        {"label": "Marketing Reports", "target": "marketing.reports", "icon": "chart",
         "permission": "marketing_reports"},
    )),
    # One project module, six desks. The register and the approval list read the
    # same rows with a different filter, so the two links point at one screen
    # rather than a second copy of the register. The team is not a register of its
    # own: it is the user master data, picked per job.
    ("Project Management", "project", (
        {"label": "Project Dashboard", "target": "project.dashboard", "icon": "chart",
         "permission": "project_dashboard"},
        {"label": "Project Register", "target": "project.projects", "icon": "file",
         "permission": "project_register"},
        {"label": "Awaiting Approval", "target": "project.projects", "icon": "check",
         "params": {"status": "Pending"}, "permission": "project_approval"},
        {"label": "My Tasks", "target": "project.tasks", "icon": "clipboard",
         "permission": "project_tasks"},
        {"label": "Milestones", "target": "project.milestones", "icon": "clock",
         "permission": "project_milestones"},
        {"label": "Project Reports", "target": "project.reports", "icon": "chart",
         "permission": "project_reports"},
    )),
    # The staff shortcut for departments that only raise requisitions is gone:
    # Purchase Requisitions (PR) now sits in the Procurement Management group and
    # is offered to the same roles, so a second entry would be the duplicate.
    ("Careers", None, (
        {"label": "Open Vacancies", "target": "recruitment.vacancies", "icon": "briefcase"},
        {"label": "My Applications", "target": "recruitment.my_applications", "icon": "file"},
        {"label": "Recruitment Requests", "target": "recruitment.my_requests",
         "icon": "clipboard"},
    )),
    # Not a module tick: this rail follows what the account is for in recruitment,
    # so the HR desk, an audit seat and a department that raises its own requests
    # all reach it, and nobody gets a link the server would refuse. What each of
    # them sees inside those screens is the same scope rule again.
    ("Hiring Desk", "hiring", (
        {"label": "Vacancies & Requests", "target": "recruitment.hr_vacancies",
         "icon": "briefcase"},
        {"label": "Applicants", "target": "recruitment.hr_applicants", "icon": "users"},
        {"label": "Examinations", "target": "recruitment.hr_exams", "icon": "clipboard"},
        {"label": "Interview Panels", "target": "recruitment.hr_interviews", "icon": "headset"},
        {"label": "Recruitment Reports", "target": "recruitment.hr_reports", "icon": "chart"},
    )),
    ("Approvals", "approvals", (
        {"label": "Approval Queue", "target": "approvals.dashboard", "icon": "check"},
        {"label": "New Request", "target": "approvals.new_request", "icon": "file"},
    )),
    ("Reporting", None, (
        {"label": "Reports", "target": "reports_centre", "icon": "chart"},
    )),
    ("Audit & Oversight", "audit", (
        {"label": "Audit Console", "target": "auditor_dashboard", "icon": "monitor"},
        {"label": "Letter Audit", "target": "auditor_letters", "icon": "mail"},
        {"label": "Audit Reviews", "target": "auditor_reviews", "icon": "clipboard"},
        {"label": "Audit Trail", "target": "audit.audit_dashboard", "icon": "database",
         "permission": "admin_logs"},
        {"label": "Management Overview", "target": "management_overview", "icon": "chart"},
    )),
)


SIDEBAR_GROUP_ICONS = {
    "Admin Management": "shield",
    "System Integration": "network",
    "Procurement Management": "handshake",
    "Letter Management": "mail",
    "HR Management": "users",
    "Finance Management": "wallet",
    "Budget Management": "chart",
    "Payroll Management": "cash",
    "Inventory & Stock": "box",
    "Asset Management": "briefcase",
    "Careers": "briefcase",
    "Hiring Desk": "user",
    "Approvals": "check",
    "Reporting": "chart",
    "Audit & Oversight": "monitor",
    "System Settings": "gear",
}


STAFF_GROUP_ROLES = {
    "Reporting": tuple(role for role in USER_ROLES if role != "member"),
}


# The administrator walks the same rail as every desk, with the settings screen
# added at the end. One structure means a link renamed once stays renamed for
# the administrator too, and no module appears in a second flat list.
SUPER_ADMIN_NAV = (
    (None, None, (
        {"label": "Dashboard", "target": "dashboard", "icon": "chart"},
    )),
) + SIDEBAR_NAV + (
    ("System Settings", "accounts", (
        {"label": "Settings", "target": "admin_settings", "icon": "gear"},
    )),
)


MANAGE_PREFIXES = ("/admin", "/finance", "/budget", "/payroll", "/inventory",
                   "/assets", "/procurement", "/approvals", "/management",
                   "/dashboard", "/search", "/auditor", "/manage-hierarchy",
                   "/reports", "/letters", "/integrations", "/marketing",
                   "/project/", "/recruitment/hr", "/recruitment/finance")


PRINT_ROLE_DEPARTMENTS = {
    "admin": "Admin",
    "hr": "HR",
    "finance": "Finance",
    "budget": "Budget",
    "procurement": "Procurement",
    "payroll": "Payroll",
    "inventory": "Inventory",
    "auditor": "Auditor",
    "employee": "Employee",
    "member": "Member",
}


PRINT_PAGE_TITLES = {
    "/": "Home",
    "/about": "About",
    "/services": "Services",
    "/projects": "Projects",
    "/process": "Process",
    "/contact": "Contact",
    "/organization": "Organization",
    "/login": "Sign In",
    "/forgot-password": "Password Reset",
    "/auth/microsoft": "Sign In",
}


def print_department_name(user, role_label=None):
    if user is not None:
        return PRINT_ROLE_DEPARTMENTS.get(
            user["role"], role_label or user["role"].replace("_", " ").title())
    return PRINT_PAGE_TITLES.get(request.path or "/", "Website")


SIDEBAR_PARENT = {
    "add_employee": "employees",
    "edit_employee": "employees",
    "delete_employee": "employees",
    "employee_document": "employees",
    "update_payroll_status": "payroll",
    # Overtime pages hang off the one Overtime rail entry.
    "hr.overtime_approvals": "hr.overtime_dashboard",
    "hr.overtime_hr_verification": "hr.overtime_dashboard",
    "hr.overtime_reports": "hr.overtime_dashboard",
    "hr.overtime_audit": "hr.overtime_dashboard",
    # Payroll subpages keep their own rail entry current.
    "payroll.employee_salary": "payroll.employee_profiles",
    "payroll.edit_employee_profile": "payroll.employee_profiles",
    "payroll.update_salary_grade": "payroll.salary_grades",
    "payroll.delete_salary_grade": "payroll.salary_grades",
    "payroll.update_salary_structure": "payroll.salary_structures",
    "payroll.toggle_salary_structure": "payroll.salary_structures",
    "payroll.update_allowance": "payroll.allowances",
    "payroll.toggle_allowance": "payroll.allowances",
    "payroll.update_deduction": "payroll.deductions",
    "payroll.toggle_deduction": "payroll.deductions",
    "payroll.generate_payroll": "payroll.payroll_processing",
    "payroll.submit_payroll": "payroll.payroll_processing",
    "payroll.approve_payroll": "payroll.payroll_processing",
    "payroll.reject_payroll": "payroll.payroll_processing",
    "payroll.record_payment": "payroll.payroll_processing",
    "payroll.import_overtime_to_payroll": "payroll.payroll_processing",
    "payroll.export_payroll_report": "payroll.payroll_reports",
    "payroll.toggle_statutory_rule": "payroll.statutory_rules",
    # Every project screen keeps the register entry current in the rail; the
    # milestone and task screens hang off their own entries instead.
    "project.create_project": "project.projects",
    "project.edit_project": "project.projects",
    "project.project_detail": "project.projects",
    "project.submit_project": "project.projects",
    "project.archive_project": "project.projects",
    "project.restore_project": "project.projects",
    "project.set_status": "project.projects",
    "project.add_member": "project.projects",
    "project.remove_member": "project.projects",
    "project.new_milestone": "project.milestones",
    "project.update_milestone": "project.milestones",
    "project.new_task": "project.tasks",
    "project.update_task": "project.tasks",
    # Every asset screen keeps the register entry current in the rail; the
    # category screens hang off the category entry and allocation off its own.
    "asset.create_asset": "asset.assets",
    "asset.edit_asset": "asset.assets",
    "asset.asset_detail": "asset.assets",
    "asset.deactivate_asset": "asset.assets",
    "asset.reactivate_asset": "asset.assets",
    "asset.return_asset": "asset.assets",
    "asset.create_category": "asset.categories",
    "asset.edit_category": "asset.categories",
    "asset.toggle_category": "asset.categories",
    "asset.department_assets": "asset.departments",
    "finance.add_payment": "finance.invoices",
    "inventory.new_item": "inventory.items",
    "inventory.edit_item": "inventory.items",
    "inventory.deactivate_item": "inventory.items",
    "inventory.item_label": "inventory.items",
    "inventory.item_barcode": "inventory.items",
    "inventory.item_qr": "inventory.items",
    # The count sheet keeps the Stocktake entry current through every step of
    # its lifecycle; the report subpages stay under the one Stock Reports link.
    "inventory.new_stocktake": "inventory.stocktakes",
    "inventory.stocktake_detail": "inventory.stocktakes",
    "inventory.stocktake_count": "inventory.stocktakes",
    "inventory.stocktake_submit": "inventory.stocktakes",
    "inventory.stocktake_approve": "inventory.stocktakes",
    "inventory.stocktake_reject": "inventory.stocktakes",
    "inventory.stocktake_post": "inventory.stocktakes",
    "inventory.stock_report": "inventory.reports",
    "inventory.stock_report_export": "inventory.reports",
    "inventory.valuation_report": "inventory.reports",
    "inventory.valuation_report_export": "inventory.reports",
    "inventory.movement_report": "inventory.reports",
    "inventory.movement_report_export": "inventory.reports",
    "admin_role_account": "admin_roles",
    "admin_departments_action": "admin_departments",
    # The desk's rail entry is the registry; every other screen of the module
    # stays on that entry while the in-page tabs show where the account is.
    "integrations.add": "integrations.dashboard",
    "integrations.edit": "integrations.dashboard",
    "integrations.connection_history": "integrations.dashboard",
    "integrations.ad_mapping": "integrations.dashboard",
    "integrations.monitoring": "integrations.dashboard",
    "integrations.logs": "integrations.dashboard",
    "integrations.directory_connections": "integrations.dashboard",
    "integrations.directory_connection": "integrations.dashboard",
    "integrations.directory_groups": "integrations.dashboard",
    "integrations.directory_sync": "integrations.dashboard",
    "integrations.directory_accounts": "integrations.dashboard",
    "auditor_findings": "auditor_reviews",
    "auditor_records": "auditor_dashboard",
    "auditor_review_status": "auditor_reviews",
    "auditor_finding_status": "auditor_reviews",
    "auditor_reports": "auditor_dashboard",
    "auditor_finance_reports": "auditor_reports",
    "search": "dashboard",
    "procurement.add_supplier": "procurement.dashboard",
    "procurement.add_request": "procurement.dashboard",
    "procurement.update_request_status": "procurement.dashboard",
    "procurement.add_order": "procurement.dashboard",
    "procurement.update_order_status": "procurement.dashboard",
    "procurement.requisition_new": "procurement.requisitions",
    "procurement.requisition_detail": "procurement.requisitions",
    "procurement.requisition_edit": "procurement.requisitions",
    "procurement.requisition_submit": "procurement.requisitions",
    "procurement.requisition_discard": "procurement.requisitions",
    "procurement.order_detail": "procurement.dashboard",
    "approvals.decide": "approvals.dashboard",
    "approvals.resubmit": "approvals.dashboard",
    "approvals.detail": "approvals.dashboard",
    "recruitment.create_vacancy": "recruitment.hr_vacancies",
    "recruitment.edit_vacancy": "recruitment.hr_vacancies",
    "recruitment.vacancy_detail": "recruitment.hr_vacancies",
    "recruitment.set_vacancy_status": "recruitment.hr_vacancies",
    "recruitment.submit_vacancy_request": "recruitment.hr_vacancies",
    "recruitment.register_applicant": "recruitment.hr_vacancies",
    "recruitment.applicant_detail": "recruitment.hr_applicants",
    "recruitment.set_application_status": "recruitment.hr_applicants",
    "recruitment.download_cv": "recruitment.hr_applicants",
    "recruitment.exam_link": "recruitment.hr_applicants",
    "recruitment.create_interview": "recruitment.hr_applicants",
    "recruitment.create_exam": "recruitment.hr_exams",
    "recruitment.exam_questions": "recruitment.hr_exams",
    "recruitment.add_exam_question": "recruitment.hr_exams",
    "recruitment.import_exam_questions": "recruitment.hr_exams",
    "recruitment.exam_question_template": "recruitment.hr_exams",
    "recruitment.delete_exam_question": "recruitment.hr_exams",
    "recruitment.publish_exam": "recruitment.hr_exams",
    "recruitment.close_exam": "recruitment.hr_exams",
    "recruitment.hr_exam_results": "recruitment.hr_exams",
    "recruitment.interview_detail": "recruitment.hr_interviews",
    "recruitment.score_interview": "recruitment.hr_interviews",
    "recruitment.set_panel": "recruitment.hr_interviews",
    "recruitment.decide_interview": "recruitment.hr_interviews",
    "letters.detail": "letters.dashboard",
    "letters.edit": "letters.dashboard",
    "admin_letter_permissions": "admin_users",
    "admin_hiring_permissions": "admin_users",
    "admin_hiring_role_permissions": "admin_users",
}


EMPLOYEE_STATUS = ("Active", "On Leave", "Inactive")


EMPLOYMENT_TYPES = ("Permanent", "Contract", "Temporary", "Intern")


EDUCATION_LEVELS = ("Certificate", "Diploma", "Bachelor's Degree",
                    "Master's Degree", "PhD")


EMPLOYEE_DOCUMENT_TYPES = (
    "National ID", "Education certificate", "Employment contract",
    "CV / Resume", "Reference letter", "Pension registration",
)


EMPLOYEE_UPLOAD_DIR = Path(os.environ.get(
    "WK_UPLOAD_DIR", str(BASE_DIR / "uploads"))) / "employees"


EMPLOYEE_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


EMPLOYEE_FILE_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"}


EMPLOYEE_PHOTO_LIMIT = 3 * 1024 * 1024


EMPLOYEE_FILE_LIMIT = 5 * 1024 * 1024


EMPLOYEE_FILE_COUNT = 5


# --------------------------------------------------------------------------
# Recruitment and vacancy management. The vocabulary lives here because the
# screens, the schema CHECK constraints and the reports all have to agree on the
# same words, exactly like EMPLOYEE_STATUS above. Departments and applicants are
# never listed again: a vacancy points at a row in the departments table and an
# application at a row in users, so the organisation and the accounts stay in one
# place.
# --------------------------------------------------------------------------
VACANCY_STATUSES = ("Draft", "Open", "Closed")


VACANCY_JOB_TYPES = ("Full-time", "Part-time", "Contract", "Temporary",
                     "Internship")


APPLICATION_STATUSES = ("Applied", "Shortlisted", "Interview", "Selected",
                        "Rejected", "Hired")


APPLICATION_SOURCES = ("Portal", "Walk-in", "Referral", "Internal")


EXAM_STATUSES = ("Draft", "Published", "Closed")


ATTEMPT_STATUSES = ("Not Started", "In Progress", "Submitted", "Missed")


INTERVIEW_OUTCOMES = ("Pending", "Passed", "Failed", "Withdrew")


RECRUITMENT_UPLOAD_DIR = Path(os.environ.get(
    "WK_UPLOAD_DIR", str(BASE_DIR / "uploads"))) / "recruitment"


RECRUITMENT_CV_EXTENSIONS = {"pdf", "doc", "docx"}


RECRUITMENT_CV_LIMIT = 5 * 1024 * 1024


# A vacancy request is raised into the shared approval chain, which knows only the
# types in services.workflow.REQUEST_TYPES. A new post is a headcount project, so
# it travels that chain instead of adding a type every other screen would have to
# learn about.
RECRUITMENT_REQUEST_TYPE = "Project"


RECRUITMENT_REQUEST_DEPARTMENT = "general"


# Manager and above in the position ladder (organizational_positions.level), which
# is what makes a department head able to see their own hiring.
RECRUITMENT_REVIEW_LEVEL = 3


EMPLOYEE_CODE_PREFIX = "DRB"


EMPLOYEE_DEPARTMENTS = (
    "Administration", "Human Resources", "Finance", "Budget", "Payroll",
    "Inventory", "Procurement", "Production", "Woodworking", "Kitchen Cabinet",
    "Finishing", "Sales", "IT", "Other",
)


EMPLOYEE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


EMPLOYEE_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-().]{5,19}$")


def ms_login_enabled():
    """True only when a complete, tenant-scoped app registration is configured."""
    return bool(msal is not None and MS_CLIENT_ID and MS_CLIENT_SECRET
                and MS_AUTHORITY)


def directory_integration_enabled():
    """The kill-switch for the whole directory module, read once per request.

    Off, the screens stop being offered and no connection is contacted, which is
    the behaviour wanted while a domain controller is being rebuilt or an app
    registration is being rotated.
    """
    return bool(DIRECTORY_ENABLED)


DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


STEP_LABEL = {
    "hr": "HR",
    "finance": "Finance",
    "payroll": "Payroll",
    "inventory": "Inventory",
    "procurement": "Procurement",
    "manager": "Manager",
    "director": "Director",
    "ceo": "CEO",
    "management": "Management",
}


POSITION_STEP_LEVEL = {
    "manager": 3,
    "director": 2,
    "ceo": 0,
    "management": 0,
}


PURCHASE_UNITS = ("pcs", "kg", "m", "m2", "m3", "l", "sheet", "bag", "roll", "set")


ORDER_STATUSES = ("Ordered", "Partially Received", "Received", "Cancelled")


MONTH_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# The session lifetime is one setting the app bootstrap reads.
try:
    SESSION_HOURS = max(1, min(int(os.environ.get("WK_SESSION_HOURS", "12")), 72))
except ValueError:
    SESSION_HOURS = 12
