# Security

## Reporting a vulnerability

Please report privately, not in a public issue: open a
[security advisory](https://github.com/nikhil-kunapareddy/ScoutAI/security/advisories/new)
on this repository. Include what you did, what happened, and the commit you
were on. I will confirm receipt and, if the report is valid, fix it and credit
you in the advisory unless you would rather not be named.

Please do not include anyone's real Slack tokens, API keys, or résumé in a
report — describe the shape of the problem and I can reproduce it.

## Supported versions

This is a single-maintainer project with no release branches: fixes land on
`main`. Run what `main` says.

## What this project handles

Scout runs as *your* bot, on *your* box, with *your* résumé. The sensitive
things it touches:

| Thing | Where it lives | How it is protected |
|---|---|---|
| Slack bot + app tokens | `.env`, `.env.<agent>`, or the platform's secret store | git-ignored; excluded from the Docker image by `.dockerignore`; injected as environment variables in production |
| Model provider API keys | same | same |
| Your résumé | `data/`, and the parsed profile in the checkpoint database | git-ignored; `data/` is baked into the image only if you choose to build it that way |
| Conversation history | in memory, or SQLite at `CHECKPOINT_DB` | local to the host; `--reset` deletes a thread |
| Your referral list | `state/referrals.json` | local to the host; excluded from `deploy.sh`'s rsync |

Design choices that follow from that:

- **Socket Mode, not a web service.** The bot holds an outbound websocket and
  listens on no port, so there is no inbound surface to attack and no public
  URL to find.
- **DMs only.** The adapter ignores channels, other bots, and message edits
  (`channel_type == "im"`), so it cannot be provoked from a shared channel.
- **The model is never told who the user is.** Tools that need an identity take
  an injected `config`, which LangChain keeps out of the schema the model sees.
- **Nothing raises with a secret in it.** Error text echoed back to Slack is
  truncated, and settings are validated by name rather than by value.

## Things to know before you deploy

- **Tracing sends prompts and completions off your machine**, including the
  résumé profile in the system prompt. `LANGFUSE_HIDE_CONTENT` keeps the call
  tree without the content. Tracing is off unless configured: the Langfuse key
  pair is the switch, and half a pair leaves it off.
- **`get_location` shares the host's public IP** with `ip-api.com`, over plain
  HTTP on the free tier. Drop `location` from an agent's `tool_modules` if that
  is not a trade you want.
- **Job tools fetch untrusted text** from employer endpoints and hand it to a
  model. It is treated as data, never as instructions, but it is worth knowing
  that a job title is attacker-influenced input.
- **One Slack app per agent.** Two bots sharing a token both answer every DM;
  two bots sharing a `CHECKPOINT_DB` interleave your conversations into one
  history.
