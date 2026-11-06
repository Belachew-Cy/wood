"""Counting, reorder alerts, reports and printable labels for the stock desk.

A stocktake never edits a balance. It snapshots what the system believes, records
what the counter found, and - once a second person approves it - posts each
difference as an ordinary stock movement. Posting refuses when the shelf moved
after the count, so a correction can never be wider than the count it came from.

The reports read the rows the register and the movement ledger already write,
and the labels are drawn from the item's SKU, so nothing here keeps a second
copy of the stock.
"""

import csv
import io
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from flask import Response
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import send_file
from flask import url_for

from core.access import current_user
from core.access import has_permission
from core.access import perm
from core.db import get_db
from core.validation import clean_date
from core.validation import clean_text
from services.audit import log_activity

from departments.inventory import INVENTORY_CATEGORIES
from departments.inventory import INVENTORY_REFERENCES
from departments.inventory import _now
from departments.inventory import _number
from departments.inventory import _stamp
from departments.inventory import active_item
from departments.inventory.blueprint import inventory_bp


STOCKTAKE_STATUS = {
    "COUNTING": "Counting",
    "SUBMITTED": "Waiting approval",
    "APPROVED": "Approved",
    "POSTED": "Posted",
}

STOCK_STATES = {
    "all": "Every item",
    "ok": "Above minimum",
    "low": "Below minimum",
    "out": "Out of stock",
}

# The ledger also carries labels the manual form never offers: the opening
# balance written when an item is registered, and the corrections a posted
# count writes. The report filter still has to be able to name them.
MOVEMENT_REFERENCES = INVENTORY_REFERENCES + ("Stocktake", "Opening Balance")


# ------------------------------------------------------------ stocktake helpers
def _stocktake(stocktake_id):
    row = get_db().execute(
        "SELECT * FROM inventory_stocktakes WHERE id = ?", (stocktake_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _stocktake_number(db):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    used = db.execute(
        "SELECT COUNT(*) AS n FROM inventory_stocktakes WHERE number LIKE ?",
        ("STK-{}-%".format(stamp),),
    ).fetchone()["n"]
    return "STK-{}-{:03d}".format(stamp, used + 1)


def _stocktake_progress(db, stocktake_id):
    return db.execute("""
        SELECT COUNT(*) AS lines,
               COALESCE(SUM(CASE WHEN counted_qty IS NOT NULL
                                 THEN 1 ELSE 0 END), 0) AS counted,
               COALESCE(SUM(CASE WHEN counted_qty IS NOT NULL
                                  AND variance <> 0 THEN 1 ELSE 0 END), 0)
                   AS variances
        FROM inventory_stocktake_lines WHERE stocktake_id = ?
    """, (stocktake_id,)).fetchone()


def _stocktake_back(stocktake_id, error):
    flash(error, "error")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


def date_today():
    return datetime.now(timezone.utc).date().isoformat()


# ------------------------------------------------------------- stocktake routes
@inventory_bp.route("/stocktake")
@perm("inventory_stocktake", "inventory_stocktake_approve")
def stocktakes():
    db = get_db()
    sessions = db.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM inventory_stocktake_lines l
                 WHERE l.stocktake_id = s.id) AS line_count,
               (SELECT COUNT(*) FROM inventory_stocktake_lines l
                 WHERE l.stocktake_id = s.id
                   AND l.counted_qty IS NOT NULL) AS counted_count,
               (SELECT COUNT(*) FROM inventory_stocktake_lines l
                 WHERE l.stocktake_id = s.id AND l.counted_qty IS NOT NULL
                   AND l.variance <> 0) AS variance_count
        FROM inventory_stocktakes AS s
        ORDER BY s.id DESC LIMIT 60
    """).fetchall()
    counts = db.execute("""
        SELECT COALESCE(SUM(CASE WHEN status = 'COUNTING' THEN 1 ELSE 0 END), 0)
                   AS counting,
               COALESCE(SUM(CASE WHEN status = 'SUBMITTED' THEN 1 ELSE 0 END), 0)
                   AS waiting,
               COALESCE(SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END), 0)
                   AS approved,
               COALESCE(SUM(CASE WHEN status = 'POSTED' THEN 1 ELSE 0 END), 0)
                   AS posted
        FROM inventory_stocktakes
    """).fetchone()
    return render_template(
        "inventory/stocktake.html", sessions=sessions, counts=counts,
        statuses=STOCKTAKE_STATUS, today=date_today(),
        can_count=has_permission("inventory_stocktake"),
    )


@inventory_bp.route("/stocktake/new", methods=["POST"])
@perm("inventory_stocktake")
def new_stocktake():
    try:
        title = clean_text(request.form.get("title", ""), "Title",
                           maximum=120, required=True)
        count_date = clean_date(request.form.get("count_date", ""),
                                "Count date", required=True)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("inventory.stocktakes"))

    db = get_db()
    active = db.execute(
        "SELECT COUNT(*) AS n FROM inventory_items WHERE active = 1"
    ).fetchone()["n"]
    if not active:
        flash("Register an item before opening a stocktake.", "error")
        return redirect(url_for("inventory.stocktakes"))

    user = current_user()
    number = _stocktake_number(db)
    cursor = db.execute("""
        INSERT INTO inventory_stocktakes
            (number, title, count_date, status, notes, created_by,
             created_by_name, created_at)
        VALUES (?, ?, ?, 'COUNTING', ?, ?, ?, ?)
    """, (number, title, count_date,
          clean_text(request.form.get("notes", ""), "Notes", maximum=400),
          user["id"], user["full_name"], _now()))
    stocktake_id = cursor.lastrowid
    # The expected quantity is frozen now: a movement made while the counters
    # walk the shelves must not silently change what they were asked to verify.
    db.execute("""
        INSERT INTO inventory_stocktake_lines
            (stocktake_id, item_id, expected_qty)
        SELECT ?, id, quantity FROM inventory_items WHERE active = 1
    """, (stocktake_id,))
    db.commit()
    log_activity("CREATE", "inventory",
                 "Stocktake {} opened with {} item(s)".format(number, active),
                 record_type="inventory_stocktake", record_id=stocktake_id,
                 details={"number": number, "lines": active})
    flash("Stocktake {} opened. Record what is on the shelf.".format(number),
          "success")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


@inventory_bp.route("/stocktake/<int:stocktake_id>")
@perm("inventory_stocktake", "inventory_stocktake_approve")
def stocktake_detail(stocktake_id):
    db = get_db()
    count = _stocktake(stocktake_id)
    lines = db.execute("""
        SELECT l.*, i.sku, i.name, i.unit, i.quantity AS current_qty
        FROM inventory_stocktake_lines AS l
        JOIN inventory_items AS i ON i.id = l.item_id
        WHERE l.stocktake_id = ?
        ORDER BY i.name
    """, (stocktake_id,)).fetchall()
    return render_template(
        "inventory/stocktake_detail.html", count=count, lines=lines,
        progress=_stocktake_progress(db, stocktake_id),
        statuses=STOCKTAKE_STATUS,
        can_count=has_permission("inventory_stocktake"),
        can_approve=has_permission("inventory_stocktake_approve"),
        owns_count=count["created_by"] == current_user()["id"],
    )


@inventory_bp.route("/stocktake/<int:stocktake_id>/count", methods=["POST"])
@perm("inventory_stocktake")
def stocktake_count(stocktake_id):
    db = get_db()
    count = _stocktake(stocktake_id)
    if count["status"] != "COUNTING":
        return _stocktake_back(stocktake_id,
                               "That stocktake is no longer open for counting.")

    user = current_user()
    if request.form.get("action") == "fill":
        filled = db.execute("""
            UPDATE inventory_stocktake_lines
            SET counted_qty = expected_qty, variance = 0,
                counted_by_name = ?, counted_at = ?
            WHERE stocktake_id = ? AND counted_qty IS NULL
        """, (user["full_name"], _now(), stocktake_id)).rowcount
        db.commit()
        log_activity("UPDATE", "inventory",
                     "Stocktake {}: {} line(s) marked at the system quantity"
                     .format(count["number"], filled),
                     record_type="inventory_stocktake", record_id=stocktake_id,
                     details={"filled": filled})
        flash("{} line{} marked at the system quantity.".format(
            filled, "" if filled == 1 else "s"), "success")
        return redirect(url_for("inventory.stocktake_detail",
                                stocktake_id=stocktake_id))

    lines = db.execute("""
        SELECT l.*, i.name FROM inventory_stocktake_lines AS l
        JOIN inventory_items AS i ON i.id = l.item_id
        WHERE l.stocktake_id = ?
    """, (stocktake_id,)).fetchall()
    updates = []
    for line in lines:
        raw = (request.form.get("count_{}".format(line["id"])) or "").strip()
        if not raw:
            continue
        try:
            counted = _number(raw, "Count for {}".format(line["name"]),
                              minimum=0)
        except ValueError as error:
            return _stocktake_back(stocktake_id, str(error))
        updates.append((counted, line["id"]))

    for counted, line_id in updates:
        db.execute("""
            UPDATE inventory_stocktake_lines
            SET counted_qty = ?, variance = ? - expected_qty,
                counted_by_name = ?, counted_at = ?
            WHERE id = ? AND stocktake_id = ?
        """, (counted, counted, user["full_name"], _now(), line_id,
              stocktake_id))
    db.commit()
    progress = _stocktake_progress(db, stocktake_id)
    if updates:
        log_activity("UPDATE", "inventory",
                     "Stocktake {}: {} line(s) counted, {} of {} done".format(
                         count["number"], len(updates), progress["counted"],
                         progress["lines"]),
                     record_type="inventory_stocktake", record_id=stocktake_id,
                     details={"updated": len(updates),
                              "counted": progress["counted"],
                              "lines": progress["lines"]})
        flash("{} count{} saved.".format(len(updates),
                                         "" if len(updates) == 1 else "s"),
              "success")
    else:
        flash("Enter at least one counted quantity.", "info")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


@inventory_bp.route("/stocktake/<int:stocktake_id>/submit", methods=["POST"])
@perm("inventory_stocktake")
def stocktake_submit(stocktake_id):
    db = get_db()
    count = _stocktake(stocktake_id)
    if count["status"] != "COUNTING":
        return _stocktake_back(stocktake_id,
                               "That stocktake has already been submitted.")
    progress = _stocktake_progress(db, stocktake_id)
    missing = progress["lines"] - progress["counted"]
    if missing:
        return _stocktake_back(
            stocktake_id,
            "Count every line before submitting: {} still blank.".format(missing))
    db.execute("""
        UPDATE inventory_stocktakes
        SET status = 'SUBMITTED', submitted_at = ?
        WHERE id = ?
    """, (_now(), stocktake_id))
    db.commit()
    log_activity("SUBMIT", "inventory",
                 "Stocktake {} submitted with {} variance(s)".format(
                     count["number"], progress["variances"]),
                 record_type="inventory_stocktake", record_id=stocktake_id,
                 details={"lines": progress["lines"],
                          "variances": progress["variances"]})
    flash("Stocktake {} sent for approval.".format(count["number"]), "success")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


@inventory_bp.route("/stocktake/<int:stocktake_id>/approve", methods=["POST"])
@perm("inventory_stocktake_approve")
def stocktake_approve(stocktake_id):
    db = get_db()
    count = _stocktake(stocktake_id)
    if count["status"] != "SUBMITTED":
        return _stocktake_back(stocktake_id,
                               "Only a submitted count can be approved.")
    user = current_user()
    if count["created_by"] == user["id"]:
        return _stocktake_back(
            stocktake_id,
            "The account that ran the count cannot approve it.")
    db.execute("""
        UPDATE inventory_stocktakes
        SET status = 'APPROVED', decided_at = ?, decided_by = ?,
            decided_by_name = ?, decision_note = ?
        WHERE id = ?
    """, (_now(), user["id"], user["full_name"],
          clean_text(request.form.get("note", ""), "Note", maximum=400),
          stocktake_id))
    db.commit()
    log_activity("APPROVE", "inventory",
                 "Stocktake {} approved".format(count["number"]),
                 record_type="inventory_stocktake", record_id=stocktake_id)
    flash("Stocktake {} approved. Post it to correct the shelf.".format(
        count["number"]), "success")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


@inventory_bp.route("/stocktake/<int:stocktake_id>/reject", methods=["POST"])
@perm("inventory_stocktake_approve")
def stocktake_reject(stocktake_id):
    db = get_db()
    count = _stocktake(stocktake_id)
    if count["status"] != "SUBMITTED":
        return _stocktake_back(stocktake_id,
                               "Only a submitted count can be returned.")
    try:
        note = clean_text(request.form.get("note", ""), "Reason",
                          maximum=400, required=True)
    except ValueError as error:
        return _stocktake_back(stocktake_id, str(error))
    user = current_user()
    db.execute("""
        UPDATE inventory_stocktakes
        SET status = 'COUNTING', decided_at = ?, decided_by = ?,
            decided_by_name = ?, decision_note = ?
        WHERE id = ?
    """, (_now(), user["id"], user["full_name"], note, stocktake_id))
    db.commit()
    log_activity("REJECT", "inventory",
                 "Stocktake {} returned for recount: {}".format(
                     count["number"], note),
                 record_type="inventory_stocktake", record_id=stocktake_id,
                 details={"reason": note})
    flash("Stocktake {} returned for recount.".format(count["number"]), "info")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


@inventory_bp.route("/stocktake/<int:stocktake_id>/post", methods=["POST"])
@perm("inventory_stocktake")
def stocktake_post(stocktake_id):
    db = get_db()
    count = _stocktake(stocktake_id)
    if count["status"] != "APPROVED":
        return _stocktake_back(stocktake_id,
                               "Only an approved count can be posted.")
    lines = db.execute("""
        SELECT l.*, i.name, i.unit FROM inventory_stocktake_lines AS l
        JOIN inventory_items AS i ON i.id = l.item_id
        WHERE l.stocktake_id = ?
    """, (stocktake_id,)).fetchall()
    user = current_user()
    corrected = 0
    for line in lines:
        variance = line["variance"] or 0
        if abs(variance) < 0.000001:
            continue
        # The update carries the snapshot it was measured against: a movement
        # since the count fails the whole post instead of applying a stale
        # difference to a shelf that has changed.
        changed = db.execute("""
            UPDATE inventory_items
            SET quantity = quantity + ?
            WHERE id = ? AND abs(quantity - ?) < 0.000001
              AND quantity + ? >= 0
        """, (variance, line["item_id"], line["expected_qty"],
              variance)).rowcount
        if not changed:
            db.rollback()
            now_qty = db.execute(
                "SELECT quantity FROM inventory_items WHERE id = ?",
                (line["item_id"],)).fetchone()["quantity"]
            return _stocktake_back(
                stocktake_id,
                "{} changed since the count ({} at the snapshot, {} now)."
                " Start a new count.".format(line["name"],
                                             line["expected_qty"], now_qty))
        db.execute("""
            INSERT INTO stock_movements
                (item_id, movement_type, quantity, reference_type,
                 reference_no, notes, created_by)
            VALUES (?, ?, ?, 'Stocktake', ?, ?, ?)
        """, (line["item_id"], "IN" if variance > 0 else "OUT",
              abs(variance), count["number"],
              "Counted {} against {} expected".format(
                  line["counted_qty"], line["expected_qty"]),
              user["full_name"]))
        _stamp(db, line["item_id"])
        corrected += 1

    db.execute("""
        UPDATE inventory_stocktakes SET status = 'POSTED', posted_at = ?
        WHERE id = ?
    """, (_now(), stocktake_id))
    db.commit()
    log_activity("POST", "inventory",
                 "Stocktake {} posted: {} line(s) corrected".format(
                     count["number"], corrected),
                 record_type="inventory_stocktake", record_id=stocktake_id,
                 details={"corrected": corrected, "lines": len(lines)})
    flash("Stocktake {} posted: {} line{} corrected.".format(
        count["number"], corrected, "" if corrected == 1 else "s"), "success")
    return redirect(url_for("inventory.stocktake_detail",
                            stocktake_id=stocktake_id))


# ------------------------------------------------------------------- reorder
@inventory_bp.route("/alerts")
@perm("inventory_alerts")
def alerts():
    rows = get_db().execute("""
        SELECT *, CASE WHEN quantity <= 0 THEN 'OUT' ELSE 'LOW' END AS state,
               CASE WHEN reorder_qty > 0 THEN reorder_qty
                    ELSE MAX(min_stock - quantity, 0) END AS suggested
        FROM inventory_items
        WHERE active = 1 AND quantity <= min_stock
        ORDER BY CASE WHEN quantity <= 0 THEN 0 ELSE 1 END,
                 quantity - min_stock, name
    """).fetchall()
    out = sum(1 for row in rows if row["state"] == "OUT")
    low = len(rows) - out
    value = sum(row["suggested"] * row["unit_cost"] for row in rows)
    return render_template("inventory/alerts.html", items=rows, out=out,
                           low=low, suggested_value=value,
                           can_receive=has_permission("inventory_movements"))


# ------------------------------------------------------------------- reports
def _report_filters():
    search = (request.args.get("q") or "").strip()[:80]
    category = request.args.get("category", "")
    if category not in INVENTORY_CATEGORIES:
        category = ""
    state = request.args.get("state", "all")
    if state not in STOCK_STATES:
        state = "all"
    return search, category, state


def _stock_rows(search, category, state):
    sql = ["""
        SELECT * FROM inventory_items
        WHERE active = 1
          AND (instr(lower(sku), lower(?)) > 0
               OR instr(lower(name), lower(?)) > 0
               OR instr(lower(category), lower(?)) > 0)
    """]
    params = [search, search, search]
    if category:
        sql.append("AND category = ?")
        params.append(category)
    if state == "out":
        sql.append("AND quantity <= 0")
    elif state == "low":
        sql.append("AND quantity > 0 AND quantity <= min_stock")
    elif state == "ok":
        sql.append("AND quantity > min_stock")
    sql.append("ORDER BY category, name")
    return get_db().execute(" ".join(sql), params).fetchall()


def _stock_totals(rows):
    return {
        "items": len(rows),
        "quantity": sum(row["quantity"] for row in rows),
        "value": sum(row["quantity"] * row["unit_cost"] for row in rows),
        "low": sum(1 for row in rows if 0 < row["quantity"] <= row["min_stock"]),
        "out": sum(1 for row in rows if row["quantity"] <= 0),
    }


def _valuation_groups(rows):
    buckets = {}
    for row in rows:
        bucket = buckets.setdefault(row["category"], {
            "category": row["category"], "items": 0, "quantity": 0.0,
            "value": 0.0})
        bucket["items"] += 1
        bucket["quantity"] += row["quantity"]
        bucket["value"] += row["quantity"] * row["unit_cost"]
    total = sum(bucket["value"] for bucket in buckets.values())
    groups = []
    for bucket in buckets.values():
        bucket["share"] = (bucket["value"] / total * 100) if total else 0
        groups.append(bucket)
    return sorted(groups, key=lambda bucket: bucket["value"], reverse=True)


def _movement_filters():
    today = datetime.now(timezone.utc).date()
    try:
        start = clean_date(request.args.get("start", ""), "Start date")
        end = clean_date(request.args.get("end", ""), "End date")
    except ValueError as error:
        flash(str(error), "error")
        start = end = None
    start = start or (today - timedelta(days=29)).isoformat()
    end = end or today.isoformat()
    if start > end:
        start, end = end, start
    movement_type = request.args.get("movement_type", "")
    if movement_type not in ("", "IN", "OUT"):
        movement_type = ""
    reference = request.args.get("reference", "")
    if reference not in MOVEMENT_REFERENCES:
        reference = ""
    return start, end, movement_type, reference


def _movement_rows(start, end, movement_type, reference):
    sql = ["""
        SELECT m.*, i.sku, i.name, i.unit, i.category
        FROM stock_movements AS m
        JOIN inventory_items AS i ON i.id = m.item_id
        WHERE substr(m.created_at, 1, 10) BETWEEN ? AND ?
    """]
    params = [start, end]
    if movement_type:
        sql.append("AND m.movement_type = ?")
        params.append(movement_type)
    if reference:
        sql.append("AND m.reference_type = ?")
        params.append(reference)
    sql.append("ORDER BY m.id DESC LIMIT 1000")
    return get_db().execute(" ".join(sql), params).fetchall()


def _csv_response(filename, header, rows):
    buffer = io.StringIO()
    buffer.write("\ufeff")  # Excel reads the BOM before the first heading.
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    log_activity("EXPORT", "inventory", "Exported {}".format(filename),
                 details={"rows": len(rows)})
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="{}"'.format(filename)},
    )


def _stamp_name():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


@inventory_bp.route("/reports")
@perm("inventory_reports")
def reports():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM inventory_items WHERE active = 1").fetchall()
    groups = _valuation_groups(rows)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    flow = db.execute("""
        SELECT COALESCE(SUM(CASE WHEN movement_type = 'IN' THEN quantity
                                 ELSE 0 END), 0) AS received,
               COALESCE(SUM(CASE WHEN movement_type = 'OUT' THEN quantity
                                 ELSE 0 END), 0) AS issued,
               COUNT(*) AS moves
        FROM stock_movements WHERE substr(created_at, 1, 7) = ?
    """, (month,)).fetchone()
    recent = db.execute("""
        SELECT m.*, i.name, i.unit FROM stock_movements AS m
        JOIN inventory_items AS i ON i.id = m.item_id
        ORDER BY m.id DESC LIMIT 8
    """).fetchall()
    value_rows = [{"label": group["category"], "value": group["value"],
                   "display": "{:,.0f} ETB".format(group["value"])}
                  for group in groups[:6]]
    return render_template("inventory/reports_dashboard.html",
                           totals=_stock_totals(rows), groups=groups,
                           value_rows=value_rows, flow=flow, month=month,
                           recent=recent,
                           today=datetime.now(timezone.utc).date().isoformat())


@inventory_bp.route("/reports/stock")
@perm("inventory_reports")
def stock_report():
    search, category, state = _report_filters()
    rows = _stock_rows(search, category, state)
    return render_template("inventory/stock_report.html", items=rows,
                           totals=_stock_totals(rows), search=search,
                           category=category, state=state,
                           categories=INVENTORY_CATEGORIES,
                           states=STOCK_STATES)


@inventory_bp.route("/reports/stock/export")
@perm("inventory_reports")
def stock_report_export():
    search, category, state = _report_filters()
    rows = _stock_rows(search, category, state)
    return _csv_response(
        "stock-report-{}.csv".format(_stamp_name()),
        ("SKU", "Item", "Category", "Unit", "In stock", "Minimum",
         "Reorder quantity", "Unit cost", "Stock value", "Location"),
        [(row["sku"], row["name"], row["category"], row["unit"],
          row["quantity"], row["min_stock"], row["reorder_qty"],
          "{:.2f}".format(row["unit_cost"]),
          "{:.2f}".format(row["quantity"] * row["unit_cost"]),
          row["location"] or "") for row in rows])


@inventory_bp.route("/reports/valuation")
@perm("inventory_reports")
def valuation_report():
    search, category, _state = _report_filters()
    rows = _stock_rows(search, category, "all")
    groups = _valuation_groups(rows)
    valued = sorted(rows, key=lambda row: row["quantity"] * row["unit_cost"],
                    reverse=True)[:40]
    return render_template("inventory/valuation_report.html", groups=groups,
                           items=valued, totals=_stock_totals(rows),
                           search=search, category=category,
                           categories=INVENTORY_CATEGORIES)


@inventory_bp.route("/reports/valuation/export")
@perm("inventory_reports")
def valuation_report_export():
    search, category, _state = _report_filters()
    rows = _stock_rows(search, category, "all")
    return _csv_response(
        "stock-valuation-{}.csv".format(_stamp_name()),
        ("SKU", "Item", "Category", "Unit", "In stock", "Unit cost",
         "Stock value"),
        [(row["sku"], row["name"], row["category"], row["unit"],
          row["quantity"], "{:.2f}".format(row["unit_cost"]),
          "{:.2f}".format(row["quantity"] * row["unit_cost"]))
         for row in rows])


@inventory_bp.route("/reports/movements")
@perm("inventory_reports")
def movement_report():
    start, end, movement_type, reference = _movement_filters()
    rows = _movement_rows(start, end, movement_type, reference)
    received = sum(row["quantity"] for row in rows
                   if row["movement_type"] == "IN")
    issued = sum(row["quantity"] for row in rows
                 if row["movement_type"] == "OUT")
    return render_template("inventory/movement_report.html", movements=rows,
                           start=start, end=end, movement_type=movement_type,
                           reference=reference, received=received,
                           issued=issued, references=MOVEMENT_REFERENCES)


@inventory_bp.route("/reports/movements/export")
@perm("inventory_reports")
def movement_report_export():
    start, end, movement_type, reference = _movement_filters()
    rows = _movement_rows(start, end, movement_type, reference)
    return _csv_response(
        "stock-movements-{}-to-{}.csv".format(start, end),
        ("Date", "SKU", "Item", "Category", "Movement", "Quantity", "Unit",
         "Related module", "Reference", "Notes", "Recorded by"),
        [(row["created_at"], row["sku"], row["name"], row["category"],
          "Stock in" if row["movement_type"] == "IN" else "Stock out",
          row["quantity"], row["unit"], row["reference_type"],
          row["reference_no"] or "", row["notes"] or "", row["created_by"])
         for row in rows])


# -------------------------------------------------------------------- labels
@inventory_bp.route("/items/<int:item_id>/label")
@perm("inventory_items")
def item_label(item_id):
    return render_template("inventory/item_label.html", item=active_item(item_id))


@inventory_bp.route("/items/<int:item_id>/barcode.png")
@perm("inventory_items")
def item_barcode(item_id):
    item = active_item(item_id)
    import barcode
    from barcode.writer import ImageWriter
    buffer = io.BytesIO()
    barcode.get("code128", item["sku"], writer=ImageWriter()).write(
        buffer,
        options={"module_width": 0.32, "module_height": 14,
                 "quiet_zone": 3, "write_text": False},
    )
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png", max_age=0)


@inventory_bp.route("/items/<int:item_id>/qr.png")
@perm("inventory_items")
def item_qr(item_id):
    item = active_item(item_id)
    import qrcode
    buffer = io.BytesIO()
    qrcode.make(item["sku"]).save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png", max_age=0)
