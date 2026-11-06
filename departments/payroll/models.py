"""
Dereje Wood & Kitchen Cabinet Management System
Payroll Department - Database Models

Step 3.2:
Employee Payroll Profiles
Salary Grades
Salary Structures
Allowances
Deductions
Salary History
Salary Approvals
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from core.db import get_db as _request_db


# ==================================================
# DATABASE CONNECTION
# ==================================================

class _RequestConnection:
    """The request's shared connection, safe to close inside a service.

    Payroll code was written around a connection it opens and closes itself.
    The application keeps one connection per request (teardown closes it), so a
    close() here only releases the caller's handle, and every other call is
    delegated to the shared connection.
    """

    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def close(self):
        return None


def get_db():
    """Use the existing application database connection."""

    return _RequestConnection(_request_db())


# ==================================================
# COMMON HELPERS
# ==================================================

def money(value):
    """Validate and normalize monetary values."""

    try:
        amount = Decimal(str(value))

        if not amount.is_finite() or amount < 0:
            raise ValueError("Invalid monetary amount.")

        return amount.quantize(Decimal("0.01"))

    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Please enter a valid positive amount.")


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def validate_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except (TypeError, ValueError):
        raise ValueError("Date must use YYYY-MM-DD format.")


# ==================================================
# DATABASE INITIALIZATION
# ==================================================

def init_payroll_tables():

    db = get_db()

    db.executescript("""

    CREATE TABLE IF NOT EXISTS payroll_salary_grades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grade_name TEXT NOT NULL UNIQUE,
        description TEXT,
        minimum_salary NUMERIC NOT NULL DEFAULT 0,
        maximum_salary NUMERIC NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS payroll_salary_structures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        structure_name TEXT NOT NULL,
        salary_grade_id INTEGER,
        basic_salary NUMERIC NOT NULL,
        description TEXT,
        effective_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (salary_grade_id)
            REFERENCES payroll_salary_grades(id)
    );

    CREATE TABLE IF NOT EXISTS payroll_employee_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL UNIQUE,
        salary_grade_id INTEGER,
        salary_structure_id INTEGER,
        effective_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (salary_grade_id)
            REFERENCES payroll_salary_grades(id),
        FOREIGN KEY (salary_structure_id)
            REFERENCES payroll_salary_structures(id)
    );

    CREATE TABLE IF NOT EXISTS payroll_allowance_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        allowance_name TEXT NOT NULL UNIQUE,
        description TEXT,
        calculation_type TEXT NOT NULL DEFAULT 'Fixed',
        default_amount NUMERIC NOT NULL DEFAULT 0,
        taxable INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS payroll_employee_allowances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        allowance_type_id INTEGER NOT NULL,
        amount NUMERIC NOT NULL,
        effective_date TEXT NOT NULL,
        end_date TEXT,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL,
        FOREIGN KEY (allowance_type_id)
            REFERENCES payroll_allowance_types(id)
    );

    CREATE TABLE IF NOT EXISTS payroll_deduction_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deduction_name TEXT NOT NULL UNIQUE,
        description TEXT,
        calculation_type TEXT NOT NULL DEFAULT 'Fixed',
        default_amount NUMERIC NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS payroll_employee_deductions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        deduction_type_id INTEGER NOT NULL,
        amount NUMERIC NOT NULL,
        effective_date TEXT NOT NULL,
        end_date TEXT,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT NOT NULL,
        FOREIGN KEY (deduction_type_id)
            REFERENCES payroll_deduction_types(id)
    );

    CREATE TABLE IF NOT EXISTS payroll_salary_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        old_salary NUMERIC NOT NULL DEFAULT 0,
        new_salary NUMERIC NOT NULL,
        effective_date TEXT NOT NULL,
        reason TEXT,
        approval_status TEXT NOT NULL DEFAULT 'Pending',
        created_by TEXT,
        approved_by TEXT,
        approved_at TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS payroll_salary_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        salary_history_id INTEGER NOT NULL,
        approval_level TEXT NOT NULL,
        approver_id TEXT,
        approval_status TEXT NOT NULL DEFAULT 'Pending',
        comments TEXT,
        approved_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (salary_history_id)
            REFERENCES payroll_salary_history(id)
    );

    CREATE INDEX IF NOT EXISTS idx_payroll_employee
        ON payroll_employee_profiles(employee_id);

    CREATE INDEX IF NOT EXISTS idx_payroll_salary_history
        ON payroll_salary_history(employee_id);

    CREATE INDEX IF NOT EXISTS idx_payroll_allowances_employee
        ON payroll_employee_allowances(employee_id);

    CREATE INDEX IF NOT EXISTS idx_payroll_deductions_employee
        ON payroll_employee_deductions(employee_id);

    """)

    db.commit()
    db.close()


def init_monthly_payroll_tables():
    db = get_db()

    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                payroll_month TEXT NOT NULL,

                basic_salary NUMERIC NOT NULL DEFAULT 0,
                allowances NUMERIC NOT NULL DEFAULT 0,
                overtime_amount NUMERIC NOT NULL DEFAULT 0,
                deductions NUMERIC NOT NULL DEFAULT 0,
                gross_salary NUMERIC NOT NULL DEFAULT 0,
                net_salary NUMERIC NOT NULL DEFAULT 0,

                payroll_status TEXT NOT NULL DEFAULT 'Draft',
                payment_status TEXT NOT NULL DEFAULT 'Pending',

                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,

                UNIQUE(employee_id, payroll_month)
            )
        """)

        # An older copy of this file may already hold payroll_records without
        # the overtime column; adding it in place keeps existing rows.
        try:
            db.execute(
                "ALTER TABLE payroll_records ADD COLUMN"
                " overtime_amount NUMERIC NOT NULL DEFAULT 0")
        except Exception as error:
            if "duplicate column name" not in str(error).lower():
                raise

        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll_overtime_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                overtime_request_id INTEGER NOT NULL UNIQUE,
                payroll_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                payroll_month TEXT NOT NULL,

                overtime_date TEXT,
                approved_hours NUMERIC NOT NULL DEFAULT 0,
                hourly_rate NUMERIC NOT NULL DEFAULT 0,
                multiplier NUMERIC NOT NULL DEFAULT 1,
                amount NUMERIC NOT NULL DEFAULT 0,

                imported_by INTEGER,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(payroll_id)
                    REFERENCES payroll_records(id)
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_payroll_overtime_imports_payroll
                ON payroll_overtime_imports(payroll_id)
        """)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def init_payroll_payment_tables():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS payroll_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payroll_id INTEGER NOT NULL UNIQUE,
            employee_id INTEGER NOT NULL,
            payroll_month TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            payment_method TEXT NOT NULL,
            payment_reference TEXT NOT NULL UNIQUE,
            payment_date TEXT NOT NULL,
            payment_status TEXT NOT NULL DEFAULT 'Paid',
            paid_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(payroll_id) REFERENCES payroll_records(id)
        )
    """)

    db.commit()
    db.close()


def init_payroll_statutory_tables():
    db = get_db()

    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll_statutory_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                rule_code TEXT NOT NULL,
                rule_name TEXT NOT NULL,

                rule_type TEXT NOT NULL CHECK (
                    rule_type IN (
                        'Income Tax',
                        'Employee Pension',
                        'Employer Pension'
                    )
                ),

                calculation_type TEXT NOT NULL DEFAULT 'Percentage'
                    CHECK (calculation_type IN ('Percentage', 'Fixed')),

                rate NUMERIC NOT NULL DEFAULT 0,
                fixed_amount NUMERIC NOT NULL DEFAULT 0,

                minimum_amount NUMERIC,
                maximum_amount NUMERIC,

                effective_from TEXT NOT NULL,
                effective_to TEXT,

                status TEXT NOT NULL DEFAULT 'Inactive'
                    CHECK (status IN ('Active', 'Inactive')),

                description TEXT,

                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,

                UNIQUE(rule_code, effective_from)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll_statutory_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                rule_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_values TEXT,
                new_values TEXT,

                action_by INTEGER,
                action_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(rule_id)
                    REFERENCES payroll_statutory_rules(id)
            )
        """)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def init_progressive_tax_tables():
    db = get_db()

    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll_tax_brackets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                lower_limit NUMERIC NOT NULL,
                upper_limit NUMERIC,
                tax_rate NUMERIC NOT NULL,
                fixed_tax NUMERIC NOT NULL DEFAULT 0,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                status TEXT NOT NULL DEFAULT 'Inactive',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(rule_id)
                    REFERENCES payroll_statutory_rules(id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS payroll_statutory_breakdown (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                payroll_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,

                deduction_type TEXT NOT NULL,
                calculation_base NUMERIC NOT NULL DEFAULT 0,
                applied_rate NUMERIC NOT NULL DEFAULT 0,
                amount NUMERIC NOT NULL DEFAULT 0,

                rule_id INTEGER,
                calculation_details TEXT,

                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(payroll_id)
                    REFERENCES payroll_records(id),

                FOREIGN KEY(rule_id)
                    REFERENCES payroll_statutory_rules(id),

                UNIQUE(payroll_id, deduction_type)
            )
        """)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# ==================================================
# STATUTORY DEFAULTS
# ==================================================

# Employment income tax and pension contributions are national law, not a
# per-company choice, so the first payroll boot installs the current Ethiopian
# schedule. An empty rule book would compute zero tax and zero pension on every
# payslip - a payroll that looks correct while underpaying the state.
DEFAULT_STATUTORY_RULES = (
    {
        "rule_code": "ET-IT",
        "rule_name": "Employment Income Tax (Proclamation 979/2016)",
        "rule_type": "Income Tax",
        "calculation_type": "Percentage",
        "rate": "0",
        "fixed_amount": "0",
        "effective_from": "2016-07-08",
    },
    {
        "rule_code": "ET-PEN-EE",
        "rule_name": "Employee Pension Contribution 7%",
        "rule_type": "Employee Pension",
        "calculation_type": "Percentage",
        "rate": "7",
        "fixed_amount": "0",
        "effective_from": "2011-07-01",
    },
    {
        "rule_code": "ET-PEN-ER",
        "rule_name": "Employer Pension Contribution 11%",
        "rule_type": "Employer Pension",
        "calculation_type": "Percentage",
        "rate": "11",
        "fixed_amount": "0",
        "effective_from": "2011-07-01",
    },
)

# (lower limit, upper limit, rate %, fixed addition). The progressive helper
# adds every slice, so the fixed column stays zero or the tax is counted twice.
DEFAULT_TAX_BRACKETS = (
    (0, 2000, 0, 0),
    (2000, 4000, 10, 0),
    (4000, 7000, 15, 0),
    (7000, 10000, 20, 0),
    (10000, 14000, 25, 0),
    (14000, None, 30, 0),
)


def seed_default_statutory_rules():
    """Install the statutory defaults once, and only while the book is empty."""
    db = get_db()

    try:
        if db.execute(
                "SELECT COUNT(*) FROM payroll_statutory_rules").fetchone()[0]:
            return False

        tax_rule_id = None
        for rule in DEFAULT_STATUTORY_RULES:
            cursor = db.execute("""
                INSERT INTO payroll_statutory_rules (
                    rule_code, rule_name, rule_type, calculation_type,
                    rate, fixed_amount, effective_from, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Active')
            """, (
                rule["rule_code"], rule["rule_name"], rule["rule_type"],
                rule["calculation_type"], rule["rate"],
                rule["fixed_amount"], rule["effective_from"],
            ))
            if rule["rule_type"] == "Income Tax":
                tax_rule_id = cursor.lastrowid

        for lower, upper, rate, fixed in DEFAULT_TAX_BRACKETS:
            db.execute("""
                INSERT INTO payroll_tax_brackets (
                    rule_id, lower_limit, upper_limit, tax_rate, fixed_tax,
                    effective_from, status
                )
                VALUES (?, ?, ?, ?, ?, '2016-07-08', 'Active')
            """, (tax_rule_id, lower, upper, rate, fixed))

        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ==================================================
# EMPLOYEE PAYROLL PROFILE
# ==================================================

class PayrollEmployee:

    @staticmethod
    def get_all():
        db = get_db()

        records = db.execute("""
            SELECT
                p.id AS profile_id,
                p.employee_id,
                p.effective_date,
                p.status,
                e.employee_code,
                e.full_name,
                e.department,
                e.job_title,
                s.basic_salary,
                g.grade_name

            FROM payroll_employee_profiles p

            JOIN employees e
                ON e.id = p.employee_id

            LEFT JOIN payroll_salary_structures s
                ON s.id = p.salary_structure_id

            LEFT JOIN payroll_salary_grades g
                ON g.id = p.salary_grade_id

            ORDER BY e.full_name
        """).fetchall()

        db.close()
        return records

    @staticmethod
    def get_by_id(profile_id):

        db = get_db()

        record = db.execute("""
            SELECT
                p.*,
                e.employee_code,
                e.full_name,
                e.department,
                e.job_title,
                s.basic_salary,
                g.grade_name

            FROM payroll_employee_profiles p

            JOIN employees e
                ON e.id = p.employee_id

            LEFT JOIN payroll_salary_structures s
                ON s.id = p.salary_structure_id

            LEFT JOIN payroll_salary_grades g
                ON g.id = p.salary_grade_id

            WHERE p.id = ?
        """, (profile_id,)).fetchone()

        db.close()
        return record

    @staticmethod
    def create(employee_id, grade_id, structure_id, effective_date):

        effective_date = validate_date(effective_date)

        db = get_db()

        try:
            employee = db.execute("""
                SELECT id FROM employees
                WHERE id = ?
            """, (employee_id,)).fetchone()

            if not employee:
                raise ValueError("Employee does not exist.")

            existing = db.execute("""
                SELECT id FROM payroll_employee_profiles
                WHERE employee_id = ?
            """, (employee_id,)).fetchone()

            if existing:
                raise ValueError(
                    "This employee already has a payroll profile."
                )

            db.execute("""
                INSERT INTO payroll_employee_profiles (
                    employee_id,
                    salary_grade_id,
                    salary_structure_id,
                    effective_date,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?, ?)
            """, (
                employee_id,
                grade_id or None,
                structure_id or None,
                effective_date,
                timestamp(),
                timestamp()
            ))

            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


# ==================================================
# SALARY GRADES
# ==================================================

class SalaryGrade:

    @staticmethod
    def get_all():

        db = get_db()

        records = db.execute("""
            SELECT * FROM payroll_salary_grades
            ORDER BY grade_name
        """).fetchall()

        db.close()
        return records

    @staticmethod
    def create(name, description, minimum, maximum):

        minimum = money(minimum)
        maximum = money(maximum)

        if minimum > maximum:
            raise ValueError(
                "Minimum salary cannot exceed maximum salary."
            )

        db = get_db()

        try:
            db.execute("""
                INSERT INTO payroll_salary_grades (
                    grade_name,
                    description,
                    minimum_salary,
                    maximum_salary,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?)
            """, (
                name.strip(),
                description,
                str(minimum),
                str(maximum),
                timestamp()
            ))

            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


# ==================================================
# SALARY STRUCTURES
# ==================================================

class SalaryStructure:

    @staticmethod
    def get_all():

        db = get_db()

        records = db.execute("""
            SELECT
                s.*,
                g.grade_name

            FROM payroll_salary_structures s

            LEFT JOIN payroll_salary_grades g
                ON g.id = s.salary_grade_id

            ORDER BY s.created_at DESC
        """).fetchall()

        db.close()
        return records

    @staticmethod
    def create(
        name,
        grade_id,
        basic_salary,
        description,
        effective_date
    ):

        basic_salary = money(basic_salary)
        effective_date = validate_date(effective_date)

        db = get_db()

        try:
            if grade_id:
                grade = db.execute("""
                    SELECT minimum_salary, maximum_salary
                    FROM payroll_salary_grades
                    WHERE id = ? AND status = 'Active'
                """, (grade_id,)).fetchone()

                if not grade:
                    raise ValueError("Selected salary grade is invalid.")

                if not (
                    Decimal(str(grade["minimum_salary"]))
                    <= basic_salary
                    <= Decimal(str(grade["maximum_salary"]))
                ):
                    raise ValueError(
                        "Basic salary must be within the selected grade."
                    )

            db.execute("""
                INSERT INTO payroll_salary_structures (
                    structure_name,
                    salary_grade_id,
                    basic_salary,
                    description,
                    effective_date,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 'Active', ?)
            """, (
                name.strip(),
                grade_id or None,
                str(basic_salary),
                description,
                effective_date,
                timestamp()
            ))

            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


# ==================================================
# EMPLOYEE ALLOWANCES
# ==================================================

class EmployeeAllowance:

    @staticmethod
    def get_by_employee(employee_id):

        db = get_db()

        records = db.execute("""
            SELECT
                a.*,
                t.allowance_name,
                t.calculation_type,
                t.taxable

            FROM payroll_employee_allowances a

            JOIN payroll_allowance_types t
                ON t.id = a.allowance_type_id

            WHERE a.employee_id = ?
            ORDER BY a.id DESC
        """, (employee_id,)).fetchall()

        db.close()
        return records

    @staticmethod
    def assign(
        employee_id,
        allowance_type_id,
        amount,
        effective_date
    ):

        amount = money(amount)
        effective_date = validate_date(effective_date)

        db = get_db()

        try:
            db.execute("""
                INSERT INTO payroll_employee_allowances (
                    employee_id,
                    allowance_type_id,
                    amount,
                    effective_date,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?)
            """, (
                employee_id,
                allowance_type_id,
                str(amount),
                effective_date,
                timestamp()
            ))

            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


# ==================================================
# EMPLOYEE DEDUCTIONS
# ==================================================

class EmployeeDeduction:

    @staticmethod
    def get_by_employee(employee_id):

        db = get_db()

        records = db.execute("""
            SELECT
                d.*,
                t.deduction_name,
                t.calculation_type

            FROM payroll_employee_deductions d

            JOIN payroll_deduction_types t
                ON t.id = d.deduction_type_id

            WHERE d.employee_id = ?
            ORDER BY d.id DESC
        """, (employee_id,)).fetchall()

        db.close()
        return records

    @staticmethod
    def assign(
        employee_id,
        deduction_type_id,
        amount,
        effective_date
    ):

        amount = money(amount)
        effective_date = validate_date(effective_date)

        db = get_db()

        try:
            db.execute("""
                INSERT INTO payroll_employee_deductions (
                    employee_id,
                    deduction_type_id,
                    amount,
                    effective_date,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, 'Active', ?)
            """, (
                employee_id,
                deduction_type_id,
                str(amount),
                effective_date,
                timestamp()
            ))

            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


# ==================================================
# SALARY HISTORY
# ==================================================

class SalaryHistory:

    @staticmethod
    def get_by_employee(employee_id):

        db = get_db()

        records = db.execute("""
            SELECT *
            FROM payroll_salary_history
            WHERE employee_id = ?
            ORDER BY effective_date DESC, id DESC
        """, (employee_id,)).fetchall()

        db.close()
        return records

    @staticmethod
    def create(
        employee_id,
        old_salary,
        new_salary,
        effective_date,
        reason,
        created_by
    ):

        old_salary = money(old_salary)
        new_salary = money(new_salary)
        effective_date = validate_date(effective_date)

        if new_salary == old_salary:
            raise ValueError(
                "New salary must differ from the current salary."
            )

        db = get_db()

        try:
            db.execute("""
                INSERT INTO payroll_salary_history (
                    employee_id,
                    old_salary,
                    new_salary,
                    effective_date,
                    reason,
                    approval_status,
                    created_by,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)
            """, (
                employee_id,
                str(old_salary),
                str(new_salary),
                effective_date,
                reason,
                str(created_by),
                timestamp()
            ))

            history_id = db.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

            db.execute("""
                INSERT INTO payroll_salary_approvals (
                    salary_history_id,
                    approval_level,
                    approval_status,
                    created_at
                )
                VALUES (?, 'Payroll Manager', 'Pending', ?)
            """, (history_id, timestamp()))

            db.commit()
            return history_id

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()