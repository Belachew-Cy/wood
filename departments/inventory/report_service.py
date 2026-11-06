from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from extensions import db

from modules.inventory.models import (
    InventoryItem,
    Warehouse,
    StockBalance,
    StockMovement,
)


def get_inventory_summary():

    total_items = InventoryItem.query.count()

    balances = StockBalance.query.all()

    total_quantity = Decimal("0")
    total_value = Decimal("0")
    low_stock = 0
    out_of_stock = 0

    for balance in balances:

        quantity = Decimal(str(balance.quantity or 0))

        item = db.session.get(
            InventoryItem,
            balance.item_id
        )

        if not item:
            continue

        unit_cost = Decimal(str(item.unit_cost or 0))

        total_quantity += quantity
        total_value += quantity * unit_cost

        minimum = Decimal(
            str(getattr(item, "minimum_stock", 0) or 0)
        )

        if quantity <= 0:
            out_of_stock += 1
        elif quantity <= minimum:
            low_stock += 1

    return {
        "total_items": total_items,
        "total_quantity": total_quantity,
        "total_value": total_value,
        "low_stock": low_stock,
        "out_of_stock": out_of_stock,
    }


def get_stock_report(warehouse_id=None, search=None):

    query = (
        db.session.query(
            StockBalance,
            InventoryItem,
            Warehouse
        )
        .join(
            InventoryItem,
            InventoryItem.id == StockBalance.item_id
        )
        .join(
            Warehouse,
            Warehouse.id == StockBalance.warehouse_id
        )
    )

    if warehouse_id:
        query = query.filter(
            StockBalance.warehouse_id == warehouse_id
        )

    if search:
        search_term = f"%{search.strip()}%"

        query = query.filter(
            db.or_(
                InventoryItem.sku.ilike(search_term),
                InventoryItem.name.ilike(search_term)
            )
        )

    return query.order_by(
        Warehouse.name,
        InventoryItem.name
    ).all()


def get_stock_valuation(warehouse_id=None):

    query = (
        db.session.query(
            InventoryItem.sku,
            InventoryItem.name,
            Warehouse.name.label("warehouse_name"),
            StockBalance.quantity,
            InventoryItem.unit_cost
        )
        .join(
            InventoryItem,
            InventoryItem.id == StockBalance.item_id
        )
        .join(
            Warehouse,
            Warehouse.id == StockBalance.warehouse_id
        )
    )

    if warehouse_id:
        query = query.filter(
            StockBalance.warehouse_id == warehouse_id
        )

    results = []

    for row in query.all():

        quantity = Decimal(str(row.quantity or 0))
        unit_cost = Decimal(str(row.unit_cost or 0))

        results.append({
            "sku": row.sku,
            "name": row.name,
            "warehouse": row.warehouse_name,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "total_value": quantity * unit_cost,
        })

    return results


def get_stock_movement_report(
    start_date=None,
    end_date=None,
    warehouse_id=None,
    movement_type=None
):

    query = (
        db.session.query(
            StockMovement,
            InventoryItem,
            Warehouse
        )
        .join(
            InventoryItem,
            InventoryItem.id == StockMovement.item_id
        )
        .join(
            Warehouse,
            Warehouse.id == StockMovement.warehouse_id
        )
    )

    if start_date:
        query = query.filter(
            StockMovement.created_at >= start_date
        )

    if end_date:
        # Include the whole selected end date.
        end_exclusive = end_date + timedelta(days=1)

        query = query.filter(
            StockMovement.created_at < end_exclusive
        )

    if warehouse_id:
        query = query.filter(
            StockMovement.warehouse_id == warehouse_id
        )

    if movement_type:
        query = query.filter(
            StockMovement.movement_type == movement_type
        )

    return query.order_by(
        StockMovement.created_at.desc()
    ).all()