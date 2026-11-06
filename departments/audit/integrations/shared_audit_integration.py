
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Shared Database Integration

Provides read-only access to existing shared audit records.

Uses the existing Shared Database tables:
    - audit_logs
    - audit_activity
    - audit_reviews
    - audit_findings

This integration does not create duplicate audit tables
or modify existing audit records.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# SHARED AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

SHARED_AUDIT_SOURCE_MODULE = "shared"

SHARED_AUDIT_TABLES = {
    "audit_logs": "audit_logs",
    "audit_activity": "audit_activity",
    "audit_reviews": "audit_reviews",
    "audit_findings": "audit_findings",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_shared_audit_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Shared Database audit table."""

    if table_key not in SHARED_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Shared Audit data source."
        )

    return read_records(
        module_name=SHARED_AUDIT_SOURCE_MODULE,
        table_name=SHARED_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# AUDIT LOGS
# -------------------------------------------------------

def get_existing_audit_logs(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_shared_audit_table(
        "audit_logs",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_existing_audit_logs(filters=None):
    return count_records(
        module_name=SHARED_AUDIT_SOURCE_MODULE,
        table_name=SHARED_AUDIT_TABLES["audit_logs"],
        filters=filters,
    )


# -------------------------------------------------------
# AUDIT ACTIVITY
# -------------------------------------------------------

def get_existing_audit_activity(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_shared_audit_table(
        "audit_activity",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_existing_audit_activity(filters=None):
    return count_records(
        module_name=SHARED_AUDIT_SOURCE_MODULE,
        table_name=SHARED_AUDIT_TABLES["audit_activity"],
        filters=filters,
    )


# -------------------------------------------------------
# AUDIT REVIEWS
# -------------------------------------------------------

def get_existing_audit_reviews(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_shared_audit_table(
        "audit_reviews",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_existing_audit_reviews(filters=None):
    return count_records(
        module_name=SHARED_AUDIT_SOURCE_MODULE,
        table_name=SHARED_AUDIT_TABLES["audit_reviews"],
        filters=filters,
    )


# -------------------------------------------------------
# AUDIT FINDINGS
# -------------------------------------------------------

def get_existing_audit_findings(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_shared_audit_table(
        "audit_findings",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_existing_audit_findings(filters=None):
    return count_records(
        module_name=SHARED_AUDIT_SOURCE_MODULE,
        table_name=SHARED_AUDIT_TABLES["audit_findings"],
        filters=filters,
    )


# -------------------------------------------------------
# SHARED AUDIT OVERVIEW
# -------------------------------------------------------

def get_shared_audit_overview():
    """Return record counts for existing Shared audit tables."""

    overview = {
        "module": SHARED_AUDIT_SOURCE_MODULE,
        "department": "Shared Audit Records",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in SHARED_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=SHARED_AUDIT_SOURCE_MODULE,
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
                "error": "Unable to read registered Shared audit table",
            }

    return overview


# -------------------------------------------------------
# SHARED DATABASE CONNECTION STATUS
# -------------------------------------------------------

def get_shared_audit_connection_status():
    """Check the existing Shared Database connection."""

    try:
        result = check_source_connection(
            SHARED_AUDIT_SOURCE_MODULE
        )

        result["department"] = "Shared Audit Records"

        return result

    except Exception:
        return {
            "module": SHARED_AUDIT_SOURCE_MODULE,
            "department": "Shared Audit Records",
            "connected": False,
            "read_only": True,
            "error": "Shared Database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE SHARED AUDIT SNAPSHOT
# -------------------------------------------------------

def get_shared_audit_data(limit=100):
    """
    Return a read-only snapshot of existing shared audit records.

    This function does not duplicate or modify audit records.
    """

    return {
        "connection": get_shared_audit_connection_status(),
        "overview": get_shared_audit_overview(),
        "audit_logs": get_existing_audit_logs(limit=limit),
        "audit_activity": get_existing_audit_activity(
            limit=limit
        ),
        "audit_reviews": get_existing_audit_reviews(
            limit=limit
        ),
        "audit_findings": get_existing_audit_findings(
            limit=limit
        ),
    }
