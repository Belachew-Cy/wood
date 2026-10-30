"""Deployment settings, roles, permissions and fixed vocabulary."""

from flask import request
from pathlib import Path
import os
import re

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


DB_PATH = Path(os.environ.get("WK_DB_PATH", str(BASE_DIR / "users.db"))).resolve()


PROJECTS_PATH = BASE_DIR / "project.json"


KEY_PATH = Path(os.environ.get(
    "WK_SECRET_KEY_FILE", str(BASE_DIR / ".secret_key"))).resolve()


BACKUP_DIR = Path(os.environ.get(
    "WK_BACKUP_DIR", str(BASE_DIR / "backups"))).resolve()


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
    "payroll": "payroll_dashboard",
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


MODULES = ("hr", "finance", "budget", "payroll", "inventory", "accounts",
           "procurement", "approvals", "audit", "letters")


MODULE_LABEL = {
    "hr": "HR module",
    "finance": "Finance module",
    "budget": "Budget module",
    "payroll": "Payroll module",
    "inventory": "Inventory & stock module",
    "accounts": "User & role management",
    "procurement": "Procurement module",
    "approvals": "Approval workflow",
    "audit": "Audit trail (read only)",
    "letters": "Letters & correspondence",
}


ROLE_PERMISSIONS = {
    "admin": MODULES,
    # No role is handed Letters on its own: the administrator grants the module
    # and its actions, which is why it is absent from the defaults below.
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


SIDEBAR_NAV = (
    ("Administration", "accounts", (
        ("Admin Dashboard", "admin"),
        ("Announcements", "admin_notifications"),
    )),
    ("HR Management", "hr", (
        ("HR Dashboard", "hr_dashboard"),
        ("Employees", "employees"),
        ("Attendance", "attendance"),
        ("Recruitment", "recruitment"),
        ("Leave Requests", "leave_management"),
        ("Training & Performance", "training_performance"),
        ("Organization", "organization"),
        ("Hierarchy Setup", "manage_hierarchy"),
        ("HR Settings", "hr_settings.index"),
    )),
    ("Finance Management", "finance", (
        ("Finance Dashboard", "finance.dashboard"),
        ("Income & Expenses", "finance.transactions"),
        ("Invoices & Payments", "finance.invoices"),
    )),
    ("Budget Management", "budget", (
        ("Dashboard", "dashboard"),
        ("Budget Plans", "budget.plan_list"),
        ("Allocations", "budget.allocations"),
        ("Budget Requests", "budget.request_list"),
        ("Expenditures", "budget.expenditures"),
    )),
    ("Payroll Management", "payroll", (
        ("Payroll Dashboard", "payroll_dashboard"),
        ("Run Payroll", "payroll"),
    )),
    ("Inventory & Stock", "inventory", (
        ("Inventory Dashboard", "inventory.dashboard"),
        ("Inventory Items", "inventory.items"),
        ("Stock In / Out", "inventory.stock_movement"),
        ("Movement History", "inventory.history"),
    )),
    ("Purchasing", None, (
        ("My Requisitions", "procurement.requisitions"),
    )),
    ("Procurement", "procurement", (
        ("Procurement Dashboard", "procurement.dashboard"),
        ("Requisitions", "procurement.requisitions"),
    )),
    ("Approvals", "approvals", (
        ("Approval Queue", "approvals.dashboard"),
        ("New Request", "approvals.new_request"),
    )),
    ("Reporting", None, (
        ("Reports", "reports_centre"),
    )),
    ("Correspondence", "letters", (
        ("Inbox", "letters.inbox"),
        ("Outbox", "letters.outbox"),
        ("Letters", "letters.dashboard"),
    )),
    ("Audit & Oversight", "audit", (
        ("Audit Console", "auditor_dashboard"),
        ("Letter Audit", "auditor_letters"),
        ("Audit Reviews", "auditor_reviews"),
        ("Audit Trail", "audit.audit_dashboard"),
        ("Management Overview", "management_overview"),
    )),
)


STAFF_GROUP_ROLES = {
    "Purchasing": PR_REQUESTER_ROLES,
    "Reporting": tuple(role for role in USER_ROLES if role != "member"),
}


SUPER_ADMIN_NAV = (
    (None, None, (
        ("Dashboard", "dashboard"),
        ("User Management", "admin_users"),
        ("Role Management", "admin_roles"),
        ("Announcements", "admin_notifications"),
        ("Organization", "organization"),
        ("Hierarchy Setup", "manage_hierarchy"),
        ("Approval Workflows", "approvals.dashboard"),
        ("Purchase Requisitions", "procurement.requisitions"),
        ("Activity Logs", "audit.audit_dashboard"),
        ("Audit Console", "auditor_dashboard"),
        ("Letter Audit", "auditor_letters"),
        ("Reports", "reports_centre"),
        ("Letter Inbox", "letters.inbox"),
        ("Letter Outbox", "letters.outbox"),
        ("Letters", "letters.dashboard"),
        ("Settings", "admin_settings"),
    )),
)


MANAGE_PREFIXES = ("/admin", "/finance", "/budget", "/payroll", "/inventory",
                   "/procurement", "/approvals", "/management",
                   "/dashboard", "/search", "/auditor", "/manage-hierarchy",
                   "/reports", "/letters")


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
    "update_application_status": "recruitment",
    "update_vacancy_status": "recruitment",
    "update_payroll_status": "payroll",
    "finance.add_payment": "finance.invoices",
    "inventory.new_item": "inventory.items",
    "inventory.edit_item": "inventory.items",
    "inventory.deactivate_item": "inventory.items",
    "admin_role_account": "admin_roles",
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
    "procurement.requisition_edit": "procurement.requisitions",
    "procurement.requisition_submit": "procurement.requisitions",
    "procurement.requisition_discard": "procurement.requisitions",
    "approvals.decide": "approvals.dashboard",
    "approvals.resubmit": "approvals.dashboard",
    "approvals.detail": "approvals.dashboard",
    "letters.create": "letters.dashboard",
    "letters.detail": "letters.dashboard",
    "letters.edit": "letters.dashboard",
    "admin_letter_permissions": "admin_users",
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
