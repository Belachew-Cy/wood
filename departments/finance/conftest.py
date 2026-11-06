
"""
Shared pytest fixtures for the Finance module.

Database used:
    SQLite in-memory database

Purpose:
    - Isolate Finance tests from production data.
    - Initialize Finance tables before each test.
    - Provide a reusable database connection.
"""

import sqlite3

import pytest

from departments.finance import models


@pytest.fixture
def finance_db():
    """
    Create an isolated SQLite database for one test.

    The database is automatically closed after the test.
    """

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    try:
        models.init_finance_tables(db)
        yield db
    finally:
        db.close()


@pytest.fixture
def sample_finance_accounts(finance_db):
    """
    Create sample accounts for journal and accounting tests.

    These are test records only.
    """

    cash_account_id = models.create_finance_account(
        db=finance_db,
        account_code="1000",
        account_name="Cash at Bank",
        account_type="Asset",
        normal_balance="Debit",
        description="Test cash account",
        created_by=1
    )

    revenue_account_id = models.create_finance_account(
        db=finance_db,
        account_code="4000",
        account_name="Sales Revenue",
        account_type="Revenue",
        normal_balance="Credit",
        description="Test revenue account",
        created_by=1
    )

    expense_account_id = models.create_finance_account(
        db=finance_db,
        account_code="5000",
        account_name="Operating Expenses",
        account_type="Expense",
        normal_balance="Debit",
        description="Test expense account",
        created_by=1
    )

    return {
        "cash": cash_account_id,
        "revenue": revenue_account_id,
        "expense": expense_account_id
    }


@pytest.fixture
def sample_financial_period(finance_db):
    """
    Create an open financial period for journal tests.
    """

    period_id = models.create_financial_period(
        db=finance_db,
        period_name="Test Financial Year",
        start_date="2026-01-01",
        end_date="2026-12-31",
        created_by=1
    )

    return period_id
