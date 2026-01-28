import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME_DIR = os.environ.get("STATUS_SCREEN_DIR", "/home/pi/status-screen")

STATUS_JSON_PATH = os.path.join(RUNTIME_DIR, "status.json")
OVERRIDE_JSON_PATH = os.path.join(RUNTIME_DIR, "override.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

def load_dotenv(dotenv_path: str):
    if not os.path.exists(dotenv_path):
        return
    try:
        with open(dotenv_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except OSError as exc:
        logging.warning("Failed to load dotenv file %s: %s", dotenv_path, exc)

# Load secrets/config from /home/pi/status-screen/.env (runtime location)
load_dotenv(os.path.join(RUNTIME_DIR, ".env"))

def parse_env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    logging.warning("Unknown %s=%s, defaulting to %s", key, raw, default)
    return default

def parse_env_falsey(raw: str | None) -> bool:
    if raw is None:
        return False
    value = raw.strip().lower()
    return value in {"0", "false", "no", "n", "off"}

def configure_logging():
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = logging.INFO
    if level_name in logging._nameToLevel:
        level = logging._nameToLevel[level_name]
    else:
        logging.warning("Unknown LOG_LEVEL=%s, defaulting to INFO", level_name)
    logging.getLogger().setLevel(level)

configure_logging()

ICS_URL = os.environ.get("ICS_URL", "")
TIMEZONE_NAME = os.environ.get("TIMEZONE_NAME", "America/Los_Angeles")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
ICS_REFRESH_SECONDS = int(
    os.environ.get("ICS_REFRESH_SECONDS", os.environ.get("ICS_REFRESH", "300"))
)
ICS_CACHE_PATH = os.environ.get(
    "ICS_CACHE_PATH", os.path.join(RUNTIME_DIR, "calendar.ics")
)
ICS_CA_BUNDLE = (
    os.environ.get("ICS_CA_BUNDLE")
    or os.environ.get("REQUESTS_CA_BUNDLE")
    or os.environ.get("SSL_CERT_FILE")
)
WORK_HOURS_START = os.environ.get("WORK_HOURS_START", "")
WORK_HOURS_END = os.environ.get("WORK_HOURS_END", "")
WORK_HOURS_DAYS = os.environ.get("WORK_HOURS_DAYS", "")

OOO_KEYWORDS = ["out of office", "ooo", "vacation", "leave", "pto", "sick"]
IGNORE_KEYWORDS = ["cancelled", "canceled"]
ALLDAY_ONLY_COUNTS_IF_OOO = parse_env_bool("ALLDAY_ONLY_COUNTS_IF_OOO", True)
USE_MS_BUSY_STATUS = parse_env_bool("USE_MS_BUSY_STATUS", False)
SHOW_EVENT_DETAILS = parse_env_bool("SHOW_EVENT_DETAILS", True)

DAY_NAME_TO_INDEX = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def get_local_tz():
    from dateutil import tz

    local_tz = tz.gettz(TIMEZONE_NAME)
    if local_tz is None:
        logging.error("Invalid TIMEZONE_NAME=%s", TIMEZONE_NAME)
    return local_tz

def parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute

def parse_day_token(token: str) -> int | None:
    t = (token or "").strip().lower()
    if not t:
        return None
    if t in DAY_NAME_TO_INDEX:
        return DAY_NAME_TO_INDEX[t]
    try:
        day_index = int(t)
    except ValueError:
        return None
    if 0 <= day_index <= 6:
        return day_index
    return None

def expand_day_range(start_day: int, end_day: int) -> set[int]:
    if start_day <= end_day:
        return set(range(start_day, end_day + 1))
    return set(range(start_day, 7)) | set(range(0, end_day + 1))

def parse_days(value: str) -> set[int]:
    if not value:
        return set(range(0, 5))
    days: set[int] = set()
    for raw_token in value.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start_day = parse_day_token(start_raw)
            end_day = parse_day_token(end_raw)
            if start_day is None or end_day is None:
                continue
            days |= expand_day_range(start_day, end_day)
            continue
        day_index = parse_day_token(token)
        if day_index is not None:
            days.add(day_index)
    return days or set(range(0, 5))

def build_work_hours_config() -> dict | None:
    if not WORK_HOURS_START or not WORK_HOURS_END:
        return None
    start = parse_hhmm(WORK_HOURS_START)
    end = parse_hhmm(WORK_HOURS_END)
    if not start or not end:
        logging.warning(
            "Invalid work hours config: start=%s end=%s",
            WORK_HOURS_START,
            WORK_HOURS_END,
        )
        return None
    days = parse_days(WORK_HOURS_DAYS)
    if not days:
        logging.warning("Invalid work hours days: %s", WORK_HOURS_DAYS)
        return None
    start_minutes = start[0] * 60 + start[1]
    end_minutes = end[0] * 60 + end[1]
    return {
        "start": start,
        "end": end,
        "days": days,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "overnight": end_minutes <= start_minutes,
    }

WORK_HOURS = build_work_hours_config()

def is_within_work_hours(now_local: datetime, config: dict) -> bool:
    minutes = now_local.hour * 60 + now_local.minute
    day = now_local.weekday()
    if not config["overnight"]:
        return day in config["days"] and config["start_minutes"] <= minutes < config["end_minutes"]
    prev_day = (day - 1) % 7
    in_today_window = day in config["days"] and minutes >= config["start_minutes"]
    in_prev_day_window = prev_day in config["days"] and minutes < config["end_minutes"]
    return in_today_window or in_prev_day_window

def next_work_start(now_local: datetime, config: dict) -> datetime | None:
    if not config["days"]:
        return None
    start_hour, start_minute = config["start"]
    for day_offset in range(0, 14):
        candidate_date = (now_local + timedelta(days=day_offset)).date()
        candidate_dt = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            start_hour,
            start_minute,
            tzinfo=now_local.tzinfo,
        )
        if candidate_dt.weekday() not in config["days"]:
            continue
        if candidate_dt <= now_local:
            continue
        return candidate_dt
    return None

def format_work_hours_detail(config: dict) -> str:
    start_hour, start_minute = config["start"]
    end_hour, end_minute = config["end"]
    return f"Outside working hours ({start_hour:02d}:{start_minute:02d}-{end_hour:02d}:{end_minute:02d})"

def working_hours_status(now: datetime | None = None) -> dict | None:
    config = WORK_HOURS
    if not config:
        return None
    local_tz = get_local_tz()
    if local_tz is None:
        return None
    current_local = (now or now_utc()).astimezone(local_tz)
    if is_within_work_hours(current_local, config):
        return None
    next_start_local = next_work_start(current_local, config)
    until = None
    if next_start_local:
        until = next_start_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "state": "ooo",
        "label": "OUT OF OFFICE",
        "detail": format_work_hours_detail(config),
        "until": until,
        "source": "working_hours",
    }

def parse_iso(dt_str: str) -> datetime | None:
    try:
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def write_status(
    state: str,
    label: str,
    detail: str = "",
    source: str = "",
    until: str | None = None,
    next_event_at: str | None = None,
):
    payload = {
        "state": state,
        "label": label,
        "detail": detail,
        "source": source,
        "time_zone": TIMEZONE_NAME,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    if until:
        payload["until"] = until
    if next_event_at:
        payload["next_event_at"] = next_event_at
    logging.debug("Writing status: %s", payload)
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
        logging.exception("Failed to load override from %s", OVERRIDE_JSON_PATH)
        return None

def fetch_ics_text() -> str:
    import requests
    from urllib.parse import parse_qs, urlparse, urlunparse

    cached_text = None
    cache_age = None
    if os.path.exists(ICS_CACHE_PATH):
        try:
            with open(ICS_CACHE_PATH, "r") as f:
                cached_text = f.read()
            cache_age = time.time() - os.path.getmtime(ICS_CACHE_PATH)
        except Exception:
            logging.exception("Failed to read ICS cache %s", ICS_CACHE_PATH)
            cached_text = None
            cache_age = None

    if ICS_REFRESH_SECONDS < 0:
        logging.warning("ICS_REFRESH_SECONDS=%s is invalid; forcing refresh", ICS_REFRESH_SECONDS)
    if cached_text and cache_age is not None and cache_age < max(ICS_REFRESH_SECONDS, 0):
        logging.debug("Using cached ICS file (%s seconds old).", int(cache_age))
        return cached_text

    if not ICS_URL:
        if cached_text:
            logging.warning("ICS_URL is not set; using cached ICS")
            return cached_text
        raise RuntimeError("ICS_URL is not set")
    fetch_url = ICS_URL
    parsed = urlparse(fetch_url)
    if parsed.scheme in {"webcal", "webcals"}:
        fetch_url = urlunparse(parsed._replace(scheme="https"))
    elif "outlook.live.com" in parsed.netloc and "rru=addsubscription" in parsed.query:
        params = parse_qs(parsed.query)
        outlook_url = params.get("url", [None])[0]
        if outlook_url:
            outlook_parsed = urlparse(outlook_url)
            if outlook_parsed.scheme in {"webcal", "webcals"}:
                outlook_url = urlunparse(outlook_parsed._replace(scheme="https"))
            fetch_url = outlook_url
    headers = {"User-Agent": "StatusScreenPi/1.0"}
    verify = True
    if parse_env_falsey(ICS_CA_BUNDLE):
        verify = False
        logging.warning("TLS verification disabled via ICS_CA_BUNDLE/SSL_CERT_FILE/REQUESTS_CA_BUNDLE.")
    elif ICS_CA_BUNDLE:
        if not os.path.exists(ICS_CA_BUNDLE):
            logging.warning("ICS_CA_BUNDLE does not exist: %s (using system defaults)", ICS_CA_BUNDLE)
        else:
            verify = ICS_CA_BUNDLE
    try:
        logging.debug("Fetching ICS URL: %s", fetch_url)
        r = requests.get(
            fetch_url,
            headers=headers,
            timeout=100,
            allow_redirects=True,
            verify=verify,
        )
        r.raise_for_status()
        text = r.text
        if "BEGIN:VCALENDAR" not in text[:2000]:
            raise RuntimeError("ICS fetch did not return VCALENDAR")
        os.makedirs(os.path.dirname(ICS_CACHE_PATH), exist_ok=True)
        tmp = ICS_CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, ICS_CACHE_PATH)
        return text
    except Exception:
        logging.exception("Failed to fetch ICS from %s", fetch_url)
        if cached_text:
            logging.warning("Using cached ICS after fetch failure.")
            return cached_text
        raise

def event_times_to_utc(ev_begin, ev_end, local_tz) -> tuple[datetime, datetime]:
    start = ev_begin.datetime
    end = ev_end.datetime
    if start is None or end is None:
        raise ValueError("Event start/end missing")
    if start.tzinfo is None:
        start = start.replace(tzinfo=local_tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=local_tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

def extract_event_extra_values(event):
    for container in (getattr(event, "extra", None), getattr(event, "_unused", None)):
        if not container:
            continue
        if isinstance(container, dict):
            for key, value in container.items():
                yield key, value
            continue
        for item in container:
            if isinstance(item, tuple) and len(item) >= 2:
                yield item[0], item[1]
                continue
            name = getattr(item, "name", None) or getattr(item, "_name", None)
            value = getattr(item, "value", None) or getattr(item, "_value", None)
            if name is not None:
                yield name, value

def microsoft_busy_status(event) -> str | None:
    for name, value in extract_event_extra_values(event):
        if not name:
            continue
        if str(name).strip().upper() != "X-MICROSOFT-CDO-BUSYSTATUS":
            continue
        raw_value = "" if value is None else str(value)
        status = raw_value.strip().lower()
        if status in {"free", "busy"}:
            return status
        if status in {"oof", "out of office", "outofoffice"}:
            return "ooo"
        if status:
            return status
    return None

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
    from ics import Calendar

    local_tz = get_local_tz()
    if local_tz is None:
        return None
    now = now_utc()
    try:
        cal = Calendar(ics_text)
    except Exception:
        logging.exception("Failed to parse ICS calendar")
        return None

    active = []
    for e in cal.events:
        name = e.name or "Meeting"
        if should_ignore(name):
            continue
        try:
            start_utc, end_utc = event_times_to_utc(e.begin, e.end, local_tz)
        except Exception:
            logging.debug("Failed to parse event times for %s", name)
            continue

        if start_utc <= now < end_utc:
            busy_status = microsoft_busy_status(e) if USE_MS_BUSY_STATUS else None
            event_is_ooo = is_ooo(name) or busy_status == "ooo"
            if ALLDAY_ONLY_COUNTS_IF_OOO and is_all_day_event(e) and not event_is_ooo:
                continue
            if busy_status == "free":
                continue
            active.append((start_utc, end_utc, name, busy_status))

    if not active:
        return None
    active.sort(key=lambda x: x[0])
    return {"name": active[0][2], "end": active[0][1], "busy_status": active[0][3]}

def next_calendar_event(ics_text: str) -> dict | None:
    from ics import Calendar

    local_tz = get_local_tz()
    if local_tz is None:
        return None
    now = now_utc()
    try:
        cal = Calendar(ics_text)
    except Exception:
        logging.exception("Failed to parse ICS calendar")
        return None
    upcoming = []

    for e in cal.events:
        name = e.name or "Meeting"
        if should_ignore(name):
            continue
        try:
            start_utc, end_utc = event_times_to_utc(e.begin, e.end, local_tz)
        except Exception:
            logging.debug("Failed to parse event times for %s", name)
            continue
        if start_utc <= now:
            continue
        busy_status = microsoft_busy_status(e) if USE_MS_BUSY_STATUS else None
        event_is_ooo = is_ooo(name) or busy_status == "ooo"
        if ALLDAY_ONLY_COUNTS_IF_OOO and is_all_day_event(e) and not event_is_ooo:
            continue
        if busy_status == "free":
            continue
        upcoming.append((start_utc, end_utc, name))

    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[0])
    return {"name": upcoming[0][2], "start": upcoming[0][0]}

def resolve_and_write():
    next_event_at = None
    error_detail = None
    try:
        ics_text = fetch_ics_text()
        ev = current_calendar_event(ics_text)
        next_ev = next_calendar_event(ics_text)
        if next_ev:
            next_event_at = next_ev["start"].isoformat().replace("+00:00", "Z")
        if ev:
            name = ev["name"]
            detail = name if SHOW_EVENT_DETAILS else ""
            until = ev["end"].isoformat().replace("+00:00", "Z")
            busy_status = ev.get("busy_status")
            if USE_MS_BUSY_STATUS and busy_status == "ooo":
                write_status(
                    "ooo",
                    "OUT OF OFFICE",
                    detail,
                    source="calendar",
                    until=until,
                    next_event_at=next_event_at,
                )
            elif is_ooo(name):
                write_status(
                    "ooo",
                    "OUT OF OFFICE",
                    detail,
                    source="calendar",
                    until=until,
                    next_event_at=next_event_at,
                )
            else:
                write_status(
                    "meeting",
                    "IN A MEETING",
                    detail,
                    source="calendar",
                    until=until,
                    next_event_at=next_event_at,
                )
            return
    except Exception as ex:
        logging.exception("Failed to resolve calendar status")
        error_detail = f"{type(ex).__name__}: {str(ex)}"[:100]

    override = load_override()
    if override:
        write_status(
            override.get("state", "busy"),
            override.get("label", "BUSY"),
            override.get("detail", ""),
            source="override",
            until=override.get("until"),
            next_event_at=next_event_at,
        )
        return

    work_status = working_hours_status()
    if work_status:
        write_status(
            work_status["state"],
            work_status["label"],
            work_status["detail"],
            source=work_status["source"],
            until=work_status.get("until"),
            next_event_at=next_event_at,
        )
        return

    if error_detail:
        write_status("error", "STATUS ERROR", error_detail, source="error")
        return

    write_status("available", "AVAILABLE", "", source="default", next_event_at=next_event_at)

def main():
    write_status("available", "AVAILABLE", "", source="boot")
    while True:
        resolve_and_write()
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
