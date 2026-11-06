"""Finance transactions, invoices and reports."""

from core.access import current_user
from core.access import perm
from core.db import get_db
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from services.audit import log_activity
from services.budget import budget_problem_for
from services.budget import budget_record_finance_spend
from services.budget import budget_spendable_allocations
from services.recency import stamp as stamp_record
import sqlite3


finance_bp = Blueprint("finance", __name__, url_prefix="/finance")


CURRENCIES = {"ETB", "USD", "EUR", "GBP"}


PAYMENT_METHODS = {"Cash", "Bank Transfer", "Card", "Mobile Money", "Other"}


def parse_amount(value, field="Amount"):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a valid number.")
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{field} must be greater than zero.")
    return amount.quantize(Decimal("0.01"))


def parse_rate(value):
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid exchange rate.")
    if not rate.is_finite() or rate <= 0:
        raise ValueError("Exchange rate must be greater than zero.")
    return rate


def parse_date(value, field="Date"):
    try:
        date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"Enter a valid {field.lower()}.")
    return value


def etb_value(amount, currency, rate):
    """Convert a transaction value into its ETB equivalent."""
    if currency == "ETB":
        rate = Decimal("1")
    return (amount * rate).quantize(Decimal("0.01"))


def invoice_balances(db):
    """One row per invoice with the amount paid and the remaining balance."""
    return db.execute("""
        SELECT i.*,
            COALESCE(p.paid, 0) AS paid_etb,
            MAX(0, i.total_etb - COALESCE(p.paid, 0)) AS balance_etb,
            o.order_number AS procurement_order_number,
            a.allocation_name AS budget_allocation_name
        FROM finance.finance_invoices i
        LEFT JOIN (
            SELECT invoice_id, SUM(amount_etb) AS paid
            FROM finance.finance_payments
            GROUP BY invoice_id
        ) p ON p.invoice_id = i.id
        LEFT JOIN finance.procurement_orders o ON o.id = i.procurement_order_id
        LEFT JOIN budget_allocations a ON a.id = i.budget_allocation_id
        ORDER BY i.issue_date DESC, i.id DESC
    """).fetchall()


@finance_bp.route("/")
@perm("finance")
def dashboard():
    db = get_db()
    totals = db.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income'
                THEN amount_etb ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN transaction_type = 'expense'
                THEN amount_etb ELSE 0 END), 0) AS expenses
        FROM finance.finance_transactions
    """).fetchone()

    recent = db.execute("""
        SELECT * FROM finance.finance_transactions
        ORDER BY transaction_date DESC, id DESC LIMIT 10
    """).fetchall()

    invoices = db.execute("""
        SELECT * FROM finance.finance_invoices
        ORDER BY issue_date DESC, id DESC LIMIT 5
    """).fetchall()

    balances = db.execute("""
        SELECT i.invoice_type,
            COALESCE(SUM(i.total_etb - COALESCE(p.paid, 0)), 0) AS balance
        FROM finance.finance_invoices i
        LEFT JOIN (
            SELECT invoice_id, SUM(amount_etb) AS paid
            FROM finance.finance_payments
            GROUP BY invoice_id
        ) p ON p.invoice_id = i.id
        WHERE i.status != 'Cancelled'
        GROUP BY i.invoice_type
    """).fetchall()

    by_type = {row["invoice_type"]: row["balance"] for row in balances}
    income = totals["income"]
    expenses = totals["expenses"]

    return render_template(
        "finance/finance_dashboard.html",
        income=income,
        expenses=expenses,
        net=income - expenses,
        receivables=by_type.get("customer", 0),
        payables=by_type.get("supplier", 0),
        recent=recent,
        invoices=invoices,
    )


@finance_bp.route("/transactions", methods=["GET", "POST"])
@perm("finance")
def transactions():
    if request.method == "POST":
        form = request.form
        try:
            tx_date = parse_date(form.get("transaction_date", ""), "Transaction date")
            tx_type = form.get("transaction_type", "")
            if tx_type not in {"income", "expense"}:
                raise ValueError("Choose income or expense.")
            category = form.get("category", "").strip()
            description = form.get("description", "").strip()
            if not category or not description:
                raise ValueError("Category and description are required.")
            currency = form.get("currency", "ETB")
            if currency not in CURRENCIES:
                raise ValueError("Choose a supported currency.")
            amount = parse_amount(form.get("amount", ""))
            rate = parse_rate(form.get("exchange_rate", "1"))
            amount_etb = etb_value(amount, currency, rate)
            method = form.get("payment_method", "Cash")
            if method not in PAYMENT_METHODS:
                raise ValueError("Choose a valid payment method.")

            db = get_db()
            allocation_value = form.get("budget_allocation_id", "").strip()
            allocation_id = None
            if allocation_value:
                try:
                    allocation_id = int(allocation_value)
                except (TypeError, ValueError):
                    raise ValueError("Choose a valid budget allocation.")
                allocation = db.execute(
                    "SELECT * FROM budget_allocations WHERE id = ?",
                    (allocation_id,)).fetchone()
                if allocation is None:
                    raise ValueError("Choose a valid budget allocation.")
                if tx_type != "expense":
                    raise ValueError("Only an expense can draw on a budget allocation.")
                if allocation["currency"] != currency:
                    raise ValueError(
                        "The transaction currency must match the budget allocation.")
                problem = budget_problem_for(db, allocation_id, amount)
                if problem:
                    raise ValueError(problem)

            cursor = db.execute("""
                INSERT INTO finance.finance_transactions (
                    transaction_date, transaction_type, category, description,
                    party_name, employee_id, currency, amount, exchange_rate,
                    amount_etb, payment_method, reference, created_by,
                    budget_allocation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx_date, tx_type, category, description,
                form.get("party_name", "").strip(),
                form.get("employee_id") or None,
                currency, str(amount), str(rate), str(amount_etb), method,
                form.get("reference", "").strip(), str(session["user_id"]),
                allocation_id,
            ))
            if allocation_id is not None:
                user = current_user()
                expenditure_id = budget_record_finance_spend(
                    db, allocation_id, description, amount, currency,
                    form.get("reference", "").strip(), user["id"],
                    user["full_name"], finance_transaction_id=cursor.lastrowid)
                db.execute(
                    "UPDATE finance.finance_transactions SET budget_expenditure_id = ?"
                    " WHERE id = ?", (expenditure_id, cursor.lastrowid))
            stamp_record(db, "finance_transactions", cursor.lastrowid)
            db.commit()
            log_activity("CREATE", "finance",
                         "{} transaction saved: {} ({} {} ETB)".format(
                             tx_type, description, str(amount_etb), currency),
                         record_type="finance_transaction", record_id=cursor.lastrowid)
            flash("Transaction saved successfully.", "success")
            return redirect(url_for("finance.transactions"))
        except ValueError as exc:
            log_activity("CREATE", "finance",
                         "Transaction rejected: {}".format(exc),
                         outcome="FAILURE", record_type="finance_transaction")
            flash(str(exc), "error")

    db = get_db()
    records = db.execute("""
        SELECT t.*, a.allocation_name, a.department AS allocation_department
        FROM finance.finance_transactions AS t
        LEFT JOIN budget_allocations AS a ON a.id = t.budget_allocation_id
        ORDER BY t.transaction_date DESC, t.id DESC LIMIT 300
    """).fetchall()
    employees = db.execute(
        "SELECT id, full_name AS name FROM employees ORDER BY full_name"
    ).fetchall()

    return render_template(
        "finance/finance_transactions.html",
        transactions=records,
        employees=employees,
        allocations=budget_spendable_allocations(db),
        today=date.today().isoformat(),
    )


@finance_bp.route("/invoices", methods=["GET", "POST"])
@perm("finance")
def invoices():
    if request.method == "POST":
        form = request.form
        try:
            number = form.get("invoice_number", "").strip()
            invoice_type = form.get("invoice_type", "")
            party = form.get("party_name", "").strip()
            issue_date = parse_date(form.get("issue_date", ""), "Issue date")
            due_date = form.get("due_date", "").strip() or None
            if due_date:
                parse_date(due_date, "Due date")
                if due_date < issue_date:
                    raise ValueError("Due date cannot precede issue date.")
            if not number or not party:
                raise ValueError("Invoice number and customer/supplier are required.")
            if invoice_type not in {"customer", "supplier"}:
                raise ValueError("Choose a valid invoice type.")
            currency = form.get("currency", "ETB")
            if currency not in CURRENCIES:
                raise ValueError("Choose a supported currency.")
            amount = parse_amount(form.get("total_amount", ""), "Invoice amount")
            rate = parse_rate(form.get("exchange_rate", "1"))
            total_etb = etb_value(amount, currency, rate)

            db = get_db()
            allocation_value = form.get("budget_allocation_id", "").strip()
            allocation_id = None
            if allocation_value:
                try:
                    allocation_id = int(allocation_value)
                except (TypeError, ValueError):
                    raise ValueError("Choose a valid budget allocation.")
                allocation = db.execute(
                    "SELECT * FROM budget_allocations WHERE id = ?",
                    (allocation_id,)).fetchone()
                if allocation is None:
                    raise ValueError("Choose a valid budget allocation.")
                if invoice_type != "supplier":
                    raise ValueError(
                        "Only a supplier invoice can draw on a budget allocation.")
                if allocation["currency"] != currency:
                    raise ValueError(
                        "The invoice currency must match the budget allocation.")
                problem = budget_problem_for(db, allocation_id, amount)
                if problem:
                    raise ValueError(problem)

            cursor = db.execute("""
                INSERT INTO finance.finance_invoices (
                    invoice_number, invoice_type, party_name, party_contact,
                    issue_date, due_date, currency, total_amount, exchange_rate,
                    total_etb, notes, status, created_by, budget_allocation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Unpaid', ?, ?)
            """, (
                number, invoice_type, party,
                form.get("party_contact", "").strip(),
                issue_date, due_date, currency, str(amount), str(rate),
                str(total_etb), form.get("notes", "").strip(),
                str(session["user_id"]), allocation_id,
            ))
            if allocation_id is not None:
                user = current_user()
                budget_record_finance_spend(
                    db, allocation_id,
                    "Supplier invoice {} · {}".format(number, party),
                    amount, currency, number, user["id"], user["full_name"],
                    finance_invoice_id=cursor.lastrowid)
            stamp_record(db, "finance_invoices", cursor.lastrowid)
            db.commit()
            log_activity("CREATE", "finance",
                         "{} invoice {} for {} ({} ETB)".format(
                             invoice_type, number, party, str(total_etb)),
                         record_type="finance_invoice", record_id=cursor.lastrowid)
            flash("Invoice saved successfully.", "success")
            return redirect(url_for("finance.invoices"))
        except sqlite3.IntegrityError:
            log_activity("CREATE", "finance",
                         "Invoice rejected: duplicate invoice number",
                         outcome="FAILURE", record_type="finance_invoice")
            flash("Invoice number already exists.", "error")
        except ValueError as exc:
            log_activity("CREATE", "finance",
                         "Invoice rejected: {}".format(exc),
                         outcome="FAILURE", record_type="finance_invoice")
            flash(str(exc), "error")

    return render_template(
        "finance/finance_invoices.html",
        invoices=invoice_balances(get_db()),
        allocations=budget_spendable_allocations(get_db()),
        today=date.today().isoformat(),
    )


@finance_bp.route("/invoices/<int:invoice_id>/payment", methods=["POST"])
@perm("finance")
def add_payment(invoice_id):
    form = request.form
    db = get_db()
    try:
        payment_date = parse_date(form.get("payment_date", ""), "Payment date")
        amount = parse_amount(form.get("amount", ""), "Payment amount")
        method = form.get("payment_method", "Cash")
        if method not in PAYMENT_METHODS:
            raise ValueError("Choose a valid payment method.")

        invoice = db.execute(
            "SELECT * FROM finance.finance_invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        if invoice is None:
            abort(404)
        if invoice["status"] == "Cancelled":
            raise ValueError("Cannot record payment for a cancelled invoice.")

        paid = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM finance.finance_payments WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()["paid"]
        remaining = Decimal(str(invoice["total_amount"])) - Decimal(str(paid))
        if amount > remaining:
            raise ValueError("Payment exceeds the outstanding invoice balance.")

        amount_etb = (amount * Decimal(str(invoice["exchange_rate"]))).quantize(Decimal("0.01"))
        payment_row = db.execute("""
            INSERT INTO finance.finance_payments (
                invoice_id, payment_date, amount, amount_etb,
                payment_method, reference, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            invoice_id, payment_date, str(amount), str(amount_etb), method,
            form.get("reference", "").strip(), str(session["user_id"]),
        ))

        new_paid = Decimal(str(paid)) + amount
        status = "Paid" if new_paid >= Decimal(str(invoice["total_amount"])) else "Partially Paid"
        db.execute("UPDATE finance.finance_invoices SET status = ? WHERE id = ?", (status, invoice_id))
        stamp_record(db, "finance_payments", payment_row.lastrowid)
        stamp_record(db, "finance_invoices", invoice_id)
        db.commit()
        log_activity("CREATE", "finance",
                     "Payment of {} ETB on invoice #{} (now {})".format(
                         str(amount_etb), invoice_id, status),
                     record_type="finance_payment", record_id=payment_row.lastrowid)
        flash("Payment recorded successfully.", "success")
    except ValueError as exc:
        log_activity("CREATE", "finance",
                     "Payment rejected on invoice #{}: {}".format(invoice_id, exc),
                     outcome="FAILURE", record_type="finance_payment", record_id=invoice_id)
        flash(str(exc), "error")

    return redirect(url_for("finance.invoices"))


@finance_bp.route("/reports")
@perm("finance")
def reports():
    start = request.args.get("start", date.today().replace(day=1).isoformat())
    end = request.args.get("end", date.today().isoformat())
    try:
        parse_date(start, "Start date")
        parse_date(end, "End date")
        if start > end:
            raise ValueError("Start date must precede end date.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("finance.reports"))

    db = get_db()
    totals = db.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income'
                THEN amount_etb ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN transaction_type = 'expense'
                THEN amount_etb ELSE 0 END), 0) AS expenses
        FROM finance.finance_transactions
        WHERE transaction_date BETWEEN ? AND ?
    """, (start, end)).fetchone()

    categories = db.execute("""
        SELECT transaction_type, category, SUM(amount_etb) AS total
        FROM finance.finance_transactions
        WHERE transaction_date BETWEEN ? AND ?
        GROUP BY transaction_type, category
        ORDER BY transaction_type, total DESC
    """, (start, end)).fetchall()

    records = db.execute("""
        SELECT t.*, a.allocation_name, a.department AS allocation_department
        FROM finance.finance_transactions AS t
        LEFT JOIN budget_allocations AS a ON a.id = t.budget_allocation_id
        WHERE t.transaction_date BETWEEN ? AND ?
        ORDER BY t.transaction_date DESC, t.id DESC
    """, (start, end)).fetchall()

    return render_template(
        "finance/finance_reports.html",
        start=start,
        end=end,
        totals=totals,
        net=totals["income"] - totals["expenses"],
        categories=categories,
        transactions=records,
    )


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(finance_bp)
