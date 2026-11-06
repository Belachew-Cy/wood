# ============================================================================
# IMPORTS
# ============================================================================

from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
import csv
import io
import os

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    make_response,
    send_file,
    current_app,
)
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError

from extensions import db
from templates.Asset_mag import asset_bp

from .models import (
    Asset,
    AssetCategory,
    AssetAssignment,
    AssetTransfer,
    AssetMaintenance,
    AssetAuditLog,
    AssetDepreciation,
    AssetDisposal,
    AssetVerificationCampaign,
    AssetVerificationRecord,
    AssetInsurance,
    AssetWarranty,
    AssetInsuranceClaim,
    AssetDocument,
    AssetDocumentVersion,
    DepartmentAssetRecord,
)

# AssetApproval is defined in approval_models.py (re-exported in models.py).
from .approval_models import AssetApproval
from .permissions import asset_permission_required

from .depreciation import (
    calculate_straight_line,
    calculate_declining_balance,
    money,
)
from .barcode_service import generate_asset_qr, generate_asset_barcode
from .document_service import save_asset_document


# ============================================================================
# HELPERS
# ============================================================================

def record_asset_activity(asset_id, action, description):
    """Record asset-related activities in the audit log."""
    log = AssetAuditLog(
        asset_id=asset_id,
        user_id=current_user.id,
        action=action,
        description=description,
    )
    db.session.add(log)


def _parse_date(value):
    """Parse an ISO date string, returning None if empty."""
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _parse_non_negative_decimal(value, default="0"):
    """Parse a Decimal and validate it is finite and >= 0."""
    amount = Decimal(value or default)
    if not amount.is_finite() or amount < 0:
        raise InvalidOperation
    return amount


def _safe_csv(value):
    """Escape CSV values that could be interpreted as formulas."""
    value = "" if value is None else str(value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


# ============================================================================
# DASHBOARD
# ============================================================================

@asset_bp.route("/")
def dashboard():
    total_assets = Asset.query.count()
    available_assets = Asset.query.filter_by(status="Available").count()
    assigned_assets = Asset.query.filter_by(status="Assigned").count()
    maintenance_assets = Asset.query.filter_by(status="Maintenance").count()
    total_categories = AssetCategory.query.filter_by(status="Active").count()
    recent_assets = (
        Asset.query.order_by(Asset.created_at.desc()).limit(5).all()
    )

    return render_template(
        "asset_management/dashboard.html",
        total_assets=total_assets,
        available_assets=available_assets,
        assigned_assets=assigned_assets,
        maintenance_assets=maintenance_assets,
        total_categories=total_categories,
        recent_assets=recent_assets,
    )


# ============================================================================
# ASSETS: LIST / REGISTER / VIEW / EDIT / DELETE
# ============================================================================

@asset_bp.route("/list")
def asset_list():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    query = Asset.query

    if search:
        query = query.filter(
            or_(
                Asset.asset_code.ilike(f"%{search}%"),
                Asset.name.ilike(f"%{search}%"),
                Asset.serial_number.ilike(f"%{search}%"),
            )
        )

    if status:
        query = query.filter_by(status=status)

    assets = query.order_by(Asset.created_at.desc()).all()

    return render_template(
        "asset_management/asset_list.html",
        assets=assets,
        search=search,
        selected_status=status,
    )


@asset_bp.route("/register", methods=["GET", "POST"])
def register_asset():
    categories = AssetCategory.query.filter_by(status="Active").all()

    if request.method == "POST":
        asset_code = request.form.get("asset_code", "").strip()
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()

        if not asset_code or not name or not category_id:
            flash("Please complete all required fields.", "danger")
            return redirect(url_for("asset_management.register_asset"))

        if Asset.query.filter_by(asset_code=asset_code).first():
            flash("Asset code already exists.", "danger")
            return redirect(url_for("asset_management.register_asset"))

        category = db.session.get(AssetCategory, int(category_id))
        if not category or category.status != "Active":
            flash("Please select a valid active category.", "danger")
            return redirect(url_for("asset_management.register_asset"))

        try:
            cost_value = _parse_non_negative_decimal(
                request.form.get("purchase_cost", "0")
            )
            purchase_date = _parse_date(request.form.get("purchase_date"))
            warranty_expiry = _parse_date(
                request.form.get("warranty_expiry")
            )
        except (ValueError, InvalidOperation):
            flash("Please enter valid dates and purchase cost.", "danger")
            return redirect(url_for("asset_management.register_asset"))

        asset = Asset(
            asset_code=asset_code,
            name=name,
            description=request.form.get("description"),
            category_id=int(category_id),
            serial_number=request.form.get("serial_number"),
            model_number=request.form.get("model_number"),
            manufacturer=request.form.get("manufacturer"),
            purchase_date=purchase_date,
            purchase_cost=cost_value,
            warranty_expiry=warranty_expiry,
            location=request.form.get("location"),
            condition=request.form.get("condition", "Good"),
            status="Available",
        )

        try:
            db.session.add(asset)
            db.session.flush()

            record_asset_activity(
                asset.id,
                "Asset Registration",
                f"Registered asset {asset.asset_code}",
            )
            db.session.commit()

            flash("Asset registered successfully.", "success")
            return redirect(url_for("asset_management.asset_list"))

        except Exception:
            db.session.rollback()
            flash("Unable to register asset. Please try again.", "danger")

    return render_template(
        "asset_management/register_asset.html", categories=categories
    )


@asset_bp.route("/view/<int:asset_id>")
def view_asset(asset_id):
    asset = db.session.get(Asset, asset_id)
    if not asset:
        abort(404)
    return render_template("asset_management/view_asset.html", asset=asset)


@asset_bp.route("/edit/<int:asset_id>", methods=["GET", "POST"])
def edit_asset(asset_id):
    asset = db.session.get(Asset, asset_id)
    if not asset:
        abort(404)

    categories = AssetCategory.query.filter_by(status="Active").all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", "").strip()

        if not name or not category_id:
            flash("Asset name and category are required.", "danger")
            return redirect(
                url_for("asset_management.edit_asset", asset_id=asset.id)
            )

        category = db.session.get(AssetCategory, int(category_id))
        if not category or category.status != "Active":
            flash("Please select a valid active category.", "danger")
            return redirect(
                url_for("asset_management.edit_asset", asset_id=asset.id)
            )

        try:
            cost = _parse_non_negative_decimal(
                request.form.get("purchase_cost", "0")
            )
            purchase_date = _parse_date(request.form.get("purchase_date"))
            warranty_expiry = _parse_date(
                request.form.get("warranty_expiry")
            )

            asset.name = name
            asset.description = request.form.get("description")
            asset.category_id = int(category_id)
            asset.serial_number = request.form.get("serial_number")
            asset.model_number = request.form.get("model_number")
            asset.manufacturer = request.form.get("manufacturer")
            asset.purchase_date = purchase_date
            asset.purchase_cost = cost
            asset.warranty_expiry = warranty_expiry
            asset.location = request.form.get("location")

            record_asset_activity(
                asset.id,
                "Asset Update",
                f"Updated asset {asset.asset_code}",
            )
            db.session.commit()

            flash("Asset updated successfully.", "success")
            return redirect(
                url_for("asset_management.view_asset", asset_id=asset.id)
            )

        except (ValueError, InvalidOperation):
            db.session.rollback()
            flash("Please enter valid dates and purchase cost.", "danger")
        except Exception:
            db.session.rollback()
            flash("Unable to update asset.", "danger")

    return render_template(
        "asset_management/edit_asset.html",
        asset=asset,
        categories=categories,
    )


@asset_bp.route("/delete/<int:asset_id>", methods=["POST"])
def delete_asset(asset_id):
    asset = db.session.get(Asset, asset_id)
    if not asset:
        abort(404)

    if AssetAssignment.query.filter_by(
        asset_id=asset.id, status="Active"
    ).first():
        flash("Return the assigned asset before deletion.", "warning")
        return redirect(url_for("asset_management.asset_list"))

    if AssetTransfer.query.filter_by(
        asset_id=asset.id, status="Pending"
    ).first():
        flash("A pending transfer prevents deletion.", "warning")
        return redirect(url_for("asset_management.asset_list"))

    if AssetMaintenance.query.filter_by(
        asset_id=asset.id, status="Pending"
    ).first():
        flash("Complete or cancel pending maintenance first.", "warning")
        return redirect(url_for("asset_management.asset_list"))

    try:
        record_asset_activity(
            asset.id,
            "Asset Deactivation",
            f"Deactivated asset {asset.asset_code}",
        )
        asset.status = "Inactive"
        db.session.commit()
        flash("Asset deactivated successfully.", "success")
    except Exception:
        db.session.rollback()
        flash("Unable to deactivate asset.", "danger")

    return redirect(url_for("asset_management.asset_list"))


# ============================================================================
# CATEGORIES
# ============================================================================

@asset_bp.route("/categories")
def category_list():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    query = AssetCategory.query

    if search:
        query = query.filter(
            or_(
                AssetCategory.name.ilike(f"%{search}%"),
                AssetCategory.code.ilike(f"%{search}%"),
            )
        )

    if status:
        query = query.filter_by(status=status)

    categories = query.order_by(AssetCategory.name.asc()).all()

    return render_template(
        "asset_management/category_list.html",
        categories=categories,
        search=search,
        selected_status=status,
    )


@asset_bp.route("/categories/create", methods=["GET", "POST"])
def create_category():
    parent_categories = AssetCategory.query.filter_by(
        status="Active", parent_id=None
    ).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("code", "").strip().upper()
        description = request.form.get("description", "").strip()
        parent_id = request.form.get("parent_id", "").strip()

        if not name or not code:
            flash("Category name and code are required.", "danger")
            return redirect(url_for("asset_management.create_category"))

        if AssetCategory.query.filter(
            or_(AssetCategory.name.ilike(name), AssetCategory.code == code)
        ).first():
            flash("Category name or code already exists.", "danger")
            return redirect(url_for("asset_management.create_category"))

        parent = None
        if parent_id:
            try:
                parent = db.session.get(AssetCategory, int(parent_id))
            except (ValueError, TypeError):
                parent = None

            if not parent or parent.parent_id is not None:
                flash("Select a valid parent category.", "danger")
                return redirect(url_for("asset_management.create_category"))

        category = AssetCategory(
            name=name,
            code=code,
            description=description,
            parent_id=parent.id if parent else None,
            status="Active",
        )

        try:
            db.session.add(category)
            db.session.flush()

            record_asset_activity(
                None,
                "Category Created",
                f"Created asset category: {name}",
            )
            db.session.commit()

            flash("Asset category created successfully.", "success")
            return redirect(url_for("asset_management.category_list"))

        except IntegrityError:
            db.session.rollback()
            flash("Category code already exists.", "danger")
        except Exception:
            db.session.rollback()
            flash("Unable to create category.", "danger")

    return render_template(
        "asset_management/category_form.html",
        category=None,
        parent_categories=parent_categories,
    )


@asset_bp.route("/categories/edit/<int:category_id>", methods=["GET", "POST"])
def edit_category(category_id):
    category = db.session.get(AssetCategory, category_id)
    if not category:
        abort(404)

    parent_categories = AssetCategory.query.filter(
        AssetCategory.id != category.id,
        AssetCategory.parent_id.is_(None),
        AssetCategory.status == "Active",
    ).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("code", "").strip().upper()
        description = request.form.get("description", "").strip()
        parent_id = request.form.get("parent_id", "").strip()

        if not name or not code:
            flash("Category name and code are required.", "danger")
            return redirect(
                url_for(
                    "asset_management.edit_category", category_id=category.id
                )
            )

        duplicate = AssetCategory.query.filter(
            AssetCategory.id != category.id,
            or_(AssetCategory.name.ilike(name), AssetCategory.code == code),
        ).first()

        if duplicate:
            flash("Category name or code already exists.", "danger")
            return redirect(
                url_for(
                    "asset_management.edit_category", category_id=category.id
                )
            )

        parent = None
        if parent_id:
            try:
                parent = db.session.get(AssetCategory, int(parent_id))
            except (ValueError, TypeError):
                parent = None

            if not parent or parent.parent_id is not None:
                flash("Invalid parent category.", "danger")
                return redirect(
                    url_for(
                        "asset_management.edit_category",
                        category_id=category.id,
                    )
                )

        if parent and parent.id == category.id:
            flash("A category cannot be its own parent.", "danger")
            return redirect(
                url_for(
                    "asset_management.edit_category", category_id=category.id
                )
            )

        try:
            category.name = name
            category.code = code
            category.description = description
            category.parent_id = parent.id if parent else None

            record_asset_activity(
                None,
                "Category Updated",
                f"Updated asset category: {name}",
            )
            db.session.commit()

            flash("Category updated successfully.", "success")
            return redirect(url_for("asset_management.category_list"))

        except Exception:
            db.session.rollback()
            flash("Unable to update category.", "danger")

    return render_template(
        "asset_management/category_form.html",
        category=category,
        parent_categories=parent_categories,
    )


@asset_bp.route("/categories/toggle/<int:category_id>", methods=["POST"])
def toggle_category(category_id):
    category = db.session.get(AssetCategory, category_id)
    if not category:
        abort(404)

    if category.status == "Active":
        category.status = "Inactive"
        action = "Category Deactivated"
    else:
        if category.parent_id:
            parent = db.session.get(AssetCategory, category.parent_id)
            if not parent or parent.status != "Active":
                flash("Activate the parent category first.", "warning")
                return redirect(url_for("asset_management.category_list"))

        category.status = "Active"
        action = "Category Activated"

    try:
        record_asset_activity(None, action, f"{action}: {category.name}")
        db.session.commit()
        flash("Category status updated successfully.", "success")
    except Exception:
        db.session.rollback()
        flash("Unable to update category status.", "danger")

    return redirect(url_for("asset_management.category_list"))


# ============================================================================
# DEPARTMENT ASSETS
# ============================================================================

@asset_bp.route("/departments")
def department_assets():
    department_id = request.args.get("department_id", type=int)

    query = (
        db.session.query(
            Asset.department_id,
            func.count(Asset.id).label("total_assets"),
        )
        .filter(
            Asset.department_id.isnot(None),
            Asset.status != "Inactive",
        )
        .group_by(Asset.department_id)
    )

    if department_id:
        query = query.filter(Asset.department_id == department_id)

    department_summary = query.all()

    return render_template(
        "asset_management/department_assets.html",
        department_summary=department_summary,
        selected_department=department_id,
    )


@asset_bp.route("/departments/<int:department_id>/assets")
def department_asset_list(department_id):
    assets = (
        Asset.query.filter_by(department_id=department_id)
        .order_by(Asset.created_at.desc())
        .all()
    )

    return render_template(
        "asset_management/department_asset_list.html",
        department_id=department_id,
        assets=assets,
        total_assets=len(assets),
        available_assets=sum(1 for a in assets if a.status == "Available"),
        assigned_assets=sum(1 for a in assets if a.status == "Assigned"),
        maintenance_assets=sum(
            1 for a in assets if a.status == "Maintenance"
        ),
    )


@asset_bp.route("/departments/allocate", methods=["GET", "POST"])
def allocate_department_asset():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        department_id = request.form.get("department_id", type=int)
        custodian_id = request.form.get("custodian_id", type=int)
        remarks = request.form.get("remarks", "").strip()

        if not asset_id or not department_id:
            flash("Asset and department are required.", "danger")
            return redirect(
                url_for("asset_management.allocate_department_asset")
            )

        asset = db.session.get(Asset, asset_id)
        if not asset:
            abort(404)

        if asset.status != "Available":
            flash("Only available assets can be allocated.", "warning")
            return redirect(
                url_for("asset_management.allocate_department_asset")
            )

        try:
            record = DepartmentAssetRecord(
                asset_id=asset.id,
                department_id=department_id,
                custodian_id=custodian_id,
                allocation_date=date.today(),
                remarks=remarks,
            )

            asset.department_id = department_id
            asset.employee_id = custodian_id
            asset.status = "Assigned"

            db.session.add(record)

            record_asset_activity(
                asset.id,
                "Department Allocation",
                f"Allocated asset to department ID {department_id}",
            )
            db.session.commit()

            flash("Asset allocated successfully.", "success")
            return redirect(
                url_for(
                    "asset_management.department_asset_list",
                    department_id=department_id,
                )
            )

        except Exception:
            db.session.rollback()
            flash("Unable to allocate asset.", "danger")

    available_assets = Asset.query.filter_by(status="Available").all()

    # Replace with your HR department and employee queries.
    departments = []
    employees = []

    return render_template(
        "asset_management/allocate_asset.html",
        available_assets=available_assets,
        departments=departments,
        employees=employees,
    )


@asset_bp.route(
    "/departments/return/<int:record_id>", methods=["POST"]
)
def return_department_asset(record_id):
    record = db.session.get(DepartmentAssetRecord, record_id)
    if not record:
        abort(404)

    if record.status != "Active":
        flash("This allocation is not active.", "warning")
        return redirect(
            url_for(
                "asset_management.department_asset_list",
                department_id=record.department_id,
            )
        )

    asset = db.session.get(Asset, record.asset_id)

    try:
        record.status = "Returned"

        if asset:
            asset.department_id = None
            asset.employee_id = None
            asset.status = "Available"

        record_asset_activity(
            record.asset_id,
            "Department Asset Return",
            f"Returned asset from department ID {record.department_id}",
        )
        db.session.commit()
        flash("Asset returned successfully.", "success")

    except Exception:
        db.session.rollback()
        flash("Unable to return asset.", "danger")

    return redirect(
        url_for(
            "asset_management.department_asset_list",
            department_id=record.department_id,
        )
    )


# ============================================================================
# EMPLOYEE ASSIGNMENTS
# ============================================================================

@asset_bp.route("/employees/assign", methods=["GET", "POST"])
@login_required
def assign_employee_asset():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        department_id = request.form.get("department_id", type=int)
        employee_id = request.form.get("employee_id", type=int)
        remarks = request.form.get("remarks", "").strip()

        if not all([asset_id, department_id, employee_id]):
            flash("Please complete all required fields.", "danger")
            return redirect(
                url_for("asset_management.assign_employee_asset")
            )

        asset = Asset.query.get_or_404(asset_id)

        if asset.department_id != department_id:
            flash(
                "This asset does not belong to the selected department.",
                "danger",
            )
            return redirect(
                url_for("asset_management.assign_employee_asset")
            )

        if AssetAssignment.query.filter_by(
            asset_id=asset.id, status="Active"
        ).first():
            flash(
                "This asset is already assigned to an employee.", "warning"
            )
            return redirect(
                url_for("asset_management.assign_employee_asset")
            )

        # TODO: Verify employee_id belongs to department_id using HR models.

        assignment = AssetAssignment(
            asset_id=asset.id,
            department_id=department_id,
            employee_id=employee_id,
            assigned_date=date.today(),
            assigned_by=current_user.id,
            remarks=remarks,
            status="Active",
        )

        asset.employee_id = employee_id
        asset.status = "Assigned"

        db.session.add(assignment)
        db.session.commit()

        record_asset_activity(
            asset.id,
            "Employee Assignment",
            f"Asset assigned to employee ID {employee_id}",
        )

        flash("Asset successfully assigned to the employee.", "success")
        return redirect(
            url_for("asset_management.employee_asset_history")
        )

    available_assets = Asset.query.filter(
        Asset.status == "Assigned",
        Asset.department_id.isnot(None),
        ~Asset.id.in_(
            db.session.query(AssetAssignment.asset_id).filter_by(
                status="Active"
            )
        ),
    ).all()

    # Replace with queries from your HR module.
    employees = []
    departments = []

    return render_template(
        "asset_management/assign_employee_asset.html",
        assets=available_assets,
        employees=employees,
        departments=departments,
    )


@asset_bp.route(
    "/employees/return/<int:assignment_id>", methods=["POST"]
)
@login_required
def return_employee_asset(assignment_id):
    assignment = AssetAssignment.query.get_or_404(assignment_id)

    if assignment.status != "Active":
        flash("This assignment has already been returned.", "warning")
        return redirect(
            url_for("asset_management.employee_asset_history")
        )

    asset = Asset.query.get_or_404(assignment.asset_id)

    assignment.status = "Returned"
    assignment.returned_date = date.today()

    if asset.employee_id == assignment.employee_id:
        asset.employee_id = None

    asset.status = "Assigned" if asset.department_id else "Available"

    db.session.commit()

    record_asset_activity(
        asset.id,
        "Employee Asset Return",
        f"Asset returned by employee ID {assignment.employee_id}",
    )

    flash("Employee asset return recorded successfully.", "success")
    return redirect(url_for("asset_management.employee_asset_history"))


@asset_bp.route("/employees/history")
@login_required
def employee_asset_history():
    assignments = AssetAssignment.query.order_by(
        AssetAssignment.created_at.desc()
    ).all()

    return render_template(
        "asset_management/employee_asset_history.html",
        assignments=assignments,
    )


# ============================================================================
# TRANSFERS
# ============================================================================

@asset_bp.route("/transfers/create", methods=["GET", "POST"])
@login_required
def create_asset_transfer():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        to_department_id = request.form.get("to_department_id", type=int)
        reason = request.form.get("reason", "").strip()

        if not asset_id or not to_department_id or not reason:
            flash("Please complete all required fields.", "danger")
            return redirect(
                url_for("asset_management.create_asset_transfer")
            )

        asset = Asset.query.get_or_404(asset_id)

        if not asset.department_id:
            flash(
                "This asset is not allocated to a department.", "danger"
            )
            return redirect(
                url_for("asset_management.create_asset_transfer")
            )

        if asset.department_id == to_department_id:
            flash("The receiving department must be different.", "warning")
            return redirect(
                url_for("asset_management.create_asset_transfer")
            )

        if asset.status != "Assigned":
            flash(
                "Only eligible department assets can be transferred.",
                "warning",
            )
            return redirect(
                url_for("asset_management.create_asset_transfer")
            )

        if AssetTransfer.query.filter_by(
            asset_id=asset.id, status="Pending"
        ).first():
            flash(
                "A pending transfer already exists for this asset.",
                "warning",
            )
            return redirect(
                url_for("asset_management.create_asset_transfer")
            )

        transfer = AssetTransfer(
            asset_id=asset.id,
            from_department_id=asset.department_id,
            to_department_id=to_department_id,
            requested_by=current_user.id,
            request_date=date.today(),
            reason=reason,
            status="Pending",
        )

        db.session.add(transfer)
        db.session.commit()

        record_asset_activity(
            asset.id,
            "Transfer Request",
            f"Transfer requested to department {to_department_id}",
        )

        flash("Asset transfer request submitted successfully.", "success")
        return redirect(url_for("asset_management.asset_transfer_list"))

    assets = Asset.query.filter(
        Asset.department_id.isnot(None), Asset.status == "Assigned"
    ).all()

    # Connect to your HR department model.
    departments = []

    return render_template(
        "asset_management/create_transfer.html",
        assets=assets,
        departments=departments,
    )


@asset_bp.route("/transfers/<int:transfer_id>/decision", methods=["POST"])
@login_required
def decide_asset_transfer(transfer_id):
    transfer = AssetTransfer.query.get_or_404(transfer_id)

    if transfer.status != "Pending":
        flash(
            "This transfer request has already been processed.", "warning"
        )
        return redirect(url_for("asset_management.asset_transfer_list"))

    decision = request.form.get("decision")
    if decision not in ["Approved", "Rejected"]:
        flash("Invalid transfer decision.", "danger")
        return redirect(url_for("asset_management.asset_transfer_list"))

    transfer.status = decision
    transfer.approved_by = current_user.id
    transfer.approval_date = date.today()

    db.session.commit()

    record_asset_activity(
        transfer.asset_id,
        f"Transfer {decision}",
        f"Transfer request {transfer.id} was {decision.lower()}.",
    )

    flash(f"Transfer request {decision.lower()} successfully.", "success")
    return redirect(url_for("asset_management.asset_transfer_list"))


@asset_bp.route("/transfers/<int:transfer_id>/receive", methods=["POST"])
@login_required
def receive_asset_transfer(transfer_id):
    transfer = AssetTransfer.query.get_or_404(transfer_id)

    if transfer.status != "Approved":
        flash("Only approved transfers can be received.", "warning")
        return redirect(url_for("asset_management.asset_transfer_list"))

    asset = Asset.query.get_or_404(transfer.asset_id)

    if asset.department_id != transfer.from_department_id:
        flash(
            "Asset department has changed. Please review this transfer.",
            "danger",
        )
        return redirect(url_for("asset_management.asset_transfer_list"))

    if AssetAssignment.query.filter_by(
        asset_id=asset.id, status="Active"
    ).first():
        flash(
            "The asset must be returned by its employee before transfer.",
            "warning",
        )
        return redirect(url_for("asset_management.asset_transfer_list"))

    transfer.status = "Completed"
    transfer.received_by = current_user.id
    transfer.received_date = date.today()

    asset.department_id = transfer.to_department_id
    asset.employee_id = None
    asset.status = "Assigned"

    db.session.commit()

    record_asset_activity(
        asset.id,
        "Department Transfer Completed",
        f"Asset transferred to department {transfer.to_department_id}",
    )

    flash("Asset transfer completed successfully.", "success")
    return redirect(url_for("asset_management.asset_transfer_list"))


@asset_bp.route("/transfers")
@login_required
def asset_transfer_list():
    transfers = AssetTransfer.query.order_by(
        AssetTransfer.created_at.desc()
    ).all()

    return render_template(
        "asset_management/transfer_list.html", transfers=transfers
    )


# ============================================================================
# MAINTENANCE
# ============================================================================

@asset_bp.route("/maintenance/create", methods=["GET", "POST"])
@login_required
def create_maintenance():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        maintenance_type = request.form.get("maintenance_type")
        priority = request.form.get("priority")
        description = request.form.get("description", "").strip()

        if not all([asset_id, maintenance_type, priority, description]):
            flash("Please complete all required fields.", "danger")
            return redirect(
                url_for("asset_management.create_maintenance")
            )

        asset = Asset.query.get_or_404(asset_id)

        if asset.status == "Inactive":
            flash(
                "Inactive assets cannot receive maintenance requests.",
                "danger",
            )
            return redirect(
                url_for("asset_management.create_maintenance")
            )

        maintenance = AssetMaintenance(
            asset_id=asset.id,
            maintenance_type=maintenance_type,
            priority=priority,
            description=description,
            requested_by=current_user.id,
            request_date=date.today(),
            status="Pending",
        )

        db.session.add(maintenance)
        db.session.commit()

        record_asset_activity(
            asset.id,
            "Maintenance Request",
            "New maintenance request submitted.",
        )

        flash("Maintenance request submitted successfully.", "success")
        return redirect(url_for("asset_management.maintenance_list"))

    assets = (
        Asset.query.filter(Asset.status != "Inactive")
        .order_by(Asset.asset_code)
        .all()
    )

    return render_template(
        "asset_management/create_maintenance.html", assets=assets
    )


@asset_bp.route(
    "/maintenance/<int:maintenance_id>/approve", methods=["POST"]
)
@login_required
def approve_maintenance(maintenance_id):
    maintenance = AssetMaintenance.query.get_or_404(maintenance_id)

    if maintenance.status != "Pending":
        flash("This request has already been processed.", "warning")
        return redirect(url_for("asset_management.maintenance_list"))

    maintenance.status = "Approved"
    maintenance.approved_by = current_user.id
    maintenance.approval_remarks = request.form.get(
        "approval_remarks", ""
    ).strip()

    db.session.commit()

    record_asset_activity(
        maintenance.asset_id,
        "Maintenance Approved",
        f"Maintenance request {maintenance.id} approved.",
    )

    flash("Maintenance request approved.", "success")
    return redirect(url_for("asset_management.maintenance_list"))


@asset_bp.route(
    "/maintenance/<int:maintenance_id>/start", methods=["POST"]
)
@login_required
def start_maintenance(maintenance_id):
    maintenance = AssetMaintenance.query.get_or_404(maintenance_id)

    if maintenance.status != "Approved":
        flash(
            "Only approved requests can start maintenance.", "warning"
        )
        return redirect(url_for("asset_management.maintenance_list"))

    asset = Asset.query.get_or_404(maintenance.asset_id)

    maintenance.status = "In Progress"
    maintenance.service_date = date.today()
    asset.status = "Maintenance"

    db.session.commit()

    record_asset_activity(
        asset.id,
        "Maintenance Started",
        f"Maintenance request {maintenance.id} started.",
    )

    flash("Maintenance started successfully.", "success")
    return redirect(url_for("asset_management.maintenance_list"))


@asset_bp.route(
    "/maintenance/<int:maintenance_id>/complete", methods=["POST"]
)
@login_required
def complete_maintenance(maintenance_id):
    maintenance = AssetMaintenance.query.get_or_404(maintenance_id)

    if maintenance.status != "In Progress":
        flash("Only ongoing maintenance can be completed.", "warning")
        return redirect(url_for("asset_management.maintenance_list"))

    try:
        cost = Decimal(request.form.get("cost", "0"))
        if not cost.is_finite() or cost < 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        flash("Please enter a valid maintenance cost.", "danger")
        return redirect(url_for("asset_management.maintenance_list"))

    asset = Asset.query.get_or_404(maintenance.asset_id)

    maintenance.cost = cost
    maintenance.service_provider = request.form.get(
        "service_provider", ""
    ).strip()
    maintenance.completion_remarks = request.form.get(
        "completion_remarks", ""
    ).strip()
    maintenance.completion_date = date.today()
    maintenance.status = "Completed"

    asset.status = "Assigned" if asset.department_id else "Available"

    db.session.commit()

    record_asset_activity(
        asset.id,
        "Maintenance Completed",
        f"Maintenance completed. Cost: {cost}",
    )

    flash("Maintenance completed successfully.", "success")
    return redirect(url_for("asset_management.maintenance_list"))


@asset_bp.route("/maintenance")
@login_required
def maintenance_list():
    maintenance_records = AssetMaintenance.query.order_by(
        AssetMaintenance.created_at.desc()
    ).all()

    return render_template(
        "asset_management/maintenance_list.html",
        records=maintenance_records,
        total_requests=AssetMaintenance.query.count(),
        pending_requests=AssetMaintenance.query.filter_by(
            status="Pending"
        ).count(),
        ongoing_requests=AssetMaintenance.query.filter_by(
            status="In Progress"
        ).count(),
        completed_requests=AssetMaintenance.query.filter_by(
            status="Completed"
        ).count(),
    )


# ============================================================================
# DEPRECIATION
# ============================================================================

@asset_bp.route("/depreciation/calculate", methods=["POST"])
@login_required
def calculate_asset_depreciation():
    asset_id = request.form.get("asset_id", type=int)
    financial_year = request.form.get("financial_year", "").strip()

    if not asset_id or not financial_year:
        flash("Please select an asset and financial year.", "danger")
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    try:
        year = int(financial_year)
        if year < 1900 or year > date.today().year:
            raise ValueError
    except ValueError:
        flash("Invalid financial year.", "danger")
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    asset = Asset.query.get_or_404(asset_id)

    if not asset.purchase_cost or not asset.useful_life_years:
        flash(
            "Asset purchase cost and useful life are required.", "danger"
        )
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    if asset.depreciation_status != "Active":
        flash("Depreciation is not active for this asset.", "warning")
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    if AssetDepreciation.query.filter_by(
        asset_id=asset.id, financial_year=financial_year
    ).first():
        flash(
            "Depreciation has already been recorded for this year.",
            "warning",
        )
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    purchase_cost = money(asset.purchase_cost)
    salvage_value = money(asset.salvage_value)
    start_date = asset.depreciation_start_date or asset.purchase_date

    if not start_date:
        flash("A depreciation start date is required.", "danger")
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    period_start = max(start_date, date(year, 1, 1))
    period_end = date(year, 12, 31)

    if period_start > period_end:
        flash(
            "The asset was not yet available for depreciation in this year.",
            "warning",
        )
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    previous = (
        AssetDepreciation.query.filter(
            AssetDepreciation.asset_id == asset.id,
            AssetDepreciation.period_end < period_start,
        )
        .order_by(AssetDepreciation.period_end.desc())
        .first()
    )

    if previous:
        opening_value = money(previous.closing_book_value)
        accumulated_before = money(previous.accumulated_depreciation)
    else:
        opening_value = purchase_cost
        accumulated_before = Decimal("0.00")

    if opening_value <= salvage_value:
        flash("The asset has reached its salvage value.", "info")
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    if asset.depreciation_method == "Straight Line":
        annual_amount = calculate_straight_line(
            purchase_cost, salvage_value, asset.useful_life_years
        )
    elif asset.depreciation_method == "Declining Balance":
        annual_amount = calculate_declining_balance(
            opening_value, salvage_value, asset.useful_life_years
        )
    else:
        flash("Unsupported depreciation method.", "danger")
        return redirect(
            url_for("asset_management.depreciation_dashboard")
        )

    days_in_year = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    depreciation_days = (period_end - period_start).days + 1

    depreciation_amount = money(
        annual_amount * Decimal(depreciation_days) / Decimal(days_in_year)
    )
    depreciation_amount = min(
        depreciation_amount, opening_value - salvage_value
    )

    accumulated = money(accumulated_before + depreciation_amount)
    closing_value = money(opening_value - depreciation_amount)

    record = AssetDepreciation(
        asset_id=asset.id,
        financial_year=financial_year,
        period_start=period_start,
        period_end=period_end,
        opening_book_value=opening_value,
        depreciation_amount=depreciation_amount,
        accumulated_depreciation=accumulated,
        closing_book_value=closing_value,
        calculation_method=asset.depreciation_method,
        created_by=current_user.id,
    )

    db.session.add(record)
    db.session.commit()

    record_asset_activity(
        asset.id,
        "Depreciation Calculated",
        f"Year {financial_year}: depreciation {depreciation_amount}",
    )

    flash("Asset depreciation calculated successfully.", "success")
    return redirect(url_for("asset_management.depreciation_dashboard"))


@asset_bp.route("/depreciation")
@login_required
def depreciation_dashboard():
    records = AssetDepreciation.query.order_by(
        AssetDepreciation.created_at.desc()
    ).all()

    assets = Asset.query.filter(
        Asset.depreciation_status == "Active"
    ).all()

    total_depreciation = db.session.query(
        func.coalesce(func.sum(AssetDepreciation.depreciation_amount), 0)
    ).scalar()

    return render_template(
        "asset_management/depreciation_dashboard.html",
        records=records,
        assets=assets,
        total_depreciation=total_depreciation,
        now_year=date.today().year,
    )


# ============================================================================
# DISPOSALS
# ============================================================================

@asset_bp.route("/disposals/create", methods=["GET", "POST"])
@login_required
def create_asset_disposal():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        disposal_type = request.form.get("disposal_type", "").strip()
        reason = request.form.get("reason", "").strip()

        if not asset_id or not disposal_type or not reason:
            flash("Please complete all required fields.", "danger")
            return redirect(
                url_for("asset_management.create_asset_disposal")
            )

        allowed_types = [
            "Sale",
            "Write-Off",
            "Scrapping",
            "Donation",
            "Loss",
            "Other",
        ]

        if disposal_type not in allowed_types:
            flash("Invalid disposal type.", "danger")
            return redirect(
                url_for("asset_management.create_asset_disposal")
            )

        asset = Asset.query.get_or_404(asset_id)

        if asset.disposal_status != "Active":
            flash(
                "This asset already has a disposal request or has been disposed.",
                "warning",
            )
            return redirect(
                url_for("asset_management.create_asset_disposal")
            )

        if AssetTransfer.query.filter_by(
            asset_id=asset.id, status="Pending"
        ).first():
            flash("This asset has a pending transfer request.", "warning")
            return redirect(
                url_for("asset_management.create_asset_disposal")
            )

        if AssetMaintenance.query.filter(
            AssetMaintenance.asset_id == asset.id,
            AssetMaintenance.status.in_(
                ["Pending", "Approved", "In Progress"]
            ),
        ).first():
            flash(
                "This asset has an unfinished maintenance request.",
                "warning",
            )
            return redirect(
                url_for("asset_management.create_asset_disposal")
            )

        if AssetAssignment.query.filter_by(
            asset_id=asset.id, status="Active"
        ).first():
            flash(
                "The asset must be returned by its employee before disposal.",
                "warning",
            )
            return redirect(
                url_for("asset_management.create_asset_disposal")
            )

        disposal = AssetDisposal(
            asset_id=asset.id,
            disposal_type=disposal_type,
            reason=reason,
            requested_by=current_user.id,
            request_date=date.today(),
            status="Pending",
        )

        asset.disposal_status = "Under Review"

        db.session.add(disposal)
        db.session.commit()

        record_asset_activity(
            asset.id,
            "Disposal Request",
            f"Disposal request submitted: {disposal_type}",
        )

        flash("Asset disposal request submitted successfully.", "success")
        return redirect(url_for("asset_management.disposal_list"))

    assets = Asset.query.filter_by(disposal_status="Active").all()

    return render_template(
        "asset_management/create_disposal.html", assets=assets
    )


@asset_bp.route(
    "/disposals/<int:disposal_id>/inspect", methods=["POST"]
)
@login_required
def inspect_asset_disposal(disposal_id):
    disposal = AssetDisposal.query.get_or_404(disposal_id)

    if disposal.status != "Pending":
        flash(
            "This disposal request is not awaiting inspection.", "warning"
        )
        return redirect(url_for("asset_management.disposal_list"))

    report = request.form.get("condition_report", "").strip()
    if not report:
        flash("An inspection report is required.", "danger")
        return redirect(url_for("asset_management.disposal_list"))

    try:
        estimated_value = Decimal(
            request.form.get("estimated_value", "0")
        )
        if not estimated_value.is_finite() or estimated_value < 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        flash("Enter a valid estimated value.", "danger")
        return redirect(url_for("asset_management.disposal_list"))

    disposal.condition_report = report
    disposal.estimated_value = estimated_value
    disposal.inspected_by = current_user.id
    disposal.inspection_date = date.today()
    disposal.status = "Inspected"

    db.session.commit()

    record_asset_activity(
        disposal.asset_id,
        "Asset Inspection",
        "Disposal inspection completed.",
    )

    flash("Asset inspection recorded successfully.", "success")
    return redirect(url_for("asset_management.disposal_list"))


@asset_bp.route(
    "/disposals/<int:disposal_id>/approve", methods=["POST"]
)
@login_required
def approve_asset_disposal(disposal_id):
    disposal = AssetDisposal.query.get_or_404(disposal_id)

    if disposal.status != "Inspected":
        flash("Only inspected requests can be approved.", "warning")
        return redirect(url_for("asset_management.disposal_list"))

    disposal.status = "Approved"
    disposal.approved_by = current_user.id
    disposal.approval_date = date.today()

    db.session.commit()

    record_asset_activity(
        disposal.asset_id,
        "Disposal Approved",
        "Asset disposal approved by authorized management.",
    )

    flash("Asset disposal approved.", "success")
    return redirect(url_for("asset_management.disposal_list"))


@asset_bp.route(
    "/disposals/<int:disposal_id>/complete", methods=["POST"]
)
@login_required
def complete_asset_disposal(disposal_id):
    disposal = AssetDisposal.query.get_or_404(disposal_id)

    if disposal.status != "Approved":
        flash(
            "Only approved disposal requests can be completed.", "warning"
        )
        return redirect(url_for("asset_management.disposal_list"))

    asset = Asset.query.get_or_404(disposal.asset_id)

    if AssetAssignment.query.filter_by(
        asset_id=asset.id, status="Active"
    ).first():
        flash(
            "Return the asset from its employee before disposal.", "danger"
        )
        return redirect(url_for("asset_management.disposal_list"))

    if AssetTransfer.query.filter_by(
        asset_id=asset.id, status="Pending"
    ).first():
        flash("Resolve pending transfers before disposal.", "danger")
        return redirect(url_for("asset_management.disposal_list"))

    if AssetMaintenance.query.filter(
        AssetMaintenance.asset_id == asset.id,
        AssetMaintenance.status.in_(
            ["Pending", "Approved", "In Progress"]
        ),
    ).first():
        flash("Resolve unfinished maintenance before disposal.", "danger")
        return redirect(url_for("asset_management.disposal_list"))

    try:
        sale_proceeds = Decimal(
            request.form.get("sale_proceeds", "0")
        )
        book_value = Decimal(request.form.get("book_value", "0"))

        if (
            not sale_proceeds.is_finite()
            or not book_value.is_finite()
            or sale_proceeds < 0
            or book_value < 0
        ):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        flash("Enter valid financial values.", "danger")
        return redirect(url_for("asset_management.disposal_list"))

    disposal.sale_proceeds = sale_proceeds
    disposal.book_value_at_disposal = book_value
    disposal.finance_reviewed_by = current_user.id
    disposal.status = "Completed"
    disposal.disposal_date = date.today()

    asset.disposal_status = "Disposed"
    asset.status = "Disposed"
    asset.department_id = None
    asset.employee_id = None

    db.session.commit()

    record_asset_activity(
        asset.id,
        "Asset Disposed",
        f"Asset disposal completed. Sale proceeds: {sale_proceeds}",
    )

    flash("Asset disposal completed successfully.", "success")
    return redirect(url_for("asset_management.disposal_list"))


@asset_bp.route("/disposals")
@login_required
def disposal_list():
    records = AssetDisposal.query.order_by(
        AssetDisposal.created_at.desc()
    ).all()

    return render_template(
        "asset_management/disposal_list.html", records=records
    )


# ============================================================================
# REPORTS
# ============================================================================

@asset_bp.route("/reports")
@login_required
def asset_reports():
    total_assets = Asset.query.count()
    active_assets = Asset.query.filter(
        Asset.status != "Disposed"
    ).count()
    assigned_assets = Asset.query.filter_by(status="Assigned").count()
    available_assets = Asset.query.filter_by(status="Available").count()
    maintenance_assets = Asset.query.filter_by(
        status="Maintenance"
    ).count()
    disposed_assets = Asset.query.filter(
        (Asset.status == "Disposed")
        | (Asset.disposal_status == "Disposed")
    ).count()

    total_maintenance_cost = db.session.query(
        func.coalesce(func.sum(AssetMaintenance.cost), 0)
    ).filter(AssetMaintenance.status == "Completed").scalar()

    total_depreciation = db.session.query(
        func.coalesce(func.sum(AssetDepreciation.depreciation_amount), 0)
    ).scalar()

    return render_template(
        "asset_management/reports_dashboard.html",
        total_assets=total_assets,
        active_assets=active_assets,
        assigned_assets=assigned_assets,
        available_assets=available_assets,
        maintenance_assets=maintenance_assets,
        disposed_assets=disposed_assets,
        total_maintenance_cost=total_maintenance_cost,
        total_depreciation=total_depreciation,
    )


@asset_bp.route("/reports/inventory")
@login_required
def asset_inventory_report():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    category_id = request.args.get("category_id", type=int)

    query = Asset.query

    if search:
        query = query.filter(
            or_(
                Asset.asset_code.ilike(f"%{search}%"),
                Asset.name.ilike(f"%{search}%"),
                Asset.serial_number.ilike(f"%{search}%"),
            )
        )

    if status:
        query = query.filter(Asset.status == status)

    if category_id:
        query = query.filter(Asset.category_id == category_id)

    assets = query.order_by(Asset.created_at.desc()).all()
    categories = AssetCategory.query.order_by(AssetCategory.name).all()

    return render_template(
        "asset_management/inventory_report.html",
        assets=assets,
        categories=categories,
        search=search,
        selected_status=status,
        selected_category=category_id,
    )


@asset_bp.route("/reports/departments")
@login_required
def department_asset_report():
    department_id = request.args.get("department_id", type=int)

    query = Asset.query
    if department_id:
        query = query.filter(Asset.department_id == department_id)

    assets = query.order_by(Asset.department_id, Asset.name).all()

    # Replace with your HR Department model.
    departments = []

    return render_template(
        "asset_management/department_report.html",
        assets=assets,
        departments=departments,
        selected_department=department_id,
    )


@asset_bp.route("/reports/maintenance")
@login_required
def maintenance_cost_report():
    maintenance_records = AssetMaintenance.query.order_by(
        AssetMaintenance.service_date.desc()
    ).all()

    total_cost = sum(
        (record.cost or 0)
        for record in maintenance_records
        if record.status == "Completed"
    )

    return render_template(
        "asset_management/maintenance_report.html",
        maintenance_records=maintenance_records,
        total_cost=total_cost,
    )


@asset_bp.route("/reports/assets/export")
@login_required
def export_asset_inventory():
    assets = Asset.query.order_by(Asset.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "Asset Code",
            "Asset Name",
            "Category",
            "Serial Number",
            "Department ID",
            "Employee ID",
            "Purchase Date",
            "Purchase Cost",
            "Condition",
            "Status",
        ]
    )

    for asset in assets:
        writer.writerow(
            [
                _safe_csv(asset.asset_code),
                _safe_csv(asset.name),
                _safe_csv(
                    asset.category.name if asset.category else ""
                ),
                _safe_csv(asset.serial_number),
                _safe_csv(asset.department_id),
                _safe_csv(asset.employee_id),
                _safe_csv(asset.purchase_date),
                _safe_csv(asset.purchase_cost),
                _safe_csv(asset.condition),
                _safe_csv(asset.status),
            ]
        )

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = (
        "attachment; filename=asset_inventory_report.csv"
    )
    response.headers["Content-Type"] = "text/csv; charset=utf-8"

    return response


# ============================================================================
# LABELS / BARCODES
# ============================================================================

@asset_bp.route("/assets/<int:asset_id>/generate-label")
@login_required
def generate_asset_label(asset_id):
    asset = Asset.query.get_or_404(asset_id)

    # Add your asset.label.generate permission check here.

    if not asset.barcode:
        asset.barcode = f"DBA-{asset.id:06d}"
        db.session.commit()

    qr_path = generate_asset_qr(asset)
    barcode_path = generate_asset_barcode(asset)

    return render_template(
        "asset_management/asset_label.html",
        asset=asset,
        qr_filename=os.path.basename(qr_path),
        barcode_filename=os.path.basename(barcode_path),
    )


@asset_bp.route("/labels/<filename>")
@login_required
def asset_label_image(filename):
    allowed = (
        filename.startswith("asset_qr_") and filename.endswith(".png")
    ) or (
        filename.startswith("asset_barcode_")
        and filename.endswith(".png")
    )

    if not allowed:
        abort(404)

    directory = os.path.join(
        current_app.instance_path, "asset_labels"
    )
    filepath = os.path.join(directory, filename)

    if not os.path.isfile(filepath):
        abort(404)

    # Add the relevant asset-label permission check here.
    return send_file(filepath, mimetype="image/png")


# ============================================================================
# VERIFICATION CAMPAIGNS
# ============================================================================

@asset_bp.route("/verification")
@login_required
def verification_dashboard():
    campaigns = AssetVerificationCampaign.query.order_by(
        AssetVerificationCampaign.created_at.desc()
    ).all()

    return render_template(
        "asset_management/verification_dashboard.html",
        campaigns=campaigns,
        total_campaigns=AssetVerificationCampaign.query.count(),
        active_campaigns=AssetVerificationCampaign.query.filter(
            AssetVerificationCampaign.status.in_(
                ["Draft", "In Progress", "Under Review"]
            )
        ).count(),
        total_records=AssetVerificationRecord.query.count(),
        verified_records=AssetVerificationRecord.query.filter_by(
            verification_status="Verified"
        ).count(),
        missing_records=AssetVerificationRecord.query.filter_by(
            verification_status="Missing"
        ).count(),
    )


@asset_bp.route("/verification/create", methods=["GET", "POST"])
@login_required
def create_verification_campaign():
    if request.method == "POST":
        campaign = AssetVerificationCampaign(
            campaign_code=f"AV-{datetime.now():%Y%m%d%H%M%S}",
            title=request.form.get("title", "").strip(),
            description=request.form.get("description"),
            department_id=request.form.get("department_id", type=int),
            start_date=date.fromisoformat(request.form["start_date"]),
            end_date=(
                date.fromisoformat(request.form["end_date"])
                if request.form.get("end_date")
                else None
            ),
            created_by=current_user.id,
            status="Draft",
        )

        db.session.add(campaign)
        db.session.commit()

        flash("Verification campaign created successfully.", "success")
        return redirect(
            url_for(
                "asset_management.verification_campaign",
                campaign_id=campaign.id,
            )
        )

    # Replace with your HR department query.
    departments = []

    return render_template(
        "asset_management/create_verification.html",
        departments=departments,
    )


@asset_bp.route("/verification/<int:campaign_id>")
@login_required
def verification_campaign(campaign_id):
    campaign = AssetVerificationCampaign.query.get_or_404(campaign_id)

    query = Asset.query
    if campaign.department_id:
        query = query.filter(
            Asset.department_id == campaign.department_id
        )

    assets = query.order_by(Asset.asset_code).all()

    existing_ids = {
        record.asset_id
        for record in AssetVerificationRecord.query.filter_by(
            campaign_id=campaign.id
        ).all()
    }

    for asset in assets:
        if asset.id not in existing_ids:
            db.session.add(
                AssetVerificationRecord(
                    campaign_id=campaign.id,
                    asset_id=asset.id,
                    verification_status="Pending",
                )
            )

    db.session.commit()

    records = AssetVerificationRecord.query.filter_by(
        campaign_id=campaign.id
    ).all()

    return render_template(
        "asset_management/verification_campaign.html",
        campaign=campaign,
        records=records,
    )


@asset_bp.route("/verification/record/<int:record_id>", methods=["POST"])
@login_required
def verify_asset_record(record_id):
    record = AssetVerificationRecord.query.get_or_404(record_id)
    campaign = record.campaign

    if campaign.status not in ["In Progress"]:
        flash(
            "This campaign is not open for verification.", "warning"
        )
        return redirect(
            url_for(
                "asset_management.verification_campaign",
                campaign_id=campaign.id,
            )
        )

    status = request.form.get("verification_status")
    allowed_statuses = ["Verified", "Missing", "Damaged", "Wrong Location"]

    if status not in allowed_statuses:
        abort(400)

    record.verification_status = status
    record.physical_condition = request.form.get("physical_condition")
    record.physical_location = request.form.get("physical_location")
    record.remarks = request.form.get("remarks")
    record.verified_by = current_user.id
    record.verified_at = datetime.utcnow()

    db.session.commit()

    flash("Asset verification record updated.", "success")
    return redirect(
        url_for(
            "asset_management.verification_campaign",
            campaign_id=campaign.id,
        )
    )


@asset_bp.route(
    "/verification/<int:campaign_id>/start", methods=["POST"]
)
@login_required
def start_verification_campaign(campaign_id):
    campaign = AssetVerificationCampaign.query.get_or_404(campaign_id)

    if campaign.status != "Draft":
        abort(400)

    campaign.status = "In Progress"
    db.session.commit()

    flash("Verification campaign started.", "success")
    return redirect(
        url_for(
            "asset_management.verification_campaign",
            campaign_id=campaign.id,
        )
    )


@asset_bp.route(
    "/verification/<int:campaign_id>/submit", methods=["POST"]
)
@login_required
def submit_verification_campaign(campaign_id):
    campaign = AssetVerificationCampaign.query.get_or_404(campaign_id)

    if campaign.status != "In Progress":
        abort(400)

    pending = AssetVerificationRecord.query.filter_by(
        campaign_id=campaign.id, verification_status="Pending"
    ).count()

    if pending:
        flash(
            f"{pending} assets are still pending verification.",
            "warning",
        )
        return redirect(
            url_for(
                "asset_management.verification_campaign",
                campaign_id=campaign.id,
            )
        )

    campaign.status = "Under Review"
    db.session.commit()

    flash("Verification submitted for management review.", "success")
    return redirect(url_for("asset_management.verification_dashboard"))


# ============================================================================
# INSURANCE / WARRANTIES / CLAIMS
# ============================================================================

@asset_bp.route("/insurance")
@login_required
def insurance_dashboard():
    today = date.today()
    warning_date = today + timedelta(days=30)

    return render_template(
        "asset_management/insurance_dashboard.html",
        total_policies=AssetInsurance.query.count(),
        active_policies=AssetInsurance.query.filter_by(
            status="Active"
        ).count(),
        expired_policies=AssetInsurance.query.filter(
            AssetInsurance.expiry_date < today
        ).count(),
        expiring_policies=AssetInsurance.query.filter(
            AssetInsurance.expiry_date.between(today, warning_date),
            AssetInsurance.status == "Active",
        ).count(),
        total_claims=AssetInsuranceClaim.query.count(),
        pending_claims=AssetInsuranceClaim.query.filter(
            AssetInsuranceClaim.status.in_(
                ["Submitted", "Under Review"]
            )
        ).count(),
        policies=AssetInsurance.query.order_by(
            AssetInsurance.expiry_date
        )
        .limit(10)
        .all(),
    )


@asset_bp.route("/insurance/create", methods=["GET", "POST"])
@login_required
def create_asset_insurance():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        asset = Asset.query.get_or_404(asset_id)

        policy_number = request.form.get(
            "policy_number", ""
        ).strip()

        if AssetInsurance.query.filter_by(
            policy_number=policy_number
        ).first():
            flash("This policy number already exists.", "danger")
            return redirect(
                url_for("asset_management.create_asset_insurance")
            )

        start_date = date.fromisoformat(request.form["start_date"])
        expiry_date = date.fromisoformat(request.form["expiry_date"])

        if expiry_date <= start_date:
            flash(
                "Expiry date must be after the start date.", "danger"
            )
            return redirect(
                url_for("asset_management.create_asset_insurance")
            )

        insurance = AssetInsurance(
            asset_id=asset.id,
            policy_number=policy_number,
            insurance_company=request.form.get("insurance_company"),
            insurance_type=request.form.get("insurance_type"),
            coverage_amount=request.form.get(
                "coverage_amount", type=float
            ),
            premium_amount=request.form.get(
                "premium_amount", type=float
            )
            or 0,
            start_date=start_date,
            expiry_date=expiry_date,
            contact_person=request.form.get("contact_person"),
            contact_phone=request.form.get("contact_phone"),
            created_by=current_user.id,
            status="Active",
        )

        db.session.add(insurance)
        db.session.commit()

        flash(
            "Asset insurance policy registered successfully.", "success"
        )
        return redirect(url_for("asset_management.insurance_dashboard"))

    assets = Asset.query.order_by(Asset.name).all()

    return render_template(
        "asset_management/create_insurance.html", assets=assets
    )


@asset_bp.route("/warranties")
@login_required
def warranty_list():
    warranties = AssetWarranty.query.order_by(
        AssetWarranty.expiry_date
    ).all()

    return render_template(
        "asset_management/warranty_list.html", warranties=warranties
    )


@asset_bp.route("/warranties/create", methods=["GET", "POST"])
@login_required
def create_asset_warranty():
    if request.method == "POST":
        asset_id = request.form.get("asset_id", type=int)
        asset = Asset.query.get_or_404(asset_id)

        start_date = date.fromisoformat(request.form["start_date"])
        expiry_date = date.fromisoformat(request.form["expiry_date"])

        if expiry_date <= start_date:
            flash(
                "Warranty expiry must be after its start date.", "danger"
            )
            return redirect(
                url_for("asset_management.create_asset_warranty")
            )

        warranty = AssetWarranty(
            asset_id=asset.id,
            warranty_provider=request.form.get("warranty_provider"),
            warranty_reference=request.form.get(
                "warranty_reference"
            ),
            warranty_type=request.form.get("warranty_type"),
            start_date=start_date,
            expiry_date=expiry_date,
            coverage_details=request.form.get("coverage_details"),
            terms_and_conditions=request.form.get(
                "terms_and_conditions"
            ),
            status="Active",
        )

        db.session.add(warranty)
        db.session.commit()

        flash("Asset warranty registered successfully.", "success")
        return redirect(url_for("asset_management.warranty_list"))

    assets = Asset.query.order_by(Asset.name).all()

    return render_template(
        "asset_management/create_warranty.html", assets=assets
    )


@asset_bp.route(
    "/insurance/claims/create/<int:insurance_id>",
    methods=["GET", "POST"],
)
@login_required
def create_insurance_claim(insurance_id):
    insurance = AssetInsurance.query.get_or_404(insurance_id)

    if request.method == "POST":
        claim = AssetInsuranceClaim(
            insurance_id=insurance.id,
            claim_number=f"CLM-{datetime.now():%Y%m%d%H%M%S}",
            incident_date=date.fromisoformat(
                request.form["incident_date"]
            ),
            incident_type=request.form.get("incident_type"),
            incident_description=request.form.get(
                "incident_description"
            ),
            claimed_amount=request.form.get(
                "claimed_amount", type=float
            ),
            submitted_date=date.today(),
            status="Submitted",
            created_by=current_user.id,
        )

        db.session.add(claim)
        db.session.commit()

        flash("Insurance claim submitted successfully.", "success")
        return redirect(url_for("asset_management.insurance_dashboard"))

    return render_template(
        "asset_management/create_insurance_claim.html",
        insurance=insurance,
    )


# ============================================================================
# DOCUMENTS
# ============================================================================

@asset_bp.route(
    "/assets/<int:asset_id>/documents/upload",
    methods=["GET", "POST"],
)
@login_required
def upload_asset_document(asset_id):
    asset = Asset.query.get_or_404(asset_id)

    # Add asset.document.upload permission check here.

    if request.method == "POST":
        file = request.files.get("document")

        try:
            saved = save_asset_document(file, asset.id)

            document = AssetDocument(
                asset_id=asset.id,
                document_name=request.form.get("document_name"),
                document_type=request.form.get("document_type"),
                description=request.form.get("description"),
                original_filename=saved["original_filename"],
                stored_filename=saved["stored_filename"],
                file_path=saved["file_path"],
                file_extension=saved["file_extension"],
                file_size=saved["file_size"],
                version=1,
                is_confidential=(
                    request.form.get("is_confidential") == "on"
                ),
                uploaded_by=current_user.id,
            )

            db.session.add(document)
            db.session.commit()

            flash("Asset document uploaded successfully.", "success")
            return redirect(
                url_for(
                    "asset_management.asset_documents",
                    asset_id=asset.id,
                )
            )

        except ValueError as error:
            flash(str(error), "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Asset document upload failed"
            )
            flash("Unable to upload the document.", "danger")

    return render_template(
        "asset_management/upload_document.html", asset=asset
    )


@asset_bp.route("/assets/<int:asset_id>/documents")
@login_required
def asset_documents(asset_id):
    asset = Asset.query.get_or_404(asset_id)

    # Check asset.document.view permission and department access.

    documents = (
        AssetDocument.query.filter_by(
            asset_id=asset.id, status="Active"
        )
        .order_by(AssetDocument.created_at.desc())
        .all()
    )

    return render_template(
        "asset_management/asset_documents.html",
        asset=asset,
        documents=documents,
    )


@asset_bp.route("/documents/<int:document_id>/download")
@login_required
def download_asset_document(document_id):
    document = AssetDocument.query.get_or_404(document_id)

    # Check asset.document.download permission.
    # Also enforce department and confidentiality restrictions.

    if document.status != "Active":
        abort(404)

    if not os.path.isfile(document.file_path):
        abort(404)

    return send_file(
        document.file_path,
        as_attachment=True,
        download_name=document.original_filename,
    )


@asset_bp.route(
    "/documents/<int:document_id>/replace", methods=["POST"]
)
@login_required
def replace_asset_document(document_id):
    document = AssetDocument.query.get_or_404(document_id)

    # Check asset.document.replace permission.

    file = request.files.get("document")

    try:
        saved = save_asset_document(file, document.asset_id)

        old_version = AssetDocumentVersion(
            document_id=document.id,
            version_number=document.version,
            original_filename=document.original_filename,
            stored_filename=document.stored_filename,
            file_path=document.file_path,
            file_size=document.file_size,
            uploaded_by=document.uploaded_by,
        )

        db.session.add(old_version)

        document.version += 1
        document.original_filename = saved["original_filename"]
        document.stored_filename = saved["stored_filename"]
        document.file_path = saved["file_path"]
        document.file_size = saved["file_size"]
        document.file_extension = saved["file_extension"]
        document.uploaded_by = current_user.id
        document.created_at = datetime.utcnow()

        db.session.commit()

        flash("Document version updated successfully.", "success")

    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Asset document replacement failed"
        )
        flash("Unable to replace the document.", "danger")

    return redirect(
        url_for(
            "asset_management.asset_documents",
            asset_id=document.asset_id,
        )
    )


# ============================================================================
# APPROVALS
# ============================================================================

@asset_bp.route("/approvals")
@asset_permission_required("asset.approval.view")
def asset_approvals():
    """List pending approval requests assigned to the current user."""

    approvals = AssetApproval.query.filter_by(
        approver_id=current_user.id,
        action="Pending",
    ).all()

    return render_template(
        "asset_management/approvals.html",
        approvals=approvals,
    )


@asset_bp.route("/approvals/<int:approval_id>")
@asset_permission_required("asset.approval.view")
def approval_detail(approval_id):
    """Show a single approval request for review."""

    approval = AssetApproval.query.get_or_404(approval_id)

    return render_template(
        "asset_management/approval_detail.html",
        approval=approval,
    )