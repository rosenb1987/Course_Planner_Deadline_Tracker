-- database schema for the coursework planner project
-- this file creates the main tables used by the app


-- =========================
-- USERS
-- =========================
-- stores login information for each user
CREATE TABLE users (
    -- unique id for each user
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- username must be unique so two people cannot register the same one
    username TEXT UNIQUE NOT NULL,

    -- password is stored as a hash, not plain text, for security
    password_hash TEXT NOT NULL,

    -- date/time the account was created
    created_at TEXT NOT NULL
);


-- =========================
-- TASKS
-- =========================
-- stores all tasks / coursework items that belong to a user
CREATE TABLE tasks (
    -- unique id for each task
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- links the task back to the user who created it
    user_id INTEGER NOT NULL,

    -- module code or module name the task belongs to
    module_name TEXT NOT NULL,

    -- main task title, for example "IPD slides"
    title TEXT NOT NULL,

    -- optional extra details about the task
    description TEXT,

    -- deadline is split into date and time so it is easier to use in forms
    due_date TEXT NOT NULL,
    due_time TEXT NOT NULL DEFAULT '23:59',

    -- current progress state of the task
    status TEXT NOT NULL DEFAULT 'To do',

    -- stored priority chosen by the user
    priority TEXT NOT NULL DEFAULT 'Medium',

    -- date/time when the task was first added
    created_at TEXT NOT NULL,

    -- if a user is deleted, all their tasks should also be removed
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- =========================
-- USER SETTINGS
-- =========================
-- stores personal settings for each user
CREATE TABLE user_settings (
    -- unique id for the settings row
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- one settings row per user
    user_id INTEGER NOT NULL UNIQUE,

    -- lets the user choose how many days count as "due soon"
    due_soon_days INTEGER NOT NULL DEFAULT 3,

    -- default number of hours before deadline for reminders
    reminder_hours INTEGER NOT NULL DEFAULT 24,

    -- stores when the settings row was created
    created_at TEXT NOT NULL,

    -- if a user is removed, their settings should also be removed
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- =========================
-- REMINDERS
-- =========================
-- stores reminder records related to tasks
CREATE TABLE reminders (
    -- unique id for each reminder
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- task the reminder belongs to
    task_id INTEGER NOT NULL,

    -- user who owns that reminder
    user_id INTEGER NOT NULL,

    -- date/time when the reminder should happen
    remind_at TEXT NOT NULL,

    -- 0 = not sent yet, 1 = already sent
    sent INTEGER NOT NULL DEFAULT 0,

    -- date/time reminder record was created
    created_at TEXT NOT NULL,

    -- if task is deleted, reminder should also be deleted
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,

    -- if user is deleted, reminders should also be deleted
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- =========================
-- INDEXES
-- =========================
-- indexes help common queries run faster

-- useful when selecting tasks for one user
CREATE INDEX idx_tasks_user ON tasks(user_id);

-- useful when sorting/filtering tasks by deadline date
CREATE INDEX idx_tasks_due_date ON tasks(due_date);

-- useful if reminders are checked by time
CREATE INDEX idx_reminders_time ON reminders(remind_at);