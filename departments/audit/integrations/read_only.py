
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Read-Only Integration Layer

Provides controlled read-only access to existing departmental databases.

Security:
- Uses the existing AUDIT_DATA_SOURCES registry.
- Does not modify departmental records.
- Enables SQLite query_only mode.
- Validates module and table ownership.
- Validates filter and ordering columns.
- Uses parameterized SQL values.
- Closes database connections after every operation.
- Excludes sensitive authentication fields from returned records.

Connection provider:
Configure the existing database connection function during application
initialization using configure_connection_provider().
"""

import sqlite3

from departments.audit.data_source import AUDIT_DATA_SOURCES


# =========================================================
# SENSITIVE FIELDS
# =========================================================

SENSITIVE_COLUMNS = {
    "password",
    "password_hash",
    "hashed_password",
    "reset_token",
    "access_token",
    "refresh_token",
    "session_token",
    "api_key",
    "secret",
    "client_secret",
    "private_key",
    "security_answer",
}


# =========================================================
# CONNECTION PROVIDER
# =========================================================

_CONNECTION_PROVIDER = None


def configure_connection_provider(provider):
    """
    Register the existing departmental database connection function.

    Expected signature:
        provider(module_name) -> sqlite3.Connection
    """
    global _CONNECTION_PROVIDER

    if not callable(provider):
        raise TypeError("Database connection provider must be callable.")

    _CONNECTION_PROVIDER = provider


def get_connection_provider():
    """Return the configured connection provider."""

    if _CONNECTION_PROVIDER is None:
        raise RuntimeError(
            "Audit database provider is not configured. "
            "Configure it during application initialization."
        )

    return _CONNECTION_PROVIDER


# =========================================================
# SOURCE VALIDATION
# =========================================================

def get_source_config(module_name, table_name):
    """Verify that a table belongs to the requested audit module."""

    for department, config in AUDIT_DATA_SOURCES.items():

        if config.get("module") != module_name:
            continue

        if table_name in config.get("tables", []):
            return {
                "department": department,
                "module": module_name,
                "table": table_name,
            }

    raise ValueError(
        "The requested table is not registered for this module."
    )


def validate_identifier(identifier):
    """
    Validate an SQLite table or column identifier.

    SQL identifiers cannot be passed as ordinary SQL parameters.
    """

    if not isinstance(identifier, str) or not identifier:
        raise ValueError("Invalid SQL identifier.")

    if not (
        identifier[0].isalpha()
        or identifier[0] == "_"
    ):
        raise ValueError("Invalid SQL identifier.")

    if not all(
        character.isalnum() or character == "_"
        for character in identifier
    ):
        raise ValueError("Invalid SQL identifier.")

    return identifier


def _quote_identifier(identifier):
    """Validate and safely quote an SQLite identifier."""

    identifier = validate_identifier(identifier)
    return '"' + identifier + '"'


# =========================================================
# READ-ONLY CONNECTION
# =========================================================

def open_read_only_connection(module_name):
    """
    Open an existing departmental database connection and enable
    SQLite query-only mode.
    """

    if not any(
        config.get("module") == module_name
        for config in AUDIT_DATA_SOURCES.values()
    ):
        raise ValueError("Unregistered audit module.")

    provider = get_connection_provider()
    connection = provider(module_name)

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError(
            "The provider must return a sqlite3.Connection."
        )

    try:
        connection.execute("PRAGMA query_only = ON")

        result = connection.execute(
            "PRAGMA query_only"
        ).fetchone()

        if not result or int(result[0]) != 1:
            raise RuntimeError(
                "Unable to enable SQLite query-only mode."
            )

        return connection

    except Exception:
        connection.close()
        raise


# =========================================================
# TABLE SCHEMA
# =========================================================

def _get_columns_from_connection(connection, table_name):
    """Get the actual column names for a table."""

    table_name = validate_identifier(table_name)

    rows = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table_name)})"
    ).fetchall()

    return [
        row[1]
        for row in rows
    ]


def get_table_columns(module_name, table_name):
    """Return the actual columns of a registered table."""

    get_source_config(module_name, table_name)

    connection = open_read_only_connection(module_name)

    try:
        return _get_columns_from_connection(
            connection,
            table_name,
        )

    finally:
        connection.close()


# =========================================================
# FILTER VALIDATION
# =========================================================

def _build_filters(filters, columns):
    """Build safe equality and IS NULL conditions."""

    if filters is None:
        filters = {}

    if not isinstance(filters, dict):
        raise ValueError("Filters must be a dictionary.")

    conditions = []
    parameters = []

    for column, value in filters.items():

        column = validate_identifier(column)

        if column not in columns:
            raise ValueError(
                f"Unknown filter column: {column}"
            )

        quoted_column = _quote_identifier(column)

        if value is None:
            conditions.append(
                f"{quoted_column} IS NULL"
            )
        else:
            conditions.append(
                f"{quoted_column} = ?"
            )
            parameters.append(value)

    return conditions, parameters


# =========================================================
# READ RECORDS
# =========================================================

def read_records(
    module_name,
    table_name,
    filters=None,
    limit=100,
    offset=0,
    order_by=None,
    descending=False,
):
    """
    Read records from a registered departmental table.

    Example:
        read_records(
            "hr",
            "employees",
            filters={"status": "Active"},
            limit=50,
        )

    This function does not support INSERT, UPDATE or DELETE.
    """

    get_source_config(module_name, table_name)

    table_name = validate_identifier(table_name)

    try:
        limit = int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        raise ValueError("Limit and offset must be integers.")

    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000.")

    if offset < 0:
        raise ValueError("Offset cannot be negative.")

    connection = open_read_only_connection(module_name)

    try:
        columns = _get_columns_from_connection(
            connection,
            table_name,
        )

        if not columns:
            raise ValueError(
                "The registered table does not exist."
            )

        conditions, parameters = _build_filters(
            filters,
            set(columns),
        )

        # Never return authentication credentials or secrets.
        safe_columns = [
            column
            for column in columns
            if column.lower() not in SENSITIVE_COLUMNS
        ]

        if not safe_columns:
            return []

        selected_columns = ", ".join(
            _quote_identifier(column)
            for column in safe_columns
        )

        sql = (
            f"SELECT {selected_columns} "
            f"FROM {_quote_identifier(table_name)}"
        )

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        if order_by:
            order_by = validate_identifier(order_by)

            if order_by not in columns:
                raise ValueError(
                    "Unknown ordering column."
                )

            direction = "DESC" if descending else "ASC"

            sql += (
                f" ORDER BY {_quote_identifier(order_by)} "
                f"{direction}"
            )

        sql += " LIMIT ? OFFSET ?"

        parameters.extend([limit, offset])

        cursor = connection.execute(
            sql,
            parameters,
        )

        result_rows = cursor.fetchall()
        result_columns = [
            description[0]
            for description in cursor.description
        ]

        return [
            dict(zip(result_columns, row))
            for row in result_rows
        ]

    finally:
        connection.close()


# =========================================================
# COUNT RECORDS
# =========================================================

def count_records(
    module_name,
    table_name,
    filters=None,
):
    """Count records in a registered departmental table."""

    get_source_config(module_name, table_name)

    table_name = validate_identifier(table_name)

    connection = open_read_only_connection(module_name)

    try:
        columns = _get_columns_from_connection(
            connection,
            table_name,
        )

        if not columns:
            raise ValueError(
                "The registered table does not exist."
            )

        conditions, parameters = _build_filters(
            filters,
            set(columns),
        )

        sql = (
            f"SELECT COUNT(*) "
            f"FROM {_quote_identifier(table_name)}"
        )

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        result = connection.execute(
            sql,
            parameters,
        ).fetchone()

        return int(result[0])

    finally:
        connection.close()


# =========================================================
# REGISTERED SOURCE OVERVIEW
# =========================================================

def get_registered_source_overview():
    """
    Return configured audit sources.

    This describes configuration only. It does not claim
    that every registered table exists in its database.
    """

    return [
        {
            "department": department,
            "module": config["module"],
            "tables": list(config.get("tables", [])),
        }
        for department, config in AUDIT_DATA_SOURCES.items()
    ]


# =========================================================
# CONNECTION HEALTH CHECK
# =========================================================

def check_source_connection(module_name):
    """Check whether a registered departmental database is accessible."""

    connection = open_read_only_connection(module_name)

    try:
        connection.execute("SELECT 1").fetchone()

        return {
            "module": module_name,
            "connected": True,
            "read_only": True,
            "error": None,
        }

    finally:
        connection.close()


def check_all_source_connections():
    """Check every distinct registered module."""

    module_names = sorted({
        config["module"]
        for config in AUDIT_DATA_SOURCES.values()
    })

    results = []

    for module_name in module_names:
        try:
            results.append(
                check_source_connection(module_name)
            )

        except Exception as exc:
            results.append({
                "module": module_name,
                "connected": False,
                "read_only": False,
                "error": str(exc),
            })

    return results
