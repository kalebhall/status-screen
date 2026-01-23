# Status Screen Pi (ICS + Override)

- Reads an ICS calendar feed to determine "IN A MEETING" / "OUT OF OFFICE"
- Allows remote override via web control page (LAN)
- Calendar always wins if an event is active (all-day events only count if they match OOO keywords)

## Pi install

```bash
git clone https://github.com/kalebhall/status-screen.git
cd status-screen
./scripts/install_pi.sh
cp /home/pi/status-screen/.env.example /home/pi/status-screen/.env
nano /home/pi/status-screen/.env
sudo systemctl restart status-from-ics.service status-control.service
```

To install for a different user or runtime directory:

```bash
STATUS_SCREEN_USER=screen ./scripts/install_pi.sh
cp /home/screen/status-screen/.env.example /home/screen/status-screen/.env
nano /home/screen/status-screen/.env
sudo systemctl restart status-from-ics.service status-control.service
```
