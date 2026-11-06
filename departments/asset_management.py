"""The asset desk: the fixed-asset register, its categories and who holds each unit.

A unit is raised once, tagged with a register-issued code, filed under a
category, and then allocated to a department or a named custodian. The desk reads
the HR register for departments and the user master data for custodians instead
of keeping a second staff list, and every write leaves one audit line with the
fields that actually moved.

The first pass covers the register itself: the dashboard, categories, the asset
list, register/view/edit, and department allocation with its return. The
maintenance, depreciation, disposal and verification folders follow on the same
tables rather than beside them.
"""

from core.access import current_user
from core.access import has_permission
from core.access import perm
from core.db import get_db
from datetime import date
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.asset import ALLOCATION_STATUSES
from services.asset import ASSET_CONDITIONS
from services.asset import ASSET_STATUSES
from services.asset import CATEGORY_STATUSES
from services.asset import allocate_asset
from services.asset import allocation_row
from services.asset import allocation_rows
from services.asset import allocation_values
from services.asset import asset_row
from services.asset import asset_rows
from services.asset import asset_summary
from services.asset import asset_values
from services.asset import assignable_accounts
from services.asset import category_breakdown
from services.asset import category_options
from services.asset import category_ref
from services.asset import category_row
from services.asset import category_rows
from services.asset import category_values
from services.asset import department_options
from services.asset import department_summary
from services.asset import insert_asset
from services.asset import insert_category
from services.asset import return_allocation
from services.asset import set_asset_status
from services.asset import set_category_status
from services.asset import status_breakdown
from services.asset import update_asset
from services.asset import update_category
from services.audit import change_set
from services.audit import log_activity
from services.procurement import purchase_order_ref


asset_bp = Blueprint("asset", __name__, url_prefix="/assets")


@asset_bp.context_processor
def asset_template_context():
    """The vocabularies every asset screen draws its selects from."""
    return {
        "asset_statuses": ASSET_STATUSES,
        "asset_conditions": ASSET_CONDITIONS,
        "category_statuses": CATEGORY_STATUSES,
        "allocation_statuses": ALLOCATION_STATUSES,
    }


# ------------------------------------------------------------------- helpers
def guarded_asset(db, asset_id):
    row = asset_row(db, asset_id)
    if row is None:
        abort(404)
    return row


def guarded_category(db, category_id):
    row = category_row(db, category_id)
    if row is None:
        abort(404)
    return row


def active_allocation(db, asset_id):
    rows = allocation_rows(db, asset_id=asset_id, status="Active", limit=1)
    return rows[0] if rows else None


# ---------------------------------------------------------------- dashboard
@asset_bp.route("/")
@perm("asset_dashboard")
def dashboard():
    """The desk at a glance: what the company owns and where it sits."""
    db = get_db()
    return render_template(
        "asset/dashboard.html",
        summary=asset_summary(db),
        breakdown=status_breakdown(db),
        categories=category_breakdown(db, 6),
        departments=department_summary(db),
        recent=asset_rows(db, limit=8),
        movements=allocation_rows(db, limit=6),
        can_register=has_permission("asset_register"),
        can_allocate=has_permission("asset_allocation"),
        can_categories=has_permission("asset_categories"))


# ------------------------------------------------------------------- register
@asset_bp.route("/register")
@perm("asset_register", "asset_allocation", "asset_dashboard")
def assets():
    """The register. The allocation screen reads the same rows with a filter."""
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in ASSET_STATUSES:
        status = ""
    category = request.args.get("category") or ""
    department = request.args.get("department") or ""
    search = (request.args.get("search") or "").strip()
    # A deactivated unit is history; only the register right may list it again.
    include_inactive = (request.args.get("inactive") == "1"
                        and has_permission("asset_register"))
    rows = asset_rows(db, status or None,
                      int(category) if category.isdigit() else None,
                      int(department) if department.isdigit() else None,
                      search or None,
                      include_inactive=include_inactive)
    return render_template(
        "asset/assets.html",
        assets=rows, status=status, category=category,
        department=department, search=search,
        include_inactive=include_inactive,
        categories=category_options(db, include_inactive=True),
        departments=department_options(db),
        can_register=has_permission("asset_register"),
        can_allocate=has_permission("asset_allocation"))


@asset_bp.route("/register/new", methods=["GET", "POST"])
@perm("asset_register")
def create_asset():
    db = get_db()
    if request.method == "POST":
        try:
            values = asset_values(request.form, db)
            purchase_order = purchase_order_ref(
                db, request.form.get("procurement_order_id"))
            values["procurement_order_id"] = (
                purchase_order["id"] if purchase_order else None)
            asset_id = insert_asset(db, values, current_user())
            db.commit()
            log_activity(
                "CREATE", "asset",
                "Asset '{}' registered ({})".format(values["asset_code"],
                                                   values["name"]),
                record_type="asset", record_id=asset_id, updated=values)
            flash("Asset {} registered.".format(values["asset_code"]), "success")
            return redirect(url_for("asset.asset_detail", asset_id=asset_id))
        except ValueError as error:
            db.rollback()
            flash(str(error), "error")
    # A unit bought on a purchase order arrives with the order, supplier and
    # price already answered; the name and category stay the registrar's call.
    source = request.form if request.method == "POST" else request.args
    try:
        purchase_cost = float(source.get("purchase_cost") or 0)
    except (TypeError, ValueError):
        purchase_cost = 0
    draft = {
        "name": (source.get("name") or "").strip()[:180],
        "supplier": (source.get("supplier") or "").strip()[:150],
        "purchase_order_number": (source.get("purchase_order_number")
                                  or "").strip()[:100],
        "procurement_order_id": (
            source.get("procurement_order_id") or "").strip()
        if (source.get("procurement_order_id") or "").strip().isdigit()
        else "",
        "purchase_cost": purchase_cost,
        "description": (source.get("description") or "").strip()[:2000],
    }
    return render_template(
        "asset/asset_form.html", asset=None, draft=draft,
        categories=category_options(db))


@asset_bp.route("/register/<int:asset_id>")
@perm("asset_register", "asset_allocation", "asset_dashboard")
def asset_detail(asset_id):
    db = get_db()
    asset = guarded_asset(db, asset_id)
    purchase_order = None
    if asset["procurement_order_id"]:
        purchase_order = db.execute(
            "SELECT id, order_number FROM finance.procurement_orders WHERE id = ?",
            (asset["procurement_order_id"],)).fetchone()
    return render_template(
        "asset/asset_detail.html",
        asset=asset,
        allocation=active_allocation(db, asset_id),
        history=allocation_rows(db, asset_id=asset_id),
        purchase_order=purchase_order,
        today=date.today().isoformat(),
        can_register=has_permission("asset_register"),
        can_allocate=has_permission("asset_allocation"))


@asset_bp.route("/register/<int:asset_id>/edit", methods=["GET", "POST"])
@perm("asset_register")
def edit_asset(asset_id):
    db = get_db()
    asset = guarded_asset(db, asset_id)
    if request.method == "POST":
        try:
            values = asset_values(request.form, db, exclude_id=asset_id)
            values.pop("asset_code", None)
            update_asset(db, asset_id, values, current_user())
            db.commit()
            previous, updated = change_set(asset, values)
            log_activity("UPDATE", "asset",
                         "Asset '{}' updated".format(asset["asset_code"]),
                         record_type="asset", record_id=asset_id,
                         previous=previous, updated=updated)
            flash("Asset updated.", "success")
            return redirect(url_for("asset.asset_detail", asset_id=asset_id))
        except ValueError as error:
            db.rollback()
            flash(str(error), "error")
    return render_template(
        "asset/asset_form.html", asset=asset,
        categories=category_options(db))


@asset_bp.route("/register/<int:asset_id>/deactivate", methods=["POST"])
@perm("asset_register")
def deactivate_asset(asset_id):
    db = get_db()
    asset = guarded_asset(db, asset_id)
    if asset["status"] == "Inactive":
        flash("That asset is already deactivated.", "info")
        return redirect(url_for("asset.asset_detail", asset_id=asset_id))
    if active_allocation(db, asset_id) is not None:
        flash("Return the allocated asset before deactivating it.", "error")
        return redirect(url_for("asset.asset_detail", asset_id=asset_id))
    set_asset_status(db, asset_id, "Inactive", current_user())
    db.commit()
    log_activity("UPDATE", "asset",
                 "Asset '{}' deactivated".format(asset["asset_code"]),
                 record_type="asset", record_id=asset_id,
                 previous={"status": asset["status"]},
                 updated={"status": "Inactive"})
    flash("Asset deactivated.", "success")
    return redirect(url_for("asset.asset_detail", asset_id=asset_id))


@asset_bp.route("/register/<int:asset_id>/reactivate", methods=["POST"])
@perm("asset_register")
def reactivate_asset(asset_id):
    db = get_db()
    asset = guarded_asset(db, asset_id)
    if asset["status"] != "Inactive":
        flash("That asset is already on the live register.", "info")
        return redirect(url_for("asset.asset_detail", asset_id=asset_id))
    set_asset_status(db, asset_id, "Available", current_user())
    db.commit()
    log_activity("UPDATE", "asset",
                 "Asset '{}' returned to the register".format(
                     asset["asset_code"]),
                 record_type="asset", record_id=asset_id,
                 previous={"status": "Inactive"},
                 updated={"status": "Available"})
    flash("Asset returned to the live register.", "success")
    return redirect(url_for("asset.asset_detail", asset_id=asset_id))


# ----------------------------------------------------------------- categories
@asset_bp.route("/categories")
@perm("asset_categories", "asset_register")
def categories():
    db = get_db()
    status = (request.args.get("status") or "").strip()
    if status and status not in CATEGORY_STATUSES:
        status = ""
    search = (request.args.get("search") or "").strip()
    return render_template(
        "asset/categories.html",
        categories=category_rows(db, status or None, search or None),
        status=status, search=search,
        can_categories=has_permission("asset_categories"),
        can_register=has_permission("asset_register"))


@asset_bp.route("/categories/new", methods=["GET", "POST"])
@perm("asset_categories")
def create_category():
    db = get_db()
    if request.method == "POST":
        try:
            values = category_values(request.form, db)
            category_id = insert_category(db, values, current_user())
            db.commit()
            log_activity(
                "CREATE", "asset",
                "Asset category '{}' created".format(values["name"]),
                record_type="asset_category", record_id=category_id,
                updated=values)
            flash("Asset category created.", "success")
            return redirect(url_for("asset.categories"))
        except ValueError as error:
            db.rollback()
            flash(str(error), "error")
    return render_template(
        "asset/category_form.html", category=None,
        parents=category_options(db))


@asset_bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@perm("asset_categories")
def edit_category(category_id):
    db = get_db()
    category = guarded_category(db, category_id)
    if request.method == "POST":
        try:
            values = category_values(request.form, db, exclude_id=category_id)
            update_category(db, category_id, values, current_user())
            db.commit()
            previous, updated = change_set(category, values)
            log_activity("UPDATE", "asset",
                         "Asset category '{}' updated".format(values["name"]),
                         record_type="asset_category", record_id=category_id,
                         previous=previous, updated=updated)
            flash("Asset category updated.", "success")
            return redirect(url_for("asset.categories"))
        except ValueError as error:
            db.rollback()
            flash(str(error), "error")
    return render_template(
        "asset/category_form.html", category=category,
        parents=category_options(db, exclude_id=category_id))


@asset_bp.route("/categories/<int:category_id>/toggle", methods=["POST"])
@perm("asset_categories")
def toggle_category(category_id):
    db = get_db()
    category = guarded_category(db, category_id)
    if category["status"] == "Active":
        child = db.execute(
            "SELECT name FROM asset_categories WHERE parent_id = ?"
            " AND status = 'Active' LIMIT 1", (category_id,)).fetchone()
        if child is not None:
            flash("Deactivate {} before its parent.".format(child["name"]),
                  "error")
            return redirect(url_for("asset.categories"))
        status = "Inactive"
        message = "Asset category deactivated."
    else:
        if category["parent_id"]:
            try:
                category_ref(db, category["parent_id"], require_active=True)
            except ValueError:
                flash("Activate the parent category first.", "error")
                return redirect(url_for("asset.categories"))
        status = "Active"
        message = "Asset category activated."
    set_category_status(db, category_id, status, current_user())
    db.commit()
    log_activity("UPDATE", "asset",
                 "Asset category '{}' {}".format(
                     category["name"], "activated" if status == "Active"
                     else "deactivated"),
                 record_type="asset_category", record_id=category_id,
                 previous={"status": category["status"]},
                 updated={"status": status})
    flash(message, "success")
    return redirect(url_for("asset.categories"))


# -------------------------------------------------------------- allocations
@asset_bp.route("/departments")
@perm("asset_allocation", "asset_register", "asset_dashboard")
def departments():
    db = get_db()
    rows = department_summary(db)
    return render_template(
        "asset/departments.html",
        departments=rows,
        units=sum(row["total"] for row in rows),
        value=sum(float(row["value"] or 0) for row in rows),
        can_allocate=has_permission("asset_allocation"))


@asset_bp.route("/departments/<int:department_id>")
@perm("asset_allocation", "asset_register", "asset_dashboard")
def department_assets(department_id):
    db = get_db()
    name = next((row["name"] for row in department_options(db)
                 if row["id"] == department_id), "")
    rows = asset_rows(db, department_id=department_id)
    return render_template(
        "asset/department_assets.html",
        department_id=department_id,
        department_name=name or "Department",
        assets=rows,
        movements=allocation_rows(db, department_id=department_id, limit=20),
        can_allocate=has_permission("asset_allocation"))


@asset_bp.route("/departments/allocate", methods=["GET", "POST"])
@perm("asset_allocation")
def allocate():
    db = get_db()
    if request.method == "POST":
        try:
            values = allocation_values(request.form, db)
            allocation_id = allocate_asset(db, values, current_user())
            db.commit()
            asset = values["asset"]
            log_activity(
                "CREATE", "asset",
                "Asset '{}' allocated to {}{}".format(
                    asset["asset_code"], values["department"]["name"],
                    " for {}".format(values["custodian"]["full_name"])
                    if values["custodian"] else ""),
                record_type="asset", record_id=asset["id"],
                details={"allocation_id": allocation_id})
            flash("Asset {} allocated to {}.".format(
                asset["asset_code"], values["department"]["name"]), "success")
            return redirect(url_for("asset.asset_detail",
                                    asset_id=asset["id"]))
        except ValueError as error:
            db.rollback()
            flash(str(error), "error")
    return render_template(
        "asset/allocate.html",
        available_assets=asset_rows(db, status="Available"),
        departments=department_options(db),
        accounts=assignable_accounts(db),
        selected_asset=request.args.get("asset_id", type=int))


@asset_bp.route("/allocations/<int:allocation_id>/return", methods=["POST"])
@perm("asset_allocation")
def return_asset(allocation_id):
    db = get_db()
    allocation = allocation_row(db, allocation_id)
    if allocation is None:
        abort(404)
    try:
        asset_id = return_allocation(db, allocation_id, current_user())
    except ValueError as error:
        db.rollback()
        flash(str(error), "error")
        return redirect(url_for("asset.asset_detail",
                                asset_id=allocation["asset_id"]))
    db.commit()
    log_activity(
        "RETURN", "asset",
        "Asset '{}' returned from {}".format(
            allocation["asset_code"], allocation["department_name"]),
        record_type="asset", record_id=asset_id,
        details={"allocation_id": allocation_id})
    flash("Asset returned to the available pool.", "success")
    return redirect(url_for("asset.asset_detail", asset_id=asset_id))


def register(app):
    app.register_blueprint(asset_bp)
