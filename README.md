# Employee Time Clock

A PIN-pad time clock that runs on a Raspberry Pi Zero (2) W and is shown on an iPad.

- Employees enter a 4-digit PIN; the same PIN toggles clock in / clock out.
- At the configured closing time everyone still clocked in is auto clocked out
  (stamped at closing time, flagged "Auto clocked out" in the CSV).
- Right after close, that day's CSV is emailed to the boss.
- Every other Sunday at close, a 2-week CSV (with total hours per employee) is emailed too.
- If the Pi was off at closing time it catches up when it boots; if email fails
  (Wi-Fi down, etc.) it retries every 10 minutes. CSVs are also kept in `reports/`.

## 1. Set up the Pi

Flash **Raspberry Pi OS Lite** with Raspberry Pi Imager. In the Imager settings set
the hostname to `timeclock`, username `pi`, your Wi-Fi, and enable SSH.

SSH in (`ssh pi@timeclock.local`) and run:

```bash
sudo timedatectl set-timezone America/Los_Angeles   # use your timezone
sudo apt update && sudo apt install -y python3-venv
```

## 2. Install the app

Copy this folder to `/home/pi/timeclock` (e.g. `scp -r timeclock pi@timeclock.local:~`), then:

```bash
cd ~/timeclock
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.example.json config.json
nano config.json
```

### config.json

| Key | Meaning |
|---|---|
| `business_name` | Shown on the keypad and in email subjects |
| `auto_clockout_time` | 24-hour closing time, e.g. `"23:00"`. Times before 06:00 (e.g. `"01:30"`) count as the night before. |
| `biweekly_anchor_sunday` | Any Sunday a 2-week report should go out. Reports go every 14 days from it. |
| `email.*` | SMTP login for the **sending** account; `to` is the boss's address (a list, so you can add yourself). |

**Gmail:** use a dedicated Gmail account, turn on 2-Step Verification, then create an
*App Password* (Google Account → Security → App passwords) and paste the 16 characters
into `password`. Your normal password won't work. Outlook/365: `smtp.office365.com`, port 587.

Test it:

```bash
venv/bin/python manage.py test-email
```

## 3. Add employees

```bash
venv/bin/python manage.py add "Jane Smith" 1234
venv/bin/python manage.py list
venv/bin/python manage.py setpin 3 5678    # by id from `list`
venv/bin/python manage.py remove 3          # keeps their history
```

## 4. Run it on boot

```bash
sudo cp timeclock.service /etc/systemd/system/
sudo systemctl enable --now timeclock
sudo systemctl status timeclock
```

Logs: `journalctl -u timeclock -f` or `timeclock.log` in the folder.

Give the Pi a **reserved IP** in the router's DHCP settings so the address never changes.

## 5. Set up the iPad

1. Open Safari to `http://timeclock.local:8080` (or `http://<pi-ip>:8080`).
2. Share → **Add to Home Screen**, then open it from the home-screen icon (full screen, no address bar).
3. Settings → Display & Brightness → Auto-Lock → **Never**.
4. Settings → Accessibility → **Guided Access** → on, set a passcode. Open the app and
   triple-click the side/home button to lock the iPad to it.
5. Keep the iPad plugged in.

## Day-to-day commands

```bash
venv/bin/python manage.py status                         # who's in, next close, stuck emails
venv/bin/python manage.py report daily 2026-10-18        # rebuild a day's CSV
venv/bin/python manage.py report biweekly 2026-10-25 --email   # rebuild & email a pay period
venv/bin/python manage.py resend                         # retry unsent emails now
```

To fix a forgotten punch, open the database with `sqlite3 timeclock.db`
(`sudo apt install sqlite3`) and edit the `shifts` table, then re-run `report ... --email`.

## Backups

All data lives in `timeclock.db`. Copy it off the Pi occasionally:
`scp pi@timeclock.local:~/timeclock/timeclock.db .`

## Notes

- The keypad page has no password: anyone on the business network can open it. Don't
  expose port 8080 to the internet.
- 5 wrong PINs in a minute locks the keypad for 30 seconds.
- A second punch from the same person within 60 seconds is ignored (stops double taps).
