
"""
Dereje Wood & Kitchen Cabinet Management System
Payroll Department

Module initialization and Blueprint registration.
"""

from flask import Blueprint

payroll_bp = Blueprint(
    "payroll",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/payroll"
)


def init_payroll(app):
    """
    Register Payroll module with the main Flask application.
    """

    if "payroll" not in app.blueprints:
        app.register_blueprint(payroll_bp)

    app.logger.info(
        "Payroll Department initialized successfully."
    )
