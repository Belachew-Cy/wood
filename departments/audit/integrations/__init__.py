
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Integrations Package

Central entry point for read-only integrations with existing
department databases.

All departmental integrations use the shared read-only adapter.
No departmental transactions are modified here.
"""

# -------------------------------------------------------
# FINANCE
# -------------------------------------------------------

from departments.audit.integrations.finance_integration import (
    get_finance_audit_data,
    get_finance_audit_overview,
    get_finance_connection_status,
)


# -------------------------------------------------------
# HUMAN RESOURCES
# -------------------------------------------------------

from departments.audit.integrations.hr_integration import (
    get_hr_audit_data,
    get_hr_audit_overview,
    get_hr_connection_status,
)


# -------------------------------------------------------
# PAYROLL
# -------------------------------------------------------

from departments.audit.integrations.payroll_integration import (
    get_payroll_audit_data,
    get_payroll_audit_overview,
    get_payroll_connection_status,
)


# -------------------------------------------------------
# PROCUREMENT
# -------------------------------------------------------

from departments.audit.integrations.procurement_integration import (
    get_procurement_audit_data,
    get_procurement_audit_overview,
    get_procurement_connection_status,
)


# -------------------------------------------------------
# INVENTORY AND WAREHOUSE
# -------------------------------------------------------

from departments.audit.integrations.inventory_integration import (
    get_inventory_audit_data,
    get_inventory_audit_overview,
    get_inventory_connection_status,
)


# -------------------------------------------------------
# ASSET MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.asset_integration import (
    get_asset_audit_data,
    get_asset_audit_overview,
    get_asset_connection_status,
)


# -------------------------------------------------------
# BUDGET MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.budget_integration import (
    get_budget_audit_data,
    get_budget_audit_overview,
    get_budget_connection_status,
)


# -------------------------------------------------------
# PROJECT MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.project_integration import (
    get_project_audit_data,
    get_project_audit_overview,
    get_project_connection_status,
)


# -------------------------------------------------------
# MARKETING MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.marketing_integration import (
    get_marketing_audit_data,
    get_marketing_audit_overview,
    get_marketing_connection_status,
)


# -------------------------------------------------------
# PERFORMANCE MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.performance_integration import (
    get_performance_audit_data,
    get_performance_audit_overview,
    get_performance_connection_status,
)


# -------------------------------------------------------
# RECRUITMENT / VACANCY
# -------------------------------------------------------

from departments.audit.integrations.vacancy_integration import (
    get_vacancy_audit_data,
    get_vacancy_audit_overview,
    get_vacancy_connection_status,
)


# -------------------------------------------------------
# RECRUITMENT EXAMINATION
# -------------------------------------------------------

from departments.audit.integrations.exam_integration import (
    get_exam_audit_data,
    get_exam_audit_overview,
    get_exam_connection_status,
)


# -------------------------------------------------------
# LETTER MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.letter_integration import (
    get_letter_audit_data,
    get_letter_audit_overview,
    get_letter_connection_status,
)


# -------------------------------------------------------
# USER MANAGEMENT
# -------------------------------------------------------

from departments.audit.integrations.user_integration import (
    get_user_audit_data,
    get_user_audit_overview,
    get_user_connection_status,
)


# -------------------------------------------------------
# SYSTEM AND DIRECTORY INTEGRATION
# -------------------------------------------------------

from departments.audit.integrations.system_integration import (
    get_system_integration_audit_data,
    get_integration_audit_overview,
    get_integration_connection_status,
)


# -------------------------------------------------------
# SHARED AUDIT RECORDS
# -------------------------------------------------------

from departments.audit.integrations.shared_audit_integration import (
    get_shared_audit_data,
    get_shared_audit_overview,
    get_shared_audit_connection_status,
)


# -------------------------------------------------------
# PUBLIC PACKAGE API
# -------------------------------------------------------

__all__ = [
    # Finance
    "get_finance_audit_data",
    "get_finance_audit_overview",
    "get_finance_connection_status",

    # HR
    "get_hr_audit_data",
    "get_hr_audit_overview",
    "get_hr_connection_status",

    # Payroll
    "get_payroll_audit_data",
    "get_payroll_audit_overview",
    "get_payroll_connection_status",

    # Procurement
    "get_procurement_audit_data",
    "get_procurement_audit_overview",
    "get_procurement_connection_status",

    # Inventory
    "get_inventory_audit_data",
    "get_inventory_audit_overview",
    "get_inventory_connection_status",

    # Asset
    "get_asset_audit_data",
    "get_asset_audit_overview",
    "get_asset_connection_status",

    # Budget
    "get_budget_audit_data",
    "get_budget_audit_overview",
    "get_budget_connection_status",

    # Project
    "get_project_audit_data",
    "get_project_audit_overview",
    "get_project_connection_status",

    # Marketing
    "get_marketing_audit_data",
    "get_marketing_audit_overview",
    "get_marketing_connection_status",

    # Performance
    "get_performance_audit_data",
    "get_performance_audit_overview",
    "get_performance_connection_status",

    # Recruitment
    "get_vacancy_audit_data",
    "get_vacancy_audit_overview",
    "get_vacancy_connection_status",

    # Examination
    "get_exam_audit_data",
    "get_exam_audit_overview",
    "get_exam_connection_status",

    # Letter
    "get_letter_audit_data",
    "get_letter_audit_overview",
    "get_letter_connection_status",

    # User
    "get_user_audit_data",
    "get_user_audit_overview",
    "get_user_connection_status",

    # System Integration
    "get_system_integration_audit_data",
    "get_integration_audit_overview",
    "get_integration_connection_status",

    # Shared Audit
    "get_shared_audit_data",
    "get_shared_audit_overview",
    "get_shared_audit_connection_status",
]
