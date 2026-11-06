
"""
Finance Management Services
Dereje Wood & Kitchen Cabinet Management System

Business logic for:
- Chart of Accounts
- Journal Entries
- Journal Approval
- Journal Posting
- Journal Reversal
- Financial Periods
- Department Integrations
- Currency Conversion
- Financial Audit Logs

Uses the existing SQLite connection and Finance models.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
import sqlite3

from . import models


# =========================================================
# CONSTANTS
# =========================================================

BASE_CURRENCY = "ETB"

VALID_DEPARTMENTS = {
    "HR",
    "Payroll",
    "Marketing",
    "Project",
    "Asset",
    "Inventory"
}

VALID_ACCOUNT_TYPES = {
    "Asset",
    "Liability",
    "Equity",
    "Income",
    "Expense"
}


# =========================================================
# COMMON HELPERS
# =========================================================

def _now():
    return datetime.now().isoformat(timespec="seconds")


def _money(value):
    """Convert a number to a two-decimal Decimal value."""
    try:
        return Decimal(str(value)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid financial amount.")


def _require_user(user_id):
    if user_id is None:
        raise ValueError("Authenticated user is required.")


def _get_journal(db, journal_id):
    journal = db.execute(
        """
        SELECT *
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_id,)
    ).fetchone()

    if not journal:
        raise ValueError("Journal entry was not found.")

    return journal


def _audit(
    db,
    entity_type,
    entity_id,
    action,
    user_id,
    previous_value=None,
    new_value=None,
    department="Finance",
    remarks=None
):
    """Write an audit event using the existing Finance audit table."""

    models.create_finance_audit_log(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=user_id,
        previous_value=previous_value,
        new_value=new_value,
        department=department,
        remarks=remarks
    )


# =========================================================
# CHART OF ACCOUNTS
# =========================================================

def create_account(
    db,
    account_code,
    account_name,
    account_type,
    normal_balance,
    user_id,
    parent_account_id=None,
    description=None,
    is_control_account=False,
    allow_manual_posting=True
):
    _require_user(user_id)

    if account_type not in VALID_ACCOUNT_TYPES:
        raise ValueError("Invalid account type.")

    expected_balance = {
        "Asset": "Debit",
        "Expense": "Debit",
        "Liability": "Credit",
        "Equity": "Credit",
        "Income": "Credit"
    }

    if normal_balance != expected_balance[account_type]:
        raise ValueError(
            f"{account_type} accounts normally use "
            f"{expected_balance[account_type]} balance."
        )

    if parent_account_id:
        parent = db.execute(
            """
            SELECT id, account_type
            FROM finance_accounts
            WHERE id = ?
              AND status = 'Active'
            """,
            (parent_account_id,)
        ).fetchone()

        if not parent:
            raise ValueError("Parent account does not exist.")

        if parent["account_type"] != account_type:
            raise ValueError(
                "Parent and child account types must match."
            )

    try:
        account_id = models.create_finance_account(
            db=db,
            account_code=account_code,
            account_name=account_name,
            account_type=account_type,
            normal_balance=normal_balance,
            created_by=user_id,
            parent_account_id=parent_account_id,
            description=description,
            is_control_account=is_control_account,
            allow_manual_posting=allow_manual_posting
        )

        _audit(
            db,
            "Finance Account",
            account_id,
            "ACCOUNT_CREATED",
            user_id,
            new_value=account_code
        )

        db.commit()
        return account_id

    except Exception:
        db.rollback()
        raise


def get_accounts(db, account_type=None, active_only=True):
    sql = """
        SELECT *
        FROM finance_accounts
        WHERE 1 = 1
    """

    params = []

    if account_type:
        sql += " AND account_type = ?"
        params.append(account_type)

    if active_only:
        sql += " AND status = 'Active'"

    sql += " ORDER BY account_code"

    return db.execute(sql, params).fetchall()


# =========================================================
# JOURNAL ENTRY CREATION
# =========================================================

def create_journal(
    db,
    entry_number,
    period_id,
    journal_date,
    description,
    user_id,
    source_department="Finance",
    source_type=None,
    source_reference=None,
    currency=BASE_CURRENCY,
    exchange_rate=1
):
    _require_user(user_id)

    if source_department != "Finance" and (
        source_department not in VALID_DEPARTMENTS
    ):
        raise ValueError("Invalid source department.")

    try:
        journal_id = models.create_journal_entry(
            db=db,
            entry_number=entry_number,
            period_id=period_id,
            journal_date=journal_date,
            description=description,
            created_by=user_id,
            source_department=source_department,
            source_type=source_type,
            source_reference=source_reference,
            currency=currency,
            exchange_rate=exchange_rate
        )

        _audit(
            db,
            "Journal",
            journal_id,
            "JOURNAL_CREATED",
            user_id,
            new_value="Draft"
        )

        db.commit()
        return journal_id

    except Exception:
        db.rollback()
        raise


# =========================================================
# ADD JOURNAL LINE
# =========================================================

def add_journal_line(
    db,
    journal_id,
    account_id,
    user_id,
    debit=0,
    credit=0,
    currency=BASE_CURRENCY,
    exchange_rate=1,
    department=None,
    project_reference=None,
    description=None
):
    _require_user(user_id)

    try:
        line_id = models.add_journal_line(
            db=db,
            journal_entry_id=journal_id,
            account_id=account_id,
            created_at=_now(),
            debit=float(_money(debit)),
            credit=float(_money(credit)),
            currency=currency,
            exchange_rate=float(exchange_rate),
            department=department,
            project_reference=project_reference,
            line_description=description
        )

        _audit(
            db,
            "Journal Line",
            line_id,
            "JOURNAL_LINE_CREATED",
            user_id,
            new_value={
                "journal_id": journal_id,
                "account_id": account_id,
                "debit": str(debit),
                "credit": str(credit)
            }
        )

        db.commit()
        return line_id

    except Exception:
        db.rollback()
        raise


# =========================================================
# JOURNAL BALANCE VALIDATION
# =========================================================

def validate_journal(db, journal_id):
    journal = _get_journal(db, journal_id)

    if journal["status"] != "Draft":
        raise ValueError(
            "Only draft journals can be submitted."
        )

    balance = models.get_journal_balance(
        db,
        journal_id
    )

    if not balance["is_balanced"]:
        raise ValueError(
            "Journal is not balanced. "
            f"Debit: {balance['total_debit']}, "
            f"Credit: {balance['total_credit']}."
        )

    return balance


# =========================================================
# SUBMIT JOURNAL FOR APPROVAL
# =========================================================

def submit_journal(db, journal_id, user_id):
    _require_user(user_id)

    try:
        journal = _get_journal(db, journal_id)
        balance = validate_journal(db, journal_id)

        cursor = db.execute(
            """
            UPDATE finance_journal_entries
            SET status = 'Pending Approval',
                submitted_by = ?,
                submitted_at = ?,
                total_debit = ?,
                total_credit = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'Draft'
            """,
            (
                user_id,
                _now(),
                balance["total_debit"],
                balance["total_credit"],
                _now(),
                journal_id
            )
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Journal changed before submission."
            )

        _audit(
            db,
            "Journal",
            journal_id,
            "JOURNAL_SUBMITTED",
            user_id,
            previous_value=journal["status"],
            new_value="Pending Approval"
        )

        db.commit()

    except Exception:
        db.rollback()
        raise


# =========================================================
# APPROVE JOURNAL
# =========================================================

def approve_journal(db, journal_id, user_id, comment=None):
    _require_user(user_id)

    try:
        journal = _get_journal(db, journal_id)

        if journal["status"] != "Pending Approval":
            raise ValueError(
                "Journal is not awaiting approval."
            )

        if journal["created_by"] == user_id:
            raise ValueError(
                "The journal creator cannot approve their own journal."
            )

        balance = models.get_journal_balance(
            db,
            journal_id
        )

        if not balance["is_balanced"]:
            raise ValueError(
                "Cannot approve an unbalanced journal."
            )

        cursor = db.execute(
            """
            UPDATE finance_journal_entries
            SET status = 'Approved',
                approved_by = ?,
                approved_at = ?,
                total_debit = ?,
                total_credit = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'Pending Approval'
            """,
            (
                user_id,
                _now(),
                balance["total_debit"],
                balance["total_credit"],
                _now(),
                journal_id
            )
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Journal approval could not be completed."
            )

        _audit(
            db,
            "Journal",
            journal_id,
            "JOURNAL_APPROVED",
            user_id,
            previous_value="Pending Approval",
            new_value="Approved",
            remarks=comment
        )

        db.commit()

    except Exception:
        db.rollback()
        raise


# =========================================================
# POST JOURNAL TO GENERAL LEDGER
# =========================================================

def post_journal(db, journal_id, user_id):
    """
    Post an approved journal.

    Posting is restricted to an approved journal whose
    accounting period is still open.
    """

    _require_user(user_id)

    try:
        journal = _get_journal(db, journal_id)

        if journal["status"] != "Approved":
            raise ValueError(
                "Only approved journals can be posted."
            )

        period = db.execute(
            """
            SELECT status
            FROM finance_periods
            WHERE id = ?
            """,
            (journal["period_id"],)
        ).fetchone()

        if not period or period["status"] != "Open":
            raise ValueError(
                "Financial period is not open."
            )

        balance = models.get_journal_balance(
            db,
            journal_id
        )

        if not balance["is_balanced"]:
            raise ValueError(
                "Journal must be balanced before posting."
            )

        cursor = db.execute(
            """
            UPDATE finance_journal_entries
            SET status = 'Posted',
                posted_by = ?,
                posted_at = ?,
                total_debit = ?,
                total_credit = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'Approved'
            """,
            (
                user_id,
                _now(),
                balance["total_debit"],
                balance["total_credit"],
                _now(),
                journal_id
            )
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Journal posting could not be completed."
            )

        _audit(
            db,
            "Journal",
            journal_id,
            "JOURNAL_POSTED",
            user_id,
            previous_value="Approved",
            new_value="Posted"
        )

        db.commit()

    except Exception:
        db.rollback()
        raise


# =========================================================
# REJECT JOURNAL
# =========================================================

def reject_journal(db, journal_id, user_id, reason):
    _require_user(user_id)

    if not reason or not reason.strip():
        raise ValueError("Rejection reason is required.")

    try:
        journal = _get_journal(db, journal_id)

        if journal["status"] != "Pending Approval":
            raise ValueError(
                "Only pending journals can be rejected."
            )

        cursor = db.execute(
            """
            UPDATE finance_journal_entries
            SET status = 'Rejected',
                updated_at = ?
            WHERE id = ?
              AND status = 'Pending Approval'
            """,
            (_now(), journal_id)
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Journal rejection could not be completed."
            )

        _audit(
            db,
            "Journal",
            journal_id,
            "JOURNAL_REJECTED",
            user_id,
            previous_value="Pending Approval",
            new_value="Rejected",
            remarks=reason
        )

        db.commit()

    except Exception:
        db.rollback()
        raise


# =========================================================
# REVERSE POSTED JOURNAL
# =========================================================

def reverse_journal(
    db,
    journal_id,
    user_id,
    reversal_entry_number,
    reversal_date,
    reason
):
    """
    Create an equal and opposite reversal journal.

    The original posted journal is never edited or deleted.
    The reversal must be approved and posted separately.
    """

    _require_user(user_id)

    if not reason or not reason.strip():
        raise ValueError("Reversal reason is required.")

    try:
        original = _get_journal(db, journal_id)

        if original["status"] != "Posted":
            raise ValueError(
                "Only posted journals can be reversed."
            )

        if original["reversal_entry_id"]:
            raise ValueError(
                "This journal has already been reversed."
            )

        period = models.get_open_financial_period(
            db,
            reversal_date
        )

        if not period:
            raise ValueError(
                "No open financial period exists for reversal date."
            )

        lines = models.get_journal_lines(
            db,
            journal_id
        )

        if not lines:
            raise ValueError(
                "Original journal has no lines."
            )

        reversal_id = models.create_journal_entry(
            db=db,
            entry_number=reversal_entry_number,
            period_id=period["id"],
            journal_date=reversal_date,
            description=f"Reversal of {original['entry_number']}: {reason}",
            created_by=user_id,
            source_department="Finance",
            source_type="Journal Reversal",
            source_reference=str(journal_id),
            currency=original["currency"],
            exchange_rate=original["exchange_rate"]
        )

        for line in lines:
            models.add_journal_line(
                db=db,
                journal_entry_id=reversal_id,
                account_id=line["account_id"],
                created_at=_now(),
                debit=line["credit"],
                credit=line["debit"],
                currency=line["currency"],
                exchange_rate=line["exchange_rate"],
                department=line["department"],
                project_reference=line["project_reference"],
                line_description=f"Reversal: {line['line_description'] or ''}"
            )

        original_update = db.execute(
            """
            UPDATE finance_journal_entries
            SET status = 'Reversed',
                reversed_by = ?,
                reversed_at = ?,
                reversal_entry_id = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'Posted'
              AND reversal_entry_id IS NULL
            """,
            (
                user_id,
                _now(),
                reversal_id,
                _now(),
                journal_id
            )
        )

        if original_update.rowcount != 1:
            raise ValueError(
                "Original journal could not be marked as reversed."
            )

        _audit(
            db,
            "Journal",
            journal_id,
            "JOURNAL_REVERSED",
            user_id,
            previous_value="Posted",
            new_value="Reversed",
            remarks=reason
        )

        _audit(
            db,
            "Journal",
            reversal_id,
            "REVERSAL_JOURNAL_CREATED",
            user_id,
            new_value="Draft",
            remarks=f"Reversal of journal {journal_id}"
        )

        db.commit()

        return reversal_id

    except Exception:
        db.rollback()
        raise


# =========================================================
# DEPARTMENT INTEGRATION
# =========================================================

def receive_department_transaction(
    db,
    department,
    source_type,
    source_reference,
    description,
    amount,
    currency,
    exchange_rate=1
):
    """
    Receive a transaction reference from another department.

    This function does not automatically approve or post
    the transaction to the general ledger.
    """

    if department not in VALID_DEPARTMENTS:
        raise ValueError("Invalid integration department.")

    if not source_reference:
        raise ValueError(
            "Department source reference is required."
        )

    return models.record_department_transaction(
        db=db,
        department=department,
        source_type=source_type,
        source_reference=source_reference,
        description=description,
        amount=amount,
        currency=currency,
        exchange_rate=exchange_rate
    )


def approve_department_transaction(
    db,
    record_id,
    user_id
):
    _require_user(user_id)

    try:
        record = db.execute(
            """
            SELECT *
            FROM finance_integration_records
            WHERE id = ?
            """,
            (record_id,)
        ).fetchone()

        if not record:
            raise ValueError(
                "Department transaction was not found."
            )

        if record["status"] not in {
            "Received",
            "Pending Review"
        }:
            raise ValueError(
                "Transaction is not eligible for review."
            )

        cursor = db.execute(
            """
            UPDATE finance_integration_records
            SET status = 'Approved',
                reviewed_by = ?,
                reviewed_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status IN ('Received', 'Pending Review')
            """,
            (
                user_id,
                _now(),
                _now(),
                record_id
            )
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Integration approval could not be completed."
            )

        _audit(
            db,
            "Department Integration",
            record_id,
            "INTEGRATION_APPROVED",
            user_id,
            previous_value=record["status"],
            new_value="Approved",
            department=record["department"]
        )

        db.commit()

    except Exception:
        db.rollback()
        raise


def reject_department_transaction(
    db,
    record_id,
    user_id,
    reason
):
    _require_user(user_id)

    if not reason or not reason.strip():
        raise ValueError("Rejection reason is required.")

    try:
        record = db.execute(
            """
            SELECT *
            FROM finance_integration_records
            WHERE id = ?
            """,
            (record_id,)
        ).fetchone()

        if not record:
            raise ValueError(
                "Department transaction was not found."
            )

        if record["status"] not in {
            "Received",
            "Pending Review"
        }:
            raise ValueError(
                "Transaction is not eligible for rejection."
            )

        cursor = db.execute(
            """
            UPDATE finance_integration_records
            SET status = 'Rejected',
                reviewed_by = ?,
                reviewed_at = ?,
                rejection_reason = ?,
                updated_at = ?
            WHERE id = ?
              AND status IN ('Received', 'Pending Review')
            """,
            (
                user_id,
                _now(),
                reason,
                _now(),
                record_id
            )
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Integration rejection could not be completed."
            )

        _audit(
            db,
            "Department Integration",
            record_id,
            "INTEGRATION_REJECTED",
            user_id,
            previous_value=record["status"],
            new_value="Rejected",
            department=record["department"],
            remarks=reason
        )

        db.commit()

    except Exception:
        db.rollback()
        raise


# =========================================================
# CURRENCY CONVERSION
# =========================================================

def convert_currency(
    db,
    amount,
    from_currency,
    to_currency,
    effective_date
):
    amount = _money(amount)

    rate = Decimal(
        str(
            models.get_exchange_rate(
                db,
                from_currency,
                to_currency,
                effective_date
            )
        )
    )

    converted = (amount * rate).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    return {
        "original_amount": str(amount),
        "from_currency": from_currency.upper(),
        "to_currency": to_currency.upper(),
        "exchange_rate": str(rate),
        "converted_amount": str(converted),
        "effective_date": effective_date
    }


# =========================================================
# FINANCIAL DASHBOARD SUMMARY
# =========================================================

def get_finance_dashboard_summary(db):
    """
    Return posted ledger totals.

    The base amounts are used so foreign-currency entries
    are reported consistently in the base currency.
    """

    income = db.execute(
        """
        SELECT COALESCE(SUM(jl.credit_base - jl.debit_base), 0)
            AS total
        FROM finance_journal_lines jl
        JOIN finance_journal_entries je
            ON je.id = jl.journal_entry_id
        JOIN finance_accounts a
            ON a.id = jl.account_id
        WHERE je.status = 'Posted'
          AND a.account_type = 'Income'
        """
    ).fetchone()["total"]

    expenses = db.execute(
        """
        SELECT COALESCE(SUM(jl.debit_base - jl.credit_base), 0)
            AS total
        FROM finance_journal_lines jl
        JOIN finance_journal_entries je
            ON je.id = jl.journal_entry_id
        JOIN finance_accounts a
            ON a.id = jl.account_id
        WHERE je.status = 'Posted'
          AND a.account_type = 'Expense'
        """
    ).fetchone()["total"]

    income = _money(income)
    expenses = _money(expenses)

    return {
        "currency": BASE_CURRENCY,
        "income": float(income),
        "expenses": float(expenses),
        "net": float(income - expenses)
    }


# =========================================================
# GENERAL LEDGER
# =========================================================

def get_general_ledger(
    db,
    account_id,
    start_date=None,
    end_date=None
):
    """Retrieve posted ledger entries for one account."""

    sql = """
        SELECT
            je.entry_number,
            je.journal_date,
            je.description,
            je.source_department,
            jl.line_description,
            jl.debit_base,
            jl.credit_base,
            a.account_code,
            a.account_name
        FROM finance_journal_lines jl
        JOIN finance_journal_entries je
            ON je.id = jl.journal_entry_id
        JOIN finance_accounts a
            ON a.id = jl.account_id
        WHERE jl.account_id = ?
          AND je.status IN ('Posted', 'Reversed')
    """

    params = [account_id]

    if start_date:
        sql += " AND je.journal_date >= ?"
        params.append(start_date)

    if end_date:
        sql += " AND je.journal_date <= ?"
        params.append(end_date)

    sql += " ORDER BY je.journal_date, je.id, jl.id"

    return db.execute(sql, params).fetchall()


# =========================================================
# FINANCE AUDIT HISTORY
# =========================================================

def get_finance_audit_logs(
    db,
    entity_type=None,
    entity_id=None,
    limit=100
):
    """Retrieve Finance audit records."""

    limit = max(1, min(int(limit), 1000))

    sql = """
        SELECT *
        FROM finance_audit_logs
        WHERE 1 = 1
    """

    params = []

    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)

    if entity_id is not None:
        sql += " AND entity_id = ?"
        params.append(entity_id)

    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)

    return db.execute(sql, params).fetchall()
