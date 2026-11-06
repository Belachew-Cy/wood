
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Payroll Integration

Provides read-only access to the existing Payroll database.

Security:
    - Uses the common Audit read-only adapter.
    - Does not modify payroll records.
    - Does not calculate or change employee salaries.
    - Does not create duplicate payroll tables.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# PAYROLL AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

PAYROLL_AUDIT_TABLES = {
    "payroll": "payroll",
    "payroll_records": "payroll_records",
    "payroll_payments": "payroll_payments",
    "salary_approvals": "payroll_salary_approvals",
    "statutory_audit": "payroll_statutory_audit",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_payroll_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Payroll table."""

    if table_key not in PAYROLL_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Payroll audit data source."
        )

    return read_records(
        module_name="payroll",
        table_name=PAYROLL_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# PAYROLL RECORDS
# -------------------------------------------------------

def get_payroll_records(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_payroll_table(
        "payroll_records",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_payroll_records(filters=None):
    return count_records(
        module_name="payroll",
        table_name=PAYROLL_AUDIT_TABLES["payroll_records"],
        filters=filters,
    )


# -------------------------------------------------------
# PAYROLL PAYMENTS
# -------------------------------------------------------

def get_payroll_payments(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_payroll_table(
        "payroll_payments",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_payroll_payments(filters=None):
    return count_records(
        module_name="payroll",
        table_name=PAYROLL_AUDIT_TABLES["payroll_payments"],
        filters=filters,
    )


# -------------------------------------------------------
# PAYROLL SALARY APPROVALS
# -------------------------------------------------------

def get_payroll_salary_approvals(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_payroll_table(
        "salary_approvals",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_payroll_salary_approvals(filters=None):
    return count_records(
        module_name="payroll",
        table_name=PAYROLL_AUDIT_TABLES["salary_approvals"],
        filters=filters,
    )


# -------------------------------------------------------
# STATUTORY PAYROLL AUDIT RECORDS
# -------------------------------------------------------

def get_payroll_statutory_records(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_payroll_table(
        "statutory_audit",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_payroll_statutory_records(filters=None):
    return count_records(
        module_name="payroll",
        table_name=PAYROLL_AUDIT_TABLES["statutory_audit"],
        filters=filters,
    )


# -------------------------------------------------------
# PAYROLL AUDIT OVERVIEW
# -------------------------------------------------------

def get_payroll_audit_overview():
    """Return counts for registered Payroll audit tables."""

    overview = {
        "module": "payroll",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in PAYROLL_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name="payroll",
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
                "error": "Unable to read registered Payroll table",
            }

    return overview


# -------------------------------------------------------
# PAYROLL CONNECTION STATUS
# -------------------------------------------------------

def get_payroll_connection_status():
    try:
        return check_source_connection("payroll")

    except Exception:
        return {
            "module": "payroll",
            "connected": False,
            "read_only": True,
            "error": "Payroll database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE PAYROLL AUDIT DATA
# -------------------------------------------------------

def get_payroll_audit_data(limit=100):
    """
    Return a read-only Payroll audit snapshot.

    The generic payroll table is included in the overview.
    Detailed records are retrieved from the specific
    registered payroll tables.
    """

    return {
        "connection": get_payroll_connection_status(),
        "overview": get_payroll_audit_overview(),
        "payroll_records": get_payroll_records(limit=limit),
        "payments": get_payroll_payments(limit=limit),
        "salary_approvals": get_payroll_salary_approvals(
            limit=limit
        ),
        "statutory_records": get_payroll_statutory_records(
            limit=limit
        ),
    }
