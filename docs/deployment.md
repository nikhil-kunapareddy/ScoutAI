# Deployment

The bot is a **worker**, not a web service: Socket Mode holds an outbound
websocket to Slack and listens on no port. That has two consequences. There is
no inbound surface to attack, and no load balancer to pay for — but it has to
stay running, because Slack cannot wake a stopped process. There is no address
to reach it at. (Ping-to-wake is possible by switching to the HTTP Events API;
see [below](#ping-to-wake).)

- [Docker](#docker)
- [AWS — what this repo runs on](#aws--what-this-repo-runs-on)
- [GCP](#gcp)
- [On your Mac](#on-your-mac)
- [Ping-to-wake](#ping-to-wake)

## Docker

```bash
docker build -t scout .
docker run --rm --env-file .env scout
```

The image runs unchanged on ECS Fargate, GCE, Lightsail, and Cloud Run *worker
pools*. Two things it expects:

- **Secrets come from the platform**, not the image — `.dockerignore` excludes
  `.env`. Pass them with `--env-file` locally, and with Secrets Manager or SSM
  (AWS) or Secret Manager (GCP) in production.
- **`FALLBACK_BACKEND` is empty in the image.** There is no Ollama in the
  container, so an empty value disables the retry instead of making every
  Claude failure fail twice.

The résumé in `data/` is baked in by the `Dockerfile`, which is why the image
belongs in a private registry. Mount `data/` as a volume and drop that `COPY`
line if you would rather it were not.

## AWS — what this repo runs on

A single **`t4g.micro`** running the bot under systemd: Graviton/arm64, Amazon
Linux 2023, no inbound ports except SSH from one address, no load balancer.

**About $10.40/month** — $6.13 instance, $3.60 public IPv4, $0.64 for an 8 GB
gp3 volume. The IPv4 charge is unavoidable on any instance that needs both SSH
and outbound internet; the alternatives (NAT gateway, VPC endpoints) cost
several times more. Your model bill will dwarf all of it.

`deploy/` holds everything:

| File | Purpose |
|---|---|
| `deploy.sh` | rsync the working tree, install deps, refresh units, restart every enabled instance |
| `scout@.service` | the bot, one instance per agent (`scout@bigtech`, `scout@edu`) |
| `scout-digest@.service` | the daily digest, one instance per agent, posting as that agent |
| `scout-digest-<key>.timer` | when each agent's digest runs — 08:00, 08:10, 08:20 `America/Los_Angeles` |
| `scout-alert@.service` | DMs you when a unit fails |

```bash
./deploy/deploy.sh                        # host from deploy/host or $SCOUT_HOST
SCOUT_REQUIRE_CLEAN=1 ./deploy/deploy.sh  # refuse to ship uncommitted work
```

It ships the **working tree**, deliberately, not a branch: `.env` and `data/`
are git-ignored but are exactly what the host needs, so one copy covers code,
résumé, and secrets. What was sent is recorded in `/opt/scout/DEPLOYED`,
because with `.git` excluded the box has no other way to say what it is running.

### Host-local settings

Two files the deploy never overwrites, read in order by `scout@.service`:

```bash
# /opt/scout/scout.env — shared by every agent
CHECKPOINT_DB=/opt/scout/state/scout.sqlite
FALLBACK_BACKEND=
DIGEST_SLACK_USER=U012ABCDEF
```

```bash
# /opt/scout/scout-edu.env — this agent's own Slack app
SLACK_BOT_TOKEN=xoxb-…
SLACK_APP_TOKEN=xapp-…
CHECKPOINT_DB=/opt/scout/state/edu.sqlite
```

Both `scout@` and `scout-digest@` read the same two files in the same order,
which is what makes the digest land in the right window: the per-agent file is
where that agent's bot token comes from.

They win over the `.env` that was copied up, because `load_dotenv()` leaves
existing environment variables alone. The per-instance file is optional (the
leading `-` in the unit) and wins on a duplicate key.

```bash
sudo systemctl enable --now scout@bigtech
sudo systemctl enable --now scout@edu
sudo systemctl enable --now scout@referral

sudo systemctl enable --now scout-digest-bigtech.timer
sudo systemctl enable --now scout-digest-edu.timer
sudo systemctl enable --now scout-digest-referral.timer
```

**Why three timers and not one.** Each digest posts with its own agent's Slack
token, so it has to be its own process — which is what puts each report in its
own DM instead of stacking them into whichever app was the default. And they
are ten minutes apart rather than simultaneous: this box has 916 MB and no
swap, the three bots are already resident at ~130 MB each, and three more
Python processes at once would not fit. A run finishes in well under a minute.

The schedule names its timezone (`OnCalendar=*-*-* 08:00:00 America/Los_Angeles`)
rather than inheriting the host's, and deliberately is not the host's: the
instance runs on `America/New_York`, the digest is read in California. systemd
does the conversion, including across a daylight-saving change, so the DM
arrives at 08:00 Pacific whatever the box is set to. Needs systemd ≥ 250;
AL2023 ships 252 — check one with
`systemd-analyze calendar '*-*-* 08:00:00 America/Los_Angeles'`.

`deploy.sh` reads the enabled instances out of systemd's wants directory, so a
new agent joins the deploy by being enabled — nothing in the script needs
editing.

### Three things worth knowing

- **One Slack app means one bot.** Two `scout@` instances sharing a bot token
  would both answer every DM. Each agent gets the credentials of its own app.
  The digest needs no second app: it runs every agent in-process and posts
  through a single token.
- **Give each instance its own `CHECKPOINT_DB`.** Thread ids are the bare Slack
  user id, so two bots sharing one database interleave your conversations into a
  single history.
- **No IAM role on the instance.** Nothing is pushed to CloudWatch — alerting
  goes to Slack instead, which is where you already look. Attach an instance
  profile if you want CloudWatch Logs; the metrics line is already shaped for
  Logs Insights.

Fargate, Lightsail, or an EC2 instance elsewhere all run the same `Dockerfile`
if you would rather not manage a box.

## GCP

**Compute Engine `e2-micro`** is the simplest fit, and it is in the always-free
tier in `us-west1`, `us-central1`, and `us-east1`:

```bash
gcloud compute instances create-with-container scout \
  --machine-type=e2-micro --zone=us-central1-a \
  --container-image=REGION-docker.pkg.dev/PROJECT/scout/scout:latest \
  --container-env-file=.env \
  --container-restart-policy=always
```

Cloud Run is the other option, but a Cloud Run *service* must listen on `$PORT`,
which a Socket Mode worker does not do — use a Cloud Run **worker pool** (built
for exactly this) if it is available in your project, or stay on GCE.

## On your Mac

A `launchd` LaunchAgent with `RunAtLoad` and `KeepAlive` restarts the bot on
crash and starts it at login. Free, and it keeps the Ollama fallback — but the
bot is unreachable whenever the Mac sleeps, so it suits a desktop better than a
laptop.

## Ping-to-wake

Scale-to-zero (Cloud Run at `min-instances=0`, or Lambda behind a Function URL)
needs three changes, none of which exist yet:

1. **An HTTP adapter.** `slack_bolt` ships `SlackRequestHandler` for
   wsgi/asgi/flask/fastapi/aws_lambda. Because the Slack layer only talks to
   `ConversationalAgent`, this is a sibling of `bot.py`, not a rewrite.
2. **Ack within 3 seconds.** Slack retries up to three times otherwise, and a
   real turn takes 10–60s. Ack immediately, do the work in a background worker,
   and post the reply with `chat.postMessage` — plus **dedupe on the Slack event
   id**, or a slow cold start gets you three replies to one message.
3. **Persistence.** Set `CHECKPOINT_DB`, or a container that scales to zero
   re-parses the résumé before nearly every first message. LangGraph makes this
   the smallest of the three: the checkpointer is already swappable, and
   `langgraph-checkpoint-postgres` is a drop-in for a platform with no disk.
