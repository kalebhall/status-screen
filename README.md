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
