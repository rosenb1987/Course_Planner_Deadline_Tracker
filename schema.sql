-- Basic user accounts
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Tasks belong to a user and store deadlines + status tracking
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,

    -- What module the task belongs to (keeps the dashboard grouped nicely)
    module_name TEXT NOT NULL,

    -- Core task details
    title TEXT NOT NULL,
    description TEXT,

    -- Deadline split into date + time so sorting/filtering is more accurate
    due_date TEXT NOT NULL,
    due_time TEXT NOT NULL DEFAULT '23:59',

    -- Simple workflow states
    status TEXT NOT NULL DEFAULT 'To do',

    -- Stored priority (UI can still “auto-bump” it when deadlines are close)
    priority TEXT NOT NULL DEFAULT 'Medium',

    created_at TEXT NOT NULL,

    -- Link task back to the user that owns it
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Small per-user settings table (handy for customising the experience later)
CREATE TABLE user_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- One settings row per user
    user_id INTEGER NOT NULL UNIQUE,

    -- How many days counts as "due soon" (defaults to 3)
    due_soon_days INTEGER NOT NULL DEFAULT 3,

    created_at TEXT NOT NULL,

    FOREIGN KEY(user_id) REFERENCES users(id)
);
