#!/usr/bin/env bash
# Push the working tree to the bot host and restart the services.
#
# Deliberately rsync and not `git pull`: .env and data/ are git-ignored but are
# exactly what the host needs, so one copy covers code, resume, and secrets.
# Host-local settings live in /opt/scout/scout.env and the per-agent
# /opt/scout/scout-<agent>.env, neither of which is ever overwritten.
#
# Every enabled scout@ instance is restarted, so a second agent is picked up
# once it is enabled — nothing here needs editing.
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
"${SSH[@]}" 'bash -s' <<'REMOTE'
# No -e: a failed restart should still print its log, which is the whole reason
# you are looking. The exit status is decided by the is-active sweep instead.
set -uo pipefail

# Which agents run is the host's business, not this script's — read the enabled
# instances out of the wants directory rather than listing them here, so a new
# agent joins the deploy by being enabled. `systemctl list-units` would only
# report what is currently loaded, skipping a stopped instance: exactly the one
# most likely to need restarting.
shopt -s nullglob
units=()
for link in /etc/systemd/system/multi-user.target.wants/scout@*.service; do
  units+=("$(basename "$link")")
done

if [[ ${#units[@]} -eq 0 ]]; then
  echo "No enabled scout@ instances. Enable one:" >&2
  echo "    sudo systemctl enable --now scout@bigtech" >&2
  exit 1
fi

sudo systemctl restart "${units[@]}"
sleep 3

status=0
for unit in "${units[@]}"; do
  state="$(systemctl is-active "$unit" || true)"
  printf '    %-28s %s\n' "$unit" "$state"
  [[ $state == active ]] || status=1
done

for unit in "${units[@]}"; do
  echo "==> Recent log: $unit"
  journalctl -u "$unit" -n 15 --no-pager -o cat
done

exit $status
REMOTE
