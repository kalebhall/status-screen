# ESP32 e-paper client

Use this sample sketch to render the `/status.json` payload on an ESP32-S3 with a
black/white e-paper panel (such as the 5.79" CrowPanel). The sketch polls the
status API on a 30-second interval and redraws the screen when data changes.

## API setup

The status screen service already writes a `status.json` file on the Pi and the
nginx snippet in this repo serves it from `/status.json`. Confirm the snippet is
included in your Pi's nginx `server {}` block and points at the correct runtime
path (`STATUS_SCREEN_DIR`).

If you want the Pi to refresh the payload every 30 seconds, ensure your `.env`
contains:

```bash
POLL_SECONDS="30"
```

Restart the service after changes:

```bash
sudo systemctl restart status-from-ics.service
```

## Sketch setup

1. Install the Arduino libraries:
   - `GxEPD2`
   - `Adafruit GFX Library`
   - `ArduinoJson`
2. Open `status_screen_epaper/status_screen_epaper.ino`.
3. Set the Wi-Fi credentials, `STATUS_URL`, and `TARGET_PERSON` (matches the `name`
   field in the JSON payload).
4. Select the correct GxEPD2 panel driver and pin wiring for your board.
5. Upload to the ESP32-S3.

## JSON payload format

`/status.json` is a JSON object with a `people` array. Each entry includes:

```json
{
  "name": "Alex",
  "state": "available",
  "label": "AVAILABLE",
  "detail": "",
  "until": "2024-01-01T12:00:00Z",
  "next_event_at": "2024-01-01T13:00:00Z"
}
```

The sample sketch uses `name`, `label`, and `detail` for display.
It also uses `state` to choose an icon to draw next to the status.
