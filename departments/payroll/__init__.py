"""Payroll department: the desk blueprint and its schema.

The screens live under ``/payroll`` and render from ``templates/payroll``. Every
table they write is owned by the payroll module file, so the blueprint only has
to mount, and the schema only has to be topped up once at startup.
"""

from flask import Blueprint

payroll_bp = Blueprint(
    "payroll",
    __name__,
    template_folder="../../templates/payroll",
    url_prefix="/payroll",
)


def init_payroll_schema(app):
    """Create or top up the payroll tables inside the app's database files."""

    from . import models

    with app.app_context():
        models.init_payroll_tables()
        models.init_monthly_payroll_tables()
        models.init_payroll_payment_tables()
        models.init_payroll_statutory_tables()
        models.init_progressive_tax_tables()
        models.seed_default_statutory_rules()


def init_payroll(app):
    """Mount the payroll desk and its template helpers."""

    from . import routes  # noqa: F401 - importing registers the routes
    from .permissions import register_payroll_template_helpers

    if "payroll" not in app.blueprints:
        app.register_blueprint(payroll_bp)

    register_payroll_template_helpers(app)

    app.logger.info("Payroll Department initialized successfully.")
