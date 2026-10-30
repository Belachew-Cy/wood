"""Central report centre and legacy report screens."""

from core.access import current_user
from core.access import effective_permissions
from core.access import login_required
from core.access import perm
from core.access import role_name_for
from core.access import user_extra_roles
from core.config import DATE_PATTERN
from core.db import get_db
from core.formatting import money_etb
from core.formatting import recent_month_keys
from core.routing import route
from datetime import date
from departments.hr import DEFAULT_SETTINGS
from flask import abort
from flask import current_app
from flask import render_template
from flask import request
from flask import url_for
from services.audit import auditor_table_names
from services.audit import log_activity
import re
import reporting
import reports_export as report_files
import sqlite3


REPORT_SECTIONS = ("workforce", "finance", "inventory", "procurement", "approvals")


@route("/admin/reports")
@perm("accounts", "audit")
def admin_reports():
    db = get_db()
    months = recent_month_keys(12)
    month = request.args.get("month", months[-1])
    if re.match(r"^\d{4}-\d{2}$", month) is None:
        month = months[-1]
    until = "{}-{:02d}-31".format(month[:4], int(month[5:7]))

    workforce = {
        "headcount": db.execute(
            "SELECT COUNT(*) AS n FROM employees").fetchone()["n"],
        "active": db.execute(
            "SELECT COUNT(*) AS n FROM employees WHERE employment_status = 'Active'"
        ).fetchone()["n"],
        "on_leave": db.execute(
            "SELECT COUNT(*) AS n FROM employees WHERE employment_status = 'On Leave'"
        ).fetchone()["n"],
        "payroll": db.execute(
            "SELECT COALESCE(SUM(net_salary), 0) AS n FROM payroll WHERE payroll_month = ?",
            (month,)).fetchone()["n"],
        "payslips": db.execute(
            "SELECT COUNT(*) AS n FROM payroll WHERE payroll_month = ?",
            (month,)).fetchone()["n"],
        "attendance": [{
            "status": row["status"], "n": row["n"],
        } for row in db.execute("""
            SELECT status, COUNT(*) AS n FROM attendance
            WHERE attendance_date BETWEEN ? AND ? GROUP BY status ORDER BY n DESC
        """, (month + "-01", until)).fetchall()],
        "departments": [{
            "label": row["department"], "value": row["n"],
            "display": "{} employee{}".format(row["n"], "" if row["n"] == 1 else "s"),
        } for row in db.execute("""
            SELECT department, COUNT(*) AS n FROM employees
            GROUP BY department ORDER BY n DESC, department
        """).fetchall()],
    }

    finance = {
        "income": db.execute("""
            SELECT COALESCE(SUM(amount_etb), 0) AS n FROM finance.finance_transactions
            WHERE transaction_type = 'income' AND transaction_date BETWEEN ? AND ?
        """, (month + "-01", until)).fetchone()["n"],
        "expense": db.execute("""
            SELECT COALESCE(SUM(amount_etb), 0) AS n FROM finance.finance_transactions
            WHERE transaction_type = 'expense' AND transaction_date BETWEEN ? AND ?
        """, (month + "-01", until)).fetchone()["n"],
        "by_category": [{
            "label": row["category"], "value": float(row["n"]),
            "display": "{} {:,.2f} ETB".format(row["kind"].title(), float(row["n"])),
        } for row in db.execute("""
            SELECT category, SUM(amount_etb) AS n, transaction_type AS kind
            FROM finance.finance_transactions
            WHERE transaction_date BETWEEN ? AND ?
            GROUP BY category, transaction_type ORDER BY n DESC LIMIT 10
        """, (month + "-01", until)).fetchall()],
        "invoices": [{
            "invoice_number": row["invoice_number"], "party_name": row["party_name"],
            "invoice_type": row["invoice_type"], "total_etb": money_etb(row["total_etb"]),
            "status": row["status"], "due_date": row["due_date"] or "",
        } for row in db.execute("""
            SELECT invoice_number, party_name, invoice_type, total_etb, status, due_date
            FROM finance.finance_invoices WHERE issue_date BETWEEN ? AND ?
            ORDER BY total_etb DESC LIMIT 20
        """, (month + "-01", until)).fetchall()],
    }
    finance["net"] = float(finance["income"]) - float(finance["expense"])

    inventory = {
        "items": db.execute(
            "SELECT COUNT(*) AS n FROM inventory_items WHERE active = 1").fetchone()["n"],
        "value": db.execute("""
            SELECT COALESCE(SUM(quantity * unit_cost), 0) AS n FROM inventory_items
            WHERE active = 1""").fetchone()["n"],
        "low": [{
            "name": row["name"], "sku": row["sku"], "quantity": row["quantity"],
            "min_stock": row["min_stock"], "unit": row["unit"],
        } for row in db.execute("""
            SELECT name, sku, quantity, min_stock, unit FROM inventory_items
            WHERE active = 1 AND quantity <= min_stock ORDER BY name""").fetchall()],
        "movements": [{
            "movement_type": row["movement_type"], "n": row["n"],
            "reference_type": row["reference_type"],
        } for row in db.execute("""
            SELECT movement_type, reference_type, COUNT(*) AS n FROM stock_movements
            WHERE created_at BETWEEN ? AND ?
            GROUP BY movement_type, reference_type ORDER BY n DESC
        """, (month + "-01", until + " 23:59:59")).fetchall()],
    }

    procurement = {
        "requests": [{
            "request_number": row["request_number"], "item_name": row["item_name"],
            "quantity": row["quantity"], "unit": row["unit"],
            "estimated_cost": money_etb(row["estimated_cost"]), "status": row["status"],
        } for row in db.execute("""
            SELECT request_number, item_name, quantity, unit, estimated_cost, status
            FROM finance.procurement_requests ORDER BY id DESC LIMIT 20""").fetchall()],
        "orders": [{
            "order_number": row["order_number"], "supplier": row["supplier"],
            "item_name": row["item_name"], "total_cost": money_etb(row["total_cost"]),
            "status": row["status"],
        } for row in db.execute("""
            SELECT o.order_number, s.name AS supplier, o.item_name, o.total_cost, o.status
            FROM finance.procurement_orders AS o
            JOIN finance.procurement_suppliers AS s ON s.id = o.supplier_id
            ORDER BY o.id DESC LIMIT 20""").fetchall()],
        "committed": db.execute("""
            SELECT COALESCE(SUM(total_cost), 0) AS n FROM finance.procurement_orders
            WHERE status != 'Cancelled'""").fetchone()["n"],
    }

    approvals = {
        "by_status": [{
            "label": row["status"], "value": row["n"],
            "display": "{} request{}".format(row["n"], "" if row["n"] == 1 else "s"),
        } for row in db.execute(
            "SELECT status, COUNT(*) AS n FROM approval_requests GROUP BY status"
        ).fetchall()],
        "recent": [{
            "title": row["title"], "request_type": row["request_type"],
            "amount": money_etb(row["amount"]), "status": row["status"],
            "by": row["created_by_name"],
        } for row in db.execute("""
            SELECT title, request_type, amount, status, created_by_name
            FROM approval_requests ORDER BY id DESC LIMIT 20""").fetchall()],
        "value": db.execute("""
            SELECT COALESCE(SUM(amount), 0) AS n FROM approval_requests
            WHERE status = 'Pending'""").fetchone()["n"],
    }

    audit = [{
        "label": row["module"], "value": row["n"],
        "display": "{} event{}".format(row["n"], "" if row["n"] == 1 else "s"),
    } for row in db.execute("""
        SELECT module, COUNT(*) AS n FROM audit_logs
        WHERE occurred_at BETWEEN ? AND ?
        GROUP BY module ORDER BY n DESC LIMIT 10
    """, (month + "-01", until + " 23:59:59")).fetchall()]

    summary = {
        "workforce": "{} employees · {:,.2f} ETB payroll".format(
            workforce["headcount"], float(workforce["payroll"])),
        "finance": "{:,.2f} ETB net".format(finance["net"]),
        "inventory": "{} items · {:,.2f} ETB stock".format(
            inventory["items"], float(inventory["value"])),
        "procurement": "{:,.2f} ETB committed".format(float(procurement["committed"])),
        "approvals": "{:,.2f} ETB awaiting a decision".format(float(approvals["value"])),
    }

    wanted = request.args.get("csv")
    if wanted in REPORT_SECTIONS:
        return csv_report(wanted, month, {
            "workforce": workforce, "finance": finance, "inventory": inventory,
            "procurement": procurement, "approvals": approvals,
        }[wanted])

    return render_template("Admin/reports.html", month=month, months=months,
                           workforce=workforce, finance=finance,
                           inventory=inventory, procurement=procurement,
                           approvals=approvals, audit=audit, summary=summary,
                           sections=REPORT_SECTIONS)


def csv_report(section, month, data):
    """A report as plain CSV, built from the same rows the screen shows.

    Each builder reads keys only its own section carries, so they stay lazy and
    only the requested one runs.
    """
    import csv
    import io

    builders = {
        "workforce": lambda: [
            ("Headcount", data["headcount"]), ("Active", data["active"]),
            ("On leave", data["on_leave"]), ("Payslips", data["payslips"]),
            ("Payroll (ETB)", money_etb(data["payroll"])),
        ] + [("Attendance " + row["status"], row["n"]) for row in data["attendance"]]
          + [(row["label"], row["value"]) for row in data["departments"]],
        "finance": lambda: [
            ("Income (ETB)", money_etb(data["income"])),
            ("Expense (ETB)", money_etb(data["expense"])),
            ("Net (ETB)", money_etb(data["net"])),
        ] + [(row["label"], row["display"]) for row in data["by_category"]],
        "inventory": lambda: [
            ("Active items", data["items"]),
            ("Stock value (ETB)", money_etb(data["value"])),
        ] + [(row["name"], "{} of {} {}".format(
            row["quantity"], row["min_stock"], row["unit"]))
            for row in data["low"]],
        "procurement": lambda: [
            ("Committed (ETB)", money_etb(data["committed"])),
        ] + [(row["request_number"], "{} {}".format(row["quantity"], row["unit"]))
             + (row["estimated_cost"], row["status"])
             for row in data["requests"]],
        "approvals": lambda: [
            (row["label"], row["value"]) for row in data["by_status"]
        ] + [(row["title"], row["status"], row["amount"]) for row in data["recent"]],
    }

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Dereje Belay Wood & Kitchen", section.title(), month])
    for row in builders[section]():
        writer.writerow(list(row))

    body = buffer.getvalue().encode("utf-8-sig")
    response = current_app.make_response(body)
    response.mimetype = "text/csv"
    response.headers["Content-Disposition"] = "attachment; filename={}-{}.csv".format(
        section, month)
    return response


def report_company(db):
    try:
        row = db.execute(
            "SELECT setting_value FROM hr_settings WHERE setting_key = 'company_name'"
        ).fetchone()
        if row is not None and row[0]:
            return row[0]
    except sqlite3.Error:
        pass
    return DEFAULT_SETTINGS["company_name"]


def report_access():
    user = current_user()
    db = get_db()
    granted = effective_permissions(user)
    ctx = reporting.context(db, user, user_extra_roles(db, user["id"]))
    items = reporting.catalogue(ctx, granted, auditor_table_names(db))
    return db, user, granted, ctx, items


REPORT_GROUP_BY_ROLE = {
    "hr": "Workforce",
    "finance": "Finance",
    "budget": "Budget",
    "procurement": "Purchasing",
    "inventory": "Inventory",
    "payroll": "Payroll",
    "auditor": "Administration",
}


def report_group_for(ctx):
    for role in ctx["roles"]:
        group = REPORT_GROUP_BY_ROLE.get(role)
        if group:
            return group
    return None


def report_selection(items):
    key = (request.args.get("report") or "").strip()
    if not key:
        return None
    item = reporting.item_for(items, key)
    if item is None or not item["available"]:
        log_activity("DENY", "reports",
                     "Refused report '{}' on {}".format(key[:80], request.path),
                     outcome="DENIED", details={"report": key[:80]})
        abort(403)
    return item


def report_dates():
    start = (request.args.get("start_date") or "").strip()
    end = (request.args.get("end_date") or "").strip()
    for value in (start, end):
        if value and not DATE_PATTERN.fullmatch(value):
            abort(400, "Dates must use YYYY-MM-DD.")
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                abort(400, "Dates must be real calendar dates.")
    if start and end and start > end:
        abort(400, "The start date cannot be after the end date.")
    return start, end


def report_filters(spec):
    values = {}
    for name, _label in spec.get("filters", ()):
        value = (request.args.get("filter_" + name) or "").strip()[:200]
        if value:
            values[name] = value
    return values


def report_meta(db, user, item, ctx, start, end, q, filters):
    company = report_company(db)
    if start or end:
        period = "{} to {}".format(start or "the beginning", end or "today")
    else:
        period = "All dates"
    lines = [
        company,
        "{} - {}".format(item["label"], item["description"]),
        "Scope: {} - Role: {}".format(item["scope_label"],
                                      role_name_for(user["role"], db)),
        "Period: {}".format(period),
    ]
    if q:
        lines.append("Search: {}".format(q))
    for name, value in filters.items():
        lines.append("{}: {}".format(name.replace("_", " ").title(), value))
    lines.append("Prepared by {} on {}".format(user["full_name"],
                                               date.today().isoformat()))
    return company, lines


@route("/reports")
@login_required()
def reports_centre():
    db, user, granted, ctx, items = report_access()
    item = report_selection(items)
    context = {
        "grouped": reporting.groups(items, report_group_for(ctx)),
        "items": items,
        "ctx": ctx,
        "role_label": role_name_for(user["role"], db),
        "company": report_company(db),
        "legacy_reports": bool({"accounts", "audit"} & granted),
        "today": date.today().strftime("%d %b %Y"),
        "employee_linked": bool(ctx["employee_id"]),
    }
    if item is None:
        return render_template("reports/index.html", item=None, **context)

    spec = reporting.get(item["key"])
    start, end = report_dates()
    q = (request.args.get("q") or "").strip()[:120]
    filters = report_filters(spec)
    try:
        rows, total = reporting.query(db, spec, ctx, item["scope"],
                                      start, end, q, filters)
        figures = reporting.summary(db, spec, ctx, item["scope"],
                                    start, end, q, filters)
        options = reporting.filter_options(db, spec, ctx, item["scope"],
                                           start, end)
    except sqlite3.Error:
        log_activity("ERROR", "reports",
                     "Report '{}' failed on {}".format(item["key"], request.path),
                     outcome="FAILURE")
        abort(500)

    cells = [[{"text": reporting.display(spec, key, row[key]),
               "money": key in spec["money"]}
              for key, _label in spec["columns"]] for row in rows]
    export_args = {"report": item["key"], "q": q or None,
                   "start_date": start or None, "end_date": end or None}
    export_args.update({"filter_" + name: value
                        for name, value in filters.items()})
    export_urls = {fmt: url_for("reports_export", format=fmt, **export_args)
                   for fmt in ("csv", "xlsx", "pdf")}
    return render_template(
        "reports/index.html", item=item, spec=spec, **context,
        rows=cells, total=total, showing=len(cells), q=q,
        filters=filters, options=options, start_date=start, end_date=end,
        summary=reporting.summary_display(figures),
        export_urls=export_urls,
        prepared="{} - {}".format(user["full_name"],
                                  date.today().strftime("%d %b %Y")),
    )


@route("/reports/export")
@login_required()
def reports_export():
    db, user, granted, ctx, items = report_access()
    item = report_selection(items)
    if item is None:
        abort(400, "Choose a report to export.")
    fmt = (request.args.get("format") or "csv").strip().lower()
    if fmt not in ("csv", "xlsx", "pdf"):
        abort(400, "Unsupported export format.")

    spec = reporting.get(item["key"])
    start, end = report_dates()
    q = (request.args.get("q") or "").strip()[:120]
    filters = report_filters(spec)
    try:
        rows, total = reporting.query(db, spec, ctx, item["scope"], start, end,
                                      q, filters, limit=reporting.EXPORT_LIMIT)
        figures = reporting.summary(db, spec, ctx, item["scope"], start, end,
                                    q, filters)
    except sqlite3.Error:
        log_activity("ERROR", "reports",
                     "Export of '{}' failed on {}".format(item["key"], request.path),
                     outcome="FAILURE")
        abort(500)

    company, meta = report_meta(db, user, item, ctx, start, end, q, filters)
    if figures:
        meta.append(reporting.summary_text(figures))
    if total > len(rows):
        meta.append("Showing the first {} of {} rows".format(len(rows), total))
    table = [[row[key] for key, _label in spec["columns"]] for row in rows]
    filename = "report-{}-{}.{}".format(
        item["key"].replace("_", "-"), date.today().isoformat(), fmt)
    if fmt == "csv":
        body = reporting.csv_bytes(spec, rows, meta)
        mimetype = "text/csv; charset=utf-8"
    elif fmt == "xlsx":
        body = report_files.xlsx_bytes(
            "{} - {}".format(company, item["label"]), meta[1:],
            spec["columns"], table, spec["money"])
        mimetype = ("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet")
    else:
        body = report_files.pdf_bytes(item["label"], meta, spec["columns"],
                                      table, spec["money"])
        mimetype = "application/pdf"

    log_activity("EXPORT", "reports",
                 "Downloaded the {} report as {} ({} of {} rows)".format(
                     item["label"], fmt.upper(), len(rows), total),
                 record_type="report", record_id=item["key"],
                 details={"format": fmt, "scope": item["scope"],
                          "rows": len(rows)})
    response = current_app.make_response(body)
    response.headers["Content-Type"] = mimetype
    response.headers["Content-Disposition"] = \
        'attachment; filename="{}"'.format(filename)
    return response
