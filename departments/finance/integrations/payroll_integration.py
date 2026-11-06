
"""
Payroll - Finance Integration
Dereje Wood & Kitchen Cabinet Management System

Purpose:
- Receive approved payroll summaries.
- Import payroll liabilities and expenses.
- Prevent duplicate payroll imports.
- Maintain source references.
- Send payroll transactions for Finance review.

Does not calculate salaries or create payroll records.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime


DEPARTMENT_NAME = "Payroll"

SUPPORTED_CURRENCIES = {
    "ETB", "USD", "EUR", "GBP"
}

VALID_PAYROLL_TYPES = {
    "SALARY_EXPENSE",
    "OVERTIME_EXPENSE",
    "BONUS_EXPENSE",
    "PENSION_EXPENSE",
    "INCOME_TAX_PAYABLE",
    "PENSION_PAYABLE",
    "NET_SALARY_PAYABLE",
    "OTHER_PAYROLL_EXPENSE"
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


def _validate_payroll_transaction(transaction):
    if not isinstance(transaction, dict):
        raise ValueError("Invalid payroll transaction.")

    required = (
        "source_reference",
        "transaction_type",
        "description",
        "amount",
        "currency",
        "approval_status"
    )

    for field in required:
        if transaction.get(field) in (None, ""):
            raise ValueError(
                f"Missing payroll field: {field}"
            )

    reference = str(
        transaction["source_reference"]
    ).strip()

    if not reference:
        raise ValueError("Payroll reference is required.")

    transaction_type = str(
        transaction["transaction_type"]
    ).upper().strip()

    if transaction_type not in VALID_PAYROLL_TYPES:
        raise ValueError("Unsupported payroll transaction type.")

    if str(transaction["approval_status"]).lower().strip() != "approved":
        raise ValueError(
            "Only approved payroll transactions can be imported."
        )

    amount = _decimal(
        transaction["amount"],
        "payroll amount"
    )

    if amount <= 0:
        raise ValueError("Payroll amount must be positive.")

    currency = str(
        transaction["currency"]
    ).upper().strip()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported payroll currency.")

    exchange_rate = _decimal(
        transaction.get("exchange_rate", "1"),
        "exchange rate"
    )

    if exchange_rate <= 0:
        raise ValueError("Exchange rate must be positive.")

    description = str(
        transaction["description"]
    ).strip()

    if len(description) > 500:
        raise ValueError("Description is too long.")

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
# IMPORT ONE PAYROLL TRANSACTION
# =========================================================

def receive_payroll_transaction(db, transaction):
    """
    Import one approved payroll transaction.

    The imported record remains in Received status.
    Finance must review and approve it separately.
    """

    data = _validate_payroll_transaction(transaction)

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
                "message": "Payroll transaction already imported."
            }

        record_id = cursor.lastrowid
        db.commit()

        return {
            "success": True,
            "duplicate": False,
            "record_id": record_id,
            "status": "Received",
            "message": "Payroll transaction imported successfully."
        }

    except Exception:
        db.rollback()
        raise


# =========================================================
# IMPORT PAYROLL SUMMARY
# =========================================================

def import_payroll_summary(db, payroll_summary):
    """
    Import multiple approved payroll financial transactions.

    Example:
    [
        {
            "source_reference": "PAY-2026-001-SALARY",
            "transaction_type": "SALARY_EXPENSE",
            "description": "Monthly salary expense",
            "amount": "500000",
            "currency": "ETB",
            "exchange_rate": "1",
            "approval_status": "Approved"
        },
        {
            "source_reference": "PAY-2026-001-TAX",
            "transaction_type": "INCOME_TAX_PAYABLE",
            "description": "Monthly income tax payable",
            "amount": "65000",
            "currency": "ETB",
            "exchange_rate": "1",
            "approval_status": "Approved"
        }
    ]
    """

    if not isinstance(payroll_summary, (list, tuple)):
        raise ValueError("Payroll summary must be a list.")

    results = []

    for transaction in payroll_summary:
        try:
            result = receive_payroll_transaction(
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
        "total": len(payroll_summary),
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
# PAYROLL INTEGRATION STATUS
# =========================================================

def get_payroll_integration_status(db):
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
# PAYROLL IMPORT HISTORY
# =========================================================

def get_payroll_import_history(db, limit=100):
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
