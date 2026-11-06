
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Asset Integration

Provides read-only access to the existing Asset database.

Audit access:
    - Asset categories
    - Asset register
    - Asset allocations

This integration does not create, update, allocate,
transfer, dispose of or delete assets.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# ASSET AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

ASSET_SOURCE_MODULE = "asset"

ASSET_AUDIT_TABLES = {
    "categories": "asset_categories",
    "assets": "assets",
    "allocations": "asset_allocations",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_asset_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Asset table."""

    if table_key not in ASSET_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Asset audit data source."
        )

    return read_records(
        module_name=ASSET_SOURCE_MODULE,
        table_name=ASSET_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# ASSET CATEGORIES
# -------------------------------------------------------

def get_asset_categories(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve asset categories for audit review."""

    return _read_asset_table(
        "categories",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_asset_categories(filters=None):
    return count_records(
        module_name=ASSET_SOURCE_MODULE,
        table_name=ASSET_AUDIT_TABLES["categories"],
        filters=filters,
    )


# -------------------------------------------------------
# ASSET REGISTER
# -------------------------------------------------------

def get_assets(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve registered assets for audit review."""

    return _read_asset_table(
        "assets",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_assets(filters=None):
    return count_records(
        module_name=ASSET_SOURCE_MODULE,
        table_name=ASSET_AUDIT_TABLES["assets"],
        filters=filters,
    )


# -------------------------------------------------------
# ASSET ALLOCATIONS
# -------------------------------------------------------

def get_asset_allocations(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve asset allocation records for audit review."""

    return _read_asset_table(
        "allocations",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_asset_allocations(filters=None):
    return count_records(
        module_name=ASSET_SOURCE_MODULE,
        table_name=ASSET_AUDIT_TABLES["allocations"],
        filters=filters,
    )


# -------------------------------------------------------
# ASSET AUDIT OVERVIEW
# -------------------------------------------------------

def get_asset_audit_overview():
    """Return counts for registered Asset tables."""

    overview = {
        "module": ASSET_SOURCE_MODULE,
        "department": "Asset Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in ASSET_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=ASSET_SOURCE_MODULE,
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
                "error": "Unable to read registered Asset table",
            }

    return overview


# -------------------------------------------------------
# ASSET CONNECTION STATUS
# -------------------------------------------------------

def get_asset_connection_status():
    """Check the existing Asset database connection."""

    try:
        result = check_source_connection(
            ASSET_SOURCE_MODULE
        )

        result["department"] = "Asset Management"

        return result

    except Exception:
        return {
            "module": ASSET_SOURCE_MODULE,
            "department": "Asset Management",
            "connected": False,
            "read_only": True,
            "error": "Asset database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE ASSET AUDIT DATA
# -------------------------------------------------------

def get_asset_audit_data(limit=100):
    """
    Return a read-only Asset Management audit snapshot.

    Records are retrieved independently without assuming
    relationships or column names between the tables.
    """

    return {
        "connection": get_asset_connection_status(),
        "overview": get_asset_audit_overview(),
        "categories": get_asset_categories(limit=limit),
        "assets": get_assets(limit=limit),
        "allocations": get_asset_allocations(limit=limit),
    }
