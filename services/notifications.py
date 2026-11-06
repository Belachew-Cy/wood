"""Notification delivery and approval notifications."""

from core.access import effective_permissions
from core.config import DB_PATH
from core.config import POSITION_STEP_LEVEL
from core.config import STEP_LABEL
from core.db import connect_db
from datetime import datetime
from datetime import timezone
from flask import session
from flask import url_for
from services.audit import audit_time
from services.email import email_approval_step
from services.email import email_request_creator
import time


STANDING_NOTIFICATION_KEYS = ("stock-low", "procurement-waiting",
                              "payroll-pending", "leave-pending")


def deliver_notification(db, user_id, title, body="", href="", category="system",
                         icon="bell", tone="neutral", dedupe_key=None,
                         reopen=False):
    """Write one notification for one account, updating its standing twin."""
    title = (title or "").strip()[:180]
    if not user_id or not title:
        return None
    body = (body or "").strip()[:400]
    href = href or ""
    if dedupe_key:
        row = db.execute(
            "SELECT id, title, body, href FROM notifications"
            " WHERE user_id = ? AND dedupe_key = ?", (user_id, dedupe_key)
        ).fetchone()
        if row is not None:
            if reopen or (row["title"], row["body"], row["href"]) != (title, body, href):
                db.execute("""
                    UPDATE notifications
                    SET title = ?, body = ?, href = ?, category = ?, icon = ?,
                        tone = ?, read_at = NULL, created_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (title, body, href, category, icon, tone, row["id"]))
            return row["id"]
    return db.execute("""
        INSERT INTO notifications
            (user_id, category, title, body, href, icon, tone, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, category, title, body, href, icon, tone, dedupe_key)).lastrowid


def notify_right_holders(db, key, title, body="", href="", category="system",
                         icon="bell", tone="info", exclude_user_ids=(),
                         dedupe_prefix=None):
    """Fan one notice out to the seats that hold a named right.

    ``notify_permission`` spreads by module tick, which is too wide for a decision
    desk: the right that raises a record and the right that signs it sit on the same
    module, and a draft should wake the signer rather than everyone who may raise
    one. This walks the active accounts through ``has_permission`` instead.
    """
    from core.access import has_permission

    delivered = 0
    for account in db.execute(
            "SELECT * FROM users WHERE status = 'Active'").fetchall():
        if account["id"] in exclude_user_ids:
            continue
        if has_permission(key, user=account, db=db):
            deliver_notification(db, account["id"], title, body, href, category,
                                 icon, tone, dedupe_prefix)
            delivered += 1
    return delivered


def step_holder_accounts(db, step, exclude_user_ids=()):
    """Every active account that can sign at this desk, read once."""
    from services.workflow import holds_step
    return [account for account in
            db.execute("SELECT * FROM users WHERE status = 'Active'").fetchall()
            if account["id"] not in exclude_user_ids and holds_step(account, step)]


def notify_permission(db, module, title, body="", href="", category="system",
                      icon="bell", tone="info", exclude_user_ids=(),
                      dedupe_prefix=None):
    """Deliver to every active account holding a module."""
    delivered = 0
    for account in db.execute(
            "SELECT * FROM users WHERE status = 'Active'").fetchall():
        if account["id"] in exclude_user_ids:
            continue
        if module in effective_permissions(account):
            deliver_notification(db, account["id"], title, body, href, category,
                                 icon, tone, dedupe_prefix)
            delivered += 1
    return delivered


def notify_approval_step(db, request_id, step, title, request_type, amount,
                         exclude_user_ids=()):
    """Bell and mailbox for the desk that has to answer now."""
    label = STEP_LABEL.get(step, step)
    holders = step_holder_accounts(db, step, exclude_user_ids)
    for account in holders:
        deliver_notification(
            db, account["id"], "Approval waiting: {}".format(title),
            "{} · {:,.2f} ETB · the {} desk needs a decision.".format(
                request_type, float(amount), label),
            url_for("approvals.detail", request_id=request_id),
            category="approval", icon="clock", tone="warn",
            dedupe_key="approval-{}-{}".format(request_id, step), reopen=True)
    request_row = db.execute("SELECT * FROM approval_requests WHERE id = ?",
                             (request_id,)).fetchone()
    if request_row is not None and holders:
        email_approval_step(db, request_row, holders, step)


def notify_request_creator(db, request_row, title, body, tone="info",
                           icon="check", suffix="update"):
    deliver_notification(
        db, request_row["created_by"], title, body,
        url_for("approvals.detail", request_id=request_row["id"]),
        category="approval", icon=icon, tone=tone,
        dedupe_key="request-{}-{}".format(request_row["id"], suffix))
    email_request_creator(db, request_row, title, body, suffix)


def retire_approval_notifications(db, request_id):
    """A desk that has answered stops being asked: its alerts turn read."""
    db.execute("""
        UPDATE notifications SET read_at = COALESCE(read_at, ?)
        WHERE category = 'approval'
          AND dedupe_key LIKE ? AND read_at IS NULL
    """, (datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "approval-{}-%".format(request_id)))


def account_holds_step(account, granted, step, db=None):
    """holds_step() against an already-resolved permission set."""
    from services.workflow import account_position_level
    ceiling = POSITION_STEP_LEVEL.get(step)
    if ceiling is not None:
        if step in ("management", "ceo") and account["role"] == "admin":
            return True
        level = account_position_level(account, db)
        return level is not None and level <= ceiling
    return step in granted


def sync_notifications(user):
    """Refresh the standing alerts that follow a role, not an event.

    Runs on its own connection: a GET that renders a notification must never
    commit a write a route is still holding open. The bell is refreshed at most
    every twenty seconds per session, which keeps page rendering from turning
    into a write on every single click.
    """
    if user is None:
        return
    last = session.get("_notes_synced_at")
    try:
        last = float(last)
    except (TypeError, ValueError):
        last = 0.0
    now_ts = time.time()
    if last and now_ts - last < 20:
        return
    session["_notes_synced_at"] = now_ts
    granted = effective_permissions(user)
    conn = connect_db(DB_PATH)
    try:
        wanted = {}

        def want(key, title, body, href, category, icon, tone):
            wanted[key] = (title, body, href, category, icon, tone)

        if "inventory" in granted:
            low = conn.execute("""
                SELECT COUNT(*) AS n FROM inventory_items
                WHERE active = 1 AND quantity <= min_stock
            """).fetchone()["n"]
            if low:
                want("stock-low",
                     "{} item{} at or below reorder level".format(
                         low, "" if low == 1 else "s"),
                     "Stock levels need a decision.", url_for("inventory.dashboard"),
                     "inventory", "box", "bad")
        if "procurement" in granted:
            waiting = conn.execute("""
                SELECT COUNT(*) AS n FROM finance.procurement_requests
                WHERE status = 'Pending'
            """).fetchone()["n"]
            if waiting:
                want("procurement-waiting",
                     "{} purchase request{} waiting".format(
                         waiting, "" if waiting == 1 else "s"),
                     "Buying cannot start until these are answered.",
                     url_for("procurement.dashboard"), "procurement", "file", "warn")
        if "payroll" in granted:
            pending = conn.execute("""
                SELECT COUNT(*) AS n FROM payroll_records
                WHERE payment_status = 'Pending'
            """).fetchone()["n"]
            if pending:
                want("payroll-pending",
                     "{} payslip{} not paid yet".format(
                         pending, "" if pending == 1 else "s"),
                     "Mark them paid once the money has moved.",
                     url_for("payroll.payroll_dashboard"),
                     "payroll", "cash", "warn")
        if "hr" in granted:
            leave = conn.execute("""
                SELECT COUNT(*) AS n FROM leave_requests WHERE status = 'Pending'
            """).fetchone()["n"]
            if leave:
                want("leave-pending",
                     "{} leave request{} awaiting review".format(
                         leave, "" if leave == 1 else "s"),
                     "Approve or reject them from Leave Requests.",
                     url_for("leave_management"), "hr", "calendar", "warn")
        if granted & {"audit", "accounts"}:
            for row in conn.execute("""
                SELECT * FROM audit_logs
                WHERE outcome IN ('FAILURE', 'DENIED')
                  AND occurred_at >= datetime('now', '-7 days')
                ORDER BY id DESC LIMIT 2
            """).fetchall():
                want("audit-{}".format(row["id"]), row["description"],
                     "{} · {} · {}".format(row["username"], row["role"],
                                           row["module"]),
                     url_for("audit.audit_dashboard", outcome=row["outcome"]),
                     "audit", "lock", "bad")

        # An approval alert follows the current step, so a desk that has signed
        # stops seeing it and the next desk starts.
        acted = {row["request_id"] for row in conn.execute(
            "SELECT DISTINCT request_id FROM approval_actions WHERE actor_id = ?",
            (user["id"],))}
        pending = conn.execute("""
            SELECT * FROM approval_requests WHERE status = 'Pending'
            ORDER BY updated_at DESC LIMIT 100
        """).fetchall()
        for row in pending:
            step = row["current_approver_step"]
            if row["created_by"] == user["id"] or row["id"] in acted:
                continue
            if not account_holds_step(user, granted, step, conn):
                continue
            want("approval-{}-{}".format(row["id"], step),
                 "Approval waiting: {}".format(row["title"]),
                 "{} · {:,.2f} ETB · the {} desk needs a decision.".format(
                     row["request_type"], float(row["amount"]),
                     STEP_LABEL.get(step, step)),
                 url_for("approvals.detail", request_id=row["id"]),
                 "approval", "clock", "warn")

        existing = {row["dedupe_key"]: row for row in conn.execute(
            "SELECT id, dedupe_key, title, body, href FROM notifications"
            " WHERE user_id = ? AND dedupe_key IS NOT NULL", (user["id"],))}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for key, (title, body, href, category, icon, tone) in wanted.items():
            row = existing.get(key)
            if row is None:
                conn.execute("""
                    INSERT INTO notifications
                        (user_id, category, title, body, href, icon, tone, dedupe_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (user["id"], category, title, body, href, icon, tone, key))
            elif (row["title"], row["body"], row["href"]) != (title, body, href):
                conn.execute("""
                    UPDATE notifications
                    SET title = ?, body = ?, href = ?, category = ?, icon = ?,
                        tone = ?, read_at = NULL, created_at = ?
                    WHERE id = ?
                """, (title, body, href, category, icon, tone, now, row["id"]))
        for key, row in existing.items():
            if key in wanted:
                continue
            if key in STANDING_NOTIFICATION_KEYS or key.startswith("audit-"):
                conn.execute("DELETE FROM notifications WHERE id = ?", (row["id"],))
            elif key.startswith("approval-"):
                conn.execute("UPDATE notifications SET read_at = COALESCE(read_at, ?)"
                             " WHERE id = ?", (now, row["id"]))
        conn.commit()
    finally:
        conn.close()


def notification_summary(db, user):
    """The bell: newest unread items first, plus the unread total."""
    if user is None:
        return [], 0
    sync_notifications(user)
    rows = db.execute("""
        SELECT * FROM notifications WHERE user_id = ?
        ORDER BY CASE WHEN read_at IS NULL THEN 0 ELSE 1 END,
                 created_at DESC, id DESC LIMIT 8
    """, (user["id"],)).fetchall()
    unread = db.execute("""
        SELECT COUNT(*) AS n FROM notifications
        WHERE user_id = ? AND read_at IS NULL
    """, (user["id"],)).fetchone()["n"]
    items = [{
        "id": row["id"], "title": row["title"], "meta": row["body"],
        "icon": row["icon"] or "bell", "tone": row["tone"] or "neutral",
        "time": audit_time(row["created_at"]),
        "href": url_for("open_notification", notification_id=row["id"]),
        "unread": row["read_at"] is None, "category": row["category"],
    } for row in rows]
    return items, unread
