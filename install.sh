#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  chmod 600 config.yaml
  echo "Created config.yaml - edit RCON URL/key and filtering rules before starting."
fi
mkdir -p logs data
printf '\nInstall complete. Test with:\n  .venv/bin/python app.py --config config.yaml --dry-run --once\n\nStart PM2 with:\n  pm2 start ecosystem.config.js\n  pm2 save\n'
