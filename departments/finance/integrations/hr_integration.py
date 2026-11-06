
"""
HR - Finance Integration
Dereje Wood & Kitchen Cabinet Management System

Purpose:
- Receive approved HR financial transactions.
- Prevent duplicate imports.
- Validate financial data.
- Send transactions to Finance for review.
- Maintain source references to existing HR records.

Important:
This module does not create or duplicate HR records.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime


# =========================================================
# CONFIGURATION
# =========================================================

DEPARTMENT_NAME = "HR"

SUPPORTED_CURRENCIES = {
    "ETB",
    "USD",
    "EUR",
    "GBP"
}

VALID_TRANSACTION_TYPES = {
    "HR_EXPENSE",
    "RECRUITMENT_EXPENSE",
    "TRAINING_EXPENSE",
    "EMPLOYEE_WELFARE_EXPENSE",
    "HR_ADMIN_EXPENSE"
}


# =========================================================
# VALIDATION HELPERS
# =========================================================

def _validate_amount(amount):
    try:
        value = Decimal(str(amount))

        if not value.is_finite() or value <= 0:
            raise ValueError(
                "Transaction amount must be greater than zero."
            )

        return value

    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid HR transaction amount.")


def _validate_currency(currency):
    currency = str(currency).upper().strip()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported transaction currency.")

    return currency


def _validate_reference(source_reference):
    if source_reference is None:
        raise ValueError("HR source reference is required.")

    reference = str(source_reference).strip()

    if not reference:
        raise ValueError("HR source reference cannot be empty.")

    return reference


# =========================================================
# SOURCE TRANSACTION VALIDATION
# =========================================================

def validate_hr_transaction(transaction):
    """
    Validate an approved financial transaction received
    from the existing HR module.

    Expected adapter payload:
    {
        "source_reference": "HR-EXP-1001",
        "transaction_type": "TRAINING_EXPENSE",
        "description": "Employee training expense",
        "amount": "15000.00",
        "currency": "ETB",
        "exchange_rate": "1",
        "approval_status": "Approved"
    }
    """

    if not isinstance(transaction, dict):
        raise ValueError("Invalid HR transaction payload.")

    required_fields = [
        "source_reference",
        "transaction_type",
        "description",
        "amount",
        "currency",
        "approval_status"
    ]

    for field in required_fields:
        if transaction.get(field) in (None, ""):
            raise ValueError(
                f"Missing required HR transaction field: {field}"
            )

    source_reference = _validate_reference(
        transaction["source_reference"]
    )

    transaction_type = str(
        transaction["transaction_type"]
    ).upper().strip()

    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise ValueError("Unsupported HR transaction type.")

    if str(transaction["approval_status"]).strip().lower() != "approved":
        raise ValueError(
            "Only approved HR transactions can be imported."
        )

    description = str(
        transaction["description"]
    ).strip()

    if len(description) > 500:
        raise ValueError("Transaction description is too long.")

    amount = _validate_amount(transaction["amount"])

    currency = _validate_currency(
        transaction["currency"]
    )

    exchange_rate = Decimal(
        str(transaction.get("exchange_rate", "1"))
    )

    if not exchange_rate.is_finite() or exchange_rate <= 0:
        raise ValueError("Invalid exchange rate.")

    return {
        "department": DEPARTMENT_NAME,
        "source_type": transaction_type,
        "source_reference": source_reference,
        "description": description,
        "amount": str(amount),
        "currency": currency,
        "exchange_rate": str(exchange_rate),
        "approval_status": "Approved"
    }


# =========================================================
# RECEIVE HR TRANSACTION
# =========================================================

def receive_hr_transaction(db, transaction):
    """
    Receive an approved transaction from HR.

    The record enters Finance as a pending review item.
    It is not automatically posted to the General Ledger.
    """

    validated = validate_hr_transaction(transaction)

    existing = db.execute(
        """
        SELECT id, status
        FROM finance_integration_records
        WHERE department = ?
          AND source_type = ?
          AND source_reference = ?
        """,
        (
            DEPARTMENT_NAME,
            validated["source_type"],
            validated["source_reference"]
        )
    ).fetchone()

    if existing:
        return {
            "success": False,
            "duplicate": True,
            "record_id": existing["id"],
            "status": existing["status"],
            "message": "This HR transaction has already been imported."
        }

    record_id = None

    try:
        record_id = _insert_hr_transaction(
            db,
            validated
        )

        db.commit()

        return {
            "success": True,
            "duplicate": False,
            "record_id": record_id,
            "status": "Received",
            "message": "HR transaction received successfully."
        }

    except Exception:
        db.rollback()
        raise


# =========================================================
# INSERT INTO FINANCE INTEGRATION TABLE
# =========================================================

def _insert_hr_transaction(db, transaction):
    """
    Insert a source reference into the existing Finance
    integration table.

    No HR employee or payroll data is copied.
    """

    now = datetime.now().isoformat(timespec="seconds")

    amount = Decimal(transaction["amount"])
    exchange_rate = Decimal(transaction["exchange_rate"])

    base_amount = amount * exchange_rate

    cursor = db.execute(
        """
        INSERT INTO finance_integration_records (
            department,
            source_type,
            source_reference,
            description,
            amount,
            currency,
            exchange_rate,
            base_amount,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Received', ?)
        """,
        (
            transaction["department"],
            transaction["source_type"],
            transaction["source_reference"],
            transaction["description"],
            str(amount),
            transaction["currency"],
            str(exchange_rate),
            str(base_amount),
            now
        )
    )

    return cursor.lastrowid


# =========================================================
# IMPORT MULTIPLE HR TRANSACTIONS
# =========================================================

def import_hr_transactions(db, transactions):
    """
    Import a collection of approved HR transactions.

    Each transaction is checked for duplicate references.
    """

    if not isinstance(transactions, (list, tuple)):
        raise ValueError("Transactions must be a list.")

    results = []

    for transaction in transactions:
        try:
            result = receive_hr_transaction(
                db,
                transaction
            )

            results.append(result)

        except Exception as exc:
            results.append({
                "success": False,
                "duplicate": False,
                "message": str(exc)
            })

    return {
        "department": DEPARTMENT_NAME,
        "total": len(transactions),
        "successful": sum(
            1 for item in results if item.get("success")
        ),
        "duplicates": sum(
            1 for item in results if item.get("duplicate")
        ),
        "failed": sum(
            1 for item in results
            if not item.get("success")
            and not item.get("duplicate")
        ),
        "results": results
    }


# =========================================================
# HR INTEGRATION STATUS
# =========================================================

def get_hr_integration_status(db):
    """
    Return HR integration statistics.
    """

    rows = db.execute(
        """
        SELECT status, COUNT(*) AS total
        FROM finance_integration_records
        WHERE department = ?
        GROUP BY status
        """,
        (DEPARTMENT_NAME,)
    ).fetchall()

    summary = {
        "Received": 0,
        "Pending Review": 0,
        "Approved": 0,
        "Posted": 0,
        "Rejected": 0,
        "Failed": 0
    }

    for row in rows:
        summary[row["status"]] = row["total"]

    return {
        "department": DEPARTMENT_NAME,
        "total": sum(summary.values()),
        "statuses": summary
    }


# =========================================================
# GET HR IMPORT HISTORY
# =========================================================

def get_hr_import_history(db, limit=100):
    limit = max(1, min(int(limit), 1000))

    return db.execute(
        """
        SELECT
            id,
            source_type,
            source_reference,
            description,
            amount,
            currency,
            exchange_rate,
            base_amount,
            status,
            created_at
        FROM finance_integration_records
        WHERE department = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (DEPARTMENT_NAME, limit)
    ).fetchall()
