
"""
Asset Management - Finance Integration
Dereje Wood & Kitchen Cabinet Management System

Purpose:
- Import approved asset acquisitions.
- Import approved asset disposals.
- Import depreciation summaries.
- Import approved asset maintenance expenses.
- Track asset transfer references.
- Prevent duplicate imports.

Uses existing Asset Management records.
Does not create duplicate asset records.
Does not automatically post accounting journals.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime


DEPARTMENT_NAME = "Asset"

SUPPORTED_CURRENCIES = {
    "ETB", "USD", "EUR", "GBP"
}

VALID_TRANSACTION_TYPES = {
    "ASSET_ACQUISITION",
    "ASSET_DISPOSAL",
    "ASSET_DEPRECIATION",
    "ASSET_MAINTENANCE",
    "ASSET_REPAIR",
    "ASSET_TRANSFER"
}


# =========================================================
# VALIDATION HELPERS
# =========================================================

def _decimal(value, field_name):
    try:
        result = Decimal(str(value))

        if not result.is_finite():
            raise ValueError()

        return result

    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {field_name}.")


def validate_asset_transaction(transaction):

    if not isinstance(transaction, dict):
        raise ValueError("Invalid asset transaction.")

    required_fields = [
        "source_reference",
        "asset_reference",
        "transaction_type",
        "description",
        "amount",
        "currency",
        "approval_status"
    ]

    for field in required_fields:
        if transaction.get(field) in (None, ""):
            raise ValueError(
                f"Missing asset field: {field}"
            )

    source_reference = str(
        transaction["source_reference"]
    ).strip()

    asset_reference = str(
        transaction["asset_reference"]
    ).strip()

    if not source_reference or not asset_reference:
        raise ValueError(
            "Asset and source references are required."
        )

    transaction_type = str(
        transaction["transaction_type"]
    ).upper().strip()

    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise ValueError(
            "Unsupported asset transaction type."
        )

    if str(transaction["approval_status"]).lower().strip() != "approved":
        raise ValueError(
            "Only approved asset transactions can be imported."
        )

    amount = _decimal(
        transaction["amount"],
        "asset amount"
    )

    if amount < 0:
        raise ValueError(
            "Asset amount cannot be negative."
        )

    if amount == 0 and transaction_type != "ASSET_TRANSFER":
        raise ValueError(
            "Asset transaction amount must be greater than zero."
        )

    currency = str(
        transaction["currency"]
    ).upper().strip()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            "Unsupported asset currency."
        )

    exchange_rate = _decimal(
        transaction.get("exchange_rate", "1"),
        "exchange rate"
    )

    if exchange_rate <= 0:
        raise ValueError(
            "Exchange rate must be positive."
        )

    description = str(
        transaction["description"]
    ).strip()

    if len(description) > 500:
        raise ValueError(
            "Asset description is too long."
        )

    return {
        "department": DEPARTMENT_NAME,
        "source_type": transaction_type,
        "source_reference": source_reference,
        "asset_reference": asset_reference,
        "description": description,
        "amount": amount,
        "currency": currency,
        "exchange_rate": exchange_rate,
        "base_amount": amount * exchange_rate
    }


# =========================================================
# RECEIVE ASSET TRANSACTION
# =========================================================

def receive_asset_transaction(db, transaction):
    """
    Receive one approved asset transaction.

    Finance receives a source reference for review.
    """

    data = validate_asset_transaction(transaction)

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
                (
                    f"Asset {data['asset_reference']}: "
                    f"{data['description']}"
                ),
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
                "message": "Asset transaction already imported."
            }

        record_id = cursor.lastrowid

        db.commit()

        return {
            "success": True,
            "duplicate": False,
            "record_id": record_id,
            "status": "Received",
            "message": "Asset transaction received successfully."
        }

    except Exception:
        db.rollback()
        raise


# =========================================================
# IMPORT MULTIPLE ASSET TRANSACTIONS
# =========================================================

def import_asset_transactions(db, transactions):

    if not isinstance(transactions, (list, tuple)):
        raise ValueError("Transactions must be a list.")

    results = []

    for transaction in transactions:
        try:
            result = receive_asset_transaction(
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
# ASSET INTEGRATION STATUS
# =========================================================

def get_asset_integration_status(db):

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
# ASSET IMPORT HISTORY
# =========================================================

def get_asset_import_history(db, limit=100):

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
# ASSET ACQUISITION SUMMARY
# =========================================================

def get_asset_acquisition_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_acquisitions
        FROM finance_integration_records
        WHERE department = ?
          AND source_type = 'ASSET_ACQUISITION'
          AND status IN ('Approved', 'Posted')
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_acquisitions": str(
            _decimal(
                row["total_acquisitions"],
                "asset acquisition total"
            )
        ),
        "currency": "ETB"
    }


# =========================================================
# ASSET DEPRECIATION SUMMARY
# =========================================================

def get_asset_depreciation_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_depreciation
        FROM finance_integration_records
        WHERE department = ?
          AND source_type = 'ASSET_DEPRECIATION'
          AND status IN ('Approved', 'Posted')
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_depreciation": str(
            _decimal(
                row["total_depreciation"],
                "asset depreciation total"
            )
        ),
        "currency": "ETB"
    }


# =========================================================
# ASSET DISPOSAL SUMMARY
# =========================================================

def get_asset_disposal_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_disposals
        FROM finance_integration_records
        WHERE department = ?
          AND source_type = 'ASSET_DISPOSAL'
          AND status IN ('Approved', 'Posted')
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_disposals": str(
            _decimal(
                row["total_disposals"],
                "asset disposal total"
            )
        ),
        "currency": "ETB"
    }
