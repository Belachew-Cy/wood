import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import (
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from modules.inventory import inventory_bp
from modules.inventory.permissions import (
    inventory_permission_required,
)
from modules.inventory.models import (
    InventoryItem,
    Warehouse,
    StockBalance,
    StockMovement,
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestApproval,
    MaterialIssue,
    StockAdjustment,
    StockAdjustmentApproval,
    InventoryStockAlert,
    StocktakeSession,
    StocktakeLine,
)
from modules.inventory.services import (
    apply_stock_adjustment,
    check_warehouse_stock,
    issue_materials,
    record_stock_movement,
    resolve_stock_alert,
    transfer_stock,
)
from modules.inventory.report_service import (
    get_inventory_summary,
    get_stock_movement_report,
    get_stock_report,
    get_stock_valuation,
)
from modules.inventory.stocktake_service import (
    add_stocktake_count,
    calculate_stocktake_variances,
    create_stocktake,
    create_stocktake_adjustments,
)
from modules.inventory.barcode_service import (
    generate_barcode_image,
    generate_qr_image,
)


# ============================================================================
# DASHBOARD
# ============================================================================

@inventory_bp.route("/")
@inventory_permission_required("inventory.dashboard.view")
def dashboard():
    summary = get_inventory_summary()
    recent = get_stock_movement_report()[:8]

    return render_template(
        "inventory/dashboard.html",
        summary=summary,
        recent=recent,
    )


# ============================================================================
# ITEMS
# ============================================================================

@inventory_bp.route("/items")
@inventory_permission_required("inventory.item.view")
def items():
    search = request.args.get("q", "").strip()
    query = InventoryItem.query.filter_by(status="Active")

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                InventoryItem.sku.ilike(like),
                InventoryItem.name.ilike(like),
                InventoryItem.category.ilike(like),
            )
        )

    rows = query.order_by(InventoryItem.name).all()

    return render_template(
        "inventory/items.html",
        items=rows,
        search=search,
    )


@inventory_bp.route("/items/new", methods=["GET", "POST"])
@inventory_permission_required("inventory.item.create")
def new_item():
    if request.method == "POST":

        sku = request.form.get("sku", "").strip().upper()
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        unit = request.form.get("unit", "").strip()
        supplier = request.form.get("supplier", "").strip()
        location = request.form.get("location", "").strip()

        if not all([sku, name, category, unit]):
            flash("Complete every required field.", "danger")
            return redirect(url_for("inventory.new_item"))

        try:
            min_stock = Decimal(
                request.form.get("min_stock", "0") or "0"
            )
            unit_cost = Decimal(
                request.form.get("unit_cost", "0") or "0"
            )
            opening = Decimal(
                request.form.get("opening_qty", "0") or "0"
            )

            if (
                not min_stock.is_finite()
                or not unit_cost.is_finite()
                or not opening.is_finite()
                or min_stock < 0
                or unit_cost < 0
                or opening < 0
            ):
                raise ValueError

        except (InvalidOperation, ValueError):
            flash("Numbers must be valid and non-negative.", "danger")
            return redirect(url_for("inventory.new_item"))

        if InventoryItem.query.filter_by(sku=sku).first():
            flash("That SKU is already registered.", "danger")
            return redirect(url_for("inventory.new_item"))

        item = InventoryItem(
            sku=sku,
            name=name,
            category=category,
            unit=unit,
            min_stock=min_stock,
            unit_cost=unit_cost,
            supplier=supplier,
            location=location,
            status="Active",
        )

        try:
            db.session.add(item)
            db.session.commit()
            flash("Inventory item created.", "success")
            return redirect(url_for("inventory.items"))

        except Exception:
            db.session.rollback()
            flash("Unable to create item.", "danger")

    return render_template(
        "inventory/item_form.html",
        item=None,
        categories=INVENTORY_CATEGORIES,
        units=INVENTORY_UNITS,
    )


@inventory_bp.route(
    "/items/<int:item_id>/edit", methods=["GET", "POST"]
)
@inventory_permission_required("inventory.item.edit")
def edit_item(item_id):
    item = db.session.get(InventoryItem, item_id)

    if not item:
        abort(404)

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        unit = request.form.get("unit", "").strip()

        if not all([name, category, unit]):
            flash("Complete every required field.", "danger")
            return redirect(
                url_for("inventory.edit_item", item_id=item.id)
            )

        try:
            min_stock = Decimal(
                request.form.get("min_stock", "0") or "0"
            )
            unit_cost = Decimal(
                request.form.get("unit_cost", "0") or "0"
            )

            if min_stock < 0 or unit_cost < 0:
                raise ValueError

        except (InvalidOperation, ValueError):
            flash("Numbers must be non-negative.", "danger")
            return redirect(
                url_for("inventory.edit_item", item_id=item.id)
            )

        item.name = name
        item.category = category
        item.unit = unit
        item.min_stock = min_stock
        item.unit_cost = unit_cost
        item.supplier = request.form.get("supplier", "").strip()
        item.location = request.form.get("location", "").strip()

        db.session.commit()
        flash("Item updated.", "success")
        return redirect(url_for("inventory.items"))

    return render_template(
        "inventory/item_form.html",
        item=item,
        categories=INVENTORY_CATEGORIES,
        units=INVENTORY_UNITS,
    )


@inventory_bp.route(
    "/items/<int:item_id>/deactivate", methods=["POST"]
)
@inventory_permission_required("inventory.item.deactivate")
def deactivate_item(item_id):
    item = db.session.get(InventoryItem, item_id)

    if not item:
        abort(404)

    item.status = "Inactive"
    db.session.commit()

    flash("Item deactivated.", "success")
    return redirect(url_for("inventory.items"))


# ============================================================================
# STOCK MOVEMENTS
# ============================================================================

@inventory_bp.route("/movement", methods=["GET", "POST"])
@login_required
def stock_movement():

    if request.method == "POST":

        movement_type = request.form.get("movement_type")

        if movement_type == "IN":
            required_permission = "inventory.stock.receive"
        elif movement_type == "OUT":
            required_permission = "inventory.stock.issue"
        else:
            abort(400, description="Invalid movement type.")

        if not current_user.has_permission(required_permission):
            abort(403)

        try:
            record_stock_movement(
                item_id=int(request.form["item_id"]),
                warehouse_id=int(request.form["warehouse_id"]),
                movement_type=movement_type,
                quantity=request.form["quantity"],
                user_id=current_user.id,
                reference_type=request.form.get("reference_type"),
                reference_no=request.form.get("reference_no"),
                remarks=request.form.get("remarks"),
            )
            db.session.commit()

            flash("Stock movement recorded.", "success")
            return redirect(url_for("inventory.history"))

        except (ValueError, KeyError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Stock movement failed"
            )
            flash("Stock movement failed.", "danger")

    return render_template(
        "inventory/stock_movement.html",
        items=InventoryItem.query.filter_by(
            status="Active"
        ).order_by(InventoryItem.name).all(),
        warehouses=Warehouse.query.filter_by(
            status="Active"
        ).order_by(Warehouse.warehouse_name).all(),
        references=INVENTORY_REFERENCES,
    )


@inventory_bp.route("/history")
@inventory_permission_required("inventory.history.view")
def history():
    movements = get_stock_movement_report()
    return render_template(
        "inventory/history.html", movements=movements
    )


# ============================================================================
# WAREHOUSES
# ============================================================================

@inventory_bp.route("/warehouses", methods=["GET", "POST"])
@inventory_permission_required("inventory.warehouse.view")
def warehouses():

    if request.method == "POST":

        if not current_user.has_permission(
            "inventory.warehouse.create"
        ):
            abort(403)

        code = request.form.get(
            "warehouse_code", ""
        ).strip()
        name = request.form.get(
            "warehouse_name", ""
        ).strip()
        location = request.form.get("location", "").strip()

        if not code or not name:
            flash("Warehouse code and name are required.", "danger")

        elif Warehouse.query.filter_by(
            warehouse_code=code
        ).first():
            flash("Warehouse code already exists.", "danger")

        else:
            warehouse = Warehouse(
                warehouse_code=code,
                warehouse_name=name,
                location=location,
                status="Active",
            )

            try:
                db.session.add(warehouse)
                db.session.commit()
                flash("Warehouse created.", "success")
                return redirect(url_for("inventory.warehouses"))

            except Exception:
                db.session.rollback()
                flash("Unable to create warehouse.", "danger")

    return render_template(
        "inventory/warehouse.html",
        warehouses=Warehouse.query.order_by(
            Warehouse.warehouse_name
        ).all(),
    )


# ============================================================================
# WAREHOUSE TRANSFER
# ============================================================================

@inventory_bp.route("/transfer", methods=["GET", "POST"])
@inventory_permission_required("inventory.stock.transfer")
def warehouse_transfer():

    if request.method == "POST":

        try:
            transfer_stock(
                item_id=int(request.form["item_id"]),
                source_warehouse_id=int(
                    request.form["source_warehouse_id"]
                ),
                destination_warehouse_id=int(
                    request.form["destination_warehouse_id"]
                ),
                quantity=request.form["quantity"],
                user_id=current_user.id,
                reference_no=request.form.get("reference_no"),
                remarks=request.form.get("remarks"),
            )
            flash("Warehouse transfer completed.", "success")
            return redirect(url_for("inventory.history"))

        except (ValueError, KeyError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Warehouse transfer failed"
            )
            flash("Warehouse transfer failed.", "danger")

    return render_template(
        "inventory/transfer.html",
        items=InventoryItem.query.filter_by(
            status="Active"
        ).all(),
        warehouses=Warehouse.query.filter_by(
            status="Active"
        ).all(),
    )


# ============================================================================
# MATERIAL REQUESTS
# ============================================================================

@inventory_bp.route("/material-requests", methods=["GET"])
@inventory_permission_required("inventory.request.view")
def material_requests():
    requests = (
        MaterialRequest.query
        .order_by(MaterialRequest.created_at.desc())
        .all()
    )
    return render_template(
        "inventory/material_requests/dashboard.html",
        requests=requests,
    )


@inventory_bp.route(
    "/material-requests/create", methods=["GET", "POST"]
)
@inventory_permission_required("inventory.request.create")
def create_material_request():

    if request.method == "POST":

        purpose = request.form.get("purpose", "").strip()

        if not purpose:
            flash("Please provide the request purpose.", "danger")
            return redirect(
                url_for("inventory.create_material_request")
            )

        material_request = MaterialRequest(
            request_number=(
                "MR-" + datetime.utcnow().strftime(
                    "%Y%m%d%H%M%S%f"
                )
            ),
            department_id=current_user.department_id,
            requested_by=current_user.id,
            purpose=purpose,
            status="DRAFT",
        )

        try:
            db.session.add(material_request)
            db.session.flush()

            item_ids = request.form.getlist("item_id[]")
            warehouse_ids = request.form.getlist(
                "warehouse_id[]"
            )
            quantities = request.form.getlist("quantity[]")

            if not item_ids or not (
                len(item_ids)
                == len(warehouse_ids)
                == len(quantities)
            ):
                raise ValueError("Invalid request item details.")

            for item_id, warehouse_id, quantity in zip(
                item_ids, warehouse_ids, quantities
            ):
                amount = Decimal(quantity)

                if amount <= 0:
                    raise ValueError(
                        "Quantity must be greater than zero."
                    )

                material_request.items.append(
                    MaterialRequestItem(
                        item_id=int(item_id),
                        warehouse_id=int(warehouse_id),
                        requested_quantity=amount,
                    )
                )

            db.session.commit()
            flash("Material request created.", "success")
            return redirect(
                url_for("inventory.material_requests")
            )

        except (ValueError, InvalidOperation, SQLAlchemyError):
            db.session.rollback()
            current_app.logger.exception(
                "Material request creation failed"
            )
            flash(
                "Unable to create request. Check the supplied details.",
                "danger",
            )

    return render_template(
        "inventory/material_requests/create.html"
    )


@inventory_bp.route(
    "/material-requests/<int:request_id>/submit",
    methods=["POST"],
)
@inventory_permission_required("inventory.request.submit")
def submit_material_request(request_id):

    material_request = db.session.get(
        MaterialRequest, request_id
    )

    if not material_request:
        abort(404)

    if material_request.requested_by != current_user.id:
        abort(403)

    if material_request.status != "DRAFT":
        flash("Only draft requests can be submitted.", "warning")
        return redirect(url_for("inventory.material_requests"))

    if not material_request.items:
        flash(
            "Add at least one material before submitting.",
            "danger",
        )
        return redirect(url_for("inventory.material_requests"))

    material_request.status = "PENDING_APPROVAL"

    db.session.add(
        MaterialRequestApproval(
            request_id=material_request.id,
            action="SUBMITTED",
            action_by=current_user.id,
            comments="Material request submitted for approval.",
        )
    )

    db.session.commit()

    flash("Material request submitted.", "success")
    return redirect(url_for("inventory.material_requests"))


@inventory_bp.route(
    "/material-requests/<int:request_id>/approve",
    methods=["POST"],
)
@inventory_permission_required("inventory.request.approve")
def approve_material_request(request_id):

    material_request = db.session.get(
        MaterialRequest, request_id
    )

    if not material_request:
        abort(404)

    if material_request.status != "PENDING_APPROVAL":
        flash("This request is not awaiting approval.", "warning")
        return redirect(url_for("inventory.material_requests"))

    if material_request.requested_by == current_user.id:
        abort(403)

    try:
        for item in material_request.items:
            approved_value = request.form.get(
                f"approved_quantity_{item.id}",
                str(item.requested_quantity),
            )
            approved_quantity = Decimal(approved_value)

            if (
                not approved_quantity.is_finite()
                or approved_quantity < 0
                or approved_quantity > item.requested_quantity
            ):
                raise ValueError("Invalid approved quantity.")

            item.approved_quantity = approved_quantity

        if not any(
            item.approved_quantity > 0
            for item in material_request.items
        ):
            raise ValueError(
                "At least one material must be approved."
            )

        material_request.status = "APPROVED"
        material_request.approved_by = current_user.id
        material_request.approved_at = datetime.utcnow()

        db.session.add(
            MaterialRequestApproval(
                request_id=material_request.id,
                action="APPROVED",
                action_by=current_user.id,
                comments=request.form.get(
                    "comments", ""
                ).strip(),
            )
        )

        db.session.commit()

    except (ValueError, InvalidOperation):
        db.session.rollback()
        flash("Invalid approved quantities.", "danger")
        return redirect(url_for("inventory.material_requests"))

    flash("Material request approved.", "success")
    return redirect(url_for("inventory.material_requests"))


@inventory_bp.route(
    "/material-requests/<int:request_id>/reject",
    methods=["POST"],
)
@inventory_permission_required("inventory.request.reject")
def reject_material_request(request_id):

    material_request = db.session.get(
        MaterialRequest, request_id
    )

    if not material_request:
        abort(404)

    if material_request.status != "PENDING_APPROVAL":
        abort(400)

    if material_request.requested_by == current_user.id:
        abort(403)

    reason = request.form.get(
        "rejection_reason", ""
    ).strip()

    if not reason:
        flash("A rejection reason is required.", "danger")
        return redirect(url_for("inventory.material_requests"))

    material_request.status = "REJECTED"
    material_request.rejected_by = current_user.id
    material_request.rejection_reason = reason

    db.session.add(
        MaterialRequestApproval(
            request_id=material_request.id,
            action="REJECTED",
            action_by=current_user.id,
            comments=reason,
        )
    )

    db.session.commit()

    flash("Material request rejected.", "warning")
    return redirect(url_for("inventory.material_requests"))


@inventory_bp.route("/material-requests/approvals")
@inventory_permission_required("inventory.request.approve")
def material_request_approvals():

    requests = (
        MaterialRequest.query
        .filter_by(status="PENDING_APPROVAL")
        .order_by(MaterialRequest.created_at.asc())
        .all()
    )

    return render_template(
        "inventory/material_requests/approvals.html",
        requests=requests,
    )


# ============================================================================
# MATERIAL ISSUES
# ============================================================================

@inventory_bp.route(
    "/material-requests/<int:request_id>/issue",
    methods=["GET", "POST"],
)
@inventory_permission_required("inventory.issue.create")
def issue_material_request(request_id):

    material_request = db.session.get(
        MaterialRequest, request_id
    )

    if not material_request:
        abort(404)

    if material_request.status not in (
        "APPROVED",
        "PARTIALLY_ISSUED",
    ):
        abort(400, description="Request is not ready for issuing.")

    if request.method == "POST":

        quantities = {}

        try:
            for item in material_request.items:
                raw = request.form.get(
                    f"issue_quantity_{item.id}", "0"
                )
                quantity = Decimal(raw)

                if not quantity.is_finite() or quantity < 0:
                    raise ValueError(
                        "Invalid issue quantity."
                    )

                if quantity > 0:
                    quantities[item.id] = quantity

            if not quantities:
                raise ValueError(
                    "Enter at least one issue quantity."
                )

            issue = issue_materials(
                request_id=material_request.id,
                issued_by=current_user.id,
                receiver_id=material_request.requested_by,
                quantities=quantities,
                remarks=request.form.get(
                    "remarks", ""
                ).strip(),
            )

            db.session.commit()

            flash(
                f"Materials issued. Issue Note: "
                f"{issue.issue_number}",
                "success",
            )

            return redirect(
                url_for(
                    "inventory.material_issue_details",
                    issue_id=issue.id,
                )
            )

        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Material issuing failed"
            )
            flash(
                "Unable to issue materials. "
                "Please check stock and try again.",
                "danger",
            )

    return render_template(
        "inventory/material_requests/issue.html",
        material_request=material_request,
    )


@inventory_bp.route("/material-issues/<int:issue_id>")
@inventory_permission_required("inventory.issue.view")
def material_issue_details(issue_id):

    issue = db.session.get(MaterialIssue, issue_id)

    if not issue:
        abort(404)

    return render_template(
        "inventory/material_requests/issue_details.html",
        issue=issue,
    )


@inventory_bp.route("/material-issues/pending")
@inventory_permission_required("inventory.issue.view")
def pending_material_issues():

    requests = (
        MaterialRequest.query
        .filter(
            MaterialRequest.status.in_(
                ["APPROVED", "PARTIALLY_ISSUED"]
            )
        )
        .order_by(MaterialRequest.created_at.asc())
        .all()
    )

    return render_template(
        "inventory/material_requests/pending_issues.html",
        requests=requests,
    )


# ============================================================================
# STOCK ADJUSTMENTS
# ============================================================================

@inventory_bp.route(
    "/adjustments/create", methods=["GET", "POST"]
)
@inventory_permission_required("inventory.adjustment.create")
def create_stock_adjustment():

    if request.method == "POST":

        try:
            item_id = int(request.form["item_id"])
            warehouse_id = int(request.form["warehouse_id"])

            item = db.session.get(InventoryItem, item_id)
            warehouse = db.session.get(Warehouse, warehouse_id)

            if not item or not warehouse:
                raise ValueError("Invalid item or warehouse.")

            balance = StockBalance.query.filter_by(
                item_id=item_id,
                warehouse_id=warehouse_id,
            ).first()

            system_quantity = Decimal(
                str(balance.quantity if balance else 0)
            )
            physical_quantity = Decimal(
                request.form["physical_quantity"]
            )

            if not physical_quantity.is_finite():
                raise ValueError("Invalid physical quantity.")

            if physical_quantity < 0:
                raise ValueError(
                    "Physical quantity cannot be negative."
                )

            reason = request.form.get("reason", "").strip()

            if not reason:
                raise ValueError("Adjustment reason is required.")

            adjustment = StockAdjustment(
                adjustment_number=(
                    f"ADJ-{datetime.utcnow():%Y%m%d}-"
                    f"{uuid4().hex[:8].upper()}"
                ),
                item_id=item_id,
                warehouse_id=warehouse_id,
                adjustment_type=request.form["adjustment_type"],
                system_quantity=system_quantity,
                physical_quantity=physical_quantity,
                adjustment_quantity=(
                    physical_quantity - system_quantity
                ),
                reason=reason,
                requested_by=current_user.id,
                status="DRAFT",
            )

            db.session.add(adjustment)
            db.session.commit()

            flash("Stock adjustment request created.", "success")
            return redirect(
                url_for("inventory.stock_adjustments")
            )

        except (ValueError, KeyError, TypeError):
            db.session.rollback()
            flash(
                "Please provide valid adjustment information.",
                "danger",
            )

    return render_template(
        "inventory/adjustments/create.html"
    )


@inventory_bp.route(
    "/adjustments/<int:adjustment_id>/submit",
    methods=["POST"],
)
@inventory_permission_required("inventory.adjustment.submit")
def submit_stock_adjustment(adjustment_id):

    adjustment = db.session.get(StockAdjustment, adjustment_id)

    if not adjustment:
        abort(404)

    if adjustment.status != "DRAFT":
        abort(400)

    if adjustment.requested_by != current_user.id:
        abort(403)

    adjustment.status = "PENDING_APPROVAL"

    db.session.add(
        StockAdjustmentApproval(
            adjustment_id=adjustment.id,
            action="SUBMITTED",
            action_by=current_user.id,
            comments="Submitted for approval.",
        )
    )

    db.session.commit()

    flash("Adjustment submitted for approval.", "success")
    return redirect(url_for("inventory.stock_adjustments"))


@inventory_bp.route(
    "/adjustments/<int:adjustment_id>/decision",
    methods=["POST"],
)
@login_required
def decide_stock_adjustment(adjustment_id):

    action = request.form.get("action")

    if action == "APPROVE":
        if not current_user.has_permission(
            "inventory.adjustment.approve"
        ):
            abort(403)
    elif action == "REJECT":
        if not current_user.has_permission(
            "inventory.adjustment.reject"
        ):
            abort(403)
    else:
        abort(400)

    adjustment = db.session.get(StockAdjustment, adjustment_id)

    if not adjustment:
        abort(404)

    if adjustment.status != "PENDING_APPROVAL":
        abort(400)

    if adjustment.requested_by == current_user.id:
        abort(403)

    comments = request.form.get("comments", "").strip()

    if action == "REJECT" and not comments:
        flash("Rejection reason is required.", "danger")
        return redirect(url_for("inventory.stock_adjustments"))

    if action == "APPROVE":
        adjustment.status = "APPROVED"
        adjustment.approved_by = current_user.id
        adjustment.approved_at = datetime.utcnow()
    else:
        adjustment.status = "REJECTED"

    db.session.add(
        StockAdjustmentApproval(
            adjustment_id=adjustment.id,
            action=action,
            action_by=current_user.id,
            comments=comments,
        )
    )

    db.session.commit()

    flash(f"Adjustment {action.lower()}d successfully.", "success")
    return redirect(url_for("inventory.stock_adjustments"))


@inventory_bp.route(
    "/adjustments/<int:adjustment_id>/apply",
    methods=["POST"],
)
@inventory_permission_required("inventory.adjustment.apply")
def apply_adjustment_route(adjustment_id):

    try:
        adjustment = apply_stock_adjustment(
            adjustment_id=adjustment_id,
            applied_by=current_user.id,
        )
        db.session.commit()

        flash(
            f"Adjustment {adjustment.adjustment_number} applied.",
            "success",
        )

    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Stock adjustment application failed"
        )
        flash("Unable to apply adjustment.", "danger")

    return redirect(url_for("inventory.stock_adjustments"))


# ============================================================================
# REORDER ALERTS
# ============================================================================

@inventory_bp.route("/reorder/alerts")
@inventory_permission_required("inventory.reorder.alerts")
def stock_alerts():

    alerts = (
        InventoryStockAlert.query
        .filter_by(status="OPEN")
        .order_by(InventoryStockAlert.created_at.desc())
        .all()
    )

    return render_template(
        "inventory/reorder/alerts.html",
        alerts=alerts,
    )


# ============================================================================
# BARCODES / QR
# ============================================================================

@inventory_bp.route(
    "/barcode/generate/<int:item_id>",
    methods=["POST"],
)
@inventory_permission_required("inventory.barcode.generate")
def generate_item_barcode(item_id):

    item = db.session.get(InventoryItem, item_id)

    if not item:
        abort(404)

    if not item.barcode_value:
        if not item.sku:
            abort(400, description="Item SKU is required.")

        item.barcode_value = item.sku
        db.session.commit()

    return jsonify({
        "success": True,
        "message": "Barcode generated successfully.",
        "barcode": item.barcode_value,
        "item_id": item.id,
    })


@inventory_bp.route("/barcode/image/<int:item_id>")
@inventory_permission_required("inventory.barcode.view")
def item_barcode_image(item_id):

    item = db.session.get(InventoryItem, item_id)

    if not item or not item.barcode_value:
        abort(404)

    image = generate_barcode_image(item.barcode_value)

    return send_file(
        image,
        mimetype="image/png",
        download_name=f"barcode-{item.sku}.png",
    )


@inventory_bp.route("/qr/image/<int:item_id>")
@inventory_permission_required("inventory.barcode.view")
def item_qr_image(item_id):

    item = db.session.get(InventoryItem, item_id)

    if not item:
        abort(404)

    if not item.qr_token:
        item.qr_token = uuid4().hex
        db.session.commit()

    image = generate_qr_image(f"INV:{item.qr_token}")

    return send_file(
        image,
        mimetype="image/png",
        download_name=f"qr-{item.sku}.png",
    )


@inventory_bp.route("/barcode/scan", methods=["POST"])
@inventory_permission_required("inventory.barcode.scan")
def scan_inventory_item():

    data = request.get_json(silent=True) or {}
    scanned_value = str(data.get("code", "")).strip()

    if not scanned_value:
        return jsonify({
            "success": False,
            "message": "Please scan or enter a code.",
        }), 400

    if scanned_value.startswith("INV:"):
        item = InventoryItem.query.filter_by(
            qr_token=scanned_value[4:]
        ).first()
    else:
        item = InventoryItem.query.filter(
            or_(
                InventoryItem.barcode_value == scanned_value,
                InventoryItem.sku == scanned_value,
            )
        ).first()

    if not item:
        return jsonify({
            "success": False,
            "message": "Inventory item not found.",
        }), 404

    return jsonify({
        "success": True,
        "item": {
            "id": item.id,
            "sku": item.sku,
            "name": item.name,
            "barcode": item.barcode_value,
        },
    })


@inventory_bp.route("/barcodes")
@inventory_permission_required("inventory.barcode.view")
def barcode_management():

    items = InventoryItem.query.order_by(
        InventoryItem.id.desc()
    ).all()

    return render_template(
        "inventory/barcodes.html", items=items
    )


# ============================================================================
# STOCKTAKE
# ============================================================================

@inventory_bp.route("/stocktake")
@inventory_permission_required("inventory.stocktake.view")
def stocktake_dashboard():

    sessions = StocktakeSession.query.order_by(
        StocktakeSession.created_at.desc()
    ).all()

    return render_template(
        "inventory/stocktake.html", sessions=sessions
    )


@inventory_bp.route("/stocktake/create", methods=["POST"])
@inventory_permission_required("inventory.stocktake.create")
def create_stocktake_route():

    try:
        warehouse_id = int(request.form.get("warehouse_id"))
        title = request.form.get("title", "").strip()
        count_date = date.fromisoformat(
            request.form.get("count_date")
        )

        if not title:
            raise ValueError("Stocktake title is required.")

        session = create_stocktake(
            warehouse_id=warehouse_id,
            title=title,
            count_date=count_date,
            created_by=current_user.id,
        )

        db.session.commit()

        flash(
            f"Stocktake {session.session_number} created.",
            "success",
        )

    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to create stocktake session"
        )
        flash("Unable to create stocktake session.", "danger")

    return redirect(url_for("inventory.stocktake_dashboard"))


@inventory_bp.route(
    "/stocktake/<int:stocktake_id>/start",
    methods=["POST"],
)
@inventory_permission_required("inventory.stocktake.start")
def start_stocktake(stocktake_id):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session:
        abort(404)

    if session.status != "DRAFT":
        flash("This stocktake cannot be started.", "warning")
        return redirect(url_for("inventory.stocktake_dashboard"))

    active = StocktakeSession.query.filter(
        StocktakeSession.warehouse_id == session.warehouse_id,
        StocktakeSession.status == "IN_PROGRESS",
    ).first()

    if active:
        flash(
            "Another stocktake is already active for this warehouse.",
            "warning",
        )
        return redirect(url_for("inventory.stocktake_dashboard"))

    session.status = "IN_PROGRESS"
    db.session.commit()

    flash("Stocktake started.", "success")
    return redirect(
        url_for(
            "inventory.stocktake_details",
            stocktake_id=session.id,
        )
    )


@inventory_bp.route("/stocktake/<int:stocktake_id>")
@inventory_permission_required("inventory.stocktake.view")
def stocktake_details(stocktake_id):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session:
        abort(404)

    lines = StocktakeLine.query.filter_by(
        stocktake_id=session.id
    ).order_by(StocktakeLine.id.asc()).all()

    return render_template(
        "inventory/stocktake_details.html",
        session=session,
        lines=lines,
    )


@inventory_bp.route(
    "/stocktake/<int:stocktake_id>/scan",
    methods=["POST"],
)
@inventory_permission_required("inventory.stocktake.count")
def stocktake_scan(stocktake_id):

    data = request.get_json(silent=True) or {}
    scanned_code = str(data.get("code", "")).strip()
    quantity = data.get("quantity", 1)

    if not scanned_code:
        return jsonify({
            "success": False,
            "message": "Please scan an item.",
        }), 400

    if scanned_code.startswith("INV:"):
        item = InventoryItem.query.filter_by(
            qr_token=scanned_code[4:]
        ).first()
    else:
        item = InventoryItem.query.filter(
            or_(
                InventoryItem.barcode_value == scanned_code,
                InventoryItem.sku == scanned_code,
            )
        ).first()

    if not item:
        return jsonify({
            "success": False,
            "message": "Item not found.",
        }), 404

    try:
        line = add_stocktake_count(
            stocktake_id=stocktake_id,
            item_id=item.id,
            quantity=quantity,
            counted_by=current_user.id,
        )
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Item counted successfully.",
            "item": item.name,
            "sku": item.sku,
            "counted_quantity": str(line.counted_quantity),
            "expected_quantity": str(line.expected_quantity),
            "variance": str(line.variance_quantity),
        })

    except ValueError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Stocktake scanning failed"
        )
        return jsonify({
            "success": False,
            "message": "Unable to record the count.",
        }), 500


@inventory_bp.route(
    "/stocktake/<int:stocktake_id>/submit",
    methods=["POST"],
)
@inventory_permission_required("inventory.stocktake.submit")
def submit_stocktake(stocktake_id):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session:
        abort(404)

    if session.status != "IN_PROGRESS":
        flash(
            "Stocktake is not open for submission.", "warning"
        )
        return redirect(url_for("inventory.stocktake_dashboard"))

    lines = StocktakeLine.query.filter_by(
        stocktake_id=session.id
    ).all()

    if not lines:
        flash("No stocktake items found.", "warning")
        return redirect(
            url_for(
                "inventory.stocktake_details",
                stocktake_id=session.id,
            )
        )

    uncounted = [
        line for line in lines
        if line.count_status == "NOT_COUNTED"
    ]

    if uncounted:
        flash(
            f"{len(uncounted)} items have not been counted.",
            "warning",
        )
        return redirect(
            url_for(
                "inventory.stocktake_details",
                stocktake_id=session.id,
            )
        )

    calculate_stocktake_variances(session.id)

    session.status = "PENDING_REVIEW"
    session.submitted_at = datetime.utcnow()

    db.session.commit()

    flash("Stocktake submitted for review.", "success")
    return redirect(url_for("inventory.stocktake_dashboard"))


@inventory_bp.route(
    "/stocktake/<int:stocktake_id>/approve",
    methods=["POST"],
)
@inventory_permission_required("inventory.stocktake.approve")
def approve_stocktake(stocktake_id):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session:
        abort(404)

    if session.status != "PENDING_REVIEW":
        flash(
            "This stocktake is not awaiting approval.", "warning"
        )
        return redirect(url_for("inventory.stocktake_dashboard"))

    if session.created_by == current_user.id:
        abort(
            403,
            description="You cannot approve your own stocktake.",
        )

    session.status = "APPROVED"
    session.approved_by = current_user.id
    session.approved_at = datetime.utcnow()

    db.session.commit()

    flash(
        "Stocktake approved. Variances are ready for reconciliation.",
        "success",
    )
    return redirect(
        url_for(
            "inventory.stocktake_details",
            stocktake_id=session.id,
        )
    )


@inventory_bp.route(
    "/stocktake/<int:stocktake_id>/reject",
    methods=["POST"],
)
@inventory_permission_required("inventory.stocktake.reject")
def reject_stocktake(stocktake_id):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session:
        abort(404)

    if session.status != "PENDING_REVIEW":
        abort(
            400,
            description="Stocktake is not awaiting review.",
        )

    if session.created_by == current_user.id:
        abort(403)

    reason = request.form.get("reason", "").strip()

    if not reason:
        flash("A rejection reason is required.", "danger")
        return redirect(
            url_for(
                "inventory.stocktake_details",
                stocktake_id=session.id,
            )
        )

    session.status = "REJECTED"
    session.reviewed_by = current_user.id
    session.remarks = reason

    db.session.commit()

    flash("Stocktake rejected.", "warning")
    return redirect(url_for("inventory.stocktake_dashboard"))


@inventory_bp.route(
    "/stocktake/<int:stocktake_id>/reconcile",
    methods=["POST"],
)
@inventory_permission_required("inventory.stocktake.reconcile")
def reconcile_stocktake(stocktake_id):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session:
        abort(404)

    try:
        created_count = create_stocktake_adjustments(
            stocktake_id=stocktake_id,
            requested_by=current_user.id,
        )
        db.session.commit()

        flash(
            f"{created_count} adjustment request(s) created. "
            "Submit them through the existing Stock Adjustment "
            "workflow.",
            "success",
        )

    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Stocktake reconciliation failed"
        )
        flash("Unable to reconcile stocktake.", "danger")

    return redirect(
        url_for(
            "inventory.stocktake_details",
            stocktake_id=stocktake_id,
        )
    )


# ============================================================================
# REPORTS
# ============================================================================

@inventory_bp.route("/reports")
@inventory_permission_required("inventory.report.dashboard")
def inventory_reports_dashboard():

    summary = get_inventory_summary()

    warehouses = Warehouse.query.order_by(
        Warehouse.name
    ).all()

    return render_template(
        "inventory/reports_dashboard.html",
        summary=summary,
        warehouses=warehouses,
    )


@inventory_bp.route("/reports/stock")
@inventory_permission_required("inventory.report.stock")
def inventory_stock_report():

    warehouse_id = request.args.get(
        "warehouse_id", type=int
    )
    search = request.args.get("search", "").strip()

    rows = get_stock_report(
        warehouse_id=warehouse_id,
        search=search,
    )

    warehouses = Warehouse.query.order_by(
        Warehouse.name
    ).all()

    return render_template(
        "inventory/stock_report.html",
        rows=rows,
        warehouses=warehouses,
        selected_warehouse=warehouse_id,
        search=search,
    )


@inventory_bp.route("/reports/valuation")
@inventory_permission_required("inventory.report.valuation")
def inventory_valuation_report():

    warehouse_id = request.args.get(
        "warehouse_id", type=int
    )
    rows = get_stock_valuation(warehouse_id=warehouse_id)

    total_value = sum(
        (row["total_value"] for row in rows),
        Decimal("0"),
    )

    warehouses = Warehouse.query.order_by(
        Warehouse.name
    ).all()

    return render_template(
        "inventory/valuation_report.html",
        rows=rows,
        total_value=total_value,
        warehouses=warehouses,
        selected_warehouse=warehouse_id,
    )


@inventory_bp.route("/reports/movements")
@inventory_permission_required("inventory.report.movement")
def inventory_movement_report():

    start_text = request.args.get("start_date")
    end_text = request.args.get("end_date")
    warehouse_id = request.args.get(
        "warehouse_id", type=int
    )
    movement_type = request.args.get(
        "movement_type", ""
    ).strip()

    try:
        start_date = (
            date.fromisoformat(start_text)
            if start_text else None
        )
        end_date = (
            date.fromisoformat(end_text)
            if end_text else None
        )

        if (
            start_date
            and end_date
            and start_date > end_date
        ):
            raise ValueError(
                "Start date cannot be after end date."
            )

    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(
            url_for("inventory.inventory_movement_report")
        )

    rows = get_stock_movement_report(
        start_date=start_date,
        end_date=end_date,
        warehouse_id=warehouse_id,
        movement_type=movement_type or None,
    )

    warehouses = Warehouse.query.order_by(
        Warehouse.name
    ).all()

    return render_template(
        "inventory/movement_report.html",
        rows=rows,
        warehouses=warehouses,
        start_date=start_text or "",
        end_date=end_text or "",
        selected_warehouse=warehouse_id,
        movement_type=movement_type,
    )


@inventory_bp.route("/reports/valuation/export")
@inventory_permission_required(
    "inventory.report.export_excel"
)
def export_inventory_valuation():

    warehouse_id = request.args.get(
        "warehouse_id", type=int
    )
    rows = get_stock_valuation(warehouse_id=warehouse_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "SKU",
        "Item Name",
        "Warehouse",
        "Quantity",
        "Unit Cost",
        "Total Value",
    ])

    for row in rows:
        writer.writerow([
            row["sku"],
            row["name"],
            row["warehouse"],
            str(row["quantity"]),
            str(row["unit_cost"]),
            str(row["total_value"]),
        ])

    response = Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
    )
    response.headers["Content-Disposition"] = (
        "attachment; filename=inventory_valuation.csv"
    )

    return response


# ============================================================================
# CONSTANTS (imported by some templates / other routes)
# ============================================================================

INVENTORY_CATEGORIES = (
    "Timber", "Plywood", "MDF", "Hardware", "Paint & Glue",
    "Finished Product", "Other",
)

INVENTORY_UNITS = (
    "pcs", "sheet", "meter", "m3", "kg", "liter", "box", "set",
)

INVENTORY_REFERENCES = (
    "Purchasing", "Production", "Sales", "Adjustment", "Manual",
)