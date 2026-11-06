"""Procurement desk: suppliers, requisitions, orders and deliveries.

The working desk lives in ``procurement.py`` (moved from
``departments/procurement.py``). It is re-exported here so the application
bootstrap keeps calling ``procurement.register(app)``.
"""

from .procurement import procurement_bp  # noqa: F401
from .procurement import register  # noqa: F401
