"""Retired: the LDAP read now lives in ``services.directory``.

This module held a second LDAP client (its own bind, search and attribute
decoders) used only by the Active Directory staging sync. The sync now calls
``services.directory.integration_ldap_spec`` and ``ldap_fetch_users``, so the
desk opens one bind and decodes each attribute once. Nothing imports this file;
it is kept only so an external script that imported it fails with an explicit
message instead of silently reading a second, divergent implementation.
"""


def _retired():
    raise RuntimeError(
        "departments.integrations.ad_ldap was merged into services.directory.")
