# Main Flask app for the coursework planner / deadline tracker
# this file handles the main routes, database helpers and most of the app logic

import os
import csv
import io
import sqlite3
import calendar
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse, unquote

from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash

from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_wtf.csrf import CSRFError

app = Flask(__name__)

# keeping the secret key outside the code is safer when deploying
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# session security settings
# secure is commented out for now because this project runs locally
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # SESSION_COOKIE_SECURE=True,  # enable if deploying over HTTPS
)

# csrf protection for forms
app.config["WTF_CSRF_ENABLED"] = True
app.config["WTF_CSRF_TIME_LIMIT"] = None

csrf = CSRFProtect(app)

# project folder and sqlite database path
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.db"


# -------------------------
# Context processors / errors
# -------------------------

@app.context_processor
def inject_cache_bust():
    # used to force the browser to reload css/js changes
    return {"cache_bust": int(datetime.now().timestamp())}


@app.context_processor
def inject_csrf_token():
    # makes csrf_token() available in all templates
    return {"csrf_token": generate_csrf}


@app.errorhandler(CSRFError)
def handle_csrf_error(e: CSRFError):
    # simple custom error page if csrf fails
    return (
        f"<h1>400 Bad Request</h1>"
        f"<p>{e.description}</p>"
        f"<p><strong>Fix:</strong> Make sure your form includes "
        f"<code>&lt;input type='hidden' name='csrf_token' value='{{{{ csrf_token() }}}}'&gt;</code>"
        f"</p>"
        f"<p><strong>Tip:</strong> Hard refresh the page (Ctrl+F5) after updating templates.</p>",
        400,
    )


# -------------------------
# Database helpers
# -------------------------

def get_db():
    # opens sqlite database connection
    # row_factory lets me access columns by name instead of index
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_tasks_priority_column(conn: sqlite3.Connection):
    # adds priority column if database was created before this feature existed
    cols = conn.execute("PRAGMA table_info(tasks)").fetchall()
    col_names = {c["name"] for c in cols}
    if "priority" not in col_names:
        conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'Medium'")
        conn.commit()


def ensure_tasks_due_time_column(conn: sqlite3.Connection):
    # same idea here but for due time
    cols = conn.execute("PRAGMA table_info(tasks)").fetchall()
    col_names = {c["name"] for c in cols}
    if "due_time" not in col_names:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT NOT NULL DEFAULT '23:59'")
        conn.commit()


def ensure_user_settings_table(conn: sqlite3.Connection):
    # creates settings table if it does not already exist
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            due_soon_days INTEGER NOT NULL DEFAULT 3,
            reminder_hours INTEGER NOT NULL DEFAULT 24,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()


def ensure_reminders_table(conn: sqlite3.Connection):
    # reminder table for future reminder support / settings
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            remind_at TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()


def ensure_indexes(conn: sqlite3.Connection):
    # indexes help speed up common queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_at)")
    conn.commit()


def init_db():
    # creates the database from schema.sql if it does not exist yet
    # also makes sure newer columns/tables exist for older databases
    if not DB_PATH.exists():
        conn = get_db()
        with open(BASE_DIR / "schema.sql", "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

        ensure_tasks_priority_column(conn)
        ensure_tasks_due_time_column(conn)
        ensure_user_settings_table(conn)
        ensure_reminders_table(conn)
        ensure_indexes(conn)

        conn.close()
        return

    conn = get_db()
    try:
        ensure_tasks_priority_column(conn)
        ensure_tasks_due_time_column(conn)
        ensure_user_settings_table(conn)
        ensure_reminders_table(conn)
        ensure_indexes(conn)
    finally:
        conn.close()


@app.before_request
def before_request():
    # make sure db structure is ready before each request
    init_db()


# -------------------------
# General helpers
# -------------------------

def login_required_view(f):
    # custom decorator to protect routes that need login
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def safe_redirect_back(default_endpoint: str, **default_values):
    """
    Redirect back to the referring page if it belongs to this app,
    otherwise fall back to a safe internal endpoint.
    """
    # this is used after edits/deletes so the user returns to where they were
    next_url = request.form.get("next") or request.args.get("next") or request.referrer
    if next_url:
        try:
            parsed = urlparse(next_url)
            if parsed.netloc in ("", request.host):
                return redirect(next_url)
        except Exception:
            pass
    return redirect(url_for(default_endpoint, **default_values))


def format_iso_datetime(iso_str: str) -> str:
    # converts stored datetime into something nicer for the UI
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso_str


def format_ymd_date(ymd_str: str) -> str:
    # converts yyyy-mm-dd into dd/mm/yyyy
    if not ymd_str:
        return ""
    try:
        d = datetime.strptime(ymd_str, "%Y-%m-%d").date()
        return d.strftime("%d/%m/%Y")
    except Exception:
        return ymd_str


def normalize_priority(p: str) -> str:
    # only allow the 3 priorities used by the project
    return p if p in ["Low", "Medium", "High"] else "Medium"


def normalize_time_hhmm(t: str) -> str:
    # if time is missing or invalid just default to end of day
    if not t:
        return "23:59"
    try:
        datetime.strptime(t, "%H:%M")
        return t
    except Exception:
        return "23:59"


def normalize_due_soon_days(value: str | int | None) -> int:
    # limits due soon setting to a sensible range
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 3
    return min(max(days, 1), 30)


def normalize_reminder_hours(value: str | int | None) -> int:
    # limits reminder hours so user cannot enter something unrealistic
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return 24
    return min(max(hours, 1), 168)


def parse_due_datetime(due_date_str: str, due_time_str: str):
    # combines separate date and time fields into one datetime object
    if not due_date_str:
        return None
    due_time_str = normalize_time_hhmm(due_time_str)
    try:
        return datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return None


def normalize_module_name(value: str | None) -> str:
    # trims spaces from module names
    return (value or "").strip()


def get_countdown_text(task: dict) -> str:
    # calculates a readable countdown for each task
    if task.get("status") == "Completed":
        return "Completed"

    due_dt = parse_due_datetime(task.get("due_date", ""), task.get("due_time", "23:59"))
    if due_dt is None:
        return ""

    now = datetime.now()
    delta_seconds = int((due_dt - now).total_seconds())

    if delta_seconds < 0:
        # overdue case
        delta_seconds = abs(delta_seconds)
        days = delta_seconds // 86400
        hours = (delta_seconds % 86400) // 3600
        minutes = (delta_seconds % 3600) // 60

        if days > 0:
            return f"Overdue by {days} day{'s' if days != 1 else ''}"
        if hours > 0:
            return f"Overdue by {hours} hour{'s' if hours != 1 else ''}"
        if minutes > 0:
            return f"Overdue by {minutes} minute{'s' if minutes != 1 else ''}"
        return "Overdue"

    # not overdue yet
    days = delta_seconds // 86400
    hours = (delta_seconds % 86400) // 3600
    minutes = (delta_seconds % 3600) // 60

    if days > 0:
        return f"Due in {days} day{'s' if days != 1 else ''}"
    if hours > 0:
        return f"Due in {hours} hour{'s' if hours != 1 else ''}"
    if minutes > 0:
        return f"Due in {minutes} minute{'s' if minutes != 1 else ''}"
    return "Due now"


def ensure_user_settings_row(user_id: int):
    # makes sure each user has a settings row
    conn = get_db()
    exists = conn.execute(
        "SELECT 1 FROM user_settings WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if not exists:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, due_soon_days, reminder_hours, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, 3, 24, datetime.now().isoformat()),
        )
        conn.commit()

    conn.close()


def get_user_settings(user_id: int) -> dict:
    # gets settings for one user
    ensure_user_settings_row(user_id)

    conn = get_db()
    row = conn.execute(
        """
        SELECT *
        FROM user_settings
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    conn.close()

    if row is None:
        return {
            "user_id": user_id,
            "due_soon_days": 3,
            "reminder_hours": 24,
        }

    settings = dict(row)
    settings["due_soon_days"] = normalize_due_soon_days(settings.get("due_soon_days", 3))
    settings["reminder_hours"] = normalize_reminder_hours(settings.get("reminder_hours", 24))
    return settings


def update_user_settings(user_id: int, due_soon_days: int, reminder_hours: int):
    # updates the settings form values
    ensure_user_settings_row(user_id)

    conn = get_db()
    conn.execute(
        """
        UPDATE user_settings
        SET due_soon_days = ?, reminder_hours = ?
        WHERE user_id = ?
        """,
        (due_soon_days, reminder_hours, user_id),
    )
    conn.commit()
    conn.close()


def effective_priority(
    due_date_str: str,
    due_time_str: str,
    status: str,
    stored_priority: str,
    due_soon_days: int = 3,
) -> tuple[str, bool]:
    # this can override stored priority if a deadline is close or passed
    pr = normalize_priority(stored_priority)

    if status == "Completed":
        return pr, False

    due_dt = parse_due_datetime(due_date_str, due_time_str)
    if due_dt is None:
        return pr, False

    now = datetime.now()
    today = date.today()

    if due_dt < now or due_dt.date() == today:
        return "High", True

    days_left = (due_dt.date() - today).days
    if 1 <= days_left <= due_soon_days:
        return "Medium", True

    return pr, False


def get_task_deadline_state(task: dict, now: datetime, today: date, due_soon_days: int = 3) -> str:
    # determines the visual state shown in dashboard/calendar
    due_dt = parse_due_datetime(task.get("due_date", ""), task.get("due_time", "23:59"))

    if task.get("status") == "Completed":
        return "completed"
    if due_dt is not None and due_dt < now:
        return "overdue"
    if due_dt is not None and due_dt.date() == today:
        return "due_today"
    if due_dt is not None and 0 < (due_dt.date() - today).days <= due_soon_days:
        return "due_soon"
    return "normal"


def enrich_task_row(row: sqlite3.Row | dict, due_soon_days: int = 3) -> dict:
    # adds extra calculated values to each task for display in templates
    task = dict(row)
    now = datetime.now()
    today = date.today()

    task["module_name"] = normalize_module_name(task.get("module_name", ""))
    task["due_time"] = normalize_time_hhmm(task.get("due_time", "23:59"))
    task["deadline_state"] = get_task_deadline_state(task, now, today, due_soon_days)

    eff_pr, was_auto = effective_priority(
        task.get("due_date", ""),
        task.get("due_time", "23:59"),
        task.get("status", "To do"),
        task.get("priority", "Medium"),
        due_soon_days,
    )
    task["priority_stored"] = normalize_priority(task.get("priority", "Medium"))
    task["priority"] = eff_pr
    task["priority_auto"] = was_auto

    task["due_date_display"] = format_ymd_date(task.get("due_date", ""))
    task["deadline_display"] = f"{task['due_date_display']} {task['due_time']}"
    task["created_at_display"] = format_iso_datetime(task.get("created_at", ""))
    task["due_iso"] = f"{task.get('due_date', '')}T{task.get('due_time', '23:59')}"
    task["countdown_text"] = get_countdown_text(task)
    return task


def get_user_task_by_id(task_id: int, user_id: int):
    # gets one task but only if it belongs to the logged-in user
    conn = get_db()
    row = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, user_id),
    ).fetchone()
    conn.close()
    return row


def get_user_modules(user_id: int) -> list[dict]:
    # gets a list of unique modules for the modules page
    conn = get_db()
    rows = conn.execute(
        """
        SELECT module_name, COUNT(*) AS total_tasks
        FROM tasks
        WHERE user_id = ?
          AND TRIM(module_name) != ''
        GROUP BY module_name
        ORDER BY LOWER(module_name) ASC
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    modules = []
    for row in rows:
        modules.append(
            {
                "module_name": row["module_name"],
                "total_tasks": row["total_tasks"],
            }
        )
    return modules


def get_module_tasks_for_user(user_id: int, module_name: str):
    # gets all tasks for one chosen module
    conn = get_db()
    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE user_id = ?
          AND module_name = ?
        ORDER BY due_date ASC, due_time ASC
        """,
        (user_id, module_name),
    ).fetchall()
    conn.close()
    return rows


def build_module_summary_from_tasks(tasks: list[dict]) -> dict:
    # summary stats used on module detail page
    total = len(tasks)
    completed = sum(1 for t in tasks if t.get("status") == "Completed")
    todo = sum(1 for t in tasks if t.get("status") == "To do")
    in_progress = sum(1 for t in tasks if t.get("status") == "In progress")
    overdue = sum(1 for t in tasks if t.get("deadline_state") == "overdue")
    due_today = sum(1 for t in tasks if t.get("deadline_state") == "due_today")
    due_soon = sum(1 for t in tasks if t.get("deadline_state") == "due_soon")
    completion_percent = 0 if total == 0 else round((completed / total) * 100)

    return {
        "total": total,
        "completed": completed,
        "todo": todo,
        "in_progress": in_progress,
        "overdue": overdue,
        "due_today": due_today,
        "due_soon": due_soon,
        "completion_percent": completion_percent,
    }


def parse_highlight_task_id() -> int | None:
    """
    Supports both the newer highlight_task param and older open_task param.
    """
    # this lets old links still work
    raw = (
        request.args.get("highlight_task", "").strip()
        or request.args.get("open_task", "").strip()
    )
    if raw.isdigit():
        return int(raw)
    return None


def parse_open_edit_flag() -> bool:
    # decides whether a task edit box should auto-open
    raw = request.args.get("open_edit", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# -------------------------
# ICS helpers
# -------------------------

def escape_ics_text(value: str) -> str:
    # escapes special characters for .ics format
    if not value:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\r\n", r"\n")
        .replace("\n", r"\n")
    )


def format_ics_timestamp(dt: datetime) -> str:
    # converts datetime to utc format needed by calendar files
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_task_ics_event(task: sqlite3.Row | dict, username: str) -> str:
    # builds one calendar event for a task
    task = dict(task)
    due_dt = parse_due_datetime(task.get("due_date", ""), task.get("due_time", "23:59"))
    if due_dt is None:
        due_dt = datetime.combine(date.today(), datetime.strptime("23:59", "%H:%M").time())

    # starts event 1 hour before deadline
    start_dt = due_dt - timedelta(hours=1)

    created_raw = task.get("created_at", "")
    try:
        stamp_dt = datetime.fromisoformat(created_raw) if created_raw else datetime.now()
    except Exception:
        stamp_dt = datetime.now()

    module_name = task.get("module_name", "")
    title = task.get("title", "Task")
    description = task.get("description", "") or ""
    status = task.get("status", "To do")
    priority = task.get("priority", "Medium")

    uid = f"task-{task.get('id', '0')}-user-{task.get('user_id', '0')}@courseplanner.local"
    summary = f"{title} ({module_name})" if module_name else title

    description_lines = [
        f"Module: {module_name}" if module_name else "",
        f"Task: {title}",
        f"Status: {status}",
        f"Priority: {priority}",
        "",
        description,
        "",
        f"Exported by: {username}",
    ]
    description_text = "\n".join([line for line in description_lines if line is not None])

    return "\r\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{escape_ics_text(uid)}",
            f"DTSTAMP:{format_ics_timestamp(stamp_dt)}",
            f"DTSTART:{format_ics_timestamp(start_dt)}",
            f"DTEND:{format_ics_timestamp(due_dt)}",
            f"SUMMARY:{escape_ics_text(summary)}",
            f"DESCRIPTION:{escape_ics_text(description_text)}",
            f"STATUS:{'COMPLETED' if status == 'Completed' else 'CONFIRMED'}",
            "END:VEVENT",
        ]
    )


def build_ics_calendar(events: list[str]) -> str:
    # wraps all events inside a valid calendar file
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Course Planner & Deadline Tracker//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            *events,
            "END:VCALENDAR",
            "",
        ]
    )


# -------------------------
# Calendar helpers
# -------------------------

def build_month_calendar_data(user_id: int, year: int, month: int, due_soon_days: int = 3):
    # prepares the data structure used by calendar.html
    month_key = f"{year:04d}-{month:02d}"

    conn = get_db()
    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE user_id = ?
          AND substr(due_date, 1, 7) = ?
        ORDER BY due_date ASC, due_time ASC
        """,
        (user_id, month_key),
    ).fetchall()
    conn.close()

    tasks_by_date: dict[str, list[dict]] = {}
    for row in rows:
        task = enrich_task_row(row, due_soon_days)
        key = task.get("due_date", "")
        tasks_by_date.setdefault(key, []).append(task)

    # monday-first calendar layout
    cal = calendar.Calendar(firstweekday=0)
    weeks = []

    for week in cal.monthdatescalendar(year, month):
        week_days = []
        for day_value in week:
            day_key = day_value.strftime("%Y-%m-%d")
            week_days.append(
                {
                    "date": day_value,
                    "day_number": day_value.day,
                    "is_current_month": day_value.month == month,
                    "is_today": day_value == date.today(),
                    "tasks": tasks_by_date.get(day_key, []),
                }
            )
        weeks.append(week_days)

    current_month = date(year, month, 1)
    prev_month_last_day = current_month - timedelta(days=1)
    next_month_first_day = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    return {
        "weeks": weeks,
        "month_label": current_month.strftime("%B %Y"),
        "prev_year": prev_month_last_day.year,
        "prev_month": prev_month_last_day.month,
        "next_year": next_month_first_day.year,
        "next_month": next_month_first_day.month,
        "year": year,
        "month": month,
        "weekday_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }


# -------------------------
# Routes
# -------------------------

@app.route("/")
def index():
    # send logged-in users to dashboard, others to login
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    # handles new account creation
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()

        # basic validation
        if not username or not password or not confirm:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("register"))

        if len(username) < 3 or len(username) > 30:
            flash("Username must be 3–30 characters.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("register"))

        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), datetime.now().isoformat()),
            )
            user_id = cur.lastrowid

            # create default settings for the new user
            conn.execute(
                """
                INSERT INTO user_settings (user_id, due_soon_days, reminder_hours, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, 3, 24, datetime.now().isoformat()),
            )
            conn.commit()

        except sqlite3.IntegrityError:
            flash("Username already exists. Please choose another one.", "error")
            return redirect(url_for("register"))
        finally:
            conn.close()

        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", title="Register")


@app.route("/login", methods=["GET", "POST"])
def login():
    # handles logging in
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            ensure_user_settings_row(user["id"])
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html", title="Login")


@app.route("/logout")
def logout():
    # clear session and send user back to login
    session.clear()
    flash("You are logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required_view
def dashboard():
    # main dashboard page
    settings = get_user_settings(session["user_id"])
    due_soon_days = settings["due_soon_days"]

    if request.method == "POST":
        # add new task form
        module_name = normalize_module_name(request.form.get("module_name", ""))
        title = request.form.get("title", "").strip()
        due_date = request.form.get("due_date", "").strip()
        due_time = normalize_time_hhmm(request.form.get("due_time", "").strip())
        description = request.form.get("description", "").strip()
        priority = normalize_priority(request.form.get("priority", "Medium").strip())

        if not module_name or not title or not due_date:
            flash("Module, Title and Due Date are required.", "error")
            return redirect(url_for("dashboard"))

        if parse_due_datetime(due_date, due_time) is None:
            flash("Invalid deadline. Please use the date/time pickers.", "error")
            return redirect(url_for("dashboard"))

        conn = get_db()
        conn.execute(
            """
            INSERT INTO tasks (user_id, module_name, title, description, due_date, due_time, status, created_at, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                module_name,
                title,
                description,
                due_date,
                due_time,
                "To do",
                datetime.now().isoformat(),
                priority,
            ),
        )
        conn.commit()
        conn.close()

        flash("Task added!", "success")
        return redirect(url_for("dashboard"))

    # get all user tasks for display
    conn = get_db()
    tasks_raw = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY due_date ASC, due_time ASC",
        (session["user_id"],),
    ).fetchall()
    conn.close()

    tasks = []
    total_tasks = 0
    todo_count = 0
    in_progress_count = 0
    completed_count = 0
    overdue_count = 0
    due_today_count = 0
    due_soon_count = 0

    module_set = set()
    module_summary_map: dict[str, dict] = {}
    attention = {"overdue": [], "due_today": [], "due_soon": []}

    highlighted_task_id = parse_highlight_task_id()
    open_edit_requested = parse_open_edit_flag()

    for row in tasks_raw:
        task = enrich_task_row(row, due_soon_days)
        total_tasks += 1

        # build module summary section
        module_name = normalize_module_name(task.get("module_name"))
        if module_name:
            module_set.add(module_name)
            if module_name not in module_summary_map:
                module_summary_map[module_name] = {"module": module_name, "total": 0, "completed": 0}
            module_summary_map[module_name]["total"] += 1

        # task status stats
        if task["status"] == "To do":
            todo_count += 1
        elif task["status"] == "In progress":
            in_progress_count += 1
        elif task["status"] == "Completed":
            completed_count += 1
            if module_name:
                module_summary_map[module_name]["completed"] += 1

        # deadline state stats
        if task["deadline_state"] == "overdue":
            overdue_count += 1
        elif task["deadline_state"] == "due_today":
            due_today_count += 1
        elif task["deadline_state"] == "due_soon":
            due_soon_count += 1

        # build attention banner but skip completed tasks
        if task["status"] != "Completed":
            if task["deadline_state"] == "overdue":
                attention["overdue"].append(task)
            elif task["deadline_state"] == "due_today":
                attention["due_today"].append(task)
            elif task["deadline_state"] == "due_soon":
                attention["due_soon"].append(task)

        # auto-open edit box if coming from calendar/module links
        task["open_edit"] = bool(highlighted_task_id == task["id"] and open_edit_requested)
        tasks.append(task)

    completion_percent = 0 if total_tasks == 0 else round((completed_count / total_tasks) * 100)
    unique_modules = sorted([m for m in module_set if m])

    module_summary = []
    for m in sorted(module_summary_map.keys(), key=lambda x: x.lower()):
        total = module_summary_map[m]["total"]
        completed = module_summary_map[m]["completed"]
        percent = 0 if total == 0 else round((completed / total) * 100)
        module_summary.append({"module": m, "total": total, "completed": completed, "percent": percent})

    return render_template(
        "dashboard.html",
        title="Dashboard",
        tasks=tasks,
        unique_modules=unique_modules,
        module_summary=module_summary,
        attention=attention,
        highlighted_task_id=highlighted_task_id,
        open_edit_requested=open_edit_requested,
        settings=settings,
        due_soon_days=due_soon_days,
        stats={
            "total": total_tasks,
            "todo": todo_count,
            "in_progress": in_progress_count,
            "completed": completed_count,
            "overdue": overdue_count,
            "due_today": due_today_count,
            "due_soon": due_soon_count,
            "completion_percent": completion_percent,
        },
    )


@app.route("/modules")
@login_required_view
def modules():
    # page that lists all modules
    user_modules = get_user_modules(session["user_id"])
    return render_template(
        "modules.html",
        title="Modules",
        modules=user_modules,
    )


@app.route("/module/<path:module_name>")
@login_required_view
def module_detail(module_name):
    # page showing only tasks from one module
    settings = get_user_settings(session["user_id"])
    due_soon_days = settings["due_soon_days"]

    module_name = normalize_module_name(unquote(module_name))
    if not module_name:
        flash("Module not found.", "error")
        return redirect(url_for("modules"))

    rows = get_module_tasks_for_user(session["user_id"], module_name)
    if not rows:
        flash("Module not found or it has no tasks.", "error")
        return redirect(url_for("modules"))

    tasks = [enrich_task_row(row, due_soon_days) for row in rows]
    summary = build_module_summary_from_tasks(tasks)

    highlighted_task_id = parse_highlight_task_id()
    open_edit_requested = parse_open_edit_flag()

    for task in tasks:
        task["open_edit"] = bool(highlighted_task_id == task["id"] and open_edit_requested)

    return render_template(
        "module_detail.html",
        title=f"Module: {module_name}",
        module_name=module_name,
        tasks=tasks,
        highlighted_task_id=highlighted_task_id,
        open_edit_requested=open_edit_requested,
        settings=settings,
        due_soon_days=due_soon_days,
        stats=summary,
    )


@app.route("/calendar")
@login_required_view
def calendar_view():
    # monthly calendar page
    settings = get_user_settings(session["user_id"])
    due_soon_days = settings["due_soon_days"]

    today = date.today()

    try:
        year = int(request.args.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year

    try:
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month

    if month < 1 or month > 12:
        month = today.month

    calendar_data = build_month_calendar_data(session["user_id"], year, month, due_soon_days)

    return render_template(
        "calendar.html",
        title="Calendar",
        calendar_data=calendar_data,
        settings=settings,
        due_soon_days=due_soon_days,
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required_view
def settings():
    # settings page for due soon days and reminder hours
    if request.method == "POST":
        due_soon_days = normalize_due_soon_days(request.form.get("due_soon_days"))
        reminder_hours = normalize_reminder_hours(request.form.get("reminder_hours"))

        update_user_settings(
            session["user_id"],
            due_soon_days=due_soon_days,
            reminder_hours=reminder_hours,
        )

        flash("Settings updated successfully.", "success")
        return redirect(url_for("settings"))

    settings_data = get_user_settings(session["user_id"])
    return render_template(
        "settings.html",
        title="Settings",
        settings=settings_data,
    )


@app.route("/export/csv")
@login_required_view
def export_csv():
    # exports all tasks as csv
    settings = get_user_settings(session["user_id"])
    due_soon_days = settings["due_soon_days"]

    conn = get_db()
    rows = conn.execute(
        """
        SELECT module_name, title, description, due_date, due_time, status, created_at, priority
        FROM tasks
        WHERE user_id = ?
        ORDER BY due_date ASC, due_time ASC
        """,
        (session["user_id"],),
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Module",
            "Title",
            "Description",
            "Deadline",
            "Status",
            "Priority (effective)",
            "Created At",
            "Countdown",
        ]
    )

    for r in rows:
        due_time = normalize_time_hhmm(r["due_time"])
        pr_eff, _ = effective_priority(
            r["due_date"],
            due_time,
            r["status"],
            r["priority"],
            due_soon_days,
        )

        enriched = enrich_task_row(r, due_soon_days)
        deadline_display = f"{format_ymd_date(r['due_date'])} {due_time}"

        writer.writerow(
            [
                r["module_name"],
                r["title"],
                r["description"] or "",
                deadline_display,
                r["status"],
                pr_eff,
                format_iso_datetime(r["created_at"]),
                enriched.get("countdown_text", ""),
            ]
        )

    csv_text = output.getvalue()
    output.close()

    filename = "tasks_" + datetime.now().strftime("%Y%m%d_%H%M") + ".csv"
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@app.route("/export/ics")
@login_required_view
def export_ics():
    # exports all tasks into one calendar file
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, user_id, module_name, title, description, due_date, due_time, status, created_at, priority
        FROM tasks
        WHERE user_id = ?
        ORDER BY due_date ASC, due_time ASC
        """,
        (session["user_id"],),
    ).fetchall()
    conn.close()

    events = [build_task_ics_event(row, session.get("username", "user")) for row in rows]
    ics_text = build_ics_calendar(events)

    filename = "tasks_calendar_" + datetime.now().strftime("%Y%m%d_%H%M") + ".ics"
    return Response(
        ics_text,
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/task/<int:task_id>/ics")
@login_required_view
def export_single_task_ics(task_id):
    # export only one task as its own .ics file
    row = get_user_task_by_id(task_id, session["user_id"])

    if row is None:
        flash("Task not found.", "error")
        return redirect(url_for("dashboard"))

    event = build_task_ics_event(row, session.get("username", "user"))
    ics_text = build_ics_calendar([event])

    safe_title = "".join(ch for ch in (row["title"] or "task") if ch.isalnum() or ch in ("-", "_", " ")).strip()
    safe_title = safe_title.replace(" ", "_") or "task"
    filename = f"{safe_title}_{task_id}.ics"

    return Response(
        ics_text,
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/task/<int:task_id>/go")
@login_required_view
def go_to_task(task_id):
    """
    Calendar shortcut:
    send user back to dashboard and request that task to be highlighted and edit-opened.
    """
    # used when clicking a task from the calendar view
    row = get_user_task_by_id(task_id, session["user_id"])
    if row is None:
        flash("Task not found.", "error")
        return redirect(url_for("calendar_view"))

    return redirect(
        url_for("dashboard", highlight_task=task_id, open_edit=1) + f"#task-{task_id}"
    )


@app.route("/task/<int:task_id>/edit", methods=["POST"])
@login_required_view
def edit_task(task_id):
    # handles editing an existing task
    module_name = normalize_module_name(request.form.get("module_name", ""))
    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date", "").strip()
    due_time = normalize_time_hhmm(request.form.get("due_time", "").strip())
    description = request.form.get("description", "").strip()
    priority = normalize_priority(request.form.get("priority", "Medium").strip())

    if not module_name or not title or not due_date:
        flash("Module, Title and Due Date are required to edit a task.", "error")
        return redirect(url_for("dashboard"))

    if parse_due_datetime(due_date, due_time) is None:
        flash("Invalid deadline. Please use the date/time pickers.", "error")
        return redirect(url_for("dashboard"))

    conn = get_db()
    cur = conn.execute(
        """
        UPDATE tasks
        SET module_name = ?, title = ?, due_date = ?, due_time = ?, description = ?, priority = ?
        WHERE id = ? AND user_id = ?
        """,
        (module_name, title, due_date, due_time, description, priority, task_id, session["user_id"]),
    )
    conn.commit()
    conn.close()

    flash(
        "Task updated successfully!" if cur.rowcount else "Task not found or you don't have permission to edit it.",
        "success" if cur.rowcount else "error",
    )
    return safe_redirect_back("dashboard")


@app.route("/task/<int:task_id>/delete", methods=["POST"])
@login_required_view
def delete_task(task_id):
    # deletes a task
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, session["user_id"]))
    conn.commit()
    conn.close()

    flash("Task deleted.", "info")
    return safe_redirect_back("dashboard")


@app.route("/task/<int:task_id>/status", methods=["POST"])
@login_required_view
def update_status(task_id):
    # updates task workflow status
    new_status = request.form.get("status", "To do")
    if new_status not in ["To do", "In progress", "Completed"]:
        new_status = "To do"

    conn = get_db()
    conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ? AND user_id = ?",
        (new_status, task_id, session["user_id"]),
    )
    conn.commit()
    conn.close()

    flash("Status updated.", "success")
    return safe_redirect_back("dashboard")


if __name__ == "__main__":
    # debug mode is useful while developing locally
    app.run(debug=True)