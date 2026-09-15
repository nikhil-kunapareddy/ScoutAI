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
# It ships the working tree, so the branch you are on is irrelevant and
# uncommitted edits go too. What was sent is recorded in /opt/scout/DEPLOYED,
# because with .git excluded the box has no other way to say what it is running.
#
#   ./deploy/deploy.sh                      # code + restart
#   SCOUT_HOST=1.2.3.4 ./deploy/deploy.sh
#   SCOUT_REQUIRE_CLEAN=1 ./deploy/deploy.sh  # refuse to ship uncommitted work
set -euo pipefail

HOST="${SCOUT_HOST:-$(cat "$(dirname "$0")/host" 2>/dev/null || true)}"
KEY="${SCOUT_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "$HOST" ]]; then
  echo "No host. Set SCOUT_HOST, or write the IP to deploy/host." >&2
  exit 1
fi

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "ec2-user@$HOST")

# What never leaves the laptop. One list, used twice: rsync skips these, and so
# must the dirty check below — an untracked file that is not shipped is not a
# deploy risk, and warning about it trains you to ignore the warning.
EXCLUDES=(.git .venv __pycache__ .pytest_cache .ruff_cache .pi logs state .DS_Store)
RSYNC_EXCLUDES=()
GIT_EXCLUDES=()
for pattern in "${EXCLUDES[@]}"; do
  RSYNC_EXCLUDES+=(--exclude "$pattern")
  GIT_EXCLUDES+=(--exclude="$pattern")
done

# What is about to ship. This copies the working tree, not a branch, so the
# commit is a label for what was on the laptop rather than something the host
# could check out — which is exactly why it is worth writing down. Untracked
# files count as dirty: rsync sends them, so they run on the box without
# appearing in any commit.
REV="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
TRACKED_EDITS="$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null || true)"
UNTRACKED="$(git -C "$REPO" ls-files --others --exclude-standard \
  "${GIT_EXCLUDES[@]}" 2>/dev/null || true)"
DIRTY=no
[[ -n "$TRACKED_EDITS$UNTRACKED" ]] && DIRTY=yes

echo "==> Shipping $BRANCH @ $REV (uncommitted changes: $DIRTY)"
if [[ "$DIRTY" == yes ]]; then
  # A warning, not a refusal: deploying a work-in-progress to your own bot is a
  # reasonable thing to want. Knowing you did it afterwards is the hard part.
  echo "    The box will run code that is not in any commit:" >&2
  [[ -n "$TRACKED_EDITS" ]] && sed 's/^/      /' <<<"$TRACKED_EDITS" >&2
  [[ -n "$UNTRACKED" ]] && sed 's/^/      ?? /' <<<"$UNTRACKED" >&2
  echo "    Set SCOUT_REQUIRE_CLEAN=1 to make this an error instead." >&2
  if [[ "${SCOUT_REQUIRE_CLEAN:-}" == 1 ]]; then
    echo "Refusing: SCOUT_REQUIRE_CLEAN=1 and the tree is dirty." >&2
    exit 1
  fi
fi

echo "==> Syncing $REPO to $HOST"
rsync -az --delete \
  -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  "${RSYNC_EXCLUDES[@]}" \
  "$REPO/" "ec2-user@$HOST:/opt/scout/app/"

echo "==> Installing dependencies if they changed"
"${SSH[@]}" '/opt/scout/venv/bin/pip install -q -r /opt/scout/app/requirements.txt'

# Outside /opt/scout/app, so `rsync --delete` never removes it and the next
# deploy's file list cannot disagree with it. `.git` is excluded from the sync,
# so this stamp is the only thing on the box that knows what it is running.
echo "==> Recording the deployed revision"
"${SSH[@]}" "cat > /opt/scout/DEPLOYED" <<EOF
commit=$REV
branch=$BRANCH
dirty=$DIRTY
deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
deployed_by=$(whoami)@$(hostname -s)
EOF

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
