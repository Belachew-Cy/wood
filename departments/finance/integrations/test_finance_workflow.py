
"""
End-to-end Finance workflow tests.

Run:
    pytest departments/finance/tests/test_finance_workflow.py -v

Important:
    These tests exercise the Finance-side workflow.
    They do not replace the actual HR, Payroll, Procurement,
    Inventory, Asset, Project, or Marketing approval workflows.
"""

from decimal import Decimal

import pytest

from departments.finance import models, services
from departments.finance.integrations import (
    hr_integration,
    payroll_integration,
    marketing_integration,
    project_integration,
    asset_integration,
    inventory_integration,
)


@pytest.fixture
def workflow_db(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    return {
        "db": finance_db,
        "period_id": sample_financial_period,
        "accounts": sample_finance_accounts,
    }


# ---------------------------------------------------------
# TRANSACTION PAYLOADS
# ---------------------------------------------------------

def make_transaction(
    source_reference,
    source_type,
    amount="1000.00",
    **extra
):
    transaction = {
        "source_reference": source_reference,
        "source_type": source_type,
        "description": "Approved department transaction",
        "amount": amount,
        "currency": "ETB",
        "exchange_rate": "1",
        "approval_status": "Approved",
    }

    transaction.update(extra)
    return transaction


# ---------------------------------------------------------
# DEPARTMENT INTEGRATION IMPORTS
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "module,transaction",
    [
        (
            hr_integration,
            make_transaction(
                "HR-WF-001",
                "HR_EXPENSE"
            )
        ),
        (
            payroll_integration,
            make_transaction(
                "PAY-WF-001",
                "SALARY_EXPENSE"
            )
        ),
        (
            marketing_integration,
            make_transaction(
                "MKT-WF-001",
                "CAMPAIGN_EXPENSE"
            )
        ),
        (
            project_integration,
            make_transaction(
                "PRJ-WF-001",
                "PROJECT_EXPENSE",
                project_reference="PROJECT-001"
            )
        ),
        (
            asset_integration,
            make_transaction(
                "AST-WF-001",
                "ASSET_MAINTENANCE",
                asset_reference="ASSET-001"
            )
        ),
        (
            inventory_integration,
            make_transaction(
                "INV-WF-001",
                "MATERIAL_CONSUMPTION",
                item_reference="ITEM-001"
            )
        ),
    ],
)
def test_department_transaction_import(
    workflow_db,
    module,
    transaction
):
    db = workflow_db["db"]

    result = module.receive_transaction(
        db=db,
        transaction=transaction
    )

    assert result is not None


# ---------------------------------------------------------
# DUPLICATE TRANSACTION PREVENTION
# ---------------------------------------------------------

@pytest.mark.integration
def test_duplicate_import_is_prevented(
    workflow_db
):
    db = workflow_db["db"]

    transaction = make_transaction(
        "HR-DUPLICATE-001",
        "HR_EXPENSE"
    )

    first = hr_integration.receive_transaction(
        db=db,
        transaction=transaction
    )

    second = hr_integration.receive_transaction(
        db=db,
        transaction=transaction
    )

    assert first is not None
    assert second is not None

    records = models.get_department_integration_records(
        db,
        department="HR"
    )

    matching = [
        record for record in records
        if record["source_reference"] == "HR-DUPLICATE-001"
    ]

    assert len(matching) == 1


# ---------------------------------------------------------
# FINANCE REVIEW
# ---------------------------------------------------------

@pytest.mark.integration
def test_finance_can_review_imported_transaction(
    workflow_db
):
    db = workflow_db["db"]

    transaction = make_transaction(
        "HR-REVIEW-001",
        "HR_EXPENSE"
    )

    record_id = hr_integration.receive_transaction(
        db=db,
        transaction=transaction
    )

    result = services.approve_department_transaction(
        db=db,
        integration_record_id=record_id,
        approved_by=2
    )

    assert result is not False

    record = db.execute(
        """
        SELECT status
        FROM finance_integration_records
        WHERE id = ?
        """,
        (record_id,)
    ).fetchone()

    assert record["status"] == "Approved"


# ---------------------------------------------------------
# JOURNAL CREATION AND POSTING
# ---------------------------------------------------------

@pytest.mark.integration
def test_finance_journal_lifecycle(
    workflow_db
):
    db = workflow_db["db"]

    entry_id = models.create_journal_entry(
        db=db,
        period_id=workflow_db["period_id"],
        journal_date="2026-06-15",
        description="Integrated department expense",
        created_by=1,
        currency="ETB",
        exchange_rate=1
    )

    accounts = workflow_db["accounts"]

    models.add_journal_line(
        db=db,
        journal_entry_id=entry_id,
        account_id=accounts["expense"],
        debit=Decimal("1000.00"),
        credit=Decimal("0.00"),
        currency="ETB",
        exchange_rate=1
    )

    models.add_journal_line(
        db=db,
        journal_entry_id=entry_id,
        account_id=accounts["cash"],
        debit=Decimal("0.00"),
        credit=Decimal("1000.00"),
        currency="ETB",
        exchange_rate=1
    )

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

    journal = db.execute(
        """
        SELECT status
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (entry_id,)
    ).fetchone()

    assert journal["status"] == "Posted"


# ---------------------------------------------------------
# AUDIT LOG
# ---------------------------------------------------------

@pytest.mark.integration
def test_workflow_creates_audit_records(
    workflow_db
):
    db = workflow_db["db"]

    transaction = make_transaction(
        "HR-AUDIT-001",
        "HR_EXPENSE"
    )

    record_id = hr_integration.receive_transaction(
        db=db,
        transaction=transaction
    )

    services.approve_department_transaction(
        db=db,
        integration_record_id=record_id,
        approved_by=2
    )

    logs = db.execute(
        """
        SELECT *
        FROM finance_audit_logs
        WHERE entity_type = 'Integration'
          AND entity_id = ?
        """,
        (str(record_id),)
    ).fetchall()

    assert len(logs) >= 1
