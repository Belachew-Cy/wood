
# departments/procurement/services.py

from datetime import datetime
import re
import sqlite3


def validate_supplier(data):
    """Validate supplier information before saving."""

    name = (data.get("name") or "").strip()

    if not name:
        raise ValueError("Supplier name is required.")

    if len(name) > 150:
        raise ValueError("Supplier name is too long.")

    email = (data.get("email") or "").strip().lower()

    if email and not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+", email
    ):
        raise ValueError("Enter a valid supplier email address.")

    return {
        "name": name,
        "contact_person": (data.get("contact_person") or "").strip(),
        "phone": (data.get("phone") or "").strip(),
        "email": email,
        "address": (data.get("address") or "").strip(),
        "tax_number": (data.get("tax_number") or "").strip(),
        "supplier_type": (
            data.get("supplier_type") or "Other"
        ).strip(),
        "notes": (data.get("notes") or "").strip(),
    }


def get_supplier_statistics(db):
    """Return supplier statistics using the existing supplier table."""

    row = db.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'Active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN status = 'Inactive' THEN 1 ELSE 0 END) AS inactive
        FROM suppliers
    """).fetchone()

    with_orders = db.execute("""
        SELECT COUNT(DISTINCT s.id)
        FROM suppliers s
        JOIN purchase_orders po ON po.supplier_id = s.id
    """).fetchone()[0]

    return {
        "total": row["total"] or 0,
        "active": row["active"] or 0,
        "inactive": row["inactive"] or 0,
        "with_orders": with_orders or 0,
    }


def get_suppliers(db, search="", status="", sort="name"):
    """Return filtered supplier records and purchase history totals."""

    query = """
        SELECT
            s.*,
            COUNT(po.id) AS order_count,
            COALESCE(SUM(
                CASE WHEN po.status != 'Cancelled'
                THEN po.total_cost ELSE 0 END
            ), 0) AS ordered_value
        FROM suppliers s
        LEFT JOIN purchase_orders po
            ON po.supplier_id = s.id
        WHERE 1 = 1
    """

    params = []

    if search:
        query += """
            AND (
                s.name LIKE ?
                OR s.contact_person LIKE ?
                OR s.phone LIKE ?
                OR s.email LIKE ?
            )
        """
        term = f"%{search.strip()}%"
        params.extend([term, term, term, term])

    if status in ("Active", "Inactive"):
        query += " AND s.status = ?"
        params.append(status)

    query += " GROUP BY s.id"

    sort_options = {
        "name": "s.name ASC",
        "recent": "s.id DESC",
        "orders": "order_count DESC",
    }

    query += " ORDER BY " + sort_options.get(sort, "s.name ASC")

    return db.execute(query, params).fetchall()


def create_supplier(db, data):
    """Create a supplier in the existing suppliers table."""

    supplier = validate_supplier(data)

    duplicate = db.execute("""
        SELECT id
        FROM suppliers
        WHERE LOWER(name) = LOWER(?)
        LIMIT 1
    """, (supplier["name"],)).fetchone()

    if duplicate:
        raise ValueError("A supplier with this name already exists.")

    cursor = db.execute("""
        INSERT INTO suppliers (
            name,
            contact_person,
            phone,
            email,
            address,
            tax_number,
            supplier_type,
            notes,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?)
    """, (
        supplier["name"],
        supplier["contact_person"],
        supplier["phone"],
        supplier["email"],
        supplier["address"],
        supplier["tax_number"],
        supplier["supplier_type"],
        supplier["notes"],
        datetime.utcnow().isoformat(timespec="seconds"),
    ))

    return cursor.lastrowid


def update_supplier(db, supplier_id, data):
    """Update an existing supplier."""

    supplier = validate_supplier(data)

    existing = db.execute("""
        SELECT id FROM suppliers WHERE id = ?
    """, (supplier_id,)).fetchone()

    if not existing:
        raise ValueError("Supplier not found.")

    duplicate = db.execute("""
        SELECT id FROM suppliers
        WHERE LOWER(name) = LOWER(?) AND id != ?
    """, (supplier["name"], supplier_id)).fetchone()

    if duplicate:
        raise ValueError("Another supplier already uses this name.")

    db.execute("""
        UPDATE suppliers
        SET
            name = ?,
            contact_person = ?,
            phone = ?,
            email = ?,
            address = ?,
            tax_number = ?,
            supplier_type = ?,
            notes = ?
        WHERE id = ?
    """, (
        supplier["name"],
        supplier["contact_person"],
        supplier["phone"],
        supplier["email"],
        supplier["address"],
        supplier["tax_number"],
        supplier["supplier_type"],
        supplier["notes"],
        supplier_id,
    ))


def toggle_supplier_status(db, supplier_id):
    """Activate or deactivate a supplier."""

    supplier = db.execute("""
        SELECT id, status
        FROM suppliers
        WHERE id = ?
    """, (supplier_id,)).fetchone()

    if not supplier:
        raise ValueError("Supplier not found.")

    new_status = (
        "Inactive"
        if supplier["status"] == "Active"
        else "Active"
    )

    db.execute("""
        UPDATE suppliers
        SET status = ?
        WHERE id = ?
    """, (new_status, supplier_id))

    return new_status
