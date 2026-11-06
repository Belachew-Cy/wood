# departments/payroll/routes.py

from datetime import date, datetime
from io import BytesIO

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort,
    current_app,
    send_file,
)

from . import payroll_bp
from . import services
from .permissions import (
    require_payroll_permission,
    has_payroll_permission,
)
from services.audit import log_activity


# ---------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------

def _current_user_id():
    user_id = session.get("user_id")
    if not user_id:
        abort(401)
    return user_id


def _handle_error(error, default_message="Unable to complete the request."):
    current_app.logger.exception("Payroll error: %s", error)
    if isinstance(error, ValueError):
        flash(str(error), "warning")
    else:
        flash(default_message, "danger")


def _form_data():
    return request.form.to_dict()


# ---------------------------------------------------------
# Payroll dashboard
# ---------------------------------------------------------

@payroll_bp.route("/", methods=["GET"])
@payroll_bp.route("/dashboard", methods=["GET"])
@require_payroll_permission("payroll_dashboard")
def payroll_dashboard():
    try:
        month = request.args.get("month", date.today().strftime("%Y-%m"))
        dashboard_data = services.get_payroll_dashboard_data(month)
        # The app templates folder is searched before the blueprint folder, so a
        # bare "dashboard.html" would load the company dashboard instead.
        return render_template(
            "payroll/dashboard.html",
            month=month,
            **dashboard_data
        )
    except Exception as error:
        _handle_error(error, "Unable to load the Payroll Dashboard.")
        return render_template(
            "payroll/dashboard.html",
            month=date.today().strftime("%Y-%m"),
            gross=0, deductions=0, net=0, overtime=0,
            records=[], paid=0, pending=0, months=[]
        )


# ---------------------------------------------------------
# Employee payroll profiles
# ---------------------------------------------------------

@payroll_bp.route("/employees", methods=["GET", "POST"])
@require_payroll_permission("payroll_employees")
def employee_profiles():

    if request.method == "POST":
        try:
            services.create_employee_profile(
                data=_form_data(),
                created_by=_current_user_id()
            )
            flash("Employee payroll profile created successfully.", "success")
        except Exception as error:
            _handle_error(error, "Unable to create the employee payroll profile.")
        return redirect(url_for("payroll.employee_profiles"))

    try:
        employees = services.get_employee_profiles(
            search=request.args.get("search", ""),
            department=request.args.get("department", ""),
            status=request.args.get("status", "")
        )
        return render_template(
            "employee_profiles.html",
            employees=employees,
            available_employees=services.get_employees_without_payroll_profile(),
            grades=services.get_salary_grades(),
            structures=services.get_salary_structures(),
            search=request.args.get("search", ""),
            selected_department=request.args.get("department", ""),
            selected_status=request.args.get("status", "")
        )
    except Exception as error:
        _handle_error(error, "Unable to load employee payroll profiles.")
        return render_template(
            "employee_profiles.html",
            employees=[], available_employees=[],
            grades=[], structures=[],
            search="", selected_department="", selected_status=""
        )


@payroll_bp.route(
    "/employees/<int:employee_id>/edit",
    methods=["GET", "POST"]
)
@require_payroll_permission("payroll_employee_edit")
def edit_employee_profile(employee_id):

    if request.method == "POST":
        try:
            services.update_employee_profile(
                employee_id=employee_id,
                data=_form_data(),
                updated_by=_current_user_id()
            )
            flash("Employee payroll profile updated successfully.", "success")
            return redirect(url_for("payroll.employee_profiles"))
        except Exception as error:
            _handle_error(error, "Unable to update the employee profile.")

    try:
        employee = services.get_employee_payroll_profile(employee_id)
        if not employee:
            abort(404)
        return render_template(
            "employee_profiles.html",
            employee=employee,
            edit_mode=True
        )
    except Exception as error:
        if getattr(error, "code", None) == 404:
            raise
        _handle_error(error, "Unable to retrieve the employee profile.")
        return redirect(url_for("payroll.employee_profiles"))


# ---------------------------------------------------------
# Employee salary details
# ---------------------------------------------------------

@payroll_bp.route("/employees/<int:employee_id>/salary", methods=["GET"])
@require_payroll_permission("payroll_salary_view")
def employee_salary(employee_id):

    try:
        employee = services.get_employee_payroll_profile(employee_id)
        if not employee:
            abort(404)

        salary_details = services.get_employee_salary_details(employee_id)

        return render_template(
            "employee_salary.html",
            employee=employee,
            salary_details=salary_details
        )
    except Exception as error:
        if getattr(error, "code", None) == 404:
            raise
        _handle_error(error, "Unable to load employee salary details.")
        return redirect(url_for("payroll.employee_profiles"))


# ---------------------------------------------------------
# Salary grades
# ---------------------------------------------------------

@payroll_bp.route("/salary-grades", methods=["GET", "POST"])
@require_payroll_permission("payroll_grade_view")
def salary_grades():

    if request.method == "POST":
        try:
            services.create_salary_grade(
                data=_form_data(),
                created_by=_current_user_id()
            )
            flash("Salary grade created successfully.", "success")
        except Exception as error:
            _handle_error(error, "Unable to create the salary grade.")
        return redirect(url_for("payroll.salary_grades"))

    try:
        grades = services.get_salary_grades()
        return render_template("salary_grades.html", grades=grades)
    except Exception as error:
        _handle_error(error, "Unable to load salary grades.")
        return render_template("salary_grades.html", grades=[])


@payroll_bp.route("/salary-grades/<int:grade_id>/edit", methods=["POST"])
@require_payroll_permission("payroll_grade_edit")
def update_salary_grade(grade_id):
    try:
        services.update_salary_grade(
            grade_id=grade_id,
            data=request.form.to_dict(),
            updated_by=_current_user_id()
        )
        flash("Salary grade updated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to update salary grade.")
    return redirect(url_for("payroll.salary_grades"))


@payroll_bp.route("/salary-grades/<int:grade_id>/deactivate", methods=["POST"])
@require_payroll_permission("payroll_grade_delete")
def delete_salary_grade(grade_id):
    try:
        services.deactivate_salary_grade(
            grade_id=grade_id,
            updated_by=_current_user_id()
        )
        flash("Salary grade deactivated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to deactivate salary grade.")
    return redirect(url_for("payroll.salary_grades"))


# ---------------------------------------------------------
# Salary structures
# ---------------------------------------------------------

@payroll_bp.route("/salary-structures", methods=["GET", "POST"])
@require_payroll_permission("payroll_salary_view")
def salary_structures():

    if request.method == "POST":
        try:
            services.create_salary_structure(
                data=_form_data(),
                created_by=_current_user_id()
            )
            flash("Salary structure created successfully.", "success")
        except Exception as error:
            _handle_error(error, "Unable to create the salary structure.")
        return redirect(url_for("payroll.salary_structures"))

    try:
        structures = services.get_salary_structures()
        grades = services.get_salary_grades()
        return render_template(
            "salary_structures.html",
            structures=structures,
            grades=grades
        )
    except Exception as error:
        _handle_error(error, "Unable to load salary structures.")
        return render_template(
            "salary_structures.html", structures=[], grades=[]
        )


@payroll_bp.route(
    "/salary-structures/<int:structure_id>/edit",
    methods=["POST"]
)
@require_payroll_permission("payroll_salary_edit")
def update_salary_structure(structure_id):
    try:
        services.update_salary_structure(
            structure_id=structure_id,
            data=request.form.to_dict(),
            updated_by=_current_user_id()
        )
        flash("Salary structure updated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to update salary structure.")
    return redirect(url_for("payroll.salary_structures"))


@payroll_bp.route(
    "/salary-structures/<int:structure_id>/toggle",
    methods=["POST"]
)
@require_payroll_permission("payroll_salary_deactivate")
def toggle_salary_structure(structure_id):
    try:
        services.toggle_salary_structure(
            structure_id=structure_id,
            updated_by=_current_user_id()
        )
        flash("Salary structure status updated.", "success")
    except Exception as error:
        _handle_error(error, "Unable to change salary structure status.")
    return redirect(url_for("payroll.salary_structures"))


# ---------------------------------------------------------
# Allowances
# ---------------------------------------------------------

@payroll_bp.route("/allowances", methods=["GET", "POST"])
@require_payroll_permission("payroll_allowance_view")
def allowances():

    if request.method == "POST":
        try:
            services.create_allowance_type(
                data=_form_data(),
                created_by=_current_user_id()
            )
            flash("Allowance created successfully.", "success")
        except Exception as error:
            _handle_error(error, "Unable to create the allowance.")
        return redirect(url_for("payroll.allowances"))

    try:
        allowance_types = services.get_allowance_types()
        return render_template(
            "allowances.html", allowances=allowance_types
        )
    except Exception as error:
        _handle_error(error, "Unable to load allowances.")
        return render_template("allowances.html", allowances=[])


@payroll_bp.route("/allowances/<int:allowance_id>/edit", methods=["POST"])
@require_payroll_permission("payroll_allowance_edit")
def update_allowance(allowance_id):
    try:
        services.update_allowance_type(
            allowance_id=allowance_id,
            data=request.form.to_dict(),
            updated_by=_current_user_id()
        )
        flash("Allowance updated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to update allowance.")
    return redirect(url_for("payroll.allowances"))


@payroll_bp.route("/allowances/<int:allowance_id>/toggle", methods=["POST"])
@require_payroll_permission("payroll_allowance_delete")
def toggle_allowance(allowance_id):
    try:
        services.toggle_allowance_type(
            allowance_id=allowance_id,
            updated_by=_current_user_id()
        )
        flash("Allowance status updated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to change allowance status.")
    return redirect(url_for("payroll.allowances"))


# ---------------------------------------------------------
# Deductions
# ---------------------------------------------------------

@payroll_bp.route("/deductions", methods=["GET", "POST"])
@require_payroll_permission("payroll_deduction_view")
def deductions():

    if request.method == "POST":
        try:
            services.create_deduction_type(
                data=_form_data(),
                created_by=_current_user_id()
            )
            flash("Deduction created successfully.", "success")
        except Exception as error:
            _handle_error(error, "Unable to create the deduction.")
        return redirect(url_for("payroll.deductions"))

    try:
        deduction_types = services.get_deduction_types()
        return render_template(
            "deductions.html", deductions=deduction_types
        )
    except Exception as error:
        _handle_error(error, "Unable to load deductions.")
        return render_template("deductions.html", deductions=[])


@payroll_bp.route("/deductions/<int:deduction_id>/edit", methods=["POST"])
@require_payroll_permission("payroll_deduction_edit")
def update_deduction(deduction_id):
    try:
        services.update_deduction_type(
            deduction_id=deduction_id,
            data=request.form.to_dict(),
            updated_by=_current_user_id()
        )
        flash("Deduction updated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to update deduction.")
    return redirect(url_for("payroll.deductions"))


@payroll_bp.route("/deductions/<int:deduction_id>/toggle", methods=["POST"])
@require_payroll_permission("payroll_deduction_delete")
def toggle_deduction(deduction_id):
    try:
        services.toggle_deduction_type(
            deduction_id=deduction_id,
            updated_by=_current_user_id()
        )
        flash("Deduction status updated successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to change deduction status.")
    return redirect(url_for("payroll.deductions"))


# ---------------------------------------------------------
# Salary history (single definition)
# ---------------------------------------------------------

@payroll_bp.route("/salary-history", methods=["GET"])
@require_payroll_permission("payroll_history_view")
def salary_history():

    employee_id = request.args.get("employee_id", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    try:
        history = services.get_salary_history(
            employee_id=employee_id or None,
            start_date=start_date or None,
            end_date=end_date or None
        )
        employees = services.get_payroll_employees()

        return render_template(
            "salary_history.html",
            history=history,
            employees=employees,
            selected_employee_id=employee_id,
            start_date=start_date,
            end_date=end_date
        )
    except Exception:
        current_app.logger.exception("Unable to load payroll salary history")
        flash("Unable to load salary history.", "danger")
        return render_template(
            "salary_history.html",
            history=[], employees=[],
            selected_employee_id=employee_id,
            start_date=start_date,
            end_date=end_date
        )


# ---------------------------------------------------------
# Employee's own payslip
# ---------------------------------------------------------

@payroll_bp.route("/my-salary", methods=["GET"])
@require_payroll_permission("payroll_own_payslip")
def my_salary():

    try:
        user_id = _current_user_id()
        payslips = services.get_employee_own_salary_records(user_id)
        return render_template(
            "employee_salary.html",
            payslips=payslips,
            own_records=True
        )
    except Exception as error:
        _handle_error(error, "Unable to load your salary records.")
        return render_template(
            "employee_salary.html",
            payslips=[],
            own_records=True
        )


@payroll_bp.route("/my-payslips")
@require_payroll_permission("payroll_own_payslip")
def my_payslips():

    payslips = services.get_employee_payslips(_current_user_id())
    return render_template(
        "employee_salary.html",
        own_records=True,
        payslips=payslips
    )


@payroll_bp.route("/payslip/<int:payroll_id>")
@require_payroll_permission("payroll_own_payslip")
def employee_payslip(payroll_id):

    payslip = services.get_employee_payslip(
        payroll_id=payroll_id,
        user_id=_current_user_id()
    )
    if not payslip:
        abort(404)

    return render_template("payslip.html", payslip=payslip)


# ---------------------------------------------------------
# Monthly payroll processing
# ---------------------------------------------------------

@payroll_bp.route("/processing", methods=["GET"])
@require_payroll_permission("payroll_process")
def payroll_processing():

    selected_month = request.args.get(
        "month", datetime.now().strftime("%Y-%m")
    )

    try:
        datetime.strptime(selected_month, "%Y-%m")
        payroll_data = services.get_monthly_payroll_data(selected_month)

        return render_template(
            "payroll_processing.html",
            selected_month=selected_month,
            records=payroll_data["records"],
            total_basic=payroll_data["total_basic"],
            total_allowances=payroll_data["total_allowances"],
            total_overtime=payroll_data.get("total_overtime", 0),
            total_deductions=payroll_data["total_deductions"],
            total_net=payroll_data["total_net"],
            paid_count=payroll_data["paid_count"],
            pending_count=payroll_data["pending_count"],
            has_payroll_permission=has_payroll_permission
        )
    except ValueError as error:
        flash(str(error), "warning")
        return redirect(url_for("payroll.payroll_processing"))
    except Exception:
        current_app.logger.exception(
            "Unable to load monthly payroll processing."
        )
        flash("Unable to load payroll records.", "danger")
        return redirect(url_for("payroll.payroll_dashboard"))


@payroll_bp.route("/generate", methods=["POST"])
@require_payroll_permission("payroll_process")
def generate_payroll():

    payroll_month = request.form.get("payroll_month", "").strip()
    created_by = session.get("user_id")

    if not created_by:
        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for("payroll.payroll_processing"))

    try:
        result = services.generate_monthly_payroll(
            payroll_month=payroll_month,
            created_by=created_by
        )
        flash(
            f"Payroll generated successfully. "
            f"Created: {result['created']}, "
            f"Skipped: {result['skipped']}, "
            f"Overtime requests imported: {result['overtime_imported']}.",
            "success"
        )
    except ValueError as error:
        flash(str(error), "warning")
    except Exception:
        current_app.logger.exception("Monthly payroll generation failed.")
        flash("Unable to generate monthly payroll.", "danger")

    return redirect(
        url_for("payroll.payroll_processing", month=payroll_month)
    )


@payroll_bp.route("/submit/<payroll_month>", methods=["POST"])
@require_payroll_permission("payroll_submit")
def submit_payroll(payroll_month):
    try:
        services.submit_monthly_payroll(
            payroll_month=payroll_month,
            submitted_by=_current_user_id()
        )
        log_activity(
            "SUBMIT", "payroll",
            "Payroll {} submitted for approval.".format(payroll_month),
            record_type="payroll", record_id=payroll_month)
        flash("Payroll submitted for approval.", "success")
    except Exception as error:
        _handle_error(error, "Unable to submit payroll.")
    return redirect(
        url_for("payroll.payroll_processing", month=payroll_month)
    )


@payroll_bp.route("/approve/<payroll_month>", methods=["POST"])
@require_payroll_permission("payroll_approve")
def approve_payroll(payroll_month):
    try:
        services.approve_monthly_payroll(
            payroll_month=payroll_month,
            approved_by=_current_user_id(),
            comments=request.form.get("comments", "").strip()
        )
        log_activity(
            "APPROVE", "payroll",
            "Payroll {} approved. {}".format(
                payroll_month, request.form.get("comments", "").strip()),
            record_type="payroll", record_id=payroll_month)
        flash("Payroll approved successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to approve payroll.")
    return redirect(
        url_for("payroll.payroll_processing", month=payroll_month)
    )


@payroll_bp.route("/reject/<payroll_month>", methods=["POST"])
@require_payroll_permission("payroll_reject")
def reject_payroll(payroll_month):
    try:
        services.reject_monthly_payroll(
            payroll_month=payroll_month,
            rejected_by=_current_user_id(),
            reason=request.form.get("reason", "")
        )
        log_activity(
            "REJECT", "payroll",
            "Payroll {} returned: {}".format(
                payroll_month, request.form.get("reason", "").strip()),
            record_type="payroll", record_id=payroll_month)
        flash("Payroll rejected.", "warning")
    except Exception as error:
        _handle_error(error, "Unable to reject payroll.")
    return redirect(
        url_for("payroll.payroll_processing", month=payroll_month)
    )


@payroll_bp.route("/payments/<int:payroll_id>", methods=["POST"])
@require_payroll_permission("payroll_payment")
def record_payment(payroll_id):
    try:
        services.record_payroll_payment(
            payroll_id=payroll_id,
            payment_method=request.form.get("payment_method"),
            payment_reference=request.form.get("payment_reference"),
            payment_date=request.form.get("payment_date"),
            paid_by=_current_user_id()
        )
        log_activity(
            "PAY", "payroll",
            "Payroll #{} paid by {} ({}).".format(
                payroll_id, request.form.get("payment_method"),
                request.form.get("payment_reference")),
            record_type="payroll", record_id=payroll_id)
        flash("Payroll payment recorded successfully.", "success")
    except Exception as error:
        _handle_error(error, "Unable to record payroll payment.")
    return redirect(
        url_for(
            "payroll.payroll_processing",
            month=request.form.get("payroll_month", "")
        )
    )


# ---------------------------------------------------------
# Reports
# ---------------------------------------------------------

@payroll_bp.route("/reports", methods=["GET"])
@require_payroll_permission("payroll_reports")
def payroll_reports():

    selected_month = request.args.get(
        "month", datetime.now().strftime("%Y-%m")
    )

    try:
        datetime.strptime(selected_month, "%Y-%m")
    except ValueError:
        abort(400, description="Invalid payroll month.")

    report = services.get_payroll_monthly_report(selected_month)
    payment_records = services.get_payroll_payment_report(selected_month)
    deduction_records = services.get_payroll_deduction_report(selected_month)

    return render_template(
        "payroll_reports.html",
        selected_month=selected_month,
        summary=report["summary"],
        departments=report["departments"],
        payment_records=payment_records,
        deduction_records=deduction_records
    )


@payroll_bp.route("/reports/export")
@require_payroll_permission("payroll_reports")
def export_payroll_report():

    payroll_month = request.args.get("month", "").strip()

    try:
        datetime.strptime(payroll_month, "%Y-%m")
    except ValueError:
        abort(400, description="Invalid payroll month.")

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    rows = services.get_payroll_export_rows(payroll_month)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Payroll Report"

    headers = [
        "Employee Code", "Employee Name", "Department",
        "Payroll Month", "Basic Salary", "Allowances", "Overtime",
        "Gross Salary", "Deductions", "Net Salary", "Payment Status"
    ]
    sheet.append(headers)

    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="365F45")
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        sheet.append([
            row["employee_code"], row["full_name"],
            row["department"], row["payroll_month"],
            float(row["basic_salary"] or 0),
            float(row["allowances"] or 0),
            float(row["overtime_amount"] or 0),
            float(row["gross_salary"] or 0),
            float(row["deductions"] or 0),
            float(row["net_salary"] or 0),
            row["payment_status"]
        ])

    for column in sheet.columns:
        max_length = max(
            len(str(cell.value or "")) for cell in column
        )
        sheet.column_dimensions[column[0].column_letter].width = (
            min(max_length + 3, 35)
        )

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"payroll_report_{payroll_month}.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )


# ---------------------------------------------------------
# Statutory rules
# ---------------------------------------------------------

@payroll_bp.route("/statutory-rules", methods=["GET", "POST"])
@require_payroll_permission("payroll_statutory_view")
def statutory_rules():

    if request.method == "POST":
        if not has_payroll_permission("payroll_statutory_create"):
            abort(403)

        try:
            services.create_statutory_rule(
                data=request.form.to_dict(),
                created_by=_current_user_id()
            )
            flash("Statutory rule created successfully.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        except Exception:
            current_app.logger.exception("Failed to create statutory rule.")
            flash("Unable to create statutory rule.", "danger")

        return redirect(url_for("payroll.statutory_rules"))

    return render_template(
        "statutory_rules.html",
        rules=services.get_statutory_rules(),
        tax_brackets=services.get_tax_brackets(),
    )


@payroll_bp.route("/statutory-rules/<int:rule_id>/toggle", methods=["POST"])
@require_payroll_permission("payroll_statutory_create")
def toggle_statutory_rule(rule_id):

    try:
        status = services.toggle_statutory_rule(rule_id, _current_user_id())
        flash(
            f"Statutory rule {rule_id} is now {status.lower()}.",
            "success",
        )
    except ValueError as error:
        flash(str(error), "warning")
    except Exception:
        current_app.logger.exception("Failed to toggle statutory rule.")
        flash("Unable to change the statutory rule.", "danger")

    return redirect(url_for("payroll.statutory_rules"))
# ---------------------------------------------------------
# HR overtime import
# ---------------------------------------------------------

@payroll_bp.route("/overtime/import", methods=["POST"])
@require_payroll_permission("hr_overtime_payroll")
def import_overtime_to_payroll():
    """Copy HR-approved overtime into the month's Draft payroll once."""

    payroll_month = request.form.get("payroll_month", "").strip()

    try:
        result = services.import_verified_overtime(
            payroll_month=payroll_month,
            imported_by=_current_user_id(),
        )
        if result["imported_count"]:
            flash(
                f"{result['imported_count']} overtime requests imported "
                f"({result['imported_amount']} ETB). Check the payslips "
                "before submitting the month.",
                "success",
            )
        else:
            flash("No approved overtime was waiting for that month.", "info")
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        current_app.logger.exception("Overtime payroll import failed.")
        flash("Overtime import failed. Please check the payroll records.",
              "danger")

    return redirect(
        url_for("payroll.payroll_processing", month=payroll_month))
