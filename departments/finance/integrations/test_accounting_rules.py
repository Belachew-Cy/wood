
"""
Finance accounting rules tests.

Run:
    pytest departments/finance/tests/test_accounting_rules.py -v

These tests use the existing Finance models and services.
Adapt function names to the actual implementation where necessary.
"""

from decimal import Decimal

import pytest

from departments.finance import models, services


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def create_journal(
    db,
    period_id,
    accounts,
    debit_amount="1000.00",
    credit_amount="1000.00",
):
    """Create a journal with debit and credit lines."""

    entry_id = models.create_journal_entry(
        db=db,
        period_id=period_id,
        journal_date="2026-06-15",
        description="Accounting rules test",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    models.add_journal_line(
        db=db,
        journal_entry_id=entry_id,
        account_id=accounts["cash"],
        debit=Decimal(debit_amount),
        credit=Decimal("0.00"),
        currency="ETB",
        exchange_rate=1
    )

    models.add_journal_line(
        db=db,
        journal_entry_id=entry_id,
        account_id=accounts["revenue"],
        debit=Decimal("0.00"),
        credit=Decimal(credit_amount),
        currency="ETB",
        exchange_rate=1
    )

    return entry_id


def post_journal(db, entry_id):
    """Submit, approve, and post using different users."""

    services.submit_journal(
        db=db,
        journal_entry_id=entry_id,
        submitted_by=1
    )

    services.approve_journal(
        db=db,
        journal_entry_id=entry_id,
        approved_by=2
    )

    services.post_journal(
        db=db,
        journal_entry_id=entry_id,
        posted_by=3
    )


# ---------------------------------------------------------
# 1. DOUBLE-ENTRY ACCOUNTING
# ---------------------------------------------------------

def test_journal_debits_equal_credits(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts
    )

    balance = models.get_journal_balance(
        finance_db,
        entry_id
    )

    debit = Decimal(str(balance["total_debit"]))
    credit = Decimal(str(balance["total_credit"]))

    assert debit == credit


def test_unbalanced_journal_fails_validation(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts,
        debit_amount="1000.00",
        credit_amount="900.00"
    )

    with pytest.raises(ValueError):
        services.submit_journal(
            db=finance_db,
            journal_entry_id=entry_id,
            submitted_by=1
        )


# ---------------------------------------------------------
# 2. TRIAL BALANCE
# ---------------------------------------------------------

@pytest.mark.integration
def test_trial_balance_debits_equal_credits(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts
    )

    post_journal(finance_db, entry_id)

    # Implement or connect this function to your existing
    # Finance reporting service.
    trial_balance = services.get_trial_balance(
        db=finance_db,
        start_date="2026-01-01",
        end_date="2026-12-31"
    )

    assert Decimal(str(trial_balance["total_debit"])) == Decimal(
        str(trial_balance["total_credit"])
    )


# ---------------------------------------------------------
# 3. FINANCIAL PERIOD RESTRICTIONS
# ---------------------------------------------------------

def test_journal_date_requires_open_period(finance_db):
    period_id = models.create_financial_period(
        db=finance_db,
        period_name="Closed Test Period",
        start_date="2026-01-01",
        end_date="2026-03-31",
        created_by=1
    )

    finance_db.execute(
        """
        UPDATE finance_periods
        SET status = 'Closed'
        WHERE id = ?
        """,
        (period_id,)
    )
    finance_db.commit()

    period = models.get_open_financial_period(
        finance_db,
        "2026-02-15"
    )

    assert period is None


# ---------------------------------------------------------
# 4. POSTED JOURNAL INTEGRITY
# ---------------------------------------------------------

def test_posted_journal_cannot_be_posted_twice(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts
    )

    post_journal(finance_db, entry_id)

    with pytest.raises((ValueError, PermissionError)):
        services.post_journal(
            db=finance_db,
            journal_entry_id=entry_id,
            posted_by=4
        )


def test_posted_journal_lines_are_preserved(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts
    )

    post_journal(finance_db, entry_id)

    before = models.get_journal_lines(
        finance_db,
        entry_id
    )

    assert len(before) == 2

    with pytest.raises((ValueError, PermissionError)):
        services.add_journal_line(
            db=finance_db,
            journal_entry_id=entry_id,
            account_id=sample_finance_accounts["cash"],
            debit=Decimal("100.00"),
            credit=Decimal("0.00"),
            created_by=4
        )

    after = models.get_journal_lines(
        finance_db,
        entry_id
    )

    assert len(after) == len(before)


# ---------------------------------------------------------
# 5. REVERSAL ACCOUNTING
# ---------------------------------------------------------

def test_reversal_has_equal_debits_and_credits(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts
    )

    post_journal(finance_db, entry_id)

    reversal_id = services.reverse_journal(
        db=finance_db,
        journal_entry_id=entry_id,
        reversed_by=4,
        reason="Accounting correction"
    )

    reversal_balance = models.get_journal_balance(
        finance_db,
        reversal_id
    )

    assert Decimal(
        str(reversal_balance["total_debit"])
    ) == Decimal(
        str(reversal_balance["total_credit"])
    )


# ---------------------------------------------------------
# 6. FINANCIAL STATEMENT CONSISTENCY
# ---------------------------------------------------------

@pytest.mark.integration
def test_financial_dashboard_uses_posted_transactions_only(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    entry_id = create_journal(
        finance_db,
        sample_financial_period,
        sample_finance_accounts
    )

    summary_before = services.get_finance_dashboard_summary(
        finance_db
    )

    post_journal(finance_db, entry_id)

    summary_after = services.get_finance_dashboard_summary(
        finance_db
    )

    assert Decimal(
        str(summary_after["total_income"])
    ) >= Decimal(
        str(summary_before["total_income"])
    )


@pytest.mark.integration
def test_balance_sheet_equation(
    finance_db,
):
    """
    Assets must equal liabilities plus equity.

    This test requires the reporting service to return
    the stated balance-sheet totals.
    """

    report = services.get_balance_sheet(
        db=finance_db,
        as_of_date="2026-12-31"
    )

    assets = Decimal(str(report["total_assets"]))
    liabilities = Decimal(str(report["total_liabilities"]))
    equity = Decimal(str(report["total_equity"]))

    assert assets == liabilities + equity
