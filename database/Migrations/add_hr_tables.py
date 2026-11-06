"""Create the HR and Performance tables this project added after the split.

Leave and training rows live in ``database/HR_Database/hr.db``; every review
lives in ``database/Performance_Database/performance.db``. Each table is created
in the file that owns it, and the ``REFERENCES employees(id)`` link is dropped
because SQLite cannot enforce a key across two files: the id stays a plain
column that the application checks.

Run from the project root:

    .venv/Scripts/python.exe database/Migrations/add_hr_tables.py
"""
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from database import database_config  # noqa: E402


TABLES = {
    "hr": (
        ("leave_requests", """
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
        """),
        ("employee_training", """
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
        """),
    ),
    "performance": (
        ("performance_reviews", """
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
        """),
    ),
}


def main():
    if not database_config.module_path("user").exists():
        raise FileNotFoundError(
            "Database not found: {}\n"
            "Check that you are in the correct project folder.".format(
                database_config.module_path("user")))

    for key, tables in TABLES.items():
        path = database_config.module_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(path))
        try:
            for name, ddl in tables:
                # Cross-file REFERENCES clauses are stripped, as the app does
                # for every CREATE TABLE it runs.
                db.execute(database_config.strip_foreign_keys(ddl, schema=key))
            db.commit()
            print("{} database tables ready: {}".format(
                key, path))
            for name, _ddl in tables:
                print("\n{}:".format(name))
                for column in db.execute(
                        "PRAGMA table_info({})".format(name)).fetchall():
                    print("  {} ({})".format(column[1], column[2]))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


if __name__ == "__main__":
    main()
