"""The System Integration desk: external systems the company connects to.

The package owns one blueprint, ``integrations_bp``, mounted at ``/integrations``,
and the tables its screens read (see ``database.py``).
"""

from .routes import integrations_bp
from . import directory as _directory_routes

__all__ = ["integrations_bp"]
