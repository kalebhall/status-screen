import json
import os
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME_DIR = os.environ.get("STATUS_SCREEN_DIR", "/home/pi/status-screen")

STATUS_JSON_PATH = os.path.join(RUNTIME_DIR, "status.json")
OVERRIDE_JSON_PATH = os.path.join(RUNTIME_DIR, "override.json")

def load_dotenv(dotenv_path: str):
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

# Load secrets/config from /home/pi/status-screen/.env (runtime location)
load_dotenv(os.path.join(RUNTIME_DIR, ".env"))

ICS_URL = os.environ.get("ICS_URL", "")
TIMEZONE_NAME = os.environ.get("TIMEZONE_NAME", "America/Los_Angeles")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

OOO_KEYWORDS = ["out of office", "ooo", "vacation", "leave", "pto", "sick"]
IGNORE_KEYWORDS = ["cancelled", "canceled"]
ALLDAY_ONLY_COUNTS_IF_OOO = True

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso(dt_str: str) -> datetime | None:
    try:
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def write_status(state: str, label: str, detail: str = "", source: str = ""):
    payload = {
        "state": state,
        "label": label,
        "detail": detail,
        "source": source,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(STATUS_JSON_PATH), exist_ok=True)
    tmp = STATUS_JSON_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, STATUS_JSON_PATH)

def is_ooo(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in OOO_KEYWORDS)

def should_ignore(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in IGNORE_KEYWORDS)

def load_override() -> dict | None:
    if not os.path.exists(OVERRIDE_JSON_PATH):
        return None
    try:
        with open(OVERRIDE_JSON_PATH, "r") as f:
            o = json.load(f)
        until = parse_iso(o.get("until", ""))
        if until is None:
            return None
        if now_utc() > until:
            return None
        return o
    except Exception:
        return None

def fetch_ics_text() -> str:
    import requests

    if not ICS_URL:
        raise RuntimeError("ICS_URL is not set")
    headers = {"User-Agent": "StatusScreenPi/1.0"}
    r = requests.get(ICS_URL, headers=headers, timeout=25, allow_redirects=True)
    r.raise_for_status()
    text = r.text
    if "BEGIN:VCALENDAR" not in text[:2000]:
        raise RuntimeError("ICS fetch did not return VCALENDAR")
    return text

def event_times_to_utc(ev_begin, ev_end, local_tz) -> tuple[datetime, datetime]:
    start = ev_begin.datetime
    end = ev_end.datetime
    if start.tzinfo is None:
        start = start.replace(tzinfo=local_tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=local_tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

def is_all_day_event(e) -> bool:
    try:
        start = e.begin.datetime
        end = e.end.datetime
        if start.hour == 0 and start.minute == 0 and start.second == 0:
            dur = end - start
            return dur >= timedelta(hours=23)
    except Exception:
        pass
    return False

def current_calendar_event(ics_text: str) -> dict | None:
    from dateutil import tz
    from ics import Calendar

    local_tz = tz.gettz(TIMEZONE_NAME)
    now = now_utc()
    cal = Calendar(ics_text)

    active = []
    for e in cal.events:
        name = e.name or "Meeting"
        if should_ignore(name):
            continue
        try:
            start_utc, end_utc = event_times_to_utc(e.begin, e.end, local_tz)
        except Exception:
            continue

        if start_utc <= now <= end_utc:
            if ALLDAY_ONLY_COUNTS_IF_OOO and is_all_day_event(e) and not is_ooo(name):
                continue
            active.append((start_utc, name))

    if not active:
        return None
    active.sort(key=lambda x: x[0])
    return {"name": active[0][1]}

def resolve_and_write():
    try:
        ics_text = fetch_ics_text()
        ev = current_calendar_event(ics_text)
        if ev:
            name = ev["name"]
            if is_ooo(name):
                write_status("ooo", "OUT OF OFFICE", name, source="calendar")
            else:
                write_status("meeting", "IN A MEETING", name, source="calendar")
            return

        override = load_override()
        if override:
            write_status(
                override.get("state", "busy"),
                override.get("label", "BUSY"),
                override.get("detail", ""),
                source="override",
            )
            return

        write_status("available", "AVAILABLE", "", source="default")
    except Exception as ex:
        write_status("error", "STATUS ERROR", str(ex)[:100], source="error")

def main():
    write_status("available", "AVAILABLE", "", source="boot")
    while True:
        resolve_and_write()
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
