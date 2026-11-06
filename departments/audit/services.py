
# =======================================================
# AUDIT DASHBOARD - CONSOLIDATED SERVICE
# Append to the existing services.py
# =======================================================

def get_audit_dashboard_context():
    """
    Prepare consolidated Audit Management dashboard data.

    Reuses the existing models, services and integration
    registry. Departmental databases remain read-only.
    """

    # ---------------------------------------------------
    # 1. SHARED DATABASE STATISTICS
    # ---------------------------------------------------

    try:
        raw_stats = get_dashboard_statistics() or {}
        statistics_error = None

    except Exception:
        raw_stats = {}
        statistics_error = "Audit statistics are unavailable."

    if not isinstance(raw_stats, dict):
        raw_stats = dict(raw_stats)

    # Preserve existing statistics and add safe defaults.
    stats = {
        **raw_stats,
        "reviews": raw_stats.get("reviews", 0),
        "open_reviews": raw_stats.get("open_reviews", 0),
        "findings": raw_stats.get("findings", 0),
        "high_risk": raw_stats.get("high_risk", 0),
        "events": raw_stats.get("events", 0),
        "refusals": raw_stats.get("refusals", 0),
        "audit_plans": raw_stats.get("audit_plans", 0),
        "planned_audits": raw_stats.get("planned_audits", 0),
        "ongoing_audits": raw_stats.get("ongoing_audits", 0),
        "completed_audits": raw_stats.get("completed_audits", 0),
        "open_findings": raw_stats.get("open_findings", 0),
        "pending_actions": raw_stats.get("pending_actions", 0),
    }

    # ---------------------------------------------------
    # 2. DEPARTMENT CONNECTION STATUS
    # ---------------------------------------------------

    connection_summary = get_all_department_connection_statuses()

    departments = connection_summary.get("departments", [])

    integration_warning = (
        connection_summary.get("unavailable_departments", 0) > 0
    )

    # ---------------------------------------------------
    # 3. DEPARTMENT DATASET OVERVIEWS
    # ---------------------------------------------------

    department_overviews = get_all_department_integration_overviews()

    groups = []

    for result in department_overviews:

        department_name = result.get("department", "Unknown")
        module_name = result.get("module", "unknown")

        connected = bool(result.get("success"))

        source_data = result.get("data") or []

        if isinstance(source_data, dict):
            source_tables = source_data.get("tables", [])
        elif isinstance(source_data, list):
            source_tables = source_data
        else:
            source_tables = []

        tables = []

        for item in source_tables:

            if not isinstance(item, dict):
                continue

            table_name = item.get("table")

            if not table_name:
                continue

            tables.append({
                "table": table_name,
                "label": item.get(
                    "label",
                    table_name.replace("_", " ").title()
                ),
                "count": item.get(
                    "record_count",
                    item.get("count", 0)
                ),
                "available": item.get("error") is None,
                "href": None,
            })

        groups.append({
            "department": department_name,
            "module": module_name,
            "connected": connected,
            "total_records": sum(
                item["count"]
                for item in tables
                if isinstance(item["count"], int)
            ),
            "tables": tables,
        })

    # ---------------------------------------------------
    # 4. CONSOLIDATED RESULT
    # ---------------------------------------------------

    return {
        "generated_at": now(),
        "stats": stats,
        "groups": groups,
        "departments": departments,
        "connection_summary": connection_summary,
        "integration_warning": integration_warning,
        "statistics_error": statistics_error,
    }


# =======================================================
# INTEGRATION SERVICE ALIASES
# =======================================================

def get_all_audit_integration_status():
    """Return the connection status of all registered modules."""

    return get_all_department_connection_statuses()


def get_all_audit_department_overviews():
    """Return read-only departmental dataset overviews."""

    return get_all_department_integration_overviews()


def get_audit_department_snapshot(module_key):
    """Return read-only records from one registered department."""

    return get_department_audit_data(module_key)


# =======================================================
# SERVICE AVAILABILITY CHECK
# =======================================================

def get_audit_service_health():
    """
    Check whether the Audit Management service layer
    and departmental integration registry are available.
    """

    return {
        "service": "Audit Management",
        "available": True,
        "shared_database": True,
        "departmental_integrations": len(
            AUDIT_INTEGRATION_REGISTRY
        ),
        "registered_departments": (
            get_available_audit_departments()
        ),
        "checked_at": now(),
    }
