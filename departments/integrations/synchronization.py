"""Running one synchronization, and running every one that is due.

A scheduled run and a "Sync now" click take the same path: an
``integration_sync_logs`` row opens as *running*, the adapter does its work, and
the row is closed as *success* or *failed* with the message the administrator
will read on the monitoring desk. Every function here takes its database
connection from the caller - a route passes ``get_db()``, the scheduler process
passes its own connection - so nothing in this module opens one behind the
caller's back.
"""
import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from .ad_sync import synchronize_ad_users
from .database import AD_TYPE
from .database import due_integrations

logger = logging.getLogger(__name__)

# Only a type with an adapter below can move data. The other types are reachable
# through the connection test desk, and offering a sync button for them would
# produce log rows that can only ever fail.
ADAPTERS = (AD_TYPE,)


def utc_now():
    return datetime.now(timezone.utc)


def iso(moment=None):
    return (moment or utc_now()).isoformat(timespec="seconds")


def has_adapter(integration_type):
    return integration_type in ADAPTERS


def next_run_at(integration, from_moment=None):
    """When this row's next automatic run is due, or None when it is switched off."""
    if not integration["sync_enabled"] or integration["status"] != "active":
        return None
    return iso((from_moment or utc_now()) +
               timedelta(minutes=integration["sync_interval"] or 60))


def adapter_message(db, integration):
    """Run the adapter for one registry row and return what it reports."""
    if integration["integration_type"] == AD_TYPE:
        search_base = (integration["search_base"] or "").strip()
        if not search_base:
            raise ValueError(
                "Enter the Active Directory search base (for example "
                "OU=Staff,DC=example,DC=local) before synchronizing.")
        return synchronize_ad_users(db, integration, search_base)
    raise ValueError("No synchronization adapter exists for {}.".format(
        integration["integration_type"]))


def _directory_profile(db, integration):
    from services.directory import connection_by_integration
    return connection_by_integration(db, integration["id"])


def _directory_sync(db, profile, sync_type):
    """Preview and apply through the directory engine, which writes its own log."""
    from services.directory import sync_now
    result = sync_now(db, profile, None,
                      trigger="scheduled" if sync_type == "scheduled" else "manual")
    counts = result.get("counts") or {}
    problem = result.get("problem") or ""
    message = ("created {}, linked {}, updated {}, roles granted {}, disabled {},"
               " restored {}, errors {}{}".format(
                   counts.get("create", 0), counts.get("link", 0),
                   counts.get("update", 0), counts.get("granted", 0),
                   counts.get("disabled", 0), counts.get("restored", 0),
                   counts.get("errors", 0),
                   " - {}".format(problem) if problem else ""))
    return not problem, message[:500]


def run_integration_sync(db, integration, sync_type="manual"):
    """Execute and record one attempt; returns (ok, message).

    The caller supplies the registry row, so the screen that asked for a run
    cannot end up synchronizing something else. A row that owns a directory
    profile is carried by the directory engine, which writes the same shared
    sync log and error table and keeps its own preview/apply history.
    """
    if integration["status"] != "active":
        return False, "{} is not active, so there is nothing to synchronize.".format(
            integration["name"])

    profile = _directory_profile(db, integration)
    if profile is not None:
        return _directory_sync(db, profile, sync_type)

    cursor = db.execute("""
        INSERT INTO integration_sync_logs
            (integration_id, sync_type, status, message, started_at)
        VALUES (?, ?, 'running', 'Synchronization started.', ?)
    """, (integration["id"], sync_type, iso()))
    log_id = cursor.lastrowid
    db.commit()

    try:
        message = (adapter_message(db, integration) or "Synchronization completed.")[:500]
        completed_at = iso()
        db.execute("""
            UPDATE integration_sync_logs
            SET status = 'success', message = ?, completed_at = ? WHERE id = ?
        """, (message, completed_at, log_id))
        db.execute("""
            UPDATE integrations
            SET last_sync_at = ?, next_run_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (completed_at, next_run_at(integration), integration["id"]))
        db.commit()
        return True, message

    except Exception as exc:
        db.rollback()
        completed_at = iso()
        message = "{}: {}".format(type(exc).__name__, str(exc)[:300])
        db.execute("""
            UPDATE integration_sync_logs
            SET status = 'failed', message = ?, completed_at = ? WHERE id = ?
        """, (message, completed_at, log_id))
        db.execute("""
            INSERT INTO integration_errors (integration_id, error_type, error_message)
            VALUES (?, ?, ?)
        """, (integration["id"], type(exc).__name__, message))
        db.execute("UPDATE integrations SET next_run_at = ? WHERE id = ?",
                   (next_run_at(integration), integration["id"]))
        db.commit()
        logger.exception("Integration synchronization failed for ID %s",
                         integration["id"])
        return False, message


def run_due_synchronizations(db):
    """Synchronize everything enabled and due; returns how many ran."""
    due = due_integrations(db, iso())

    for integration in due:
        # The next slot is booked before the work starts: a run that hangs must
        # not be picked up again on the next tick, or one row would sync forever.
        db.execute("UPDATE integrations SET next_run_at = ? WHERE id = ?",
                   (next_run_at(integration), integration["id"]))
        db.commit()
        run_integration_sync(db, integration, sync_type="scheduled")
    return len(due)
