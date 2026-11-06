
"""
Inventory & Warehouse - Finance Integration
Dereje Wood & Kitchen Cabinet Management System

Purpose:
- Receive approved inventory purchases.
- Receive goods receipt references.
- Receive material consumption and issue summaries.
- Receive stock adjustment references.
- Receive inventory valuation summaries.
- Prevent duplicate transaction imports.

Uses existing Inventory & Warehouse records.
Does not maintain a second stock database.
Does not automatically post accounting journals.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime


DEPARTMENT_NAME = "Inventory"

SUPPORTED_CURRENCIES = {
    "ETB", "USD", "EUR", "GBP"
}

VALID_TRANSACTION_TYPES = {
    "INVENTORY_PURCHASE",
    "GOODS_RECEIPT",
    "MATERIAL_ISSUE",
    "MATERIAL_CONSUMPTION",
    "STOCK_ADJUSTMENT",
    "INVENTORY_VALUATION",
    "INVENTORY_RETURN"
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


def validate_inventory_transaction(transaction):

    if not isinstance(transaction, dict):
        raise ValueError("Invalid inventory transaction.")

    required_fields = [
        "source_reference",
        "item_reference",
        "transaction_type",
        "description",
        "amount",
        "currency",
        "approval_status"
    ]

    for field in required_fields:
        if transaction.get(field) in (None, ""):
            raise ValueError(
                f"Missing inventory field: {field}"
            )

    source_reference = str(
        transaction["source_reference"]
    ).strip()

    item_reference = str(
        transaction["item_reference"]
    ).strip()

    if not source_reference or not item_reference:
        raise ValueError(
            "Inventory item and source references are required."
        )

    transaction_type = str(
        transaction["transaction_type"]
    ).upper().strip()

    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise ValueError(
            "Unsupported inventory transaction type."
        )

    if str(transaction["approval_status"]).lower().strip() != "approved":
        raise ValueError(
            "Only approved inventory transactions can be imported."
        )

    amount = _decimal(
        transaction["amount"],
        "inventory amount"
    )

    if amount < 0:
        raise ValueError(
            "Inventory amount cannot be negative."
        )

    if amount == 0 and transaction_type != "GOODS_RECEIPT":
        raise ValueError(
            "Inventory transaction amount must be greater than zero."
        )

    currency = str(
        transaction["currency"]
    ).upper().strip()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            "Unsupported inventory currency."
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
            "Inventory description is too long."
        )

    return {
        "department": DEPARTMENT_NAME,
        "source_type": transaction_type,
        "source_reference": source_reference,
        "item_reference": item_reference,
        "description": description,
        "amount": amount,
        "currency": currency,
        "exchange_rate": exchange_rate,
        "base_amount": amount * exchange_rate
    }


# =========================================================
# RECEIVE INVENTORY TRANSACTION
# =========================================================

def receive_inventory_transaction(db, transaction):
    """
    Receive one approved inventory transaction.

    The source inventory record remains in the Inventory module.
    Finance receives its financial reference and amount.
    """

    data = validate_inventory_transaction(transaction)

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
                    f"Inventory item {data['item_reference']}: "
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
                "message": "Inventory transaction already imported."
            }

        record_id = cursor.lastrowid

        db.commit()

        return {
            "success": True,
            "duplicate": False,
            "record_id": record_id,
            "status": "Received",
            "message": "Inventory transaction received successfully."
        }

    except Exception:
        db.rollback()
        raise


# =========================================================
# IMPORT MULTIPLE INVENTORY TRANSACTIONS
# =========================================================

def import_inventory_transactions(db, transactions):

    if not isinstance(transactions, (list, tuple)):
        raise ValueError("Transactions must be a list.")

    results = []

    for transaction in transactions:
        try:
            result = receive_inventory_transaction(
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
# INVENTORY INTEGRATION STATUS
# =========================================================

def get_inventory_integration_status(db):

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
# INVENTORY IMPORT HISTORY
# =========================================================

def get_inventory_import_history(db, limit=100):

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
# INVENTORY PURCHASE SUMMARY
# =========================================================

def get_inventory_purchase_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_purchases
        FROM finance_integration_records
        WHERE department = ?
          AND source_type IN (
              'INVENTORY_PURCHASE',
              'GOODS_RECEIPT'
          )
          AND status = 'Posted'
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_purchases": str(
            _decimal(
                row["total_purchases"],
                "inventory purchase total"
            )
        ),
        "currency": "ETB"
    }


# =========================================================
# MATERIAL CONSUMPTION SUMMARY
# =========================================================

def get_material_consumption_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_consumption
        FROM finance_integration_records
        WHERE department = ?
          AND source_type IN (
              'MATERIAL_ISSUE',
              'MATERIAL_CONSUMPTION'
          )
          AND status = 'Posted'
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_consumption": str(
            _decimal(
                row["total_consumption"],
                "material consumption total"
            )
        ),
        "currency": "ETB"
    }


# =========================================================
# INVENTORY VALUATION SUMMARY
# =========================================================

def get_inventory_valuation_summary(db):

    row = db.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(base_amount), 0) AS total_valuation
        FROM finance_integration_records
        WHERE department = ?
          AND source_type = 'INVENTORY_VALUATION'
          AND status = 'Posted'
        """,
        (DEPARTMENT_NAME,)
    ).fetchone()

    return {
        "transaction_count": row["transaction_count"],
        "total_valuation": str(
            _decimal(
                row["total_valuation"],
                "inventory valuation total"
            )
        ),
        "currency": "ETB"
    }
