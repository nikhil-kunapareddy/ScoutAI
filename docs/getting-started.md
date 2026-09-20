# Getting started

A walkthrough from an empty directory to a bot that answers your DMs, in about
fifteen minutes. Most of that is Slack's app-creation screens; the code needs
three commands.

- [What you'll need](#what-youll-need)
- [1. Install](#1-install)
- [2. Create the Slack app](#2-create-the-slack-app)
- [3. Configure](#3-configure)
- [4. Add your résumé](#4-add-your-résumé)
- [5. Check the setup](#5-check-the-setup)
- [6. Talk to it](#6-talk-to-it)
- [7. Tell it who you know](#7-tell-it-who-you-know)
- [8. Turn on the daily digest](#8-turn-on-the-daily-digest)
- [Troubleshooting](#troubleshooting)
- [Where to go next](#where-to-go-next)

## What you'll need

| | |
|---|---|
| **Python 3.10 or newer** | `python3 --version` |
| **A Slack workspace you can install apps into** | Your own free workspace is the easiest — create one at [slack.com/create](https://slack.com/create) |
| **A model** | An [Anthropic API key](https://console.anthropic.com/settings/keys) (recommended), or [Ollama](https://ollama.com) running locally for a free, private setup |
| **Your résumé** | PDF, DOCX, TXT, or MD |

You do not need a server, a domain, or an open port. The bot dials out to Slack
over a websocket, so it runs fine on a laptop while you try it.

## 1. Install

```bash
git clone https://github.com/nikhil-kunapareddy/ScoutAI.git
cd ScoutAI
make install
source .venv/bin/activate
```

`make install` creates `.venv`, installs the dependencies, and puts a `scout`
command on your path. Check it:

```bash
scout --help
```

Prefer to do it by hand? `python3 -m venv .venv && source .venv/bin/activate &&
pip install -e ".[dev]"` is the same thing.

## 2. Create the Slack app

Each agent is its own Slack app, which is what gives it its own DM window in
your sidebar. Start with one; add the second later if you want both.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
   → **From a manifest**, pick your workspace, and paste this:

   ```yaml
   display_information:
     name: BigTech Agent
   features:
     bot_user: { display_name: BigTech Agent, always_online: true }
     app_home:
       messages_tab_enabled: true
       messages_tab_read_only_enabled: false
   oauth_config:
     scopes:
       bot: [chat:write, im:history, im:read, im:write]
   settings:
     event_subscriptions:
       bot_events: [message.im]
     socket_mode_enabled: true
   ```

   The manifest covers the scopes, the DM event subscription, and the Messages
   tab — the three things that are easy to miss when clicking through.

2. **Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**.
   Give it any name, add the `connections:write` scope, and copy the
   `xapp-…` token. This is the one Socket Mode uses.

3. **Install App** → **Install to Workspace** → copy the **Bot User OAuth
   Token**, `xoxb-…`.

You now have two tokens. Keep the tab open; you will paste them next.

<details>
<summary>Doing it without the manifest</summary>

Create a **Blank app**, then: **Socket Mode** → on, and generate the app-level
token with `connections:write`; **OAuth & Permissions** → add the bot scopes
`chat:write`, `im:history`, `im:read`, `im:write`; **Event Subscriptions** → on,
and subscribe to the bot event `message.im`; **App Home** → enable the Messages
tab *and* "Allow users to send Slash commands and messages from the messages
tab" — without that last checkbox the message box is greyed out. Then install.
</details>

## 3. Configure

Settings come from two files. `.env` holds everything the agents share; each
agent's Slack tokens go in its own `.env.<agent>`, because two apps' tokens
cannot live under the same names.

```bash
cp .env.example .env
```

Put your model key in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

and the Slack pair in `.env.bigtech`:

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

Both files are git-ignored. A real environment variable beats both, so
`SLACK_BOT_TOKEN=... scout run` always wins — handy for a one-off.

> **No Anthropic key?** Run `ollama serve` and `ollama pull llama3.2:3b`
> instead. Scout defaults to the local model when no key is set. It is less
> precise at ranking roles, and free.

Every other setting has a default. [docs/configuration.md](configuration.md)
lists them all.

## 4. Add your résumé

```bash
cp ~/Documents/my_resume.pdf data/
```

The Resume Parser reads the most recently modified file in `data/` (PDF, DOCX,
TXT, or MD), distils it once into a profile — target titles, skills, search
keywords — and every search afterwards is run against that profile. `data/` is
git-ignored.

## 5. Check the setup

```bash
scout doctor
```

```
scout doctor — /Users/you/ScoutAI

  ✓ agent              bigtech — BigTech Agent
  ✓ env files          .env.bigtech > .env (first wins)
  ✓ slack              bot and app tokens present
  ✓ models             default=anthropic; available: anthropic (claude-opus-5), ollama (…)
  ✓ fallback           ollama, for one retry inside a failed turn
  ✓ resume             data/my_resume.pdf
  ✓ state              in memory — history and the parsed resume reset on restart
  ✓ referrals          /Users/you/ScoutAI/state/referrals.json (not created yet)
  ! digest             DIGEST_SLACK_USER unset — `scout digest` has nobody to DM
  ✓ langfuse           off (set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to trace turns)

Ready to start, with 1 thing worth knowing about.
```

`✗` means it will not start; `!` means it will run with less than it could.
Every check is a local file read, so this is also the first thing to run when a
deployed bot goes quiet.

## 6. Talk to it

```bash
scout run
```

Then open Slack, find the app under **Apps** in the sidebar, and DM it in plain
language:

- *"latest amazon jobs"* — recent Amazon AI/ML roles, with links and dates.
- *"anything at databricks or stripe?"* — their Greenhouse boards.
- *"any ML roles at anthropic or openai?"* — hosted boards too, by name.
- *"what's new at nvidia?"* — its Workday site (which reports "Posted 5
  Days Ago" rather than a date, so no date is shown).
- *"what google roles are open?"* — newest first (Google publishes no dates, so
  none are shown).
- *"what time is it?"* / *"where am I?"* — the small tools, useful for checking
  the loop works.

The first message takes longer: that is the résumé being parsed. After that the
profile is cached, and the bot searches, filters, and ranks against it.

A handful of typed commands are intercepted before the model sees them:

| Command | What it does |
|---|---|
| `--claude` | use the Anthropic API (also `--anthropic`) |
| `--ollama` | use the local Ollama model |
| `--api` | use the Meta Llama API (also `--llama`) |
| `--backend` | say which model you are on |
| `--reset` | clear your conversation history and re-read your résumé |
| `--help` | list the commands |

Switching models mid-conversation keeps the thread: history is stored as
provider-agnostic messages, so each model renders the same conversation into its
own format.

Stop the bot with Ctrl-C.

## 7. Tell it who you know

A referral is the strongest signal you have, so it is worth recording. Run the
Referral Window agent — its own Slack app, created exactly as in step 2 with
`.env.referral` for its tokens:

```bash
scout run --agent referral
```

```
You ▸ I have a referral at Stripe — ex-teammate on the platform team
You ▸ who do I know?
You ▸ drop Netflix
You ▸ what's open where I have a referral?
```

That last one is what the list is for. The Referral Window searches every
company on it in a single pass and answers with one merged list — so everything
in the reply is a role you could ask someone about. It also says what it could
*not* see: a careers board that was down, and any company Scout has no board for
(it covers the twenty-odd companies the job agents search — the big-tech
sources, the hosted boards, and the two universities — but not, say, Meta or
Palantir). A gap gets named rather than quietly reading as "nothing open".

The job agents read the same list and cannot edit it, which is the point: a
searching agent with write access eventually records something you never asked
for. They search by *source* and use the list to rank; the Referral Window
searches by *company* and uses the list as its scope.

The list lives in `state/referrals.json`. `--reset` does not touch it, and
neither does a redeploy.

## 8. Turn on the daily digest

```bash
scout digest
```

It runs every job agent, merges the results, and DMs you one briefing. It needs
to know who you are — your own Slack member id (profile → **Copy member ID**):

```bash
# .env
DIGEST_SLACK_USER=U012ABCDEF
```

Run it from `cron` or a systemd timer to get it every morning; the deployed
setup uses a timer at 08:00 local. See [docs/deployment.md](deployment.md).

## Troubleshooting

| What you see | What it usually is |
|---|---|
| `Missing required setting(s): SLACK_BOT_TOKEN` | The tokens are not in `.env.<agent>` for the agent you are running. `scout doctor` names the file it looked in. |
| `not_authed` or `invalid_auth` at startup | The `xoxb-` token is from a different app than the `xapp-` one, or the app was reinstalled and the bot token changed. |
| The bot never replies, and the log shows no DM | The Messages tab is off, or `message.im` is not subscribed. Reinstall the app after fixing either. |
| The message box in Slack is greyed out | **App Home** → allow sending messages from the messages tab. |
| Replies mention no roles at all | Ask for something specific ("amazon jobs in the last week"). Every source filters to AI/ML roles by title; a general search may legitimately find nothing. |
| *"No resume found in data/"* | Nothing in `data/` with a `.pdf`, `.docx`, `.txt`, or `.md` suffix. |
| A new résumé has no effect | The profile is cached per user. Send `--reset`. |
| *"Sorry, I got stuck calling my tools"* | The turn used its tool budget. Narrow the question, or raise `MAX_TOOL_HOPS`. |
| Claude errors, and the reply says the local model answered | The fallback rescued the turn. Check the key, or `--ollama` deliberately. |
| Two bots answer every DM | Two processes share one bot token. Each agent needs its own Slack app and its own `.env.<agent>`. |
| Conversations with two bots are mixed together | They share a `CHECKPOINT_DB`. Give each agent its own path. |

Logs are at `logs/bot.log` (rotated at 5 MB). `LOG_LEVEL=DEBUG scout run` turns
on everything, including the Slack client's own chatter.

## Where to go next

- [Configuration](configuration.md) — every setting and which file it belongs in
- [Architecture](architecture.md) — how a DM becomes a ranked list of roles
- [Extending](extending.md) — add a job source, a tool, or an agent of your own
- [Deployment](deployment.md) — run it somewhere that does not sleep
- [Operations](operations.md) — the digest, metrics, tracing, and alerts
