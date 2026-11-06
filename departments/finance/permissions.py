
"""
Finance Management Permissions
Dereje Wood & Kitchen Cabinet Management System

Defines Finance permissions for integration with the
existing centralized Role & Permission Management system.
"""

# ---------------------------------------------------------
# Finance Permission Definitions
# ---------------------------------------------------------

FINANCE_PERMISSIONS = {

    # Dashboard
    "finance_dashboard_view": {
        "name": "View Finance Dashboard",
        "module": "Finance",
        "description": "Access the Finance dashboard."
    },

    # Chart of Accounts
    "finance_accounts_view": {
        "name": "View Chart of Accounts",
        "module": "Finance",
        "description": "View financial accounts."
    },
    "finance_accounts_create": {
        "name": "Create Financial Accounts",
        "module": "Finance",
        "description": "Create new chart of accounts entries."
    },
    "finance_accounts_edit": {
        "name": "Edit Financial Accounts",
        "module": "Finance",
        "description": "Edit existing financial accounts."
    },
    "finance_accounts_deactivate": {
        "name": "Deactivate Financial Accounts",
        "module": "Finance",
        "description": "Deactivate financial accounts."
    },

    # General Ledger
    "finance_ledger_view": {
        "name": "View General Ledger",
        "module": "Finance",
        "description": "View posted general ledger transactions."
    },
    "finance_ledger_export": {
        "name": "Export General Ledger",
        "module": "Finance",
        "description": "Export general ledger reports."
    },

    # Journal Entries
    "finance_journal_view": {
        "name": "View Journal Entries",
        "module": "Finance",
        "description": "View financial journal entries."
    },
    "finance_journal_create": {
        "name": "Create Journal Entries",
        "module": "Finance",
        "description": "Create draft journal entries."
    },
    "finance_journal_edit": {
        "name": "Edit Journal Entries",
        "module": "Finance",
        "description": "Edit eligible draft journal entries."
    },
    "finance_journal_approve": {
        "name": "Approve Journal Entries",
        "module": "Finance",
        "description": "Approve journal entries."
    },
    "finance_journal_post": {
        "name": "Post Journal Entries",
        "module": "Finance",
        "description": "Post approved journals to the ledger."
    },
    "finance_journal_reverse": {
        "name": "Reverse Journal Entries",
        "module": "Finance",
        "description": "Create controlled reversal entries."
    },

    # Income and Expenses
    "finance_transactions_view": {
        "name": "View Transactions",
        "module": "Finance",
        "description": "View income and expense transactions."
    },
    "finance_transactions_create": {
        "name": "Create Transactions",
        "module": "Finance",
        "description": "Record income and expenses."
    },
    "finance_transactions_edit": {
        "name": "Edit Transactions",
        "module": "Finance",
        "description": "Edit eligible unposted transactions."
    },

    # Accounts Receivable
    "finance_receivables_view": {
        "name": "View Accounts Receivable",
        "module": "Finance",
        "description": "View customer balances and invoices."
    },
    "finance_receivables_manage": {
        "name": "Manage Accounts Receivable",
        "module": "Finance",
        "description": "Manage customer invoices and collections."
    },

    # Accounts Payable
    "finance_payables_view": {
        "name": "View Accounts Payable",
        "module": "Finance",
        "description": "View supplier balances and invoices."
    },
    "finance_payables_manage": {
        "name": "Manage Accounts Payable",
        "module": "Finance",
        "description": "Manage supplier invoices and payments."
    },

    # Cash and Bank
    "finance_cash_bank_view": {
        "name": "View Cash and Bank",
        "module": "Finance",
        "description": "View cash and bank accounts."
    },
    "finance_cash_bank_manage": {
        "name": "Manage Cash and Bank",
        "module": "Finance",
        "description": "Manage authorized cash and bank transactions."
    },
    "finance_bank_reconcile": {
        "name": "Reconcile Bank Accounts",
        "module": "Finance",
        "description": "Perform bank reconciliation."
    },

    # Budget
    "finance_budget_view": {
        "name": "View Financial Budgets",
        "module": "Finance",
        "description": "View department and project budgets."
    },
    "finance_budget_manage": {
        "name": "Manage Financial Budgets",
        "module": "Finance",
        "description": "Create and update financial budgets."
    },
    "finance_budget_approve": {
        "name": "Approve Financial Budgets",
        "module": "Finance",
        "description": "Approve submitted financial budgets."
    },

    # Department Integrations
    "finance_integration_view": {
        "name": "View Department Integrations",
        "module": "Finance",
        "description": "View connected department financial records."
    },
    "finance_integration_import": {
        "name": "Import Department Transactions",
        "module": "Finance",
        "description": "Import eligible department transactions."
    },
    "finance_integration_approve": {
        "name": "Approve Integrated Transactions",
        "module": "Finance",
        "description": "Approve eligible imported transactions."
    },

    # Financial Reports
    "finance_reports_view": {
        "name": "View Financial Reports",
        "module": "Finance",
        "description": "View financial statements and reports."
    },
    "finance_reports_export": {
        "name": "Export Financial Reports",
        "module": "Finance",
        "description": "Export authorized financial reports."
    },

    # Audit
    "finance_audit_view": {
        "name": "View Finance Audit Logs",
        "module": "Finance",
        "description": "View financial audit history."
    }
}


# ---------------------------------------------------------
# Permission Utilities
# ---------------------------------------------------------

def get_finance_permission_codes():
    """Return all Finance permission codes."""
    return list(FINANCE_PERMISSIONS.keys())


def get_finance_permissions():
    """Return Finance permission definitions."""
    return FINANCE_PERMISSIONS.copy()


def get_finance_permissions_by_category():
    """Group Finance permissions by functional category."""

    categories = {}

    for code, details in FINANCE_PERMISSIONS.items():
        category = code.split("_")[1]

        categories.setdefault(category, []).append({
            "code": code,
            **details
        })

    return categories


# ---------------------------------------------------------
# Shared RBAC Integration
# ---------------------------------------------------------

def register_finance_permissions(register_permission):
    """
    Register Finance permissions through the existing
    shared RBAC registration function.

    register_permission must be supplied by your existing
    Role & Permission Management service.

    Example:
        register_finance_permissions(existing_register_function)

    Do not call this with a new or separate permission table.
    """

    if not callable(register_permission):
        raise TypeError(
            "An existing shared RBAC registration function is required."
        )

    registered = []

    for code, details in FINANCE_PERMISSIONS.items():

        register_permission(
            code=code,
            name=details["name"],
            module=details["module"],
            description=details["description"]
        )

        registered.append(code)

    return registered


# ---------------------------------------------------------
# Recommended Role Permission Groups
# ---------------------------------------------------------

FINANCE_ROLE_DEFAULTS = {

    "Administrator": get_finance_permission_codes(),

    "Finance Manager": [
        code for code in FINANCE_PERMISSIONS
        if code != "finance_audit_view"
    ],

    "Finance Officer": [
        "finance_dashboard_view",
        "finance_accounts_view",
        "finance_ledger_view",
        "finance_journal_view",
        "finance_journal_create",
        "finance_transactions_view",
        "finance_transactions_create",
        "finance_receivables_view",
        "finance_receivables_manage",
        "finance_payables_view",
        "finance_payables_manage",
        "finance_cash_bank_view",
        "finance_reports_view",
        "finance_integration_view"
    ],

    "Auditor": [
        "finance_dashboard_view",
        "finance_accounts_view",
        "finance_ledger_view",
        "finance_journal_view",
        "finance_transactions_view",
        "finance_receivables_view",
        "finance_payables_view",
        "finance_cash_bank_view",
        "finance_budget_view",
        "finance_integration_view",
        "finance_reports_view",
        "finance_audit_view"
    ]
}
