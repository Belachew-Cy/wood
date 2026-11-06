
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Marketing Integration

Provides read-only access to the existing Marketing database
for internal audit, compliance and management reviews.

This integration does not modify marketing transactions.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# MARKETING AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

MARKETING_SOURCE_MODULE = "marketing"

MARKETING_AUDIT_TABLES = {
    "plans": "marketing_plans",
    "campaigns": "marketing_campaigns",
    "leads": "marketing_leads",
    "market_research": "market_research",
    "digital_campaigns": "digital_campaigns",
    "customer_feedback": "customer_feedback",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_marketing_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Marketing table."""

    if table_key not in MARKETING_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Marketing audit data source."
        )

    return read_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# MARKETING PLANS
# -------------------------------------------------------

def get_marketing_plans(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_marketing_table(
        "plans",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_marketing_plans(filters=None):
    return count_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES["plans"],
        filters=filters,
    )


# -------------------------------------------------------
# MARKETING CAMPAIGNS
# -------------------------------------------------------

def get_marketing_campaigns(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_marketing_table(
        "campaigns",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_marketing_campaigns(filters=None):
    return count_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES["campaigns"],
        filters=filters,
    )


# -------------------------------------------------------
# MARKETING LEADS
# -------------------------------------------------------

def get_marketing_leads(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_marketing_table(
        "leads",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_marketing_leads(filters=None):
    return count_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES["leads"],
        filters=filters,
    )


# -------------------------------------------------------
# MARKET RESEARCH
# -------------------------------------------------------

def get_market_research(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_marketing_table(
        "market_research",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_market_research(filters=None):
    return count_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES["market_research"],
        filters=filters,
    )


# -------------------------------------------------------
# DIGITAL CAMPAIGNS
# -------------------------------------------------------

def get_digital_campaigns(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_marketing_table(
        "digital_campaigns",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_digital_campaigns(filters=None):
    return count_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES["digital_campaigns"],
        filters=filters,
    )


# -------------------------------------------------------
# CUSTOMER FEEDBACK
# -------------------------------------------------------

def get_customer_feedback(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
):
    return _read_marketing_table(
        "customer_feedback",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def count_customer_feedback(filters=None):
    return count_records(
        module_name=MARKETING_SOURCE_MODULE,
        table_name=MARKETING_AUDIT_TABLES["customer_feedback"],
        filters=filters,
    )


# -------------------------------------------------------
# MARKETING AUDIT OVERVIEW
# -------------------------------------------------------

def get_marketing_audit_overview():
    """Return record counts for registered Marketing tables."""

    overview = {
        "module": MARKETING_SOURCE_MODULE,
        "department": "Marketing Management",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in MARKETING_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=MARKETING_SOURCE_MODULE,
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
                "error": "Unable to read registered Marketing table",
            }

    return overview


# -------------------------------------------------------
# MARKETING CONNECTION STATUS
# -------------------------------------------------------

def get_marketing_connection_status():
    """Check the existing Marketing database connection."""

    try:
        result = check_source_connection(
            MARKETING_SOURCE_MODULE
        )

        result["department"] = "Marketing Management"

        return result

    except Exception:
        return {
            "module": MARKETING_SOURCE_MODULE,
            "department": "Marketing Management",
            "connected": False,
            "read_only": True,
            "error": "Marketing database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE MARKETING AUDIT DATA
# -------------------------------------------------------

def get_marketing_audit_data(limit=100):
    """
    Return a read-only Marketing Management audit snapshot.

    Each source table is queried independently.
    """

    return {
        "connection": get_marketing_connection_status(),
        "overview": get_marketing_audit_overview(),
        "plans": get_marketing_plans(limit=limit),
        "campaigns": get_marketing_campaigns(limit=limit),
        "leads": get_marketing_leads(limit=limit),
        "market_research": get_market_research(limit=limit),
        "digital_campaigns": get_digital_campaigns(limit=limit),
        "customer_feedback": get_customer_feedback(limit=limit),
    }
