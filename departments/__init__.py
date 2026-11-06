"""Department packages.

Importing a department module executes its route decorators against the shared
router in ``core.routing``; the modules that own a Flask blueprint are mounted
afterwards by ``register_blueprints`` in the same order the monolith did.
"""

from departments import admin
from departments import asset_management
from departments import department_management
from departments import approvals
from departments import audit
from departments import budget
from departments import finance
from departments import inventory
from departments import letters
from departments import marketing
from departments import organization
from departments import procurement
from departments import project
from departments import reports
from departments.HR import reports as hr_reports, hr
from departments.payroll import payroll as hr_attendance

BLUEPRINT_MODULES = (
    hr,
    finance,
    budget,
    inventory,
    asset_management,
    audit,
    procurement,
    approvals,
    letters,
    marketing,
    project,
)


def register_blueprints(app):
    for module in BLUEPRINT_MODULES:
        module.register(app)
