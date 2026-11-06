
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Performance Integration

Provides read-only access to the existing Performance
Management database for internal audit and compliance reviews.

This integration does not create, update or delete
performance records.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# PERFORMANCE AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

PERFORMANCE_SOURCE_MODULE = "performance"

PERFORMANCE_AUDIT_TABLES = {
    "reviews": "performance_reviews",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_performance_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Performance Management table."""

    if table_key not in PERFORMANCE_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Performance audit data source."
        )

    return read_records(
        module_name=PERFORMANCE_SOURCE_MODULE,
        table_name=PERFORMANCE_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# PERFORMANCE REVIEWS
# -------------------------------------------------------

def get_performance_reviews(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_performance_table(
        "reviews",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_performance_reviews(filters=None):
    return count_records(
        module_name=PERFORMANCE_SOURCE_MODULE,
        table_name=PERFORMANCE_AUDIT_TABLES["reviews"],
        filters=filters,
    )


# -------------------------------------------------------
# PERFORMANCE AUDIT OVERVIEW
# -------------------------------------------------------

def get_performance_audit_overview():
    """Return record counts for registered Performance tables."""

    overview = {
        "module": PERFORMANCE_SOURCE_MODULE,
        "department": "Performance Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in PERFORMANCE_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=PERFORMANCE_SOURCE_MODULE,
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
                "error": "Unable to read registered Performance table",
            }

    return overview


# -------------------------------------------------------
# PERFORMANCE CONNECTION STATUS
# -------------------------------------------------------

def get_performance_connection_status():
    """Check the existing Performance database connection."""

    try:
        result = check_source_connection(
            PERFORMANCE_SOURCE_MODULE
        )

        result["department"] = "Performance Management"

        return result

    except Exception:
        return {
            "module": PERFORMANCE_SOURCE_MODULE,
            "department": "Performance Management",
            "connected": False,
            "read_only": True,
            "error": "Performance database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE PERFORMANCE AUDIT DATA
# -------------------------------------------------------

def get_performance_audit_data(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """
    Return a read-only Performance Management audit snapshot.

    Filters and ordering are validated by the shared
    read-only adapter against the actual table schema.
    """

    return {
        "connection": get_performance_connection_status(),
        "overview": get_performance_audit_overview(),
        "reviews": get_performance_reviews(
            filters=filters,
            limit=limit,
            offset=offset,
            order_by=order_by,
            descending=descending,
        ),
    }
