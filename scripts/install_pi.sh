#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="/home/pi/status-screen"

sudo apt update
sudo apt install -y nginx python3-venv python3-pip ca-certificates
sudo update-ca-certificates

mkdir -p "$RUNTIME_DIR"
sudo chown -R pi:pi "$RUNTIME_DIR"

# Copy repo contents into runtime dir (simple deployment model)
rsync -a --delete --exclude '.git' "$REPO_DIR/" "$RUNTIME_DIR/"

cd "$RUNTIME_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install requests ics python-dateutil flask

# Web
sudo rm -rf /var/www/html/*
sudo cp "$RUNTIME_DIR/web/index.html" /var/www/html/index.html
sudo chown -R www-data:www-data /var/www/html
sudo chmod -R 755 /var/www/html

# Nginx snippet
sudo sed -i '/server {/a \\' /etc/nginx/sites-available/default
sudo sed -i '/server {/a \\' /etc/nginx/sites-available/default
# Append our locations if not already present
if ! grep -q "alias /home/pi/status-screen/status.json" /etc/nginx/sites-available/default; then
  sudo sed -i '/server {/a \    include /etc/nginx/snippets/status-screen.conf;\n' /etc/nginx/sites-available/default
fi
sudo mkdir -p /etc/nginx/snippets
sudo cp "$RUNTIME_DIR/config/nginx-status-screen.conf" /etc/nginx/snippets/status-screen.conf
sudo systemctl restart nginx

# systemd services
sudo cp "$RUNTIME_DIR/config/status-from-ics.service" /etc/systemd/system/status-from-ics.service
sudo cp "$RUNTIME_DIR/config/status-control.service" /etc/systemd/system/status-control.service
sudo systemctl daemon-reload
sudo systemctl enable status-from-ics.service status-control.service
sudo systemctl restart status-from-ics.service status-control.service

echo ""
echo "Done."
echo "Next: create $RUNTIME_DIR/.env from $RUNTIME_DIR/.env.example (set ICS_URL + AUTH_TOKEN), then:"
echo "  sudo systemctl restart status-from-ics.service status-control.service"
echo "Display: http://<pi-ip>/   Control: http://<pi-ip>/control"
