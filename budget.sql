-- =========================================================
-- DEREJE WOOD & KITCHEN CABINET MANAGEMENT SYSTEM
-- Budget Management - SQLite schema
-- File: budget.sql
-- =========================================================
-- This file mirrors the six budget tables the application
-- creates in users.db on boot (see the BUDGET_*_DDL constants
-- and init_db in app.py). It is plain SQLite DDL, so it can be
-- reviewed or applied to another SQLite file without the
-- PostgreSQL-only constructs (IDENTITY, TIMESTAMPTZ, JSONB,
-- INET) the earlier draft used.
--
-- Budget lives in the shared users.db. department_id points at
-- User Management's departments table, and the finance_* links
-- point at the Finance ledger rows that are kept in sync with
-- each budget expenditure. Apply this file only to a database
-- where the departments, finance_invoices and finance_transactions
-- tables already exist.

PRAGMA foreign_keys = ON;

BEGIN;

-- 1. Annual budget plans
CREATE TABLE IF NOT EXISTS budget_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_name TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    total_budget NUMERIC NOT NULL CHECK (total_budget >= 0),
    currency TEXT NOT NULL DEFAULT 'ETB'
        CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
    status TEXT NOT NULL DEFAULT 'Draft'
        CHECK (status IN ('Draft', 'Approved', 'Active', 'Closed')),
    created_by INTEGER,
    created_by_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Departmental budget allocations
CREATE TABLE IF NOT EXISTS budget_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    allocation_name TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT 'general',
    department_id INTEGER REFERENCES departments(id),
    currency TEXT NOT NULL DEFAULT 'ETB'
        CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
    allocated_amount NUMERIC NOT NULL CHECK (allocated_amount >= 0),
    status TEXT NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Closed')),
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_by_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES budget_plans(id)
);

-- 3. Budget requests
CREATE TABLE IF NOT EXISTS budget_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_title TEXT NOT NULL,
    department TEXT NOT NULL,
    department_id INTEGER REFERENCES departments(id),
    justification TEXT NOT NULL DEFAULT '',
    requested_amount NUMERIC NOT NULL CHECK (requested_amount > 0),
    currency TEXT NOT NULL DEFAULT 'ETB'
        CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Rejected')),
    plan_id INTEGER,
    requested_by INTEGER,
    requested_by_name TEXT NOT NULL DEFAULT '',
    review_comment TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES budget_plans(id)
);

-- 4. Approval decisions and history
CREATE TABLE IF NOT EXISTS budget_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    approver_id INTEGER,
    approver_name TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES budget_requests(id)
        ON DELETE CASCADE
);

-- 5. Budget expenditure records
CREATE TABLE IF NOT EXISTS budget_expenditures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    allocation_id INTEGER NOT NULL,
    description TEXT NOT NULL,
    amount NUMERIC NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'ETB'
        CHECK (currency IN ('ETB', 'USD', 'EUR', 'GBP')),
    reference TEXT NOT NULL DEFAULT '',
    recorded_by INTEGER,
    recorded_by_name TEXT NOT NULL DEFAULT '',
    procurement_order_id INTEGER,
    finance_invoice_id INTEGER REFERENCES finance_invoices(id),
    finance_transaction_id INTEGER REFERENCES finance_transactions(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (allocation_id) REFERENCES budget_allocations(id)
);

-- 6. Committed spend held against an allocation
CREATE TABLE IF NOT EXISTS budget_commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    allocation_id INTEGER NOT NULL,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    amount NUMERIC NOT NULL CHECK (amount >= 0),
    currency TEXT NOT NULL DEFAULT 'ETB',
    status TEXT NOT NULL DEFAULT 'Open'
        CHECK (status IN ('Open', 'Released', 'Converted')),
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_table, source_id),
    FOREIGN KEY (allocation_id) REFERENCES budget_allocations(id)
);

-- Indexes used by the budget queries
CREATE INDEX IF NOT EXISTS idx_budget_alloc_plan
    ON budget_allocations(plan_id);

CREATE INDEX IF NOT EXISTS idx_budget_alloc_department
    ON budget_allocations(department);

CREATE INDEX IF NOT EXISTS idx_budget_alloc_department_id
    ON budget_allocations(department_id);

CREATE INDEX IF NOT EXISTS idx_budget_req_department_id
    ON budget_requests(department_id);

CREATE INDEX IF NOT EXISTS idx_budget_req_requester
    ON budget_requests(requested_by);

CREATE INDEX IF NOT EXISTS idx_budget_exp_recorder
    ON budget_expenditures(recorded_by);

CREATE INDEX IF NOT EXISTS idx_budget_exp_finance_tx
    ON budget_expenditures(finance_transaction_id);

CREATE INDEX IF NOT EXISTS idx_budget_exp_finance_invoice
    ON budget_expenditures(finance_invoice_id);

CREATE INDEX IF NOT EXISTS idx_budget_expenditure_alloc
    ON budget_expenditures(allocation_id);

CREATE INDEX IF NOT EXISTS idx_budget_commitment_alloc
    ON budget_commitments(allocation_id, status);

-- The Finance side of the link is added by app.py on boot:
--   ALTER TABLE finance_transactions
--       ADD COLUMN budget_expenditure_id INTEGER
--       REFERENCES budget_expenditures(id);
-- It is not listed above because this file only creates budget tables.

COMMIT;
