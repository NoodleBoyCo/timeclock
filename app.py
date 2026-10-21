"""Employee time clock – web server. Run with:  python app.py"""
import logging
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory
from waitress import serve

import config
import db
import scheduler

app = Flask(__name__, static_folder="static")

# Simple brute-force protection: 5 wrong PINs within a minute locks the keypad for 30s.
_lock = threading.Lock()
_fails = []
_locked_until = 0.0


@app.get("/")
def index():
    resp = send_from_directory(app.static_folder, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/info")
def info():
    return jsonify(business_name=config.get()["business_name"])


@app.post("/api/punch")
def punch():
    global _locked_until
    now = time.time()
    with _lock:
        if now < _locked_until:
            return jsonify(ok=False, message="Too many wrong PINs. Please wait 30 seconds."), 429

    pin = str((request.get_json(silent=True) or {}).get("pin", ""))
    if len(pin) != 4 or not pin.isdigit():
        return jsonify(ok=False, message="Enter a 4-digit PIN."), 400

    result = db.punch(pin)
    if result.get("reason") == "bad_pin":
        with _lock:
            _fails[:] = [t for t in _fails if now - t < 60] + [now]
            if len(_fails) >= 5:
                _locked_until = now + 30
                _fails.clear()
    elif result["ok"]:
        logging.getLogger("punch").info("%s clocked %s", result["name"], result["action"])
    return jsonify(result)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(config.BASE_DIR, "timeclock.log")),
        ],
    )
    cfg = config.get()
    db.init()
    scheduler.start()
    port = int(cfg.get("port", 8080))
    logging.info("Time clock running on port %d", port)
    serve(app, host="0.0.0.0", port=port, threads=4)


if __name__ == "__main__":
    main()
