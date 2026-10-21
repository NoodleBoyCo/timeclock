"""Loads and validates config.json."""
import json
import os
from datetime import date, time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

_cfg = None


def get():
    global _cfg
    if _cfg is None:
        if not os.path.exists(CONFIG_PATH):
            raise SystemExit(
                "config.json not found. Copy config.example.json to config.json and fill it in."
            )
        with open(CONFIG_PATH) as f:
            _cfg = json.load(f)
        # Validate early so mistakes show up at startup, not at 11pm.
        close_time()
        biweekly_anchor()
    return _cfg


def close_time() -> time:
    """Nightly auto clock-out time. Times before 06:00 are treated as
    'after midnight' for the previous business day (e.g. a bar closing at 02:00)."""
    raw = get()["auto_clockout_time"]
    try:
        h, m = (int(x) for x in raw.split(":"))
        return time(h, m)
    except Exception:
        raise SystemExit(f'auto_clockout_time must look like "23:00", got "{raw}"')


def biweekly_anchor() -> date:
    """Any Sunday on which a 2-week report should go out. Every 14 days from it
    (forwards or backwards) is also a report day."""
    raw = get()["biweekly_anchor_sunday"]
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f'biweekly_anchor_sunday must look like "2026-10-25", got "{raw}"')
    if d.weekday() != 6:
        raise SystemExit(f"biweekly_anchor_sunday ({raw}) is not a Sunday.")
    return d
