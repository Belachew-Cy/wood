from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def inventory_permission_required(permission_code):
    """Require an authenticated user holding the given permission."""

    def decorator(view_function):

        @wraps(view_function)
        @login_required
        def wrapped_view(*args, **kwargs):
            if not current_user.has_permission(permission_code):
                abort(403)
            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator


INVENTORY_STOCKTAKE_PERMISSIONS = [
    "inventory.stocktake.view",
    "inventory.stocktake.create",
    "inventory.stocktake.edit",
    "inventory.stocktake.start",
    "inventory.stocktake.count",
    "inventory.stocktake.submit",
    "inventory.stocktake.review",
    "inventory.stocktake.approve",
    "inventory.stocktake.reject",
    "inventory.stocktake.reconcile",
    "inventory.stocktake.report",
    "inventory.stocktake.cancel",
]


INVENTORY_REPORT_PERMISSIONS = [
    "inventory.report.dashboard",
    "inventory.report.stock",
    "inventory.report.valuation",
    "inventory.report.movement",
    "inventory.report.warehouse",
    "inventory.report.low_stock",
    "inventory.report.performance",
    "inventory.report.stocktake",
    "inventory.report.export_excel",
    "inventory.report.export_pdf",
]


INVENTORY_NOTIFICATION_PERMISSIONS = [
    "inventory.notification.view",
    "inventory.notification.manage",
    "inventory.notification.stock_alert",
    "inventory.notification.request",
    "inventory.notification.approval",
    "inventory.notification.reorder",
    "inventory.notification.stocktake",
]


INVENTORY_NOTIFICATION_CATEGORIES = {
    "INVENTORY_LOW_STOCK": "Low Stock",
    "INVENTORY_OUT_OF_STOCK": "Out of Stock",
    "INVENTORY_REQUEST": "Material Request",
    "INVENTORY_REQUEST_DECISION": "Request Decision",
    "INVENTORY_ADJUSTMENT": "Stock Adjustment",
    "INVENTORY_ADJUSTMENT_DECISION": "Adjustment Decision",
    "INVENTORY_STOCKTAKE": "Stocktake",
    "INVENTORY_REORDER": "Reorder Alert",
}