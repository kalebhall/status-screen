import json
import os
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, Response

RUNTIME_DIR = os.environ.get("STATUS_SCREEN_DIR", "/home/pi/status-screen")
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

load_dotenv(os.path.join(RUNTIME_DIR, ".env"))

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

AUTH_TOKENS = parse_env_list("AUTH_TOKENS")
ICS_URLS = parse_env_list("ICS_URLS")
DISPLAY_NAMES = parse_env_list("DISPLAY_NAMES")
GROUP_COUNT = len(ICS_URLS) if ICS_URLS else 1

app = Flask(__name__)

def now_utc():
    return datetime.now(timezone.utc)

def resolve_token_index(req) -> int | None:
    token = req.headers.get("X-Auth-Token", "")
    if not token:
        return None
    try:
        return AUTH_TOKENS.index(token)
    except ValueError:
        return None

def group_display_names() -> list[str]:
    return [
        DISPLAY_NAMES[index] if index < len(DISPLAY_NAMES) else f"Group {index + 1}"
        for index in range(GROUP_COUNT)
    ]

def override_path_for(index: int) -> str:
    if GROUP_COUNT <= 1:
        return OVERRIDE_JSON_PATH
    return os.path.join(RUNTIME_DIR, f"override-{index + 1}.json")

def resolve_group_index(token_index: int, data: dict) -> int:
    if GROUP_COUNT <= 1:
        return 0
    if len(AUTH_TOKENS) > 1:
        return min(token_index, GROUP_COUNT - 1)
    requested = data.get("group_index", data.get("group"))
    if requested is None:
        return 0
    try:
        requested_index = int(requested)
    except (TypeError, ValueError):
        return 0
    if 0 <= requested_index < GROUP_COUNT:
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
    group_selector = ""
    if GROUP_COUNT > 1:
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
