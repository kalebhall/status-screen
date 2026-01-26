# Status Screen Pi (ICS + Override)

- Reads an ICS calendar feed to determine "IN A MEETING" / "OUT OF OFFICE"
- Allows remote override via web control page (LAN)
- Calendar always wins if an event is active (all-day events only count if they match OOO keywords)
- Optional working hours automatically mark time outside of normal hours as OUT OF OFFICE

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

## ICS refresh caching

To avoid fetching the calendar on every poll, the service caches the last ICS file and reuses it until the refresh timer expires:

```bash
ICS_REFRESH_SECONDS="300"
```

By default the cached file is stored at `/home/pi/status-screen/calendar.ics`. You can override the path with `ICS_CACHE_PATH` if needed.

## Office365 / Outlook ICS troubleshooting

If the display shows `HTTPSConnectionPool(... Max retries exceeded ...)`, the Pi cannot reach the
ICS URL or is receiving a non-calendar response. Ensure:

- The `ICS_URL` points to a published calendar feed (Settings → Calendar → Shared calendars → Publish a calendar).
- The calendar is published with **Can view all details** and an ICS URL copied from the publish dialog.
- The Pi can reach the URL over HTTPS (test from the Pi with `curl -I "<ICS_URL>"`; you should see a 200 and `BEGIN:VCALENDAR` in the body).
- The service also accepts `webcal://` URLs and Outlook subscription links (it will normalize them to the underlying HTTPS ICS feed).

If `curl -L "<ICS_URL>" -o /tmp/cal.ics` works on the Pi but the display still shows the error:

- Confirm the runtime `.env` file at `/home/pi/status-screen/.env` contains the same `ICS_URL`.
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

The optional Windows mic agent watches for active microphone usage and posts a BUSY override to the Pi whenever the mic is in use. It uses the `AUTH_TOKEN` value from the `.env` file. Run it from PowerShell:

```powershell
.\windows\status_agent.ps1 -PiBaseUrl "http://<pi-ip>" -Token "<AUTH_TOKEN>"
```

Optional parameters let you tune polling and the busy label:

```powershell
.\windows\status_agent.ps1 -PiBaseUrl "http://<pi-ip>" -Token "<AUTH_TOKEN>" -PollSeconds 10 -BusyMinutes 5 -BusyDetail "On a call (mic active)"
```

## Kiosk mode (full-screen display)

For a dedicated status display, launch Chromium in kiosk mode on the Pi:

```bash
chromium-browser --kiosk --app="http://<pi-ip>/" --start-fullscreen
```
