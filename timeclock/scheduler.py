"""Nightly close: auto clock-out, daily report, every-other-Sunday 2-week report.

Runs in a background thread and checks every 30 seconds. It records the last
business day it closed, so if the Pi was off or rebooting at closing time it
catches up when it comes back, and auto clock-outs are stamped with the
scheduled closing time rather than the catch-up time.
"""
import logging
import threading
import time as _time
from datetime import date, datetime, timedelta

import config
import db
import reports

log = logging.getLogger("scheduler")
TICK_SECONDS = 30
RETRY_MINUTES = 10
MAX_CATCHUP_DAYS = 14


def close_dt_for(business_day: date) -> datetime:
    ct = config.close_time()
    d = datetime.combine(business_day, ct)
    if ct.hour < 6:  # closing after midnight belongs to the previous business day
        d += timedelta(days=1)
    return d


def latest_due_business_day(now: datetime) -> date:
    d = now.date()
    while close_dt_for(d) > now:
        d -= timedelta(days=1)
    return d


def is_biweekly_day(d: date) -> bool:
    return d.weekday() == 6 and (d - config.biweekly_anchor()).days % 14 == 0


def run_close(business_day: date):
    close_dt = close_dt_for(business_day)
    name = config.get()["business_name"]

    n = db.auto_clock_out(close_dt)
    log.info("Close for %s: auto clocked out %d employee(s)", business_day, n)

    day_csv = reports.build_csv(close_dt - timedelta(days=1), close_dt)
    reports.queue_report(
        subject=f"{name} – Daily timesheet {business_day:%a %b %-d, %Y}",
        body=f"Attached is the clock-in report for {business_day:%A, %B %-d, %Y}.\n"
             f"Employees auto clocked out at close: {n}\n",
        filename=f"daily_{business_day.isoformat()}.csv",
        csv_text=day_csv,
    )

    if is_biweekly_day(business_day):
        start_day = business_day - timedelta(days=13)
        period_csv = reports.build_csv(close_dt - timedelta(days=14), close_dt)
        reports.queue_report(
            subject=f"{name} – 2-week timesheet {start_day:%b %-d} – {business_day:%b %-d, %Y}",
            body=f"Attached is the clock-in report for {start_day:%A, %B %-d} through "
                 f"{business_day:%A, %B %-d, %Y}, with total hours per employee at the bottom.\n",
            filename=f"biweekly_{start_day.isoformat()}_to_{business_day.isoformat()}.csv",
            csv_text=period_csv,
        )
        log.info("Queued 2-week report ending %s", business_day)


def send_outbox(force=False):
    for e in db.unsent_emails():
        if not force and e["last_attempt"]:
            if datetime.now() - db.parse(e["last_attempt"]) < timedelta(minutes=RETRY_MINUTES):
                continue
        try:
            reports.send_email(e["subject"], e["body"], e["filename"], e["csv_path"])
            db.mark_email(e["id"], ok=True)
            log.info("Emailed %s", e["filename"])
        except Exception as ex:
            db.mark_email(e["id"], ok=False, error=ex)
            log.warning("Email %s failed (will retry): %s", e["filename"], ex)


def tick(now=None):
    now = now or datetime.now()
    due = latest_due_business_day(now)
    last_raw = db.get_meta("last_close_day")

    if last_raw is None:
        # First run ever: don't send a report for the day before installation.
        db.set_meta("last_close_day", due.isoformat())
        log.info("Scheduler initialized; next close is %s", close_dt_for(due + timedelta(days=1)))
    else:
        d = date.fromisoformat(last_raw) + timedelta(days=1)
        d = max(d, due - timedelta(days=MAX_CATCHUP_DAYS - 1))
        while d <= due:
            run_close(d)
            db.set_meta("last_close_day", d.isoformat())
            d += timedelta(days=1)

    send_outbox()


def _loop():
    while True:
        try:
            tick()
        except Exception:
            log.exception("Scheduler tick failed")
        _time.sleep(TICK_SECONDS)


def start():
    threading.Thread(target=_loop, name="scheduler", daemon=True).start()
