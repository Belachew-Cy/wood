
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Vacancy and Recruitment Integration

Provides read-only access to the existing Vacancy database
for recruitment audits, compliance reviews and audit evidence.

This integration does not modify recruitment records.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# RECRUITMENT AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

VACANCY_SOURCE_MODULE = "vacancy"

VACANCY_AUDIT_TABLES = {
    "vacancies": "job_vacancies",
    "applications": "job_applications",
    "interviews": "recruitment_interviews",
    "interview_panel": "interview_panel",
    "interview_scores": "interview_scores",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_vacancy_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Recruitment table."""

    if table_key not in VACANCY_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Recruitment audit data source."
        )

    return read_records(
        module_name=VACANCY_SOURCE_MODULE,
        table_name=VACANCY_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# JOB VACANCIES
# -------------------------------------------------------

def get_job_vacancies(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_vacancy_table(
        "vacancies",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_job_vacancies(filters=None):
    return count_records(
        module_name=VACANCY_SOURCE_MODULE,
        table_name=VACANCY_AUDIT_TABLES["vacancies"],
        filters=filters,
    )


# -------------------------------------------------------
# JOB APPLICATIONS
# -------------------------------------------------------

def get_job_applications(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_vacancy_table(
        "applications",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_job_applications(filters=None):
    return count_records(
        module_name=VACANCY_SOURCE_MODULE,
        table_name=VACANCY_AUDIT_TABLES["applications"],
        filters=filters,
    )


# -------------------------------------------------------
# RECRUITMENT INTERVIEWS
# -------------------------------------------------------

def get_recruitment_interviews(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_vacancy_table(
        "interviews",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_recruitment_interviews(filters=None):
    return count_records(
        module_name=VACANCY_SOURCE_MODULE,
        table_name=VACANCY_AUDIT_TABLES["interviews"],
        filters=filters,
    )


# -------------------------------------------------------
# INTERVIEW PANEL MEMBERS
# -------------------------------------------------------

def get_interview_panel(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_vacancy_table(
        "interview_panel",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_interview_panel(filters=None):
    return count_records(
        module_name=VACANCY_SOURCE_MODULE,
        table_name=VACANCY_AUDIT_TABLES["interview_panel"],
        filters=filters,
    )


# -------------------------------------------------------
# INTERVIEW SCORES
# -------------------------------------------------------

def get_interview_scores(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_vacancy_table(
        "interview_scores",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_interview_scores(filters=None):
    return count_records(
        module_name=VACANCY_SOURCE_MODULE,
        table_name=VACANCY_AUDIT_TABLES["interview_scores"],
        filters=filters,
    )


# -------------------------------------------------------
# RECRUITMENT AUDIT OVERVIEW
# -------------------------------------------------------

def get_vacancy_audit_overview():
    """Return record counts for registered Recruitment tables."""

    overview = {
        "module": VACANCY_SOURCE_MODULE,
        "department": "Recruitment",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in VACANCY_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=VACANCY_SOURCE_MODULE,
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
                "error": "Unable to read registered Recruitment table",
            }

    return overview


# -------------------------------------------------------
# RECRUITMENT CONNECTION STATUS
# -------------------------------------------------------

def get_vacancy_connection_status():
    """Check the existing Vacancy database connection."""

    try:
        result = check_source_connection(
            VACANCY_SOURCE_MODULE
        )

        result["department"] = "Recruitment"

        return result

    except Exception:
        return {
            "module": VACANCY_SOURCE_MODULE,
            "department": "Recruitment",
            "connected": False,
            "read_only": True,
            "error": "Recruitment database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE RECRUITMENT AUDIT DATA
# -------------------------------------------------------

def get_vacancy_audit_data(limit=100):
    """
    Return a read-only Recruitment audit snapshot.

    Each source table is queried independently through the
    shared read-only adapter.
    """

    return {
        "connection": get_vacancy_connection_status(),
        "overview": get_vacancy_audit_overview(),
        "vacancies": get_job_vacancies(limit=limit),
        "applications": get_job_applications(limit=limit),
        "interviews": get_recruitment_interviews(limit=limit),
        "interview_panel": get_interview_panel(limit=limit),
        "interview_scores": get_interview_scores(limit=limit),
    }
