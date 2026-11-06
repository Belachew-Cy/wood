
"""
Finance department integration tests.

Run:
    pytest departments/finance/tests/test_integrations.py -v
"""

import pytest

from departments.finance.integrations import (
    hr_integration,
    payroll_integration,
    marketing_integration,
    project_integration,
    asset_integration,
    inventory_integration,
)


# ---------------------------------------------------------
# TEST DATA
# ---------------------------------------------------------

@pytest.fixture
def approved_transaction():
    return {
        "source_reference": "TEST-REF-001",
        "description": "Approved test transaction",
        "amount": "1000.00",
        "currency": "ETB",
        "exchange_rate": "1",
        "approval_status": "Approved",
    }


# ---------------------------------------------------------
# SHARED VALIDATION
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "integration_module,transaction_type",
    [
        (hr_integration, "HR_EXPENSE"),
        (payroll_integration, "SALARY_EXPENSE"),
        (marketing_integration, "CAMPAIGN_EXPENSE"),
        (project_integration, "PROJECT_EXPENSE"),
        (asset_integration, "ASSET_MAINTENANCE"),
        (inventory_integration, "MATERIAL_CONSUMPTION"),
    ],
)
def test_integration_rejects_unapproved_transaction(
    integration_module,
    transaction_type,
    approved_transaction
):
    transaction = approved_transaction.copy()
    transaction["source_type"] = transaction_type
    transaction["approval_status"] = "Pending"

    with pytest.raises((ValueError, PermissionError)):
        integration_module.validate_transaction(transaction)


@pytest.mark.parametrize(
    "integration_module,transaction_type",
    [
        (hr_integration, "HR_EXPENSE"),
        (payroll_integration, "SALARY_EXPENSE"),
        (marketing_integration, "CAMPAIGN_EXPENSE"),
        (project_integration, "PROJECT_EXPENSE"),
        (asset_integration, "ASSET_MAINTENANCE"),
        (inventory_integration, "MATERIAL_CONSUMPTION"),
    ],
)
def test_integration_rejects_negative_amount(
    integration_module,
    transaction_type,
    approved_transaction
):
    transaction = approved_transaction.copy()
    transaction["source_type"] = transaction_type
    transaction["amount"] = "-100.00"

    with pytest.raises(ValueError):
        integration_module.validate_transaction(transaction)


@pytest.mark.parametrize(
    "integration_module,transaction_type",
    [
        (hr_integration, "HR_EXPENSE"),
        (payroll_integration, "SALARY_EXPENSE"),
        (marketing_integration, "CAMPAIGN_EXPENSE"),
        (project_integration, "PROJECT_EXPENSE"),
        (asset_integration, "ASSET_MAINTENANCE"),
        (inventory_integration, "MATERIAL_CONSUMPTION"),
    ],
)
def test_integration_rejects_unsupported_currency(
    integration_module,
    transaction_type,
    approved_transaction
):
    transaction = approved_transaction.copy()
    transaction["source_type"] = transaction_type
    transaction["currency"] = "XYZ"

    with pytest.raises(ValueError):
        integration_module.validate_transaction(transaction)


# ---------------------------------------------------------
# DEPARTMENT-SPECIFIC REQUIRED REFERENCES
# ---------------------------------------------------------

def test_asset_transaction_requires_asset_reference(
    approved_transaction
):
    transaction = approved_transaction.copy()
    transaction["source_type"] = "ASSET_MAINTENANCE"

    with pytest.raises(ValueError):
        asset_integration.validate_transaction(transaction)


def test_inventory_transaction_requires_item_reference(
    approved_transaction
):
    transaction = approved_transaction.copy()
    transaction["source_type"] = "MATERIAL_CONSUMPTION"

    with pytest.raises(ValueError):
        inventory_integration.validate_transaction(transaction)


def test_project_transaction_requires_project_reference(
    approved_transaction
):
    transaction = approved_transaction.copy()
    transaction["source_type"] = "PROJECT_EXPENSE"

    with pytest.raises(ValueError):
        project_integration.validate_transaction(transaction)


# ---------------------------------------------------------
# INTEGRATION MODULE REGISTRY
# ---------------------------------------------------------

def test_all_finance_integrations_are_registered():
    from departments.finance.integrations import (
        get_integration_modules
    )

    registered = get_integration_modules()

    expected = {
        "hr",
        "payroll",
        "marketing",
        "project",
        "asset",
        "inventory",
    }

    assert expected.issubset(set(registered))


@pytest.mark.parametrize(
    "department",
    [
        "hr",
        "payroll",
        "marketing",
        "project",
        "asset",
        "inventory",
    ],
)
def test_integration_module_can_be_loaded(department):
    from departments.finance.integrations import load_integration

    module = load_integration(department)

    assert module is not None


def test_unknown_integration_is_rejected():
    from departments.finance.integrations import load_integration

    with pytest.raises(ValueError):
        load_integration("unknown_department")
