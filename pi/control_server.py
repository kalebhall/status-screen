import json
import os
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, Response

RUNTIME_DIR = "/home/pi/status-screen"
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
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")

app = Flask(__name__)

def now_utc():
    return datetime.now(timezone.utc)

def auth_ok(req) -> bool:
    return AUTH_TOKEN and req.headers.get("X-Auth-Token", "") == AUTH_TOKEN

def write_override(state: str, label: str, detail: str, minutes: int):
    until = now_utc() + timedelta(minutes=minutes)
    payload = {
        "state": state,
        "label": label,
        "detail": detail,
        "until": until.isoformat().replace("+00:00", "Z"),
    }
    os.makedirs(os.path.dirname(OVERRIDE_JSON_PATH), exist_ok=True)
    tmp = OVERRIDE_JSON_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, OVERRIDE_JSON_PATH)
    return payload

def clear_override():
    try:
        os.remove(OVERRIDE_JSON_PATH)
    except FileNotFoundError:
        pass

@app.get("/control")
def control_page():
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
    <input id="token" size="50" placeholder="Paste AUTH_TOKEN here">
  </div>

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

  const r = await fetch('/api/override', {
    method: 'POST',
    headers: { 'Content-Type':'application/json', 'X-Auth-Token': token },
    body: JSON.stringify({ state, label, detail, minutes })
  });

  document.getElementById('out').textContent = await r.text();
}

async function clearOverride() {
  const token = document.getElementById('token').value;
  const r = await fetch('/api/clear', { method: 'POST', headers: { 'X-Auth-Token': token }});
  document.getElementById('out').textContent = await r.text();
}
</script>
</body>
</html>
"""
    return Response(html, mimetype="text/html")

@app.post("/api/override")
def api_override():
    if not auth_ok(request):
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
    return jsonify(write_override(state, label, detail, minutes))

@app.post("/api/clear")
def api_clear():
    if not auth_ok(request):
        return jsonify({"error": "unauthorized"}), 401
    clear_override()
    return jsonify({"ok": True})

@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
