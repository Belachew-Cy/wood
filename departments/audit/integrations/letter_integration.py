
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Letter Integration

Provides read-only access to the existing Letter Management
database for internal audit and compliance reviews.

This integration does not create, update or delete letters,
delivery records, read records or approval records.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# LETTER AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

LETTER_SOURCE_MODULE = "letter"

LETTER_AUDIT_TABLES = {
    "letters": "letters",
    "deliveries": "letter_deliveries",
    "reads": "letter_reads",
    "approval_steps": "letter_approval_steps",
    "audit_logs": "letter_audit_logs",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_letter_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Letter Management table."""

    if table_key not in LETTER_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Letter audit data source."
        )

    return read_records(
        module_name=LETTER_SOURCE_MODULE,
        table_name=LETTER_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# LETTERS
# -------------------------------------------------------

def get_letters(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_letter_table(
        "letters",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_letters(filters=None):
    return count_records(
        module_name=LETTER_SOURCE_MODULE,
        table_name=LETTER_AUDIT_TABLES["letters"],
        filters=filters,
    )


# -------------------------------------------------------
# LETTER DELIVERIES
# -------------------------------------------------------

def get_letter_deliveries(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_letter_table(
        "deliveries",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_letter_deliveries(filters=None):
    return count_records(
        module_name=LETTER_SOURCE_MODULE,
        table_name=LETTER_AUDIT_TABLES["deliveries"],
        filters=filters,
    )


# -------------------------------------------------------
# LETTER READ RECORDS
# -------------------------------------------------------

def get_letter_reads(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_letter_table(
        "reads",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_letter_reads(filters=None):
    return count_records(
        module_name=LETTER_SOURCE_MODULE,
        table_name=LETTER_AUDIT_TABLES["reads"],
        filters=filters,
    )


# -------------------------------------------------------
# LETTER APPROVAL STEPS
# -------------------------------------------------------

def get_letter_approval_steps(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_letter_table(
        "approval_steps",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_letter_approval_steps(filters=None):
    return count_records(
        module_name=LETTER_SOURCE_MODULE,
        table_name=LETTER_AUDIT_TABLES["approval_steps"],
        filters=filters,
    )


# -------------------------------------------------------
# LETTER AUDIT LOGS
# -------------------------------------------------------

def get_letter_audit_logs(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_letter_table(
        "audit_logs",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_letter_audit_logs(filters=None):
    return count_records(
        module_name=LETTER_SOURCE_MODULE,
        table_name=LETTER_AUDIT_TABLES["audit_logs"],
        filters=filters,
    )


# -------------------------------------------------------
# LETTER AUDIT OVERVIEW
# -------------------------------------------------------

def get_letter_audit_overview():
    """Return record counts for registered Letter tables."""

    overview = {
        "module": LETTER_SOURCE_MODULE,
        "department": "Letter Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in LETTER_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=LETTER_SOURCE_MODULE,
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
                "error": "Unable to read registered Letter table",
            }

    return overview


# -------------------------------------------------------
# LETTER CONNECTION STATUS
# -------------------------------------------------------

def get_letter_connection_status():
    """Check the existing Letter database connection."""

    try:
        result = check_source_connection(
            LETTER_SOURCE_MODULE
        )

        result["department"] = "Letter Management"

        return result

    except Exception:
        return {
            "module": LETTER_SOURCE_MODULE,
            "department": "Letter Management",
            "connected": False,
            "read_only": True,
            "error": "Letter database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE LETTER AUDIT DATA
# -------------------------------------------------------

def get_letter_audit_data(limit=100):
    """
    Return a read-only Letter Management audit snapshot.

    Each source table is queried independently through the
    shared read-only adapter.
    """

    return {
        "connection": get_letter_connection_status(),
        "overview": get_letter_audit_overview(),
        "letters": get_letters(limit=limit),
        "deliveries": get_letter_deliveries(limit=limit),
        "reads": get_letter_reads(limit=limit),
        "approval_steps": get_letter_approval_steps(limit=limit),
        "audit_logs": get_letter_audit_logs(limit=limit),
    }
