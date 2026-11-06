
"""
Where every database file lives, and how one request reaches all of them.

Each business module owns one SQLite file under database/.
The user database is the main connection. Other module databases are
attached only when required.

This keeps database access centralized and avoids attaching every
module database to one connection.
"""

from pathlib import Path
import os
import re
import sqlite3


# -------------------------------------------------------
# DATABASE PATHS
# -------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_DIR = Path(__file__).resolve().parent

BACKUP_DIR = Path(
    os.environ.get(
        "WK_BACKUP_DIR",
        str(DATABASE_DIR / "Backups")
    )
).resolve()

# SQLite supports a maximum of 10 attached databases.
MAX_ATTACHED = 10


# -------------------------------------------------------
# DATABASE MODULE CONFIGURATION
# -------------------------------------------------------

MODULES = (

    # ---------------------------------------------------
    # USER MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "user",
        "label": "User",
        "folder": "User_Database",
        "file": "user.db",
        "schema": "main",
        "env": "WK_DB_PATH",
        "note": "Accounts, authentication and permissions.",
        "tables": (
            "users",
            "login_attempts",
            "password_reset_requests",
            "password_reset_tokens",
            "username_sequence",
            "user_roles",
            "user_permissions",
            "user_permission_grants",
        ),
    },

    # ---------------------------------------------------
    # SHARED DATABASE
    # ---------------------------------------------------

    {
        "key": "shared",
        "label": "Shared",
        "folder": "Shared_Database",
        "file": "shared.db",
        "schema": "shared",
        "env": "WK_SHARED_DB_PATH",
        "note": "Shared roles, workflows, notifications and audit records.",
        "tables": (
            "roles",
            "role_permissions",
            "role_permission_grants",
            "department_roles",
            "hiring_role_permissions",
            "hiring_user_permissions",

            "approval_requests",
            "approval_actions",
            "approval_routes",
            "management_approval_policy",

            "notifications",
            "email_deliveries",
            "announcements",

            # Existing audit tables
            "audit_logs",
            "audit_activity",
            "audit_reviews",
            "audit_findings",

            # Audit Management additions
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
        ),
    },

    # ---------------------------------------------------
    # HUMAN RESOURCE MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "hr",
        "label": "HR",
        "folder": "HR_Database",
        "file": "hr.db",
        "schema": "hr",
        "env": "WK_HR_DB_PATH",
        "note": "Employees, departments, attendance and leave.",
        "tables": (
            "departments",
            "organizational_positions",
            "employees",
            "attendance",
            "leave_requests",
            "employee_training",
            "hr_settings",
            "hr_overtime_requests",
            "hr_overtime_audit_logs",
        ),
    },

    # ---------------------------------------------------
    # PAYROLL MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "payroll",
        "label": "Payroll",
        "folder": "Payroll_Database",
        "file": "payroll.db",
        "schema": "payroll",
        "env": "WK_PAYROLL_DB_PATH",
        "note": "Salary structures, payroll and statutory deductions.",
        "tables": (
            "payroll",
            "payroll_salary_grades",
            "payroll_salary_structures",
            "payroll_employee_profiles",
            "payroll_allowance_types",
            "payroll_employee_allowances",
            "payroll_deduction_types",
            "payroll_employee_deductions",
            "payroll_salary_history",
            "payroll_salary_approvals",
            "payroll_records",
            "payroll_payments",
            "payroll_statutory_rules",
            "payroll_statutory_audit",
            "payroll_tax_brackets",
            "payroll_statutory_breakdown",
            "payroll_overtime_imports",
        ),
    },

    # ---------------------------------------------------
    # PERFORMANCE MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "performance",
        "label": "Performance",
        "folder": "Performance_Database",
        "file": "performance.db",
        "schema": "performance",
        "env": "WK_PERFORMANCE_DB_PATH",
        "note": "Employee performance reviews and goals.",
        "tables": (
            "performance_reviews",
        ),
    },

    # ---------------------------------------------------
    # INVENTORY AND STOCK MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "stock",
        "label": "Stock",
        "folder": "Stock_Database",
        "file": "stock.db",
        "schema": "stock",
        "env": "WK_STOCK_DB_PATH",
        "note": "Inventory items, stock movements and stocktakes.",
        "tables": (
            "inventory_items",
            "stock_movements",
            "inventory_stocktakes",
            "inventory_stocktake_lines",
        ),
    },

    # ---------------------------------------------------
    # FIXED ASSET MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "asset",
        "label": "Asset Management",
        "folder": "Asset_Database",
        "file": "asset.db",
        "schema": "asset",
        "env": "WK_ASSET_DB_PATH",
        "note": "Fixed assets, categories and allocations.",
        "tables": (
            "asset_categories",
            "assets",
            "asset_allocations",
        ),
    },

    # ---------------------------------------------------
    # VACANCY AND RECRUITMENT
    # ---------------------------------------------------

    {
        "key": "vacancy",
        "label": "Vacancy",
        "folder": "Vacancy_Database",
        "file": "vacancy.db",
        "schema": "vacancy",
        "env": "WK_VACANCY_DB_PATH",
        "note": "Vacancies, applications and interviews.",
        "tables": (
            "job_vacancies",
            "job_applications",
            "recruitment_interviews",
            "interview_panel",
            "interview_scores",
        ),
    },

    # ---------------------------------------------------
    # EXAM MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "exam",
        "label": "Exam",
        "folder": "Exam_Database",
        "file": "exam.db",
        "schema": "exam",
        "env": "WK_EXAM_DB_PATH",
        "note": "Recruitment examinations and candidate attempts.",
        "tables": (
            "recruitment_exams",
            "exam_questions",
            "exam_attempts",
            "exam_answers",
        ),
    },

    # ---------------------------------------------------
    # SYSTEM AND DIRECTORY INTEGRATION
    # ---------------------------------------------------

    {
        "key": "integration",
        "label": "Integration",
        "folder": "Integration_Database",
        "file": "integration.db",
        "schema": "integration",
        "env": "WK_INTEGRATION_DB_PATH",
        "note": "System integrations and directory synchronization.",
        "tables": (
            "integrations",
            "integration_ad_users",
            "integration_connection_tests",
            "integration_credentials",
            "integration_department_mappings",
            "integration_employee_mappings",
            "integration_errors",
            "integration_sync_logs",
            "directory_connections",
            "directory_registry",
            "directory_group_mappings",
            "directory_sync_runs",
            "directory_sync_items",
        ),
    },

    # ---------------------------------------------------
    # BUDGET MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "budget",
        "label": "Budget",
        "folder": "Budget_Database",
        "file": "budget.db",
        "schema": "budget",
        "env": "WK_BUDGET_DB_PATH",
        "note": "Budget plans, allocations and expenditures.",
        "tables": (
            "budget_plans",
            "budget_allocations",
            "budget_requests",
            "budget_approvals",
            "budget_commitments",
            "budget_expenditures",
        ),
    },

    # ---------------------------------------------------
    # FINANCE AND PROCUREMENT
    # ---------------------------------------------------

    {
        "key": "finance",
        "label": "Finance and Procurement",
        "folder": "Finance_and_Procurement",
        "file": "finance.db",
        "schema": "finance",
        "env": "WK_FINANCE_DB_PATH",
        "note": "Financial transactions, invoices, payments and procurement.",
        "tables": (
            "finance_transactions",
            "finance_invoices",
            "finance_payments",
            "procurement_suppliers",
            "procurement_requests",
            "procurement_orders",
            "procurement_request_items",
            "procurement_deliveries",
        ),
    },

    # ---------------------------------------------------
    # LETTER MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "letter",
        "label": "Letters",
        "folder": "Letter_Database",
        "file": "letter.db",
        "schema": "letter",
        "env": "WK_LETTERS_DB_PATH",
        "note": "Letters, approvals, deliveries and audit history.",
        "tables": (
            "letters",
            "letter_permissions",
            "letter_category_permissions",
            "letter_role_permissions",
            "letter_deliveries",
            "letter_reads",
            "letter_approval_steps",
            "letter_audit_logs",
        ),
    },

    # ---------------------------------------------------
    # MARKETING MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "marketing",
        "label": "Marketing",
        "folder": "Marketing_Database",
        "file": "marketing.db",
        "schema": "marketing",
        "env": "WK_MARKETING_DB_PATH",
        "note": "Marketing plans, campaigns, leads and research.",
        "tables": (
            "marketing_plans",
            "marketing_campaigns",
            "marketing_leads",
            "market_research",
            "digital_campaigns",
            "customer_feedback",
        ),
    },

    # ---------------------------------------------------
    # PROJECT MANAGEMENT
    # ---------------------------------------------------

    {
        "key": "project",
        "label": "Project Management",
        "folder": "Project_Database",
        "file": "project.db",
        "schema": "project",
        "env": "WK_PROJECT_DB_PATH",
        "note": "Projects, project members, milestones and tasks.",
        "tables": (
            "projects",
            "project_members",
            "project_milestones",
            "project_tasks",
        ),
    },
)


# -------------------------------------------------------
# MODULE LOOKUP CONFIGURATION
# -------------------------------------------------------

MODULE_BY_KEY = {
    module["key"]: module
    for module in MODULES
}

SCHEMA_MODULE = {
    module["schema"]: module["key"]
    for module in MODULES
}

ATTACHED_SCHEMAS = tuple(
    module["schema"]
    for module in MODULES
    if module["schema"] != "main"
)


# -------------------------------------------------------
# TABLE OWNERSHIP MAPPING
# -------------------------------------------------------

TABLE_SCHEMA = {}
MODULE_BY_TABLE = {}

for _module in MODULES:
    for _table in _module["tables"]:

        if _table in TABLE_SCHEMA:
            raise RuntimeError(
                "{} is claimed by two modules".format(_table)
            )

        TABLE_SCHEMA[_table] = _module["schema"]
        MODULE_BY_TABLE[_table] = _module["key"]


# -------------------------------------------------------
# DATABASE PATH HELPERS
# -------------------------------------------------------

def module_path(key):
    """Return the database path for a module."""

    module = MODULE_BY_KEY[key]

    override = os.environ.get(
        module["env"], ""
    ).strip()

    if override:
        return Path(override).resolve()

    return (
        DATABASE_DIR
        / module["folder"]
        / module["file"]
    ).resolve()


def path_for_table(table):
    """Return the database path owning a table."""

    return module_path(
        MODULE_BY_TABLE[table]
    )


DB_PATH = module_path("user")
USER_DB_PATH = DB_PATH


def database_files():
    """Return all module database paths."""

    return [
        (
            module["key"],
            module_path(module["key"])
        )
        for module in MODULES
    ]

# -------------------------------------------------------
# SQL STATEMENT REWRITING
# -------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9_$]+")
_QUOTES = "'\"`"


def _tokens(sql):
    """Split SQL into tokens without modifying quoted strings or comments."""

    tokens = []
    index = 0
    size = len(sql)

    while index < size:
        char = sql[index]

        if char in _QUOTES:
            end = index + 1

            while end < size:
                if sql[end] == char:
                    if end + 1 < size and sql[end + 1] == char:
                        end += 2
                        continue

                    end += 1
                    break

                end += 1

            tokens.append(("lit", sql[index:end], index, end))
            index = end

        elif char == "[":
            close = sql.find("]", index + 1)
            end = size if close == -1 else close + 1

            tokens.append(("lit", sql[index:end], index, end))
            index = end

        elif sql.startswith("--", index):
            end = sql.find("\n", index)
            end = size if end == -1 else end

            tokens.append(("ws", sql[index:end], index, end))
            index = end

        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = size if end == -1 else end + 2

            tokens.append(("ws", sql[index:end], index, end))
            index = end

        elif char.isalnum() or char in "_$":
            match = _WORD.match(sql, index)
            end = match.end()

            tokens.append(("word", sql[index:end], index, end))
            index = end

        elif char.isspace():
            end = index

            while end < size and sql[end].isspace():
                end += 1

            tokens.append(("ws", sql[index:end], index, end))
            index = end

        else:
            tokens.append(("punct", char, index, index + 1))
            index += 1

    return tokens


def _words(tokens):
    return [
        token[1].lower()
        for token in tokens
        if token[0] == "word"
    ]


def _text(tokens):
    return "".join(
        token[1]
        for token in tokens
    )


def _previous(word_index, tokens, position):
    """Return the previous non-whitespace token."""

    for index in range(position - 1, -1, -1):
        if tokens[index][0] != "ws":
            return index

    return -1


def _next(position, tokens):
    """Return the next non-whitespace token."""

    for index in range(position + 1, len(tokens)):
        if tokens[index][0] != "ws":
            return index

    return -1


def _name_schema(text):
    """Find the database schema owning a table name."""

    if "." in text:
        return text.split(".", 1)[0].strip(
            "'\"`["
        ).lower()

    return TABLE_SCHEMA.get(text.lower())


def _statement_table_schema(tokens):
    """Identify the target schema of CREATE TABLE."""

    for index, token in enumerate(tokens):

        if (
            token[0] == "word"
            and token[1].lower() == "table"
        ):
            name = _next(index, tokens)

            if name == -1:
                return None

            if (
                tokens[name][0] == "word"
                and _next(name, tokens) != -1
            ):
                following = tokens[_next(name, tokens)]

                if (
                    following[0] == "punct"
                    and following[1] == "."
                ):
                    return tokens[name][1].lower()

            return _name_schema(tokens[name][1])

    return None


# -------------------------------------------------------
# CROSS-DATABASE FOREIGN KEY HANDLING
# -------------------------------------------------------

def _strip_foreign_keys(tokens, schema=None):
    """
    Remove foreign-key constraints referencing another database.

    SQLite does not support enforced foreign keys between attached
    database files. Cross-module relationships must therefore be
    checked by application services.
    """

    target = schema or _statement_table_schema(tokens)

    if target is None:
        return tokens

    drop = set()
    index = 0

    while index < len(tokens):

        kind, text, _start, _end = tokens[index]

        if kind != "word" or index in drop:
            index += 1
            continue

        word = text.lower()

        if word not in ("references", "foreign"):
            index += 1
            continue

        start = index

        if word == "foreign":

            key = _next(index, tokens)

            if (
                key == -1
                or tokens[key][1].lower() != "key"
            ):
                index += 1
                continue

            reference = None
            probe = key

            while probe != -1:

                probe = _next(probe, tokens)

                if probe == -1:
                    break

                if (
                    tokens[probe][0] == "word"
                    and tokens[probe][1].lower() == "references"
                ):
                    reference = probe
                    break

            name = (
                _next(reference, tokens)
                if reference is not None
                else -1
            )

        else:
            name = _next(index, tokens)

        if (
            name == -1
            or tokens[name][0] != "word"
        ):
            index += 1
            continue

        referenced_schema = _name_schema(
            tokens[name][1]
        )

        if referenced_schema in (None, target):
            index += 1
            continue

        # Find the end of the constraint.
        depth = 1
        end = len(tokens) - 1

        for probe in range(start, len(tokens)):

            token = tokens[probe]

            if token[0] != "punct":
                continue

            if token[1] == "(":
                depth += 1

            elif token[1] == ")":
                depth -= 1

                if depth == 0:
                    end = probe - 1
                    break

            elif token[1] == "," and depth == 1:
                end = probe - 1
                break

        before = _previous(start, tokens, start)
        after = _next(end, tokens)

        if (
            before != -1
            and tokens[before][0] == "punct"
            and tokens[before][1] == ","
        ):
            start = before

        elif (
            before != -1
            and tokens[before][0] == "punct"
            and tokens[before][1] == "("
        ):
            if (
                after != -1
                and tokens[after][0] == "punct"
                and tokens[after][1] == ","
            ):
                end = after

        drop.update(range(start, end + 1))
        index = end + 1

    if not drop:
        return tokens

    kept = [
        token
        for position, token in enumerate(tokens)
        if position not in drop
    ]

    cleaned = []

    for position, token in enumerate(kept):

        if (
            token[0] == "punct"
            and token[1] == ","
        ):
            following = _next(position, kept)

            if (
                following != -1
                and kept[following][0] == "punct"
                and kept[following][1] == ")"
            ):
                continue

        cleaned.append(token)

    return cleaned


def strip_foreign_keys(sql, schema=None):
    """Public helper for module schema creation."""

    return _text(
        _strip_foreign_keys(
            _tokens(sql),
            schema
        )
    )


# -------------------------------------------------------
# AUTOMATIC TABLE QUALIFICATION
# -------------------------------------------------------

def _qualify_words(tokens):
    """Prefix recognized table names with their database schema."""

    out = []

    previous = ""
    before_that = ""

    for index, token in enumerate(tokens):

        kind, text, start, end = token

        if kind != "word":
            out.append(token)
            continue

        lower = text.lower()

        before = (
            index > 0
            and tokens[index - 1][0] == "punct"
            and tokens[index - 1][1] == "."
        )

        after = (
            index + 1 < len(tokens)
            and tokens[index + 1][0] == "punct"
            and tokens[index + 1][1] == "."
        )

        schema = TABLE_SCHEMA.get(lower)

        bare_here = (
            previous == "references"
            or previous in ("as", "with", "recursive")
            or (
                previous == "to"
                and before_that == "rename"
            )
        )

        if (
            schema
            and schema != "main"
            and not before
            and not after
            and not bare_here
        ):
            out.append((
                kind,
                "{}.{}".format(schema, text),
                start,
                end
            ))
        else:
            out.append(token)

        before_that = previous
        previous = lower

    return out


# -------------------------------------------------------
# PRAGMA REWRITING
# -------------------------------------------------------

def _rewrite_pragma(tokens):
    """
    Convert PRAGMA table_info(table_name) into the correct
    schema-specific PRAGMA when the table belongs to a module.
    """

    index = next(
        (
            i for i, token in enumerate(tokens)
            if token[0] == "word"
        ),
        -1
    )

    if index == -1:
        return _text(tokens)

    name = _next(index, tokens)

    if (
        name == -1
        or tokens[name][0] != "word"
    ):
        return _text(tokens)

    following = _next(name, tokens)

    if (
        following != -1
        and tokens[following][0] == "punct"
        and tokens[following][1] == "."
    ):
        return _text(tokens)

    opening = _next(name, tokens)

    if (
        opening == -1
        or tokens[opening][1] != "("
    ):
        return _text(tokens)

    argument = _next(opening, tokens)

    if argument == -1:
        return _text(tokens)

    argument_text = tokens[argument][1]

    if tokens[argument][0] == "lit":
        argument_text = argument_text.strip(
            "\"'`[]"
        )

    elif tokens[argument][0] != "word":
        return _text(tokens)

    schema = TABLE_SCHEMA.get(
        argument_text.lower()
    )

    if not schema or schema == "main":
        return _text(tokens)

    tokens = list(tokens)

    tokens[name] = (
        "word",
        "{}.{}".format(schema, tokens[name][1]),
        tokens[name][2],
        tokens[name][3]
    )

    return _text(tokens)


# -------------------------------------------------------
# CREATE INDEX REWRITING
# -------------------------------------------------------

def _rewrite_create_index(tokens):

    words = _words(tokens)

    if "index" not in words:
        return _qualify_words(tokens)

    cursor = next(
        (
            i for i, token in enumerate(tokens)
            if token[0] == "word"
            and token[1].lower() == "index"
        ),
        -1
    )

    name = _next(cursor, tokens)

    while (
        name != -1
        and tokens[name][0] == "word"
        and tokens[name][1].lower()
        in ("if", "not", "exists")
    ):
        name = _next(name, tokens)

    if (
        name == -1
        or tokens[name][0] != "word"
    ):
        return _qualify_words(tokens)

    following_name = _next(name, tokens)

    if (
        following_name != -1
        and tokens[following_name][0] == "punct"
        and tokens[following_name][1] == "."
    ):
        return tokens

    on = next(
        (
            i for i, token in enumerate(tokens)
            if token[0] == "word"
            and token[1].lower() == "on"
            and i > name
        ),
        -1
    )

    if on == -1:
        return _qualify_words(tokens)

    table = _next(on, tokens)

    if table == -1:
        return _qualify_words(tokens)

    table_name = tokens[table][1]

    following = _next(table, tokens)

    if (
        following != -1
        and tokens[following][0] == "punct"
        and tokens[following][1] == "."
    ):
        next_table = _next(following, tokens)

        if next_table != -1:
            table_name = "{}.{}".format(
                table_name,
                tokens[next_table][1]
            )

    schema = _name_schema(table_name)

    if not schema or schema == "main":
        return _qualify_words(tokens)

    tokens = list(tokens)

    if "." not in tokens[name][1]:

        tokens[name] = (
            "word",
            "{}.{}".format(schema, tokens[name][1]),
            tokens[name][2],
            tokens[name][3]
        )

    return tokens


# -------------------------------------------------------
# CREATE TRIGGER REWRITING
# -------------------------------------------------------

def _rewrite_create_trigger(tokens):

    index = next(
        (
            i for i, token in enumerate(tokens)
            if token[0] == "word"
            and token[1].lower() == "trigger"
        ),
        -1
    )

    if index == -1:
        return _qualify_words(tokens)

    name = _next(index, tokens)

    while (
        name != -1
        and tokens[name][0] == "word"
        and tokens[name][1].lower()
        in ("if", "not", "exists")
    ):
        name = _next(name, tokens)

    on = next(
        (
            i for i, token in enumerate(tokens)
            if token[0] == "word"
            and token[1].lower() == "on"
            and i > name
        ),
        -1
    )

    table = _next(on, tokens) if on != -1 else -1

    if (
        name == -1
        or table == -1
        or tokens[name][0] != "word"
    ):
        return _qualify_words(tokens)

    following_name = _next(name, tokens)

    if (
        following_name != -1
        and tokens[following_name][0] == "punct"
        and tokens[following_name][1] == "."
    ):
        return tokens

    schema = _name_schema(tokens[table][1])

    tokens = _qualify_words(tokens)

    if (
        schema
        and schema != "main"
        and "." not in tokens[name][1]
    ):
        tokens[name] = (
            "word",
            "{}.{}".format(schema, tokens[name][1]),
            tokens[name][2],
            tokens[name][3]
        )

    return tokens


# -------------------------------------------------------
# SQL STATEMENT QUALIFICATION
# -------------------------------------------------------

def qualify_statement(sql):
    """Prepare one SQL statement for the multi-database connection."""

    tokens = _tokens(sql)
    words = _words(tokens)

    if not words:
        return sql

    first = words[0]

    if first == "pragma":
        return _rewrite_pragma(tokens)

    if first == "create":

        if "index" in words[1:5]:
            return _text(
                _rewrite_create_index(tokens)
            )

        if "trigger" in words[1:5]:
            return _text(
                _rewrite_create_trigger(tokens)
            )

        tokens = _qualify_words(tokens)

        if "table" in words[:5]:
            tokens = _strip_foreign_keys(tokens)

        return _text(tokens)

    return _text(
        _qualify_words(tokens)
    )

# -------------------------------------------------------
# SQL SCRIPT PROCESSING
# -------------------------------------------------------

def _split_statements(sql):
    """
    Split SQL scripts using top-level semicolons.

    Trigger bodies may contain their own semicolons,
    so trigger statements are handled separately.
    """

    tokens = _tokens(sql)

    statements = []
    current = []

    depth = 0
    trigger = False

    for token in tokens:

        kind = token[0]
        text = token[1].lower()

        if kind == "word" and depth == 0 and not trigger:

            if (
                text == "trigger"
                and current
                and "create" in _words(current)
            ):
                trigger = True

        elif kind == "punct":

            if text == "(":
                depth += 1

            elif text == ")":
                depth -= 1

            elif text == ";" and depth <= 0:

                if trigger:

                    if not any(
                        item[0] == "word"
                        and item[1].lower() == "end"
                        for item in current[-3:]
                    ):
                        current.append(token)
                        continue

                    trigger = False

                statements.append(
                    _text(current)
                )

                current = []
                depth = 0
                continue

        current.append(token)

    tail = _text(current)

    if tail.strip():
        statements.append(tail)

    return statements


def qualify_script(sql):
    """Qualify all statements in an SQL script."""

    parts = [
        qualify_statement(part)
        for part in _split_statements(sql)
    ]

    return ";\n".join(
        part.strip()
        for part in parts
        if part.strip()
    )


# -------------------------------------------------------
# DATABASE SCHEMA DETECTION
# -------------------------------------------------------

_SCHEMA_REF = re.compile(
    r"\b({})\s*\.".format(
        "|".join(
            re.escape(schema)
            for schema in sorted(
                ATTACHED_SCHEMAS,
                key=len,
                reverse=True
            )
        )
    ),
    re.IGNORECASE
)


def referenced_schemas(sql):
    """
    Return schemas explicitly referenced by qualified SQL names.
    """

    return {
        match.group(1).lower()
        for match in _SCHEMA_REF.finditer(sql)
    }


# -------------------------------------------------------
# TABLE QUALIFICATION HELPER
# -------------------------------------------------------

def qualified(table):
    """
    Return a schema-qualified table name.

    Main database tables remain unqualified.
    """

    schema = TABLE_SCHEMA.get(table.lower())

    if not schema or schema == "main":
        return table

    return "{}.{}".format(schema, table)


# -------------------------------------------------------
# TABLE EXISTENCE CHECK
# -------------------------------------------------------

def table_exists(db, table):
    """
    Check whether a table exists in its owning database.
    """

    schema = TABLE_SCHEMA.get(table.lower())

    if not schema or schema == "main":

        return db.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            """,
            (table,)
        ).fetchone() is not None

    ensure_attached(db, schema)

    return db.execute(
        """
        SELECT 1
        FROM {}.sqlite_master
        WHERE type = 'table'
          AND name = ?
        """.format(schema),
        (table,)
    ).fetchone() is not None


# -------------------------------------------------------
# CONNECTION CONFIGURATION
# -------------------------------------------------------

def _configure(conn):
    """Configure SQLite connection defaults."""

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    conn.execute(
        "PRAGMA busy_timeout = 15000"
    )

    conn.execute(
        "PRAGMA synchronous = NORMAL"
    )

    return conn


# -------------------------------------------------------
# DATABASE CONNECTION WRAPPER
# -------------------------------------------------------

class Connection:
    """
    SQLite connection wrapper.

    Automatically attaches the database modules referenced
    in SQL statements.

    Existing application code can continue using:

        db.execute()
        db.executemany()
        db.executescript()
        db.commit()
        db.rollback()
        db.close()
    """

    def __init__(self, db):

        self._db = db
        self._schemas = set()

    # ---------------------------------------------------
    # ATTACHMENT MANAGEMENT
    # ---------------------------------------------------

    def _refresh(self):

        self._schemas = {
            row[1]
            for row in self._db.execute(
                "PRAGMA database_list"
            )
        }

        return self._schemas

    def attach(self, schema):
        """Attach one module database when needed."""

        if schema in ("main", "temp"):
            return

        if schema not in self._schemas:
            self._refresh()

        if schema in self._schemas:
            return

        if schema not in SCHEMA_MODULE:
            raise ValueError(
                "Unknown database schema: {}".format(schema)
            )

        attached = len([
            name
            for name in self._schemas
            if name not in ("main", "temp")
        ])

        if attached >= MAX_ATTACHED:

            raise RuntimeError(
                "Connection already holds {} attached databases. "
                "SQLite allows {}. Read {} using its own connection "
                "instead.".format(
                    attached,
                    MAX_ATTACHED,
                    schema
                )
            )

        path = module_path(
            SCHEMA_MODULE[schema]
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self._db.execute(
            "ATTACH DATABASE ? AS {}".format(schema),
            (str(path),)
        )

        self._schemas.add(schema)

    # ---------------------------------------------------
    # SQL PREPARATION
    # ---------------------------------------------------

    def _prepare(self, sql):

        statement = qualify_statement(sql)

        for schema in referenced_schemas(statement):
            self.attach(schema)

        return statement

    # ---------------------------------------------------
    # SQL EXECUTION
    # ---------------------------------------------------

    def execute(self, sql, params=()):

        return self._db.execute(
            self._prepare(sql),
            params
        )

    def executemany(self, sql, params):

        return self._db.executemany(
            self._prepare(sql),
            params
        )

    def executescript(self, sql):

        script = qualify_script(sql)

        for schema in referenced_schemas(script):
            self.attach(schema)

        return self._db.executescript(script)

    # ---------------------------------------------------
    # CONTEXT MANAGER
    # ---------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback
    ):
        return self._db.__exit__(
            exc_type,
            exc_value,
            traceback
        )

    # ---------------------------------------------------
    # FORWARD OTHER SQLITE METHODS
    # ---------------------------------------------------

    def __getattr__(self, name):

        return getattr(
            self._db,
            name
        )

    def __repr__(self):

        return "<DatabaseConnection {}>".format(
            sorted(self._schemas)
        )


# -------------------------------------------------------
# MAIN DATABASE CONNECTION
# -------------------------------------------------------

def connect(path=None):
    """
    Open the main user database.

    Other module databases are attached automatically
    when their tables are referenced.
    """

    path = Path(
        path or module_path("user")
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    db = sqlite3.connect(
        str(path),
        timeout=15
    )

    return Connection(
        _configure(db)
    )


# -------------------------------------------------------
# DIRECT MODULE DATABASE CONNECTION
# -------------------------------------------------------

def connect_file(key):
    """
    Open one module database directly.

    This does not attach any other database.
    """

    if key not in MODULE_BY_KEY:
        raise ValueError(
            "Unknown database module: {}".format(key)
        )

    path = module_path(key)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    db = sqlite3.connect(
        str(path),
        timeout=15
    )

    return _configure(db)


# -------------------------------------------------------
# ENSURE DATABASE ATTACHMENT
# -------------------------------------------------------

def ensure_attached(db, schema):
    """
    Attach a module database to an existing connection.
    """

    if schema in ("main", "temp"):
        return

    if schema not in SCHEMA_MODULE:
        raise ValueError(
            "Unknown database schema: {}".format(schema)
        )

    if isinstance(db, Connection):

        db.attach(schema)
        return

    names = {
        row[1]
        for row in db.execute(
            "PRAGMA database_list"
        )
    }

    if schema in names:
        return

    attached = len([
        name
        for name in names
        if name not in ("main", "temp")
    ])

    if attached >= MAX_ATTACHED:
        raise RuntimeError(
            "Maximum attached database limit reached."
        )

    path = module_path(
        SCHEMA_MODULE[schema]
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    db.execute(
        "ATTACH DATABASE ? AS {}".format(schema),
        (str(path),)
    )


# -------------------------------------------------------
# ENABLE SQLITE WAL MODE
# -------------------------------------------------------

def enable_wal(key_or_path):
    """
    Enable Write-Ahead Logging for an existing database file.
    """

    if key_or_path in MODULE_BY_KEY:

        path = module_path(key_or_path)

    else:

        path = Path(key_or_path).resolve()

    if not path.exists():
        return False

    conn = sqlite3.connect(
        str(path),
        timeout=15
    )

    try:

        conn.execute(
            "PRAGMA journal_mode = WAL"
        )

        conn.execute(
            "PRAGMA synchronous = NORMAL"
        )

        return True

    finally:
        conn.close()


# -------------------------------------------------------
# DATABASE SNAPSHOT / BACKUP
# -------------------------------------------------------

def snapshot(source, target):
    """
    Create a consistent standalone SQLite snapshot.

    This copies one database file at a time.
    """

    source = sqlite3.connect(
        str(source),
        timeout=15
    )

    destination = sqlite3.connect(
        str(target),
        timeout=15
    )

    try:

        with destination:
            source.backup(destination)

    finally:

        destination.close()
        source.close()


# -------------------------------------------------------
# CROSS-MODULE TABLE COUNT
# -------------------------------------------------------

def table_count(table):
    """
    Count records in the database file owning a table.

    This opens one database file at a time and does not
    attach all modules to one connection.
    """

    if table not in MODULE_BY_TABLE:
        raise ValueError(
            "Unknown table: {}".format(table)
        )

    path = path_for_table(table)

    if not path.exists():
        return 0

    conn = sqlite3.connect(
        str(path),
        timeout=15
    )

    try:

        return conn.execute(
            'SELECT COUNT(*) FROM "{}"'.format(table)
        ).fetchone()[0]

    finally:
        conn.close()


# -------------------------------------------------------
# LIST ALL EXISTING MODULE TABLES
# -------------------------------------------------------

def all_table_names():
    """
    List tables across all module databases.

    Each database is inspected separately to respect
    SQLite's attached database limit.
    """

    names = set()

    for key, path in database_files():

        if not path.exists():
            continue

        conn = sqlite3.connect(
            str(path),
            timeout=15
        )

        try:

            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()

            names.update(
                row[0]
                for row in rows
            )

        finally:
            conn.close()

    return names


# -------------------------------------------------------
# DATABASE CONFIGURATION SUMMARY
# -------------------------------------------------------

def get_database_configuration():
    """
    Return information about configured modules.

    Useful for the Admin Database Monitoring page.
    """

    result = []

    for module in MODULES:

        path = module_path(
            module["key"]
        )

        result.append({
            "key": module["key"],
            "label": module["label"],
            "schema": module["schema"],
            "database_file": str(path),
            "exists": path.exists(),
            "tables": list(module["tables"]),
            "table_count": len(module["tables"]),
        })

    return result


# -------------------------------------------------------
# DATABASE HEALTH CHECK
# -------------------------------------------------------

def database_health_check():
    """
    Check whether configured database files are accessible.

    This is a basic file-level check, not a full integrity
    check or verification of every module's schema.
    """

    results = []

    for module in MODULES:

        path = module_path(
            module["key"]
        )

        status = {
            "module": module["label"],
            "key": module["key"],
            "path": str(path),
            "exists": path.exists(),
            "status": "Missing",
        }

        if path.exists():

            try:

                conn = sqlite3.connect(
                    str(path),
                    timeout=10
                )

                try:

                    integrity = conn.execute(
                        "PRAGMA quick_check"
                    ).fetchone()[0]

                    status["status"] = (
                        "Healthy"
                        if integrity == "ok"
                        else "Integrity warning"
                    )

                    status["integrity"] = integrity

                finally:
                    conn.close()

            except sqlite3.Error as exc:

                status["status"] = "Connection error"
                status["error"] = str(exc)

        results.append(status)

    return results


"""
=========================================================
AUDIT MANAGEMENT - READ-ONLY DATABASE CONNECTION PROVIDER
Dereje Wood & Kitchen Cabinet
=========================================================

Provides read-only connections to existing departmental
SQLite databases.

Does not create, modify, or delete departmental records.
"""

import os
import sqlite3
from contextlib import contextmanager


# -------------------------------------------------------
# AUDIT DATABASE MODULE MAPPING
# -------------------------------------------------------

AUDIT_MODULE_DATABASES = {
    "user": "user",
    "shared": "shared",
    "hr": "hr",
    "payroll": "payroll",
    "performance": "performance",
    "stock": "stock",
    "asset": "asset",
    "vacancy": "vacancy",
    "exam": "exam",
    "integration": "integration",
    "budget": "budget",
    "finance": "finance",
    "letter": "letter",
    "marketing": "marketing",
    "project": "project",
}


# -------------------------------------------------------
# RESOLVE DATABASE PATH
# -------------------------------------------------------

def get_audit_database_path(module_name):
    """
    Resolve the existing database path for a module.

    Uses the existing database registry where available.
    """

    if module_name not in AUDIT_MODULE_DATABASES:
        raise ValueError(
            f"Unregistered audit database module: {module_name}"
        )

    # Prefer the existing path resolver from Database-config.py.
    existing_resolver = globals().get("database_path")

    if callable(existing_resolver):
        path = existing_resolver(module_name)
        if path and os.path.isfile(path):
            return os.path.abspath(path)

    # Support an existing MODULES registry.
    modules = globals().get("MODULES")

    if isinstance(modules, dict):
        config = modules.get(module_name)

        if isinstance(config, dict):
            path = (
                config.get("path")
                or config.get("database")
                or config.get("db_path")
            )

            if path:
                resolved = os.path.abspath(path)
                if os.path.isfile(resolved):
                    return resolved

    # Support an existing path mapping.
    database_paths = globals().get("DATABASE_PATHS")

    if isinstance(database_paths, dict):
        path = database_paths.get(module_name)

        if path:
            resolved = os.path.abspath(path)
            if os.path.isfile(resolved):
                return resolved

    raise FileNotFoundError(
        f"Could not resolve the existing database path "
        f"for module '{module_name}'. "
        "Connect this function to your existing database registry."
    )


# -------------------------------------------------------
# READ-ONLY CONNECTION
# -------------------------------------------------------

def connect_audit_readonly(module_name):
    """
    Open an existing departmental database in SQLite
    read-only mode.

    Returns:
        sqlite3.Connection
    """

    database_path = get_audit_database_path(module_name)

    # SQLite URI mode=ro prevents database writes.
    database_uri = (
        "file:"
        + database_path.replace("\\", "/")
        + "?mode=ro"
    )

    connection = sqlite3.connect(
        database_uri,
        uri=True,
        timeout=15
    )

    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA query_only = ON")

        # Confirm read-only enforcement.
        result = connection.execute(
            "PRAGMA query_only"
        ).fetchone()

        if not result or result[0] != 1:
            raise RuntimeError(
                "Could not enforce read-only database access."
            )

        return connection

    except Exception:
        connection.close()
        raise


# -------------------------------------------------------
# AUDIT CONNECTION PROVIDER
# -------------------------------------------------------

def audit_connection_provider(module_name):
    """
    Connection provider expected by:
        departments.audit.integrations.read_only
    """

    return connect_audit_readonly(module_name)


# -------------------------------------------------------
# CONNECTION HEALTH CHECK
# -------------------------------------------------------

def check_audit_database_connection(module_name):
    """
    Test whether a departmental database is accessible.
    """

    connection = None

    try:
        connection = connect_audit_readonly(module_name)

        connection.execute("SELECT 1").fetchone()

        return {
            "module": module_name,
            "connected": True,
            "read_only": True,
            "error": None,
        }

    except Exception:
        return {
            "module": module_name,
            "connected": False,
            "read_only": True,
            "error": "Database connection unavailable.",
        }

    finally:
        if connection is not None:
            connection.close()


# -------------------------------------------------------
# ALL DEPARTMENT CONNECTION STATUS
# -------------------------------------------------------

def get_all_audit_database_status():
    """
    Return connection status for registered modules.
    """

    results = []

    for module_name in AUDIT_MODULE_DATABASES:
        results.append(
            check_audit_database_connection(module_name)
        )

    return results

# -------------------------------------------------------
# END OF DATABASE CONFIGURATION
# -------------------------------------------------------
