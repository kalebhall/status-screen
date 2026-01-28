import json
import logging
import os
from datetime import datetime

RUNTIME_DIR = os.environ.get("STATUS_SCREEN_DIR", "/home/pi/status-screen")
CONFIG_PATH = os.environ.get(
    "STATUS_MULTI_CONFIG",
    os.path.join(RUNTIME_DIR, "status-multi-config.json"),
)
DEFAULT_OUTPUT_PATH = os.path.join(RUNTIME_DIR, "status-multi.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def load_person_status(entry: dict) -> dict:
    name = entry.get("name", "Unknown")
    status_path = entry.get("status_json")
    if not status_path:
        return {
            "name": name,
            "state": "error",
            "label": "STATUS ERROR",
            "detail": "Missing status_json in config",
        }
    if not os.path.exists(status_path):
        return {
            "name": name,
            "state": "error",
            "label": "STATUS ERROR",
            "detail": f"Missing status file: {status_path}",
        }
    try:
        with open(status_path, "r") as f:
            data = json.load(f)
    except Exception as exc:
        return {
            "name": name,
            "state": "error",
            "label": "STATUS ERROR",
            "detail": f"Failed to load status: {exc}",
        }
    merged = {
        "name": name,
        "state": data.get("state", "error"),
        "label": data.get("label", "UNKNOWN"),
        "detail": data.get("detail", ""),
        "source": data.get("source", ""),
        "time_zone": data.get("time_zone"),
        "updated": data.get("updated"),
    }
    if data.get("until"):
        merged["until"] = data.get("until")
    if data.get("next_event_at"):
        merged["next_event_at"] = data.get("next_event_at")
    return merged


def write_output(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def main() -> int:
    if not os.path.exists(CONFIG_PATH):
        logging.error("Missing config file at %s", CONFIG_PATH)
        return 1
    config = load_config(CONFIG_PATH)
    people = config.get("people", [])
    output_path = config.get("output_json", DEFAULT_OUTPUT_PATH)
    statuses = [load_person_status(entry) for entry in people]
    payload = {
        "generated": utc_now_iso(),
        "people": statuses,
    }
    write_output(output_path, payload)
    logging.info("Wrote %s with %s entries", output_path, len(statuses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
