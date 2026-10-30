"""Cross-module search helpers."""

from flask import url_for


SEARCH_SCOPES = (
    # module gate, group title, table, columns to match, row renderer
    (("hr", "accounts"), "Employees", "employees",
     ("full_name", "employee_code", "department", "job_title")),
    (("accounts",), "Sign-in accounts", "users",
     ("full_name", "email", "phone")),
    (("inventory",), "Inventory items", "inventory_items",
     ("name", "sku", "category", "supplier")),
    (("procurement",), "Suppliers", "procurement_suppliers",
     ("name", "contact_person", "email")),
    (("procurement",), "Purchase requests", "procurement_requests",
     ("request_number", "item_name", "requested_by_name")),
    (("finance",), "Transactions", "finance_transactions",
     ("description", "category", "party_name")),
    (("finance",), "Invoices", "finance_invoices",
     ("invoice_number", "party_name", "status")),
    (("approvals",), "Approval requests", "approval_requests",
     ("title", "department", "created_by_name")),
    (("letters",), "Letters", "letters",
     ("reference_no", "subject", "sender", "recipient")),
    (("audit", "accounts"), "Activity log", "audit_logs",
     ("description", "username", "module")),
)


def search_scope_where(table, db, user):
    """(sql, params) narrowing one scope to what this account may open."""
    if table == "letters":
        from services.letters import letter_visibility_sql
        return letter_visibility_sql(db, user, alias="letters")
    return "", ()


def search_href(table, row):
    """Where a hit opens, matching the rail the account already uses."""
    if table == "employees":
        return url_for("edit_employee", employee_id=row["id"])
    if table == "users":
        return url_for("admin_role_account", user_id=row["id"])
    if table == "inventory_items":
        return url_for("inventory.edit_item", item_id=row["id"])
    if table == "finance_transactions":
        return url_for("finance.transactions", month=row["transaction_date"][:7])
    if table == "finance_invoices":
        return url_for("finance.invoices")
    if table == "approval_requests":
        return url_for("approvals.detail", request_id=row["id"])
    if table == "letters":
        return url_for("letters.detail", letter_id=row["id"])
    if table == "audit_logs":
        return url_for("audit.audit_dashboard", username=row["username"])
    return url_for("procurement.dashboard")


def search_title(row):
    for key in ("full_name", "name", "title", "description", "invoice_number",
                "request_number", "subject"):
        value = row[key] if key in row.keys() else None
        if value:
            return value
    return "(untitled)"
