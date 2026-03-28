#!/usr/bin/env bash
# Initial server setup script — run once on a fresh Ubuntu EC2 instance.
# Usage: ssh into your server and run:
#   bash setup.sh

set -euo pipefail

APP_DIR="/home/ubuntu/Tierlister"

echo "==> Updating system packages..."
sudo apt update && sudo apt upgrade -y

echo "==> Installing Python 3, pip, venv, nginx, git..."
sudo apt install -y python3 python3-pip python3-venv nginx git

echo "==> Cloning repo (if not already cloned)..."
if [ ! -d "$APP_DIR" ]; then
    git clone https://github.com/tdcinaz/Tierlister.git "$APP_DIR"
fi

cd "$APP_DIR"

echo "==> Creating Python virtual environment..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==> Setting up .env file..."
if [ ! -f .env ]; then
    echo "# Add your API keys here:" > .env
    echo 'SERP_KEY=""' >> .env
    echo 'GEMINI_KEY=""' >> .env
    echo ""
    echo "*** IMPORTANT: Edit /home/ubuntu/Tierlister/.env and add your API keys ***"
fi

echo "==> Installing systemd service..."
sudo cp deploy/tierlister.service /etc/systemd/system/tierlister.service
sudo cp deploy/sudoers-tierlister /etc/sudoers.d/tierlister
sudo chmod 0440 /etc/sudoers.d/tierlister
sudo systemctl daemon-reload
sudo systemctl enable tierlister
sudo systemctl start tierlister

echo "==> Configuring nginx..."
sudo rm -f /etc/nginx/sites-enabled/default
sudo cp deploy/nginx.conf /etc/nginx/sites-available/tierlister
sudo ln -sf /etc/nginx/sites-available/tierlister /etc/nginx/sites-enabled/tierlister
sudo nginx -t
sudo systemctl restart nginx

echo ""
echo "==> Setup complete!"
echo "    App is running at http://$(curl -s http://checkip.amazonaws.com)"
echo "    Don't forget to edit .env with your API keys, then restart:"
echo "    sudo systemctl restart tierlister"
