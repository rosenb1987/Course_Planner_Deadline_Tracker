# Main Flask app for the coursework planner / deadline tracker
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from pathlib import Path
from datetime import datetime, date
import csv
import io

app = Flask(__name__)

# Secret key for sessions/flash messages 
app.secret_key = "f9dK29#slP02@Kdm29sL!293kslPq"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.db"


# Cache-bust for static files so CSS updates show up immediately
@app.context_processor
def inject_cache_bust():
    return {"cache_bust": int(datetime.now().timestamp())}



# Database helpers

def get_db():
    """Open a DB connection and return rows as dict-like objects."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tasks_priority_column(conn: sqlite3.Connection):
    """Add the priority column if the database was created before that feature existed."""
    cols = conn.execute("PRAGMA table_info(tasks)").fetchall()
    col_names = {c["name"] for c in cols}
    if "priority" not in col_names:
        conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'Medium'")
        conn.commit()


def ensure_tasks_due_time_column(conn: sqlite3.Connection):
    """Same idea as priority: older DBs won't have due_time yet."""
    cols = conn.execute("PRAGMA table_info(tasks)").fetchall()
    col_names = {c["name"] for c in cols}
    if "due_time" not in col_names:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT NOT NULL DEFAULT '23:59'")
        conn.commit()


def init_db():
    """Create DB from schema.sql if it doesn't exist, otherwise run small migrations."""
    if not DB_PATH.exists():
        conn = get_db()
        with open(BASE_DIR / "schema.sql", "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

        # Safety: make sure new columns exist even on fresh DBs
        ensure_tasks_priority_column(conn)
        ensure_tasks_due_time_column(conn)

        conn.close()
        return

    # Existing DB: ensure columns are present (acts like a mini-migration)
    conn = get_db()
    try:
        ensure_tasks_priority_column(conn)
        ensure_tasks_due_time_column(conn)
    finally:
        conn.close()



# General helpers

def login_required():
    """Quick session check used across routes."""
    return "user_id" in session


def format_iso_datetime(iso_str: str) -> str:
    """Turn ISO timestamps into something readable (dd/mm/yyyy hh:mm)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso_str


def format_ymd_date(ymd_str: str) -> str:
    """Convert YYYY-MM-DD into dd/mm/yyyy for display."""
    if not ymd_str:
        return ""
    try:
        d = datetime.strptime(ymd_str, "%Y-%m-%d").date()
        return d.strftime("%d/%m/%Y")
    except Exception:
        return ymd_str


def normalize_priority(p: str) -> str:
    """Keep priority values clean (only Low/Medium/High)."""
    return p if p in ["Low", "Medium", "High"] else "Medium"


def normalize_time_hhmm(t: str) -> str:
    """Validate/clean time input. If it's invalid, fall back to end-of-day."""
    if not t:
        return "23:59"
    try:
        datetime.strptime(t, "%H:%M")
        return t
    except Exception:
        return "23:59"


def parse_due_datetime(due_date_str: str, due_time_str: str):
    """Combine the date + time strings into a real datetime object."""
    if not due_date_str:
        return None
    due_time_str = normalize_time_hhmm(due_time_str)
    try:
        return datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return None


def effective_priority(due_date_str: str, due_time_str: str, status: str, stored_priority: str) -> tuple[str, bool]:
    """
    Calculates the priority the UI should show.
    Returns: (priority_value, was_auto_applied)
    """
    pr = normalize_priority(stored_priority)

    # If the task is done, we just show whatever priority was stored
    if status == "Completed":
        return pr, False

    due_dt = parse_due_datetime(due_date_str, due_time_str)
    if due_dt is None:
        return pr, False

    now = datetime.now()
    today = date.today()

    # Past deadline OR due today = High priority
    if due_dt < now:
        return "High", True
    if due_dt.date() == today:
        return "High", True

    # 1–3 days away: medium (keeps things visible without panicking)
    days_left = (due_dt.date() - today).days
    if 1 <= days_left <= 3:
        return "Medium", True

    return pr, False


# Make sure DB exists before each request 
@app.before_request
def before_request():
    init_db()



# Routes

@app.route("/")
def index():
    # If you're already logged in, jump straight to the dashboard
    if login_required():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please fill in all fields.")
            return redirect(url_for("register"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), datetime.now().isoformat()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Unique username constraint triggered
            flash("Username already exists. Please choose another one.")
            return redirect(url_for("register"))
        finally:
            conn.close()

        flash("Account created! Please log in.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    # Clear session and send user back to login
    session.clear()
    flash("You are logged out.")
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not login_required():
        return redirect(url_for("login"))

    # Create a new task
    if request.method == "POST":
        module_name = request.form.get("module_name", "").strip()
        title = request.form.get("title", "").strip()
        due_date = request.form.get("due_date", "").strip()
        due_time = normalize_time_hhmm(request.form.get("due_time", "").strip())
        description = request.form.get("description", "").strip()
        priority = normalize_priority(request.form.get("priority", "Medium").strip())

        if not module_name or not title or not due_date:
            flash("Module, Title and Due Date are required.")
            return redirect(url_for("dashboard"))

        if parse_due_datetime(due_date, due_time) is None:
            flash("Invalid deadline. Please use the date/time pickers.")
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

        flash("Task added!")
        return redirect(url_for("dashboard"))

    # Pull tasks in deadline order (date first, then time)
    conn = get_db()
    tasks_raw = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY due_date ASC, due_time ASC",
        (session["user_id"],),
    ).fetchall()
    conn.close()

    tasks = []
    now = datetime.now()
    today = date.today()

    # Stats counters for the progress panel
    total_tasks = 0
    todo_count = 0
    in_progress_count = 0
    completed_count = 0
    overdue_count = 0
    due_soon_count = 0

    # Used to build the module progress list
    module_set = set()
    module_summary_map: dict[str, dict] = {}

    # Small "attention" bucket for the warning banner
    attention = {"overdue": [], "due_today": [], "due_soon": []}

    for row in tasks_raw:
        task = dict(row)
        total_tasks += 1

        module_name = (task.get("module_name") or "").strip()
        if module_name:
            module_set.add(module_name)
            if module_name not in module_summary_map:
                module_summary_map[module_name] = {"module": module_name, "total": 0, "completed": 0}
            module_summary_map[module_name]["total"] += 1

        # Status counts
        if task["status"] == "To do":
            todo_count += 1
        elif task["status"] == "In progress":
            in_progress_count += 1
        elif task["status"] == "Completed":
            completed_count += 1
            if module_name:
                module_summary_map[module_name]["completed"] += 1

        # Deadline state (time-aware)
        task["due_time"] = normalize_time_hhmm(task.get("due_time", "23:59"))
        due_dt = parse_due_datetime(task.get("due_date", ""), task.get("due_time", "23:59"))

        if task["status"] == "Completed":
            task["deadline_state"] = "completed"
        elif due_dt is not None and due_dt < now:
            task["deadline_state"] = "overdue"
            overdue_count += 1
        elif due_dt is not None and due_dt.date() == today:
            task["deadline_state"] = "due_today"
            due_soon_count += 1
        elif due_dt is not None and 0 < (due_dt.date() - today).days <= 3:
            task["deadline_state"] = "due_soon"
            due_soon_count += 1
        else:
            task["deadline_state"] = "normal"

        # Feed the "Attention" banner (only for tasks not completed)
        if task["status"] != "Completed":
            if task["deadline_state"] == "overdue":
                attention["overdue"].append(task)
            elif task["deadline_state"] == "due_today":
                attention["due_today"].append(task)
            elif task["deadline_state"] == "due_soon":
                attention["due_soon"].append(task)

        # Priority displayed in the UI (stored value + auto rules)
        eff_pr, was_auto = effective_priority(
            task.get("due_date", ""),
            task.get("due_time", "23:59"),
            task.get("status", "To do"),
            task.get("priority", "Medium"),
        )
        task["priority_stored"] = normalize_priority(task.get("priority", "Medium"))
        task["priority"] = eff_pr
        task["priority_auto"] = was_auto

        # Display formatting
        task["due_date_display"] = format_ymd_date(task.get("due_date", ""))
        task["deadline_display"] = f"{task['due_date_display']} {task['due_time']}"
        task["created_at_display"] = format_iso_datetime(task.get("created_at", ""))

        # Used by JS sorting/filters on the dashboard
        task["due_iso"] = f"{task.get('due_date','')}T{task.get('due_time','23:59')}"

        tasks.append(task)

    completion_percent = 0 if total_tasks == 0 else round((completed_count / total_tasks) * 100)
    unique_modules = sorted([m for m in module_set if m])

    # Build module progress list (completed vs total per module)
    module_summary = []
    for m in sorted(module_summary_map.keys()):
        total = module_summary_map[m]["total"]
        completed = module_summary_map[m]["completed"]
        percent = 0 if total == 0 else round((completed / total) * 100)
        module_summary.append({"module": m, "total": total, "completed": completed, "percent": percent})

    return render_template(
        "dashboard.html",
        tasks=tasks,
        unique_modules=unique_modules,
        module_summary=module_summary,
        attention=attention,
        stats={
            "total": total_tasks,
            "todo": todo_count,
            "in_progress": in_progress_count,
            "completed": completed_count,
            "overdue": overdue_count,
            "due_soon": due_soon_count,
            "completion_percent": completion_percent,
        },
    )


@app.route("/export/csv")
def export_csv():
    if not login_required():
        return redirect(url_for("login"))

    # Export current user's tasks in the same order as the dashboard
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
    writer.writerow(["Module", "Title", "Description", "Deadline", "Status", "Priority", "Created At"])

    for r in rows:
        due_time = normalize_time_hhmm(r["due_time"])
        pr_eff, _ = effective_priority(r["due_date"], due_time, r["status"], r["priority"])
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


@app.route("/task/<int:task_id>/edit", methods=["POST"])
def edit_task(task_id):
    if not login_required():
        return redirect(url_for("login"))

    module_name = request.form.get("module_name", "").strip()
    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date", "").strip()
    due_time = normalize_time_hhmm(request.form.get("due_time", "").strip())
    description = request.form.get("description", "").strip()
    priority = normalize_priority(request.form.get("priority", "Medium").strip())

    if not module_name or not title or not due_date:
        flash("Module, Title and Due Date are required to edit a task.")
        return redirect(url_for("dashboard"))

    if parse_due_datetime(due_date, due_time) is None:
        flash("Invalid deadline. Please use the date/time pickers.")
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

    if cur.rowcount == 0:
        flash("Task not found or you don't have permission to edit it.")
    else:
        flash("Task updated successfully!")

    return redirect(url_for("dashboard"))


@app.route("/task/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, session["user_id"]))
    conn.commit()
    conn.close()

    flash("Task deleted.")
    return redirect(url_for("dashboard"))


@app.route("/task/<int:task_id>/status", methods=["POST"])
def update_status(task_id):
    if not login_required():
        return redirect(url_for("login"))

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

    flash("Status updated.")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    
    app.run(debug=True)
