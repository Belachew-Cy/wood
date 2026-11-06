
"""
Finance service-layer tests.

Run:
    pytest departments/finance/tests/test_finance_services.py -v

Note:
    These tests use the Finance models and service functions
    defined in the earlier Finance module steps.
"""

from decimal import Decimal

import pytest

from departments.finance import models, services


@pytest.fixture
def journal_fixture(
    finance_db,
    sample_financial_period,
    sample_finance_accounts
):
    """Create a balanced draft journal."""

    entry_id = models.create_journal_entry(
        db=finance_db,
        period_id=sample_financial_period,
        journal_date="2026-06-15",
        description="Finance service test",
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

    return entry_id


def test_submit_balanced_journal(
    finance_db,
    journal_fixture
):
    """A balanced draft journal should be submitted."""

    result = services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    assert result is not False

    entry = finance_db.execute(
        """
        SELECT status
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_fixture,)
    ).fetchone()

    assert entry["status"] == "Pending Approval"


def test_approve_journal(
    finance_db,
    journal_fixture
):
    """A different authorized user should approve the journal."""

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    result = services.approve_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        approved_by=2
    )

    assert result is not False

    entry = finance_db.execute(
        """
        SELECT status, approved_by
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_fixture,)
    ).fetchone()

    assert entry["status"] == "Approved"
    assert entry["approved_by"] == 2


def test_self_approval_is_rejected(
    finance_db,
    journal_fixture
):
    """The creator must not approve their own journal."""

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    with pytest.raises((ValueError, PermissionError)):
        services.approve_journal(
            db=finance_db,
            journal_entry_id=journal_fixture,
            approved_by=1
        )


def test_post_approved_journal(
    finance_db,
    journal_fixture
):
    """An approved journal should be posted."""

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    services.approve_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        approved_by=2
    )

    result = services.post_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        posted_by=3
    )

    assert result is not False

    entry = finance_db.execute(
        """
        SELECT status, posted_by
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_fixture,)
    ).fetchone()

    assert entry["status"] == "Posted"
    assert entry["posted_by"] == 3


def test_unapproved_journal_cannot_be_posted(
    finance_db,
    journal_fixture
):
    """A draft journal must not bypass approval."""

    with pytest.raises((ValueError, PermissionError)):
        services.post_journal(
            db=finance_db,
            journal_entry_id=journal_fixture,
            posted_by=3
        )


def test_reject_journal(
    finance_db,
    journal_fixture
):
    """An approver should be able to reject a submitted journal."""

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    result = services.reject_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        rejected_by=2,
        reason="Supporting document is missing"
    )

    assert result is not False

    entry = finance_db.execute(
        """
        SELECT status
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_fixture,)
    ).fetchone()

    assert entry["status"] == "Rejected"


def test_posted_journal_cannot_be_edited(
    finance_db,
    journal_fixture
):
    """Posted accounting records must not be edited directly."""

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    services.approve_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        approved_by=2
    )

    services.post_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        posted_by=3
    )

    with pytest.raises((ValueError, PermissionError)):
        services.add_journal_line(
            db=finance_db,
            journal_entry_id=journal_fixture,
            account_id=sample_finance_accounts["cash"],
            debit=Decimal("100.00"),
            credit=Decimal("0.00"),
            created_by=3
        )


def test_reverse_posted_journal(
    finance_db,
    journal_fixture
):
    """
    Reversal should create a separate journal.

    The original journal must not be marked Reversed until
    the reversal journal has itself been approved and posted.
    """

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    services.approve_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        approved_by=2
    )

    services.post_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        posted_by=3
    )

    reversal_id = services.reverse_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        reversed_by=4,
        reason="Correction of original transaction"
    )

    assert reversal_id is not None
    assert reversal_id != journal_fixture

    original = finance_db.execute(
        """
        SELECT status
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_fixture,)
    ).fetchone()

    reversal = finance_db.execute(
        """
        SELECT status
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (reversal_id,)
    ).fetchone()

    assert original["status"] == "Posted"
    assert reversal["status"] in ("Draft", "Pending Approval")


def test_finance_audit_log_created(
    finance_db,
    journal_fixture
):
    """Journal submission should produce an audit record."""

    services.submit_journal(
        db=finance_db,
        journal_entry_id=journal_fixture,
        submitted_by=1
    )

    logs = finance_db.execute(
        """
        SELECT *
        FROM finance_audit_logs
        WHERE entity_type = 'Journal'
          AND entity_id = ?
        """,
        (str(journal_fixture),)
    ).fetchall()

    assert len(logs) >= 1
