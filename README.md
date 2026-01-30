# Status Screen Pi (ICS + Override)

- Reads an ICS calendar feed to determine "IN A MEETING" / "OUT OF OFFICE"
- Allows remote override via web control page (LAN)
- Calendar always wins if an event is active (all-day events only count if they match OOO keywords)
- Optional working hours automatically mark time outside of normal hours as OUT OF OFFICE

## Calendar keywords for status changes

All-day events only flip the status to **OUT OF OFFICE** when the event title contains one of
these case-insensitive keywords:

- `out of office`
- `ooo`
- `vacation`
- `leave`
- `pto`
- `sick`

Events with `cancelled` or `canceled` in the title are ignored.

## Pi install

```bash
git clone https://github.com/kalebhall/status-screen.git
cd status-screen
STATUS_SCREEN_USER=screen ./scripts/install_pi.sh
cp /home/screen/status-screen/.env.example /home/screen/status-screen/.env
nano /home/screen/status-screen/.env
sudo systemctl restart status-from-ics.service status-control.service
```

## Nginx troubleshooting

If Nginx logs `location` directive errors for `/etc/nginx/snippets/status-screen.conf`, the
snippet is being loaded outside a `server {}` block. Ensure the snippet is only included
inside the desired server configuration (for example in `/etc/nginx/sites-available/default`)
and remove any global include of `/etc/nginx/snippets/*.conf` from `nginx.conf` or the `http {}`
context. The `config/nginx-status-screen.conf` file is intended to be included inside a
server block as written. 

## Working hours / office time

You can automatically mark time outside your normal office hours as out of office by adding these values to `.env`:

```bash
WORK_HOURS_START="09:00"
WORK_HOURS_END="17:00"
WORK_HOURS_DAYS="Mon-Fri"
```

Notes:

- Working hours are evaluated in `TIMEZONE_NAME`.
- Calendar events still win during scheduled meetings.
- `WORK_HOURS_DAYS` supports comma-separated days or ranges (e.g., `Mon,Wed,Fri` or `Mon-Fri`).
- `TIMEZONE_NAME` accepts IANA names (like `America/Los_Angeles`) and common Windows names (like `Pacific Standard Time`).

### Working hours per group

If you configure multiple groups (`ICS_URLS`), you can supply per-group working hours by using
list-based values. Each entry maps to the matching group index (1st entry → group 1, etc.):

```bash
WORK_HOURS_STARTS='["08:00", "10:00"]'
WORK_HOURS_ENDS='["16:00", "19:00"]'
WORK_HOURS_DAYS_LIST='["Mon-Fri", "Tue-Sat"]'
```

When a list entry is missing, the global `WORK_HOURS_START`, `WORK_HOURS_END`, and
`WORK_HOURS_DAYS` values are used as fallbacks.

## Microsoft busy status

If your Outlook calendar includes Microsoft busy status values (free/busy/out of office), you can
use them to decide whether an event should trigger a meeting or out-of-office state:

```bash
USE_MS_BUSY_STATUS="true"
```

With this enabled, `X-MICROSOFT-CDO-BUSYSTATUS=FREE` events are ignored, and `OOF` events are treated
as out of office even if the event title does not include OOO keywords.

## ICS refresh caching

To avoid fetching the calendar on every poll, the service caches the last ICS file and reuses it until the refresh timer expires:

```bash
ICS_REFRESH_SECONDS="300"
# or
ICS_REFRESH="300"
```

By default the cached file is stored at `/home/pi/status-screen/calendar.ics`. You can override the path with `ICS_CACHE_PATH` if needed.
When using multiple groups (`ICS_URLS`), each group/person gets its own cache file derived from the base path (for example, `calendar-1.ics`, `calendar-2.ics`).

## Hide calendar event titles

If you prefer to keep meeting titles off the display, disable event details:

```bash
SHOW_EVENT_DETAILS="false"
```

## Custom CA certificates (Fortigate DPI, etc.)

If your network uses a custom TLS inspection certificate, set one of these environment variables so `requests` trusts it when downloading the ICS feed:

```bash
ICS_CA_BUNDLE="/home/pi/status-screen/fortigate-ca.pem"
# or use the standard Requests/Python variables:
# REQUESTS_CA_BUNDLE="/home/pi/status-screen/fortigate-ca.pem"
# SSL_CERT_FILE="/home/pi/status-screen/fortigate-ca.pem"
```

To disable TLS certificate verification entirely (not recommended), set one of the bundle variables to `false`:

```bash
SSL_CERT_FILE="false"
```

Restart the service after updating `.env`: `sudo systemctl restart status-from-ics.service`.

## Office365 / Outlook ICS troubleshooting

If the display shows `HTTPSConnectionPool(... Max retries exceeded ...)`, the Pi cannot reach the
ICS URL or is receiving a non-calendar response. Ensure:

- The first `ICS_URLS` entry points to a published calendar feed (Settings → Calendar → Shared calendars → Publish a calendar).
- The calendar is published with **Can view all details** and an ICS URL copied from the publish dialog.
- The Pi can reach the URL over HTTPS (test from the Pi with `curl -I "<ICS_URLS_ENTRY>"`; you should see a 200 and `BEGIN:VCALENDAR` in the body).
- The service also accepts `webcal://` URLs and Outlook subscription links (it will normalize them to the underlying HTTPS ICS feed).

If `curl -L "<ICS_URLS_ENTRY>" -o /tmp/cal.ics` works on the Pi but the display still shows the error:

- Confirm the runtime `.env` file at `/home/pi/status-screen/.env` contains the same `ICS_URLS` entry.
- Restart the service after changes: `sudo systemctl restart status-from-ics.service`.
- Check logs for the live error message: `sudo journalctl -u status-from-ics.service -n 200 --no-pager`.

To install for a different user or runtime directory:

```bash
STATUS_SCREEN_USER=custom ./scripts/install_pi.sh
cp /home/custom/status-screen/.env.example /home/custom/status-screen/.env
nano /home/custom/status-screen/.env
sudo systemctl restart status-from-ics.service status-control.service
```

## Windows mic agent

The optional Windows mic agent watches for active microphone usage and posts a BUSY override to the Pi whenever the mic is in use. It uses the matching `AUTH_TOKENS` entry from the `.env` file (the same index as the calendar you want to control). Run it from PowerShell:

```powershell
.\windows\status_agent.ps1 -PiBaseUrl "http://<pi-ip>" -Token "<AUTH_TOKENS_ENTRY>"
```

Optional parameters let you tune polling and the busy label:

```powershell
.\windows\status_agent.ps1 -PiBaseUrl "http://<pi-ip>" -Token "<AUTH_TOKENS_ENTRY>" -PollSeconds 10 -BusyMinutes 5 -BusyDetail "On a call (mic active)"
```

To run it in the background with Task Scheduler (no taskbar window), use the VBScript
launcher and point your task at `wscript.exe`:

```
wscript.exe "C:\path\to\status_agent_hidden.vbs" -ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File "C:\path\to\status_agent.ps1" -PiBaseUrl "http://<pi-ip>" -Token "<AUTH_TOKENS_ENTRY>"
```

Calendar BUSY/IN A MEETING states take priority over the mic-active override.

## Kiosk mode (full-screen display)

For a dedicated status display, launch Chromium in kiosk mode on the Pi:

```bash
chromium-browser --kiosk --app="http://<pi-ip>/" --start-fullscreen
```

## Multi-person display (single UI)

The status service can now render one or many groups from a single UI. Configure
calendar feeds by providing arrays in `.env`, and the main UI will automatically show each
group (use a single entry array for a one-person setup).

Example `.env` configuration:

```bash
ICS_URLS='["https://calendar-1.ics","https://calendar-2.ics"]'
AUTH_TOKENS='["token-1","token-2"]'
DISPLAY_NAMES='["Team A","Team B"]'
```

If you only need one group, keep just one entry in each array (and optionally supply a
single `DISPLAY_NAMES` entry). The UI will display just that single group.
