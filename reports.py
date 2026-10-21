"""CSV report building and email sending."""
import csv
import io
import os
import smtplib
from collections import OrderedDict
from datetime import datetime
from email.message import EmailMessage

import config
import db

REPORTS_DIR = os.path.join(config.BASE_DIR, "reports")


def build_csv(start: datetime, end: datetime) -> str:
    """CSV of every shift clocked in during (start, end], plus per-employee totals."""
    rows = db.shifts_between(start, end)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Employee", "Date", "Clock In", "Clock Out", "Hours", "Note"])

    totals = OrderedDict()
    for r in rows:
        cin = db.parse(r["clock_in"])
        cout = db.parse(r["clock_out"]) if r["clock_out"] else None
        hours = round((cout - cin).total_seconds() / 3600, 2) if cout else None
        note = "Auto clocked out" if r["auto_clocked_out"] else ("Still clocked in" if not cout else "")
        w.writerow([
            r["name"],
            cin.strftime("%Y-%m-%d"),
            cin.strftime("%I:%M %p"),
            cout.strftime("%Y-%m-%d %I:%M %p") if cout and cout.date() != cin.date()
            else (cout.strftime("%I:%M %p") if cout else ""),
            f"{hours:.2f}" if hours is not None else "",
            note,
        ])
        totals[r["name"]] = totals.get(r["name"], 0) + (hours or 0)

    w.writerow([])
    w.writerow(["TOTALS"])
    w.writerow(["Employee", "Total Hours"])
    for name, h in sorted(totals.items()):
        w.writerow([name, f"{h:.2f}"])
    if not totals:
        w.writerow(["(no shifts)", "0.00"])
    return out.getvalue()


def save_csv(filename, text) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "w", newline="") as f:
        f.write(text)
    return path


def send_email(subject, body, filename, csv_path):
    cfg = config.get()["email"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(body)
    with open(csv_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="text", subtype="csv", filename=filename)

    if cfg.get("use_ssl"):
        server = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
    else:
        server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
        server.starttls()
    with server:
        server.login(cfg["username"], cfg["password"])
        server.send_message(msg)


def queue_report(subject, body, filename, csv_text):
    """Save the CSV to reports/ and put it in the outbox. The scheduler sends it
    and keeps retrying if the network or mail server is down."""
    path = save_csv(filename, csv_text)
    return db.queue_email(subject, body, filename, path)
