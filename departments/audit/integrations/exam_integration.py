
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Examination Integration

Provides read-only access to the existing Examination database
for recruitment examination audits and compliance reviews.

This integration does not create, update or delete examination
records, questions, attempts or answers.
"""

from departments.audit.integrations.read_only import (
    read_records,
    count_records,
    check_source_connection,
)


# -------------------------------------------------------
# EXAMINATION AUDIT SOURCE CONFIGURATION
# -------------------------------------------------------

EXAM_SOURCE_MODULE = "exam"

EXAM_AUDIT_TABLES = {
    "exams": "recruitment_exams",
    "questions": "exam_questions",
    "attempts": "exam_attempts",
    "answers": "exam_answers",
}


# -------------------------------------------------------
# INTERNAL READ HELPER
# -------------------------------------------------------

def _read_exam_table(
    table_key,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    """Read a registered Examination table."""

    if table_key not in EXAM_AUDIT_TABLES:
        raise ValueError(
            "Unsupported Examination audit data source."
        )

    return read_records(
        module_name=EXAM_SOURCE_MODULE,
        table_name=EXAM_AUDIT_TABLES[table_key],
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


# -------------------------------------------------------
# RECRUITMENT EXAMS
# -------------------------------------------------------

def get_recruitment_exams(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_exam_table(
        "exams",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_recruitment_exams(filters=None):
    return count_records(
        module_name=EXAM_SOURCE_MODULE,
        table_name=EXAM_AUDIT_TABLES["exams"],
        filters=filters,
    )


# -------------------------------------------------------
# EXAM QUESTIONS
# -------------------------------------------------------

def get_exam_questions(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_exam_table(
        "questions",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_exam_questions(filters=None):
    return count_records(
        module_name=EXAM_SOURCE_MODULE,
        table_name=EXAM_AUDIT_TABLES["questions"],
        filters=filters,
    )


# -------------------------------------------------------
# EXAM ATTEMPTS
# -------------------------------------------------------

def get_exam_attempts(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_exam_table(
        "attempts",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_exam_attempts(filters=None):
    return count_records(
        module_name=EXAM_SOURCE_MODULE,
        table_name=EXAM_AUDIT_TABLES["attempts"],
        filters=filters,
    )


# -------------------------------------------------------
# EXAM ANSWERS
# -------------------------------------------------------

def get_exam_answers(
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=True,
):
    return _read_exam_table(
        "answers",
        filters=filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def count_exam_answers(filters=None):
    return count_records(
        module_name=EXAM_SOURCE_MODULE,
        table_name=EXAM_AUDIT_TABLES["answers"],
        filters=filters,
    )


# -------------------------------------------------------
# EXAMINATION AUDIT OVERVIEW
# -------------------------------------------------------

def get_exam_audit_overview():
    """Return record counts for registered Examination tables."""

    overview = {
        "module": EXAM_SOURCE_MODULE,
        "department": "Recruitment Examination",
        "read_only": True,
        "tables": {},
        "total_records": 0,
    }

    for key, table_name in EXAM_AUDIT_TABLES.items():

        try:
            total = count_records(
                module_name=EXAM_SOURCE_MODULE,
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
                "error": "Unable to read registered Examination table",
            }

    return overview


# -------------------------------------------------------
# EXAMINATION CONNECTION STATUS
# -------------------------------------------------------

def get_exam_connection_status():
    """Check the existing Examination database connection."""

    try:
        result = check_source_connection(
            EXAM_SOURCE_MODULE
        )

        result["department"] = "Recruitment Examination"

        return result

    except Exception:
        return {
            "module": EXAM_SOURCE_MODULE,
            "department": "Recruitment Examination",
            "connected": False,
            "read_only": True,
            "error": "Examination database connection unavailable",
        }


# -------------------------------------------------------
# COMPLETE EXAMINATION AUDIT DATA
# -------------------------------------------------------

def get_exam_audit_data(limit=100):
    """
    Return a read-only Examination audit snapshot.

    Each source table is queried independently through the
    shared read-only adapter.
    """

    return {
        "connection": get_exam_connection_status(),
        "overview": get_exam_audit_overview(),
        "exams": get_recruitment_exams(limit=limit),
        "questions": get_exam_questions(limit=limit),
        "attempts": get_exam_attempts(limit=limit),
        "answers": get_exam_answers(limit=limit),
    }
