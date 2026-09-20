#!/usr/bin/env bash
# Report which of the shipped timers are not enabled. Runs on the box, after
# the unit files have been copied into /etc/systemd/system and daemon-reload'd.
#
# Both deploy paths call it — deploy.sh over ssh, update.sh locally — so the
# check cannot drift between the laptop route and the CI route.
#
# It exists because copying a unit file is not installing it: `cp` plus
# `daemon-reload` makes a timer *known*, `systemctl enable` is what links it
# into timers.target and makes it fire. Nothing in either deploy script enables
# anything, which is how the three per-agent digest timers shipped in
# ee7a3b6 and then sat in /etc/systemd/system for a day without ever running.
# The only symptom was a digest that did not arrive.
#
# It warns and returns 0; it never enables anything. Which agents run is the
# host's business, the same rule the scout@ restart sweep already follows —
# a deploy that decided would silently undo a timer someone turned off on
# purpose, and start a timer nobody chose.
#
#   timer-check.sh [unit-dir]        # default /opt/scout/app/deploy
set -uo pipefail

UNITS_DIR="${1:-/opt/scout/app/deploy}"

shopt -s nullglob
timers=()
for path in "$UNITS_DIR"/*.timer; do
  timers+=("$(basename "$path")")
done

if [[ ${#timers[@]} -eq 0 ]]; then
  exit 0
fi

# `is-enabled` exits non-zero for a disabled unit, which is the case being
# reported rather than a failure of this script — hence the `|| true` and the
# `set -e` left off above.
dead=()
for timer in "${timers[@]}"; do
  state="$(systemctl is-enabled "$timer" 2>/dev/null || true)"
  [[ -n "$state" ]] || state=not-installed
  printf '    %-32s %s\n' "$timer" "$state"
  [[ "$state" == enabled ]] || dead+=("$timer")
done

if [[ ${#dead[@]} -gt 0 ]]; then
  noun="timer"
  [[ ${#dead[@]} -gt 1 ]] && noun="timers"
  echo >&2
  echo "    ${#dead[@]} $noun shipped but will never fire. Enable:" >&2
  echo "      sudo systemctl enable --now ${dead[*]}" >&2
fi

exit 0
