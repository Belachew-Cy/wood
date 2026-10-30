
import sqlite3
from pathlib import Path

# Use the same folder as this script.
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "users.db"

if not DB_PATH.exists():
    raise FileNotFoundError(
        f"Database not found: {DB_PATH}\n"
        "Check that you are in the correct project folder."
    )

db = sqlite3.connect(DB_PATH)

try:
    # Confirm the existing employee table is available.
    tables = {
        row[0]
        for row in db.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table'
        """)
    }

    if "employees" not in tables:
        raise RuntimeError(
            "The employees table does not exist. "
            "Check that this is the correct database."
        )

    # Leave Management
    db.execute("""
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            leave_type TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            days_requested INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'Approved', 'Rejected')),
            reviewed_by TEXT DEFAULT '',
            review_comment TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
    """)

    # Employee Training
    db.execute("""
        CREATE TABLE IF NOT EXISTS employee_training (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            course_title TEXT NOT NULL,
            provider TEXT DEFAULT '',
            start_date TEXT NOT NULL,
            end_date TEXT,
            status TEXT NOT NULL DEFAULT 'Planned'
                CHECK (status IN (
                    'Planned', 'In Progress', 'Completed', 'Cancelled'
                )),
            certificate TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
    """)

    # Performance Reviews
    db.execute("""
        CREATE TABLE IF NOT EXISTS performance_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            review_date TEXT NOT NULL,
            review_period TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            strengths TEXT DEFAULT '',
            improvements TEXT DEFAULT '',
            goals TEXT DEFAULT '',
            reviewer TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
    """)

    db.commit()

    print("HR database tables created successfully.")

    for table in (
        "leave_requests",
        "employee_training",
        "performance_reviews",
    ):
        print(f"\n{table}:")
        for column in db.execute(
            f"PRAGMA table_info({table})"
        ).fetchall():
            print(f"  {column[1]} ({column[2]})")

except Exception:
    db.rollback()
    raise

finally:
    db.close()
