
"""
Dereje Wood & Kitchen Cabinet
Audit Management - User Management Integration

Provides read-only access to registered user account,
role and permission records.

This integration does not create users, assign roles,
change permissions or modify account status.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# USER AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

USER_SOURCE_MODULE = "user"

USER_AUDIT_TABLES = {
    "users": "users",
    "roles": "user_roles",
    "permissions": "user_permissions",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_user_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered User Management table."""

    if table_key not in USER_AUDIT_TABLES:
        raise ValueError(
            "Unsupported User Management audit data source."
        )

    return read_records(
        module_name=USER_SOURCE_MODULE,
        table_name=USER_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# USER ACCOUNTS
# -------------------------------------------------------

def get_user_accounts(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_user_table(
        "users",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_user_accounts(filters=None):
    return count_records(
        module_name=USER_SOURCE_MODULE,
        table_name=USER_AUDIT_TABLES["users"],
        filters=filters,
    )


# -------------------------------------------------------
# USER ROLES
# -------------------------------------------------------

def get_user_roles(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_user_table(
        "roles",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_user_roles(filters=None):
    return count_records(
        module_name=USER_SOURCE_MODULE,
        table_name=USER_AUDIT_TABLES["roles"],
        filters=filters,
    )


# -------------------------------------------------------
# USER PERMISSIONS
# -------------------------------------------------------

def get_user_permissions(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_user_table(
        "permissions",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_user_permissions(filters=None):
    return count_records(
        module_name=USER_SOURCE_MODULE,
        table_name=USER_AUDIT_TABLES["permissions"],
        filters=filters,
    )


# -------------------------------------------------------
# USER MANAGEMENT AUDIT OVERVIEW
# -------------------------------------------------------

def get_user_audit_overview():
    """Return record counts for registered User tables."""

    overview = {
        "module": USER_SOURCE_MODULE,
        "department": "User Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in USER_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=USER_SOURCE_MODULE,
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
                "error": "Unable to read registered User table",
            }

    return overview


# -------------------------------------------------------
# USER DATABASE CONNECTION STATUS
# -------------------------------------------------------

def get_user_connection_status():
    """Check the existing User database connection."""

    try:
        result = check_source_connection(
            USER_SOURCE_MODULE
        )

        result["department"] = "User Management"

        return result

    except Exception:
        return {
            "module": USER_SOURCE_MODULE,
            "department": "User Management",
            "connected": False,
            "read_only": True,
            "error": "User database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE USER MANAGEMENT AUDIT DATA
# -------------------------------------------------------

def get_user_audit_data(limit=100):
    """
    Return a read-only User Management audit snapshot.

    Do not expose password hashes, password reset tokens,
    session tokens or other authentication secrets to audit
    dashboards or general report exports.
    """

    return {
        "connection": get_user_connection_status(),
        "overview": get_user_audit_overview(),
        "users": get_user_accounts(limit=limit),
        "roles": get_user_roles(limit=limit),
        "permissions": get_user_permissions(limit=limit),
    }
