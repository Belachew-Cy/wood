"""
Audit Management database models.

Uses the existing multi-database connection supplied by the application.
Audit management tables are stored in the Shared database.

Existing audit_logs, audit_activity, audit_reviews and audit_findings
are deliberately reused and are NOT recreated here.
"""

AUDIT_SCHEMA = (

    """
    CREATE TABLE IF NOT EXISTS audit_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_number TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        audit_type TEXT NOT NULL,
        department TEXT,
        scope TEXT NOT NULL,
        objectives TEXT,
        methodology TEXT,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Draft',
        risk_level TEXT DEFAULT 'Medium',
        created_by INTEGER NOT NULL,
        approved_by INTEGER,
        approved_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        auditor_user_id INTEGER NOT NULL,
        assignment_role TEXT DEFAULT 'Auditor',
        assigned_by INTEGER NOT NULL,
        assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        due_date TEXT,
        status TEXT NOT NULL DEFAULT 'Assigned',
        notes TEXT
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_checklist_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT,
        audit_category TEXT,
        description TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        guidance TEXT,
        expected_evidence TEXT,
        risk_weight INTEGER DEFAULT 1,
        sequence_number INTEGER DEFAULT 1,
        is_required INTEGER NOT NULL DEFAULT 1
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_checklist_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        assignment_id INTEGER,
        checklist_item_id INTEGER NOT NULL,
        result TEXT NOT NULL DEFAULT 'Not Reviewed',
        auditor_comment TEXT,
        evidence_reference TEXT,
        reviewed_by INTEGER NOT NULL,
        reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_risk_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        risk_reference TEXT NOT NULL UNIQUE,
        department TEXT NOT NULL,
        process_name TEXT NOT NULL,
        risk_description TEXT NOT NULL,
        risk_category TEXT,
        likelihood INTEGER NOT NULL,
        impact INTEGER NOT NULL,
        risk_score INTEGER NOT NULL,
        risk_level TEXT NOT NULL,
        existing_controls TEXT,
        recommended_controls TEXT,
        owner_user_id INTEGER,
        assessment_date TEXT NOT NULL,
        next_review_date TEXT,
        status TEXT NOT NULL DEFAULT 'Open',
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_corrective_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_reference TEXT NOT NULL UNIQUE,
        finding_id INTEGER NOT NULL,
        department TEXT,
        finding_description TEXT NOT NULL,
        recommended_action TEXT NOT NULL,
        responsible_user_id INTEGER,
        target_date TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'Medium',
        status TEXT NOT NULL DEFAULT 'Open',
        management_response TEXT,
        completion_evidence TEXT,
        verified_by INTEGER,
        verified_at TEXT,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_compliance_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference_number TEXT NOT NULL UNIQUE,
        department TEXT NOT NULL,
        requirement_name TEXT NOT NULL,
        requirement_source TEXT,
        description TEXT,
        compliance_status TEXT NOT NULL DEFAULT 'Not Assessed',
        evidence_reference TEXT,
        responsible_user_id INTEGER,
        review_date TEXT,
        next_review_date TEXT,
        findings TEXT,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evidence_reference TEXT NOT NULL UNIQUE,
        plan_id INTEGER,
        finding_id INTEGER,
        entity_module TEXT,
        entity_type TEXT,
        entity_id INTEGER,
        file_name TEXT,
        stored_file_name TEXT,
        file_path TEXT,
        evidence_description TEXT,
        evidence_type TEXT DEFAULT 'Document',
        uploaded_by INTEGER NOT NULL,
        uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        confidentiality TEXT NOT NULL DEFAULT 'Internal'
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS audit_report_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_number TEXT NOT NULL UNIQUE,
        plan_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        executive_summary TEXT,
        scope TEXT,
        methodology TEXT,
        findings_summary TEXT,
        recommendations TEXT,
        report_status TEXT NOT NULL DEFAULT 'Draft',
        prepared_by INTEGER NOT NULL,
        reviewed_by INTEGER,
        approved_by INTEGER,
        approved_at TEXT,
        report_file TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
)


def create_audit_schema(db):
    """
    Create only missing Audit Management tables.

    db must be the existing application database connection,
    such as the connection returned by your database.connect().

    Transaction ownership remains with the caller.
    """

    for statement in AUDIT_SCHEMA:
        db.execute(statement)


def get_table_columns(db, table_name):
    """
    Inspect an existing table without assuming its column structure.
    Useful for adapting to existing audit_findings and audit_logs.
    """

    allowed_tables = {
        "audit_logs",
        "audit_activity",
        "audit_reviews",
        "audit_findings",
        "audit_plans",
        "audit_assignments",
        "audit_checklist_templates",
        "audit_checklist_items",
        "audit_checklist_results",
        "audit_risk_assessments",
        "audit_corrective_actions",
        "audit_compliance_records",
        "audit_evidence",
        "audit_report_records",
    }

    if table_name not in allowed_tables:
        raise ValueError("Unsupported audit table.")

    rows = db.execute(
        "PRAGMA table_info({})".format(table_name)
    ).fetchall()

    return [row["name"] for row in rows]


def audit_table_exists(db, table_name):
    """
    Check whether an audit table exists in its owning database.
    """

    allowed_tables = {
        "audit_logs",
        "audit_activity",
        "audit_reviews",
        "audit_findings",
        "audit_plans",
        "audit_assignments",
        "audit_checklist_templates",
        "audit_checklist_items",
        "audit_checklist_results",
        "audit_risk_assessments",
        "audit_corrective_actions",
        "audit_compliance_records",
        "audit_evidence",
        "audit_report_records",
    }

    if table_name not in allowed_tables:
        raise ValueError("Unsupported audit table.")

    from database import table_exists

    return table_exists(db, table_name)

"""
Dereje Wood & Kitchen Cabinet
Audit Management Models

Database:
    Existing Shared Database

Existing tables reused:
    audit_logs
    audit_activity
    audit_reviews
    audit_findings

Additional tables:
    audit_plans
    audit_assignments
    audit_checklist_templates
    audit_checklist_items
    audit_checklist_results
    audit_risk_assessments
    audit_corrective_actions
    audit_compliance_records
    audit_evidence
    audit_report_records

Departmental source records are not modified here.
"""

import json
import sqlite3

from datetime import datetime, timezone

from Database_config import connect_file


# -------------------------------------------------------
# DATABASE HELPERS
# -------------------------------------------------------

def get_audit_connection():
    """
    Open the existing Shared Database directly.

    Adjust the import above if your actual configuration
    module has a different Python filename or import path.
    """

    return connect_file("shared")


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def table_exists(db, table_name):
    return db.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,)
    ).fetchone() is not None


# -------------------------------------------------------
# CREATE ADDITIONAL AUDIT TABLES
# -------------------------------------------------------

def create_audit_tables():
    """
    Create only the additional Audit Management tables.

    Existing audit tables are preserved.
    """

    statements = [

        # ------------------------------------------------
        # 1. AUDIT PLANS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            plan_number TEXT UNIQUE NOT NULL,

            title TEXT NOT NULL,

            audit_type TEXT NOT NULL,

            department TEXT NOT NULL,

            objective TEXT,

            scope TEXT,

            methodology TEXT,

            start_date TEXT,

            end_date TEXT,

            risk_level TEXT DEFAULT 'Medium',

            status TEXT DEFAULT 'Draft',

            created_by INTEGER NOT NULL,

            approved_by INTEGER,

            approval_date TEXT,

            created_at TEXT NOT NULL,

            updated_at TEXT
        )
        """,

        # ------------------------------------------------
        # 2. AUDIT ASSIGNMENTS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            audit_plan_id INTEGER NOT NULL,

            auditor_id INTEGER NOT NULL,

            assignment_role TEXT DEFAULT 'Auditor',

            assigned_by INTEGER NOT NULL,

            assigned_at TEXT NOT NULL,

            status TEXT DEFAULT 'Assigned',

            notes TEXT,

            FOREIGN KEY (audit_plan_id)
                REFERENCES audit_plans(id)
                ON DELETE CASCADE,

            UNIQUE (audit_plan_id, auditor_id)
        )
        """,

        # ------------------------------------------------
        # 3. CHECKLIST TEMPLATES
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_checklist_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            template_name TEXT NOT NULL,

            department TEXT NOT NULL,

            audit_category TEXT,

            description TEXT,

            status TEXT DEFAULT 'Active',

            created_by INTEGER NOT NULL,

            created_at TEXT NOT NULL,

            updated_at TEXT
        )
        """,

        # ------------------------------------------------
        # 4. CHECKLIST ITEMS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_checklist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            template_id INTEGER NOT NULL,

            question TEXT NOT NULL,

            guidance TEXT,

            control_reference TEXT,

            risk_level TEXT DEFAULT 'Medium',

            display_order INTEGER DEFAULT 1,

            is_required INTEGER DEFAULT 1,

            FOREIGN KEY (template_id)
                REFERENCES audit_checklist_templates(id)
                ON DELETE CASCADE
        )
        """,

        # ------------------------------------------------
        # 5. CHECKLIST RESULTS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_checklist_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            audit_plan_id INTEGER NOT NULL,

            checklist_item_id INTEGER NOT NULL,

            result TEXT NOT NULL,

            auditor_comment TEXT,

            evidence_reference TEXT,

            completed_by INTEGER NOT NULL,

            completed_at TEXT NOT NULL,

            FOREIGN KEY (audit_plan_id)
                REFERENCES audit_plans(id),

            FOREIGN KEY (checklist_item_id)
                REFERENCES audit_checklist_items(id)
        )
        """,

        # ------------------------------------------------
        # 6. RISK ASSESSMENTS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_risk_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            audit_plan_id INTEGER NOT NULL,

            risk_title TEXT NOT NULL,

            risk_description TEXT,

            likelihood INTEGER NOT NULL,

            impact INTEGER NOT NULL,

            risk_score INTEGER NOT NULL,

            risk_level TEXT NOT NULL,

            mitigation_plan TEXT,

            responsible_person INTEGER,

            status TEXT DEFAULT 'Open',

            assessed_by INTEGER NOT NULL,

            assessed_at TEXT NOT NULL,

            FOREIGN KEY (audit_plan_id)
                REFERENCES audit_plans(id)
        )
        """,

        # ------------------------------------------------
        # 7. CORRECTIVE ACTIONS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_corrective_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            finding_id INTEGER NOT NULL,

            action_title TEXT NOT NULL,

            action_description TEXT,

            assigned_to INTEGER,

            due_date TEXT,

            priority TEXT DEFAULT 'Medium',

            status TEXT DEFAULT 'Open',

            completion_notes TEXT,

            verified_by INTEGER,

            verified_at TEXT,

            created_by INTEGER NOT NULL,

            created_at TEXT NOT NULL,

            updated_at TEXT
        )
        """,

        # ------------------------------------------------
        # 8. COMPLIANCE RECORDS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_compliance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            audit_plan_id INTEGER NOT NULL,

            department TEXT NOT NULL,

            compliance_area TEXT NOT NULL,

            requirement TEXT,

            compliance_status TEXT NOT NULL,

            observation TEXT,

            evidence_reference TEXT,

            assessed_by INTEGER NOT NULL,

            assessed_at TEXT NOT NULL,

            FOREIGN KEY (audit_plan_id)
                REFERENCES audit_plans(id)
        )
        """,

        # ------------------------------------------------
        # 9. AUDIT EVIDENCE
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            audit_plan_id INTEGER NOT NULL,

            finding_id INTEGER,

            evidence_title TEXT NOT NULL,

            evidence_type TEXT,

            file_path TEXT,

            source_module TEXT,

            source_table TEXT,

            source_record_id TEXT,

            description TEXT,

            uploaded_by INTEGER NOT NULL,

            uploaded_at TEXT NOT NULL,

            FOREIGN KEY (audit_plan_id)
                REFERENCES audit_plans(id)
        )
        """,

        # ------------------------------------------------
        # 10. AUDIT REPORT RECORDS
        # ------------------------------------------------

        """
        CREATE TABLE IF NOT EXISTS audit_report_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            audit_plan_id INTEGER NOT NULL,

            report_number TEXT UNIQUE NOT NULL,

            report_title TEXT NOT NULL,

            executive_summary TEXT,

            findings_summary TEXT,

            recommendations TEXT,

            report_status TEXT DEFAULT 'Draft',

            prepared_by INTEGER NOT NULL,

            reviewed_by INTEGER,

            approved_by INTEGER,

            prepared_at TEXT NOT NULL,

            reviewed_at TEXT,

            approved_at TEXT,

            report_file TEXT,

            FOREIGN KEY (audit_plan_id)
                REFERENCES audit_plans(id)
        )
        """,
    ]

    db = get_audit_connection()

    try:

        with db:

            for statement in statements:
                db.execute(statement)

            # Indexes for frequently queried audit records.

            db.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_audit_plans_department
                ON audit_plans(department)
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_audit_plans_status
                ON audit_plans(status)
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_audit_assignments_audit
                ON audit_assignments(audit_plan_id)
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_audit_risk_plan
                ON audit_risk_assessments(audit_plan_id)
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_audit_corrective_finding
                ON audit_corrective_actions(finding_id)
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_audit_evidence_plan
                ON audit_evidence(audit_plan_id)
            """)

        return True

    finally:
        db.close()


# -------------------------------------------------------
# DATABASE INITIALIZATION
# -------------------------------------------------------

def initialize_audit_database():
    """
    Initialize additional audit-management tables.

    Call during the application's controlled database
    initialization process, not on every page request.
    """

    return create_audit_tables()


# -------------------------------------------------------
# AUDIT PLAN OPERATIONS
# -------------------------------------------------------

def create_audit_plan(
    plan_number,
    title,
    audit_type,
    department,
    created_by,
    objective=None,
    scope=None,
    methodology=None,
    start_date=None,
    end_date=None,
    risk_level="Medium"
):
    """Create a new audit plan."""

    db = get_audit_connection()

    try:

        cursor = db.execute(
            """
            INSERT INTO audit_plans (
                plan_number,
                title,
                audit_type,
                department,
                objective,
                scope,
                methodology,
                start_date,
                end_date,
                risk_level,
                status,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Draft', ?, ?)
            """,
            (
                plan_number,
                title,
                audit_type,
                department,
                objective,
                scope,
                methodology,
                start_date,
                end_date,
                risk_level,
                created_by,
                utc_now()
            )
        )

        db.commit()

        return cursor.lastrowid

    except Exception:

        db.rollback()
        raise

    finally:
        db.close()


# -------------------------------------------------------
# AUDIT PLAN RETRIEVAL
# -------------------------------------------------------

def get_audit_plans(department=None, status=None):

    db = get_audit_connection()

    try:

        query = """
            SELECT *
            FROM audit_plans
            WHERE 1 = 1
        """

        params = []

        if department:

            query += " AND department = ?"
            params.append(department)

        if status:

            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC"

        rows = db.execute(
            query,
            params
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        db.close()


# -------------------------------------------------------
# RISK SCORE CALCULATION
# -------------------------------------------------------

def calculate_risk_score(likelihood, impact):
    """
    Calculate risk using a 1-5 likelihood and impact scale.
    """

    likelihood = int(likelihood)
    impact = int(impact)

    if not 1 <= likelihood <= 5:
        raise ValueError(
            "Likelihood must be between 1 and 5."
        )

    if not 1 <= impact <= 5:
        raise ValueError(
            "Impact must be between 1 and 5."
        )

    score = likelihood * impact

    if score >= 15:
        level = "High"

    elif score >= 8:
        level = "Medium"

    else:
        level = "Low"

    return {
        "score": score,
        "level": level
    }


# -------------------------------------------------------
# CREATE RISK ASSESSMENT
# -------------------------------------------------------

def create_risk_assessment(
    audit_plan_id,
    risk_title,
    likelihood,
    impact,
    assessed_by,
    risk_description=None,
    mitigation_plan=None,
    responsible_person=None
):

    risk = calculate_risk_score(
        likelihood,
        impact
    )

    db = get_audit_connection()

    try:

        cursor = db.execute(
            """
            INSERT INTO audit_risk_assessments (
                audit_plan_id,
                risk_title,
                risk_description,
                likelihood,
                impact,
                risk_score,
                risk_level,
                mitigation_plan,
                responsible_person,
                assessed_by,
                assessed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_plan_id,
                risk_title,
                risk_description,
                likelihood,
                impact,
                risk["score"],
                risk["level"],
                mitigation_plan,
                responsible_person,
                assessed_by,
                utc_now()
            )
        )

        db.commit()

        return cursor.lastrowid

    except Exception:

        db.rollback()
        raise

    finally:
        db.close()


# -------------------------------------------------------
# AUDIT DASHBOARD STATISTICS
# -------------------------------------------------------

def get_audit_statistics():

    db = get_audit_connection()

    try:

        statistics = {}

        statistics["total_plans"] = db.execute(
            "SELECT COUNT(*) FROM audit_plans"
        ).fetchone()[0]

        statistics["active_plans"] = db.execute(
            """
            SELECT COUNT(*)
            FROM audit_plans
            WHERE status IN ('Approved', 'In Progress')
            """
        ).fetchone()[0]

        statistics["completed_plans"] = db.execute(
            """
            SELECT COUNT(*)
            FROM audit_plans
            WHERE status = 'Completed'
            """
        ).fetchone()[0]

        statistics["open_risks"] = db.execute(
            """
            SELECT COUNT(*)
            FROM audit_risk_assessments
            WHERE status = 'Open'
            """
        ).fetchone()[0]

        statistics["corrective_actions"] = db.execute(
            """
            SELECT COUNT(*)
            FROM audit_corrective_actions
            WHERE status != 'Completed'
            """
        ).fetchone()[0]

        statistics["audit_reports"] = db.execute(
            """
            SELECT COUNT(*)
            FROM audit_report_records
            """
        ).fetchone()[0]

        return statistics

    finally:
        db.close()


# -------------------------------------------------------
# END OF AUDIT MODELS
# -------------------------------------------------------
