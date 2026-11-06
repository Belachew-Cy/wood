
"""
Finance Department Integration Registry

Central entry point for communication between Finance
and HR, Payroll, Marketing, Projects, Asset Management,
and Inventory & Warehouse.

This module does not create separate databases,
authentication systems, or department records.
"""

from importlib import import_module


# ---------------------------------------------------------
# Integration Registry
# ---------------------------------------------------------

INTEGRATION_MODULES = {
    "hr": ".hr_integration",
    "payroll": ".payroll_integration",
    "marketing": ".marketing_integration",
    "project": ".project_integration",
    "asset": ".asset_integration",
    "inventory": ".inventory_integration",
}


# ---------------------------------------------------------
# Integration Availability
# ---------------------------------------------------------

def get_integration_modules():
    """
    Return the names of all supported integrations.
    """
    return list(INTEGRATION_MODULES.keys())


# ---------------------------------------------------------
# Load One Integration
# ---------------------------------------------------------

def load_integration(department):
    """
    Load a department integration when required.

    Example:
        integration = load_integration("payroll")

    Returns:
        Imported integration module.

    Raises:
        ValueError: If the department is not supported.
        ImportError: If the integration file cannot load.
    """
    department = str(department).strip().lower()

    module_path = INTEGRATION_MODULES.get(department)

    if not module_path:
        raise ValueError(
            f"Unsupported Finance integration: {department}"
        )

    return import_module(module_path, package=__name__)


# ---------------------------------------------------------
# Load All Integrations
# ---------------------------------------------------------

def load_all_integrations():
    """
    Load all integration modules.

    Use this only after all six integration files
    have been created.
    """
    loaded = {}

    for department in INTEGRATION_MODULES:
        loaded[department] = load_integration(department)

    return loaded


# ---------------------------------------------------------
# Integration Status
# ---------------------------------------------------------

def get_integration_status():
    """
    Return the configured integration names.

    This reports registered module names, not whether
    each department's database integration is operational.
    """
    return {
        department: {
            "configured": True,
            "module": module_path
        }
        for department, module_path in INTEGRATION_MODULES.items()
    }
