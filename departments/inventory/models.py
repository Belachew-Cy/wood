import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint

from extensions import db


# ============================================================================
# MATERIAL REQUESTS
# ============================================================================

class MaterialRequest(db.Model):
    __tablename__ = "inventory_material_requests"

    id = db.Column(db.Integer, primary_key=True)

    request_number = db.Column(
        db.String(50), unique=True, nullable=False
    )

    department_id = db.Column(db.Integer, nullable=False)
    requested_by = db.Column(db.Integer, nullable=False)

    purpose = db.Column(db.Text, nullable=False)
    required_date = db.Column(db.Date, nullable=True)

    status = db.Column(
        db.String(30), default="DRAFT", nullable=False
    )

    approved_by = db.Column(db.Integer, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)

    rejected_by = db.Column(db.Integer, nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime, default=datetime.utcnow
    )
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    items = db.relationship(
        "MaterialRequestItem",
        backref="material_request",
        cascade="all, delete-orphan",
    )


class MaterialRequestItem(db.Model):
    __tablename__ = "inventory_material_request_items"

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_material_requests.id"),
        nullable=False,
    )

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_items.id"),
        nullable=False,
    )

    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_warehouses.id"),
        nullable=False,
    )

    requested_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )
    approved_quantity = db.Column(
        db.Numeric(14, 3), default=0, nullable=False
    )
    issued_quantity = db.Column(
        db.Numeric(14, 3), default=0, nullable=False
    )

    remarks = db.Column(db.Text, nullable=True)


class MaterialRequestApproval(db.Model):
    __tablename__ = "inventory_material_request_approvals"

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_material_requests.id"),
        nullable=False,
    )

    action = db.Column(db.String(20), nullable=False)
    action_by = db.Column(db.Integer, nullable=False)
    comments = db.Column(db.Text, nullable=True)

    action_date = db.Column(
        db.DateTime, default=datetime.utcnow
    )


# ============================================================================
# MATERIAL ISSUES
# ============================================================================

class MaterialIssue(db.Model):
    __tablename__ = "inventory_material_issues"

    id = db.Column(db.Integer, primary_key=True)

    issue_number = db.Column(
        db.String(50), unique=True, nullable=False
    )

    request_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_material_requests.id"),
        nullable=False,
    )

    issued_by = db.Column(db.Integer, nullable=False)

    issue_date = db.Column(
        db.DateTime, default=datetime.utcnow
    )

    remarks = db.Column(db.Text, nullable=True)


class MaterialIssueItem(db.Model):
    __tablename__ = "inventory_material_issue_items"

    id = db.Column(db.Integer, primary_key=True)

    issue_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_material_issues.id"),
        nullable=False,
    )

    request_item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_material_request_items.id"),
        nullable=False,
    )

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_items.id"),
        nullable=False,
    )

    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_warehouses.id"),
        nullable=False,
    )

    issued_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )

    issue = db.relationship(
        "MaterialIssue",
        backref=db.backref(
            "items", cascade="all, delete-orphan"
        ),
    )


# ============================================================================
# STOCK ADJUSTMENTS
# ============================================================================

class StockAdjustment(db.Model):
    __tablename__ = "inventory_stock_adjustments"

    id = db.Column(db.Integer, primary_key=True)

    adjustment_number = db.Column(
        db.String(60), unique=True, nullable=False
    )

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_items.id"),
        nullable=False,
    )

    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_warehouses.id"),
        nullable=False,
    )

    stocktake_line_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_stocktake_lines.id"),
        nullable=True,
        unique=True,
    )

    adjustment_type = db.Column(
        db.String(30), nullable=False
    )

    system_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )
    physical_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )
    adjustment_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )

    reason = db.Column(db.Text, nullable=False)

    status = db.Column(
        db.String(30), default="DRAFT", nullable=False
    )

    requested_by = db.Column(
        db.Integer, nullable=False
    )
    approved_by = db.Column(
        db.Integer, nullable=True
    )
    approved_at = db.Column(
        db.DateTime, nullable=True
    )
    applied_by = db.Column(
        db.Integer, nullable=True
    )
    applied_at = db.Column(
        db.DateTime, nullable=True
    )

    created_at = db.Column(
        db.DateTime, default=datetime.utcnow
    )


class StockAdjustmentApproval(db.Model):
    __tablename__ = "inventory_stock_adjustment_approvals"

    id = db.Column(db.Integer, primary_key=True)

    adjustment_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_stock_adjustments.id"),
        nullable=False,
    )

    action = db.Column(db.String(30), nullable=False)
    action_by = db.Column(db.Integer, nullable=False)
    comments = db.Column(db.Text, nullable=True)

    action_date = db.Column(
        db.DateTime, default=datetime.utcnow
    )


# ============================================================================
# WAREHOUSE STOCK SETTINGS + ALERTS
# ============================================================================

class WarehouseStockSetting(db.Model):
    __tablename__ = "inventory_warehouse_stock_settings"

    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_items.id"),
        nullable=False,
    )

    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_warehouses.id"),
        nullable=False,
    )

    minimum_stock = db.Column(
        db.Numeric(14, 3), nullable=False, default=0
    )
    maximum_stock = db.Column(
        db.Numeric(14, 3), nullable=True
    )
    reorder_quantity = db.Column(
        db.Numeric(14, 3), nullable=False, default=0
    )
    lead_time_days = db.Column(
        db.Integer, default=7, nullable=False
    )

    active = db.Column(
        db.Boolean, default=True, nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "item_id",
            "warehouse_id",
            name="uq_warehouse_stock_setting",
        ),
    )


class InventoryStockAlert(db.Model):
    __tablename__ = "inventory_stock_alerts"

    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_items.id"),
        nullable=False,
    )

    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_warehouses.id"),
        nullable=False,
    )

    alert_type = db.Column(
        db.String(30), nullable=False
    )
    current_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )
    minimum_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )
    recommended_quantity = db.Column(
        db.Numeric(14, 3), nullable=False
    )

    status = db.Column(
        db.String(30), default="OPEN", nullable=False
    )

    created_at = db.Column(
        db.DateTime, default=datetime.utcnow
    )
    resolved_at = db.Column(
        db.DateTime, nullable=True
    )


# ============================================================================
# STOCKTAKE
# ============================================================================

class StocktakeSession(db.Model):
    __tablename__ = "inventory_stocktake_sessions"

    id = db.Column(db.Integer, primary_key=True)

    session_number = db.Column(
        db.String(50), unique=True, nullable=False, index=True
    )

    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_warehouses.id"),
        nullable=False,
    )

    title = db.Column(db.String(150), nullable=False)
    count_date = db.Column(db.Date, nullable=False)

    status = db.Column(
        db.String(30), nullable=False, default="DRAFT", index=True
    )

    created_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    reviewed_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    approved_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )

    remarks = db.Column(db.Text)

    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )
    submitted_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    lines = db.relationship(
        "StocktakeLine",
        backref="stocktake",
        cascade="all, delete-orphan",
        lazy=True,
    )

    __table_args__ = (
        db.Index(
            "ix_stocktake_warehouse_status",
            "warehouse_id",
            "status",
        ),
    )


class StocktakeLine(db.Model):
    __tablename__ = "inventory_stocktake_lines"

    id = db.Column(db.Integer, primary_key=True)

    stocktake_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "inventory_stocktake_sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_items.id"),
        nullable=False,
    )

    expected_quantity = db.Column(
        db.Numeric(14, 3), nullable=False, default=0
    )
    counted_quantity = db.Column(
        db.Numeric(14, 3), nullable=False, default=0
    )
    variance_quantity = db.Column(
        db.Numeric(14, 3), nullable=False, default=0
    )

    count_status = db.Column(
        db.String(30), nullable=False, default="NOT_COUNTED"
    )

    counted_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    counted_at = db.Column(db.DateTime)
    remarks = db.Column(db.Text)

    item = db.relationship(
        "InventoryItem", foreign_keys=[item_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "stocktake_id",
            "item_id",
            name="uq_stocktake_item",
        ),
    )


# ============================================================================
# INVENTORY ITEM (barcode/QR fields appended)
# ============================================================================
#
# The InventoryItem model is assumed to already exist elsewhere in your
# codebase. If not, make sure it defines (or your migration adds):
#
#   barcode_value = db.Column(
#       db.String(100), unique=True, nullable=True, index=True
#   )
#   qr_token = db.Column(
#       db.String(100), unique=True, nullable=True, index=True,
#       default=lambda: uuid.uuid4().hex,
#   )
#   minimum_stock / maximum_stock / reorder_quantity / lead_time_days
#
# (The original file scattered these fields at module level — they belong
# inside InventoryItem, not here.)