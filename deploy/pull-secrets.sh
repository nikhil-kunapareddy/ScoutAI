#!/usr/bin/env bash
# Mirror the /scout parameters in AWS Parameter Store into the env files the
# units read. Run as root by update.sh and deploy.sh, before any restart.
#
#   /scout/shared/NAME   ──▶ /opt/scout/secrets.env           every unit
#   /scout/<agent>/NAME  ──▶ /opt/scout/secrets-<agent>.env   that agent's units
#
# Keys live in the store because neither deploy route carries them: git must
# not, and rsync only carries whatever the laptop's .env happened to hold, so a
# new key reached the box on the next laptop deploy and never on a merge to
# main. Changing one is now `aws ssm put-parameter --overwrite` and a re-run of
# the Deploy workflow.
#
# The files are generated whole on every deploy, so a parameter deleted from
# the store disappears from the box. Hand-kept settings stay in scout.env and
# scout-<agent>.env, which this never touches; the units read the generated
# files after those, so the store wins on a duplicate key.
#
# Nothing is written unless the read succeeds: a missing permission or an SSM
# outage fails the deploy before a restart, rather than starting the bots on
# whatever was there before without saying so.
#
# Not called secrets.sh: .gitignore's `secrets.*` would keep that out of the
# repo, and update.sh reads this file out of git.
#
#   deploy/pull-secrets.sh [dest-dir]
set -euo pipefail

PREFIX=/scout
DEST="${1:-/opt/scout}"
REGION="${AWS_REGION:-us-east-1}"
OWNER=ec2-user

params="$(aws ssm get-parameters-by-path --region "$REGION" --path "$PREFIX" \
  --recursive --with-decryption --output json)"

# Built beside the destination, so each file lands with a rename and a unit
# starting mid-deploy reads the old file or the new one, never half of one.
tmp="$(mktemp -d "$DEST/.secrets.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

# Single quotes read the same to systemd and to a shell sourcing the file, as
# long as the value holds no quote or line break — so those are refused rather
# than escaped two different ways. Errors name the parameter, never its value.
jq -r --arg prefix "$PREFIX/" --arg q "'" '
  .Parameters[]
  | .Name as $name
  | ($name | ltrimstr($prefix) | split("/")) as $path
  | if ($path | length) != 2 then
      error("\($name): expected \($prefix)shared/NAME or \($prefix)<agent>/NAME")
    elif ($path[0] | test("^[a-z0-9-]+$") | not) then
      error("\($name): \"\($path[0])\" is not an agent key")
    elif ($path[1] | test("^[A-Za-z_][A-Za-z0-9_]*$") | not) then
      error("\($name): \"\($path[1])\" is not an environment variable name")
    elif (.Value | test("[\n\r]") or contains($q)) then
      error("\($name): the value holds a quote or a line break")
    else
      "\($path[0])\t\($path[1])=\($q)\(.Value)\($q)"
    end
' <<<"$params" | sort | while IFS=$'\t' read -r group line; do
  if [[ "$group" == shared ]]; then
    file="$tmp/secrets.env"
  else
    file="$tmp/secrets-$group.env"
  fi
  printf '%s\n' "$line" >> "$file"
done

shopt -s nullglob

# A file whose parameters are all gone goes too, or the box would keep a key
# the store no longer has.
for file in "$DEST"/secrets.env "$DEST"/secrets-*.env; do
  [[ -e "$tmp/$(basename "$file")" ]] || rm -f "$file"
done

files=("$tmp"/*.env)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "    nothing under $PREFIX in Parameter Store"
fi

# The `+` form because an empty array is "unbound" to `set -u` before bash 4.4.
for file in ${files[@]+"${files[@]}"}; do
  chmod 600 "$file"
  if [[ $EUID -eq 0 ]]; then
    chown "$OWNER:$OWNER" "$file"
  fi
  # Names only: this lands in the Actions log.
  echo "    $(basename "$file"): $(cut -d= -f1 "$file" | tr '\n' ' ')"
  mv "$file" "$DEST/"
done
