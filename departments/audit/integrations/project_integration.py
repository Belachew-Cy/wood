
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Project Integration

Provides read-only access to the existing Project Management
database for internal audit and compliance reviews.

This integration does not create, update or delete project data.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# PROJECT AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

PROJECT_SOURCE_MODULE = "project"

PROJECT_AUDIT_TABLES = {
    "projects": "projects",
    "members": "project_members",
    "milestones": "project_milestones",
    "tasks": "project_tasks",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_project_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Project Management table."""

    if table_key not in PROJECT_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Project audit data source."
        )

    return read_records(
        module_name=PROJECT_SOURCE_MODULE,
        table_name=PROJECT_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# PROJECTS
# -------------------------------------------------------

def get_projects(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_project_table(
        "projects",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_projects(filters=None):
    return count_records(
        module_name=PROJECT_SOURCE_MODULE,
        table_name=PROJECT_AUDIT_TABLES["projects"],
        filters=filters,
    )


# -------------------------------------------------------
# PROJECT MEMBERS
# -------------------------------------------------------

def get_project_members(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_project_table(
        "members",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_project_members(filters=None):
    return count_records(
        module_name=PROJECT_SOURCE_MODULE,
        table_name=PROJECT_AUDIT_TABLES["members"],
        filters=filters,
    )


# -------------------------------------------------------
# PROJECT MILESTONES
# -------------------------------------------------------

def get_project_milestones(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_project_table(
        "milestones",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_project_milestones(filters=None):
    return count_records(
        module_name=PROJECT_SOURCE_MODULE,
        table_name=PROJECT_AUDIT_TABLES["milestones"],
        filters=filters,
    )


# -------------------------------------------------------
# PROJECT TASKS
# -------------------------------------------------------

def get_project_tasks(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_project_table(
        "tasks",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_project_tasks(filters=None):
    return count_records(
        module_name=PROJECT_SOURCE_MODULE,
        table_name=PROJECT_AUDIT_TABLES["tasks"],
        filters=filters,
    )


# -------------------------------------------------------
# PROJECT AUDIT OVERVIEW
# -------------------------------------------------------

def get_project_audit_overview():
    """Return record counts for registered project tables."""

    overview = {
        "module": PROJECT_SOURCE_MODULE,
        "department": "Project Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in PROJECT_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=PROJECT_SOURCE_MODULE,
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
                "error": "Unable to read registered Project table",
            }

    return overview


# -------------------------------------------------------
# PROJECT CONNECTION STATUS
# -------------------------------------------------------

def get_project_connection_status():
    """Check the existing Project database connection."""

    try:
        result = check_source_connection(
            PROJECT_SOURCE_MODULE
        )

        result["department"] = "Project Management"

        return result

    except Exception:
        return {
            "module": PROJECT_SOURCE_MODULE,
            "department": "Project Management",
            "connected": False,
            "read_only": True,
            "error": "Project database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE PROJECT AUDIT DATA
# -------------------------------------------------------

def get_project_audit_data(limit=100):
    """
    Return a read-only Project Management audit snapshot.

    Each table is queried independently through the shared
    read-only adapter.
    """

    return {
        "connection": get_project_connection_status(),
        "overview": get_project_audit_overview(),
        "projects": get_projects(limit=limit),
        "members": get_project_members(limit=limit),
        "milestones": get_project_milestones(limit=limit),
        "tasks": get_project_tasks(limit=limit),
    }
