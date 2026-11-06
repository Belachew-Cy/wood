
"""
Unit tests for Finance database models.

Run:
    pytest departments/finance/tests/test_models.py -v
"""

from decimal import Decimal

import pytest

from departments.finance import models


# ---------------------------------------------------------
# 1. CHART OF ACCOUNTS
# ---------------------------------------------------------

def test_create_finance_account(finance_db):
    account_id = models.create_finance_account(
        db=finance_db,
        account_code="1000",
        account_name="Cash at Bank",
        account_type="Asset",
        normal_balance="Debit",
        description="Cash account",
        created_by=1
    )

    assert account_id is not None

    accounts = models.get_finance_accounts(
        finance_db,
        active_only=True
    )

    assert len(accounts) == 1
    assert accounts[0]["account_code"] == "1000"
    assert accounts[0]["account_name"] == "Cash at Bank"


def test_duplicate_account_code_is_rejected(finance_db):
    models.create_finance_account(
        db=finance_db,
        account_code="1000",
        account_name="Cash",
        account_type="Asset",
        normal_balance="Debit",
        created_by=1
    )

    with pytest.raises(Exception):
        models.create_finance_account(
            db=finance_db,
            account_code="1000",
            account_name="Duplicate Cash",
            account_type="Asset",
            normal_balance="Debit",
            created_by=1
        )


# ---------------------------------------------------------
# 2. FINANCIAL PERIODS
# ---------------------------------------------------------

def test_create_financial_period(finance_db):
    period_id = models.create_financial_period(
        db=finance_db,
        period_name="FY 2026",
        start_date="2026-01-01",
        end_date="2026-12-31",
        created_by=1
    )

    assert period_id is not None

    period = finance_db.execute(
        """
        SELECT *
        FROM finance_periods
        WHERE id = ?
        """,
        (period_id,)
    ).fetchone()

    assert period is not None
    assert period["period_name"] == "FY 2026"


def test_find_open_financial_period(finance_db, sample_financial_period):
    period = models.get_open_financial_period(
        finance_db,
        "2026-06-15"
    )

    assert period is not None
    assert period["id"] == sample_financial_period


# ---------------------------------------------------------
# 3. JOURNAL ENTRIES
# ---------------------------------------------------------

def test_create_journal_entry(
    finance_db,
    sample_financial_period
):
    entry_id = models.create_journal_entry(
        db=finance_db,
        period_id=sample_financial_period,
        journal_date="2026-06-15",
        description="Test journal entry",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    assert entry_id is not None

    entry = finance_db.execute(
        """
        SELECT *
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (entry_id,)
    ).fetchone()

    assert entry is not None
    assert entry["description"] == "Test journal entry"
    assert entry["currency"] == "ETB"


# ---------------------------------------------------------
# 4. JOURNAL DEBIT/CREDIT VALIDATION
# ---------------------------------------------------------

def test_add_debit_journal_line(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = models.create_journal_entry(
        db=finance_db,
        period_id=sample_financial_period,
        journal_date="2026-06-15",
        description="Cash receipt",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    line_id = models.add_journal_line(
        db=finance_db,
        journal_entry_id=entry_id,
        account_id=sample_finance_accounts["cash"],
        debit=Decimal("1000.00"),
        credit=Decimal("0.00"),
        currency="ETB",
        exchange_rate=1
    )

    assert line_id is not None


def test_add_credit_journal_line(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = models.create_journal_entry(
        db=finance_db,
        period_id=sample_financial_period,
        journal_date="2026-06-15",
        description="Revenue received",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    line_id = models.add_journal_line(
        db=finance_db,
        journal_entry_id=entry_id,
        account_id=sample_finance_accounts["revenue"],
        debit=Decimal("0.00"),
        credit=Decimal("1000.00"),
        currency="ETB",
        exchange_rate=1
    )

    assert line_id is not None


def test_journal_balance_calculation(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = models.create_journal_entry(
        db=finance_db,
        period_id=sample_financial_period,
        journal_date="2026-06-15",
        description="Balanced transaction",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    models.add_journal_line(
        db=finance_db,
        journal_entry_id=entry_id,
        account_id=sample_finance_accounts["cash"],
        debit=Decimal("1000.00"),
        credit=Decimal("0.00"),
        currency="ETB",
        exchange_rate=1
    )

    models.add_journal_line(
        db=finance_db,
        journal_entry_id=entry_id,
        account_id=sample_finance_accounts["revenue"],
        debit=Decimal("0.00"),
        credit=Decimal("1000.00"),
        currency="ETB",
        exchange_rate=1
    )

    balance = models.get_journal_balance(
        finance_db,
        entry_id
    )

    assert Decimal(str(balance["total_debit"])) == Decimal("1000.00")
    assert Decimal(str(balance["total_credit"])) == Decimal("1000.00")
    assert Decimal(str(balance["difference"])) == Decimal("0.00")


def test_unbalanced_journal_is_detected(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = models.create_journal_entry(
        db=finance_db,
        period_id=sample_financial_period,
        journal_date="2026-06-15",
        description="Unbalanced transaction",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    models.add_journal_line(
        db=finance_db,
        journal_entry_id=entry_id,
        account_id=sample_finance_accounts["cash"],
        debit=Decimal("1000.00"),
        credit=Decimal("0.00"),
        currency="ETB",
        exchange_rate=1
    )

    balance = models.get_journal_balance(
        finance_db,
        entry_id
    )

    assert Decimal(str(balance["total_debit"])) == Decimal("1000.00")
    assert Decimal(str(balance["total_credit"])) == Decimal("0.00")
    assert Decimal(str(balance["difference"])) != Decimal("0.00")


# ---------------------------------------------------------
# 5. DEPARTMENT INTEGRATION RECORDS
# ---------------------------------------------------------

def test_record_department_transaction(finance_db):
    record_id = models.record_department_transaction(
        db=finance_db,
        department="HR",
        source_type="HR_EXPENSE",
        source_reference="HR-TEST-001",
        description="Staff training expense",
        amount=Decimal("5000.00"),
        currency="ETB",
        exchange_rate=1,
        created_by=1
    )

    assert record_id is not None

    records = models.get_department_integration_records(
        finance_db,
        department="HR"
    )

    assert len(records) == 1
    assert records[0]["source_reference"] == "HR-TEST-001"


# ---------------------------------------------------------
# 6. EXCHANGE RATES
# ---------------------------------------------------------

def test_add_exchange_rate(finance_db):
    rate_id = models.add_exchange_rate(
        db=finance_db,
        from_currency="USD",
        to_currency="ETB",
        exchange_rate=150.00,
        effective_date="2026-06-15",
        source="Test exchange rate",
        created_by=1
    )

    assert rate_id is not None


def test_get_exchange_rate(finance_db):
    models.add_exchange_rate(
        db=finance_db,
        from_currency="USD",
        to_currency="ETB",
        exchange_rate=150.00,
        effective_date="2026-06-15",
        source="Test exchange rate",
        created_by=1
    )

    rate = models.get_exchange_rate(
        finance_db,
        from_currency="USD",
        to_currency="ETB",
        effective_date="2026-06-15"
    )

    assert rate is not None
    assert Decimal(str(rate)) == Decimal("150.00")
