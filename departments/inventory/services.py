from datetime import datetime
from decimal import Decimal, InvalidOperation

from extensions import db

from modules.inventory.models import (
    InventoryItem,
    Warehouse,
    StockBalance,
    StockMovement,
    StockAdjustment,
    StockAdjustmentApproval,
    StocktakeSession,
    WarehouseStockSetting,
    InventoryStockAlert,
)


# ============================================================================
# STOCK MOVEMENTS
# ============================================================================

def record_stock_movement(
    item_id,
    warehouse_id,
    movement_type,
    quantity,
    user_id,
    reference_type=None,
    reference_no=None,
    remarks=None,
):
    try:
        quantity = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid quantity.")

    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    if movement_type not in ("IN", "OUT"):
        raise ValueError("Invalid movement type.")

    item = db.session.get(InventoryItem, item_id)
    warehouse = db.session.get(Warehouse, warehouse_id)

    if not item or item.status != "Active":
        raise ValueError("Inventory item is unavailable.")

    if not warehouse or warehouse.status != "Active":
        raise ValueError("Warehouse is unavailable.")

    balance = (
        StockBalance.query
        .filter_by(item_id=item_id, warehouse_id=warehouse_id)
        .with_for_update()
        .first()
    )

    if balance is None:
        if movement_type == "OUT":
            raise ValueError("No available stock in this warehouse.")

        balance = StockBalance(
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("0"),
        )
        db.session.add(balance)
        db.session.flush()

    before = Decimal(str(balance.quantity))

    if movement_type == "OUT":
        if before < quantity:
            raise ValueError(
                f"Insufficient stock. Available quantity: {before}"
            )
        after = before - quantity
    else:
        after = before + quantity

    balance.quantity = after

    movement = StockMovement(
        item_id=item_id,
        warehouse_id=warehouse_id,
        movement_type=movement_type,
        quantity=quantity,
        balance_before=before,
        balance_after=after,
        reference_type=reference_type,
        reference_no=reference_no,
        remarks=remarks,
        created_by=user_id,
    )

    db.session.add(movement)
    db.session.flush()

    return movement


def transfer_stock(
    item_id,
    source_warehouse_id,
    destination_warehouse_id,
    quantity,
    user_id,
    reference_no=None,
    remarks=None,
):
    if source_warehouse_id == destination_warehouse_id:
        raise ValueError("Source and destination must differ.")

    try:
        quantity = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid quantity.")

    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("Transfer quantity must be positive.")

    try:
        record_stock_movement(
            item_id=item_id,
            warehouse_id=source_warehouse_id,
            movement_type="OUT",
            quantity=quantity,
            user_id=user_id,
            reference_type="Warehouse Transfer",
            reference_no=reference_no,
            remarks=remarks,
        )

        record_stock_movement(
            item_id=item_id,
            warehouse_id=destination_warehouse_id,
            movement_type="IN",
            quantity=quantity,
            user_id=user_id,
            reference_type="Warehouse Transfer",
            reference_no=reference_no,
            remarks=remarks,
        )

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise


# ============================================================================
# STOCK ADJUSTMENTS
# ============================================================================

def apply_stock_adjustment(adjustment_id, applied_by):

    adjustment = (
        StockAdjustment.query
        .filter_by(id=adjustment_id)
        .with_for_update()
        .first()
    )

    if not adjustment:
        raise ValueError("Adjustment not found.")

    if adjustment.status != "APPROVED":
        raise ValueError("Only approved adjustments can be applied.")

    if adjustment.approved_by == applied_by:
        raise ValueError(
            "The approver cannot apply their own adjustment."
        )

    balance = (
        StockBalance.query
        .filter_by(
            item_id=adjustment.item_id,
            warehouse_id=adjustment.warehouse_id,
        )
        .with_for_update()
        .first()
    )

    current_quantity = Decimal(
        str(balance.quantity if balance else 0)
    )

    physical_quantity = Decimal(
        str(adjustment.physical_quantity)
    )

    if current_quantity != Decimal(str(adjustment.system_quantity)):
        raise ValueError(
            "Stock has changed since the request. "
            "Create a new physical stock verification."
        )

    difference = physical_quantity - current_quantity

    if difference == 0:
        raise ValueError("No stock adjustment is required.")

    record_stock_movement(
        item_id=adjustment.item_id,
        warehouse_id=adjustment.warehouse_id,
        movement_type="IN" if difference > 0 else "OUT",
        quantity=abs(difference),
        reference_type="STOCK_ADJUSTMENT",
        reference_no=adjustment.adjustment_number,
        user_id=applied_by,
        remarks=adjustment.reason,
    )

    adjustment.status = "APPLIED"
    adjustment.applied_by = applied_by
    adjustment.applied_at = datetime.utcnow()

    db.session.add(
        StockAdjustmentApproval(
            adjustment_id=adjustment.id,
            action="APPLIED",
            action_by=applied_by,
            comments="Approved stock adjustment applied.",
        )
    )

    return adjustment


# ============================================================================
# STOCK ALERTS
# ============================================================================

def check_warehouse_stock(item_id, warehouse_id):

    setting = WarehouseStockSetting.query.filter_by(
        item_id=item_id,
        warehouse_id=warehouse_id,
        active=True,
    ).first()

    if not setting:
        return None

    balance = StockBalance.query.filter_by(
        item_id=item_id,
        warehouse_id=warehouse_id,
    ).first()

    current_quantity = Decimal(
        str(balance.quantity if balance else 0)
    )
    minimum = Decimal(str(setting.minimum_stock))
    reorder_quantity = Decimal(str(setting.reorder_quantity))

    if current_quantity <= 0:
        alert_type = "OUT_OF_STOCK"
    elif current_quantity <= minimum:
        alert_type = "LOW_STOCK"
    else:
        return None

    recommended = max(
        reorder_quantity, minimum - current_quantity
    )

    existing_alert = InventoryStockAlert.query.filter_by(
        item_id=item_id,
        warehouse_id=warehouse_id,
        status="OPEN",
    ).first()

    if existing_alert:
        existing_alert.alert_type = alert_type
        existing_alert.current_quantity = current_quantity
        existing_alert.minimum_quantity = minimum
        existing_alert.recommended_quantity = recommended
        return existing_alert

    alert = InventoryStockAlert(
        item_id=item_id,
        warehouse_id=warehouse_id,
        alert_type=alert_type,
        current_quantity=current_quantity,
        minimum_quantity=minimum,
        recommended_quantity=recommended,
        status="OPEN",
    )

    db.session.add(alert)
    return alert


def resolve_stock_alert(item_id, warehouse_id):

    alerts = InventoryStockAlert.query.filter_by(
        item_id=item_id,
        warehouse_id=warehouse_id,
        status="OPEN",
    ).all()

    for alert in alerts:
        alert.status = "RESOLVED"
        alert.resolved_at = datetime.utcnow()


# ============================================================================
# MATERIAL REQUESTS / ISSUES
# ============================================================================

def issue_materials(
    request_id,
    issued_by,
    receiver_id,
    quantities,
    remarks=None,
):
    """
    Create a MaterialIssue and its line items, and decrement stock.

    `quantities` is a mapping of request_item_id -> Decimal quantity.
    """

    from modules.inventory.models import (
        MaterialRequest,
        MaterialIssue,
        MaterialIssueItem,
    )

    material_request = db.session.get(
        MaterialRequest, request_id
    )

    if not material_request:
        raise ValueError("Material request not found.")

    issue = MaterialIssue(
        issue_number=(
            f"ISS-{datetime.utcnow():%Y%m%d%H%M%S%f}"
        ),
        request_id=material_request.id,
        issued_by=issued_by,
        remarks=remarks,
    )

    db.session.add(issue)
    db.session.flush()

    all_issued = True

    for request_item in material_request.items:

        quantity = quantities.get(request_item.id)

        if quantity is None:
            continue

        if quantity <= 0:
            continue

        remaining = (
            Decimal(str(request_item.approved_quantity))
            - Decimal(str(request_item.issued_quantity))
        )

        if quantity > remaining:
            raise ValueError(
                "Issue quantity exceeds the approved quantity."
            )

        record_stock_movement(
            item_id=request_item.item_id,
            warehouse_id=request_item.warehouse_id,
            movement_type="OUT",
            quantity=quantity,
            user_id=issued_by,
            reference_type="MATERIAL_ISSUE",
            reference_no=issue.issue_number,
            remarks=remarks,
        )

        request_item.issued_quantity = (
            Decimal(str(request_item.issued_quantity)) + quantity
        )

        db.session.add(
            MaterialIssueItem(
                issue_id=issue.id,
                request_item_id=request_item.id,
                item_id=request_item.item_id,
                warehouse_id=request_item.warehouse_id,
                issued_quantity=quantity,
            )
        )

        if (
            Decimal(str(request_item.issued_quantity))
            < Decimal(str(request_item.approved_quantity))
        ):
            all_issued = False

    material_request.status = (
        "ISSUED" if all_issued else "PARTIALLY_ISSUED"
    )

    db.session.flush()
    return issue