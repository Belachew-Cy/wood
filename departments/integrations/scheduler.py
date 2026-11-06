"""The background worker that runs due synchronizations.

Started with ``flask integration-scheduler`` in its own process, it asks the
database what is due every ``POLL_SECONDS`` and goes back to sleep - the same
shape as ``flask sync-directory``, so a deployment schedules both the same way.
A plain loop rather than a job library: the work is one query and a handful of
calls a minute, requirements.txt carries no scheduler package, and adding one
would mean installing a dependency before a screen that works today could be
reached.
"""
import logging
import time

from .synchronization import run_due_synchronizations

logger = logging.getLogger(__name__)

POLL_SECONDS = 30


def integration_scheduler_command():
    """``flask integration-scheduler`` - the worker beside backup-db."""
    import click
    from core.db import connect_db
    from .database import ensure_integrations_schema

    ensure_integrations_schema()
    click.echo("Integration scheduler running (every {} seconds).".format(
        POLL_SECONDS))
    db = connect_db()
    try:
        while True:
            try:
                from services.directory import sync_due_connections
                due = run_due_synchronizations(db)
                directory_runs = sync_due_connections(db)
                if due or directory_runs:
                    click.echo("{} integration(s), {} directory connection(s)"
                               " synchronized.".format(due, len(directory_runs)))
            except Exception as exc:  # A bad controller must not end the worker.
                db.rollback()
                logger.exception("Integration scheduler pass failed.")
                click.echo("Scheduler pass failed: {}".format(
                    type(exc).__name__))
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        click.echo("Integration scheduler stopped.")
    finally:
        db.close()
