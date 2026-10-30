"""Stock items, movements and history."""

from core.access import current_user
from core.access import perm
from core.db import get_db
from flask import Blueprint
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from services.audit import change_set
from services.audit import log_activity
from services.recency import stamp as stamp_record
import sqlite3


INVENTORY_CATEGORIES = (
    "Timber", "Plywood", "MDF", "Hardware", "Paint & Glue",
    "Finished Product", "Other",
)


INVENTORY_UNITS = ("pcs", "sheet", "meter", "m3", "kg", "liter", "box", "set")


INVENTORY_REFERENCES = (
    "Purchasing", "Production", "Sales", "Adjustment", "Manual",
)


inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def inventory_item_fields(require_sku):
    """Read and validate the item form, or return None after flashing."""
    sku = request.form.get("sku", "").strip().upper()
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    unit = request.form.get("unit", "").strip()
    supplier = request.form.get("supplier", "").strip()
    location = request.form.get("location", "").strip()

    required = [name, category, unit] + ([sku] if require_sku else [])
    if not all(required):
        flash("Complete every required field.", "error")
        return None

    try:
        min_stock = float(request.form.get("min_stock", 0) or 0)
        unit_cost = float(request.form.get("unit_cost", 0) or 0)
    except ValueError:
        flash("Minimum stock and unit cost must be numbers.", "error")
        return None
    if min(min_stock, unit_cost) < 0:
        flash("Minimum stock and unit cost cannot be negative.", "error")
        return None

    return {
        "sku": sku, "name": name, "category": category, "unit": unit,
        "supplier": supplier, "location": location,
        "min_stock": min_stock, "unit_cost": unit_cost,
    }


def inventory_item(item_id):
    return get_db().execute(
        "SELECT * FROM inventory_items WHERE id = ?", (item_id,)
    ).fetchone()


@inventory_bp.route("/")
@perm("inventory")
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
    return render_template("inventory/dashboard.html", summary=summary, recent=recent)


@inventory_bp.route("/items")
@perm("inventory")
def items():
    search = request.args.get("q", "").strip()
    like = f"%{search}%"
    rows = get_db().execute("""
        SELECT * FROM inventory_items
        WHERE active = 1
          AND (sku LIKE ? OR name LIKE ? OR category LIKE ?)
        ORDER BY name
    """, (like, like, like)).fetchall()
    return render_template("inventory/items.html", items=rows, search=search)


@inventory_bp.route("/items/new", methods=["GET", "POST"])
@perm("inventory")
def new_item():
    if request.method == "POST":
        record = inventory_item_fields(require_sku=True)
        if record is not None:
            opening = request.form.get("opening_qty", 0)
            try:
                opening = float(opening or 0)
            except ValueError:
                opening = -1
            if opening < 0:
                flash("Opening quantity cannot be negative.", "error")
            else:
                db = get_db()
                try:
                    cursor = db.execute("""
                        INSERT INTO inventory_items
                            (sku, name, category, unit, quantity, min_stock,
                             unit_cost, supplier, location)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        record["sku"], record["name"], record["category"],
                        record["unit"], opening, record["min_stock"],
                        record["unit_cost"], record["supplier"], record["location"],
                    ))
                    if opening > 0:
                        db.execute("""
                            INSERT INTO stock_movements
                                (item_id, movement_type, quantity, reference_type,
                                 reference_no, notes, created_by)
                            VALUES (?, 'IN', ?, 'Opening Balance', 'OPENING',
                                    'Initial stock', ?)
                        """, (cursor.lastrowid, opening, current_user()["full_name"]))
                    stamp_record(db, "inventory_items", cursor.lastrowid)
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
                                 record_type="inventory_item", record_id=cursor.lastrowid)
                    flash("Inventory item created.", "success")
                    return redirect(url_for("inventory.items"))
    return render_template(
        "inventory/item_form.html", item=None, categories=INVENTORY_CATEGORIES,
        units=INVENTORY_UNITS,
    )


@inventory_bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@perm("inventory")
def edit_item(item_id):
    item = inventory_item(item_id)
    if item is None:
        abort(404)

    if request.method == "POST":
        record = inventory_item_fields(require_sku=False)
        if record is not None:
            db = get_db()
            previous_values, updated_values = change_set(item, {
                "name": record["name"], "category": record["category"],
                "unit": record["unit"], "min_stock": record["min_stock"],
                "unit_cost": record["unit_cost"], "supplier": record["supplier"],
                "location": record["location"],
            })
            db.execute("""
                UPDATE inventory_items
                SET name = ?, category = ?, unit = ?, min_stock = ?,
                    unit_cost = ?, supplier = ?, location = ?
                WHERE id = ?
            """, (
                record["name"], record["category"], record["unit"],
                record["min_stock"], record["unit_cost"], record["supplier"],
                record["location"], item_id,
            ))
            stamp_record(db, "inventory_items", item_id)
            db.commit()
            log_activity("UPDATE", "inventory",
                         "Item {} edited".format(record["name"]),
                         record_type="inventory_item", record_id=item_id,
                         previous=previous_values, updated=updated_values)
            flash("Item updated.", "success")
            return redirect(url_for("inventory.items"))
        item = inventory_item(item_id)

    return render_template(
        "inventory/item_form.html", item=item, categories=INVENTORY_CATEGORIES,
        units=INVENTORY_UNITS,
    )


@inventory_bp.route("/items/<int:item_id>/deactivate", methods=["POST"])
@perm("inventory")
def deactivate_item(item_id):
    db = get_db()
    db.execute("UPDATE inventory_items SET active = 0 WHERE id = ?", (item_id,))
    stamp_record(db, "inventory_items", item_id)
    db.commit()
    log_activity("DELETE", "inventory",
                 "Item #{} deactivated".format(item_id),
                 record_type="inventory_item", record_id=item_id)
    flash("Item deactivated.", "success")
    return redirect(url_for("inventory.items"))


@inventory_bp.route("/movement", methods=["GET", "POST"])
@perm("inventory")
def stock_movement():
    if request.method == "POST":
        db = get_db()
        try:
            item_id = int(request.form.get("item_id", ""))
        except ValueError:
            item_id = 0
        item = db.execute(
            "SELECT * FROM inventory_items WHERE id = ? AND active = 1", (item_id,)
        ).fetchone()
        movement_type = request.form.get("movement_type", "")
        reference_type = request.form.get("reference_type", "Manual")

        try:
            quantity = float(request.form.get("quantity", ""))
        except ValueError:
            quantity = 0

        if item is None:
            flash("Choose an active inventory item.", "error")
        elif movement_type not in ("IN", "OUT"):
            flash("Choose whether this is stock in or stock out.", "error")
        elif reference_type not in INVENTORY_REFERENCES:
            flash("Choose a related module.", "error")
        elif quantity <= 0:
            flash("Quantity must be greater than zero.", "error")
        else:
            if movement_type == "IN":
                db.execute(
                    "UPDATE inventory_items SET quantity = quantity + ? WHERE id = ?",
                    (quantity, item_id),
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
                    flash(
                        f"Only {item['quantity']} {item['unit']} of {item['name']}"
                        " is available.", "error")
                    return redirect(url_for("inventory.stock_movement"))

            db.execute("""
                INSERT INTO stock_movements
                    (item_id, movement_type, quantity, reference_type,
                     reference_no, notes, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                item_id, movement_type, quantity, reference_type,
                request.form.get("reference_no", "").strip(),
                request.form.get("notes", "").strip(),
                current_user()["full_name"],
            ))
            stamp_record(db, "inventory_items", item_id)
            db.commit()
            log_activity("CREATE", "inventory",
                         "{} of {} {} recorded ({})".format(
                             movement_type, quantity, item["name"], reference_type),
                         record_type="inventory_item", record_id=item_id)
            flash("Stock movement recorded.", "success")
            return redirect(url_for("inventory.history"))

    return render_template(
        "inventory/stock_movement.html",
        items=get_db().execute("""
            SELECT id, sku, name, unit, quantity FROM inventory_items
            WHERE active = 1 ORDER BY name
        """).fetchall(),
        references=INVENTORY_REFERENCES,
    )


@inventory_bp.route("/history")
@perm("inventory")
def history():
    movements = get_db().execute("""
        SELECT m.*, i.sku, i.name, i.unit
        FROM stock_movements m
        JOIN inventory_items i ON i.id = m.item_id
        ORDER BY m.id DESC LIMIT 300
    """).fetchall()
    return render_template("inventory/history.html", movements=movements)


def register(app):
    """Attach this department's blueprint to the application."""
    app.register_blueprint(inventory_bp)
