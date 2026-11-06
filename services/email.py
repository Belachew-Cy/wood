"""Outbound email: recipient resolution, SMTP delivery and the delivery log.

Every address comes from User Master Data: the account row first, then the HR
staff record it is linked to. Nothing is typed by hand at the call site. Each
attempt, successful or not, lands in email_deliveries on its own connection, so a
slow mailbox can never hold a route's transaction open.
"""

from core.config import APP_BASE_URL
from core.config import EMPLOYEE_EMAIL_RE
from core.config import MAIL_FROM
from core.config import MAIL_FROM_NAME
from core.config import MAIL_HOST
from core.config import MAIL_PASSWORD
from core.config import MAIL_PORT
from core.config import MAIL_SECURITY
from core.config import MAIL_SIGNATURE
from core.config import MAIL_TIMEOUT_SECONDS
from core.config import MAIL_USERNAME
from core.config import RESET_TOKEN_MINUTES
from core.config import STEP_LABEL
from core.db import connect_db
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.message import EmailMessage
from email.utils import formataddr
from email.utils import formatdate
from email.utils import make_msgid
from flask import current_app
from flask import g
from flask import has_app_context
from flask import has_request_context
from flask import url_for
from urllib.parse import urlsplit
import hashlib
import logging
import secrets
import smtplib


log = logging.getLogger(__name__)


REQUIRED_SETTINGS = (("WK_MAIL_HOST", MAIL_HOST),
                     ("WK_MAIL_FROM", MAIL_FROM))


def mail_ready():
    """True only when a host and a sender address are configured."""
    return bool(MAIL_HOST and MAIL_FROM)


def mail_status():
    return {
        "ready": mail_ready(),
        "host": MAIL_HOST,
        "port": MAIL_PORT,
        "security": MAIL_SECURITY,
        "sender": MAIL_FROM,
        "sender_name": MAIL_FROM_NAME,
        "missing": [name for name, value in REQUIRED_SETTINGS if not value],
    }


def clean_address(value):
    """The address as it should be used, or "" when it is not a mailbox."""
    address = (value or "").strip().lower()
    if len(address) > 254 or not EMPLOYEE_EMAIL_RE.match(address):
        return ""
    return address


def account_address(db, account):
    """(address, reason) for one account, read from User Master Data.

    The account's own address wins. A blank or malformed one falls back to the HR
    record the account is linked to, which is the other copy of the same person's
    mailbox. A disabled account is never mailed.
    """
    if account is None:
        return "", "No account on record"
    if account["status"] != "Active":
        return "", "The account is disabled"
    address = clean_address(account["email"])
    if address:
        return address, ""
    employee_id = account["employee_id"] if "employee_id" in account.keys() else None
    if employee_id:
        row = db.execute("SELECT email FROM employees WHERE id = ?",
                         (employee_id,)).fetchone()
        if row is not None:
            fallback = clean_address(row["email"])
            if fallback:
                return fallback, ""
    return "", "No valid email address on the account"


def absolute_url(path):
    """Pin a link to the configured site address, or leave the request's own."""
    if not APP_BASE_URL or not path:
        return path
    parts = urlsplit(path)
    if not parts.scheme and not parts.netloc:
        return APP_BASE_URL + (path if path.startswith("/") else "/" + path)
    suffix = parts.path
    if parts.query:
        suffix += "?" + parts.query
    return APP_BASE_URL + suffix


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_connection():
    return connect_db()


def _record_delivery(user_id, recipient, subject, category, event_key, status,
                     error="", related_type=None, related_id=None):
    """Write one attempt to the delivery log on a connection of its own.

    A failure to log never takes the message or the route down with it.
    """
    conn = None
    try:
        conn = _log_connection()
        conn.execute("""
            INSERT INTO email_deliveries (
                user_id, recipient, subject, category, event_key, status, error,
                related_type, related_id, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, recipient[:254], subject[:180], category[:40],
              event_key, status, (error or "")[:400],
              related_type, None if related_id is None else str(related_id),
              _now(), _now()))
        conn.commit()
    except Exception as problem:  # the log must never break the caller
        log.warning("Could not record the email delivery: %s", problem)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _already_sent(recipient, event_key):
    if not event_key:
        return False
    conn = None
    try:
        conn = _log_connection()
        row = conn.execute("""
            SELECT 1 FROM email_deliveries
            WHERE recipient = ? AND event_key = ? AND status = 'Sent'
            LIMIT 1
        """, (recipient, event_key)).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _compose(account, intro, details, action_url, action_label):
    name = (account["full_name"] or "").split(" ")[0].strip() or "there"
    lines = ["Hello {},".format(name), "", intro.strip(), ""]
    for item in details:
        lines.append(str(item))
    if details:
        lines.append("")
    if action_url:
        lines.append(action_label + ":")
        lines.append(action_url)
        lines.append("")
    lines += ["--", MAIL_SIGNATURE,
              "This message was sent automatically. Please do not reply."]
    return "\n".join(lines)


def _transmit(address, subject, body):
    """Hand one message to the SMTP server. Returns (ok, error)."""
    message = EmailMessage()
    message["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM))
    message["To"] = address
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    domain = MAIL_FROM.rsplit("@", 1)[-1] if "@" in MAIL_FROM else None
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(body)
    server = None
    try:
        if MAIL_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT,
                                      timeout=MAIL_TIMEOUT_SECONDS)
        else:
            server = smtplib.SMTP(MAIL_HOST, MAIL_PORT,
                                  timeout=MAIL_TIMEOUT_SECONDS)
            server.ehlo()
            if MAIL_SECURITY == "starttls":
                server.starttls()
                server.ehlo()
        if MAIL_USERNAME:
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(message)
        return True, ""
    except (smtplib.SMTPException, OSError, ValueError) as problem:
        return False, "{}: {}".format(type(problem).__name__, problem)
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def _report(status, address, subject, error=""):
    if status == "Failed":
        if has_app_context():
            current_app.logger.warning("Email to %s failed: %s", address, error)
        else:
            log.warning("Email to %s failed: %s", address, error)


def _pending_jobs():
    jobs = getattr(g, "_pending_emails", None)
    if jobs is None:
        jobs = []
        g._pending_emails = jobs
    return jobs


def _deliver_job(job):
    """Send one prepared message and log what happened."""
    if job["skip"]:
        _record_delivery(job["user_id"], job["address"], job["subject"],
                         job["category"], job["event_key"], "Skipped",
                         job["skip"], job["related_type"], job["related_id"])
        return "skipped"
    ok, error = _transmit(job["address"], job["subject"], job["body"])
    _record_delivery(job["user_id"], job["address"], job["subject"],
                     job["category"], job["event_key"],
                     "Sent" if ok else "Failed", error,
                     job["related_type"], job["related_id"])
    if not ok:
        _report("Failed", job["address"], job["subject"], error)
        return "failed"
    return "sent"


def flush_pending_emails():
    """Send the mail a request queued, once that request has committed.

    Mail leaves after the page's own write is on disk, so a slow mailbox never
    holds the database write lock and never turns a saved record into an error.
    """
    if not has_request_context():
        return
    jobs = getattr(g, "_pending_emails", None)
    if not jobs:
        return
    g._pending_emails = []
    for job in jobs:
        try:
            _deliver_job(job)
        except Exception as problem:  # a mailbox must never break the response
            log.warning("Could not send a queued email: %s", problem)


def send_accounts(db, accounts, subject, intro, details=(), event_key=None,
                  category="system", exclude_ids=(), action_url="",
                  action_label="Open in the system", related_type=None,
                  related_id=None):
    """Mail every listed account, skipping anyone without a usable address.

    Inside a request the message is queued and sent once the route commits;
    outside one it goes immediately. The same event to the same mailbox is sent
    once, so a repeated call after a refresh is a duplicate, not a second alert.
    """
    subject = " ".join(str(subject or "").split())[:180]
    if not subject or not intro:
        return {"sent": 0, "failed": 0, "skipped": 0, "duplicate": 0, "queued": 0}
    summary = {"sent": 0, "failed": 0, "skipped": 0, "duplicate": 0, "queued": 0}
    queued = has_request_context()
    pending = _pending_jobs() if queued else []
    claimed = {(job["address"], job["event_key"]) for job in pending
               if job["address"] and job["event_key"]}
    seen = set()
    for account in accounts or ():
        if account is None or account["id"] in seen:
            continue
        seen.add(account["id"])
        if account["id"] in exclude_ids:
            continue
        address, reason = account_address(db, account)
        job = {
            "user_id": account["id"], "address": address, "subject": subject,
            "category": category, "event_key": event_key, "body": "",
            "skip": "", "related_type": related_type, "related_id": related_id,
        }
        if not address:
            job["skip"] = reason
        elif ((address, event_key) in claimed
              or _already_sent(address, event_key)):
            summary["duplicate"] += 1
            continue
        elif not mail_ready():
            job["skip"] = "Mail is not configured on this site"
        else:
            job["body"] = _compose(account, intro, details, action_url,
                                   action_label)
        if not queued:
            summary[_deliver_job(job)] += 1
            continue
        if job["skip"]:
            summary["skipped"] += 1
        pending.append(job)
        if address and event_key:
            claimed.add((address, event_key))
        summary["queued"] += 1
    return summary


def _action_marker(db, request_id):
    """The id of the newest action, which changes with every stage decision."""
    row = db.execute("SELECT COALESCE(MAX(id), 0) AS marker FROM approval_actions"
                     " WHERE request_id = ?", (request_id,)).fetchone()
    return row["marker"] if row is not None else 0


def request_link(request_row):
    """Where a request lives: its own module screen when a module raised it."""
    table = (request_row["source_table"]
             if "source_table" in request_row.keys() else None)
    source_id = (request_row["source_id"]
                 if "source_id" in request_row.keys() else None)
    if table == "procurement_requests" and source_id:
        return url_for("procurement.requisition_detail", request_id=source_id,
                       _external=True)
    return url_for("approvals.detail", request_id=request_row["id"], _external=True)


def email_approval_step(db, request_row, accounts, step):
    """Tell the desk that has to answer now that the request is with them."""
    label = STEP_LABEL.get(step, step)
    amount = float(request_row["amount"] or 0)
    is_requisition = (request_row["source_table"] == "procurement_requests"
                      if "source_table" in request_row.keys() else False)
    noun = "Requisition" if is_requisition else request_row["request_type"]
    subject = "Approval waiting: {}".format(request_row["title"])
    intro = ("{} \"{}\" is waiting for your decision at the {} desk.".format(
        noun, request_row["title"], label))
    details = ["Raised by: {}".format(request_row["created_by_name"] or "a colleague"),
               "Amount: {:,.2f} ETB".format(amount),
               "Stage: {}".format(label)]
    return send_accounts(
        db, accounts, subject, intro, details,
        event_key="approval-{}-{}-{}".format(request_row["id"], step,
                                             _action_marker(db, request_row["id"])),
        category="approval", action_url=absolute_url(request_link(request_row)),
        action_label="Open the request", related_type="approval_request",
        related_id=request_row["id"])


def email_request_creator(db, request_row, title, body, suffix):
    """Carry each decision back to the person who raised the request."""
    creator = db.execute("SELECT * FROM users WHERE id = ?",
                         (request_row["created_by"],)).fetchone()
    if creator is None:
        return {"sent": 0, "failed": 0, "skipped": 0, "duplicate": 0}
    is_requisition = (request_row["source_table"] == "procurement_requests"
                      if "source_table" in request_row.keys() else False)
    details = ["Request: {}".format(request_row["title"]),
               "Amount: {:,.2f} ETB".format(float(request_row["amount"] or 0))]
    if is_requisition:
        details.append("Reference: your purchase requisition")
    return send_accounts(
        db, [creator], "{}: {}".format(title, request_row["title"]), body, details,
        event_key="request-{}-{}-{}".format(request_row["id"], suffix,
                                            _action_marker(db, request_row["id"])),
        category="approval", action_url=absolute_url(request_link(request_row)),
        action_label="Open the request", related_type="approval_request",
        related_id=request_row["id"])


def email_request_receipt(db, request_row, raised_by):
    """A receipt for the requester the moment the chain opens."""
    details = ["Request: {}".format(request_row["title"]),
               "Type: {}".format(request_row["request_type"]),
               "Amount: {:,.2f} ETB".format(float(request_row["amount"] or 0)),
               "First desk: {}".format(STEP_LABEL.get(
                   request_row["current_approver_step"],
                   request_row["current_approver_step"]))]
    return send_accounts(
        db, [raised_by], "Request received: {}".format(request_row["title"]),
        "Your request has been submitted and is on its approval route.",
        details,
        event_key="request-{}-received".format(request_row["id"]),
        category="approval", action_url=absolute_url(request_link(request_row)),
        action_label="Open the request", related_type="approval_request",
        related_id=request_row["id"])


def send_password_reset(db, user, ip=""):
    """Issue one single-use reset link and email it to the account's mailbox.

    Returns (status, reason) where status is "sent", "skipped" or "failed". Only
    the link is mailed - never a password - and only its digest is stored.
    """
    # The caller's request row is committed first: the log write and the SMTP
    # call below must not run while this connection still holds the write lock.
    db.commit()
    address, reason = account_address(db, user)
    if not address:
        _record_delivery(user["id"], "", "Reset your Wood & Kitchen password",
                         "security", None, "Skipped", reason, "password_reset",
                         user["id"])
        return "skipped", reason
    if not mail_ready():
        _record_delivery(user["id"], address, "Reset your Wood & Kitchen password",
                         "security", None, "Skipped",
                         "Mail is not configured on this site",
                         "password_reset", user["id"])
        return "skipped", "Mail is not configured on this site"
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = now + timedelta(minutes=RESET_TOKEN_MINUTES)
    db.execute("""
        UPDATE password_reset_tokens SET used_at = ?
        WHERE user_id = ? AND used_at IS NULL
    """, (now.isoformat(timespec="seconds"), user["id"]))
    cursor = db.execute("""
        INSERT INTO password_reset_tokens
            (user_id, token_hash, requested_ip, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user["id"], digest, (ip or "")[:64],
          now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")))
    db.commit()
    url = absolute_url(url_for("reset_password", token=token, _external=True))
    body = "\n".join([
        "Hello {},", "",
        "A password reset was requested for your account at {}.".format(
            MAIL_SIGNATURE),
        "",
        "Choose a new password here:",
        url,
        "",
        "The link can be used once and expires in {} minutes. If you did not ask"
        " for this, ignore the message - your password stays as it is.".format(
            RESET_TOKEN_MINUTES),
        "",
        "--", MAIL_SIGNATURE,
        "This message was sent automatically. Please do not reply.",
    ])
    subject = "Reset your Wood & Kitchen password"
    ok, error = _transmit(address, subject, body)
    _record_delivery(user["id"], address, subject, "security",
                     "reset-{}".format(cursor.lastrowid),
                     "Sent" if ok else "Failed", error,
                     "password_reset", cursor.lastrowid)
    if not ok:
        _report("Failed", address, subject, error)
        return "failed", error
    return "sent", ""


def reset_token_row(db, token):
    """The live token row for a link, or None when it is spent, old or unknown."""
    digest = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    row = db.execute("""
        SELECT t.*, u.full_name, u.email, u.role, u.status, u.password_hash
        FROM password_reset_tokens AS t
        JOIN users AS u ON u.id = t.user_id
        WHERE t.token_hash = ?
    """, (digest,)).fetchone()
    if row is None or row["used_at"] or row["status"] != "Active":
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        return None
    return row if expires > datetime.now(timezone.utc) else None


def delivery_summary(db, days=30):
    """Sent, failed and skipped counts for the Settings card."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    counts = {"Sent": 0, "Failed": 0, "Skipped": 0}
    for row in db.execute("""
        SELECT status, COUNT(*) AS n FROM email_deliveries
        WHERE finished_at >= ? GROUP BY status
    """, (cutoff,)).fetchall():
        if row["status"] in counts:
            counts[row["status"]] = row["n"]
    return counts


def recent_deliveries(db, limit=8):
    return db.execute("""
        SELECT * FROM email_deliveries ORDER BY id DESC LIMIT ?
    """, (int(limit),)).fetchall()
