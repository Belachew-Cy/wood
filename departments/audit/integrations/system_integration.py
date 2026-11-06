
"""
Dereje Wood & Kitchen Cabinet
Audit Management - System Integration

Provides read-only access to registered System Integration
and Directory Integration records.

This integration does not create connections, execute
synchronizations, change settings or modify integration logs.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# SYSTEM INTEGRATION AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

INTEGRATION_SOURCE_MODULE = "integration"

INTEGRATION_AUDIT_TABLES = {
    "integrations": "integrations",
    "connection_tests": "integration_connection_tests",
    "sync_logs": "integration_sync_logs",
    "errors": "integration_errors",
    "directory_connections": "directory_connections",
    "directory_sync_runs": "directory_sync_runs",
    "directory_sync_items": "directory_sync_items",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_integration_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered System Integration table."""

    if table_key not in INTEGRATION_AUDIT_TABLES:
        raise ValueError(
            "Unsupported System Integration audit data source."
        )

    return read_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# SYSTEM INTEGRATIONS
# -------------------------------------------------------

def get_integrations(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "integrations",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_integrations(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["integrations"],
        filters=filters,
    )


# -------------------------------------------------------
# CONNECTION TESTS
# -------------------------------------------------------

def get_connection_tests(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "connection_tests",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_connection_tests(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["connection_tests"],
        filters=filters,
    )


# -------------------------------------------------------
# SYNCHRONIZATION LOGS
# -------------------------------------------------------

def get_sync_logs(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "sync_logs",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_sync_logs(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["sync_logs"],
        filters=filters,
    )


# -------------------------------------------------------
# INTEGRATION ERRORS
# -------------------------------------------------------

def get_integration_errors(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "errors",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_integration_errors(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["errors"],
        filters=filters,
    )


# -------------------------------------------------------
# DIRECTORY CONNECTIONS
# -------------------------------------------------------

def get_directory_connections(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "directory_connections",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_directory_connections(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["directory_connections"],
        filters=filters,
    )


# -------------------------------------------------------
# DIRECTORY SYNCHRONIZATION RUNS
# -------------------------------------------------------

def get_directory_sync_runs(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "directory_sync_runs",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_directory_sync_runs(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["directory_sync_runs"],
        filters=filters,
    )


# -------------------------------------------------------
# DIRECTORY SYNCHRONIZATION ITEMS
# -------------------------------------------------------

def get_directory_sync_items(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_integration_table(
        "directory_sync_items",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_directory_sync_items(filters=None):
    return count_records(
        module_name=INTEGRATION_SOURCE_MODULE,
        table_name=INTEGRATION_AUDIT_TABLES["directory_sync_items"],
        filters=filters,
    )


# -------------------------------------------------------
# SYSTEM INTEGRATION AUDIT OVERVIEW
# -------------------------------------------------------

def get_integration_audit_overview():
    """Return record counts for registered Integration tables."""

    overview = {
        "module": INTEGRATION_SOURCE_MODULE,
        "department": "System Integration",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in INTEGRATION_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=INTEGRATION_SOURCE_MODULE,
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
                "error": "Unable to read registered Integration table",
            }

    return overview


# -------------------------------------------------------
# SYSTEM INTEGRATION CONNECTION STATUS
# -------------------------------------------------------

def get_integration_connection_status():
    """Check the existing System Integration database."""

    try:
        result = check_source_connection(
            INTEGRATION_SOURCE_MODULE
        )

        result["department"] = "System Integration"

        return result

    except Exception:
        return {
            "module": INTEGRATION_SOURCE_MODULE,
            "department": "System Integration",
            "connected": False,
            "read_only": True,
            "error": "System Integration database unavailable",
        }


# -------------------------------------------------------
# COMPLETE SYSTEM INTEGRATION AUDIT DATA
# -------------------------------------------------------

def get_system_integration_audit_data(limit=100):
    """
    Return a read-only System Integration audit snapshot.

    Each source table is queried independently.
    """

    return {
        "connection": get_integration_connection_status(),
        "overview": get_integration_audit_overview(),
        "integrations": get_integrations(limit=limit),
        "connection_tests": get_connection_tests(limit=limit),
        "sync_logs": get_sync_logs(limit=limit),
        "errors": get_integration_errors(limit=limit),
        "directory_connections": get_directory_connections(
            limit=limit
        ),
        "directory_sync_runs": get_directory_sync_runs(
            limit=limit
        ),
        "directory_sync_items": get_directory_sync_items(
            limit=limit
        ),
    }
