#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_SCREEN_USER="${STATUS_SCREEN_USER:-pi}"
if ! id -u "$STATUS_SCREEN_USER" >/dev/null 2>&1; then
  if [[ -n "${SUDO_USER:-}" ]] && id -u "$SUDO_USER" >/dev/null 2>&1; then
    echo "STATUS_SCREEN_USER '$STATUS_SCREEN_USER' not found; falling back to SUDO_USER '$SUDO_USER'."
    STATUS_SCREEN_USER="$SUDO_USER"
  else
    echo "Error: STATUS_SCREEN_USER '$STATUS_SCREEN_USER' does not exist and no valid SUDO_USER found." >&2
    exit 1
  fi
fi
RUNTIME_DIR="${STATUS_SCREEN_DIR:-/home/${STATUS_SCREEN_USER}/status-screen}"

sudo apt update
sudo apt install -y nginx python3-venv python3-pip ca-certificates
sudo update-ca-certificates

sudo mkdir -p "$RUNTIME_DIR"
sudo chown -R "${STATUS_SCREEN_USER}:${STATUS_SCREEN_USER}" "$RUNTIME_DIR"

# Copy repo contents into runtime dir (simple deployment model)
rsync -a --delete --exclude '.git' "$REPO_DIR/" "$RUNTIME_DIR/"

sudo -u "$STATUS_SCREEN_USER" python3 -m venv "$RUNTIME_DIR/.venv"
sudo -u "$STATUS_SCREEN_USER" "$RUNTIME_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$STATUS_SCREEN_USER" "$RUNTIME_DIR/.venv/bin/pip" install requests ics python-dateutil flask

# Web
sudo rm -rf /var/www/html/*
sudo cp "$RUNTIME_DIR/web/index.html" /var/www/html/index.html
sudo chown -R www-data:www-data /var/www/html
sudo chmod -R 755 /var/www/html

# Nginx snippet
if grep -q "location / {" /etc/nginx/sites-available/default; then
  sudo sed -i '/location \/ {/,/}/d' /etc/nginx/sites-available/default
fi
sudo sed -i '/server {/a \\' /etc/nginx/sites-available/default
sudo sed -i '/server {/a \\' /etc/nginx/sites-available/default
# Append our locations if not already present
if ! grep -q "include /etc/nginx/snippets/status-screen.conf;" /etc/nginx/sites-available/default; then
  sudo sed -i '/server {/a \    include /etc/nginx/snippets/status-screen.conf;\n' /etc/nginx/sites-available/default
fi
sudo mkdir -p /etc/nginx/snippets
sudo sed \
  -e "s|__STATUS_SCREEN_DIR__|$RUNTIME_DIR|g" \
  "$RUNTIME_DIR/config/nginx-status-screen.conf" \
  | sudo tee /etc/nginx/snippets/status-screen.conf >/dev/null
sudo systemctl restart nginx

# systemd services
sudo sed \
  -e "s|__STATUS_SCREEN_USER__|$STATUS_SCREEN_USER|g" \
  -e "s|__STATUS_SCREEN_DIR__|$RUNTIME_DIR|g" \
  "$RUNTIME_DIR/config/status-from-ics.service" \
  | sudo tee /etc/systemd/system/status-from-ics.service >/dev/null
sudo sed \
  -e "s|__STATUS_SCREEN_USER__|$STATUS_SCREEN_USER|g" \
  -e "s|__STATUS_SCREEN_DIR__|$RUNTIME_DIR|g" \
  "$RUNTIME_DIR/config/status-control.service" \
  | sudo tee /etc/systemd/system/status-control.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable status-from-ics.service status-control.service
sudo systemctl restart status-from-ics.service status-control.service

echo ""
echo "Done."
echo "Next: create $RUNTIME_DIR/.env from $RUNTIME_DIR/.env.example (set ICS_URL + AUTH_TOKEN), then:"
echo "  sudo systemctl restart status-from-ics.service status-control.service"
echo "Display: http://<pi-ip>/   Control: http://<pi-ip>/control"
