from flask import current_app


def send_inventory_notification(
    recipient_ids,
    title,
    message,
    category,
    action_url=None,
    reference_id=None,
):
    """
    Send an inventory notification using the existing
    application notification service.
    """

    if not recipient_ids:
        return 0

    sent_count = 0

    from modules.notifications.services import create_notification

    for recipient_id in set(recipient_ids):

        if not recipient_id:
            continue

        create_notification(
            user_id=recipient_id,
            title=title,
            message=message,
            category=category,
            action_url=action_url,
            reference_id=reference_id,
        )

        sent_count += 1

    return sent_count


from modules.inventory.notification_recipients import (
    get_inventory_manager_ids,
    get_warehouse_manager_ids,
    get_procurement_manager_ids,
    get_department_manager_ids,
)


# ============================================================================
# STOCK ALERTS
# ============================================================================

def notify_low_stock(item, warehouse, current_quantity):

    recipients = (
        get_inventory_manager_ids()
        + get_warehouse_manager_ids(warehouse.id)
    )

    return send_inventory_notification(
        recipient_ids=recipients,
        title="Low Inventory Stock Alert",
        message=(
            f"{item.name} is running low in "
            f"{warehouse.name}. Current quantity: "
            f"{current_quantity}."
        ),
        category="INVENTORY_LOW_STOCK",
        action_url="/inventory/reorder/alerts",
        reference_id=item.id,
    )


def notify_out_of_stock(item, warehouse):

    recipients = (
        get_inventory_manager_ids()
        + get_warehouse_manager_ids(warehouse.id)
        + get_procurement_manager_ids()
    )

    return send_inventory_notification(
        recipient_ids=recipients,
        title="Inventory Item Out of Stock",
        message=(
            f"{item.name} is out of stock in "
            f"{warehouse.name}. Please review replenishment."
        ),
        category="INVENTORY_OUT_OF_STOCK",
        action_url="/inventory/reorder/alerts",
        reference_id=item.id,
    )


# ============================================================================
# MATERIAL REQUESTS
# ============================================================================

def notify_material_request_submitted(material_request):

    recipients = get_department_manager_ids(
        material_request.department_id
    )

    return send_inventory_notification(
        recipient_ids=recipients,
        title="New Material Request",
        message=(
            f"Material request {material_request.request_number} "
            "is waiting for your approval."
        ),
        category="INVENTORY_REQUEST",
        action_url=f"/inventory/requests/{material_request.id}",
        reference_id=material_request.id,
    )


def notify_material_request_decision(material_request, decision):

    return send_inventory_notification(
        recipient_ids=[material_request.requested_by],
        title=f"Material Request {decision.title()}",
        message=(
            f"Your material request "
            f"{material_request.request_number} "
            f"has been {decision.lower()}."
        ),
        category="INVENTORY_REQUEST_DECISION",
        action_url=f"/inventory/requests/{material_request.id}",
        reference_id=material_request.id,
    )


# ============================================================================
# STOCK ADJUSTMENTS
# ============================================================================

def notify_stock_adjustment_submitted(adjustment):

    return send_inventory_notification(
        recipient_ids=get_inventory_manager_ids(),
        title="Stock Adjustment Requires Approval",
        message=(
            f"Adjustment request #{adjustment.id} "
            "is waiting for approval."
        ),
        category="INVENTORY_ADJUSTMENT",
        action_url="/inventory/adjustments",
        reference_id=adjustment.id,
    )


def notify_stock_adjustment_decision(adjustment, decision):

    return send_inventory_notification(
        recipient_ids=[adjustment.requested_by],
        title=f"Stock Adjustment {decision.title()}",
        message=(
            f"Your stock adjustment request "
            f"#{adjustment.id} has been {decision.lower()}."
        ),
        category="INVENTORY_ADJUSTMENT_DECISION",
        action_url="/inventory/adjustments",
        reference_id=adjustment.id,
    )


# ============================================================================
# STOCKTAKE
# ============================================================================

def notify_stocktake_submitted(stocktake, recipient_ids):

    return send_inventory_notification(
        recipient_ids=recipient_ids,
        title="Stocktake Submitted for Review",
        message=(
            f"Stocktake {stocktake.session_number} "
            "has been submitted for review."
        ),
        category="INVENTORY_STOCKTAKE",
        action_url=f"/inventory/stocktake/{stocktake.id}",
        reference_id=stocktake.id,
    )


def notify_stocktake_approved(stocktake, recipient_ids):

    return send_inventory_notification(
        recipient_ids=recipient_ids,
        title="Stocktake Approved",
        message=(
            f"Stocktake {stocktake.session_number} "
            "has been approved. Review its discrepancies."
        ),
        category="INVENTORY_STOCKTAKE",
        action_url=f"/inventory/stocktake/{stocktake.id}",
        reference_id=stocktake.id,
    )


# ============================================================================
# BATCH SCAN
# ============================================================================

def run_inventory_stock_alert_scan():

    from modules.inventory.models import WarehouseStockSetting
    from modules.inventory.services import check_warehouse_stock

    settings = WarehouseStockSetting.query.filter_by(
        active=True
    ).all()

    scanned = 0
    errors = 0

    for setting in settings:

        try:
            check_warehouse_stock(
                item_id=setting.item_id,
                warehouse_id=setting.warehouse_id,
            )
            scanned += 1
        except Exception:
            errors += 1
            current_app.logger.exception(
                "Inventory stock alert scan failed for "
                "item %s, warehouse %s",
                setting.item_id,
                setting.warehouse_id,
            )

    return {"scanned": scanned, "errors": errors}