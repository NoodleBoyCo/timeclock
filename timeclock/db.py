"""SQLite storage for employees, shifts, and the email outbox.

Timestamps are stored as local time strings "YYYY-MM-DDTHH:MM:SS", which sort
correctly as text. Set the Pi's timezone correctly (see README).
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import BASE_DIR

DB_PATH = os.path.join(BASE_DIR, "timeclock.db")
FMT = "%Y-%m-%dT%H:%M:%S"
DEBOUNCE_SECONDS = 60  # ignore a second punch from the same person within a minute

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    pin     TEXT NOT NULL,
    active  INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_pin ON employees(pin) WHERE active = 1;

CREATE TABLE IF NOT EXISTS shifts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id      INTEGER NOT NULL REFERENCES employees(id),
    clock_in         TEXT NOT NULL,
    clock_out        TEXT,
    auto_clocked_out INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_shifts_clock_in ON shifts(clock_in);

CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    filename     TEXT NOT NULL,
    csv_path     TEXT NOT NULL,
    created      TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_attempt TEXT,
    last_error   TEXT,
    sent_at      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def ts(d: datetime) -> str:
    return d.strftime(FMT)


def parse(s: str) -> datetime:
    return datetime.strptime(s, FMT)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode = WAL")


# ---------- meta ----------

def get_meta(key):
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------- employees ----------

def add_employee(name, pin):
    with connect() as conn:
        cur = conn.execute("INSERT INTO employees(name, pin) VALUES(?, ?)", (name, pin))
        return cur.lastrowid


def list_employees(include_inactive=False):
    q = "SELECT * FROM employees"
    if not include_inactive:
        q += " WHERE active = 1"
    with connect() as conn:
        return conn.execute(q + " ORDER BY name").fetchall()


def deactivate_employee(emp_id):
    with connect() as conn:
        return conn.execute("UPDATE employees SET active = 0 WHERE id = ?", (emp_id,)).rowcount


def set_pin(emp_id, pin):
    with connect() as conn:
        return conn.execute(
            "UPDATE employees SET pin = ? WHERE id = ? AND active = 1", (pin, emp_id)
        ).rowcount


# ---------- punching ----------

def punch(pin, now=None):
    """Toggle clock in/out for the employee with this PIN."""
    now = (now or datetime.now()).replace(microsecond=0)
    with connect() as conn:
        emp = conn.execute(
            "SELECT * FROM employees WHERE pin = ? AND active = 1", (pin,)
        ).fetchone()
        if not emp:
            return {"ok": False, "reason": "bad_pin", "message": "PIN not recognized."}

        first = emp["name"].split()[0]
        last = conn.execute(
            "SELECT * FROM shifts WHERE employee_id = ? ORDER BY id DESC LIMIT 1",
            (emp["id"],),
        ).fetchone()

        if last:
            last_event = parse(last["clock_out"] or last["clock_in"])
            if (now - last_event).total_seconds() < DEBOUNCE_SECONDS:
                did = "out" if last["clock_out"] else "in"
                return {
                    "ok": False,
                    "reason": "too_soon",
                    "message": f"{first}, you already clocked {did} at "
                               f"{last_event.strftime('%-I:%M %p')}.",
                }

        if last and last["clock_out"] is None:
            conn.execute("UPDATE shifts SET clock_out = ? WHERE id = ?", (ts(now), last["id"]))
            hours = (now - parse(last["clock_in"])).total_seconds() / 3600
            return {
                "ok": True,
                "action": "out",
                "name": emp["name"],
                "time": now.strftime("%-I:%M %p"),
                "hours": round(hours, 2),
                "message": f"Goodbye, {first}! Clocked out at {now.strftime('%-I:%M %p')} "
                           f"({hours:.2f} hrs).",
            }

        conn.execute(
            "INSERT INTO shifts(employee_id, clock_in) VALUES(?, ?)", (emp["id"], ts(now))
        )
        return {
            "ok": True,
            "action": "in",
            "name": emp["name"],
            "time": now.strftime("%-I:%M %p"),
            "message": f"Welcome, {first}! Clocked in at {now.strftime('%-I:%M %p')}.",
        }


def auto_clock_out(close_dt: datetime):
    """Close every open shift that started at or before close_dt, stamping it with
    close_dt (not 'now'), so a late catch-up never inflates hours."""
    with connect() as conn:
        return conn.execute(
            "UPDATE shifts SET clock_out = ?, auto_clocked_out = 1 "
            "WHERE clock_out IS NULL AND clock_in <= ?",
            (ts(close_dt), ts(close_dt)),
        ).rowcount


def clocked_in_now():
    with connect() as conn:
        return conn.execute(
            "SELECT e.name, s.clock_in FROM shifts s JOIN employees e ON e.id = s.employee_id "
            "WHERE s.clock_out IS NULL ORDER BY s.clock_in"
        ).fetchall()


def shifts_between(start: datetime, end: datetime):
    """Shifts whose clock-in falls in (start, end]."""
    with connect() as conn:
        return conn.execute(
            "SELECT s.*, e.name FROM shifts s JOIN employees e ON e.id = s.employee_id "
            "WHERE s.clock_in > ? AND s.clock_in <= ? ORDER BY e.name, s.clock_in",
            (ts(start), ts(end)),
        ).fetchall()


# ---------- outbox ----------

def queue_email(subject, body, filename, csv_path):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO outbox(subject, body, filename, csv_path, created) VALUES(?,?,?,?,?)",
            (subject, body, filename, csv_path, ts(datetime.now())),
        ).lastrowid


def unsent_emails():
    with connect() as conn:
        return conn.execute("SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY id").fetchall()


def mark_email(email_id, ok, error=None):
    now = ts(datetime.now())
    with connect() as conn:
        if ok:
            conn.execute(
                "UPDATE outbox SET sent_at = ?, attempts = attempts + 1, last_attempt = ? "
                "WHERE id = ?", (now, now, email_id))
        else:
            conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_attempt = ?, last_error = ? "
                "WHERE id = ?", (now, str(error)[:500], email_id))
