
"""
Finance permission tests.

Run:
    pytest departments/finance/tests/test_permissions.py -v

These tests validate permission definitions.
Connect the adapter below to your existing shared RBAC
implementation before running the authorization tests.
"""

import pytest

from departments.finance.permissions import (
    FINANCE_PERMISSIONS,
    get_finance_permission_codes,
    get_finance_permissions,
    get_finance_permissions_by_category,
)


# ---------------------------------------------------------
# PERMISSION DEFINITIONS
# ---------------------------------------------------------

def test_finance_permissions_are_defined():
    assert FINANCE_PERMISSIONS
    assert isinstance(FINANCE_PERMISSIONS, dict)


def test_permission_codes_are_unique():
    codes = get_finance_permission_codes()

    assert len(codes) == len(set(codes))


def test_required_finance_permissions_exist():
    codes = set(get_finance_permission_codes())

    required = {
        "finance.dashboard.view",
        "finance.accounts.view",
        "finance.accounts.create",
        "finance.journals.view",
        "finance.journals.create",
        "finance.journals.approve",
        "finance.journals.post",
        "finance.ledger.view",
        "finance.reports.view",
        "finance.audit.view",
    }

    assert required.issubset(codes)


def test_permission_categories_are_available():
    categories = get_finance_permissions_by_category()

    assert isinstance(categories, dict)
    assert categories


# ---------------------------------------------------------
# AUDITOR READ-ONLY POLICY
# ---------------------------------------------------------

def test_auditor_default_permissions_are_read_only():
    """
    Auditor should only receive viewing and reporting permissions.

    Adapt this test to the actual structure of FINANCE_ROLE_DEFAULTS.
    """

    from departments.finance.permissions import FINANCE_ROLE_DEFAULTS

    auditor_permissions = set(
        FINANCE_ROLE_DEFAULTS.get("Auditor", [])
    )

    prohibited = {
        "finance.accounts.create",
        "finance.accounts.edit",
        "finance.journals.create",
        "finance.journals.edit",
        "finance.journals.approve",
        "finance.journals.post",
        "finance.journals.reverse",
        "finance.integration.import",
        "finance.integration.approve",
        "finance.budgets.manage",
    }

    assert auditor_permissions.isdisjoint(prohibited)


# ---------------------------------------------------------
# SHARED RBAC ADAPTER
# ---------------------------------------------------------

@pytest.fixture
def permission_checker():
    """
    Replace this adapter with your existing shared RBAC checker.

    Expected interface:
        checker(user_id, permission_code) -> bool

    Do not create a separate Finance permission database.
    """

    from core.auth import has_permission

    def checker(user_id, permission_code):
        return has_permission(
            user_id=user_id,
            permission=permission_code
        )

    return checker


# ---------------------------------------------------------
# AUTHORIZATION TESTS
# ---------------------------------------------------------

@pytest.mark.integration
def test_unauthorized_user_cannot_view_finance_dashboard(
    permission_checker
):
    assert permission_checker(
        user_id=999999,
        permission_code="finance.dashboard.view"
    ) is False


@pytest.mark.integration
def test_unauthorized_user_cannot_post_journals(
    permission_checker
):
    assert permission_checker(
        user_id=999999,
        permission_code="finance.journals.post"
    ) is False


@pytest.mark.integration
def test_unauthorized_user_cannot_approve_integrations(
    permission_checker
):
    assert permission_checker(
        user_id=999999,
        permission_code="finance.integration.approve"
    ) is False
