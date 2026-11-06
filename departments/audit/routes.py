
"""
Dereje Wood & Kitchen Cabinet
Audit Management - Flask Routes

Connects Audit Management services to the frontend.

Security:
    - Uses the existing Audit RBAC adapter.
    - Departmental source records are read-only.
    - State-changing operations use POST.
    - Validates identifiers and form input.
    - Does not expose credentials or connection secrets.
"""

from functools import wraps
from datetime import datetime, timezone
from inspect import signature, Parameter
from uuid import uuid4

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    abort,
    current_app,
)

from departments.audit import audit_bp
from departments.audit import services
from departments.audit import permissions

from departments.audit.permissions import (
    AUDIT_PERMISSIONS,
    has_audit_permission,
)


# =========================================================
# CONSTANTS
# =========================================================

ALLOWED_MODULES = {
    "finance",
    "hr",
    "payroll",
    "procurement",
    "inventory",
    "stock",
    "asset",
    "budget",
    "project",
    "marketing",
    "performance",
    "vacancy",
    "exam",
    "letter",
    "user",
    "integration",
    "shared",
}

ALLOWED_ACTION_STATUSES = {
    "Open",
    "In Progress",
    "Completed",
    "Overdue",
    "Rejected",
}

ALLOWED_RISK_LEVELS = {
    "Low",
    "Medium",
    "High",
    "Critical",
}

SENSITIVE_FIELDS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "api_key",
    "private_key",
    "connection_string",
}


# =========================================================
# HELPERS
# =========================================================

def audit_route_permission(permission):
    """Protect an Audit route using the existing RBAC adapter."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_audit_permission(permission):
                abort(403)

            return view(*args, **kwargs)

        return wrapped

    return decorator


def _service(name, *args, default=None, **kwargs):
    """
    Call an existing service function.

    Missing services and service errors are logged. A caller
    must check the returned value before reporting success.
    """

    function = getattr(services, name, None)

    if not callable(function):
        current_app.logger.warning(
            "Audit service is not implemented: %s",
            name,
        )
        return default

    try:
        return function(*args, **kwargs)

    except Exception:
        current_app.logger.exception(
            "Audit service failed: %s",
            name,
        )
        return default


def _service_first(names, *args, default=None, **kwargs):
    """Call the first available function from a list of names."""

    for name in names:
        function = getattr(services, name, None)

        if callable(function):
            return _service(
                name,
                *args,
                default=default,
                **kwargs,
            )

    current_app.logger.warning(
        "None of these Audit services is available: %s",
        ", ".join(names),
    )

    return default


def _call_with_supported_arguments(function, values):
    """
    Pass only arguments accepted by the service function.

    This supports existing service functions whose signatures
    differ slightly from the frontend form.
    """

    try:
        parameters = signature(function).parameters

        if any(
            parameter.kind == Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return function(**values)

        accepted = {
            key: value
            for key, value in values.items()
            if key in parameters
        }

        return function(**accepted)

    except (TypeError, ValueError):
        return function(**values)


def _render(template, **context):
    """Render an Audit Management template."""

    return render_template(
        f"audit/{template}",
        **context,
    )


def _valid_id(value):
    """Return a positive integer ID or None."""

    try:
        identifier = int(value)
        return identifier if identifier > 0 else None

    except (TypeError, ValueError):
        return None


def _not_found_if_invalid(identifier):
    if identifier is None:
        abort(404)


def _current_actor():
    """Retrieve the authenticated user from the existing RBAC adapter."""

    getter = getattr(permissions, "get_current_user", None)

    if callable(getter):
        try:
            return getter()
        except Exception:
            current_app.logger.exception(
                "Unable to retrieve the current Audit user."
            )

    return None


def _actor_value(user, *names):
    """Read an identity field from either a dictionary or user object."""

    if user is None:
        return None

    for name in names:
        if isinstance(user, dict):
            value = user.get(name)
        else:
            value = getattr(user, name, None)

        if value is not None:
            return value

    return None


def _current_actor_id():
    user = _current_actor()

    return _valid_id(
        _actor_value(user, "id", "user_id", "employee_id")
    )


def _clean_form(*fields):
    """Read and trim selected form fields."""

    return {
        field: request.form.get(field, "").strip()
        for field in fields
    }


def _safe_records(records):
    """
    Remove sensitive fields before returning records to the UI.

    This is an additional presentation safeguard. The source
    integration should also restrict sensitive columns.
    """

    if not isinstance(records, list):
        return []

    safe = []

    for record in records:
        if not isinstance(record, dict):
            continue

        safe.append({
            key: value
            for key, value in record.items()
            if str(key).lower() not in SENSITIVE_FIELDS
            and not any(
                sensitive in str(key).lower()
                for sensitive in (
                    "password",
                    "token",
                    "secret",
                    "private_key",
                )
            )
        })

    return safe


def _make_plan_number():
    """Generate a unique audit plan reference."""

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%d%H%M%S"
    )

    return f"AUD-{timestamp}-{uuid4().hex[:6].upper()}"


def _flash_result(result, success_message, failure_message):
    if result:
        flash(success_message, "success")
        return True

    flash(failure_message, "danger")
    return False


# =========================================================
# DASHBOARD
# =========================================================

@audit_bp.route("/", methods=["GET"])
@audit_bp.route("/dashboard", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def dashboard():

    context = _service(
        "get_audit_dashboard_context",
        default={},
    ) or {}

    if not isinstance(context, dict):
        context = {}

    stats = context.get("stats", {})

    if not isinstance(stats, dict):
        stats = {}

    # Preserve the original dashboard's expected statistic names.
    stats.setdefault("reviews", 0)
    stats.setdefault("open_reviews", 0)
    stats.setdefault("findings", 0)
    stats.setdefault("high_risk", 0)
    stats.setdefault("events", 0)
    stats.setdefault("refusals", 0)

    recent_activities = _service_first(
        (
            "get_recent_audit_activities",
            "get_audit_activity_logs",
        ),
        limit=10,
        default=[],
    ) or []

    departments = context.get("groups", [])

    if not departments:
        departments = _service(
            "get_department_audit_overview",
            default=[],
        ) or []

    # Existing dashboard variables are preserved.
    reviews = _service(
        "get_audit_reviews",
        default=[],
    ) or []

    findings = _service(
        "get_audit_findings",
        default=[],
    ) or []

    correspondence = _service(
        "get_audit_correspondence_activity",
        default=[],
    ) or []

    tabs = _service(
        "get_audit_report_tabs",
        default=[],
    ) or []

    attention = _service(
        "get_audit_attention_items",
        default=[],
    ) or []

    return _render(
        "dashboard.html",
        stats=stats,
        statistics=stats,
        groups=departments,
        departments=departments,
        activity=recent_activities,
        recent_activities=recent_activities,
        reviews=reviews,
        findings=findings,
        correspondence=correspondence,
        tabs=tabs,
        attention=attention,
        has_reviews=True,
        has_findings=True,
        integration_warning=context.get(
            "integration_warning",
            False,
        ),
        connection_summary=context.get(
            "connection_summary",
            {},
        ),
    )


# =========================================================
# AUDIT PLANS
# =========================================================

@audit_bp.route("/plans", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_plans():

    plans = _service_first(
        ("get_audit_plans", "get_all_audit_plans"),
        default=[],
    ) or []

    return _render(
        "plans.html",
        plans=plans,
    )


@audit_bp.route("/plans/create", methods=["GET", "POST"])
@audit_route_permission(AUDIT_PERMISSIONS["CREATE"])
def create_audit_plan():

    if request.method == "GET":
        return _render(
            "plan_form.html",
            plan=None,
        )

    actor_id = _current_actor_id()

    if actor_id is None:
        abort(403)

    data = _clean_form(
        "title",
        "department",
        "audit_type",
        "start_date",
        "end_date",
        "objectives",
        "scope",
        "description",
    )

    if not data["title"] or not data["department"]:
        flash(
            "Title and department are required.",
            "danger",
        )

        return _render(
            "plan_form.html",
            plan=data,
        )

    if (
        data["start_date"]
        and data["end_date"]
        and data["end_date"] < data["start_date"]
    ):
        flash(
            "The end date cannot be earlier than the start date.",
            "danger",
        )

        return _render(
            "plan_form.html",
            plan=data,
        )

    plan_number = _make_plan_number()

    function = getattr(services, "create_plan", None)

    if not callable(function):
        function = getattr(
            services,
            "create_audit_plan",
            None,
        )

    if not callable(function):
        flash(
            "The audit plan creation service is not available.",
            "danger",
        )

        return _render(
            "plan_form.html",
            plan=data,
        )

    try:
        result = _call_with_supported_arguments(
            function,
            {
                "data": data,
                "form_data": data,
                "plan_data": data,
                "created_by": actor_id,
                "plan_number": plan_number,
                **data,
            },
        )

    except Exception:
        current_app.logger.exception(
            "Audit plan creation failed."
        )
        result = None

    if result:
        flash(
            "Audit plan created successfully.",
            "success",
        )

        return redirect(
            url_for("audit.audit_plans")
        )

    flash(
        "Unable to create the audit plan. Check the service and database logs.",
        "danger",
    )

    return _render(
        "plan_form.html",
        plan=data,
    )


@audit_bp.route("/plans/<int:plan_id>", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_plan_detail(plan_id):

    plan = _service_first(
        ("get_audit_plan", "get_plan"),
        plan_id,
        default=None,
    )

    if not plan:
        abort(404)

    assignments = _service(
        "get_audit_assignments",
        plan_id,
        default=[],
    ) or []

    return _render(
        "plan_detail.html",
        plan=plan,
        assignments=assignments,
    )


@audit_bp.route(
    "/plans/<int:plan_id>/edit",
    methods=["GET", "POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["MANAGE"])
def edit_audit_plan(plan_id):

    plan = _service_first(
        ("get_audit_plan", "get_plan"),
        plan_id,
        default=None,
    )

    if not plan:
        abort(404)

    if request.method == "GET":
        return _render(
            "plan_form.html",
            plan=plan,
        )

    data = _clean_form(
        "title",
        "department",
        "audit_type",
        "start_date",
        "end_date",
        "objectives",
        "scope",
        "description",
    )

    if not data["title"] or not data["department"]:
        flash(
            "Title and department are required.",
            "danger",
        )

        return _render(
            "plan_form.html",
            plan=data,
        )

    if (
        data["start_date"]
        and data["end_date"]
        and data["end_date"] < data["start_date"]
    ):
        flash(
            "The end date cannot be earlier than the start date.",
            "danger",
        )

        return _render(
            "plan_form.html",
            plan=data,
        )

    result = _service_first(
        ("update_audit_plan", "update_plan"),
        plan_id,
        data,
        default=None,
    )

    if _flash_result(
        result,
        "Audit plan updated successfully.",
        "Unable to update the audit plan.",
    ):
        return redirect(
            url_for(
                "audit.audit_plan_detail",
                plan_id=plan_id,
            )
        )

    return _render(
        "plan_form.html",
        plan=data,
    )


# =========================================================
# AUDITOR ASSIGNMENTS
# =========================================================

@audit_bp.route("/assignments", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_assignments():

    plan_id = _valid_id(
        request.args.get("plan_id")
    )

    if plan_id is None:
        assignments = _service_first(
            (
                "get_all_audit_assignments",
                "get_audit_assignment_overview",
            ),
            default=[],
        ) or []
    else:
        assignments = _service(
            "get_audit_assignments",
            plan_id,
            default=[],
        ) or []

    return _render(
        "assignments.html",
        assignments=assignments,
        plan_id=plan_id,
    )


@audit_bp.route(
    "/plans/<int:plan_id>/assign",
    methods=["POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["ASSIGN"])
def assign_auditor(plan_id):

    actor_id = _current_actor_id()
    auditor_id = _valid_id(
        request.form.get("user_id")
    )

    if actor_id is None:
        abort(403)

    if auditor_id is None:
        flash(
            "Please select a valid auditor.",
            "danger",
        )

        return redirect(
            url_for(
                "audit.audit_plan_detail",
                plan_id=plan_id,
            )
        )

    function = getattr(
        services,
        "assign_auditor",
        None,
    )

    if not callable(function):
        flash(
            "Auditor assignment service is unavailable.",
            "danger",
        )

        return redirect(
            url_for(
                "audit.audit_plan_detail",
                plan_id=plan_id,
            )
        )

    try:
        result = _call_with_supported_arguments(
            function,
            {
                "audit_plan_id": plan_id,
                "plan_id": plan_id,
                "auditor_id": auditor_id,
                "user_id": auditor_id,
                "assigned_by": actor_id,
            },
        )

    except Exception:
        current_app.logger.exception(
            "Auditor assignment failed."
        )
        result = None

    _flash_result(
        result,
        "Auditor assigned successfully.",
        "Unable to assign auditor.",
    )

    return redirect(
        url_for(
            "audit.audit_plan_detail",
            plan_id=plan_id,
        )
    )


# =========================================================
# CHECKLISTS
# =========================================================

@audit_bp.route("/checklists", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def checklist_templates():

    templates = _service(
        "get_checklist_templates",
        default=[],
    ) or []

    return _render(
        "checklist_templates.html",
        templates=templates,
    )


@audit_bp.route(
    "/checklists/<int:checklist_id>",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def checklist_detail(checklist_id):

    checklist = _service(
        "get_checklist_template",
        checklist_id,
        default=None,
    )

    if not checklist:
        abort(404)

    return _render(
        "checklist_detail.html",
        checklist=checklist,
    )


@audit_bp.route(
    "/checklists/<int:checklist_id>/results",
    methods=["POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["MANAGE"])
def save_checklist_results(checklist_id):

    results = request.form.getlist("results")

    if not results:
        flash(
            "No checklist results were submitted.",
            "warning",
        )

    else:
        result = _service(
            "save_checklist_results",
            checklist_id,
            results,
            default=None,
        )

        _flash_result(
            result,
            "Checklist results saved.",
            "Unable to save checklist results.",
        )

    return redirect(
        url_for(
            "audit.checklist_detail",
            checklist_id=checklist_id,
        )
    )


# =========================================================
# RISK ASSESSMENTS
# =========================================================

@audit_bp.route("/risks", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def risk_assessments():

    risks = _service(
        "get_audit_risk_assessments",
        default=[],
    ) or []

    return _render(
        "risk_assessments.html",
        risks=risks,
    )


@audit_bp.route("/risks/create", methods=["POST"])
@audit_route_permission(AUDIT_PERMISSIONS["CREATE"])
def create_risk_assessment():

    data = _clean_form(
        "department",
        "risk_title",
        "description",
        "likelihood",
        "impact",
    )

    if not data["department"] or not data["risk_title"]:
        flash(
            "Department and risk title are required.",
            "danger",
        )

    elif not data["likelihood"] or not data["impact"]:
        flash(
            "Likelihood and impact are required.",
            "danger",
        )

    else:
        result = _service(
            "create_audit_risk_assessment",
            data,
            default=None,
        )

        _flash_result(
            result,
            "Risk assessment created.",
            "Unable to create risk assessment.",
        )

    return redirect(
        url_for("audit.risk_assessments")
    )


# =========================================================
# AUDIT FINDINGS
# =========================================================

@audit_bp.route("/findings", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_findings():

    findings = _service(
        "get_audit_findings",
        default=[],
    ) or []

    return _render(
        "findings.html",
        findings=findings,
    )


@audit_bp.route(
    "/findings/<int:finding_id>",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def finding_detail(finding_id):

    finding = _service(
        "get_audit_finding",
        finding_id,
        default=None,
    )

    if not finding:
        abort(404)

    return _render(
        "finding_detail.html",
        finding=finding,
    )


@audit_bp.route(
    "/findings/create",
    methods=["POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["CREATE"])
def create_audit_finding():

    data = _clean_form(
        "title",
        "department",
        "severity",
        "description",
        "recommendation",
    )

    if not data["title"] or not data["department"]:
        flash(
            "Finding title and department are required.",
            "danger",
        )

    elif (
        data["severity"]
        and data["severity"] not in ALLOWED_RISK_LEVELS
    ):
        flash(
            "Invalid finding severity.",
            "danger",
        )

    else:
        result = _service(
            "create_audit_finding",
            data,
            default=None,
        )

        _flash_result(
            result,
            "Audit finding recorded.",
            "Unable to record audit finding.",
        )

    return redirect(
        url_for("audit.audit_findings")
    )


# =========================================================
# CORRECTIVE ACTIONS
# =========================================================

@audit_bp.route(
    "/corrective-actions",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def corrective_actions():

    actions = _service(
        "get_corrective_actions",
        default=[],
    ) or []

    return _render(
        "corrective_actions.html",
        actions=actions,
    )


@audit_bp.route(
    "/corrective-actions/<int:action_id>/update",
    methods=["POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["MANAGE"])
def update_corrective_action(action_id):

    status = request.form.get(
        "status",
        "",
    ).strip()

    if status not in ALLOWED_ACTION_STATUSES:
        abort(400)

    result = _service(
        "update_corrective_action_status",
        action_id,
        status,
        default=None,
    )

    _flash_result(
        result,
        "Corrective action updated.",
        "Unable to update corrective action.",
    )

    return redirect(
        url_for("audit.corrective_actions")
    )


# =========================================================
# COMPLIANCE
# =========================================================

@audit_bp.route("/compliance", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def compliance_records():

    records = _service(
        "get_compliance_records",
        default=[],
    ) or []

    return _render(
        "compliance.html",
        records=records,
    )


# =========================================================
# EVIDENCE
# =========================================================

@audit_bp.route("/evidence", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_evidence():

    evidence = _service(
        "get_audit_evidence",
        default=[],
    ) or []

    return _render(
        "evidence.html",
        evidence=evidence,
    )


@audit_bp.route(
    "/evidence/upload",
    methods=["POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["MANAGE"])
def upload_audit_evidence():

    uploaded_file = request.files.get(
        "evidence_file"
    )

    if not uploaded_file or not uploaded_file.filename:
        flash(
            "Please select an evidence file.",
            "danger",
        )

        return redirect(
            url_for("audit.audit_evidence")
        )

    result = _service(
        "save_audit_evidence",
        uploaded_file,
        request.form.get(
            "description",
            "",
        ).strip(),
        default=None,
    )

    _flash_result(
        result,
        "Evidence uploaded successfully.",
        "Evidence upload failed.",
    )

    return redirect(
        url_for("audit.audit_evidence")
    )


# =========================================================
# AUDIT REPORTS
# =========================================================

@audit_bp.route("/reports", methods=["GET"])
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_reports():

    reports = _service(
        "get_audit_reports",
        default=[],
    ) or []

    return _render(
        "reports.html",
        reports=reports,
    )


@audit_bp.route(
    "/reports/<int:report_id>",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_report_detail(report_id):

    report = _service(
        "get_audit_report",
        report_id,
        default=None,
    )

    if not report:
        abort(404)

    return _render(
        "report_detail.html",
        report=report,
    )


@audit_bp.route(
    "/reports/<int:report_id>/approve",
    methods=["POST"],
)
@audit_route_permission(AUDIT_PERMISSIONS["APPROVE"])
def approve_audit_report(report_id):

    actor_id = _current_actor_id()

    if actor_id is None:
        abort(403)

    function = getattr(
        services,
        "approve_audit_report",
        None,
    )

    if not callable(function):
        flash(
            "Report approval service is unavailable.",
            "danger",
        )

    else:
        try:
            result = _call_with_supported_arguments(
                function,
                {
                    "report_id": report_id,
                    "approved_by": actor_id,
                    "user_id": actor_id,
                },
            )

        except Exception:
            current_app.logger.exception(
                "Audit report approval failed."
            )
            result = None

        _flash_result(
            result,
            "Audit report approved.",
            "Unable to approve audit report.",
        )

    return redirect(
        url_for(
            "audit.audit_report_detail",
            report_id=report_id,
        )
    )


# =========================================================
# DEPARTMENT INTEGRATIONS
# =========================================================

@audit_bp.route(
    "/departments",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def department_overview():

    departments = _service_first(
        (
            "get_all_audit_department_overviews",
            "get_department_audit_overview",
        ),
        default=[],
    ) or []

    return _render(
        "department_overview.html",
        departments=departments,
    )


@audit_bp.route(
    "/integrations",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def integration_status():

    statuses = _service_first(
        (
            "get_all_audit_integration_status",
            "get_all_department_connection_statuses",
        ),
        default=[],
    ) or []

    return _render(
        "integration_status.html",
        statuses=statuses,
    )


@audit_bp.route(
    "/departments/<module_name>/records",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def department_records(module_name):

    if module_name not in ALLOWED_MODULES:
        abort(404)

    table_name = request.args.get(
        "table",
        "",
    ).strip()

    if not table_name:
        abort(400)

    limit = request.args.get(
        "limit",
        50,
        type=int,
    )

    offset = request.args.get(
        "offset",
        0,
        type=int,
    )

    limit = min(max(limit or 50, 1), 100)
    offset = max(offset or 0, 0)

    records = _service(
        "get_department_audit_records",
        module_name,
        table_name,
        limit=limit,
        offset=offset,
        default=None,
    )

    if records is None:
        abort(404)

    return jsonify({
        "success": True,
        "module": module_name,
        "table": table_name,
        "records": _safe_records(records),
        "limit": limit,
        "offset": offset,
    })


# =========================================================
# AUDIT ACTIVITY LOGS
# =========================================================

@audit_bp.route(
    "/logs",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["VIEW"])
def audit_logs():

    logs = _service_first(
        (
            "get_audit_activity_logs",
            "get_recent_audit_activities",
        ),
        default=[],
    ) or []

    return _render(
        "audit_logs.html",
        logs=logs,
    )


# =========================================================
# EXPORT
# =========================================================

@audit_bp.route(
    "/reports/export",
    methods=["GET"],
)
@audit_route_permission(AUDIT_PERMISSIONS["EXPORT"])
def export_audit_reports():

    result = _service(
        "export_audit_reports",
        default=None,
    )

    if result is None:
        flash(
            "Report export is not available.",
            "warning",
        )

        return redirect(
            url_for("audit.audit_reports")
        )

    return result
