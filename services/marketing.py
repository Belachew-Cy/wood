"""Marketing plans, campaigns, leads, research, digital pushes and feedback.

The Marketing desk keeps its own file, one row per record, exactly like Budget and
Procurement: a plan carries campaigns, a campaign carries the digital pushes that
run inside it, a lead records who showed interest, and feedback records what a
customer said afterwards. Nothing here opens a second registry of the people the
company already knows - a customer is read from the invoices the Finance desk
already keeps, so Marketing never disagrees with what was billed.

Every write runs through the validators below, so a direct URL, a forged POST and
a form button are answered by the same code and refuse in the same words. A
marketing budget is a plan, not a spend, so nothing here touches an allocation.
"""

from core.config import EMPLOYEE_EMAIL_RE
from core.config import EMPLOYEE_PHONE_RE
from core.db import check_in
from core.db import connect_db
from core.validation import clean_amount
from core.validation import clean_choice
from core.validation import clean_date
from core.validation import clean_status
from core.validation import clean_text
from datetime import date


# ---------------------------------------------------------------- vocabularies
# One tuple per choice list. The screens draw their selects from these, the DDL
# repeats the same words in a CHECK, and anything else is refused before it
# reaches SQLite, so a stale browser tab cannot invent a status.
PLAN_STATUSES = ("Draft", "Pending", "Approved", "Rejected", "Closed")
CAMPAIGN_STATUSES = ("Planned", "Active", "On Hold", "Completed", "Cancelled")
DIGITAL_STATUSES = ("Planned", "Live", "Paused", "Completed", "Cancelled")
LEAD_STATUSES = ("New", "Contacted", "Qualified", "Converted", "Lost")
FEEDBACK_STATUSES = ("Pending", "In Progress", "Resolved", "Closed")
FEEDBACK_TYPES = ("Complaint", "Inquiry", "Compliment", "Suggestion")
RESEARCH_TYPES = ("Market Study", "Competitor Analysis", "Customer Survey",
                  "Price Study", "Trend Report", "Other")
CHANNELS = ("Showroom", "Referral", "Contractor", "Event", "Print", "Broadcast",
            "Online", "Other")
LEAD_SOURCES = ("Walk-in", "Phone", "Website", "Social Media", "Referral",
                "Exhibition", "Contractor", "Other")
DIGITAL_PLATFORMS = ("Facebook", "Instagram", "Telegram", "TikTok", "LinkedIn",
                     "YouTube", "Website", "SMS", "Other")
MARKETING_CURRENCIES = ("ETB", "USD", "EUR", "GBP")


# ------------------------------------------------------------------- the tables
# SQLite keeps a CHECK inside the definition, so widening a vocabulary means
# rebuilding the table the way the vacancy desk does; nothing here is edited by
# hand. Dates are ISO text and amounts are NUMERIC, as everywhere else.
PLAN_DDL = """
    CREATE TABLE IF NOT EXISTS marketing_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        budget NUMERIC NOT NULL DEFAULT 0 CHECK (budget >= 0),
        currency TEXT NOT NULL DEFAULT 'ETB' {currency},
        status TEXT NOT NULL DEFAULT 'Draft' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (end_date >= start_date)
    )
""".format(currency=check_in("currency", MARKETING_CURRENCIES),
           status=check_in("status", PLAN_STATUSES))


CAMPAIGN_DDL = """
    CREATE TABLE IF NOT EXISTS marketing_campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL DEFAULT '' {channel},
        target_audience TEXT NOT NULL DEFAULT '',
        budget NUMERIC NOT NULL DEFAULT 0 CHECK (budget >= 0),
        currency TEXT NOT NULL DEFAULT 'ETB' {currency},
        start_date TEXT,
        end_date TEXT,
        status TEXT NOT NULL DEFAULT 'Planned' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((start_date IS NULL OR end_date IS NULL)
               OR end_date >= start_date),
        FOREIGN KEY (plan_id) REFERENCES marketing_plans(id)
    )
""".format(channel=check_in("channel", CHANNELS, blank=True),
           currency=check_in("currency", MARKETING_CURRENCIES),
           status=check_in("status", CAMPAIGN_STATUSES))


LEAD_DDL = """
    CREATE TABLE IF NOT EXISTS marketing_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        product_interest TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '' {source},
        notes TEXT NOT NULL DEFAULT '',
        assigned_to INTEGER,
        assigned_to_name TEXT NOT NULL DEFAULT '',
        follow_up_date TEXT,
        status TEXT NOT NULL DEFAULT 'New' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (name, phone)
    )
""".format(source=check_in("source", LEAD_SOURCES, blank=True),
           status=check_in("status", LEAD_STATUSES))


RESEARCH_DDL = """
    CREATE TABLE IF NOT EXISTS market_research (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        research_type TEXT NOT NULL DEFAULT '' {kind},
        market_segment TEXT NOT NULL DEFAULT '',
        findings TEXT NOT NULL DEFAULT '',
        research_date TEXT,
        prepared_by INTEGER,
        prepared_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
""".format(kind=check_in("research_type", RESEARCH_TYPES, blank=True))


DIGITAL_DDL = """
    CREATE TABLE IF NOT EXISTS digital_campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER,
        name TEXT NOT NULL,
        platform TEXT NOT NULL DEFAULT '' {platform},
        content TEXT NOT NULL DEFAULT '',
        start_date TEXT,
        end_date TEXT,
        budget NUMERIC NOT NULL DEFAULT 0 CHECK (budget >= 0),
        currency TEXT NOT NULL DEFAULT 'ETB' {currency},
        status TEXT NOT NULL DEFAULT 'Planned' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK ((start_date IS NULL OR end_date IS NULL)
               OR end_date >= start_date),
        FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns(id)
    )
""".format(platform=check_in("platform", DIGITAL_PLATFORMS, blank=True),
           currency=check_in("currency", MARKETING_CURRENCIES),
           status=check_in("status", DIGITAL_STATUSES))


FEEDBACK_DDL = """
    CREATE TABLE IF NOT EXISTS customer_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT NOT NULL DEFAULT '',
        contact TEXT NOT NULL DEFAULT '',
        feedback_type TEXT NOT NULL DEFAULT 'Inquiry' {kind},
        description TEXT NOT NULL,
        rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
        assigned_to INTEGER,
        assigned_to_name TEXT NOT NULL DEFAULT '',
        resolution TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Pending' {status},
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
""".format(kind=check_in("feedback_type", FEEDBACK_TYPES),
           status=check_in("status", FEEDBACK_STATUSES))


MARKETING_DDL = (PLAN_DDL, CAMPAIGN_DDL, LEAD_DDL, RESEARCH_DDL, DIGITAL_DDL,
                 FEEDBACK_DDL)


INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_marketing_plan_status"
    " ON marketing_plans(status, start_date)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_campaign_plan"
    " ON marketing_campaigns(plan_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_campaign_status"
    " ON marketing_campaigns(status, start_date)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_lead_status"
    " ON marketing_leads(status, follow_up_date)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_lead_assigned"
    " ON marketing_leads(assigned_to)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_research_date"
    " ON market_research(research_date)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_digital_campaign"
    " ON digital_campaigns(campaign_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_feedback_status"
    " ON customer_feedback(status, created_at)",
)


def ensure_marketing_schema():
    """Create or top up every marketing table. Safe to call on every boot."""
    db = connect_db()
    try:
        for statement in MARKETING_DDL:
            db.execute(statement)
        for statement in INDEX_STATEMENTS:
            db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ------------------------------------------------------------------ validators
# Text, choice, status, amount and date are the house rules in
# core/validation.py, which the project register shares; only the
# marketing-shaped rules are kept below.


def clean_period(start, end, end_required=True, start_required=True):
    """Both ends of a window, in order.

    A plan always names its period; a campaign or a push can be registered
    before the dates are fixed, so either end may be released.
    """
    first = clean_date(start, "Start date", required=start_required)
    second = clean_date(end, "End date", required=end_required)
    if first and second is not None and second < first:
        raise ValueError("End date cannot be before the start date.")
    return first, second


def clean_email(value, field="Email", required=False):
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("{} is required.".format(field))
        return ""
    if len(text) > 150 or EMPLOYEE_EMAIL_RE.fullmatch(text) is None:
        raise ValueError("{} is not a valid email address.".format(field))
    return text


def clean_phone(value, field="Phone", required=False):
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("{} is required.".format(field))
        return ""
    digits = "".join(character for character in text if character.isdigit())
    if not (7 <= len(digits) <= 15):
        raise ValueError("{} is not a valid phone number.".format(field))
    if EMPLOYEE_PHONE_RE.fullmatch(text) is None:
        raise ValueError(
            "{} may use digits, one leading + and spaces or dashes only."
            .format(field))
    return text


def clean_rating(value):
    text = (value or "").strip()
    if not text:
        return None
    try:
        rating = int(text)
    except ValueError:
        raise ValueError("Rating must be a whole number from 1 to 5.")
    if rating < 1 or rating > 5:
        raise ValueError("Rating must be between 1 and 5.")
    return rating


def clean_account(value, field="Assigned account"):
    """An account id from a select, or None when the desk left it unassigned."""
    text = (value or "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError("{} must be one of the listed accounts.".format(field))
    return int(text)


def account_name(db, account_id):
    """The name to carry beside an assignment, so a list needs no extra join."""
    if account_id is None:
        return ""
    row = db.execute("SELECT full_name FROM users WHERE id = ?",
                     (account_id,)).fetchone()
    if row is None:
        raise ValueError("The selected account is no longer on the register.")
    return row["full_name"]


# --------------------------------------------------------------- reading rows
# One SELECT per desk, with the join that gives the screen its words. The list,
# the detail page and the export read through these so they cannot disagree.
PLAN_SELECT = """
    SELECT p.*,
           (SELECT COUNT(*) FROM marketing_campaigns c
             WHERE c.plan_id = p.id) AS campaign_count,
           (SELECT COALESCE(SUM(c.budget), 0) FROM marketing_campaigns c
             WHERE c.plan_id = p.id AND c.status <> 'Cancelled')
             AS committed_budget
    FROM marketing_plans p
"""

CAMPAIGN_SELECT = """
    SELECT c.*, p.title AS plan_title, p.status AS plan_status,
           (SELECT COUNT(*) FROM digital_campaigns d
             WHERE d.campaign_id = c.id) AS digital_count
    FROM marketing_campaigns c
    LEFT JOIN marketing_plans p ON p.id = c.plan_id
"""

LEAD_SELECT = """
    SELECT l.*, s.full_name AS creator_name
    FROM marketing_leads l
    LEFT JOIN users s ON s.id = l.created_by
"""

RESEARCH_SELECT = "SELECT r.* FROM market_research r"

DIGITAL_SELECT = """
    SELECT d.*, c.name AS campaign_name, c.status AS campaign_status
    FROM digital_campaigns d
    LEFT JOIN marketing_campaigns c ON c.id = d.campaign_id
"""

FEEDBACK_SELECT = """
    SELECT f.*, s.full_name AS assigned_name
    FROM customer_feedback f
    LEFT JOIN users s ON s.id = f.assigned_to
"""

# A customer is whoever the Finance desk has already billed, so the marketing
# screen reads that registry instead of keeping a second list of the same people.
CUSTOMER_SELECT = """
    SELECT i.party_name AS name,
           MAX(i.party_contact) AS contact,
           COUNT(*) AS invoice_count,
           COALESCE(SUM(i.total_amount), 0) AS total_amount,
           COALESCE(SUM(i.total_etb), 0) AS total_etb,
           MIN(i.issue_date) AS first_order,
           MAX(i.issue_date) AS last_order,
           SUM(CASE WHEN i.status = 'Unpaid' THEN 1 ELSE 0 END)
               AS open_invoices
    FROM finance_invoices i
    WHERE i.invoice_type = 'customer'
    GROUP BY i.party_name
"""


def _like_terms(search, columns):
    """A parenthesised LIKE block over several columns, plus its parameters.

    The backslash is declared as the escape, so a percent typed into the search
    box finds a percent instead of matching everything.
    """
    needle = "%{}%".format(
        (search or "").replace("\\", "\\\\").replace("%", "\\%")
        .replace("_", "\\_")).lower()
    clause = " OR ".join("LOWER({}) LIKE ? ESCAPE '\\'".format(column)
                         for column in columns)
    return "({})".format(clause), [needle] * len(columns)


def marketing_rows(db, select, where=None, params=(), order=None, limit=None):
    """One of the SELECTs above, narrowed and ordered for a screen."""
    statement = select
    if where:
        statement = "{} WHERE {}".format(statement, where)
    if order:
        statement = "{} ORDER BY {}".format(statement, order)
    if limit:
        statement = "{} LIMIT ?".format(statement)
        params = tuple(params) + (int(limit),)
    return db.execute(statement, params).fetchall()


def marketing_row(db, table, record_id):
    """One row by id, or None - an id arrives from the URL and may be stale."""
    return db.execute("SELECT * FROM {} WHERE id = ?".format(table),
                      (record_id,)).fetchone()


def plan_rows(db, status=None, search=None):
    where, params = [], []
    if status:
        where.append("p.status = ?")
        params.append(status)
    if search:
        clause, terms = _like_terms(search, ("p.title", "p.description"))
        where.append(clause)
        params += terms
    return marketing_rows(db, PLAN_SELECT, " AND ".join(where) or None, params,
                          "p.start_date DESC, p.id DESC")


def campaign_rows(db, status=None, plan_id=None, search=None):
    where, params = [], []
    if status:
        where.append("c.status = ?")
        params.append(status)
    if plan_id:
        where.append("c.plan_id = ?")
        params.append(plan_id)
    if search:
        clause, terms = _like_terms(
            search, ("c.name", "c.target_audience", "c.channel"))
        where.append(clause)
        params += terms
    return marketing_rows(db, CAMPAIGN_SELECT, " AND ".join(where) or None,
                          params, "c.id DESC")


def lead_rows(db, status=None, search=None, follow_up=None):
    where, params = [], []
    if status:
        where.append("l.status = ?")
        params.append(status)
    today = date.today().isoformat()
    if follow_up == "overdue":
        where.append("l.follow_up_date IS NOT NULL AND l.follow_up_date < ?"
                     " AND l.status NOT IN ('Converted', 'Lost')")
        params.append(today)
    elif follow_up == "today":
        where.append("l.follow_up_date = ?")
        params.append(today)
    if search:
        clause, terms = _like_terms(search, ("l.name", "l.phone", "l.email"))
        where.append(clause)
        params += terms
    return marketing_rows(db, LEAD_SELECT, " AND ".join(where) or None, params,
                          "l.id DESC")


def research_rows(db, search=None):
    clause, params = (None, ())
    if search:
        clause, params = _like_terms(
            search, ("r.title", "r.market_segment", "r.findings"))
    return marketing_rows(db, RESEARCH_SELECT, clause, params,
                          "r.research_date DESC, r.id DESC")


def digital_rows(db, status=None, search=None):
    where, params = [], []
    if status:
        where.append("d.status = ?")
        params.append(status)
    if search:
        clause, terms = _like_terms(
            search, ("d.name", "d.content", "d.platform"))
        where.append(clause)
        params += terms
    return marketing_rows(db, DIGITAL_SELECT, " AND ".join(where) or None,
                          params, "d.id DESC")


def feedback_rows(db, status=None, search=None):
    where, params = [], []
    if status:
        where.append("f.status = ?")
        params.append(status)
    if search:
        clause, terms = _like_terms(
            search, ("f.customer_name", "f.description", "f.contact"))
        where.append(clause)
        params += terms
    return marketing_rows(db, FEEDBACK_SELECT, " AND ".join(where) or None,
                          params, "f.id DESC")


def customer_rows(db, search=None):
    rows = marketing_rows(db, CUSTOMER_SELECT, None, (), "total_etb DESC")
    if search:
        needle = search.lower()
        rows = [row for row in rows if needle in (row["name"] or "").lower()]
    return rows


def plan_options(db):
    """Plans a campaign may hang from: anything not refused or closed."""
    return db.execute(
        "SELECT id, title, status FROM marketing_plans"
        " WHERE status IN ('Draft', 'Pending', 'Approved')"
        " ORDER BY start_date DESC, id DESC").fetchall()


def campaign_options(db):
    return db.execute(
        "SELECT id, name, status FROM marketing_campaigns"
        " WHERE status IN ('Planned', 'Active', 'On Hold')"
        " ORDER BY id DESC").fetchall()


# ------------------------------------------------------------------- figures
def marketing_summary(db):
    """The figures every marketing screen is built around, in one read."""
    counts = {}
    for table in ("marketing_plans", "marketing_campaigns", "marketing_leads",
                  "digital_campaigns", "customer_feedback"):
        counts[table] = {
            row["status"]: row["total"]
            for row in db.execute(
                "SELECT status, COUNT(*) AS total FROM {}"
                " GROUP BY status".format(table))}
    totals = db.execute("""
        SELECT (SELECT COALESCE(SUM(budget), 0) FROM marketing_plans
                 WHERE status IN ('Approved', 'Closed')) AS plan_budget,
               (SELECT COALESCE(SUM(budget), 0) FROM marketing_campaigns
                 WHERE status <> 'Cancelled') AS campaign_budget,
               (SELECT COUNT(*) FROM marketing_plans) AS plan_count,
               (SELECT COUNT(*) FROM marketing_campaigns
                 WHERE status = 'Active') AS active_campaigns,
               (SELECT COUNT(*) FROM marketing_leads
                 WHERE status = 'Converted') AS converted_leads,
               (SELECT COUNT(*) FROM marketing_leads) AS lead_count,
               (SELECT COUNT(*) FROM marketing_leads
                 WHERE follow_up_date IS NOT NULL AND follow_up_date < ?
                   AND status NOT IN ('Converted', 'Lost')) AS overdue_leads,
               (SELECT COUNT(*) FROM customer_feedback
                 WHERE status = 'Pending') AS pending_feedback,
               (SELECT COALESCE(AVG(rating), 0) FROM customer_feedback
                 WHERE rating IS NOT NULL) AS average_rating,
               (SELECT COUNT(DISTINCT party_name) FROM finance_invoices
                 WHERE invoice_type = 'customer') AS customer_count,
               (SELECT COALESCE(SUM(total_etb), 0) FROM finance_invoices
                 WHERE invoice_type = 'customer') AS customer_revenue
    """, (date.today().isoformat(),)).fetchone()
    return {"counts": counts, "totals": totals}


def marketing_pipeline(db):
    """Leads by source: how many arrived, how many were won, what is overdue."""
    return db.execute("""
        SELECT CASE WHEN TRIM(source) = '' THEN 'Unspecified' ELSE source END
               AS source,
               COUNT(*) AS total,
               SUM(CASE WHEN status = 'Converted' THEN 1 ELSE 0 END)
                   AS converted,
               SUM(CASE WHEN follow_up_date IS NOT NULL
                         AND follow_up_date < ?
                         AND status NOT IN ('Converted', 'Lost')
                        THEN 1 ELSE 0 END) AS overdue
        FROM marketing_leads
        GROUP BY CASE WHEN TRIM(source) = '' THEN 'Unspecified' ELSE source END
        ORDER BY total DESC, source
    """, (date.today().isoformat(),)).fetchall()


def marketing_channel_report(db):
    """Campaigns by channel: what was planned, what it costs, how far it got."""
    return db.execute("""
        SELECT CASE WHEN TRIM(c.channel) = '' THEN 'Unspecified'
               ELSE c.channel END AS channel,
               COUNT(*) AS campaigns,
               COALESCE(SUM(c.budget), 0) AS budget,
               SUM(CASE WHEN c.status = 'Active' THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN c.status = 'Completed' THEN 1 ELSE 0 END)
                   AS completed
        FROM marketing_campaigns c
        GROUP BY CASE WHEN TRIM(c.channel) = '' THEN 'Unspecified'
                 ELSE c.channel END
        ORDER BY budget DESC, campaigns DESC
    """).fetchall()


def marketing_feedback_report(db):
    return db.execute("""
        SELECT feedback_type,
               COUNT(*) AS total,
               SUM(CASE WHEN status IN ('Resolved', 'Closed') THEN 1 ELSE 0 END)
                   AS settled,
               COALESCE(AVG(rating), 0) AS average_rating
        FROM customer_feedback
        GROUP BY feedback_type
        ORDER BY total DESC
    """).fetchall()


def marketing_activity(db, limit=12):
    """The newest record at each desk, so the overview reads like a live page."""
    return db.execute("""
        SELECT 'Plan' AS kind, title AS name, status, updated_at, id
          FROM marketing_plans
        UNION ALL
        SELECT 'Campaign', name, status, updated_at, id
          FROM marketing_campaigns
        UNION ALL
        SELECT 'Lead', name, status, updated_at, id FROM marketing_leads
        UNION ALL
        SELECT 'Digital push', name, status, updated_at, id
          FROM digital_campaigns
        UNION ALL
        SELECT 'Feedback',
               CASE WHEN TRIM(customer_name) = '' THEN description
                    ELSE customer_name END,
               status, updated_at, id FROM customer_feedback
        UNION ALL
        SELECT 'Research', title, 'Recorded', updated_at, id
          FROM market_research
        ORDER BY updated_at DESC LIMIT ?
    """, (int(limit),)).fetchall()


# ------------------------------------------------------------------- writing
# Each writer takes an already validated dict and returns the new id. The route
# stamps, audits and commits, so these stay plain SQL.
def insert_plan(db, values, user):
    cursor = db.execute("""
        INSERT INTO marketing_plans (
            title, description, start_date, end_date, budget, currency,
            status, created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (values["title"], values["description"], values["start_date"],
          values["end_date"], str(values["budget"]), values["currency"],
          values["status"], user["id"], user["full_name"]))
    return cursor.lastrowid


def update_plan(db, plan_id, values):
    db.execute("""
        UPDATE marketing_plans
           SET title = ?, description = ?, start_date = ?, end_date = ?,
               budget = ?, currency = ?, status = ?
         WHERE id = ?
    """, (values["title"], values["description"], values["start_date"],
          values["end_date"], str(values["budget"]), values["currency"],
          values["status"], plan_id))


def insert_campaign(db, values, user):
    cursor = db.execute("""
        INSERT INTO marketing_campaigns (
            plan_id, name, description, channel, target_audience, budget,
            currency, start_date, end_date, status, created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (values["plan_id"], values["name"], values["description"],
          values["channel"], values["target_audience"], str(values["budget"]),
          values["currency"], values["start_date"], values["end_date"],
          values["status"], user["id"], user["full_name"]))
    return cursor.lastrowid


def update_campaign(db, campaign_id, values):
    db.execute("""
        UPDATE marketing_campaigns
           SET plan_id = ?, name = ?, description = ?, channel = ?,
               target_audience = ?, budget = ?, currency = ?, start_date = ?,
               end_date = ?, status = ?
         WHERE id = ?
    """, (values["plan_id"], values["name"], values["description"],
          values["channel"], values["target_audience"], str(values["budget"]),
          values["currency"], values["start_date"], values["end_date"],
          values["status"], campaign_id))


def insert_lead(db, values, user):
    cursor = db.execute("""
        INSERT INTO marketing_leads (
            name, phone, email, product_interest, source, notes, assigned_to,
            assigned_to_name, follow_up_date, status, created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (values["name"], values["phone"], values["email"],
          values["product_interest"], values["source"], values["notes"],
          values["assigned_to"], values["assigned_to_name"],
          values["follow_up_date"], values["status"], user["id"],
          user["full_name"]))
    return cursor.lastrowid


def update_lead(db, lead_id, values):
    db.execute("""
        UPDATE marketing_leads
           SET name = ?, phone = ?, email = ?, product_interest = ?,
               source = ?, notes = ?, assigned_to = ?, assigned_to_name = ?,
               follow_up_date = ?, status = ?
         WHERE id = ?
    """, (values["name"], values["phone"], values["email"],
          values["product_interest"], values["source"], values["notes"],
          values["assigned_to"], values["assigned_to_name"],
          values["follow_up_date"], values["status"], lead_id))


def insert_research(db, values, user):
    cursor = db.execute("""
        INSERT INTO market_research (
            title, research_type, market_segment, findings, research_date,
            prepared_by, prepared_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (values["title"], values["research_type"], values["market_segment"],
          values["findings"], values["research_date"], user["id"],
          user["full_name"]))
    return cursor.lastrowid


def update_research(db, research_id, values):
    db.execute("""
        UPDATE market_research
           SET title = ?, research_type = ?, market_segment = ?, findings = ?,
               research_date = ?
         WHERE id = ?
    """, (values["title"], values["research_type"], values["market_segment"],
          values["findings"], values["research_date"], research_id))


def insert_digital(db, values, user):
    cursor = db.execute("""
        INSERT INTO digital_campaigns (
            campaign_id, name, platform, content, start_date, end_date, budget,
            currency, status, created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (values["campaign_id"], values["name"], values["platform"],
          values["content"], values["start_date"], values["end_date"],
          str(values["budget"]), values["currency"], values["status"],
          user["id"], user["full_name"]))
    return cursor.lastrowid


def update_digital(db, digital_id, values):
    db.execute("""
        UPDATE digital_campaigns
           SET campaign_id = ?, name = ?, platform = ?, content = ?,
               start_date = ?, end_date = ?, budget = ?, currency = ?,
               status = ?
         WHERE id = ?
    """, (values["campaign_id"], values["name"], values["platform"],
          values["content"], values["start_date"], values["end_date"],
          str(values["budget"]), values["currency"], values["status"],
          digital_id))


def insert_feedback(db, values, user):
    cursor = db.execute("""
        INSERT INTO customer_feedback (
            customer_name, contact, feedback_type, description, rating,
            assigned_to, assigned_to_name, status, created_by, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (values["customer_name"], values["contact"], values["feedback_type"],
          values["description"], values["rating"], values["assigned_to"],
          values["assigned_to_name"], values["status"], user["id"],
          user["full_name"]))
    return cursor.lastrowid


def update_feedback(db, feedback_id, values):
    db.execute("""
        UPDATE customer_feedback
           SET customer_name = ?, contact = ?, feedback_type = ?,
               description = ?, rating = ?, assigned_to = ?,
               assigned_to_name = ?, resolution = ?, status = ?
         WHERE id = ?
    """, (values["customer_name"], values["contact"], values["feedback_type"],
          values["description"], values["rating"], values["assigned_to"],
          values["assigned_to_name"], values["resolution"], values["status"],
          feedback_id))


def set_status(db, table, record_id, status):
    """One of the status buttons: move a record, keep everything else."""
    db.execute("UPDATE {} SET status = ? WHERE id = ?".format(table),
               (status, record_id))


# ---------------------------------------------------------- duplicate refusal
# A UNIQUE clause already guards a lead by name and phone; the other desks are
# answered here, with the row that already exists named in the message.
def refuse_duplicate(db, table, column, value, exclude=None, label="record"):
    if not value:
        return
    statement = ("SELECT id, {0} FROM {1} WHERE LOWER({0}) = LOWER(?)"
                 .format(column, table))
    params = [value]
    if exclude is not None:
        statement += " AND id <> ?"
        params.append(exclude)
    row = db.execute(statement, params).fetchone()
    if row is not None:
        raise ValueError("A {} named '{}' is already registered (id {})."
                         .format(label, row[column], row["id"]))


def refuse_duplicate_lead(db, phone, email, exclude=None):
    """A person already on the book, matched on phone first, then email."""
    clauses, params = [], []
    if phone:
        clauses.append("REPLACE(REPLACE(phone, ' ', ''), '-', '') = ?")
        params.append(phone.replace(" ", "").replace("-", ""))
    if email:
        clauses.append("LOWER(email) = ?")
        params.append(email.lower())
    if not clauses:
        return
    statement = ("SELECT id, name FROM marketing_leads WHERE {}".format(
        " OR ".join(clauses)))
    if exclude is not None:
        statement += " AND id <> ?"
        params.append(exclude)
    row = db.execute(statement, params).fetchone()
    if row is not None:
        raise ValueError("{} is already registered as a lead (id {})."
                         .format(row["name"], row["id"]))


# ------------------------------------------------------------------- reports
def campaign_performance(db, start=None, end=None):
    """Campaigns with the leads that name the same channel, in one pass."""
    where, params = ["1 = 1"], []
    if start:
        where.append("c.start_date >= ?")
        params.append(start)
    if end:
        where.append("c.start_date <= ?")
        params.append(end)
    return db.execute("""
        SELECT c.id, c.name, c.channel, c.status, c.budget, c.currency,
               c.start_date, c.end_date,
               (SELECT COUNT(*) FROM marketing_leads l
                 WHERE TRIM(l.source) <> ''
                   AND TRIM(l.source) = TRIM(c.channel)) AS leads,
               (SELECT COUNT(*) FROM digital_campaigns d
                 WHERE d.campaign_id = c.id) AS pushes
        FROM marketing_campaigns c
        WHERE {}
        ORDER BY c.start_date DESC, c.id DESC
    """.format(" AND ".join(where)), params).fetchall()
