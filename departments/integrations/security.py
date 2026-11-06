"""Sealing of the credentials an integration stores.

A connection's password is written once, read only by the adapter that uses it,
and never shown on a screen again. This module does not pick its own key: it uses
the same application secret and the same HKDF-derived Fernet key as the directory
integration in ``services/directory.py``, so one deployment has one master secret
and an installation that moves its key file sees the same behaviour in both desks.
"""

from services.directory import open_secret
from services.directory import seal_secret


def decrypt_credential(sealed):
    """The plaintext for a stored value; raises when it cannot be opened."""
    credential, problem = open_secret(sealed)
    if problem:
        raise RuntimeError(problem)
    return credential


def read_sealed(db, integration_id, credential_name):
    """(plaintext, problem) for one named credential of one integration.

    Adapters want to keep going and report a clean failure rather than raise, so
    this is the form they use; ``decrypt_credential`` is for callers that would
    rather abort.
    """
    row = db.execute("""
        SELECT encrypted_value FROM integration_credentials
        WHERE integration_id = ? AND credential_name = ?
    """, (integration_id, credential_name)).fetchone()
    if row is None:
        return "", "No {} is stored for this integration.".format(credential_name)
    return open_secret(row["encrypted_value"])


def store_credential(db, integration_id, credential_name, value):
    """Seal and save one credential; the row is replaced when it already exists."""
    sealed = seal_secret(value)
    if not sealed:
        return False
    db.execute("""
        INSERT INTO integration_credentials
            (integration_id, credential_name, encrypted_value, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(integration_id, credential_name)
        DO UPDATE SET encrypted_value = excluded.encrypted_value,
                      updated_at = CURRENT_TIMESTAMP
    """, (integration_id, credential_name, sealed))
    return True
