
"""
Finance Routes
Dereje Wood & Kitchen Cabinet Management System

Connects Finance services to Flask.
Uses the existing Finance blueprint and shared RBAC system.
"""

from functools import wraps
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort
)

from . import finance_bp
from . import services


# =========================================================
# APPLICATION INTEGRATION ADAPTERS
# =========================================================

def get_db():
    """
    Replace this function with your existing application
    database connection helper.

    Do not create another database connection system here.
    """
    raise NotImplementedError(
        "Connect get_db() to your existing database helper."
    )


def get_authenticated_user_id():
    """
    Replace this with your existing authenticated-user helper.
    """
    user_id = session.get("user_id")

    if not user_id:
        abort(401)

    return user_id


def check_finance_permission(permission_code):
    """
    Replace this with your existing shared RBAC permission checker.

    Example:
        return current_user.has_permission(permission_code)

    Do not create a separate Finance permission system.
    """
    raise NotImplementedError(
        "Connect Finance routes to your shared RBAC checker."
    )


def finance_permission_required(permission_code):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user_id = get_authenticated_user_id()

            if not check_finance_permission(permission_code):
                abort(403)

            return view(*args, **kwargs)

        return wrapped
    return decorator


# =========================================================
# DATABASE CONNECTION
# =========================================================

def finance_db():
    db = get_db()

    # Finance models expect dictionary-style SQLite rows.
    # Configure this in your existing database helper if possible.
    return db


# =========================================================
# FINANCE DASHBOARD
# =========================================================

@finance_bp.route("/", methods=["GET"])
@finance_permission_required("finance.dashboard.view")
def dashboard():

    db = finance_db()

    summary = services.get_finance_dashboard_summary(db)

    recent_journals = db.execute(
        """
        SELECT
            id,
            entry_number,
            journal_date,
            description,
            total_debit,
            total_credit,
            status
        FROM finance_journal_entries
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()

    integration_pending = db.execute(
        """
        SELECT COUNT(*) AS total
        FROM finance_integration_records
        WHERE status IN ('Received', 'Pending Review')
        """
    ).fetchone()["total"]

    pending_approvals = db.execute(
        """
        SELECT COUNT(*) AS total
        FROM finance_journal_entries
        WHERE status = 'Pending Approval'
        """
    ).fetchone()["total"]

    return render_template(
        "dashboard.html",
        income=summary["income"],
        expenses=summary["expenses"],
        net=summary["net"],
        currency=summary["currency"],
        recent=recent_journals,
        pending_approvals=pending_approvals,
        integration_pending=integration_pending
    )


# =========================================================
# CHART OF ACCOUNTS
# =========================================================

@finance_bp.route("/accounts", methods=["GET"])
@finance_permission_required("finance.accounts.view")
def accounts():

    db = finance_db()

    account_list = services.get_accounts(db)

    return render_template(
        "chart_of_accounts.html",
        accounts=account_list
    )


@finance_bp.route("/accounts/create", methods=["GET", "POST"])
@finance_permission_required("finance.accounts.create")
def create_account():

    if request.method == "POST":

        db = finance_db()
        user_id = get_authenticated_user_id()

        try:
            services.create_account(
                db=db,
                account_code=request.form.get("account_code", "").strip(),
                account_name=request.form.get("account_name", "").strip(),
                account_type=request.form.get("account_type", "").strip(),
                normal_balance=request.form.get("normal_balance", "").strip(),
                user_id=user_id,
                parent_account_id=(
                    request.form.get("parent_account_id") or None
                ),
                description=request.form.get("description", "").strip(),
                is_control_account=(
                    request.form.get("is_control_account") == "1"
                ),
                allow_manual_posting=(
                    request.form.get("allow_manual_posting") == "1"
                )
            )

            flash("Account created successfully.", "success")
            return redirect(url_for("finance.accounts"))

        except (ValueError, Exception) as exc:
            flash(f"Unable to create account: {exc}", "danger")

    return render_template("chart_of_accounts.html", accounts=[])


# =========================================================
# JOURNAL ENTRIES
# =========================================================

@finance_bp.route("/journals", methods=["GET"])
@finance_permission_required("finance.journals.view")
def journals():

    db = finance_db()

    journal_list = db.execute(
        """
        SELECT *
        FROM finance_journal_entries
        ORDER BY journal_date DESC, id DESC
        """
    ).fetchall()

    return render_template(
        "journal_entries.html",
        journals=journal_list
    )


@finance_bp.route("/journals/create", methods=["GET", "POST"])
@finance_permission_required("finance.journals.create")
def create_journal():

    if request.method == "POST":

        db = finance_db()
        user_id = get_authenticated_user_id()

        try:
            journal_id = services.create_journal(
                db=db,
                entry_number=request.form.get("entry_number", "").strip(),
                period_id=int(request.form.get("period_id")),
                journal_date=request.form.get("journal_date"),
                description=request.form.get("description", "").strip(),
                user_id=user_id,
                currency=request.form.get("currency", "ETB"),
                exchange_rate=request.form.get("exchange_rate", "1")
            )

            flash("Journal created successfully.", "success")

            return redirect(
                url_for(
                    "finance.journal_detail",
                    journal_id=journal_id
                )
            )

        except Exception as exc:
            flash(f"Unable to create journal: {exc}", "danger")

    db = finance_db()

    periods = db.execute(
        """
        SELECT id, period_name
        FROM finance_periods
        WHERE status = 'Open'
        ORDER BY start_date DESC
        """
    ).fetchall()

    return render_template(
        "journal_entries.html",
        journals=[],
        periods=periods
    )


@finance_bp.route("/journals/<int:journal_id>", methods=["GET"])
@finance_permission_required("finance.journals.view")
def journal_detail(journal_id):

    db = finance_db()

    journal = db.execute(
        """
        SELECT *
        FROM finance_journal_entries
        WHERE id = ?
        """,
        (journal_id,)
    ).fetchone()

    if not journal:
        abort(404)

    lines = services.models.get_journal_lines(
        db,
        journal_id
    )

    balance = services.models.get_journal_balance(
        db,
        journal_id
    )

    return render_template(
        "journal_entries.html",
        journal=journal,
        journal_lines=lines,
        balance=balance
    )


# =========================================================
# JOURNAL APPROVAL
# =========================================================

@finance_bp.route(
    "/journals/<int:journal_id>/submit",
    methods=["POST"]
)
@finance_permission_required("finance.journals.create")
def submit_journal(journal_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        services.submit_journal(
            db,
            journal_id,
            user_id
        )

        flash("Journal submitted for approval.", "success")

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.journals")
    )


@finance_bp.route(
    "/journals/<int:journal_id>/approve",
    methods=["POST"]
)
@finance_permission_required("finance.journals.approve")
def approve_journal(journal_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        services.approve_journal(
            db,
            journal_id,
            user_id,
            comment=request.form.get("comment")
        )

        flash("Journal approved successfully.", "success")

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.journals")
    )


@finance_bp.route(
    "/journals/<int:journal_id>/reject",
    methods=["POST"]
)
@finance_permission_required("finance.journals.approve")
def reject_journal(journal_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        services.reject_journal(
            db,
            journal_id,
            user_id,
            request.form.get("reason", "")
        )

        flash("Journal rejected.", "warning")

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.journals")
    )


# =========================================================
# JOURNAL POSTING
# =========================================================

@finance_bp.route(
    "/journals/<int:journal_id>/post",
    methods=["POST"]
)
@finance_permission_required("finance.journals.post")
def post_journal(journal_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        services.post_journal(
            db,
            journal_id,
            user_id
        )

        flash("Journal posted to the general ledger.", "success")

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.journals")
    )


# =========================================================
# JOURNAL REVERSAL
# =========================================================

@finance_bp.route(
    "/journals/<int:journal_id>/reverse",
    methods=["POST"]
)
@finance_permission_required("finance.journals.reverse")
def reverse_journal(journal_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        reversal_id = services.reverse_journal(
            db=db,
            journal_id=journal_id,
            user_id=user_id,
            reversal_entry_number=request.form.get(
                "reversal_entry_number", ""
            ).strip(),
            reversal_date=request.form.get("reversal_date"),
            reason=request.form.get("reason", "")
        )

        flash(
            f"Reversal draft {reversal_id} created. "
            "It must be approved and posted separately.",
            "success"
        )

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.journals")
    )


# =========================================================
# GENERAL LEDGER
# =========================================================

@finance_bp.route("/general-ledger", methods=["GET"])
@finance_permission_required("finance.ledger.view")
def general_ledger():

    db = finance_db()

    accounts = services.get_accounts(db)

    account_id = request.args.get("account_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    ledger_entries = []

    if account_id:
        ledger_entries = services.get_general_ledger(
            db,
            int(account_id),
            start_date,
            end_date
        )

    return render_template(
        "general_ledger.html",
        accounts=accounts,
        ledger_entries=ledger_entries,
        selected_account=account_id,
        start_date=start_date,
        end_date=end_date
    )


# =========================================================
# DEPARTMENT INTEGRATIONS
# =========================================================

@finance_bp.route("/integrations", methods=["GET"])
@finance_permission_required("finance.integration.view")
def integrations():

    db = finance_db()

    records = db.execute(
        """
        SELECT *
        FROM finance_integration_records
        ORDER BY id DESC
        LIMIT 200
        """
    ).fetchall()

    return render_template(
        "dashboard.html",
        integration_records=records,
        integration_page=True
    )


@finance_bp.route(
    "/integrations/<int:record_id>/approve",
    methods=["POST"]
)
@finance_permission_required("finance.integration.approve")
def approve_integration(record_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        services.approve_department_transaction(
            db,
            record_id,
            user_id
        )

        flash("Department transaction approved for mapping.", "success")

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.integrations")
    )


@finance_bp.route(
    "/integrations/<int:record_id>/reject",
    methods=["POST"]
)
@finance_permission_required("finance.integration.approve")
def reject_integration(record_id):

    db = finance_db()
    user_id = get_authenticated_user_id()

    try:
        services.reject_department_transaction(
            db,
            record_id,
            user_id,
            request.form.get("reason", "")
        )

        flash("Department transaction rejected.", "warning")

    except Exception as exc:
        flash(str(exc), "danger")

    return redirect(
        url_for("finance.integrations")
    )


# =========================================================
# FINANCIAL AUDIT LOG
# =========================================================

@finance_bp.route("/audit-logs", methods=["GET"])
@finance_permission_required("finance.audit.view")
def audit_logs():

    db = finance_db()

    logs = services.get_finance_audit_logs(
        db,
        limit=request.args.get("limit", 100)
    )

    return render_template(
        "financial_reports.html",
        audit_logs=logs,
        audit_page=True
    )
