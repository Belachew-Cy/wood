
"""
Finance Management Module
Dereje Wood & Kitchen Cabinet Management System

Initializes the Finance Blueprint and module configuration.
"""

from flask import Blueprint

# ---------------------------------------------------------
# Finance Blueprint
# ---------------------------------------------------------

finance_bp = Blueprint(
    "finance",
    __name__,
    template_folder="templates",
    url_prefix="/finance"
)


# ---------------------------------------------------------
# Module Configuration
# ---------------------------------------------------------

FINANCE_MODULE_NAME = "Finance Management"

FINANCE_CURRENCY = "ETB"

SUPPORTED_CURRENCIES = (
    "ETB",
    "USD",
    "EUR",
    "GBP"
)


# ---------------------------------------------------------
# Register Finance Routes
# ---------------------------------------------------------

def register_finance_routes():
    """
    Import routes after creating the blueprint.

    This avoids circular imports between routes.py
    and the Finance package.
    """
    from . import routes  # noqa: F401


# ---------------------------------------------------------
# Register Finance Integrations
# ---------------------------------------------------------

def register_finance_integrations():
    """
    Load department integration modules.

    Integration modules should expose services only.
    They must not register duplicate database tables,
    blueprints, or independent authentication systems.
    """
    from .integrations import hr_integration  # noqa: F401
    from .integrations import payroll_integration  # noqa: F401
    from .integrations import marketing_integration  # noqa: F401
    from .integrations import project_integration  # noqa: F401
    from .integrations import asset_integration  # noqa: F401
    from .integrations import inventory_integration  # noqa: F401


# ---------------------------------------------------------
# Initialize Finance Module
# ---------------------------------------------------------

def init_finance():
    """
    Initialize Finance routes and integration modules.

    Call this during the existing Flask application setup.
    """
    register_finance_routes()
    register_finance_integrations()

    return finance_bp
