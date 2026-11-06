
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Budget Integration

Provides read-only access to the existing Budget database.

No budget allocations, approvals, commitments or
expenditures are created or modified by this integration.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# BUDGET AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

BUDGET_SOURCE_MODULE = "budget"

BUDGET_AUDIT_TABLES = {
    "plans": "budget_plans",
    "allocations": "budget_allocations",
    "requests": "budget_requests",
    "approvals": "budget_approvals",
    "commitments": "budget_commitments",
    "expenditures": "budget_expenditures",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_budget_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Budget table."""

    if table_key not in BUDGET_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Budget audit data source."
        )

    return read_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# BUDGET PLANS
# -------------------------------------------------------

def get_budget_plans(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_budget_table(
        "plans",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_budget_plans(filters=None):
    return count_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES["plans"],
        filters=filters,
    )


# -------------------------------------------------------
# BUDGET ALLOCATIONS
# -------------------------------------------------------

def get_budget_allocations(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_budget_table(
        "allocations",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_budget_allocations(filters=None):
    return count_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES["allocations"],
        filters=filters,
    )


# -------------------------------------------------------
# BUDGET REQUESTS
# -------------------------------------------------------

def get_budget_requests(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_budget_table(
        "requests",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_budget_requests(filters=None):
    return count_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES["requests"],
        filters=filters,
    )


# -------------------------------------------------------
# BUDGET APPROVALS
# -------------------------------------------------------

def get_budget_approvals(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_budget_table(
        "approvals",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_budget_approvals(filters=None):
    return count_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES["approvals"],
        filters=filters,
    )


# -------------------------------------------------------
# BUDGET COMMITMENTS
# -------------------------------------------------------

def get_budget_commitments(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_budget_table(
        "commitments",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_budget_commitments(filters=None):
    return count_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES["commitments"],
        filters=filters,
    )


# -------------------------------------------------------
# BUDGET EXPENDITURES
# -------------------------------------------------------

def get_budget_expenditures(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_budget_table(
        "expenditures",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_budget_expenditures(filters=None):
    return count_records(
        module_name=BUDGET_SOURCE_MODULE,
        table_name=BUDGET_AUDIT_TABLES["expenditures"],
        filters=filters,
    )


# -------------------------------------------------------
# BUDGET AUDIT OVERVIEW
# -------------------------------------------------------

def get_budget_audit_overview():
    """Return counts for registered Budget tables."""

    overview = {
        "module": BUDGET_SOURCE_MODULE,
        "department": "Budget Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in BUDGET_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=BUDGET_SOURCE_MODULE,
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
                "error": "Unable to read registered Budget table",
            }

    return overview


# -------------------------------------------------------
# BUDGET CONNECTION STATUS
# -------------------------------------------------------

def get_budget_connection_status():
    """Check the existing Budget database connection."""

    try:
        result = check_source_connection(
            BUDGET_SOURCE_MODULE
        )

        result["department"] = "Budget Management"

        return result

    except Exception:
        return {
            "module": BUDGET_SOURCE_MODULE,
            "department": "Budget Management",
            "connected": False,
            "read_only": True,
            "error": "Budget database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE BUDGET AUDIT DATA
# -------------------------------------------------------

def get_budget_audit_data(limit=100):
    """
    Return a read-only Budget audit snapshot.

    Each source table is queried independently.
    No budget calculations or financial changes occur here.
    """

    return {
        "connection": get_budget_connection_status(),
        "overview": get_budget_audit_overview(),
        "plans": get_budget_plans(limit=limit),
        "allocations": get_budget_allocations(limit=limit),
        "requests": get_budget_requests(limit=limit),
        "approvals": get_budget_approvals(limit=limit),
        "commitments": get_budget_commitments(limit=limit),
        "expenditures": get_budget_expenditures(limit=limit),
    }
