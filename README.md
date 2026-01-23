# Status Screen Pi (ICS + Override)

- Reads an ICS calendar feed to determine "IN A MEETING" / "OUT OF OFFICE"
- Allows remote override via web control page (LAN)
- Calendar always wins if an event is active

## Pi install

```bash
git clone <your repo url>
cd status-screen-pi
./scripts/install_pi.sh
cp /home/pi/status-screen/.env.example /home/pi/status-screen/.env
nano /home/pi/status-screen/.env
sudo systemctl restart status-from-ics.service status-control.service
