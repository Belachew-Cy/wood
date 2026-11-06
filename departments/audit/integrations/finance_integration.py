
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Finance Integration

Provides read-only access to Finance records.

All database access goes through:
    departments.audit.integrations.read_only

No Finance transactions are created, updated or deleted.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# FINANCE AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

FINANCE_AUDIT_TABLES = {
    "transactions": "finance_transactions",
    "invoices": "finance_invoices",
    "payments": "finance_payments",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_finance_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True
):
    """
    Read a registered Finance table.

    Table ownership is checked by the common read-only
    integration adapter.
    """

    if table_key not in FINANCE_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Finance audit data source."
        )

    table_name = FINANCE_AUDIT_TABLES[table_key]

    return read_records(
        module_name="finance",
        table_name=table_name,
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# FINANCE TRANSACTIONS
# -------------------------------------------------------

def get_finance_transactions(
    filters=None,
    limit=100,
    offset=0,
    order_by=None
):
    """
    Retrieve Finance transactions for audit review.
    """

    return _read_finance_table(
        "transactions",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_finance_transactions(filters=None):

    return count_records(
        module_name="finance",
        table_name=FINANCE_AUDIT_TABLES["transactions"],
        filters=filters,
    )


# -------------------------------------------------------
# FINANCE INVOICES
# -------------------------------------------------------

def get_finance_invoices(
    filters=None,
    limit=100,
    offset=0,
    order_by=None
):
    """
    Retrieve invoices for audit review.
    """

    return _read_finance_table(
        "invoices",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_finance_invoices(filters=None):

    return count_records(
        module_name="finance",
        table_name=FINANCE_AUDIT_TABLES["invoices"],
        filters=filters,
    )


# -------------------------------------------------------
# FINANCE PAYMENTS
# -------------------------------------------------------

def get_finance_payments(
    filters=None,
    limit=100,
    offset=0,
    order_by=None
):
    """
    Retrieve payment records for audit review.
    """

    return _read_finance_table(
        "payments",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_finance_payments(filters=None):

    return count_records(
        module_name="finance",
        table_name=FINANCE_AUDIT_TABLES["payments"],
        filters=filters,
    )


# -------------------------------------------------------
# FINANCE AUDIT OVERVIEW
# -------------------------------------------------------

def get_finance_audit_overview():
    """
    Return record counts for registered Finance tables.

    A missing or unregistered table is reported as an error.
    """

    overview = {
        "module": "finance",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in FINANCE_AUDIT_TABLES.items():

        try:

            total = count_records(
                module_name="finance",
                table_name=table_name,
            )

            overview["tables"][key] = {
                "table": table_name,
                "record_count": total,
                "error": None,
            }

            overview["total_records"] += total

        except Exception:

            overview["tables"][key] = {
                "table": table_name,
                "record_count": None,
                "error": "Unable to read registered Finance table",
            }

    return overview


# -------------------------------------------------------
# FINANCE CONNECTION STATUS
# -------------------------------------------------------

def get_finance_connection_status():
    """
    Check the existing Finance database connection.
    """

    try:

        return check_source_connection("finance")

    except Exception:

        return {
            "module": "finance",
            "connected": False,
            "read_only": True,
            "error": "Finance database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE FINANCE AUDIT DATA
# -------------------------------------------------------

def get_finance_audit_data(
    filters=None,
    limit=100
):
    """
    Retrieve a combined Finance audit snapshot.

    Each table is read independently. This avoids joining
    records across databases or assuming common column names.
    """

    return {
        "connection": get_finance_connection_status(),
        "overview": get_finance_audit_overview(),
        "transactions": get_finance_transactions(
            filters=filters,
            limit=limit,
        ),
        "invoices": get_finance_invoices(
            limit=limit,
        ),
        "payments": get_finance_payments(
            limit=limit,
        ),
    }
