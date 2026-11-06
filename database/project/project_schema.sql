
-- =====================================================
-- DEREJE WOOD & CAM
-- PROJECT MANAGEMENT DATABASE
-- PostgreSQL
-- =====================================================

CREATE SCHEMA IF NOT EXISTS project_management;

-- 1. PROJECTS
CREATE TABLE IF NOT EXISTS project_management.projects (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_code VARCHAR(40) NOT NULL UNIQUE,
    project_name VARCHAR(180) NOT NULL,
    client_name VARCHAR(180),
    location VARCHAR(180),
    description TEXT,

    start_date DATE,
    end_date DATE,

    budget NUMERIC(14,2) NOT NULL DEFAULT 0,

    project_status VARCHAR(30) NOT NULL DEFAULT 'Planning'
        CHECK (project_status IN (
            'Planning',
            'In Progress',
            'On Hold',
            'Completed',
            'Cancelled'
        )),

    priority VARCHAR(20) NOT NULL DEFAULT 'Normal'
        CHECK (priority IN ('Low', 'Normal', 'High', 'Urgent')),

    manager_user_id VARCHAR(80),
    created_by VARCHAR(80),
    updated_by VARCHAR(80),

    is_archived BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT valid_project_dates
        CHECK (
            start_date IS NULL OR end_date IS NULL
            OR end_date >= start_date
        ),

    CONSTRAINT valid_project_budget CHECK (budget >= 0)
);

-- 2. PROJECT TEAM MEMBERS
CREATE TABLE IF NOT EXISTS project_management.project_members (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    project_id BIGINT NOT NULL
        REFERENCES project_management.projects(id)
        ON DELETE CASCADE,

    user_id VARCHAR(80) NOT NULL,
    team_role VARCHAR(80) NOT NULL DEFAULT 'Team Member',

    assigned_by VARCHAR(80),

    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (project_id, user_id)
);

-- 3. PROJECT MILESTONES
CREATE TABLE IF NOT EXISTS project_management.project_milestones (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    project_id BIGINT NOT NULL
        REFERENCES project_management.projects(id)
        ON DELETE CASCADE,

    milestone_name VARCHAR(180) NOT NULL,
    description TEXT,

    due_date DATE NOT NULL,
    completed_date DATE,

    status VARCHAR(30) NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'In Progress', 'Completed')),

    created_by VARCHAR(80),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. PROJECT TASKS
CREATE TABLE IF NOT EXISTS project_management.project_tasks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    project_id BIGINT NOT NULL
        REFERENCES project_management.projects(id)
        ON DELETE CASCADE,

    task_title VARCHAR(180) NOT NULL,
    description TEXT,

    assigned_user_id VARCHAR(80),

    start_date DATE,
    due_date DATE,

    status VARCHAR(30) NOT NULL DEFAULT 'Not Started'
        CHECK (status IN (
            'Not Started',
            'In Progress',
            'Blocked',
            'Completed'
        )),

    priority VARCHAR(20) NOT NULL DEFAULT 'Normal'
        CHECK (priority IN ('Low', 'Normal', 'High', 'Urgent')),

    progress INTEGER NOT NULL DEFAULT 0
        CHECK (progress BETWEEN 0 AND 100),

    created_by VARCHAR(80),
    updated_by VARCHAR(80),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT valid_task_dates
        CHECK (
            start_date IS NULL OR due_date IS NULL
            OR due_date >= start_date
        )
);

-- 5. INDEXES
CREATE INDEX IF NOT EXISTS idx_projects_status
ON project_management.projects(project_status);

CREATE INDEX IF NOT EXISTS idx_projects_manager
ON project_management.projects(manager_user_id);

CREATE INDEX IF NOT EXISTS idx_tasks_project
ON project_management.project_tasks(project_id);

CREATE INDEX IF NOT EXISTS idx_tasks_assignee
ON project_management.project_tasks(assigned_user_id);

CREATE INDEX IF NOT EXISTS idx_milestones_project
ON project_management.project_milestones(project_id);

-- =====================================================
-- END OF PROJECT MANAGEMENT SCHEMA
-- =====================================================
