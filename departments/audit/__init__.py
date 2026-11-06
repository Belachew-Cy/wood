"""Audit Management package.

The signed-in console, auditor screens and oversight routes live in
``console`` (moved from ``departments/audit.py``). The management scaffold
in ``routes`` is deliberately not mounted: its permission adapter has not
been configured and its services are still placeholders.
"""

from .console import audit_bp  # noqa: F401
from .console import register  # noqa: F401
