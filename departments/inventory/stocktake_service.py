from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from extensions import db

from modules.inventory.models import (
    InventoryItem,
    Warehouse,
    StockBalance,
    StockAdjustment,
    StocktakeSession,
    StocktakeLine,
)


def generate_stocktake_number():
    return f"STK-{datetime.utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}"


def create_stocktake(warehouse_id, title, count_date, created_by):

    warehouse = db.session.get(Warehouse, warehouse_id)

    if not warehouse:
        raise ValueError("Warehouse not found.")

    session = StocktakeSession(
        session_number=generate_stocktake_number(),
        warehouse_id=warehouse_id,
        title=title,
        count_date=count_date,
        created_by=created_by,
        status="DRAFT",
    )

    db.session.add(session)
    db.session.flush()

    balances = StockBalance.query.filter_by(
        warehouse_id=warehouse_id
    ).all()

    for balance in balances:
        db.session.add(
            StocktakeLine(
                stocktake_id=session.id,
                item_id=balance.item_id,
                expected_quantity=Decimal(
                    str(balance.quantity or 0)
                ),
                counted_quantity=Decimal("0"),
                variance_quantity=Decimal("0"),
                count_status="NOT_COUNTED",
            )
        )

    db.session.flush()
    return session


def add_stocktake_count(
    stocktake_id,
    item_id,
    quantity,
    counted_by,
    remarks=None,
):
    session = db.session.get(StocktakeSession, stocktake_id)

    if not session or session.status != "IN_PROGRESS":
        raise ValueError("Stocktake is not open for counting.")

    try:
        quantity = Decimal(str(quantity))
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid count quantity.")

    if not quantity.is_finite() or quantity < 0:
        raise ValueError("Count quantity must be zero or greater.")

    line = StocktakeLine.query.filter_by(
        stocktake_id=stocktake_id,
        item_id=item_id,
    ).first()

    if not line:
        item = db.session.get(InventoryItem, item_id)

        if not item:
            raise ValueError("Inventory item not found.")

        line = StocktakeLine(
            stocktake_id=stocktake_id,
            item_id=item_id,
            expected_quantity=Decimal("0"),
            counted_quantity=Decimal("0"),
            variance_quantity=Decimal("0"),
            count_status="NOT_COUNTED",
        )
        db.session.add(line)

    line.counted_quantity = (
        Decimal(str(line.counted_quantity or 0)) + quantity
    )
    line.variance_quantity = (
        line.counted_quantity - line.expected_quantity
    )
    line.count_status = "COUNTED"
    line.counted_by = counted_by
    line.counted_at = datetime.utcnow()

    if remarks:
        line.remarks = remarks

    return line


def calculate_stocktake_variances(stocktake_id):

    lines = StocktakeLine.query.filter_by(
        stocktake_id=stocktake_id
    ).all()

    for line in lines:
        line.variance_quantity = (
            Decimal(str(line.counted_quantity or 0))
            - Decimal(str(line.expected_quantity or 0))
        )

        if line.variance_quantity == 0:
            line.count_status = "MATCHED"
        else:
            line.count_status = "VARIANCE"

    return lines


def create_stocktake_adjustments(stocktake_id, requested_by):

    session = db.session.get(StocktakeSession, stocktake_id)

    if not session or session.status != "APPROVED":
        raise ValueError(
            "Only approved stocktakes can be reconciled."
        )

    lines = StocktakeLine.query.filter_by(
        stocktake_id=stocktake_id
    ).all()

    created_count = 0

    for line in lines:

        variance = Decimal(str(line.variance_quantity or 0))

        if variance == 0:
            continue

        existing = StockAdjustment.query.filter_by(
            stocktake_line_id=line.id
        ).first()

        if existing:
            continue

        adjustment = StockAdjustment(
            adjustment_number=(
                f"ADJ-{datetime.utcnow():%Y%m%d}-"
                f"{uuid4().hex[:8].upper()}"
            ),
            stocktake_line_id=line.id,
            item_id=line.item_id,
            warehouse_id=session.warehouse_id,
            adjustment_type="DATA_CORRECTION",
            system_quantity=line.expected_quantity,
            physical_quantity=line.counted_quantity,
            adjustment_quantity=variance,
            reason=(
                f"Stocktake {session.session_number}: "
                f"{line.remarks or 'Physical stock discrepancy'}"
            ),
            status="DRAFT",
            requested_by=requested_by,
        )

        db.session.add(adjustment)
        created_count += 1

    session.status = "RECONCILIATION_PENDING"
    return created_count