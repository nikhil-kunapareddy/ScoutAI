#!/usr/bin/env bash
# Deploy one commit onto this box. Run by SSM Run Command, as root, from the
# GitHub Actions deploy job — never by hand from a laptop (that is deploy.sh,
# which ships the working tree instead).
#
#   main merged ──▶ CI green ──▶ Actions ──OIDC──▶ ssm:SendCommand ──▶ here
#
# The script is read out of the commit being deployed (`git show <sha>:...`),
# so a change to the deploy procedure ships with the change that needs it and
# this file is never the stale half of a deploy.
#
# What this does *not* touch is the point of using git rather than rsync: .env,
# data/ and state/ are git-ignored, so they are untracked here and a hard reset
# leaves them alone. No `git clean` for the same reason — it would take the
# resume and the referral list with it.
#
#   deploy/update.sh <sha>
set -euo pipefail

SHA="${1:?usage: update.sh <commit-sha>}"
APP=/opt/scout/app
VENV=/opt/scout/venv

# The checkout belongs to ec2-user, and git refuses to work in a tree it does
# not own. Everything that writes into $APP goes through this; only systemd
# calls stay as root.
app() { runuser -u ec2-user -- "$@"; }

enabled_units() {
  shopt -s nullglob
  local link units=()
  for link in /etc/systemd/system/multi-user.target.wants/scout@*.service; do
    units+=("$(basename "$link")")
  done
  printf '%s\n' "${units[@]}"
}

restart_and_check() {
  local units=("$@") unit state failed=0
  systemctl restart "${units[@]}"
  # Long enough for Socket Mode to connect or for a bad import to abort: the
  # unit is Restart=always, so a crash loop still reads "activating" here.
  sleep 15
  for unit in "${units[@]}"; do
    state="$(systemctl is-active "$unit" || true)"
    printf '    %-28s %s\n' "$unit" "$state"
    [[ "$state" == active ]] || failed=1
  done
  return $failed
}

mapfile -t UNITS < <(enabled_units)
if [[ ${#UNITS[@]} -eq 0 ]]; then
  echo "No enabled scout@ instances — nothing to deploy to." >&2
  exit 1
fi

PREV="$(app git -C "$APP" rev-parse HEAD)"
echo "==> $PREV -> $SHA"

app git -C "$APP" fetch --quiet origin main
app git -C "$APP" reset --hard --quiet "$SHA"
app "$VENV/bin/pip" install -q -r "$APP/requirements.txt"

# Units can change between commits — a new agent brings a new timer with it.
cp "$APP"/deploy/*.service "$APP"/deploy/*.timer /etc/systemd/system/
systemctl daemon-reload

# ...and copying that timer is not enabling it. Same check deploy.sh runs, out
# of the same file, so the CI route and the laptop route cannot disagree.
echo "==> Timers"
bash "$APP/deploy/timer-check.sh" "$APP/deploy"

echo "==> Restarting ${#UNITS[@]} bot(s)"
if ! restart_and_check "${UNITS[@]}"; then
  echo "==> A bot did not come up. Rolling back to $PREV" >&2
  app git -C "$APP" reset --hard --quiet "$PREV"
  app "$VENV/bin/pip" install -q -r "$APP/requirements.txt"
  cp "$APP"/deploy/*.service "$APP"/deploy/*.timer /etc/systemd/system/
  systemctl daemon-reload
  restart_and_check "${UNITS[@]}" || echo "    Rollback did not come up either." >&2
  for unit in "${UNITS[@]}"; do
    echo "==> Log: $unit"
    journalctl -u "$unit" -n 20 --no-pager -o cat
  done
  exit 1
fi

# Same stamp deploy.sh writes, so `cat /opt/scout/DEPLOYED` answers the same
# question however the box was last updated.
cat > /opt/scout/DEPLOYED <<EOF
commit=$(app git -C "$APP" rev-parse --short HEAD)
branch=main
dirty=no
deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
deployed_by=github-actions
EOF

echo "==> Deployed $SHA"
