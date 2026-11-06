"""
Payroll Department - Business Services

Responsibilities:
- Employee payroll profile management
- Salary structure assignment
- Allowance and deduction management
- Salary change requests
- Salary validation
- Monthly payroll generation
- Approval workflow
- Payments
- Reports
- Statutory (tax / pension) calculations

Important:
- Routes must enforce the user's permissions before calling these services.
- Salary changes are submitted for approval; they do not immediately change
  an employee's approved salary.
"""

import calendar
import json
import sqlite3
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from services.audit import log_activity

from .models import get_db, money, timestamp, validate_date


# ---------------------------------------------------------
# Common helpers
# ---------------------------------------------------------

def _to_money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Please enter a valid salary or amount.")

    if not amount.is_finite() or amount < 0:
        raise ValueError("The amount must be a non-negative number.")

    return amount.quantize(Decimal("0.01"))


def _payroll_decimal(value):
    return Decimal(str(value or 0))


def _payroll_round(value):
    return _payroll_decimal(value).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _require_text(value, field_name):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{field_name} is required.")
    return value


def _get_employee(conn, employee_id):
    employee = conn.execute(
        """
        SELECT id, employee_code, full_name, department, job_title
        FROM employees
        WHERE id = ?
        """,
        (employee_id,)
    ).fetchone()

    if not employee:
        raise ValueError("Employee not found.")

    return employee


def _get_active_structure(conn, structure_id):
    structure = conn.execute(
        """
        SELECT *
        FROM payroll_salary_structures
        WHERE id = ? AND status = 'Active'
        """,
        (structure_id,)
    ).fetchone()

    if not structure:
        raise ValueError("The selected salary structure is not active.")

    return structure


def _calculate_component(amount, calculation_type, basic_salary):
    amount = _payroll_decimal(amount)

    if calculation_type == "Percentage":
        return _payroll_round(basic_salary * amount / Decimal("100"))
    if calculation_type == "Fixed":
        return _payroll_round(amount)

    raise ValueError("Unsupported payroll calculation type.")


# ---------------------------------------------------------
# Employee payroll profiles
# ---------------------------------------------------------

def get_employee_payroll_profile(employee_id):
    conn = get_db()

    try:
        return conn.execute(
            """
            SELECT
                e.id AS employee_id,
                e.employee_code,
                e.full_name,
                e.department,
                e.job_title,
                p.id AS payroll_profile_id,
                p.salary_grade_id,
                p.salary_structure_id,
                s.basic_salary,
                p.effective_date,
                p.status
            FROM employees e
            LEFT JOIN payroll_employee_profiles p
                ON p.employee_id = e.id
            LEFT JOIN payroll_salary_structures s
                ON s.id = p.salary_structure_id
            WHERE e.id = ?
            """,
            (employee_id,)
        ).fetchone()
    finally:
        conn.close()


def get_employee_profiles(search="", department="", status=""):
    conn = get_db()

    try:
        query = """
            SELECT
                e.id,
                e.employee_code,
                e.full_name,
                e.department,
                e.job_title,
                p.id AS payroll_profile_id,
                p.status,
                g.grade_name
            FROM employees e
            LEFT JOIN payroll_employee_profiles p
                ON p.employee_id = e.id
            LEFT JOIN payroll_salary_grades g
                ON g.id = p.salary_grade_id
            WHERE 1=1
        """
        params = []

        if search:
            query += " AND (e.full_name LIKE ? OR e.employee_code LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])

        if department:
            query += " AND e.department = ?"
            params.append(department)

        if status:
            query += " AND p.status = ?"
            params.append(status)

        query += " ORDER BY e.full_name"

        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def get_employees_without_payroll_profile():
    conn = get_db()

    try:
        return conn.execute("""
            SELECT e.id, e.employee_code, e.full_name, e.department
            FROM employees e
            LEFT JOIN payroll_employee_profiles p
                ON p.employee_id = e.id
            WHERE p.id IS NULL
            ORDER BY e.full_name
        """).fetchall()
    finally:
        conn.close()


def create_employee_profile(data, created_by=None):
    """
    Create an employee payroll profile from a submitted form dict.
    """
    employee_id = data.get("employee_id")
    grade_id = data.get("salary_grade_id") or None
    structure_id = data.get("salary_structure_id") or None
    effective_date = data.get("effective_date")

    if not employee_id:
        raise ValueError("Employee is required.")

    conn = get_db()

    try:
        _get_employee(conn, employee_id)

        existing = conn.execute(
            "SELECT id FROM payroll_employee_profiles WHERE employee_id = ?",
            (employee_id,)
        ).fetchone()
        if existing:
            raise ValueError(
                "This employee already has a payroll profile."
            )

        effective_date = validate_date(
            effective_date or timestamp()[:10]
        )

        with conn:
            conn.execute("""
                INSERT INTO payroll_employee_profiles (
                    employee_id, salary_grade_id, salary_structure_id,
                    effective_date, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?, ?)
            """, (
                employee_id, grade_id, structure_id,
                effective_date, timestamp(), timestamp()
            ))

        return True
    finally:
        conn.close()


def update_employee_profile(employee_id, data, updated_by=None):
    grade_id = data.get("salary_grade_id") or None
    structure_id = data.get("salary_structure_id") or None
    effective_date = data.get("effective_date")

    conn = get_db()

    try:
        _get_employee(conn, employee_id)

        profile = conn.execute(
            "SELECT id FROM payroll_employee_profiles WHERE employee_id = ?",
            (employee_id,)
        ).fetchone()
        if not profile:
            raise ValueError("Payroll profile not found.")

        effective_date = validate_date(
            effective_date or timestamp()[:10]
        )

        with conn:
            conn.execute("""
                UPDATE payroll_employee_profiles
                SET salary_grade_id = ?,
                    salary_structure_id = ?,
                    effective_date = ?,
                    updated_at = ?
                WHERE employee_id = ?
            """, (
                grade_id, structure_id,
                effective_date, timestamp(), employee_id
            ))

        return True
    finally:
        conn.close()


def get_employee_salary_details(employee_id):
    """Return salary summary + structure + allowances + deductions + history."""
    conn = get_db()

    try:
        profile = conn.execute("""
            SELECT
                p.*,
                s.structure_name,
                s.basic_salary AS structure_basic,
                s.effective_date AS structure_effective,
                g.grade_name
            FROM payroll_employee_profiles p
            LEFT JOIN payroll_salary_structures s
                ON s.id = p.salary_structure_id
            LEFT JOIN payroll_salary_grades g
                ON g.id = p.salary_grade_id
            WHERE p.employee_id = ?
        """, (employee_id,)).fetchone()

        allowances = conn.execute("""
            SELECT a.*, t.allowance_name, t.calculation_type
            FROM payroll_employee_allowances a
            JOIN payroll_allowance_types t ON t.id = a.allowance_type_id
            WHERE a.employee_id = ? AND a.status = 'Active'
        """, (employee_id,)).fetchall()

        deductions = conn.execute("""
            SELECT d.*, t.deduction_name, t.calculation_type
            FROM payroll_employee_deductions d
            JOIN payroll_deduction_types t ON t.id = d.deduction_type_id
            WHERE d.employee_id = ? AND d.status = 'Active'
        """, (employee_id,)).fetchall()

        history = conn.execute("""
            SELECT old_salary AS previous_salary, new_salary,
                   effective_date, reason, approval_status AS status
            FROM payroll_salary_history
            WHERE employee_id = ?
            ORDER BY effective_date DESC
        """, (employee_id,)).fetchall()

        basic = Decimal(str((profile["structure_basic"] if profile else 0) or 0))
        total_allowances = sum(
            (_to_money(r["amount"]) for r in allowances), Decimal("0.00")
        )
        total_deductions = sum(
            (_to_money(r["amount"]) for r in deductions), Decimal("0.00")
        )

        return {
            "basic_salary": basic,
            "total_allowances": total_allowances,
            "total_deductions": total_deductions,
            "net_salary": basic + total_allowances - total_deductions,
            "structure": dict(profile) if profile else None,
            "effective_date": profile["structure_effective"] if profile else None,
            "allowances": allowances,
            "deductions": deductions,
            "history": history,
        }
    finally:
        conn.close()


# ---------------------------------------------------------
# Salary grades / structures / allowances / deductions
# ---------------------------------------------------------

def get_salary_grades():
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM payroll_salary_grades ORDER BY grade_name"
        ).fetchall()
    finally:
        conn.close()


def create_salary_grade(data, created_by=None):
    name = _require_text(data.get("grade_name"), "Grade name")
    minimum = _to_money(data.get("minimum_salary") or 0)
    maximum = _to_money(data.get("maximum_salary") or 0)

    if minimum > maximum:
        raise ValueError("Minimum salary cannot exceed maximum salary.")

    conn = get_db()
    try:
        with conn:
            conn.execute("""
                INSERT INTO payroll_salary_grades (
                    grade_name, description, minimum_salary,
                    maximum_salary, status, created_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?)
            """, (
                name, data.get("description", ""),
                str(minimum), str(maximum), timestamp()
            ))
        return True
    finally:
        conn.close()


def update_salary_grade(grade_id, data, updated_by=None):
    name = _require_text(data.get("grade_name"), "Grade name")
    minimum = _to_money(data.get("minimum_salary") or 0)
    maximum = _to_money(data.get("maximum_salary") or 0)

    if minimum > maximum:
        raise ValueError("Minimum salary cannot exceed maximum salary.")

    conn = get_db()
    try:
        with conn:
            conn.execute("""
                UPDATE payroll_salary_grades
                SET grade_name = ?, description = ?,
                    minimum_salary = ?, maximum_salary = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                name, data.get("description", ""),
                str(minimum), str(maximum),
                timestamp(), grade_id
            ))
        return True
    finally:
        conn.close()


def deactivate_salary_grade(grade_id, updated_by=None):
    conn = get_db()
    try:
        with conn:
            conn.execute("""
                UPDATE payroll_salary_grades
                SET status = 'Inactive', updated_at = ?
                WHERE id = ?
            """, (timestamp(), grade_id))
        return True
    finally:
        conn.close()


def get_salary_structures():
    conn = get_db()
    try:
        return conn.execute("""
            SELECT s.*, g.grade_name,
                   g.minimum_salary, g.maximum_salary
            FROM payroll_salary_structures s
            LEFT JOIN payroll_salary_grades g
                ON g.id = s.salary_grade_id
            ORDER BY s.created_at DESC
        """).fetchall()
    finally:
        conn.close()


def create_salary_structure(data, created_by=None):
    name = _require_text(data.get("structure_name"), "Structure name")
    grade_id = data.get("grade_id") or None
    basic = _to_money(data.get("basic_salary") or 0)
    effective_date = validate_date(
        data.get("effective_date") or timestamp()[:10]
    )

    conn = get_db()
    try:
        if grade_id:
            grade = conn.execute("""
                SELECT minimum_salary, maximum_salary
                FROM payroll_salary_grades
                WHERE id = ? AND status = 'Active'
            """, (grade_id,)).fetchone()
            if not grade:
                raise ValueError("Selected salary grade is invalid.")
            if not (
                Decimal(str(grade["minimum_salary"]))
                <= basic
                <= Decimal(str(grade["maximum_salary"]))
            ):
                raise ValueError(
                    "Basic salary must be within the selected grade."
                )

        with conn:
            conn.execute("""
                INSERT INTO payroll_salary_structures (
                    structure_name, salary_grade_id, basic_salary,
                    description, effective_date, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'Active', ?)
            """, (
                name, grade_id, str(basic),
                data.get("description", ""),
                effective_date, timestamp()
            ))
        return True
    finally:
        conn.close()


def update_salary_structure(structure_id, data, updated_by=None):
    name = _require_text(data.get("structure_name"), "Structure name")
    grade_id = data.get("grade_id") or None
    basic = _to_money(data.get("basic_salary") or 0)
    effective_date = validate_date(
        data.get("effective_date") or timestamp()[:10]
    )

    conn = get_db()
    try:
        with conn:
            conn.execute("""
                UPDATE payroll_salary_structures
                SET structure_name = ?, salary_grade_id = ?,
                    basic_salary = ?, description = ?,
                    effective_date = ?, updated_at = ?
                WHERE id = ?
            """, (
                name, grade_id, str(basic),
                data.get("description", ""),
                effective_date, timestamp(), structure_id
            ))
        return True
    finally:
        conn.close()


def toggle_salary_structure(structure_id, updated_by=None):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status FROM payroll_salary_structures WHERE id = ?",
            (structure_id,)
        ).fetchone()
        if not row:
            raise ValueError("Salary structure not found.")
        new_status = "Inactive" if row["status"] == "Active" else "Active"
        with conn:
            conn.execute("""
                UPDATE payroll_salary_structures
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (new_status, timestamp(), structure_id))
        return True
    finally:
        conn.close()


def get_allowance_types():
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM payroll_allowance_types ORDER BY allowance_name"
        ).fetchall()
    finally:
        conn.close()


def create_allowance_type(data, created_by=None):
    name = _require_text(data.get("allowance_name"), "Allowance name")
    conn = get_db()
    try:
        with conn:
            conn.execute("""
                INSERT INTO payroll_allowance_types (
                    allowance_name, description, calculation_type,
                    default_amount, taxable, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'Active', ?)
            """, (
                name,
                data.get("description", ""),
                data.get("calculation_type", "Fixed"),
                str(_to_money(data.get("default_amount") or 0)),
                1 if str(data.get("is_taxable", "0")) in ("1", "True", "true") else 0,
                timestamp()
            ))
        return True
    finally:
        conn.close()


def update_allowance_type(allowance_id, data, updated_by=None):
    conn = get_db()
    try:
        with conn:
            conn.execute("""
                UPDATE payroll_allowance_types
                SET allowance_name = ?, description = ?,
                    calculation_type = ?, default_amount = ?,
                    taxable = ?
                WHERE id = ?
            """, (
                _require_text(data.get("allowance_name"), "Allowance name"),
                data.get("description", ""),
                data.get("calculation_type", "Fixed"),
                str(_to_money(data.get("default_amount") or 0)),
                1 if str(data.get("is_taxable", "0")) in ("1", "True", "true") else 0,
                allowance_id
            ))
        return True
    finally:
        conn.close()


def toggle_allowance_type(allowance_id, updated_by=None):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status FROM payroll_allowance_types WHERE id = ?",
            (allowance_id,)
        ).fetchone()
        if not row:
            raise ValueError("Allowance not found.")
        new_status = "Inactive" if row["status"] == "Active" else "Active"
        with conn:
            conn.execute("""
                UPDATE payroll_allowance_types
                SET status = ? WHERE id = ?
            """, (new_status, allowance_id))
        return True
    finally:
        conn.close()


def get_deduction_types():
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM payroll_deduction_types ORDER BY deduction_name"
        ).fetchall()
    finally:
        conn.close()


def create_deduction_type(data, created_by=None):
    name = _require_text(data.get("deduction_name"), "Deduction name")
    conn = get_db()
    try:
        with conn:
            conn.execute("""
                INSERT INTO payroll_deduction_types (
                    deduction_name, description, calculation_type,
                    default_amount, status, created_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?)
            """, (
                name,
                data.get("description", ""),
                data.get("calculation_type", "Fixed"),
                str(_to_money(data.get("default_amount") or 0)),
                timestamp()
            ))
        return True
    finally:
        conn.close()


def update_deduction_type(deduction_id, data, updated_by=None):
    conn = get_db()
    try:
        with conn:
            conn.execute("""
                UPDATE payroll_deduction_types
                SET deduction_name = ?, description = ?,
                    calculation_type = ?, default_amount = ?
                WHERE id = ?
            """, (
                _require_text(data.get("deduction_name"), "Deduction name"),
                data.get("description", ""),
                data.get("calculation_type", "Fixed"),
                str(_to_money(data.get("default_amount") or 0)),
                deduction_id
            ))
        return True
    finally:
        conn.close()


def toggle_deduction_type(deduction_id, updated_by=None):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status FROM payroll_deduction_types WHERE id = ?",
            (deduction_id,)
        ).fetchone()
        if not row:
            raise ValueError("Deduction not found.")
        new_status = "Inactive" if row["status"] == "Active" else "Active"
        with conn:
            conn.execute("""
                UPDATE payroll_deduction_types
                SET status = ? WHERE id = ?
            """, (new_status, deduction_id))
        return True
    finally:
        conn.close()


# ---------------------------------------------------------
# Salary history / list of employees
# ---------------------------------------------------------

def get_salary_history(employee_id=None, start_date=None, end_date=None):
    conn = get_db()
    try:
        query = """
            SELECT
                h.*,
                h.old_salary AS previous_salary,
                h.approval_status AS status,
                e.employee_code,
                e.full_name AS employee_name
            FROM payroll_salary_history h
            JOIN employees e ON e.id = h.employee_id
            WHERE 1=1
        """
        params = []

        if employee_id:
            query += " AND h.employee_id = ?"
            params.append(employee_id)
        if start_date:
            query += " AND h.effective_date >= ?"
            params.append(validate_date(start_date))
        if end_date:
            query += " AND h.effective_date <= ?"
            params.append(validate_date(end_date))

        query += " ORDER BY h.effective_date DESC, h.id DESC"

        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def get_payroll_employees():
    conn = get_db()
    try:
        return conn.execute("""
            SELECT id, employee_code, full_name, department, job_title
            FROM employees
            ORDER BY full_name
        """).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------
# Monthly payroll calculation
# ---------------------------------------------------------

# Overtime is paid from the hours HR verified, at the rate the basic salary
# implies. Both settings are optional: a desk that never set them still gets the
# statutory 208-hour month and the 1.5 multiplier.
OVERTIME_STANDARD_MONTHLY_HOURS = Decimal("208")
OVERTIME_MULTIPLIER = Decimal("1.5")
OVERTIME_SETTING_KEYS = ("overtime_standard_monthly_hours", "overtime_multiplier")


def validate_payroll_month(payroll_month):
    try:
        datetime.strptime(payroll_month, "%Y-%m")
    except (TypeError, ValueError):
        raise ValueError("Invalid payroll month. Use YYYY-MM.")
    return payroll_month


def _month_end(payroll_month):
    year, month = (int(part) for part in payroll_month.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _overtime_settings(conn):
    """Hourly-rate divisor and multiplier, from HR settings when they are set."""
    hours, multiplier = OVERTIME_STANDARD_MONTHLY_HOURS, OVERTIME_MULTIPLIER

    try:
        rows = conn.execute("""
            SELECT setting_key, setting_value
            FROM hr_settings
            WHERE setting_key IN (?, ?)
        """, OVERTIME_SETTING_KEYS).fetchall()
    except sqlite3.Error:
        rows = []

    values = {row["setting_key"]: row["setting_value"] for row in rows}

    for key, fallback in (
            ("overtime_standard_monthly_hours", hours),
            ("overtime_multiplier", multiplier)):
        try:
            parsed = Decimal(str(values.get(key) or ""))
            if parsed <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            parsed = fallback
        if key == "overtime_standard_monthly_hours":
            hours = parsed
        else:
            multiplier = parsed

    return {"standard_monthly_hours": hours, "multiplier": multiplier}


def calculate_payroll_overtime(
    basic_salary, standard_monthly_hours, approved_hours, multiplier
):
    """Hourly rate times approved hours times the multiplier."""
    basic = Decimal(str(basic_salary or 0))
    hours = Decimal(str(standard_monthly_hours or 0))
    approved = Decimal(str(approved_hours or 0))
    factor = Decimal(str(multiplier or 0))

    if basic < 0:
        raise ValueError("Basic salary cannot be negative.")
    if hours <= 0:
        raise ValueError("Standard monthly hours must be greater than zero.")
    if approved <= 0:
        raise ValueError("Approved overtime hours must be greater than zero.")
    if factor <= 0:
        raise ValueError("Overtime multiplier must be greater than zero.")

    hourly_rate = basic / hours

    return {
        "hourly_rate": _payroll_round(hourly_rate),
        "approved_hours": approved,
        "multiplier": factor,
        "overtime_amount": _payroll_round(hourly_rate * approved * factor),
    }


def get_approved_overtime_rows(conn, employee_id, payroll_month):
    """HR-approved overtime for a month payroll has not imported yet."""
    return conn.execute("""
        SELECT r.id, r.overtime_date, r.approved_hours, r.multiplier,
               r.attendance_id
        FROM hr_overtime_requests r
        WHERE r.employee_id = ?
          AND r.status = 'Approved'
          AND substr(r.overtime_date, 1, 7) = ?
          AND NOT EXISTS (
              SELECT 1 FROM payroll_overtime_imports i
              WHERE i.overtime_request_id = r.id
          )
        ORDER BY r.overtime_date, r.id
    """, (employee_id, payroll_month)).fetchall()


def _plan_overtime(conn, rows, basic_salary):
    """Price each approved request once and total the earnings."""
    settings = _overtime_settings(conn)
    items, total = [], Decimal("0.00")

    for row in rows:
        hours = _payroll_decimal(row["approved_hours"])
        if hours <= 0:
            continue

        multiplier = _payroll_decimal(row["multiplier"])
        if multiplier <= 0:
            multiplier = settings["multiplier"]

        calculated = calculate_payroll_overtime(
            basic_salary=basic_salary,
            standard_monthly_hours=settings["standard_monthly_hours"],
            approved_hours=hours,
            multiplier=multiplier,
        )

        items.append({
            "overtime_request_id": row["id"],
            "overtime_date": row["overtime_date"],
            "approved_hours": calculated["approved_hours"],
            "hourly_rate": calculated["hourly_rate"],
            "multiplier": calculated["multiplier"],
            "amount": calculated["overtime_amount"],
        })
        total += calculated["overtime_amount"]

    return {"items": items, "amount": _payroll_round(total)}


def _allowance_rows(conn, employee_id):
    return conn.execute("""
        SELECT a.amount, t.calculation_type, t.taxable
        FROM payroll_employee_allowances a
        JOIN payroll_allowance_types t ON t.id = a.allowance_type_id
        WHERE a.employee_id = ? AND a.status = 'Active'
    """, (employee_id,)).fetchall()


def _salary_deduction_rows(conn, employee_id):
    return conn.execute("""
        SELECT d.amount, t.calculation_type
        FROM payroll_employee_deductions d
        JOIN payroll_deduction_types t ON t.id = d.deduction_type_id
        WHERE d.employee_id = ? AND d.status = 'Active'
    """, (employee_id,)).fetchall()


def _month_components(conn, employee_id, payroll_month, basic_salary,
                      overtime_amount):
    """Every figure one payslip needs, with overtime and tax applied once."""
    allowances = taxable_allowances = Decimal("0.00")
    for row in _allowance_rows(conn, employee_id):
        amount = _calculate_component(
            row["amount"], row["calculation_type"], basic_salary)
        allowances += amount
        if row["taxable"]:
            taxable_allowances += amount

    salary_deductions = sum(
        (_calculate_component(
            row["amount"], row["calculation_type"], basic_salary)
         for row in _salary_deduction_rows(conn, employee_id)),
        Decimal("0.00"))

    overtime_amount = _payroll_round(overtime_amount)
    gross = _payroll_round(basic_salary + allowances + overtime_amount)

    statutory = calculate_employee_statutory_breakdown(
        basic_salary=basic_salary,
        taxable_allowances=taxable_allowances + overtime_amount,
        payroll_date=_month_end(payroll_month),
        pensionable_salary=basic_salary,
    )

    deductions = _payroll_round(
        salary_deductions + statutory["total_employee_statutory"])

    if deductions > gross:
        raise ValueError(
            "Deductions exceed gross salary for employee #{}.".format(
                employee_id))

    return {
        "allowances": _payroll_round(allowances),
        "overtime_amount": overtime_amount,
        "salary_deductions": _payroll_round(salary_deductions),
        "statutory": statutory,
        "gross_salary": gross,
        "deductions": deductions,
        "net_salary": _payroll_round(gross - deductions),
    }


def _write_statutory_rows(db, payroll_id, employee_id, breakdown):
    db.execute(
        "DELETE FROM payroll_statutory_breakdown WHERE payroll_id = ?",
        (payroll_id,))

    for item in breakdown["details"]:
        db.execute("""
            INSERT INTO payroll_statutory_breakdown (
                payroll_id, employee_id, deduction_type,
                calculation_base, applied_rate, amount, rule_id,
                calculation_details
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payroll_id, employee_id, item["type"],
            str(item["base"]),
            str(item["rate"] or 0),
            str(item["amount"]),
            item["rule_id"],
            json.dumps({
                "calculation_type": item["type"],
                "base": str(item["base"]),
                "rate": str(item["rate"]) if item["rate"] is not None else None,
                "amount": str(item["amount"]),
            }),
        ))


def _write_overtime_imports(db, payroll_id, employee_id, payroll_month,
                            items, imported_by):
    for item in items:
        db.execute("""
            INSERT INTO payroll_overtime_imports (
                overtime_request_id, payroll_id, employee_id, payroll_month,
                overtime_date, approved_hours, hourly_rate, multiplier,
                amount, imported_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["overtime_request_id"], payroll_id, employee_id,
            payroll_month, item["overtime_date"],
            str(item["approved_hours"]), str(item["hourly_rate"]),
            str(item["multiplier"]), str(item["amount"]), imported_by,
        ))
        db.execute("""
            INSERT INTO hr_overtime_audit_logs (
                overtime_request_id, action, previous_status,
                new_status, performed_by, comment
            )
            VALUES (?, 'Payroll Import', 'Approved', 'Approved', ?, ?)
        """, (
            item["overtime_request_id"],
            imported_by,
            "Imported into the {} payroll run.".format(payroll_month),
        ))
    return len(items)


def calculate_employee_monthly_payroll(employee_id, payroll_month):
    validate_payroll_month(payroll_month)
    db = get_db()

    try:
        profile = db.execute("""
            SELECT p.employee_id, s.basic_salary
            FROM payroll_employee_profiles p
            JOIN payroll_salary_structures s
                ON s.id = p.salary_structure_id
            WHERE p.employee_id = ?
              AND p.status = 'Active'
              AND s.status = 'Active'
        """, (employee_id,)).fetchone()

        if not profile:
            raise ValueError(
                "Employee has no active payroll profile or salary structure.")

        basic_salary = _payroll_round(profile["basic_salary"])
        overtime = _plan_overtime(
            db, get_approved_overtime_rows(db, employee_id, payroll_month),
            basic_salary)
        components = _month_components(
            db, employee_id, payroll_month, basic_salary, overtime["amount"])

        return {
            "employee_id": employee_id,
            "payroll_month": payroll_month,
            "basic_salary": basic_salary,
            "allowances": components["allowances"],
            "overtime_amount": components["overtime_amount"],
            "salary_deductions": components["salary_deductions"],
            "statutory": components["statutory"],
            "deductions": components["deductions"],
            "gross_salary": components["gross_salary"],
            "net_salary": components["net_salary"],
            "overtime_items": overtime["items"],
        }
    finally:
        db.close()


def generate_monthly_payroll(payroll_month, created_by):
    validate_payroll_month(payroll_month)
    db = get_db()

    try:
        employees = db.execute("""
            SELECT employee_id
            FROM payroll_employee_profiles
            WHERE status = 'Active'
        """).fetchall()

        created_count = 0
        skipped_count = 0
        overtime_count = 0

        for employee in employees:
            employee_id = employee["employee_id"]

            existing = db.execute("""
                SELECT id FROM payroll_records
                WHERE employee_id = ? AND payroll_month = ?
            """, (employee_id, payroll_month)).fetchone()

            if existing:
                skipped_count += 1
                continue

            profile = db.execute("""
                SELECT s.basic_salary
                FROM payroll_employee_profiles p
                JOIN payroll_salary_structures s
                    ON s.id = p.salary_structure_id
                WHERE p.employee_id = ?
                  AND p.status = 'Active'
                  AND s.status = 'Active'
            """, (employee_id,)).fetchone()

            if not profile:
                skipped_count += 1
                continue

            basic_salary = _payroll_round(profile["basic_salary"])
            overtime = _plan_overtime(
                db, get_approved_overtime_rows(db, employee_id, payroll_month),
                basic_salary)
            components = _month_components(
                db, employee_id, payroll_month, basic_salary,
                overtime["amount"])

            cursor = db.execute("""
                INSERT INTO payroll_records (
                    employee_id, payroll_month, basic_salary,
                    allowances, overtime_amount, deductions,
                    gross_salary, net_salary,
                    payroll_status, payment_status, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Draft', 'Pending', ?)
            """, (
                employee_id, payroll_month,
                str(basic_salary),
                str(components["allowances"]),
                str(components["overtime_amount"]),
                str(components["deductions"]),
                str(components["gross_salary"]),
                str(components["net_salary"]),
                created_by,
            ))

            payroll_id = cursor.lastrowid

            _write_statutory_rows(
                db, payroll_id, employee_id, components["statutory"])
            overtime_count += _write_overtime_imports(
                db, payroll_id, employee_id, payroll_month,
                overtime["items"], created_by)

            created_count += 1

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    log_activity(
        "CREATE", "payroll",
        "Payroll for {} generated: {} created, {} skipped, {} overtime"
        " requests imported.".format(
            payroll_month, created_count, skipped_count, overtime_count),
        record_type="payroll", record_id=payroll_month)

    return {
        "created": created_count,
        "skipped": skipped_count,
        "overtime_imported": overtime_count,
        "payroll_month": payroll_month,
    }


def get_monthly_payroll_data(payroll_month):
    db = get_db()

    try:
        rows = db.execute("""
            SELECT
                p.id, p.employee_id, p.payroll_month,
                p.basic_salary, p.allowances, p.overtime_amount,
                p.deductions, p.gross_salary, p.net_salary,
                p.payroll_status, p.payment_status,
                e.employee_code, e.full_name
            FROM payroll_records p
            JOIN employees e ON e.id = p.employee_id
            WHERE p.payroll_month = ?
            ORDER BY e.full_name
        """, (payroll_month,)).fetchall()

        records = [dict(row) for row in rows]

        total_basic = total_allowances = total_overtime = Decimal("0.00")
        total_deductions = total_net = Decimal("0.00")
        paid_count = 0
        pending_count = 0

        for record in records:
            total_basic += Decimal(str(record["basic_salary"] or 0))
            total_allowances += Decimal(str(record["allowances"] or 0))
            total_overtime += Decimal(str(record["overtime_amount"] or 0))
            total_deductions += Decimal(str(record["deductions"] or 0))
            total_net += Decimal(str(record["net_salary"] or 0))

            if record["payment_status"] == "Paid":
                paid_count += 1
            else:
                pending_count += 1

        return {
            "records": records,
            "total_basic": total_basic,
            "total_allowances": total_allowances,
            "total_overtime": total_overtime,
            "total_deductions": total_deductions,
            "total_net": total_net,
            "paid_count": paid_count,
            "pending_count": pending_count,
        }
    finally:
        db.close()


# ---------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------

def submit_monthly_payroll(payroll_month, submitted_by):
    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        records = db.execute("""
            SELECT id, payroll_status FROM payroll_records
            WHERE payroll_month = ?
        """, (payroll_month,)).fetchall()

        if not records:
            raise ValueError("No payroll records found for this month.")

        if any(row["payroll_status"] != "Draft" for row in records):
            raise ValueError(
                "Only payroll batches containing Draft records can be submitted."
            )

        now = datetime.utcnow().isoformat()

        db.execute("""
            UPDATE payroll_records
            SET payroll_status = 'Pending Approval',
                updated_at = ?
            WHERE payroll_month = ? AND payroll_status = 'Draft'
        """, (now, payroll_month))

        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def approve_monthly_payroll(payroll_month, approved_by, comments=None):
    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        records = db.execute("""
            SELECT id, payroll_status FROM payroll_records
            WHERE payroll_month = ?
        """, (payroll_month,)).fetchall()

        if not records:
            raise ValueError("Payroll batch not found.")

        if any(row["payroll_status"] != "Pending Approval" for row in records):
            raise ValueError("Only submitted payroll can be approved.")

        now = datetime.utcnow().isoformat()

        db.execute("""
            UPDATE payroll_records
            SET payroll_status = 'Approved',
                updated_at = ?
            WHERE payroll_month = ?
              AND payroll_status = 'Pending Approval'
        """, (now, payroll_month))

        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reject_monthly_payroll(payroll_month, rejected_by, reason):
    if not reason or not reason.strip():
        raise ValueError("A rejection reason is required.")

    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        records = db.execute("""
            SELECT id, payroll_status FROM payroll_records
            WHERE payroll_month = ?
        """, (payroll_month,)).fetchall()

        if not records:
            raise ValueError("Payroll batch not found.")

        if any(row["payroll_status"] != "Pending Approval" for row in records):
            raise ValueError("Only submitted payroll can be rejected.")

        now = datetime.utcnow().isoformat()

        db.execute("""
            UPDATE payroll_records
            SET payroll_status = 'Rejected',
                updated_at = ?
            WHERE payroll_month = ?
              AND payroll_status = 'Pending Approval'
        """, (now, payroll_month))

        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------
# Payments
# ---------------------------------------------------------

def record_payroll_payment(
    payroll_id, payment_method, payment_reference,
    payment_date, paid_by
):
    if not payment_reference or not payment_reference.strip():
        raise ValueError("Payment reference is required.")

    if payment_method not in ("Bank Transfer", "Cash", "Other"):
        raise ValueError("Invalid payment method.")

    try:
        datetime.strptime(payment_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Invalid payment date.")

    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        payroll = db.execute("""
            SELECT * FROM payroll_records WHERE id = ?
        """, (payroll_id,)).fetchone()

        if not payroll:
            raise ValueError("Payroll record not found.")

        if payroll["payroll_status"] != "Approved":
            raise ValueError("Only approved payroll can be paid.")

        if payroll["payment_status"] == "Paid":
            raise ValueError("This payroll has already been paid.")

        db.execute("""
            INSERT INTO payroll_payments (
                payroll_id, employee_id, payroll_month,
                amount, payment_method, payment_reference,
                payment_date, payment_status, paid_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Paid', ?)
        """, (
            payroll["id"], payroll["employee_id"],
            payroll["payroll_month"],
            str(Decimal(str(payroll["net_salary"]))),
            payment_method, payment_reference.strip(),
            payment_date, paid_by
        ))

        db.execute("""
            UPDATE payroll_records
            SET payment_status = 'Paid',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND payroll_status = 'Approved'
              AND payment_status != 'Paid'
        """, (payroll_id,))

        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------
# Payslips
# ---------------------------------------------------------

def get_employee_payslips(user_id):
    db = get_db()
    try:
        employee = db.execute(
            "SELECT id FROM employees WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not employee:
            return []

        rows = db.execute("""
            SELECT
                p.id, p.payroll_month,
                p.basic_salary, p.allowances, p.overtime_amount,
                p.deductions, p.gross_salary, p.net_salary,
                p.payment_status, p.payroll_status,
                payment.payment_reference,
                payment.payment_method,
                payment.payment_date
            FROM payroll_records p
            LEFT JOIN payroll_payments payment
                ON payment.payroll_id = p.id
            WHERE p.employee_id = ?
              AND p.payroll_status = 'Approved'
            ORDER BY p.payroll_month DESC
        """, (employee["id"],)).fetchall()

        return [dict(row) for row in rows]
    finally:
        db.close()


def get_employee_own_salary_records(user_id):
    return get_employee_payslips(user_id)


def get_employee_payslip(payroll_id, user_id):
    db = get_db()
    try:
        row = db.execute("""
            SELECT
                p.id, p.employee_id, p.payroll_month,
                p.basic_salary, p.allowances, p.overtime_amount,
                p.deductions, p.gross_salary, p.net_salary, p.payment_status,
                e.employee_code, e.full_name,
                e.department, e.job_title,
                payment.payment_method,
                payment.payment_reference,
                payment.payment_date
            FROM payroll_records p
            JOIN employees e ON e.id = p.employee_id
            LEFT JOIN payroll_payments payment
                ON payment.payroll_id = p.id
            WHERE p.id = ?
              AND p.payroll_status = 'Approved'
              AND e.user_id = ?
        """, (payroll_id, user_id)).fetchone()

        if not row:
            return None

        payslip = dict(row)
        overtime = db.execute("""
            SELECT overtime_date, approved_hours, hourly_rate, multiplier,
                   amount
            FROM payroll_overtime_imports
            WHERE payroll_id = ?
            ORDER BY overtime_date, id
        """, (payroll_id,)).fetchall()
        payslip["overtime_items"] = [dict(r) for r in overtime]
        return payslip
    finally:
        db.close()


# ---------------------------------------------------------
# Reports
# ---------------------------------------------------------

def get_payroll_monthly_report(payroll_month):
    db = get_db()
    try:
        summary = db.execute("""
            SELECT
                COUNT(*) AS employee_count,
                COALESCE(SUM(basic_salary), 0) AS total_basic,
                COALESCE(SUM(allowances), 0) AS total_allowances,
                COALESCE(SUM(overtime_amount), 0) AS total_overtime,
                COALESCE(SUM(gross_salary), 0) AS total_gross,
                COALESCE(SUM(deductions), 0) AS total_deductions,
                COALESCE(SUM(net_salary), 0) AS total_net,
                SUM(CASE WHEN payment_status = 'Paid'
                    THEN 1 ELSE 0 END) AS paid_count,
                SUM(CASE WHEN payment_status != 'Paid'
                    THEN 1 ELSE 0 END) AS unpaid_count
            FROM payroll_records
            WHERE payroll_month = ?
              AND payroll_status = 'Approved'
        """, (payroll_month,)).fetchone()

        departments = db.execute("""
            SELECT
                COALESCE(e.department, 'Unassigned') AS department,
                COUNT(p.id) AS employee_count,
                SUM(p.basic_salary) AS total_basic,
                SUM(p.allowances) AS total_allowances,
                SUM(p.overtime_amount) AS total_overtime,
                SUM(p.deductions) AS total_deductions,
                SUM(p.net_salary) AS total_net
            FROM payroll_records p
            JOIN employees e ON e.id = p.employee_id
            WHERE p.payroll_month = ?
              AND p.payroll_status = 'Approved'
            GROUP BY e.department
            ORDER BY total_net DESC
        """, (payroll_month,)).fetchall()

        return {
            "summary": dict(summary),
            "departments": [dict(r) for r in departments],
        }
    finally:
        db.close()


def get_payroll_payment_report(payroll_month):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                p.id, p.payroll_month,
                e.employee_code, e.full_name, e.department,
                p.net_salary, p.payment_status,
                payment.payment_method,
                payment.payment_reference,
                payment.payment_date
            FROM payroll_records p
            JOIN employees e ON e.id = p.employee_id
            LEFT JOIN payroll_payments payment
                ON payment.payroll_id = p.id
            WHERE p.payroll_month = ?
              AND p.payroll_status = 'Approved'
            ORDER BY e.department, e.full_name
        """, (payroll_month,)).fetchall()

        return [dict(r) for r in rows]
    finally:
        db.close()


def get_payroll_deduction_report(payroll_month):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                e.employee_code, e.full_name, e.department,
                p.basic_salary, p.deductions, p.net_salary
            FROM payroll_records p
            JOIN employees e ON e.id = p.employee_id
            WHERE p.payroll_month = ?
              AND p.payroll_status = 'Approved'
            ORDER BY e.department, e.full_name
        """, (payroll_month,)).fetchall()

        return [dict(r) for r in rows]
    finally:
        db.close()


def get_payroll_export_rows(payroll_month):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                e.employee_code, e.full_name, e.department,
                p.payroll_month, p.basic_salary, p.allowances,
                p.overtime_amount, p.gross_salary, p.deductions, p.net_salary,
                p.payment_status
            FROM payroll_records p
            JOIN employees e ON e.id = p.employee_id
            WHERE p.payroll_month = ?
              AND p.payroll_status = 'Approved'
            ORDER BY e.department, e.full_name
        """, (payroll_month,)).fetchall()

        return [dict(r) for r in rows]
    finally:
        db.close()


# ---------------------------------------------------------
# Statutory rules & tax
# ---------------------------------------------------------

def calculate_statutory_amount(
    base_amount, rate, fixed_amount=0,
    calculation_type="Percentage",
    minimum_amount=None, maximum_amount=None
):
    base = Decimal(str(base_amount or 0))
    rate = Decimal(str(rate or 0))
    fixed = Decimal(str(fixed_amount or 0))

    if calculation_type == "Percentage":
        amount = base * rate / Decimal("100")
    elif calculation_type == "Fixed":
        amount = fixed
    else:
        raise ValueError("Unsupported statutory calculation type.")

    if minimum_amount is not None:
        amount = max(amount, Decimal(str(minimum_amount)))
    if maximum_amount is not None:
        amount = min(amount, Decimal(str(maximum_amount)))

    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_active_statutory_rules(payroll_date):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT * FROM payroll_statutory_rules
            WHERE status = 'Active'
              AND effective_from <= ?
              AND (
                  effective_to IS NULL
                  OR effective_to = ''
                  OR effective_to >= ?
              )
            ORDER BY rule_type, effective_from DESC
        """, (payroll_date, payroll_date)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_active_tax_brackets(payroll_date):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                b.id, b.rule_id, b.lower_limit, b.upper_limit,
                b.tax_rate, b.fixed_tax,
                b.effective_from, b.effective_to
            FROM payroll_tax_brackets b
            JOIN payroll_statutory_rules r ON r.id = b.rule_id
            WHERE r.rule_type = 'Income Tax'
              AND r.status = 'Active'
              AND b.status = 'Active'
              AND b.effective_from <= ?
              AND (b.effective_to IS NULL OR b.effective_to = ''
                   OR b.effective_to >= ?)
              AND r.effective_from <= ?
              AND (r.effective_to IS NULL OR r.effective_to = ''
                   OR r.effective_to >= ?)
            ORDER BY b.lower_limit ASC
        """, (
            payroll_date, payroll_date, payroll_date, payroll_date
        )).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def calculate_progressive_income_tax(taxable_income, brackets):
    income = Decimal(str(taxable_income or 0))
    if income <= 0 or not brackets:
        return Decimal("0.00")

    tax = Decimal("0.00")

    for bracket in brackets:
        lower = Decimal(str(bracket["lower_limit"]))
        upper = (
            Decimal(str(bracket["upper_limit"]))
            if bracket["upper_limit"] is not None else None
        )
        rate = Decimal(str(bracket["tax_rate"]))
        fixed = Decimal(str(bracket["fixed_tax"] or 0))

        if income <= lower:
            break

        slice_amt = (
            income - lower if upper is None
            else min(income, upper) - lower
        )
        if slice_amt > 0:
            tax += fixed + (slice_amt * rate / Decimal("100"))

    return tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_employee_statutory_deductions(basic_salary, payroll_date):
    rules = get_active_statutory_rules(payroll_date)

    employee_tax = Decimal("0.00")
    employee_pension = Decimal("0.00")
    employer_pension = Decimal("0.00")

    for rule in rules:
        amount = calculate_statutory_amount(
            base_amount=basic_salary,
            rate=rule["rate"],
            fixed_amount=rule["fixed_amount"],
            calculation_type=rule["calculation_type"],
            minimum_amount=rule["minimum_amount"],
            maximum_amount=rule["maximum_amount"],
        )

        if rule["rule_type"] == "Income Tax":
            employee_tax += amount
        elif rule["rule_type"] == "Employee Pension":
            employee_pension += amount
        elif rule["rule_type"] == "Employer Pension":
            employer_pension += amount

    return {
        "income_tax": employee_tax,
        "employee_pension": employee_pension,
        "employer_pension": employer_pension,
        "total_employee_deductions": employee_tax + employee_pension,
    }


def calculate_employee_statutory_breakdown(
    basic_salary, taxable_allowances,
    payroll_date, pensionable_salary=None
):
    basic = Decimal(str(basic_salary or 0))
    allowances = Decimal(str(taxable_allowances or 0))
    taxable_income = basic + allowances
    pension_base = Decimal(
        str(pensionable_salary if pensionable_salary is not None else basic)
    )

    if basic < 0 or allowances < 0 or pension_base < 0:
        raise ValueError("Salary bases cannot be negative.")

    tax_brackets = get_active_tax_brackets(payroll_date)
    income_tax = calculate_progressive_income_tax(taxable_income, tax_brackets)

    pension_rules = get_active_statutory_rules(payroll_date)
    employee_pension = Decimal("0.00")
    employer_pension = Decimal("0.00")
    details = []

    for rule in pension_rules:
        if rule["rule_type"] not in {"Employee Pension", "Employer Pension"}:
            continue

        amount = calculate_statutory_amount(
            base_amount=pension_base,
            rate=rule["rate"],
            fixed_amount=rule["fixed_amount"],
            calculation_type=rule["calculation_type"],
            minimum_amount=rule["minimum_amount"],
            maximum_amount=rule["maximum_amount"],
        )

        details.append({
            "rule_id": rule["id"],
            "type": rule["rule_type"],
            "base": pension_base,
            "rate": rule["rate"],
            "amount": amount,
        })

        if rule["rule_type"] == "Employee Pension":
            employee_pension += amount
        else:
            employer_pension += amount

    details.append({
        "rule_id": None,
        "type": "Income Tax",
        "base": taxable_income,
        "rate": None,
        "amount": income_tax,
    })

    return {
        "taxable_income": taxable_income,
        "income_tax": income_tax,
        "employee_pension": employee_pension,
        "employer_pension": employer_pension,
        "total_employee_statutory": income_tax + employee_pension,
        "details": details,
    }


def save_statutory_breakdown(payroll_id, employee_id, breakdown):
    db = get_db()

    try:
        db.execute("BEGIN")

        existing = db.execute("""
            SELECT payroll_status, payment_status
            FROM payroll_records WHERE id = ?
        """, (payroll_id,)).fetchone()

        if not existing:
            raise ValueError("Payroll record not found.")
        if existing["payroll_status"] != "Draft":
            raise ValueError(
                "Statutory deductions can only be saved for Draft payroll."
            )
        if existing["payment_status"] == "Paid":
            raise ValueError("Paid payroll cannot be changed.")

        _write_statutory_rows(db, payroll_id, employee_id, breakdown)

        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_statutory_rules():
    db = get_db()
    try:
        rows = db.execute("""
            SELECT * FROM payroll_statutory_rules
            ORDER BY rule_type, effective_from DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_tax_brackets():
    db = get_db()
    try:
        rows = db.execute("""
            SELECT b.id, b.rule_id, b.lower_limit, b.upper_limit,
                   b.tax_rate, b.fixed_tax, b.effective_from,
                   b.effective_to, b.status,
                   r.rule_code, r.rule_name, r.status AS rule_status
            FROM payroll_tax_brackets b
            JOIN payroll_statutory_rules r ON r.id = b.rule_id
            ORDER BY b.lower_limit
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def toggle_statutory_rule(rule_id, toggled_by):
    """Activate or park a rule; payroll only reads Active rules."""
    db = get_db()

    try:
        rule = db.execute("""
            SELECT rule_code, status FROM payroll_statutory_rules
            WHERE id = ?
        """, (rule_id,)).fetchone()

        if not rule:
            raise ValueError("Statutory rule not found.")

        new_status = "Inactive" if rule["status"] == "Active" else "Active"

        db.execute("""
            UPDATE payroll_statutory_rules
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (new_status, timestamp(), rule_id))

        db.execute("""
            INSERT INTO payroll_statutory_audit (
                rule_id, action, old_values, new_values, action_by, action_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            rule_id,
            "ACTIVATE" if new_status == "Active" else "DEACTIVATE",
            str(rule["status"]), str(new_status), toggled_by, timestamp(),
        ))

        db.commit()
        return new_status
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_statutory_rule(data, created_by):
    required = [
        "rule_code", "rule_name", "rule_type",
        "calculation_type", "effective_from"
    ]
    for field in required:
        if not data.get(field):
            raise ValueError(f"{field} is required.")

    rule_type = data["rule_type"]
    if rule_type not in {"Income Tax", "Employee Pension", "Employer Pension"}:
        raise ValueError("Invalid statutory rule type.")

    calculation_type = data["calculation_type"]
    if calculation_type not in {"Percentage", "Fixed"}:
        raise ValueError("Invalid calculation type.")

    rate = Decimal(str(data.get("rate") or 0))
    fixed_amount = Decimal(str(data.get("fixed_amount") or 0))

    if rate < 0 or fixed_amount < 0:
        raise ValueError("Rates and amounts cannot be negative.")

    effective_from = date.fromisoformat(data["effective_from"])
    effective_to = (
        date.fromisoformat(data["effective_to"])
        if data.get("effective_to") else None
    )

    if effective_to and effective_to < effective_from:
        raise ValueError("End date cannot precede start date.")

    db = get_db()
    try:
        cursor = db.execute("""
            INSERT INTO payroll_statutory_rules (
                rule_code, rule_name, rule_type, calculation_type,
                rate, fixed_amount, minimum_amount, maximum_amount,
                effective_from, effective_to, status, description, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Inactive', ?, ?)
        """, (
            data["rule_code"].strip(),
            data["rule_name"].strip(),
            rule_type, calculation_type,
            str(rate), str(fixed_amount),
            data.get("minimum_amount") or None,
            data.get("maximum_amount") or None,
            effective_from.isoformat(),
            effective_to.isoformat() if effective_to else None,
            data.get("description", "").strip(),
            created_by,
        ))

        rule_id = cursor.lastrowid

        db.execute("""
            INSERT INTO payroll_statutory_audit (
                rule_id, action, new_values, action_by
            )
            VALUES (?, 'CREATE', ?, ?)
        """, (rule_id, str(dict(data)), created_by))

        db.commit()
        return rule_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_payroll_dashboard_data(payroll_month):
    """Return data for the payroll dashboard."""
    validate_payroll_month(payroll_month)
    data = get_monthly_payroll_data(payroll_month)

    db = get_db()
    try:
        months = db.execute("""
            SELECT
                payroll_month AS month,
                COUNT(*) AS lines,
                SUM(CASE WHEN payment_status = 'Paid' THEN 1 ELSE 0 END) AS paid,
                SUM(net_salary) AS net
            FROM payroll_records
            GROUP BY payroll_month
            ORDER BY payroll_month DESC
            LIMIT 12
        """).fetchall()
    finally:
        db.close()

    return {
        "gross": (data["total_basic"] + data["total_allowances"]
                  + data["total_overtime"]),
        "deductions": data["total_deductions"],
        "net": data["total_net"],
        "overtime": data["total_overtime"],
        "records": data["records"],
        "paid": data["paid_count"],
        "pending": data["pending_count"],
        "months": [dict(r) for r in months],
    }


def get_verified_overtime_requests(payroll_month):
    """Approved HR overtime for a month that payroll has not imported yet."""
    validate_payroll_month(payroll_month)
    db = get_db()
    try:
        rows = db.execute("""
            SELECT r.id, r.employee_id, r.overtime_date, r.approved_hours,
                   r.multiplier, e.employee_code, e.full_name
            FROM hr_overtime_requests r
            JOIN employees e ON e.id = r.employee_id
            WHERE r.status = 'Approved'
              AND substr(r.overtime_date, 1, 7) = ?
              AND NOT EXISTS (
                  SELECT 1 FROM payroll_overtime_imports i
                  WHERE i.overtime_request_id = r.id
              )
            ORDER BY e.full_name, r.overtime_date, r.id
        """, (payroll_month,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def get_payroll_overtime_items(payroll_id):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT overtime_request_id, overtime_date, approved_hours,
                   hourly_rate, multiplier, amount
            FROM payroll_overtime_imports
            WHERE payroll_id = ?
            ORDER BY overtime_date, id
        """, (payroll_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def get_payroll_overtime_total(payroll_id):
    db = get_db()
    try:
        row = db.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM payroll_overtime_imports WHERE payroll_id = ?
        """, (payroll_id,)).fetchone()
        return _payroll_round(row["total"])
    finally:
        db.close()


def import_verified_overtime(payroll_month, imported_by=None):
    """Add approved overtime to the month's Draft payroll exactly once.

    Each HR request is priced here and written to payroll_overtime_imports with
    its own unique request id, so a second import finds no pending request. The
    payslip's tax and pension are then recalculated from the new gross, which is
    how overtime is counted once and taxed with the rest of the month.
    """
    validate_payroll_month(payroll_month)
    db = get_db()

    imported_count = 0
    imported_amount = Decimal("0.00")

    try:
        employees = db.execute("""
            SELECT r.employee_id
            FROM hr_overtime_requests r
            WHERE r.status = 'Approved'
              AND substr(r.overtime_date, 1, 7) = ?
              AND NOT EXISTS (
                  SELECT 1 FROM payroll_overtime_imports i
                  WHERE i.overtime_request_id = r.id
              )
            GROUP BY r.employee_id
        """, (payroll_month,)).fetchall()

        for employee in employees:
            employee_id = employee["employee_id"]

            record = db.execute("""
                SELECT id, basic_salary, payroll_status
                FROM payroll_records
                WHERE employee_id = ? AND payroll_month = ?
            """, (employee_id, payroll_month)).fetchone()

            if not record:
                raise ValueError(
                    "No {} payroll record for employee #{}. Generate the"
                    " month before importing overtime.".format(
                        payroll_month, employee_id))

            if record["payroll_status"] != "Draft":
                raise ValueError(
                    "Payroll for employee #{} is {}, not Draft.".format(
                        employee_id, record["payroll_status"]))

            basic_salary = _payroll_round(record["basic_salary"])
            overtime = _plan_overtime(
                db, get_approved_overtime_rows(db, employee_id, payroll_month),
                basic_salary)

            if not overtime["items"]:
                continue

            existing = db.execute("""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM payroll_overtime_imports WHERE payroll_id = ?
            """, (record["id"],)).fetchone()["total"]

            total_overtime = _payroll_round(
                _payroll_decimal(existing) + overtime["amount"])

            components = _month_components(
                db, employee_id, payroll_month, basic_salary, total_overtime)

            db.execute("""
                UPDATE payroll_records
                SET allowances = ?, overtime_amount = ?, deductions = ?,
                    gross_salary = ?, net_salary = ?, updated_at = ?
                WHERE id = ?
            """, (
                str(components["allowances"]),
                str(components["overtime_amount"]),
                str(components["deductions"]),
                str(components["gross_salary"]),
                str(components["net_salary"]),
                timestamp(), record["id"],
            ))

            _write_statutory_rows(
                db, record["id"], employee_id, components["statutory"])
            imported_count += _write_overtime_imports(
                db, record["id"], employee_id, payroll_month,
                overtime["items"], imported_by)
            imported_amount += overtime["amount"]

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    log_activity(
        "IMPORT", "payroll",
        "Overtime for {} imported: {} requests, {} ETB.".format(
            payroll_month, imported_count, _payroll_round(imported_amount)),
        record_type="payroll", record_id=payroll_month)

    return {
        "imported_count": imported_count,
        "imported_amount": _payroll_round(imported_amount),
    }
