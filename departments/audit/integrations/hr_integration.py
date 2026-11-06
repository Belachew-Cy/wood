
"""
Dereje Wood & Kitchen Cabinet
Audit Management - HR Integration

Provides read-only access to existing HR records.

All access goes through the shared Audit read-only adapter.
No HR records are created, modified or deleted.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# HR AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

HR_AUDIT_TABLES = {
    "employees": "employees",
    "attendance": "attendance",
    "leave_requests": "leave_requests",
    "employee_training": "employee_training",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_hr_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered HR table."""

    if table_key not in HR_AUDIT_TABLES:
        raise ValueError(
            "Unsupported HR audit data source."
        )

    return read_records(
        module_name="hr",
        table_name=HR_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# EMPLOYEE RECORDS
# -------------------------------------------------------

def get_hr_employees(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve employee records for audit review."""

    return _read_hr_table(
        "employees",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_hr_employees(filters=None):
    return count_records(
        module_name="hr",
        table_name=HR_AUDIT_TABLES["employees"],
        filters=filters,
    )


# -------------------------------------------------------
# ATTENDANCE RECORDS
# -------------------------------------------------------

def get_hr_attendance(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve attendance records for audit review."""

    return _read_hr_table(
        "attendance",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_hr_attendance(filters=None):
    return count_records(
        module_name="hr",
        table_name=HR_AUDIT_TABLES["attendance"],
        filters=filters,
    )


# -------------------------------------------------------
# LEAVE REQUESTS
# -------------------------------------------------------

def get_hr_leave_requests(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve leave requests for audit review."""

    return _read_hr_table(
        "leave_requests",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_hr_leave_requests(filters=None):
    return count_records(
        module_name="hr",
        table_name=HR_AUDIT_TABLES["leave_requests"],
        filters=filters,
    )


# -------------------------------------------------------
# EMPLOYEE TRAINING
# -------------------------------------------------------

def get_hr_training_records(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    """Retrieve employee training records for audit review."""

    return _read_hr_table(
        "employee_training",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_hr_training_records(filters=None):
    return count_records(
        module_name="hr",
        table_name=HR_AUDIT_TABLES["employee_training"],
        filters=filters,
    )


# -------------------------------------------------------
# HR AUDIT OVERVIEW
# -------------------------------------------------------

def get_hr_audit_overview():
    """
    Return record counts for registered HR tables.

    Each table is checked independently so a missing table
    does not prevent the remaining overview from loading.
    """

    overview = {
        "module": "hr",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in HR_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name="hr",
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
                "error": "Unable to read registered HR table",
            }

    return overview


# -------------------------------------------------------
# HR CONNECTION STATUS
# -------------------------------------------------------

def get_hr_connection_status():
    """Check the existing HR database connection."""

    try:
        return check_source_connection("hr")

    except Exception:
        return {
            "module": "hr",
            "connected": False,
            "read_only": True,
            "error": "HR database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE HR AUDIT DATA
# -------------------------------------------------------

def get_hr_audit_data(limit=100):
    """
    Return a read-only HR audit snapshot.

    No assumptions are made about shared column names
    between HR tables.
    """

    return {
        "connection": get_hr_connection_status(),
        "overview": get_hr_audit_overview(),
        "employees": get_hr_employees(limit=limit),
        "attendance": get_hr_attendance(limit=limit),
        "leave_requests": get_hr_leave_requests(limit=limit),
        "training": get_hr_training_records(limit=limit),
    }
