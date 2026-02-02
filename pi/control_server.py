import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, Response

RUNTIME_DIR = os.environ.get("STATUS_SCREEN_DIR", "/home/pi/status-screen")
OVERRIDE_JSON_PATH = os.path.join(RUNTIME_DIR, "override.json")
PEOPLE_JSON_PATH = os.path.join(RUNTIME_DIR, "people.json")

def parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in ("\"", "'"):
        quote = value[0]
        end_index = value.find(quote, 1)
        if end_index != -1:
            return value[1:end_index]
        return value[1:]
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            value = value[:index]
            break
    return value.strip()

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
            v = parse_env_value(v)
            os.environ.setdefault(k, v)

load_dotenv(os.path.join(RUNTIME_DIR, ".env"))

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

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

def load_people_config() -> list[dict]:
    if not os.path.exists(PEOPLE_JSON_PATH):
        return []
    try:
        with open(PEOPLE_JSON_PATH, "r") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        people = payload.get("people")
        if isinstance(people, list):
            return [item for item in people if isinstance(item, dict)]
    return []

def build_people_from_env() -> list[dict]:
    ics_urls = parse_env_list("ICS_URLS")
    display_names = parse_env_list("DISPLAY_NAMES")
    auth_tokens = parse_env_list("AUTH_TOKENS")
    work_hour_starts = parse_env_list("WORK_HOURS_STARTS")
    work_hour_ends = parse_env_list("WORK_HOURS_ENDS")
    work_hour_days = parse_env_list("WORK_HOURS_DAYS_LIST")
    if not any(
        [ics_urls, display_names, auth_tokens, work_hour_starts, work_hour_ends, work_hour_days]
    ):
        return []
    group_count = len(ics_urls) if ics_urls else 1
    people = []
    for index in range(group_count):
        people.append(
            {
                "ics_url": ics_urls[index] if index < len(ics_urls) else "",
                "display_name": display_names[index] if index < len(display_names) else "",
                "auth_code": auth_tokens[index] if index < len(auth_tokens) else "",
                "work_hours_start": work_hour_starts[index] if index < len(work_hour_starts) else "",
                "work_hours_end": work_hour_ends[index] if index < len(work_hour_ends) else "",
                "work_hour_days_list": work_hour_days[index] if index < len(work_hour_days) else "",
            }
        )
    return people

def get_people_config() -> list[dict]:
    people = load_people_config()
    if people:
        return people
    return build_people_from_env()

def person_auth_code(entry: dict) -> str:
    return (entry.get("auth_code") or entry.get("auth_token") or "").strip()

app = Flask(__name__)

def add_people_cors_headers(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

def now_utc():
    return datetime.now(timezone.utc)

def resolve_token_index(req) -> int | None:
    people = get_people_config()
    auth_tokens = [person_auth_code(entry) for entry in people]
    token = req.headers.get("X-Auth-Token", "")
    if not token:
        return None
    try:
        return auth_tokens.index(token)
    except ValueError:
        return None

def group_display_names() -> list[str]:
    people = get_people_config()
    group_count = len(people) if people else 1
    return [
        (people[index].get("display_name") if index < len(people) else "")
        or f"Group {index + 1}"
        for index in range(group_count)
    ]

def override_path_for(index: int) -> str:
    group_count = len(get_people_config()) or 1
    if group_count <= 1:
        return OVERRIDE_JSON_PATH
    return os.path.join(RUNTIME_DIR, f"override-{index + 1}.json")

def resolve_group_index(token_index: int, data: dict) -> int:
    group_count = len(get_people_config()) or 1
    if group_count <= 1:
        return 0
    auth_tokens = [person_auth_code(entry) for entry in get_people_config()]
    if len(auth_tokens) > 1:
        return min(token_index, group_count - 1)
    requested = data.get("group_index", data.get("group"))
    if requested is None:
        return 0
    try:
        requested_index = int(requested)
    except (TypeError, ValueError):
        return 0
    if 0 <= requested_index < group_count:
        return requested_index
    return 0

def write_override(state: str, label: str, detail: str, minutes: int, override_path: str):
    until = now_utc() + timedelta(minutes=minutes)
    payload = {
        "state": state,
        "label": label,
        "detail": detail,
        "until": until.isoformat().replace("+00:00", "Z"),
    }
    os.makedirs(os.path.dirname(override_path), exist_ok=True)
    tmp = override_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, override_path)
    return payload

def clear_override(override_path: str):
    try:
        os.remove(override_path)
    except FileNotFoundError:
        pass

@app.get("/control")
def control_page():
    group_options = "".join(
        f'<option value="{index}">{name}</option>'
        for index, name in enumerate(group_display_names())
    )
    group_count = len(get_people_config()) or 1
    group_selector = ""
    if group_count > 1:
        group_selector = f"""
  <div class="row">
    <label>Person: </label>
    <select id="group">{group_options}</select>
  </div>
"""
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Status Control</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; }
    input { font-size: 16px; padding: 6px; }
    button { font-size: 16px; padding: 10px 14px; margin: 6px 6px 6px 0; }
    .row { margin: 10px 0; }
    .small { opacity: 0.75; }
    code { background:#f2f2f2; padding:2px 6px; }
  </style>
</head>
<body>
  <h2>Status Control</h2>
  <div class="row small">Uses header <code>X-Auth-Token</code> to protect the API.</div>

  <div class="row">
    <label>Token: </label>
    <input id="token" size="50" placeholder="Paste AUTH_TOKENS entry here">
  </div>

__GROUP_SELECTOR__
  <div class="row">
    <label>Detail: </label>
    <input id="detail" size="40" placeholder="e.g., Unscheduled call">
    <label>Minutes: </label>
    <input id="minutes" type="number" value="30" min="1" max="480">
  </div>

  <div class="row">
    <button onclick="setOverride('available','AVAILABLE')">AVAILABLE (override)</button>
    <button onclick="setOverride('busy','BUSY')">BUSY</button>
    <button onclick="setOverride('ooo','OUT OF OFFICE')">OUT OF OFFICE</button>
    <button onclick="clearOverride()">CLEAR OVERRIDE</button>
  </div>

  <div class="row small">Calendar always wins during scheduled events.</div>

  <pre id="out"></pre>

<script>
async function setOverride(state,label) {
  const token = document.getElementById('token').value;
  const detail = document.getElementById('detail').value;
  const minutes = parseInt(document.getElementById('minutes').value || '30', 10);
  const groupEl = document.getElementById('group');
  const group_index = groupEl ? parseInt(groupEl.value, 10) : 0;

  const r = await fetch('/api/override', {
    method: 'POST',
    headers: { 'Content-Type':'application/json', 'X-Auth-Token': token },
    body: JSON.stringify({ state, label, detail, minutes, group_index })
  });

  document.getElementById('out').textContent = await r.text();
}

async function clearOverride() {
  const token = document.getElementById('token').value;
  const groupEl = document.getElementById('group');
  const group_index = groupEl ? parseInt(groupEl.value, 10) : 0;
  const r = await fetch('/api/clear', {
    method: 'POST',
    headers: { 'Content-Type':'application/json', 'X-Auth-Token': token },
    body: JSON.stringify({ group_index })
  });
  document.getElementById('out').textContent = await r.text();
}
</script>
</body>
</html>
"""
    html = html.replace("__GROUP_SELECTOR__", group_selector)
    return Response(html, mimetype="text/html")

@app.post("/api/override")
def api_override():
    token_index = resolve_token_index(request)
    if token_index is None:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    state = data.get("state", "busy")
    label = data.get("label", "BUSY")
    detail = data.get("detail", "")
    raw_minutes = data.get("minutes", 30)
    try:
        minutes_value = int(raw_minutes)
    except (TypeError, ValueError):
        minutes_value = 30
    minutes = max(1, min(minutes_value, 24 * 60))
    group_index = resolve_group_index(token_index, data)
    override_path = override_path_for(group_index)
    return jsonify(write_override(state, label, detail, minutes, override_path))

@app.post("/api/clear")
def api_clear():
    token_index = resolve_token_index(request)
    if token_index is None:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    group_index = resolve_group_index(token_index, data)
    override_path = override_path_for(group_index)
    clear_override(override_path)
    return jsonify({"ok": True})

@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})

@app.get("/api/admin-config")
def api_admin_config():
    if not ADMIN_PASSWORD:
        return jsonify({"enabled": False})
    digest = hashlib.sha256(ADMIN_PASSWORD.encode("utf-8")).hexdigest()
    return jsonify({"enabled": True, "password_hash": digest})

@app.route("/api/people", methods=["GET", "POST", "OPTIONS"])
def api_people():
    if request.method == "OPTIONS":
        return add_people_cors_headers(Response(status=204))
    if request.method == "GET":
        people = get_people_config()
        response = jsonify({"people": people})
        return add_people_cors_headers(response)
    data = request.get_json(force=True, silent=True)
    if isinstance(data, dict):
        people = data.get("people", [])
    else:
        people = data
    if not isinstance(people, list):
        response = jsonify({"error": "invalid payload"})
        return add_people_cors_headers(response), 400
    cleaned = [entry for entry in people if isinstance(entry, dict)]
    payload = {
        "people": cleaned,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    os.makedirs(os.path.dirname(PEOPLE_JSON_PATH), exist_ok=True)
    tmp = PEOPLE_JSON_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, PEOPLE_JSON_PATH)
    response = jsonify(payload)
    return add_people_cors_headers(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
