"""The central report catalogue and the scoping rules behind it.

Every report in the system is described once here: the SQL it runs, the
columns it returns, the module that may see it and the slice of the
organisation its rows are limited to. The /reports screen and both file
exports read this one catalogue, so a report cannot appear in a download after
the screen has hidden it, and a new report is a spec rather than a new route.

Scopes are decided per account:
  own        the staff record linked to the account
  team       the account's staff record plus everyone reporting up to it
  department the account's department, for accounts granted the module
  all        HR, payroll, finance, budget and procurement desks, and admins

A scope is never taken from the query string. The route asks for the spec, the
catalogue says which scope this account gets, and the SQL is built with that
scope bound as a parameter.
"""
import csv
import io
import sqlite3

GROUP_ORDER = ("My reports", "Workforce", "My team", "Administration",
               "Finance", "Budget", "Purchasing", "Inventory", "Payroll")

ROW_LIMIT = 500
EXPORT_LIMIT = 20000
FILTER_LIMIT = 150

HR_SCOPE = ("hr", "admin")
PAYROLL_SCOPE = ("payroll", "hr", "admin")
BUDGET_SCOPE = ("budget", "admin")
PROCUREMENT_SCOPE = ("procurement", "admin")


def _in(values):
    return "(" + ",".join("?" for _ in values) + ")"


def _employee_scope(ctx, scope, alias="e"):
    """A WHERE fragment over an employees row, and its bound parameters."""
    if scope == "all":
        return "", []
    if scope == "own":
        return " AND {}.id = ?".format(alias), [ctx["employee_id"]]
    if scope == "team":
        if not ctx["team_ids"]:
            return " AND 1 = 0", []
        return " AND {}.id IN {}".format(alias, _in(ctx["team_ids"])), list(ctx["team_ids"])
    if scope == "department":
        if not ctx["department"]:
            return " AND 1 = 0", []
        return (" AND {}.department COLLATE NOCASE = ? COLLATE NOCASE".format(alias),
                [ctx["department"]])
    return " AND 1 = 0", []


def _department_scope(ctx, scope, column):
    if scope == "all":
        return "", []
    if scope == "team":
        names = ctx["team_departments"]
        if not names:
            return " AND 1 = 0", []
        return (" AND {} COLLATE NOCASE IN ({})".format(
            column, ",".join("? COLLATE NOCASE" for _ in names)), list(names))
    if scope == "department":
        if not ctx["department"]:
            return " AND 1 = 0", []
        return (" AND {} COLLATE NOCASE = ? COLLATE NOCASE".format(column),
                [ctx["department"]])
    return " AND 1 = 0", []


def _approval_scope(ctx, scope):
    if scope == "all":
        return "", []
    if scope == "team":
        parts, params = [], []
        if ctx["team_user_ids"]:
            parts.append("r.created_by IN {}".format(_in(ctx["team_user_ids"])))
            params.extend(ctx["team_user_ids"])
        if ctx["team_departments"]:
            parts.append("r.department COLLATE NOCASE IN ({})".format(
                ",".join("? COLLATE NOCASE" for _ in ctx["team_departments"])))
            params.extend(ctx["team_departments"])
        if not parts:
            return " AND 1 = 0", []
        return " AND (" + " OR ".join(parts) + ")", params
    if scope == "department":
        if not ctx["department"]:
            return " AND 1 = 0", []
        return (" AND r.department COLLATE NOCASE = ? COLLATE NOCASE",
                [ctx["department"]])
    return " AND 1 = 0", []


# ------------------------------------------------------------------ SQL ----
def _roster_sql(ctx, scope, _start, _end, contact=False):
    clause, params = _employee_scope(ctx, scope)
    fields = ("e.employee_code AS code, e.full_name AS employee, e.department,"
              " e.job_title, e.employment_status AS status, e.employment_type,"
              " e.hire_date AS date, e.work_location,"
              " COALESCE(m.full_name, '') AS manager")
    if contact:
        fields += ", e.email, e.phone"
    return ("SELECT {} FROM employees e LEFT JOIN employees m ON m.id = e.manager_id"
            " WHERE 1 = 1{}".format(fields, clause), params)


def _attendance_sql(ctx, scope, _start, _end):
    clause, params = _employee_scope(ctx, scope)
    return ("SELECT a.attendance_date AS date, e.full_name AS employee,"
            " e.department, a.status, a.check_in, a.check_out, a.notes"
            " FROM attendance a JOIN employees e ON e.id = a.employee_id"
            " WHERE 1 = 1{}".format(clause), params)


def _leave_sql(ctx, scope, _start, _end):
    clause, params = _employee_scope(ctx, scope)
    return ("SELECT l.start_date AS date, e.full_name AS employee, e.department,"
            " l.leave_type, l.end_date, l.days_requested AS days, l.status,"
            " l.reason, l.reviewed_by, l.review_comment"
            " FROM leave_requests l JOIN employees e ON e.id = l.employee_id"
            " WHERE 1 = 1{}".format(clause), params)


def _performance_sql(ctx, scope, _start, _end):
    clause, params = _employee_scope(ctx, scope)
    return ("SELECT r.review_date AS date, e.full_name AS employee, e.department,"
            " r.review_period, r.rating, r.reviewer, r.strengths, r.improvements,"
            " r.goals"
            " FROM performance_reviews r JOIN employees e ON e.id = r.employee_id"
            " WHERE 1 = 1{}".format(clause), params)


def _leave_summary_sql(ctx, scope, start, end):
    clause, params = _employee_scope(ctx, scope)
    return ("""
        SELECT e.id AS employee_id, e.full_name AS employee, e.department,
               COUNT(l.id) AS requests,
               COALESCE(SUM(CASE WHEN l.status = 'Approved'
                                 THEN l.days_requested ELSE 0 END), 0) AS approved_days,
               COALESCE(SUM(CASE WHEN l.status = 'Pending'
                                 THEN l.days_requested ELSE 0 END), 0) AS pending_days,
               COALESCE(SUM(CASE WHEN l.status = 'Rejected'
                                 THEN l.days_requested ELSE 0 END), 0) AS rejected_days,
               COALESCE(SUM(l.days_requested), 0) AS total_days
        FROM employees e
        LEFT JOIN leave_requests l ON l.employee_id = e.id
             AND l.start_date BETWEEN ? AND ?
        WHERE 1 = 1{}
        GROUP BY e.id, e.full_name, e.department
    """.format(clause), [start or "0000-01-01", end or "9999-12-31"] + params)


def _approvals_sql(ctx, scope, _start, _end):
    clause, params = _approval_scope(ctx, scope)
    return ("SELECT r.created_at AS date, r.title, r.request_type, r.department,"
            " r.amount, r.status, r.current_approver_step AS step,"
            " r.created_by_name AS raised_by, r.description"
            " FROM approval_requests r WHERE 1 = 1{}".format(clause), params)


def _requisitions_sql(ctx, scope, _start, _end):
    clause, params = _department_scope(ctx, scope, "pr.department")
    return ("SELECT COALESCE(pr.submitted_at, pr.created_at) AS date,"
            " pr.request_number AS reference, pr.department, pr.item_name,"
            " pr.quantity, pr.unit, pr.estimated_cost AS estimated,"
            " s.name AS supplier, pr.requested_by_name AS raised_by, pr.status"
            " FROM finance.procurement_requests pr"
            " LEFT JOIN finance.procurement_suppliers s ON s.id = pr.supplier_id"
            " WHERE 1 = 1{}".format(clause), params)


def _system_usage_sql(_ctx, _scope, start, end):
    return ("""
        SELECT module, action, COUNT(*) AS events,
               COUNT(DISTINCT COALESCE(username, '')) AS users,
               SUM(CASE WHEN outcome IN ('DENIED', 'FAILURE')
                        THEN 1 ELSE 0 END) AS refusals,
               MIN(occurred_at) AS first_seen, MAX(occurred_at) AS last_seen
        FROM audit_logs
        WHERE occurred_at BETWEEN ? AND ?
        GROUP BY module, action
    """, [start + "T00:00:00" if start else "0000-01-01",
          end + "T23:59:59" if end else "9999-12-31T23:59:59"])


def _budget_allocation_sql(ctx, scope, _start, _end):
    clause, params = _department_scope(ctx, scope, "a.department")
    spent = ("(SELECT COALESCE(SUM(x.amount), 0) FROM budget_expenditures x"
             " WHERE x.allocation_id = a.id AND x.currency = a.currency)")
    return ("""
        SELECT a.allocation_name AS allocation, COALESCE(p.plan_name, '') AS plan,
               COALESCE(p.fiscal_year, '') AS year, a.department, a.currency,
               a.allocated_amount AS allocated, {} AS spent,
               a.allocated_amount - {} AS remaining, a.status,
               a.created_at AS date
        FROM budget_allocations a
        LEFT JOIN budget_plans p ON p.id = a.plan_id
        WHERE 1 = 1{}
    """.format(spent, spent, clause), params)


def _budget_expenditure_sql(ctx, scope, _start, _end):
    clause, params = _department_scope(ctx, scope, "a.department")
    return ("""
        SELECT e.created_at AS date, a.allocation_name AS allocation,
               a.department, e.description, e.amount, e.currency, e.reference,
               e.recorded_by_name AS recorded_by
        FROM budget_expenditures e
        JOIN budget_allocations a ON a.id = e.allocation_id
        WHERE 1 = 1{}
    """.format(clause), params)


def _finance_month_sql(_ctx, _scope, start, end):
    """Transactions rolled up by calendar month, narrowed before the grouping."""
    return ("""
        SELECT SUBSTR(t.transaction_date, 1, 7) AS month,
               COUNT(*) AS transactions,
               COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
                                 THEN t.amount_etb ELSE 0 END), 0) AS income,
               COALESCE(SUM(CASE WHEN t.transaction_type = 'expense'
                                 THEN t.amount_etb ELSE 0 END), 0) AS expense,
               COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
                                 THEN t.amount_etb ELSE -t.amount_etb END), 0) AS net
        FROM finance.finance_transactions t
        WHERE t.transaction_date BETWEEN ? AND ?
        GROUP BY SUBSTR(t.transaction_date, 1, 7)
    """, [start or "0000-01-01", end or "9999-12-31"])


# --------------------------------------------------------------- summaries ---
# Totals are run over the same scoped, filtered rows the table shows. They never
# read the whole table, so a date range or department filter is reflected in the
# figures as well as in the row list.
def _figure(label, value, money=False):
    return {"label": label, "value": 0 if value is None else value, "money": money}


def _measure(db, wrapped, params, select):
    return db.execute("SELECT {} FROM ({}) r".format(select, wrapped),
                      params).fetchone()


def _sum_people(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.status = 'Active' THEN 1 ELSE 0 END), 0) AS active,
        COALESCE(SUM(CASE WHEN r.status = 'On Leave' THEN 1 ELSE 0 END), 0) AS on_leave,
        COUNT(DISTINCT NULLIF(r.department, '')) AS departments
    """)
    return [_figure("Employees", row["n"]), _figure("Active", row["active"]),
            _figure("On leave", row["on_leave"]),
            _figure("Departments", row["departments"])]


def _sum_attendance(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.status = 'Present' THEN 1 ELSE 0 END), 0) AS present,
        COALESCE(SUM(CASE WHEN r.status = 'Late' THEN 1 ELSE 0 END), 0) AS late,
        COALESCE(SUM(CASE WHEN r.status = 'Absent' THEN 1 ELSE 0 END), 0) AS absent,
        COALESCE(SUM(CASE WHEN r.status = 'Half Day' THEN 1 ELSE 0 END), 0) AS half
    """)
    return [_figure("Records", row["n"]), _figure("Present", row["present"]),
            _figure("Late", row["late"]), _figure("Absent", row["absent"]),
            _figure("Half day", row["half"])]


def _sum_leave(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.status = 'Approved' THEN 1 ELSE 0 END), 0) AS approved,
        COALESCE(SUM(CASE WHEN r.status = 'Pending' THEN 1 ELSE 0 END), 0) AS pending,
        COALESCE(SUM(CASE WHEN r.status = 'Rejected' THEN 1 ELSE 0 END), 0) AS rejected,
        COALESCE(SUM(CASE WHEN r.status = 'Approved' THEN r.days ELSE 0 END), 0) AS approved_days
    """)
    return [_figure("Requests", row["n"]), _figure("Approved", row["approved"]),
            _figure("Pending", row["pending"]), _figure("Rejected", row["rejected"]),
            _figure("Approved days", row["approved_days"])]


def _sum_leave_balances(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.approved_days), 0) AS approved_days,
        COALESCE(SUM(r.pending_days), 0) AS pending_days,
        COALESCE(SUM(r.rejected_days), 0) AS rejected_days,
        COALESCE(SUM(r.total_days), 0) AS total_days
    """)
    return [_figure("Employees", row["n"]),
            _figure("Approved days", row["approved_days"]),
            _figure("Pending days", row["pending_days"]),
            _figure("Rejected days", row["rejected_days"]),
            _figure("Total days", row["total_days"])]


def _sum_performance(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n, ROUND(AVG(r.rating), 2) AS rating
    """)
    return [_figure("Reviews", row["n"]), _figure("Average rating", row["rating"])]


def _sum_payroll(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.basic), 0) AS basic,
        COALESCE(SUM(r.allowances), 0) AS allowances,
        COALESCE(SUM(r.deductions), 0) AS deductions,
        COALESCE(SUM(r.net), 0) AS net,
        COALESCE(SUM(CASE WHEN r.status = 'Paid' THEN 1 ELSE 0 END), 0) AS paid,
        COALESCE(SUM(CASE WHEN r.status = 'Pending' THEN 1 ELSE 0 END), 0) AS pending
    """)
    return [_figure("Payslips", row["n"]),
            _figure("Gross pay (ETB)", row["basic"] + row["allowances"], True),
            _figure("Deductions (ETB)", row["deductions"], True),
            _figure("Net pay (ETB)", row["net"], True),
            _figure("Paid", row["paid"])]


def _finance_totals(db, wrapped, params, label):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.type = 'income' THEN r.amount_etb ELSE 0 END), 0) AS income,
        COALESCE(SUM(CASE WHEN r.type = 'expense' THEN r.amount_etb ELSE 0 END), 0) AS expense
    """)
    return [_figure("Transactions", row["n"]),
            _figure("Income (ETB)", row["income"], True),
            _figure("Expenses (ETB)", row["expense"], True),
            _figure(label, row["income"] - row["expense"], True)]


def _sum_finance(db, wrapped, params):
    return _finance_totals(db, wrapped, params, "Net (ETB)")


def _finance_side(db, wrapped, params, label):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n, COALESCE(SUM(r.amount_etb), 0) AS total
    """)
    return [_figure("Transactions", row["n"]),
            _figure(label, row["total"], True),
            _figure("Average (ETB)", row["total"] / row["n"] if row["n"] else 0, True)]


def _sum_finance_income(db, wrapped, params):
    return _finance_side(db, wrapped, params, "Income (ETB)")


def _sum_finance_expense(db, wrapped, params):
    return _finance_side(db, wrapped, params, "Expenses (ETB)")


def _sum_finance_month(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.income), 0) AS income,
        COALESCE(SUM(r.expense), 0) AS expense,
        COALESCE(SUM(r.net), 0) AS net
    """)
    return [_figure("Months", row["n"]), _figure("Income (ETB)", row["income"], True),
            _figure("Expenses (ETB)", row["expense"], True),
            _figure("Net (ETB)", row["net"], True)]


def _sum_invoices(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.total_etb), 0) AS total,
        COALESCE(SUM(CASE WHEN r.status = 'Unpaid'
                          THEN r.total_etb ELSE 0 END), 0) AS unpaid,
        COALESCE(SUM(CASE WHEN r.status = 'Paid' THEN 1 ELSE 0 END), 0) AS paid
    """)
    return [_figure("Invoices", row["n"]), _figure("Total (ETB)", row["total"], True),
            _figure("Unpaid (ETB)", row["unpaid"], True), _figure("Paid", row["paid"])]


def _sum_payments(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n, COALESCE(SUM(r.amount_etb), 0) AS total
    """)
    return [_figure("Payments", row["n"]),
            _figure("Paid (ETB)", row["total"], True)]


def _sum_budget_alloc(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.allocated), 0) AS allocated,
        COALESCE(SUM(r.spent), 0) AS spent,
        COALESCE(SUM(r.remaining), 0) AS remaining
    """)
    return [_figure("Allocations", row["n"]),
            _figure("Allocated (ETB)", row["allocated"], True),
            _figure("Spent (ETB)", row["spent"], True),
            _figure("Remaining (ETB)", row["remaining"], True)]


def _sum_budget_exp(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n, COALESCE(SUM(r.amount), 0) AS total
    """)
    return [_figure("Expenditures", row["n"]),
            _figure("Amount (ETB)", row["total"], True)]


def _sum_requisitions(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.estimated), 0) AS estimated,
        COALESCE(SUM(CASE WHEN r.status = 'Pending' THEN 1 ELSE 0 END), 0) AS pending,
        COALESCE(SUM(CASE WHEN r.status = 'Approved' THEN 1 ELSE 0 END), 0) AS approved
    """)
    return [_figure("Requisitions", row["n"]),
            _figure("Estimated (ETB)", row["estimated"], True),
            _figure("Pending", row["pending"]), _figure("Approved", row["approved"])]


def _sum_orders(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n, COALESCE(SUM(r.total), 0) AS total
    """)
    return [_figure("Orders", row["n"]),
            _figure("Value (ETB)", row["total"], True)]


def _sum_stock(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.value), 0) AS value,
        COALESCE(SUM(CASE WHEN r.quantity <= r.min_stock THEN 1 ELSE 0 END), 0) AS low,
        COALESCE(SUM(CASE WHEN r.active = 'Yes' THEN 1 ELSE 0 END), 0) AS active
    """)
    return [_figure("Items", row["n"]), _figure("Stock value (ETB)", row["value"], True),
            _figure("At or below minimum", row["low"]), _figure("In use", row["active"])]


def _sum_movements(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.type = 'IN' THEN r.quantity ELSE 0 END), 0) AS received,
        COALESCE(SUM(CASE WHEN r.type = 'OUT' THEN r.quantity ELSE 0 END), 0) AS issued
    """)
    return [_figure("Movements", row["n"]), _figure("Quantity in", row["received"]),
            _figure("Quantity out", row["issued"])]


def _sum_accounts(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.status = 'Active' THEN 1 ELSE 0 END), 0) AS active,
        COALESCE(SUM(CASE WHEN r.status = 'Disabled' THEN 1 ELSE 0 END), 0) AS disabled,
        COALESCE(SUM(CASE WHEN r.role = 'admin' THEN 1 ELSE 0 END), 0) AS admins
    """)
    return [_figure("Accounts", row["n"]), _figure("Active", row["active"]),
            _figure("Disabled", row["disabled"]), _figure("Administrators", row["admins"])]


def _sum_roles(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.accounts), 0) AS accounts,
        COALESCE(SUM(CASE WHEN r.system_role = 'Yes' THEN 1 ELSE 0 END), 0) AS system_roles
    """)
    return [_figure("Roles", row["n"]),
            _figure("Account assignments", row["accounts"]),
            _figure("Built in", row["system_roles"])]


def _sum_activity(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.outcome IN ('FAILURE', 'DENIED')
                          THEN 1 ELSE 0 END), 0) AS failed,
        COUNT(DISTINCT NULLIF(r.username, '')) AS users
    """)
    return [_figure("Events", row["n"]), _figure("Refused or failed", row["failed"]),
            _figure("Accounts", row["users"])]


def _sum_usage(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(DISTINCT r.module) AS modules,
        COALESCE(SUM(r.events), 0) AS events,
        COALESCE(SUM(r.refusals), 0) AS refusals
    """)
    return [_figure("Modules", row["modules"]), _figure("Events", row["events"]),
            _figure("Refusals", row["refusals"])]


def _sum_approvals(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(r.amount), 0) AS total,
        COALESCE(SUM(CASE WHEN r.status = 'Pending' THEN 1 ELSE 0 END), 0) AS pending,
        COALESCE(SUM(CASE WHEN r.status = 'Approved' THEN 1 ELSE 0 END), 0) AS approved
    """)
    return [_figure("Requests", row["n"]), _figure("Amount (ETB)", row["total"], True),
            _figure("Pending", row["pending"]), _figure("Approved", row["approved"])]


def _sum_training(db, wrapped, params):
    row = _measure(db, wrapped, params, """
        COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN r.status = 'Completed' THEN 1 ELSE 0 END), 0) AS completed
    """)
    return [_figure("Courses", row["n"]), _figure("Completed", row["completed"])]


# ---------------------------------------------------------------- specs ----
REPORT_SPECS = (
    # -------- personal --------
    {
        "key": "my_attendance",
        "label": "My attendance",
        "group": "My reports",
        "description": "Every day the register has you marked in, with the notes on your record.",
        "modules": (),
        "personal": True,
        "admin_all": False,
        "scope": "own",
        "requires": ("attendance", "employees"),
        "sql": _attendance_sql,
        "columns": (("date", "Date"), ("status", "Status"),
                    ("check_in", "Check in"), ("check_out", "Check out"),
                    ("employee", "Employee"), ("department", "Department"),
                    ("notes", "Notes")),
        "money": (),
        "date_column": "date",
        "search": ("status", "notes"),
        "filters": (("status", "Status"),),
        "note": "Only your own attendance rows are read, whatever department you belong to.",
    },
    {
        "key": "my_leave",
        "label": "My leave requests",
        "group": "My reports",
        "description": "Your leave applications, the days asked for and the decision on each.",
        "modules": (),
        "personal": True,
        "admin_all": False,
        "scope": "own",
        "requires": ("leave_requests", "employees"),
        "sql": _leave_sql,
        "columns": (("date", "Start"), ("end_date", "End"),
                    ("leave_type", "Leave type"), ("days", "Days"),
                    ("status", "Status"), ("reason", "Reason"),
                    ("review_comment", "Review comment")),
        "money": (),
        "date_column": "date",
        "search": ("leave_type", "reason", "status"),
        "filters": (("status", "Status"),),
        "note": "Approved, pending and rejected requests are all listed.",
    },
    {
        "key": "my_payslips",
        "label": "My payslips",
        "group": "My reports",
        "description": "Your monthly pay records: gross, deductions and what was released.",
        "modules": (),
        "personal": True,
        "admin_all": False,
        "scope": "own",
        "requires": ("payroll", "employees"),
        "sql": lambda ctx, scope, _start, _end: (
            "SELECT p.payroll_month AS date, p.payroll_month AS month,"
            " p.basic_salary AS basic, p.allowances, p.deductions,"
            " p.net_salary AS net,"
            " p.payment_status AS status, e.full_name AS employee"
            " FROM payroll p JOIN employees e ON e.id = p.employee_id"
            " WHERE p.employee_id = ?", [ctx["employee_id"]]),
        "columns": (("date", "Month"), ("basic", "Basic"),
                    ("allowances", "Allowances"), ("deductions", "Deductions"),
                    ("net", "Net pay"), ("status", "Payment status")),
        "money": ("basic", "allowances", "deductions", "net"),
        "date_column": "date",
        "date_kind": "month",
        "search": ("month", "status"),
        "filters": (("status", "Payment status"),),
        "note": "Pay is shown as booked in the payroll register, before any bank fees.",
    },
    {
        "key": "my_performance",
        "label": "My performance",
        "group": "My reports",
        "description": "Your appraisal history and the goals recorded for each review.",
        "modules": (),
        "personal": True,
        "admin_all": False,
        "scope": "own",
        "requires": ("performance_reviews", "employees"),
        "sql": _performance_sql,
        "columns": (("date", "Reviewed"), ("review_period", "Period"),
                    ("rating", "Rating"), ("reviewer", "Reviewer"),
                    ("strengths", "Strengths"), ("improvements", "Improvements"),
                    ("goals", "Goals")),
        "money": (),
        "date_column": "date",
        "search": ("review_period", "reviewer", "goals"),
        "filters": (),
        "note": "Only reviews recorded against your own staff record are shown.",
    },
    {
        "key": "my_training",
        "label": "My training",
        "group": "My reports",
        "description": "Courses assigned to you and the status of each.",
        "modules": (),
        "personal": True,
        "admin_all": False,
        "scope": "own",
        "requires": ("employee_training", "employees"),
        "sql": lambda ctx, scope, _start, _end: (
            "SELECT t.start_date AS date, t.end_date, t.course_title, t.provider,"
            " t.status, t.notes, e.full_name AS employee"
            " FROM employee_training t JOIN employees e ON e.id = t.employee_id"
            " WHERE t.employee_id = ?", [ctx["employee_id"]]),
        "columns": (("date", "Start"), ("end_date", "End"),
                    ("course_title", "Course"), ("provider", "Provider"),
                    ("status", "Status"), ("notes", "Notes")),
        "money": (),
        "date_column": "date",
        "search": ("course_title", "provider", "status"),
        "filters": (("status", "Status"),),
        "note": "Certificate files stay on the employee record in the HR module.",
    },

    # -------- workforce --------
    {
        "key": "employee_register",
        "label": "Employee register",
        "group": "Workforce",
        "description": "The staff register with role, employment status and line manager.",
        "modules": ("hr",),
        "scope": "department",
        "full_roles": HR_SCOPE,
        "requires": ("employees",),
        "sql": lambda ctx, scope, start, end: _roster_sql(ctx, scope, start, end, contact=True),
        "columns": (("code", "Code"), ("employee", "Employee"),
                    ("department", "Department"), ("job_title", "Job title"),
                    ("status", "Status"), ("employment_type", "Type"),
                    ("date", "Hired"), ("work_location", "Work location"),
                    ("manager", "Line manager"), ("email", "Email"),
                    ("phone", "Phone")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "code", "department", "job_title", "email"),
        "filters": (("department", "Department"), ("status", "Status"),
                    ("employment_type", "Type")),
        "note": "HR sees the whole register; another department granted this module "
                "sees its own staff only.",
    },
    {
        "key": "attendance_register",
        "label": "Attendance register",
        "group": "Workforce",
        "description": "Daily attendance marks by employee, with check-in and check-out times.",
        "modules": ("hr",),
        "scope": "department",
        "full_roles": HR_SCOPE,
        "requires": ("attendance", "employees"),
        "sql": _attendance_sql,
        "columns": (("date", "Date"), ("employee", "Employee"),
                    ("department", "Department"), ("status", "Status"),
                    ("check_in", "Check in"), ("check_out", "Check out"),
                    ("notes", "Notes")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "department", "status", "notes"),
        "filters": (("status", "Status"), ("department", "Department")),
        "note": "One row per employee per day; the period filter is applied to the"
                " attendance date.",
    },
    {
        "key": "leave_register",
        "label": "Leave requests",
        "group": "Workforce",
        "description": "Leave applications across the team or department, with their decisions.",
        "modules": ("hr",),
        "scope": "department",
        "full_roles": HR_SCOPE,
        "requires": ("leave_requests", "employees"),
        "sql": _leave_sql,
        "columns": (("date", "Start"), ("end_date", "End"),
                    ("employee", "Employee"), ("department", "Department"),
                    ("leave_type", "Leave type"), ("days", "Days"),
                    ("status", "Status"), ("reason", "Reason"),
                    ("reviewed_by", "Reviewed by")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "department", "leave_type", "reason", "status"),
        "filters": (("status", "Status"), ("leave_type", "Leave type"),
                    ("department", "Department")),
        "note": "The date range follows the first day of leave, as HR files it.",
    },
    {
        "key": "leave_summary",
        "label": "Leave balances",
        "group": "Workforce",
        "description": "Leave days requested, approved, pending and rejected for each employee in a period.",
        "modules": ("hr",),
        "scope": "department",
        "full_roles": HR_SCOPE,
        "requires": ("leave_requests", "employees"),
        "sql": _leave_summary_sql,
        "date_in_sql": True,
        "columns": (("employee", "Employee"), ("department", "Department"),
                    ("requests", "Requests"), ("approved_days", "Approved days"),
                    ("pending_days", "Pending days"), ("rejected_days", "Rejected days"),
                    ("total_days", "Total days")),
        "money": (),
        "date_column": None,
        "order_by": "r.total_days DESC, r.employee ASC",
        "search": ("employee", "department"),
        "filters": (("department", "Department"),),
        "note": "The date range decides which leave applications are counted; days are"
                " as requested, not recalculated from the calendar.",
    },
    {
        "key": "performance_register",
        "label": "Employee performance",
        "group": "Workforce",
        "description": "Appraisal ratings, reviewers and the development notes behind them.",
        "modules": ("hr",),
        "scope": "department",
        "full_roles": HR_SCOPE,
        "requires": ("performance_reviews", "employees"),
        "sql": _performance_sql,
        "columns": (("date", "Reviewed"), ("employee", "Employee"),
                    ("department", "Department"), ("review_period", "Period"),
                    ("rating", "Rating"), ("reviewer", "Reviewer"),
                    ("strengths", "Strengths"), ("improvements", "Improvements"),
                    ("goals", "Goals")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "department", "reviewer", "review_period"),
        "filters": (("department", "Department"),),
        "note": "Reviews stay read-only here; they are recorded in the HR module.",
    },

    # -------- team --------
    {
        "key": "team_roster",
        "label": "Team roster",
        "group": "My team",
        "description": "The people who report to you, including your own record.",
        "modules": (),
        "manager_only": True,
        "admin_all": False,
        "hide_unavailable": True,
        "scope": "team",
        "requires": ("employees",),
        "sql": _roster_sql,
        "columns": (("code", "Code"), ("employee", "Employee"),
                    ("department", "Department"), ("job_title", "Job title"),
                    ("status", "Status"), ("employment_type", "Type"),
                    ("date", "Hired"), ("work_location", "Work location"),
                    ("manager", "Line manager")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "code", "department", "job_title"),
        "filters": (("status", "Status"), ("department", "Department")),
        "note": "Team scope follows employees.manager_id from your staff record; an"
                " account without one falls back to its department.",
    },
    {
        "key": "team_attendance",
        "label": "Team attendance",
        "group": "My team",
        "description": "Attendance marks for your team over the selected period.",
        "modules": (),
        "manager_only": True,
        "admin_all": False,
        "hide_unavailable": True,
        "scope": "team",
        "requires": ("attendance", "employees"),
        "sql": _attendance_sql,
        "columns": (("date", "Date"), ("employee", "Employee"),
                    ("status", "Status"), ("check_in", "Check in"),
                    ("check_out", "Check out"), ("notes", "Notes")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "status", "notes"),
        "filters": (("status", "Status"),),
        "note": "Only the staff records in your team are read.",
    },
    {
        "key": "team_leave",
        "label": "Team leave",
        "group": "My team",
        "description": "Leave applications from your team and where each one stands.",
        "modules": (),
        "manager_only": True,
        "admin_all": False,
        "hide_unavailable": True,
        "scope": "team",
        "requires": ("leave_requests", "employees"),
        "sql": _leave_sql,
        "columns": (("date", "Start"), ("end_date", "End"),
                    ("employee", "Employee"), ("leave_type", "Leave type"),
                    ("days", "Days"), ("status", "Status"), ("reason", "Reason"),
                    ("reviewed_by", "Reviewed by")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "leave_type", "reason", "status"),
        "filters": (("status", "Status"), ("leave_type", "Leave type")),
        "note": "Team scope follows employees.manager_id from your staff record.",
    },
    {
        "key": "team_performance",
        "label": "Team performance",
        "group": "My team",
        "description": "Appraisal history for the people reporting to you.",
        "modules": (),
        "manager_only": True,
        "admin_all": False,
        "hide_unavailable": True,
        "scope": "team",
        "requires": ("performance_reviews", "employees"),
        "sql": _performance_sql,
        "columns": (("date", "Reviewed"), ("employee", "Employee"),
                    ("review_period", "Period"), ("rating", "Rating"),
                    ("reviewer", "Reviewer"), ("goals", "Goals")),
        "money": (),
        "date_column": "date",
        "search": ("employee", "reviewer", "review_period"),
        "filters": (),
        "note": "Only reviews recorded against your team's staff records are read.",
    },
    {
        "key": "team_approvals",
        "label": "Approval summary",
        "group": "My team",
        "description": "Requests raised in your team or department and their current step.",
        "modules": ("approvals",),
        "manager_only": True,
        "admin_all": False,
        "hide_unavailable": True,
        "scope": "team",
        "requires": ("approval_requests",),
        "sql": _approvals_sql,
        "columns": (("date", "Raised"), ("title", "Request"),
                    ("request_type", "Type"), ("department", "Department"),
                    ("amount", "Amount"), ("status", "Status"),
                    ("step", "Current step"), ("raised_by", "Raised by")),
        "money": ("amount",),
        "date_column": "date",
        "time": True,
        "search": ("title", "raised_by", "request_type", "description"),
        "filters": (("status", "Status"), ("request_type", "Type")),
        "note": "Team requests and requests tagged with your department are listed;"
                " decisions are taken on the Approval Queue.",
    },
    {
        "key": "team_requisitions",
        "label": "Requisition progress",
        "group": "My team",
        "description": "Purchase requisitions from your team and how far each has moved.",
        "modules": ("procurement",),
        "manager_only": True,
        "admin_all": False,
        "hide_unavailable": True,
        "scope": "team",
        "requires": ("procurement_requests",),
        "sql": _requisitions_sql,
        "columns": (("date", "Submitted"), ("reference", "Reference"),
                    ("department", "Department"), ("item_name", "Item"),
                    ("quantity", "Quantity"), ("unit", "Unit"),
                    ("estimated", "Estimated (ETB)"), ("supplier", "Supplier"),
                    ("raised_by", "Raised by"), ("status", "Status")),
        "money": ("estimated",),
        "date_column": "date",
        "time": True,
        "search": ("reference", "item_name", "raised_by", "department"),
        "filters": (("status", "Status"), ("department", "Department")),
        "note": "The date is the submission day, or the day a draft was opened.",
    },

    # -------- administration --------
    {
        "key": "user_accounts",
        "label": "User accounts",
        "group": "Administration",
        "description": "Every sign-in account with its primary role, department and status.",
        "modules": ("accounts",),
        "scope": "all",
        "requires": ("users",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT u.username, u.full_name AS account, u.role, u.status,
                      COALESCE(d.name, '') AS department,
                      COALESCE(p.title, '') AS position,
                      u.last_login AS date,
                      CASE WHEN u.must_change_password = 1
                           THEN 'Yes' ELSE 'No' END AS password_change_required
               FROM users u
               LEFT JOIN departments d ON d.id = u.department_id
               LEFT JOIN organizational_positions p ON p.id = u.position_id""", []),
        "columns": (("username", "Username"), ("account", "Account"),
                    ("role", "Primary role"), ("status", "Status"),
                    ("department", "Department"), ("position", "Position"),
                    ("date", "Last sign-in"),
                    ("password_change_required", "Password change due")),
        "money": (),
        "date_column": "date",
        "time": True,
        "search": ("account", "username", "role", "department"),
        "filters": (("role", "Role"), ("status", "Status"),
                    ("department", "Department")),
        "note": "Credentials are never part of a report: only the account, its role"
                " and its sign-in state are read.",
    },
    {
        "key": "role_permissions",
        "label": "Roles and permissions",
        "group": "Administration",
        "description": "Each role's landing screen, modules and how many accounts carry it.",
        "modules": ("accounts",),
        "scope": "all",
        "requires": ("roles", "role_permissions"),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT r.key AS role_key, r.name AS role, r.description, r.landing,
                      CASE WHEN r.is_system = 1 THEN 'Yes' ELSE 'No' END AS system_role,
                      COALESCE(GROUP_CONCAT(rp.module, ', '), '') AS modules,
                      (SELECT COUNT(*) FROM users u WHERE u.role = r.key) AS accounts
               FROM roles r
               LEFT JOIN role_permissions rp ON rp.role_key = r.key
               GROUP BY r.key, r.name, r.description, r.landing, r.is_system""", []),
        "columns": (("role_key", "Key"), ("role", "Role"),
                    ("system_role", "Built in"), ("landing", "Landing screen"),
                    ("modules", "Modules"), ("accounts", "Accounts"),
                    ("description", "Description")),
        "money": (),
        "date_column": None,
        "order_by": "r.role ASC",
        "search": ("role", "role_key", "modules", "description"),
        "filters": (("system_role", "Built in"),),
        "note": "The account count is by primary role; additional roles on an account"
                " widen its modules without changing this figure.",
    },
    {
        "key": "activity_logs",
        "label": "Activity logs",
        "group": "Administration",
        "description": "Every recorded action, its module, actor and outcome.",
        "modules": ("audit",),
        "scope": "all",
        "requires": ("audit_logs",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT occurred_at AS date, username, role, action, module,
                      description, outcome, ip_address
               FROM audit_logs""", []),
        "columns": (("date", "When"), ("username", "Account"), ("role", "Role"),
                    ("module", "Module"), ("action", "Action"),
                    ("description", "Description"), ("outcome", "Outcome"),
                    ("ip_address", "IP address")),
        "money": (),
        "date_column": "date",
        "time": True,
        "search": ("username", "description", "action", "module", "outcome"),
        "filters": (("module", "Module"), ("action", "Action"),
                    ("outcome", "Outcome")),
        "note": "Read-only. The audit console holds the investigation tools that sit"
                " on top of these rows.",
    },
    {
        "key": "system_usage",
        "label": "System activities",
        "group": "Administration",
        "description": "Actions grouped by module, with event counts, accounts and refusals.",
        "modules": ("audit",),
        "scope": "all",
        "requires": ("audit_logs",),
        "sql": _system_usage_sql,
        "date_in_sql": True,
        "columns": (("module", "Module"), ("action", "Action"),
                    ("events", "Events"), ("users", "Accounts"),
                    ("refusals", "Refusals"), ("first_seen", "First seen"),
                    ("last_seen", "Last seen")),
        "money": (),
        "date_column": None,
        "order_by": "r.events DESC, r.module ASC",
        "search": ("module", "action"),
        "filters": (("module", "Module"),),
        "note": "The date range is applied to the audit timestamp before the counts"
                " are grouped.",
    },
    {
        "key": "audit_trail",
        "label": "Audit trail",
        "group": "Administration",
        "description": "Refused and failed actions, plus sign-in activity, for review.",
        "modules": ("audit",),
        "scope": "all",
        "requires": ("audit_logs",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT occurred_at AS date, username, role, module, action,
                      description, outcome, ip_address, record_type, record_id
               FROM audit_logs
               WHERE outcome IN ('DENIED', 'FAILURE') OR module = 'auth'""", []),
        "columns": (("date", "When"), ("username", "Account"), ("role", "Role"),
                    ("module", "Module"), ("action", "Action"),
                    ("description", "Description"), ("outcome", "Outcome"),
                    ("ip_address", "IP address"), ("record_type", "Record type"),
                    ("record_id", "Record id")),
        "money": (),
        "date_column": "date",
        "time": True,
        "search": ("username", "description", "action", "module", "outcome"),
        "filters": (("module", "Module"), ("outcome", "Outcome")),
        "note": "Security-relevant events only; the full log is on Activity logs.",
    },

    # -------- finance --------
    {
        "key": "finance_transactions",
        "label": "Income and expenses",
        "group": "Finance",
        "description": "Every money movement booked in the Finance module.",
        "modules": ("finance",),
        "scope": "all",
        "requires": ("finance_transactions",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT t.transaction_date AS date, t.transaction_type AS type,
                      t.category, t.description, t.party_name AS party,
                      t.payment_method AS method, t.reference, t.currency,
                      t.amount, t.amount_etb, t.created_by AS recorded_by
               FROM finance.finance_transactions t""", []),
        "columns": (("date", "Date"), ("type", "Type"), ("category", "Category"),
                    ("party", "Counterparty"), ("description", "Description"),
                    ("method", "Method"), ("reference", "Reference"),
                    ("currency", "Currency"), ("amount", "Amount"),
                    ("amount_etb", "Amount (ETB)"), ("recorded_by", "Recorded by")),
        "money": ("amount", "amount_etb"),
        "date_column": "date",
        "search": ("description", "party", "reference", "category"),
        "filters": (("type", "Type"), ("category", "Category"),
                    ("method", "Method")),
        "note": "Amount is as booked in its own currency; Amount (ETB) uses the rate"
                " captured on the transaction.",
    },
    {
        "key": "finance_income",
        "label": "Income",
        "group": "Finance",
        "description": "Income booked in the Finance module, with the total for the period.",
        "modules": ("finance",),
        "scope": "all",
        "requires": ("finance_transactions",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT t.transaction_date AS date, t.category,
                      t.description, t.party_name AS party,
                      t.payment_method AS method, t.reference, t.currency,
                      t.amount, t.amount_etb, t.created_by AS recorded_by
               FROM finance.finance_transactions t
               WHERE t.transaction_type = 'income'""", []),
        "columns": (("date", "Date"), ("category", "Category"),
                    ("party", "Counterparty"), ("description", "Description"),
                    ("method", "Method"), ("reference", "Reference"),
                    ("currency", "Currency"), ("amount", "Amount"),
                    ("amount_etb", "Amount (ETB)"), ("recorded_by", "Recorded by")),
        "money": ("amount", "amount_etb"),
        "date_column": "date",
        "search": ("description", "party", "reference", "category"),
        "filters": (("category", "Category"), ("method", "Method")),
        "note": "Only income transactions are read; the totals above cover the same"
                " rows as the table and follow the date range and filters.",
    },
    {
        "key": "finance_expense",
        "label": "Expenses",
        "group": "Finance",
        "description": "Expenses booked in the Finance module, with the total for the period.",
        "modules": ("finance",),
        "scope": "all",
        "requires": ("finance_transactions",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT t.transaction_date AS date, t.category,
                      t.description, t.party_name AS party,
                      t.payment_method AS method, t.reference, t.currency,
                      t.amount, t.amount_etb, t.created_by AS recorded_by
               FROM finance.finance_transactions t
               WHERE t.transaction_type = 'expense'""", []),
        "columns": (("date", "Date"), ("category", "Category"),
                    ("party", "Counterparty"), ("description", "Description"),
                    ("method", "Method"), ("reference", "Reference"),
                    ("currency", "Currency"), ("amount", "Amount"),
                    ("amount_etb", "Amount (ETB)"), ("recorded_by", "Recorded by")),
        "money": ("amount", "amount_etb"),
        "date_column": "date",
        "search": ("description", "party", "reference", "category"),
        "filters": (("category", "Category"), ("method", "Method")),
        "note": "Only expense transactions are read; the totals above cover the same"
                " rows as the table and follow the date range and filters.",
    },
    {
        "key": "finance_monthly",
        "label": "Monthly income and expenses",
        "group": "Finance",
        "description": "Income, expenses and the net result for each calendar month.",
        "modules": ("finance",),
        "scope": "all",
        "requires": ("finance_transactions",),
        "sql": _finance_month_sql,
        "date_in_sql": True,
        "columns": (("month", "Month"), ("transactions", "Transactions"),
                    ("income", "Income (ETB)"), ("expense", "Expenses (ETB)"),
                    ("net", "Net (ETB)")),
        "money": ("income", "expense", "net"),
        "date_column": None,
        "order_by": "r.month DESC",
        "search": ("month",),
        "filters": (),
        "note": "Amounts use the exchange rate captured on each transaction; the date"
                " range narrows the transactions before the months are grouped.",
    },
    {
        "key": "finance_invoices",
        "label": "Invoices",
        "group": "Finance",
        "description": "Customer and supplier invoices with due dates and payment status.",
        "modules": ("finance",),
        "scope": "all",
        "requires": ("finance_invoices",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT i.issue_date AS date, i.invoice_number AS invoice,
                      i.invoice_type AS type, i.party_name AS party,
                      i.due_date, i.currency, i.total_amount AS total,
                      i.total_etb, i.status
               FROM finance.finance_invoices i""", []),
        "columns": (("invoice", "Invoice"), ("type", "Type"),
                    ("party", "Party"), ("date", "Issued"),
                    ("due_date", "Due"), ("currency", "Currency"),
                    ("total", "Total"), ("total_etb", "Total (ETB)"),
                    ("status", "Status")),
        "money": ("total", "total_etb"),
        "date_column": "date",
        "search": ("invoice", "party"),
        "filters": (("status", "Status"), ("type", "Type")),
        "note": "Payments against an invoice are on the Payments report.",
    },
    {
        "key": "finance_payments",
        "label": "Payments",
        "group": "Finance",
        "description": "Money released against invoices, with the method and reference.",
        "modules": ("finance",),
        "scope": "all",
        "requires": ("finance_payments", "finance_invoices"),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT p.payment_date AS date, i.invoice_number AS invoice,
                      i.party_name AS party, p.amount, p.amount_etb,
                      p.payment_method AS method, p.reference,
                      p.created_by AS recorded_by
               FROM finance.finance_payments p
               JOIN finance.finance_invoices i ON i.id = p.invoice_id""", []),
        "columns": (("date", "Paid"), ("invoice", "Invoice"),
                    ("party", "Party"), ("amount", "Amount"),
                    ("amount_etb", "Amount (ETB)"), ("method", "Method"),
                    ("reference", "Reference"), ("recorded_by", "Recorded by")),
        "money": ("amount", "amount_etb"),
        "date_column": "date",
        "search": ("invoice", "party", "reference"),
        "filters": (("method", "Method"),),
        "note": "Each row is one payment, so a part-paid invoice appears more than once.",
    },

    # -------- budget --------
    {
        "key": "budget_allocations",
        "label": "Budget allocations",
        "group": "Budget",
        "description": "Allocations, what has been drawn against them and what remains.",
        "modules": ("budget",),
        "scope": "department",
        "full_roles": BUDGET_SCOPE,
        "requires": ("budget_allocations", "budget_plans", "budget_expenditures"),
        "sql": _budget_allocation_sql,
        "columns": (("allocation", "Allocation"), ("plan", "Plan"),
                    ("year", "Year"), ("department", "Department"),
                    ("currency", "Currency"), ("allocated", "Allocated"),
                    ("spent", "Spent"), ("remaining", "Remaining"),
                    ("status", "Status"), ("date", "Opened")),
        "money": ("allocated", "spent", "remaining"),
        "date_column": "date",
        "time": True,
        "search": ("allocation", "plan", "department"),
        "filters": (("department", "Department"), ("status", "Status"),
                    ("currency", "Currency")),
        "note": "Spent is the sum of expenditures recorded against the allocation in"
                " its own currency; it is not a maintained figure.",
    },
    {
        "key": "budget_expenditures",
        "label": "Budget expenditures",
        "group": "Budget",
        "description": "Every expenditure booked against an allocation.",
        "modules": ("budget",),
        "scope": "department",
        "full_roles": BUDGET_SCOPE,
        "requires": ("budget_expenditures", "budget_allocations"),
        "sql": _budget_expenditure_sql,
        "columns": (("date", "Recorded"), ("allocation", "Allocation"),
                    ("department", "Department"), ("description", "Description"),
                    ("amount", "Amount"), ("currency", "Currency"),
                    ("reference", "Reference"), ("recorded_by", "Recorded by")),
        "money": ("amount",),
        "date_column": "date",
        "time": True,
        "search": ("allocation", "description", "reference", "recorded_by"),
        "filters": (("department", "Department"), ("currency", "Currency")),
        "note": "Expenditures posted from Finance or Procurement are included; the"
                " reference tells you which thread they came from.",
    },

    # -------- purchasing --------
    {
        "key": "procurement_requests",
        "label": "Purchase requisitions",
        "group": "Purchasing",
        "description": "Requisitions raised, their cost and where they stand.",
        "modules": ("procurement",),
        "scope": "department",
        "full_roles": PROCUREMENT_SCOPE,
        "requires": ("procurement_requests", "procurement_suppliers"),
        "sql": _requisitions_sql,
        "columns": (("date", "Submitted"), ("reference", "Reference"),
                    ("department", "Department"), ("item_name", "Item"),
                    ("quantity", "Quantity"), ("unit", "Unit"),
                    ("estimated", "Estimated (ETB)"), ("supplier", "Supplier"),
                    ("raised_by", "Raised by"), ("status", "Status")),
        "money": ("estimated",),
        "date_column": "date",
        "time": True,
        "search": ("reference", "item_name", "raised_by", "department"),
        "filters": (("status", "Status"), ("department", "Department")),
        "note": "The date is the submission day, or the day a draft was opened.",
    },
    {
        "key": "procurement_orders",
        "label": "Purchase orders",
        "group": "Purchasing",
        "description": "Orders placed with suppliers, their value and expected delivery.",
        "modules": ("procurement",),
        "scope": "all",
        "requires": ("procurement_orders", "procurement_suppliers"),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT o.created_at AS date, o.order_number AS reference,
                      s.name AS supplier, o.item_name, o.quantity, o.unit,
                      o.unit_price, o.total_cost AS total, o.expected_date,
                      o.status, o.created_by_name AS ordered_by
               FROM finance.procurement_orders o
               LEFT JOIN finance.procurement_suppliers s ON s.id = o.supplier_id""", []),
        "columns": (("reference", "Order"), ("supplier", "Supplier"),
                    ("item_name", "Item"), ("quantity", "Quantity"),
                    ("unit", "Unit"), ("unit_price", "Unit price"),
                    ("total", "Total (ETB)"), ("date", "Placed"),
                    ("expected_date", "Expected"), ("status", "Status"),
                    ("ordered_by", "Ordered by")),
        "money": ("unit_price", "total"),
        "date_column": "date",
        "time": True,
        "search": ("reference", "supplier", "item_name"),
        "filters": (("status", "Status"),),
        "note": "Orders carry no department of their own; the Procurement desk manages"
                " them for the whole company.",
    },

    # -------- inventory --------
    {
        "key": "inventory_stock",
        "label": "Stock on hand",
        "group": "Inventory",
        "description": "Items, quantities, minimum levels and the value they carry.",
        "modules": ("inventory",),
        "scope": "all",
        "requires": ("inventory_items",),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT i.sku, i.name AS item, i.category, i.unit, i.quantity,
                      i.min_stock, i.unit_cost, i.quantity * i.unit_cost AS value,
                      i.location, i.supplier,
                      CASE WHEN i.active = 1 THEN 'Yes' ELSE 'No' END AS active,
                      i.created_at AS date
               FROM inventory_items i""", []),
        "columns": (("sku", "SKU"), ("item", "Item"), ("category", "Category"),
                    ("quantity", "On hand"), ("unit", "Unit"),
                    ("min_stock", "Minimum"), ("unit_cost", "Unit cost"),
                    ("value", "Value"), ("location", "Location"),
                    ("supplier", "Supplier"), ("active", "In use"),
                    ("date", "Added")),
        "money": ("unit_cost", "value"),
        "date_column": "date",
        "time": True,
        "search": ("sku", "item", "category", "supplier", "location"),
        "filters": (("category", "Category"), ("active", "In use")),
        "note": "Value is quantity multiplied by the unit cost as the item is recorded"
                " today.",
    },
    {
        "key": "stock_movements",
        "label": "Stock movements",
        "group": "Inventory",
        "description": "Stock in, stock out and adjustments with their reference.",
        "modules": ("inventory",),
        "scope": "all",
        "requires": ("stock_movements", "inventory_items"),
        "sql": lambda ctx, scope, _start, _end: (
            """SELECT m.created_at AS date, i.name AS item, i.sku,
                      m.movement_type AS type, m.quantity, m.reference_type,
                      m.reference_no AS reference, m.notes, m.created_by
               FROM stock_movements m
               JOIN inventory_items i ON i.id = m.item_id""", []),
        "columns": (("date", "When"), ("item", "Item"), ("sku", "SKU"),
                    ("type", "Movement"), ("quantity", "Quantity"),
                    ("reference_type", "Source"), ("reference", "Reference"),
                    ("notes", "Notes"), ("created_by", "Recorded by")),
        "money": (),
        "date_column": "date",
        "time": True,
        "search": ("item", "sku", "reference", "notes"),
        "filters": (("type", "Movement"), ("reference_type", "Source")),
        "note": "Movement history is append-only; stock levels are adjusted by these rows.",
    },

    # -------- payroll --------
    {
        "key": "payroll_register",
        "label": "Payroll register",
        "group": "Payroll",
        "description": "Monthly pay records by employee, with deductions and payment status.",
        "modules": ("payroll",),
        "scope": "department",
        "full_roles": PAYROLL_SCOPE,
        "requires": ("payroll", "employees"),
        "sql": lambda ctx, scope, start, end: _payroll_sql(ctx, scope),
        "columns": (("date", "Month"), ("employee", "Employee"),
                    ("department", "Department"), ("basic", "Basic"),
                    ("allowances", "Allowances"), ("deductions", "Deductions"),
                    ("net", "Net pay"), ("status", "Payment status")),
        "money": ("basic", "allowances", "deductions", "net"),
        "date_column": "date",
        "date_kind": "month",
        "search": ("employee", "department", "date"),
        "filters": (("status", "Payment status"), ("department", "Department")),
        "note": "The date filter compares payroll months, so a range inside one month"
                " selects that month.",
    },
)


# The totals strip on the page and in every export. Each function is handed the
# same scoped, filtered SQL the table reads, so a date range, search word or
# filter changes the figures exactly as it changes the rows.
SUMMARY_BY_KEY = {
    "my_attendance": _sum_attendance,
    "my_leave": _sum_leave,
    "my_payslips": _sum_payroll,
    "my_performance": _sum_performance,
    "my_training": _sum_training,
    "employee_register": _sum_people,
    "attendance_register": _sum_attendance,
    "leave_register": _sum_leave,
    "leave_summary": _sum_leave_balances,
    "performance_register": _sum_performance,
    "team_roster": _sum_people,
    "team_attendance": _sum_attendance,
    "team_leave": _sum_leave,
    "team_performance": _sum_performance,
    "team_approvals": _sum_approvals,
    "team_requisitions": _sum_requisitions,
    "user_accounts": _sum_accounts,
    "role_permissions": _sum_roles,
    "activity_logs": _sum_activity,
    "system_usage": _sum_usage,
    "audit_trail": _sum_activity,
    "finance_transactions": _sum_finance,
    "finance_income": _sum_finance_income,
    "finance_expense": _sum_finance_expense,
    "finance_monthly": _sum_finance_month,
    "finance_invoices": _sum_invoices,
    "finance_payments": _sum_payments,
    "budget_allocations": _sum_budget_alloc,
    "budget_expenditures": _sum_budget_exp,
    "procurement_requests": _sum_requisitions,
    "procurement_orders": _sum_orders,
    "inventory_stock": _sum_stock,
    "stock_movements": _sum_movements,
    "payroll_register": _sum_payroll,
}


def _payroll_sql(ctx, scope):
    clause, params = _employee_scope(ctx, scope)
    return ("""
        SELECT p.payroll_month AS date, e.full_name AS employee, e.department,
               p.basic_salary AS basic, p.allowances, p.deductions,
               p.net_salary AS net, p.payment_status AS status
        FROM payroll p
        JOIN employees e ON e.id = p.employee_id
        WHERE 1 = 1{}
    """.format(clause), params)


# --------------------------------------------------------------- accounts ----
def context(db, user, roles=()):
    """Who the current account is, organisationally, for scoping a report."""
    employee_id = user["employee_id"] if user is not None else None
    roles = tuple(dict.fromkeys((user["role"],) + tuple(roles))) if user else ()
    department = None
    if user is not None and user["department_id"]:
        row = db.execute("SELECT name FROM departments WHERE id = ?",
                         (user["department_id"],)).fetchone()
        department = row["name"] if row else None

    position_level = None
    if user is not None and user["position_id"]:
        row = db.execute("SELECT level FROM organizational_positions WHERE id = ?",
                         (user["position_id"],)).fetchone()
        position_level = row["level"] if row else None

    employee = None
    if employee_id:
        employee = db.execute(
            "SELECT id, department FROM employees WHERE id = ?",
            (employee_id,)).fetchone()
        if employee is not None and not department:
            department = employee["department"] or None

    reports = False
    if employee_id:
        reports = db.execute(
            "SELECT 1 FROM employees WHERE manager_id = ? LIMIT 1",
            (employee_id,)).fetchone() is not None

    team_ids = []
    if employee_id:
        try:
            team_ids = [row[0] for row in db.execute("""
                WITH RECURSIVE team(id) AS (
                    SELECT id FROM employees WHERE id = ?
                    UNION
                    SELECT e.id FROM employees e JOIN team t ON e.manager_id = t.id
                ) SELECT id FROM team
            """, (employee_id,)).fetchall()]
        except sqlite3.Error:
            team_ids = [employee_id]

    team_departments = []
    if team_ids:
        try:
            team_departments = [row[0] for row in db.execute(
                "SELECT DISTINCT department FROM employees"
                " WHERE id IN ({}) AND department IS NOT NULL AND department <> ''"
                .format(_in(team_ids)), team_ids).fetchall()]
        except sqlite3.Error:
            team_departments = []
    if department and department not in team_departments:
        team_departments = team_departments + [department]

    team_user_ids = []
    if team_ids:
        try:
            team_user_ids = [row[0] for row in db.execute(
                "SELECT id FROM users WHERE employee_id IN {}".format(_in(team_ids)),
                team_ids).fetchall()]
        except sqlite3.Error:
            team_user_ids = []

    return {
        "user_id": user["id"] if user is not None else None,
        "roles": roles,
        "role": user["role"] if user is not None else "",
        "employee_id": employee_id,
        "department": department,
        "position_level": position_level,
        "is_admin": "admin" in roles,
        "is_manager": (position_level is not None and position_level <= 3) or reports,
        "team_ids": team_ids,
        "team_departments": team_departments,
        "team_user_ids": team_user_ids,
    }


def resolve_scope(spec, ctx):
    """The slice of data this account gets for this spec."""
    if spec.get("personal"):
        return "own" if ctx["employee_id"] else "none"
    if ctx["is_admin"] and spec.get("admin_all", True):
        return "all"
    if set(ctx["roles"]) & set(spec.get("full_roles", ())):
        return "all"
    base = spec.get("scope", "all")
    if base == "all":
        return "all"
    if base == "own":
        return "own" if ctx["employee_id"] else "none"
    if base == "department":
        return "department" if ctx["department"] else "none"
    if base == "team":
        if ctx["team_ids"]:
            return "team"
        if ctx["is_manager"] and ctx["department"]:
            return "department"
        return "none"
    return "none"


def scope_label(scope, ctx):
    if scope == "all":
        return "all records"
    if scope == "own":
        return "your own records"
    if scope == "team":
        return "your team ({})".format(ctx["department"]) if ctx["department"] else "your team"
    if scope == "department":
        return "department: {}".format(ctx["department"] or "not set")
    return "not available"


def _reason(spec, ctx, scope, present):
    if scope == "none":
        if spec.get("personal"):
            return "Link your account to a staff record to read this report."
        if spec.get("manager_only"):
            return "This account has no team or department to report on."
        return "Assign this account to a department to read this report."
    missing = [table for table in spec["requires"] if table not in present]
    if missing:
        return "Not available yet: the module's storage is missing."
    return ""


def catalogue(ctx, granted, present):
    """The specs this account may see, each with the scope it resolves to."""
    items = []
    for spec in REPORT_SPECS:
        if spec.get("personal") and not ctx["employee_id"]:
            # The page explains the missing staff link once instead of showing a
            # row of reports that can only refuse.
            continue
        if spec.get("modules") and not (set(spec["modules"]) & set(granted)):
            continue
        if spec.get("manager_only") and not ctx["is_manager"]:
            continue
        scope = resolve_scope(spec, ctx)
        reason = _reason(spec, ctx, scope, present)
        available = scope != "none" and not reason
        if not available and spec.get("hide_unavailable"):
            continue
        items.append({
            "key": spec["key"], "label": spec["label"], "group": spec["group"],
            "description": spec["description"], "scope": scope,
            "scope_label": scope_label(scope, ctx),
            "available": available, "reason": reason,
            "columns": spec["columns"], "money": spec["money"],
            "filters": spec.get("filters", ()),
            "note": spec.get("note", ""),
        })
    return items


def groups(items, first=None):
    """The catalogue in display order, with empty groups dropped.

    ``first`` names the group that belongs to the account's own department. It
    is moved straight after the personal reports so a finance officer opens on
    the finance reports instead of scrolling past every other department.
    """
    order = list(GROUP_ORDER)
    if first in order:
        order.remove(first)
        order.insert(1 if order and order[0] == "My reports" else 0, first)
    grouped = []
    for name in order:
        reports = [item for item in items if item["group"] == name]
        if reports:
            grouped.append({"label": name, "reports": reports})
    return grouped


def get(key):
    for spec in REPORT_SPECS:
        if spec["key"] == key:
            return spec
    return None


def item_for(items, key):
    for item in items:
        if item["key"] == key:
            return item
    return None


def _search_pattern(value):
    return "%" + (value.strip().lower()
                  .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")) + "%"


def _wrapped(spec, ctx, scope, start="", end="", q="", filters=None):
    """The scoped and filtered SQL behind a report, with its parameters."""
    inner, params = spec["sql"](ctx, scope, start, end)
    conditions, values = [], []
    column = spec.get("date_column")
    if column and not spec.get("date_in_sql"):
        if start:
            conditions.append("r.{} >= ?".format(column))
            values.append(start[:7] if spec.get("date_kind") == "month" else start)
        if end:
            conditions.append("r.{} <= ?".format(column))
            if spec.get("date_kind") == "month":
                values.append(end[:7])
            elif spec.get("time"):
                values.append(end + "T23:59:59")
            else:
                values.append(end)
    if q and spec.get("search"):
        pattern = _search_pattern(q)
        parts = []
        for name in spec["search"]:
            parts.append("LOWER(COALESCE(CAST(r.{} AS TEXT), '')) LIKE ? ESCAPE '\\'"
                         .format(name))
            values.append(pattern)
        conditions.append("(" + " OR ".join(parts) + ")")
    allowed = {name for name, _label in spec.get("filters", ())}
    for name, value in (filters or {}).items():
        if name in allowed and value:
            conditions.append("CAST(r.{} AS TEXT) = ?".format(name))
            values.append(value)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    wrapped = "SELECT * FROM ({}) r{}".format(inner, where)
    return wrapped, list(params) + values


def query(db, spec, ctx, scope, start="", end="", q="", filters=None, limit=ROW_LIMIT):
    """Run the scoped report with the page filters; returns (rows, total)."""
    wrapped, params = _wrapped(spec, ctx, scope, start, end, q, filters)
    column = spec.get("date_column")
    order = spec.get("order_by") or (
        "r.{} DESC".format(column) if column else "1")
    total = db.execute("SELECT COUNT(*) FROM ({})".format(wrapped),
                       params).fetchone()[0]
    rows = db.execute("{} ORDER BY {} LIMIT ?".format(wrapped, order),
                      params + [limit]).fetchall()
    return rows, total


def summary(db, spec, ctx, scope, start="", end="", q="", filters=None):
    """The totals for the same rows the table and exports read."""
    figures = spec.get("summary") or SUMMARY_BY_KEY.get(spec["key"])
    if figures is None:
        return []
    wrapped, params = _wrapped(spec, ctx, scope, start, end, q, filters)
    return figures(db, wrapped, params)


def _figure_text(figure):
    value = figure["value"]
    if figure["money"]:
        return "{:,.2f}".format(float(value or 0))
    if isinstance(value, float):
        return "{:,.2f}".format(value)
    return "{:,}".format(int(value or 0))


def summary_display(figures):
    return [{"label": figure["label"], "value": _figure_text(figure),
             "money": figure["money"]} for figure in figures]


def summary_text(figures):
    return "Totals - " + " | ".join(
        "{}: {}".format(figure["label"], _figure_text(figure))
        for figure in figures)


def filter_options(db, spec, ctx, scope, start="", end=""):
    """The distinct values behind each filter select, within the scoped report."""
    options = []
    inner, params = spec["sql"](ctx, scope, start, end)
    for name, label in spec.get("filters", ()):
        try:
            rows = db.execute(
                "SELECT DISTINCT CAST(r.{} AS TEXT) AS value FROM ({}) r"
                " WHERE r.{} IS NOT NULL AND CAST(r.{} AS TEXT) <> ''"
                " ORDER BY value LIMIT ?".format(name, inner, name, name),
                list(params) + [FILTER_LIMIT]).fetchall()
            values = [row["value"] for row in rows]
        except sqlite3.Error:
            values = []
        options.append({"name": name, "label": label, "choices": values})
    return options


def display(spec, key, value):
    if value is None:
        return ""
    if key in spec["money"]:
        try:
            return "{:,.2f}".format(float(value))
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return "{:,.2f}".format(value)
    return str(value)


def display_rows(spec, rows):
    return [[display(spec, key, row[key]) for key, _label in spec["columns"]]
            for row in rows]


def csv_bytes(spec, rows, meta):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for line in meta:
        writer.writerow([line])
    writer.writerow([])
    writer.writerow([label for _key, label in spec["columns"]])
    for row in rows:
        writer.writerow(["" if row[key] is None else row[key]
                         for key, _label in spec["columns"]])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
