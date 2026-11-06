
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Inventory Integration

Provides read-only access to the existing Stock database.

Audit access:
    - Inventory items
    - Stock movements
    - Inventory stocktakes
    - Inventory stocktake lines

This integration does not modify inventory quantities,
stock movements, warehouse records or stocktake results.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# INVENTORY AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

INVENTORY_SOURCE_MODULE = "stock"

INVENTORY_AUDIT_TABLES = {
    "items": "inventory_items",
    "stock_movements": "stock_movements",
    "stocktakes": "inventory_stocktakes",
    "stocktake_lines": "inventory_stocktake_lines",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_inventory_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Inventory table."""

    if table_key not in INVENTORY_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Inventory audit data source."
        )

    return read_records(
        module_name=INVENTORY_SOURCE_MODULE,
        table_name=INVENTORY_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# INVENTORY ITEMS
# -------------------------------------------------------

def get_inventory_items(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve inventory item records for audit review."""

    return _read_inventory_table(
        "items",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_inventory_items(filters=None):
    return count_records(
        module_name=INVENTORY_SOURCE_MODULE,
        table_name=INVENTORY_AUDIT_TABLES["items"],
        filters=filters,
    )


# -------------------------------------------------------
# STOCK MOVEMENTS
# -------------------------------------------------------

def get_stock_movements(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve stock movement history for audit review."""

    return _read_inventory_table(
        "stock_movements",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_stock_movements(filters=None):
    return count_records(
        module_name=INVENTORY_SOURCE_MODULE,
        table_name=INVENTORY_AUDIT_TABLES["stock_movements"],
        filters=filters,
    )


# -------------------------------------------------------
# INVENTORY STOCKTAKES
# -------------------------------------------------------

def get_inventory_stocktakes(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve stocktake records for audit review."""

    return _read_inventory_table(
        "stocktakes",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_inventory_stocktakes(filters=None):
    return count_records(
        module_name=INVENTORY_SOURCE_MODULE,
        table_name=INVENTORY_AUDIT_TABLES["stocktakes"],
        filters=filters,
    )


# -------------------------------------------------------
# STOCKTAKE LINES
# -------------------------------------------------------

def get_inventory_stocktake_lines(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve individual stocktake lines for audit review."""

    return _read_inventory_table(
        "stocktake_lines",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_inventory_stocktake_lines(filters=None):
    return count_records(
        module_name=INVENTORY_SOURCE_MODULE,
        table_name=INVENTORY_AUDIT_TABLES["stocktake_lines"],
        filters=filters,
    )


# -------------------------------------------------------
# INVENTORY AUDIT OVERVIEW
# -------------------------------------------------------

def get_inventory_audit_overview():
    """Return counts for registered Inventory tables."""

    overview = {
        "module": "stock",
        "department": "Inventory and Warehouse",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in INVENTORY_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=INVENTORY_SOURCE_MODULE,
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
                "error": "Unable to read registered Inventory table",
            }

    return overview


# -------------------------------------------------------
# INVENTORY CONNECTION STATUS
# -------------------------------------------------------

def get_inventory_connection_status():
    """Check the existing Stock database connection."""

    try:
        result = check_source_connection(
            INVENTORY_SOURCE_MODULE
        )

        result["department"] = "Inventory and Warehouse"

        return result

    except Exception:
        return {
            "module": INVENTORY_SOURCE_MODULE,
            "department": "Inventory and Warehouse",
            "connected": False,
            "read_only": True,
            "error": "Inventory database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE INVENTORY AUDIT DATA
# -------------------------------------------------------

def get_inventory_audit_data(limit=100):
    """
    Return a read-only Inventory audit snapshot.

    Each table is retrieved independently.
    No stock quantities are recalculated or changed.
    """

    return {
        "connection": get_inventory_connection_status(),
        "overview": get_inventory_audit_overview(),
        "items": get_inventory_items(limit=limit),
        "stock_movements": get_stock_movements(
            limit=limit
        ),
        "stocktakes": get_inventory_stocktakes(
            limit=limit
        ),
        "stocktake_lines": get_inventory_stocktake_lines(
            limit=limit
        ),
    }
