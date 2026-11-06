"""Compatibility pointer: the audit console lives in
``departments/audit/console.py`` and is mounted by the package.
"""

from departments.audit import audit_bp  # noqa: F401
from departments.audit import register  # noqa: F401
