
"""
Marketing - Finance Integration
Dereje Wood & Kitchen Cabinet Management System

Purpose:
- Import approved marketing expenses.
- Import approved advertising and campaign costs.
- Receive approved marketing revenue references.
- Prevent duplicate transactions.
- Maintain source references.
- Send transactions for Finance review.

Does not duplicate Marketing records or automatically
post transactions to the General Ledger.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime


DEPARTMENT_NAME = "Marketing"

SUPPORTED_CURRENCIES = {
    "ETB", "USD", "EUR", "GBP"
}

VALID_TRANSACTION_TYPES = {
    "CAMPAIGN_EXPENSE",
    "ADVERTISING_EXPENSE",
    "PROMOTION_EXPENSE",
    "MARKETING_EVENT_EXPENSE",
    "MARKETING_ADMIN_EXPENSE",
    "SALES_REVENUE"
}


# =========================================================
# VALIDATION
# =========================================================

def _decimal(value, field_name):
    try:
        result = Decimal(str(value))

        if not result.is_finite():
            raise ValueError()

        return result

    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {field_name}.")


def validate_marketing_transaction(transaction):

    if not isinstance(transaction, dict):
        raise ValueError("Invalid marketing transaction.")

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
                f"Missing marketing field: {field}"
            )

    reference = str(
        transaction["source_reference"]
    ).strip()

    if not reference:
        raise ValueError(
            "Marketing source reference is required."
        )

    transaction_type = str(
        transaction["transaction_type"]
    ).upper().strip()

    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise ValueError(
            "Unsupported marketing transaction type."
        )

    if str(transaction["approval_status"]).lower().strip() != "approved":
        raise ValueError(
            "Only approved marketing transactions can be imported."
        )

    amount = _decimal(
        transaction["amount"],
        "marketing amount"
    )

    if amount <= 0:
        raise ValueError(
            "Transaction amount must be greater than zero."
        )

    currency = str(
        transaction["currency"]
    ).upper().strip()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            "Unsupported marketing currency."
        )

    exchange_rate = _decimal(
        transaction.get("exchange_rate", "1"),
        "exchange rate"
    )

    if exchange_rate <= 0:
        raise ValueError(
            "Exchange rate must be greater than zero."
        )

    description = str(
        transaction["description"]
    ).strip()

    if len(description) > 500:
        raise ValueError(
            "Transaction description is too long."
        )

    return {
        "department": DEPARTMENT_NAME,
        "source_type": transaction_type,
        "source_reference": reference,
        "description": description,
        "amount": amount,
        "currency": currency,
        "exchange_rate": exchange_rate,
        "base_amount": amount * exchange_rate
    }


# =========================================================
# RECEIVE MARKETING TRANSACTION
# =========================================================

def receive_marketing_transaction(db, transaction):
    """
    Receive one approved Marketing transaction.

    Finance receives the reference as a review item.
    The transaction is not automatically posted.
    """

    data = validate_marketing_transaction(transaction)

    try:
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
            ON CONFLICT(
                department,
                source_type,
                source_reference
            ) DO NOTHING
            """,
            (
                data["department"],
                data["source_type"],
                data["source_reference"],
                data["description"],
                str(data["amount"]),
                data["currency"],
                str(data["exchange_rate"]),
                str(data["base_amount"]),
                datetime.now().isoformat(timespec="seconds")
            )
        )

        if cursor.rowcount == 0:
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
                    data["source_type"],
                    data["source_reference"]
                )
            ).fetchone()

            db.commit()

            return {
                "success": False,
                "duplicate": True,
                "record_id": existing["id"],
                "status": existing["status"],
                "message": "Marketing transaction already imported."
            }

        record_id = cursor.lastrowid

        db.commit()

        return {
            "success": True,
            "duplicate": False,
            "record_id": record_id,
            "status": "Received",
            "message": "Marketing transaction received successfully."
        }

    except Exception:
        db.rollback()
        raise


# =========================================================
# IMPORT MULTIPLE MARKETING TRANSACTIONS
# =========================================================

def import_marketing_transactions(db, transactions):
    """
    Import approved Marketing transactions in bulk.
    """

    if not isinstance(transactions, (list, tuple)):
        raise ValueError("Transactions must be a list.")

    results = []

    for transaction in transactions:
        try:
            result = receive_marketing_transaction(
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
            1 for item in results
            if item.get("success")
        ),
        "duplicates": sum(
            1 for item in results
            if item.get("duplicate")
        ),
        "failed": sum(
            1 for item in results
            if not item.get("success")
            and not item.get("duplicate")
        ),
        "results": results
    }


# =========================================================
# MARKETING INTEGRATION STATUS
# =========================================================

def get_marketing_integration_status(db):

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
# MARKETING IMPORT HISTORY
# =========================================================

def get_marketing_import_history(db, limit=100):

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


# =========================================================
# MARKETING EXPENSE SUMMARY
# =========================================================

def get_marketing_expense_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_expenses
        FROM finance_integration_records
        WHERE department = ?
          AND source_type IN (
              'CAMPAIGN_EXPENSE',
              'ADVERTISING_EXPENSE',
              'PROMOTION_EXPENSE',
              'MARKETING_EVENT_EXPENSE',
              'MARKETING_ADMIN_EXPENSE'
          )
          AND status IN ('Approved', 'Posted')
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_expenses": str(
            _decimal(row["total_expenses"], "expense total")
        ),
        "currency": "ETB"
    }


# =========================================================
# MARKETING REVENUE SUMMARY
# =========================================================

def get_marketing_revenue_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_revenue
        FROM finance_integration_records
        WHERE department = ?
          AND source_type = 'SALES_REVENUE'
          AND status IN ('Approved', 'Posted')
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_revenue": str(
            _decimal(row["total_revenue"], "revenue total")
        ),
        "currency": "ETB"
    }
