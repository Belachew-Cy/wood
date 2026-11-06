"""The stock desk: catalogue items, the shelves they sit on and every movement.

The screen set stays small on purpose. An item is registered once with a SKU,
filed under a category, given a minimum level and a unit cost, and from then on
its balance only changes through a stock movement. Every write stamps who
touched the row and leaves one audit line, so the register and the movement
history explain each other without a second ledger.

The stock, procurement, production and sales desks read these same rows through
``services/audit.py`` and the report centre; the module owns the tables, nothing
else keeps a copy.
"""

import math
import sqlite3
from datetime import datetime
from datetime import timezone

from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for

from core.access import current_user
from core.access import perm
from core.db import get_db
from core.validation import clean_text
from services.audit import log_activity
from services.procurement import purchase_order_ref

from departments.inventory.blueprint import inventory_bp


INVENTORY_CATEGORIES = (
    "Timber", "Plywood", "MDF", "Hardware", "Paint & Glue",
    "Finished Product", "Other",
)
INVENTORY_UNITS = ("pcs", "sheet", "meter", "m3", "kg", "liter", "box", "set")
INVENTORY_REFERENCES = (
    "Purchasing", "Production", "Sales", "Adjustment", "Manual",
)


# ------------------------------------------------------------------- helpers
def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value, field, minimum=0.0):
    """Read a form number, refusing blanks and values a balance cannot hold."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be a number.".format(field))
    if not math.isfinite(number):
        raise ValueError("{} must be a finite number.".format(field))
    if minimum is not None and number < minimum:
        raise ValueError("{} cannot be below {}.".format(field, minimum))
    return number


def inventory_item_fields(require_sku):
    """Read and validate the item form; a bad field raises ValueError."""
    sku = clean_text(request.form.get("sku", ""), "SKU", maximum=40,
                     required=require_sku).upper()
    name = clean_text(request.form.get("name", ""), "Item name", maximum=120,
                      required=True)
    category = clean_text(request.form.get("category", ""), "Category",
                          maximum=40, required=True)
    unit = clean_text(request.form.get("unit", ""), "Unit", maximum=20,
                      required=True)
    supplier = clean_text(request.form.get("supplier", ""), "Supplier",
                          maximum=120)
    location = clean_text(request.form.get("location", ""), "Location",
                          maximum=120)

    if category not in INVENTORY_CATEGORIES:
        raise ValueError("Choose a category from the list.")
    if unit not in INVENTORY_UNITS:
        raise ValueError("Choose a unit from the list.")

    return {
        "sku": sku, "name": name, "category": category, "unit": unit,
        "supplier": supplier, "location": location,
        "min_stock": _number(request.form.get("min_stock", 0) or 0,
                             "Minimum stock"),
        "reorder_qty": _number(request.form.get("reorder_qty", 0) or 0,
                               "Reorder quantity"),
        "unit_cost": _number(request.form.get("unit_cost", 0) or 0,
                             "Unit cost"),
    }


def inventory_item(item_id):
    return get_db().execute(
        "SELECT * FROM inventory_items WHERE id = ?", (item_id,)
    ).fetchone()


def active_item(item_id):
    item = get_db().execute(
        "SELECT * FROM inventory_items WHERE id = ? AND active = 1", (item_id,)
    ).fetchone()
    if item is None:
        abort(404)
    return item


def _stamp(db, item_id):
    """Mark the catalogue row as edited by the signed-in account."""
    user = current_user()
    db.execute("""
        UPDATE inventory_items
        SET updated_by = ?, updated_by_name = ?, updated_at = ?
        WHERE id = ?
    """, (user["id"], user["full_name"], _now(), item_id))


# -------------------------------------------------------------------- routes
@inventory_bp.route("/")
@perm("inventory_dashboard")
def dashboard():
    db = get_db()
    summary = db.execute("""
        SELECT COUNT(*) AS total_items,
               COALESCE(SUM(quantity * unit_cost), 0) AS stock_value,
               SUM(CASE WHEN quantity <= min_stock AND quantity > 0
                        THEN 1 ELSE 0 END) AS low_stock,
               SUM(CASE WHEN quantity = 0 THEN 1 ELSE 0 END) AS out_of_stock
        FROM inventory_items
        WHERE active = 1
    """).fetchone()
    recent = db.execute("""
        SELECT m.*, i.sku, i.name, i.unit
        FROM stock_movements m
        JOIN inventory_items i ON i.id = m.item_id
        ORDER BY m.id DESC LIMIT 8
    """).fetchall()
    return render_template("inventory/dashboard.html", summary=summary,
                           recent=recent)


@inventory_bp.route("/items")
@perm("inventory_items")
def items():
    search = (request.args.get("q") or "").strip()[:80]
    rows = get_db().execute("""
        SELECT * FROM inventory_items
        WHERE active = 1
          AND (instr(lower(sku), lower(?)) > 0
               OR instr(lower(name), lower(?)) > 0
               OR instr(lower(category), lower(?)) > 0)
        ORDER BY name
    """, (search, search, search)).fetchall()
    return render_template("inventory/items.html", items=rows, search=search)


@inventory_bp.route("/items/new", methods=["GET", "POST"])
@perm("inventory_items")
def new_item():
    if request.method == "POST":
        try:
            record = inventory_item_fields(require_sku=True)
            opening = _number(request.form.get("opening_qty", 0) or 0,
                              "Opening quantity")
        except ValueError as error:
            flash(str(error), "error")
        else:
            db = get_db()
            try:
                cursor = db.execute("""
                    INSERT INTO inventory_items
                        (sku, name, category, unit, quantity, min_stock,
                         reorder_qty, unit_cost, supplier, location)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record["sku"], record["name"], record["category"],
                    record["unit"], opening, record["min_stock"],
                    record["reorder_qty"], record["unit_cost"],
                    record["supplier"], record["location"],
                ))
                item_id = cursor.lastrowid
                _stamp(db, item_id)
                if opening > 0:
                    db.execute("""
                        INSERT INTO stock_movements
                            (item_id, movement_type, quantity, reference_type,
                             reference_no, notes, created_by)
                        VALUES (?, 'IN', ?, 'Opening Balance', 'OPENING',
                                'Initial stock', ?)
                    """, (item_id, opening, current_user()["full_name"]))
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                log_activity("CREATE", "inventory",
                             "Duplicate SKU rejected: {}".format(record["sku"]),
                             outcome="FAILURE", record_type="inventory_item")
                flash("That SKU is already registered.", "error")
            else:
                log_activity("CREATE", "inventory",
                             "Item {} created with {} {} on hand".format(
                                 record["name"], opening, record["unit"]),
                             record_type="inventory_item", record_id=item_id)
                flash("Inventory item created.", "success")
                return redirect(url_for("inventory.items"))
    return render_template(
        "inventory/item_form.html", item=None, categories=INVENTORY_CATEGORIES,
        units=INVENTORY_UNITS,
    )


@inventory_bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@perm("inventory_items")
def edit_item(item_id):
    item = active_item(item_id)

    if request.method == "POST":
        try:
            record = inventory_item_fields(require_sku=False)
        except ValueError as error:
            flash(str(error), "error")
        else:
            db = get_db()
            db.execute("""
                UPDATE inventory_items
                SET name = ?, category = ?, unit = ?, min_stock = ?,
                    reorder_qty = ?, unit_cost = ?, supplier = ?, location = ?
                WHERE id = ?
            """, (
                record["name"], record["category"], record["unit"],
                record["min_stock"], record["reorder_qty"], record["unit_cost"],
                record["supplier"], record["location"], item_id,
            ))
            _stamp(db, item_id)
            db.commit()
            log_activity("UPDATE", "inventory",
                         "Item {} edited".format(record["name"]),
                         record_type="inventory_item", record_id=item_id)
            flash("Item updated.", "success")
            return redirect(url_for("inventory.items"))
        item = active_item(item_id)

    return render_template(
        "inventory/item_form.html", item=item, categories=INVENTORY_CATEGORIES,
        units=INVENTORY_UNITS,
    )


@inventory_bp.route("/items/<int:item_id>/deactivate", methods=["POST"])
@perm("inventory_items")
def deactivate_item(item_id):
    item = active_item(item_id)
    db = get_db()
    db.execute("UPDATE inventory_items SET active = 0 WHERE id = ?", (item_id,))
    _stamp(db, item_id)
    db.commit()
    log_activity("DELETE", "inventory",
                 "Item {} ({}) deactivated".format(item["name"], item["sku"]),
                 record_type="inventory_item", record_id=item_id)
    flash("Item deactivated.", "success")
    return redirect(url_for("inventory.items"))


@inventory_bp.route("/movement", methods=["GET", "POST"])
@perm("inventory_movements")
def stock_movement():
    if request.method == "POST":
        db = get_db()
        try:
            item_id = int(request.form.get("item_id", ""))
        except (TypeError, ValueError):
            item_id = 0
        item = db.execute(
            "SELECT * FROM inventory_items WHERE id = ? AND active = 1",
            (item_id,),
        ).fetchone()
        movement_type = request.form.get("movement_type", "")
        reference_type = request.form.get("reference_type", "Manual")
        try:
            quantity = _number(request.form.get("quantity", ""), "Quantity",
                               minimum=None)
        except ValueError as error:
            quantity = None
            flash(str(error), "error")

        try:
            purchase_order = purchase_order_ref(
                db, request.form.get("procurement_order_id"))
            order_refused = False
        except ValueError as error:
            purchase_order = None
            order_refused = True
            flash(str(error), "error")

        if item is None:
            flash("Choose an active inventory item.", "error")
        elif movement_type not in ("IN", "OUT"):
            flash("Choose whether this is stock in or stock out.", "error")
        elif reference_type not in INVENTORY_REFERENCES:
            flash("Choose a related module.", "error")
        elif quantity is not None and quantity <= 0:
            flash("Quantity must be greater than zero.", "error")
        elif quantity is not None and not order_refused:
            if movement_type == "IN":
                db.execute(
                    "UPDATE inventory_items SET quantity = quantity + ?"
                    " WHERE id = ?", (quantity, item_id),
                )
            else:
                # The stock floor is enforced by the update itself, so a
                # concurrent issue cannot overspend the same balance.
                taken = db.execute(
                    "UPDATE inventory_items SET quantity = quantity - ?"
                    " WHERE id = ? AND quantity >= ?",
                    (quantity, item_id, quantity),
                ).rowcount
                if not taken:
                    db.rollback()
                    log_activity("CREATE", "inventory",
                                 "Stock out refused: {} {} of {} on hand".format(
                                     item["quantity"], item["unit"], item["name"]),
                                 outcome="FAILURE",
                                 record_type="inventory_item", record_id=item_id)
                    flash("Only {} {} of {} is available.".format(
                        item["quantity"], item["unit"], item["name"]), "error")
                    return redirect(url_for("inventory.stock_movement"))

            db.execute("""
                INSERT INTO stock_movements
                    (item_id, movement_type, quantity, reference_type,
                     reference_no, procurement_order_id, notes, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_id, movement_type, quantity, reference_type,
                (request.form.get("reference_no") or "").strip()[:80],
                purchase_order["id"] if purchase_order else None,
                (request.form.get("notes") or "").strip()[:500],
                current_user()["full_name"],
            ))
            _stamp(db, item_id)
            db.commit()
            log_activity("CREATE", "inventory",
                         "{} of {} {} recorded ({})".format(
                             movement_type, quantity, item["name"],
                             reference_type),
                         record_type="inventory_item", record_id=item_id)
            flash("Stock movement recorded.", "success")
            return redirect(url_for("inventory.history"))

    # A goods receipt opened from a purchase order arrives with the module and
    # the order number already answered; the item and quantity stay the
    # storekeeper's own decision.
    reference_type = request.args.get("reference_type", "Manual")
    if reference_type not in INVENTORY_REFERENCES:
        reference_type = "Manual"
    movement_type = request.args.get("movement_type", "IN")
    if movement_type not in ("IN", "OUT"):
        movement_type = "IN"
    draft = {
        "movement_type": movement_type,
        "reference_type": reference_type,
        "reference_no": (request.args.get("reference_no") or "").strip()[:60],
        "procurement_order_id": (
            request.args.get("procurement_order_id") or "").strip()
        if (request.args.get("procurement_order_id") or "").strip().isdigit()
        else "",
        "notes": (request.args.get("notes") or "").strip()[:500],
    }
    return render_template(
        "inventory/stock_movement.html",
        items=get_db().execute("""
            SELECT id, sku, name, unit, quantity FROM inventory_items
            WHERE active = 1 ORDER BY name
        """).fetchall(),
        references=INVENTORY_REFERENCES,
        draft=draft,
    )


@inventory_bp.route("/history")
@perm("inventory_movements")
def history():
    movements = get_db().execute("""
        SELECT m.*, i.sku, i.name, i.unit
        FROM stock_movements m
        JOIN inventory_items i ON i.id = m.item_id
        ORDER BY m.id DESC LIMIT 300
    """).fetchall()
    return render_template("inventory/history.html", movements=movements)


def register(app):
    # The counting, alert and reporting screens live beside the register rather
    # than inside it; importing them decorates the shared blueprint, and every
    # endpoint keeps the ``inventory.`` name the rail and matrix already use.
    from departments.inventory import stock_control  # noqa: F401
    app.register_blueprint(inventory_bp)
