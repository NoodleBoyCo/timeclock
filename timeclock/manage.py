"""Admin commands. Run on the Pi from the timeclock folder, e.g.:

  python manage.py add "Jane Smith" 1234
  python manage.py list
  python manage.py remove 3
  python manage.py setpin 3 5678
  python manage.py status
  python manage.py test-email
  python manage.py report daily 2026-10-18 [--email]
  python manage.py report biweekly 2026-10-25 [--email]
  python manage.py resend
"""
import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta

import config
import db
import reports
import scheduler


def valid_pin(pin):
    if len(pin) != 4 or not pin.isdigit():
        sys.exit("PIN must be exactly 4 digits.")
    return pin


def cmd_add(a):
    try:
        emp_id = db.add_employee(a.name.strip(), valid_pin(a.pin))
    except sqlite3.IntegrityError:
        sys.exit("That PIN is already used by another active employee.")
    print(f"Added {a.name} (id {emp_id}).")


def cmd_list(a):
    rows = db.list_employees(include_inactive=a.all)
    if not rows:
        print("No employees yet. Add one with: python manage.py add \"Name\" 1234")
    for r in rows:
        status = "" if r["active"] else "  (removed)"
        print(f"{r['id']:>4}  {r['pin']}  {r['name']}{status}")


def cmd_remove(a):
    print("Removed." if db.deactivate_employee(a.id) else "No employee with that id.")


def cmd_setpin(a):
    try:
        ok = db.set_pin(a.id, valid_pin(a.pin))
    except sqlite3.IntegrityError:
        sys.exit("That PIN is already used by another active employee.")
    print("PIN updated." if ok else "No active employee with that id.")


def cmd_status(a):
    rows = db.clocked_in_now()
    if not rows:
        print("Nobody is clocked in.")
    for r in rows:
        print(f"{r['name']:<25} in since {db.parse(r['clock_in']):%a %-I:%M %p}")
    print(f"\nNext auto clock-out: {scheduler.close_dt_for(scheduler.latest_due_business_day(datetime.now()) + timedelta(days=1)):%a %b %-d %-I:%M %p}")
    pending = db.unsent_emails()
    if pending:
        print(f"\n{len(pending)} email(s) waiting to send:")
        for e in pending:
            print(f"  {e['filename']}  attempts={e['attempts']}  last error: {e['last_error']}")


def cmd_test_email(a):
    path = reports.save_csv("test.csv", "Employee,Hours\nTest,1.00\n")
    reports.send_email("Time clock test email", "If you got this, email is set up correctly.",
                       "test.csv", path)
    print("Sent to:", ", ".join(config.get()["email"]["to"]))


def cmd_report(a):
    day = date.fromisoformat(a.day) if a.day else scheduler.latest_due_business_day(datetime.now())
    end = scheduler.close_dt_for(day)
    days = 1 if a.kind == "daily" else 14
    text = reports.build_csv(end - timedelta(days=days), end)
    fname = (f"daily_{day}.csv" if days == 1
             else f"biweekly_{day - timedelta(days=13)}_to_{day}.csv")
    path = reports.save_csv(fname, text)
    print(text)
    print("Saved to", path)
    if a.email:
        reports.send_email(f"{config.get()['business_name']} – {a.kind} timesheet ({day})",
                           "Report attached.", fname, path)
        print("Emailed.")


def cmd_resend(a):
    scheduler.send_outbox(force=True)
    left = len(db.unsent_emails())
    print("All queued emails sent." if not left else f"{left} still failing; see `status`.")


def main():
    config.get()
    db.init()
    p = argparse.ArgumentParser(description="Time clock admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="Add an employee"); s.add_argument("name"); s.add_argument("pin"); s.set_defaults(f=cmd_add)
    s = sub.add_parser("list", help="List employees"); s.add_argument("--all", action="store_true"); s.set_defaults(f=cmd_list)
    s = sub.add_parser("remove", help="Remove an employee (history is kept)"); s.add_argument("id", type=int); s.set_defaults(f=cmd_remove)
    s = sub.add_parser("setpin", help="Change a PIN"); s.add_argument("id", type=int); s.add_argument("pin"); s.set_defaults(f=cmd_setpin)
    s = sub.add_parser("status", help="Who is clocked in, pending emails"); s.set_defaults(f=cmd_status)
    s = sub.add_parser("test-email", help="Send a test email"); s.set_defaults(f=cmd_test_email)
    s = sub.add_parser("report", help="Build a report manually")
    s.add_argument("kind", choices=["daily", "biweekly"])
    s.add_argument("day", nargs="?", help="Business day (YYYY-MM-DD); biweekly = last day of period")
    s.add_argument("--email", action="store_true"); s.set_defaults(f=cmd_report)
    s = sub.add_parser("resend", help="Retry any unsent report emails now"); s.set_defaults(f=cmd_resend)

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
