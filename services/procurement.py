"""Requisition capture schema and line parsing."""

from core.access import current_user
from core.access import effective_permissions


def procurement_granted():
    user = current_user()
    return user is not None and "procurement" in effective_permissions(user)


def purchase_order_ref(db, order_id):
    """The purchase order a stock or asset row was bought on, or None.

    stock.db and asset.db are their own files, so SQLite cannot hold a foreign
    key into finance.procurement_orders. The id the receipt hand-off carries is
    checked against the order desk when the row is written; a blank field simply
    means the row did not come from an order.
    """
    if order_id in (None, ""):
        return None
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        raise ValueError("Choose a purchase order from the order desk.")
    order = db.execute(
        "SELECT id, order_number FROM finance.procurement_orders WHERE id = ?",
        (order_id,)).fetchone()
    if order is None:
        raise ValueError("That purchase order no longer exists.")
    return order


def migrate_procurement_capture(db):
    """Give legacy purchasing rows the shape the requisition screens read.

    A database written before the requisition existed carries one line in the
    request header and no link from an order back to its request. The tables
    themselves live in finance.db now; this only fills in what an older row
    lacks, so it is safe to run on every boot.
    """
    order_columns = {row[1] for row in
                     db.execute("PRAGMA finance.table_info(procurement_orders)").fetchall()}
    if "request_id" not in order_columns:
        db.execute("ALTER TABLE finance.procurement_orders ADD COLUMN request_id INTEGER")
    if "issued_at" not in order_columns:
        db.execute("ALTER TABLE finance.procurement_orders ADD COLUMN issued_at TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS finance.idx_po_request"
               " ON procurement_orders(request_id)")

    # A request raised before this table existed still carries its one line in the
    # header columns, so give it a line row. The ids are kept, which is what makes an
    # existing reference on the approval chain still point at the same requisition.
    for row in db.execute("""
        SELECT r.id, r.item_name, r.quantity, r.unit, r.estimated_cost
        FROM finance.procurement_requests AS r
        WHERE NOT EXISTS (SELECT 1 FROM finance.procurement_request_items AS i
                          WHERE i.request_id = r.id)
    """).fetchall():
        quantity = float(row["quantity"] or 0)
        stored_total = round(float(row["estimated_cost"] or 0), 2)
        unit_price = (round(stored_total / quantity, 2) if quantity else 0.0)
        # The line total is the amount the header already carried, not quantity x the
        # rounded unit price: 50,000 over 15 pcs would replay as 49,999.95 and move the
        # request down an approval band it was already routed through.
        db.execute("""
            INSERT INTO finance.procurement_request_items (
                request_id, line_number, item_name, quantity, unit, unit_price, line_total
            ) VALUES (?, 1, ?, ?, ?, ?, ?)
        """, (row["id"], row["item_name"], quantity, row["unit"],
              unit_price, stored_total))


def requisition_lines(db, request_id):
    return db.execute("""
        SELECT * FROM finance.procurement_request_items
        WHERE request_id = ?
        ORDER BY line_number, id
    """, (request_id,)).fetchall()


def requisition_total(lines):
    return round(sum(float(row["line_total"]) for row in lines), 2)


def parse_requisition_lines(form):
    """Read the repeating item block into validated lines.

    Quantity and unit price are what the total - and therefore the approval band - is
    worked out from, so a blank or unusable line is refused here rather than quietly
    becoming a zero that routes the request to a cheaper desk.
    """
    names = form.getlist("line_name[]")
    quantities = form.getlist("line_quantity[]")
    units = form.getlist("line_unit[]")
    prices = form.getlist("line_price[]")
    lines = []
    for index, name in enumerate(names):
        name = (name or "").strip()
        quantity_text = (quantities[index] if index < len(quantities) else "").strip()
        price_text = (prices[index] if index < len(prices) else "").strip()
        if not name and not quantity_text and not price_text:
            continue
        if not name:
            raise ValueError("Every item line needs a material or item name.")
        try:
            quantity = float(quantity_text or "0")
            unit_price = float(price_text or "0")
        except (TypeError, ValueError):
            raise ValueError("Enter a number for the quantity and unit price on line {}."
                             .format(index + 1))
        if quantity <= 0:
            raise ValueError("Line {} needs a quantity above zero.".format(index + 1))
        if unit_price < 0:
            raise ValueError("Line {} cannot have a negative unit price.".format(index + 1))
        unit = (units[index] if index < len(units) else "pcs")
        unit = (unit or "pcs").strip() or "pcs"
        lines.append({
            "item_name": name[:150],
            "quantity": quantity,
            "unit": unit[:20],
            "unit_price": round(unit_price, 2),
            "line_total": round(quantity * unit_price, 2),
        })
    if not lines:
        raise ValueError("Add at least one item line to the requisition.")
    return lines


def requisition_header(lines):
    """The legacy single-line columns, kept honest so the old screens still read."""
    total_quantity = round(sum(row["quantity"] for row in lines), 2)
    first = lines[0]["item_name"]
    name = first if len(lines) == 1 else "{} (+{} more)".format(first, len(lines) - 1)
    return name[:150], total_quantity, lines[0]["unit"]


def save_requisition_lines(db, request_id, lines):
    db.execute("DELETE FROM finance.procurement_request_items WHERE request_id = ?", (request_id,))
    for number, line in enumerate(lines, start=1):
        db.execute("""
            INSERT INTO finance.procurement_request_items (
                request_id, line_number, item_name, quantity, unit, unit_price, line_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (request_id, number, line["item_name"], line["quantity"], line["unit"],
              line["unit_price"], line["line_total"]))
