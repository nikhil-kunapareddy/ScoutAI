#!/usr/bin/env bash
# Push the working tree to the bot host and restart the services.
#
# Deliberately rsync and not `git pull`: .env and data/ are git-ignored but are
# exactly what the host needs, so one copy covers code, resume, and secrets.
# Host-local settings live in /opt/scout/scout.env, which is never overwritten.
#
#   ./deploy/deploy.sh              # code + restart
#   SCOUT_HOST=1.2.3.4 ./deploy/deploy.sh
set -euo pipefail

HOST="${SCOUT_HOST:-$(cat "$(dirname "$0")/host" 2>/dev/null || true)}"
KEY="${SCOUT_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "$HOST" ]]; then
  echo "No host. Set SCOUT_HOST, or write the IP to deploy/host." >&2
  exit 1
fi

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "ec2-user@$HOST")

echo "==> Syncing $REPO to $HOST"
rsync -az --delete \
  -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '.pi' \
  --exclude 'logs' --exclude 'state' --exclude '.DS_Store' \
  "$REPO/" "ec2-user@$HOST:/opt/scout/app/"

echo "==> Installing dependencies if they changed"
"${SSH[@]}" '/opt/scout/venv/bin/pip install -q -r /opt/scout/app/requirements.txt'

echo "==> Installing unit files"
"${SSH[@]}" 'sudo cp /opt/scout/app/deploy/*.service /opt/scout/app/deploy/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload'

echo "==> Restarting"
"${SSH[@]}" 'sudo systemctl restart scout@bigtech && sleep 3 && systemctl is-active scout@bigtech'

echo "==> Recent log"
"${SSH[@]}" 'journalctl -u scout@bigtech -n 15 --no-pager -o cat'
