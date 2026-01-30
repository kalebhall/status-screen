import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME_DIR = os.environ.get("STATUS_SCREEN_DIR", "/home/pi/status-screen")

STATUS_JSON_PATH = os.path.join(RUNTIME_DIR, "status.json")
STATUS_MULTI_JSON_PATH = os.path.join(RUNTIME_DIR, "status-multi.json")
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
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))
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

def parse_env_list(key: str) -> list[str]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]

def build_groups() -> list[dict]:
    ics_urls = parse_env_list("ICS_URLS")
    if not ics_urls and ICS_URL:
        ics_urls = [ICS_URL]
    display_names = parse_env_list("DISPLAY_NAMES")
    if not display_names:
        single_name = os.environ.get("DISPLAY_NAME", "").strip()
        if single_name:
            display_names = [single_name]
    auth_tokens = parse_env_list("AUTH_TOKENS")
    if not auth_tokens:
        single_token = os.environ.get("AUTH_TOKEN", "").strip()
        if single_token:
            auth_tokens = [single_token]
    work_hour_starts = parse_env_list("WORK_HOURS_STARTS")
    work_hour_ends = parse_env_list("WORK_HOURS_ENDS")
    work_hour_days = parse_env_list("WORK_HOURS_DAYS_LIST")

    group_count = len(ics_urls) if ics_urls else 1
    if len(display_names) > group_count or len(auth_tokens) > group_count:
        logging.warning(
            "Extra DISPLAY_NAMES/AUTH_TOKENS provided; only the first %s entries will be used.",
            group_count,
        )

    base_cache = ICS_CACHE_PATH
    cache_root, cache_ext = os.path.splitext(base_cache)
    groups = []
    for index in range(group_count):
        display_name = (
            display_names[index]
            if index < len(display_names)
            else f"Group {index + 1}"
        )
        if group_count == 1:
            cache_path = base_cache
            status_path = STATUS_JSON_PATH
            override_path = OVERRIDE_JSON_PATH
        else:
            safe_name = "".join(
                ch.lower() if ch.isalnum() else "-"
                for ch in display_name.strip()
            ).strip("-")
            name_suffix = f"-{safe_name}" if safe_name else ""
            suffix = f"-{index + 1}{name_suffix}"
            cache_path = f"{cache_root}{suffix}{cache_ext}" if cache_ext else f"{base_cache}{suffix}"
            status_path = (
                STATUS_JSON_PATH
                if index == 0
                else os.path.join(RUNTIME_DIR, f"status-{index + 1}.json")
            )
            override_path = os.path.join(RUNTIME_DIR, f"override-{index + 1}.json")
        start_value = work_hour_starts[index] if index < len(work_hour_starts) else WORK_HOURS_START
        end_value = work_hour_ends[index] if index < len(work_hour_ends) else WORK_HOURS_END
        days_value = work_hour_days[index] if index < len(work_hour_days) else WORK_HOURS_DAYS
        groups.append(
            {
                "index": index,
                "ics_url": ics_urls[index] if index < len(ics_urls) else "",
                "display_name": display_name,
                "auth_token": auth_tokens[index] if index < len(auth_tokens) else "",
                "cache_path": cache_path,
                "status_path": status_path,
                "override_path": override_path,
                "work_hours": build_work_hours_config(start_value, end_value, days_value, index),
            }
        )
    return groups

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

def now_local(local_tz) -> datetime:
    return datetime.now(local_tz)

def get_local_tz():
    from dateutil import tz

    local_tz = resolve_tzinfo(TIMEZONE_NAME)
    if local_tz is None:
        logging.error("Invalid TIMEZONE_NAME=%s", TIMEZONE_NAME)
    else:
        logging.debug("Resolved TIMEZONE_NAME=%s to tzinfo=%s", TIMEZONE_NAME, local_tz)
    return local_tz

WINDOWS_TZ_MAP = {
    "pacific standard time": "America/Los_Angeles",
    "mountain standard time": "America/Denver",
    "central standard time": "America/Chicago",
    "eastern standard time": "America/New_York",
    "utc": "UTC",
    "pacific time (us & canada)": "America/Los_Angeles",
    "mountain time (us & canada)": "America/Denver",
    "central time (us & canada)": "America/Chicago",
    "eastern time (us & canada)": "America/New_York",
}

def normalize_tz_key(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.strip().lower()
    if lowered.startswith("(utc") and ")" in lowered:
        lowered = lowered.split(")", 1)[1].strip()
    return lowered

def map_windows_tz(value: str | None) -> str | None:
    key = normalize_tz_key(value)
    if not key:
        return None
    return WINDOWS_TZ_MAP.get(key)

@lru_cache(maxsize=64)
def resolve_tzinfo(name: str | None):
    from dateutil import tz

    if not name:
        return None
    tzinfo = tz.gettz(name)
    if tzinfo is not None:
        return tzinfo
    mapped = map_windows_tz(name)
    if mapped:
        return tz.gettz(mapped)
    return None

def coerce_event_timezone(dt: datetime, local_tz) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local_tz)
    tzname = dt.tzinfo.tzname(dt)
    mapped = map_windows_tz(tzname)
    if mapped:
        mapped_tz = resolve_tzinfo(mapped)
        if mapped_tz:
            return dt.replace(tzinfo=mapped_tz)
    return dt

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

def build_work_hours_config(
    work_hours_start: str,
    work_hours_end: str,
    work_hours_days: str,
    group_index: int | None = None,
) -> dict | None:
    if not work_hours_start or not work_hours_end:
        return None
    start = parse_hhmm(work_hours_start)
    end = parse_hhmm(work_hours_end)
    if not start or not end:
        suffix = f" (group {group_index + 1})" if group_index is not None else ""
        logging.warning(
            "Invalid work hours config%s: start=%s end=%s",
            suffix,
            work_hours_start,
            work_hours_end,
        )
        return None
    days = parse_days(work_hours_days)
    if not days:
        suffix = f" (group {group_index + 1})" if group_index is not None else ""
        logging.warning("Invalid work hours days%s: %s", suffix, work_hours_days)
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

def working_hours_status(config: dict | None, now: datetime | None = None) -> dict | None:
    if not config:
        return None
    local_tz = get_local_tz()
    if local_tz is None:
        return None
    current_local = (now or now_local(local_tz)).astimezone(local_tz)
    if is_within_work_hours(current_local, config):
        return None
    next_start_local = next_work_start(current_local, config)
    until = None
    if next_start_local:
        until = next_start_local.isoformat()
    return {
        "state": "ooo",
        "label": "OUT OF OFFICE",
        "detail": format_work_hours_detail(config),
        "until": until,
        "source": "working_hours",
    }

def parse_iso(dt_str: str, local_tz) -> datetime | None:
    try:
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)
        return dt.astimezone(local_tz)
    except Exception:
        return None

def write_status(
    state: str,
    label: str,
    detail: str = "",
    source: str = "",
    until: str | None = None,
    next_event_at: str | None = None,
    name: str | None = None,
    status_path: str = STATUS_JSON_PATH,
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
    if name:
        payload["name"] = name
    logging.debug("Writing status: %s", payload)
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    tmp = status_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, status_path)
    return payload

def is_ooo(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in OOO_KEYWORDS)

def should_ignore(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in IGNORE_KEYWORDS)

def load_override(override_path: str) -> dict | None:
    if not os.path.exists(override_path):
        return None
    try:
        with open(override_path, "r") as f:
            o = json.load(f)
        local_tz = get_local_tz()
        if local_tz is None:
            return None
        until = parse_iso(o.get("until", ""), local_tz)
        if until is None:
            return None
        if now_local(local_tz) > until:
            return None
        return o
    except Exception:
        logging.exception("Failed to load override from %s", override_path)
        return None

def fetch_ics_text(ics_url: str, cache_path: str) -> str:
    import requests
    from urllib.parse import parse_qs, urlparse, urlunparse

    cached_text = None
    cache_age = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached_text = f.read()
            cache_age = time.time() - os.path.getmtime(cache_path)
        except Exception:
            logging.exception("Failed to read ICS cache %s", cache_path)
            cached_text = None
            cache_age = None

    if ICS_REFRESH_SECONDS < 0:
        logging.warning("ICS_REFRESH_SECONDS=%s is invalid; forcing refresh", ICS_REFRESH_SECONDS)
    if cached_text and cache_age is not None and cache_age < max(ICS_REFRESH_SECONDS, 0):
        logging.debug("Using cached ICS file (%s seconds old).", int(cache_age))
        return cached_text

    if not ics_url:
        if cached_text:
            logging.warning("ICS_URL is not set; using cached ICS")
            return cached_text
        raise RuntimeError("ICS_URL is not set")
    fetch_url = ics_url
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
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, cache_path)
        return text
    except Exception:
        logging.exception("Failed to fetch ICS from %s", fetch_url)
        if cached_text:
            logging.warning("Using cached ICS after fetch failure.")
            return cached_text
        raise

def iter_event_extra_items(event):
    for container in (getattr(event, "extra", None), getattr(event, "_unused", None)):
        if not container:
            continue
        if isinstance(container, dict):
            for value in container.values():
                yield value
            continue
        for item in container:
            yield item

def extract_event_tzid(event, prop_name: str) -> str | None:
    target = prop_name.upper()
    for item in iter_event_extra_items(event):
        name = None
        value_obj = item
        if isinstance(item, tuple) and len(item) >= 2:
            name = item[0]
            value_obj = item[1]
        name = name or getattr(value_obj, "name", None) or getattr(value_obj, "_name", None)
        if not name:
            continue
        if str(name).strip().upper() != target:
            continue
        params = getattr(value_obj, "params", None) or getattr(value_obj, "_params", None)
        if not params:
            continue
        tzid = params.get("TZID") or params.get("tzid")
        if isinstance(tzid, (list, tuple)):
            tzid = tzid[0] if tzid else None
        if tzid:
            return str(tzid)
    return None

def apply_event_tzid(dt: datetime, event, prop_name: str) -> datetime:
    if dt is None:
        return dt
    tzid = extract_event_tzid(event, prop_name)
    if not tzid:
        return dt
    tzinfo = resolve_tzinfo(tzid)
    if tzinfo is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tzinfo)
    if dt.utcoffset() == timedelta(0) and tzid.upper() not in {"UTC", "Etc/UTC"}:
        return dt.replace(tzinfo=tzinfo)
    return dt

def event_times_to_local(event, local_tz) -> tuple[datetime, datetime]:
    start = event.begin.datetime
    end = event.end.datetime
    if start is None or end is None:
        raise ValueError("Event start/end missing")
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug(
            "Event %s raw start=%s tz=%s end=%s tz=%s",
            getattr(event, "name", None),
            start,
            getattr(start, "tzinfo", None),
            end,
            getattr(end, "tzinfo", None),
        )
    start = apply_event_tzid(start, event, "DTSTART")
    end = apply_event_tzid(end, event, "DTEND")
    start = coerce_event_timezone(start, local_tz)
    end = coerce_event_timezone(end, local_tz)
    start_local = start.astimezone(local_tz)
    end_local = end.astimezone(local_tz)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug(
            "Event %s local start=%s tz=%s end=%s tz=%s",
            getattr(event, "name", None),
            start_local,
            getattr(start_local, "tzinfo", None),
            end_local,
            getattr(end_local, "tzinfo", None),
        )
    return start_local, end_local

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
    now = now_local(local_tz)
    try:
        cal = Calendar(ics_text)
    except Exception:
        logging.exception("Failed to parse ICS calendar")
        return None

    try:
        timeline_events = list(cal.timeline.at(now))
    except Exception:
        logging.debug("Failed to read timeline events for current time", exc_info=True)
        timeline_events = None

    active = []
    for e in (timeline_events if timeline_events is not None else cal.events):
        name = e.name or "Meeting"
        if should_ignore(name):
            continue
        try:
            start_local, end_local = event_times_to_local(e, local_tz)
        except Exception:
            logging.debug("Failed to parse event times for %s", name)
            continue

        if start_local <= now < end_local:
            busy_status = microsoft_busy_status(e) if USE_MS_BUSY_STATUS else None
            event_is_ooo = is_ooo(name) or busy_status == "ooo"
            if ALLDAY_ONLY_COUNTS_IF_OOO and is_all_day_event(e) and not event_is_ooo:
                continue
            if busy_status == "free":
                continue
            active.append((start_local, end_local, name, busy_status))

    if not active:
        return None
    active.sort(key=lambda x: x[0])
    return {"name": active[0][2], "end": active[0][1], "busy_status": active[0][3]}

def next_calendar_event(ics_text: str) -> dict | None:
    from ics import Calendar

    local_tz = get_local_tz()
    if local_tz is None:
        return None
    now = now_local(local_tz)
    try:
        cal = Calendar(ics_text)
    except Exception:
        logging.exception("Failed to parse ICS calendar")
        return None
    try:
        timeline_events = cal.timeline.start_after(now)
    except Exception:
        logging.debug("Failed to read timeline events after current time", exc_info=True)
        timeline_events = None

    if timeline_events is not None:
        for e in timeline_events:
            name = e.name or "Meeting"
            if should_ignore(name):
                continue
            try:
                start_local, end_local = event_times_to_local(e, local_tz)
            except Exception:
                logging.debug("Failed to parse event times for %s", name)
                continue
            if start_local <= now:
                continue
            busy_status = microsoft_busy_status(e) if USE_MS_BUSY_STATUS else None
            event_is_ooo = is_ooo(name) or busy_status == "ooo"
            if ALLDAY_ONLY_COUNTS_IF_OOO and is_all_day_event(e) and not event_is_ooo:
                continue
            if busy_status == "free":
                continue
            return {"name": name, "start": start_local}
        return None

    upcoming = []

    for e in cal.events:
        name = e.name or "Meeting"
        if should_ignore(name):
            continue
        try:
            start_local, end_local = event_times_to_local(e, local_tz)
        except Exception:
            logging.debug("Failed to parse event times for %s", name)
            continue
        if start_local <= now:
            continue
        busy_status = microsoft_busy_status(e) if USE_MS_BUSY_STATUS else None
        event_is_ooo = is_ooo(name) or busy_status == "ooo"
        if ALLDAY_ONLY_COUNTS_IF_OOO and is_all_day_event(e) and not event_is_ooo:
            continue
        if busy_status == "free":
            continue
        upcoming.append((start_local, end_local, name))

    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[0])
    return {"name": upcoming[0][2], "start": upcoming[0][0]}

def same_local_day(first: datetime, second: datetime) -> bool:
    return first.date() == second.date()

def next_event_for_display(
    ics_text: str,
    work_hours: dict | None,
) -> str | None:
    local_tz = get_local_tz()
    if local_tz is None:
        return None
    now = now_local(local_tz)
    next_ev = next_calendar_event(ics_text)
    if not next_ev:
        return None
    start_local = next_ev["start"]
    if start_local.tzinfo is None:
        start_local = start_local.replace(tzinfo=local_tz)
    start_local = start_local.astimezone(local_tz)
    if not same_local_day(start_local, now):
        return None
    if work_hours and not is_within_work_hours(start_local, work_hours):
        return None
    return start_local.isoformat()

def resolve_and_write(group: dict) -> dict:
    display_name = group.get("display_name", "")
    next_event_at = None
    error_detail = None
    try:
        ics_text = fetch_ics_text(group["ics_url"], group["cache_path"])
        ev = current_calendar_event(ics_text)
        next_event_at = next_event_for_display(ics_text, group.get("work_hours"))
        if ev:
            name = ev["name"]
            detail = name if SHOW_EVENT_DETAILS else ""
            until = ev["end"].isoformat()
            busy_status = ev.get("busy_status")
            if USE_MS_BUSY_STATUS and busy_status == "ooo":
                return write_status(
                    "ooo",
                    "OUT OF OFFICE",
                    detail,
                    source="calendar",
                    until=until,
                    next_event_at=next_event_at,
                    name=display_name,
                    status_path=group["status_path"],
                )
            elif is_ooo(name):
                return write_status(
                    "ooo",
                    "OUT OF OFFICE",
                    detail,
                    source="calendar",
                    until=until,
                    next_event_at=next_event_at,
                    name=display_name,
                    status_path=group["status_path"],
                )
            else:
                return write_status(
                    "meeting",
                    "IN A MEETING",
                    detail,
                    source="calendar",
                    until=until,
                    next_event_at=next_event_at,
                    name=display_name,
                    status_path=group["status_path"],
                )
    except Exception as ex:
        logging.exception("Failed to resolve calendar status")
        error_detail = f"{type(ex).__name__}: {str(ex)}"[:100]

    override = load_override(group["override_path"])
    if override:
        return write_status(
            override.get("state", "busy"),
            override.get("label", "BUSY"),
            override.get("detail", ""),
            source="override",
            until=override.get("until"),
            next_event_at=next_event_at,
            name=display_name,
            status_path=group["status_path"],
        )

    work_status = working_hours_status(group.get("work_hours"))
    if work_status:
        return write_status(
            work_status["state"],
            work_status["label"],
            work_status["detail"],
            source=work_status["source"],
            until=work_status.get("until"),
            next_event_at=next_event_at,
            name=display_name,
            status_path=group["status_path"],
        )

    if error_detail:
        return write_status(
            "error",
            "STATUS ERROR",
            error_detail,
            source="error",
            name=display_name,
            status_path=group["status_path"],
        )

    return write_status(
        "available",
        "AVAILABLE",
        "",
        source="default",
        next_event_at=next_event_at,
        name=display_name,
        status_path=group["status_path"],
    )

def main():
    groups = build_groups()
    for group in groups:
        write_status(
            "available",
            "AVAILABLE",
            "",
            source="boot",
            name=group.get("display_name", ""),
            status_path=group["status_path"],
        )
    while True:
        people = []
        for group in groups:
            payload = resolve_and_write(group)
            payload["name"] = group["display_name"]
            people.append(payload)
        os.makedirs(os.path.dirname(STATUS_MULTI_JSON_PATH), exist_ok=True)
        tmp = STATUS_MULTI_JSON_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"generated": datetime.utcnow().isoformat(timespec="seconds") + "Z", "people": people}, f)
        os.replace(tmp, STATUS_MULTI_JSON_PATH)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
