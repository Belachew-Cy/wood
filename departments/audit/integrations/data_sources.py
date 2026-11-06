
"""
Read-only source registry for Audit Management.

The registry follows the module/table ownership map in the
existing database connection module.

It does not modify departmental transactions.
"""

AUDIT_DATA_SOURCES = {
    "User Management": {
        "module": "user",
        "tables": ["users", "user_roles", "user_permissions"],
    },
    "HR Management": {
        "module": "hr",
        "tables": [
            "departments",
            "organizational_positions",
            "employees",
            "attendance",
            "leave_requests",
            "employee_training",
        ],
    },
    "Payroll Management": {
        "module": "payroll",
        "tables": [
            "payroll",
            "payroll_records",
            "payroll_payments",
            "payroll_salary_approvals",
            "payroll_statutory_audit",
        ],
    },
    "Performance Management": {
        "module": "performance",
        "tables": ["performance_reviews"],
    },
    "Finance Management": {
        "module": "finance",
        "tables": [
            "finance_transactions",
            "finance_invoices",
            "finance_payments",
        ],
    },
    "Budget Management": {
        "module": "budget",
        "tables": [
            "budget_plans",
            "budget_allocations",
            "budget_requests",
            "budget_approvals",
            "budget_commitments",
            "budget_expenditures",
        ],
    },
    "Procurement Management": {
        "module": "finance",
        "tables": [
            "procurement_suppliers",
            "procurement_requests",
            "procurement_orders",
            "procurement_request_items",
            "procurement_deliveries",
        ],
    },
    "Inventory and Warehouse": {
        "module": "stock",
        "tables": [
            "inventory_items",
            "stock_movements",
            "inventory_stocktakes",
            "inventory_stocktake_lines",
        ],
    },
    "Asset Management": {
        "module": "asset",
        "tables": [
            "asset_categories",
            "assets",
            "asset_allocations",
        ],
    },
    "Marketing Management": {
        "module": "marketing",
        "tables": [
            "marketing_plans",
            "marketing_campaigns",
            "marketing_leads",
            "market_research",
            "digital_campaigns",
            "customer_feedback",
        ],
    },
    "Project Management": {
        "module": "project",
        "tables": [
            "projects",
            "project_members",
            "project_milestones",
            "project_tasks",
        ],
    },
    "System Integration": {
        "module": "integration",
        "tables": [
            "integrations",
            "integration_connection_tests",
            "integration_sync_logs",
            "integration_errors",
            "directory_connections",
            "directory_sync_runs",
            "directory_sync_items",
        ],
    },
    "Recruitment": {
        "module": "vacancy",
        "tables": [
            "job_vacancies",
            "job_applications",
            "recruitment_interviews",
            "interview_panel",
            "interview_scores",
        ],
    },
    "Recruitment Examination": {
        "module": "exam",
        "tables": [
            "recruitment_exams",
            "exam_questions",
            "exam_attempts",
            "exam_answers",
        ],
    },
    "Letter Management": {
        "module": "letter",
        "tables": [
            "letters",
            "letter_deliveries",
            "letter_reads",
            "letter_approval_steps",
            "letter_audit_logs",
        ],
    },
}


def get_department_audit_overview(table_count_fn):
    """
    Return a read-only overview of records by department.

    Pass the existing database.table_count function as table_count_fn.

    Each table is counted independently to avoid exceeding SQLite's
    attached-database limit.
    """

    overview = []

    for department, config in AUDIT_DATA_SOURCES.items():
        table_results = []
        total_records = 0

        for table_name in config["tables"]:
            try:
                count = int(table_count_fn(table_name))
                error = None
            except Exception:
                count = None
                error = "Unable to read table"

            if count is not None:
                total_records += count

            table_results.append({
                "table": table_name,
                "record_count": count,
                "error": error,
            })

        overview.append({
            "department": department,
            "module": config["module"],
            "total_records": total_records,
            "tables": table_results,
        })

    return overview
