
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Procurement Integration

Provides read-only access to existing Procurement records.

Current source registry:
    Procurement tables are registered under "finance".

No purchase requests, purchase orders, supplier records
or delivery records are modified by this integration.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# PROCUREMENT AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

PROCUREMENT_SOURCE_MODULE = "finance"

PROCUREMENT_AUDIT_TABLES = {
    "suppliers": "procurement_suppliers",
    "purchase_requests": "procurement_requests",
    "purchase_orders": "procurement_orders",
    "request_items": "procurement_request_items",
    "deliveries": "procurement_deliveries",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_procurement_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Procurement table."""

    if table_key not in PROCUREMENT_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Procurement audit data source."
        )

    return read_records(
        module_name=PROCUREMENT_SOURCE_MODULE,
        table_name=PROCUREMENT_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# SUPPLIERS
# -------------------------------------------------------

def get_procurement_suppliers(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_procurement_table(
        "suppliers",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_procurement_suppliers(filters=None):
    return count_records(
        module_name=PROCUREMENT_SOURCE_MODULE,
        table_name=PROCUREMENT_AUDIT_TABLES["suppliers"],
        filters=filters,
    )


# -------------------------------------------------------
# PURCHASE REQUESTS
# -------------------------------------------------------

def get_purchase_requests(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_procurement_table(
        "purchase_requests",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_purchase_requests(filters=None):
    return count_records(
        module_name=PROCUREMENT_SOURCE_MODULE,
        table_name=PROCUREMENT_AUDIT_TABLES["purchase_requests"],
        filters=filters,
    )


# -------------------------------------------------------
# PURCHASE ORDERS
# -------------------------------------------------------

def get_purchase_orders(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_procurement_table(
        "purchase_orders",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_purchase_orders(filters=None):
    return count_records(
        module_name=PROCUREMENT_SOURCE_MODULE,
        table_name=PROCUREMENT_AUDIT_TABLES["purchase_orders"],
        filters=filters,
    )


# -------------------------------------------------------
# PURCHASE REQUEST ITEMS
# -------------------------------------------------------

def get_purchase_request_items(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_procurement_table(
        "request_items",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_purchase_request_items(filters=None):
    return count_records(
        module_name=PROCUREMENT_SOURCE_MODULE,
        table_name=PROCUREMENT_AUDIT_TABLES["request_items"],
        filters=filters,
    )


# -------------------------------------------------------
# DELIVERIES
# -------------------------------------------------------

def get_procurement_deliveries(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_procurement_table(
        "deliveries",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_procurement_deliveries(filters=None):
    return count_records(
        module_name=PROCUREMENT_SOURCE_MODULE,
        table_name=PROCUREMENT_AUDIT_TABLES["deliveries"],
        filters=filters,
    )


# -------------------------------------------------------
# PROCUREMENT AUDIT OVERVIEW
# -------------------------------------------------------

def get_procurement_audit_overview():
    """Return counts for all registered Procurement tables."""

    overview = {
        "module": "procurement",
        "database_module": PROCUREMENT_SOURCE_MODULE,
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in PROCUREMENT_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=PROCUREMENT_SOURCE_MODULE,
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
                "error": "Unable to read registered Procurement table",
            }

    return overview


# -------------------------------------------------------
# PROCUREMENT CONNECTION STATUS
# -------------------------------------------------------

def get_procurement_connection_status():
    """
    Check the database currently registered as the source
    of Procurement audit records.
    """

    try:
        result = check_source_connection(
            PROCUREMENT_SOURCE_MODULE
        )

        result["department"] = "Procurement"

        return result

    except Exception:
        return {
            "module": "procurement",
            "database_module": PROCUREMENT_SOURCE_MODULE,
            "department": "Procurement",
            "connected": False,
            "read_only": True,
            "error": "Procurement database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE PROCUREMENT AUDIT DATA
# -------------------------------------------------------

def get_procurement_audit_data(limit=100):
    """
    Return a read-only Procurement audit snapshot.

    Tables are retrieved independently. No cross-table
    relationships or column names are assumed.
    """

    return {
        "connection": get_procurement_connection_status(),
        "overview": get_procurement_audit_overview(),
        "suppliers": get_procurement_suppliers(limit=limit),
        "purchase_requests": get_purchase_requests(
            limit=limit
        ),
        "purchase_orders": get_purchase_orders(
            limit=limit
        ),
        "request_items": get_purchase_request_items(
            limit=limit
        ),
        "deliveries": get_procurement_deliveries(
            limit=limit
        ),
    }
