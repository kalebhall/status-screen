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

## Battery life / deep sleep

Yes — you can run this on battery and use deep sleep during off-hours.

In `status_screen_epaper/status_screen_epaper.ino`, these settings control it:

- `ENABLE_OFF_HOURS_DEEP_SLEEP`: enables timer wake + deep sleep.
- `ACTIVE_HOURS_START` / `ACTIVE_HOURS_END`: local hours where the device stays awake and polls every 30s.
- `OFF_HOURS_WAKE_SECONDS`: wake interval during off-hours (poll once, update if needed, then sleep again).
- `ENABLE_REBOOT_BUTTONS`: set to `false` to skip button polling and save a bit more power.
- `BATTERY_CUTOFF_MV`: when battery is below this threshold and not charging, the sketch shows a low-battery warning and enters a long deep sleep.
- `BATTERY_ADC_PIN`: ADC input used for battery-voltage measurement. Set `-1` to disable when battery sense is not wired.
- `BATTERY_CHARGE_DETECT_PIN`: set this to your charger status GPIO (`CHRG`) to show charging state on-screen (default `-1` disables charging detection).

The sketch uses the server `generated` timestamp (formatted in Pacific time) to decide whether current time is in off-hours.
When charging detection is wired, the battery widget shows `CHG xx%` plus a lightning icon while charging.



### Battery voltage reading verification (from your schematic)

From the schematic you posted, the battery/charger section does not clearly show a battery-sense
divider routed into an ESP32 ADC pin. That means the previous default (`GPIO4`) is likely not a
valid battery measurement source on this board revision, which explains a stuck battery %.

In this repo we now default `BATTERY_ADC_PIN` to `-1` (disabled) so the UI won’t present a false
voltage-based percentage unless you provide a real battery-sense ADC connection.

If you do add/identify a real sense line, set:
- `BATTERY_ADC_PIN` to that ADC-capable GPIO
- `BATTERY_DIVIDER_RATIO` to the actual divider ratio from your resistor values

Use serial logs (`[BAT] ...`) while testing to confirm measured mV changes as expected.

### Charging detect pin on this board

From the schematic you shared, the battery charger status pin (`CHRG`) does **not** appear
to be routed to an ESP32-S3 GPIO (it looks routed only to a test pad). That means:

- There is no firmware-only GPIO number to set today for `BATTERY_CHARGE_DETECT_PIN`.
- Keep `BATTERY_CHARGE_DETECT_PIN = -1` on stock hardware.
- If you want true charging state on-screen, a hardware rework is needed (wire `CHRG` to a
  free GPIO, then set that GPIO number in the sketch).


## Troubleshooting: BOOT / RESET buttons not working

If the physical BOOT or RESET buttons appear unresponsive, the most common cause
is an EPD pin mapped to one of the ESP32-S3 **strapping pins**: GPIO 0, 3, 45,
or 46. If any of those are held low by the e-paper panel during power-on, the
chip cannot enter the bootloader.

**To recover a stuck board:**

1. Disconnect the e-paper panel completely.
2. Hold the BOOT button, press and release RESET, then release BOOT.
3. The board should enumerate as a serial/JTAG device and accept a new upload.
4. Fix the pin assignments in the sketch (see comments near `EPD_RST`/`EPD_BUSY`)
   so none of them overlap GPIO 0, 3, 45, or 46, then re-upload with the panel
   disconnected, reconnect, and verify it works.

**To force-flash without button access:**
```bash
esptool.py --chip esp32s3 --port /dev/ttyUSB0 erase_flash
```

Then re-upload from the Arduino IDE or `arduino-cli`.
