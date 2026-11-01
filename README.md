# flow-demo

A one-month demo of an AI-assisted team workflow: people talk to Grok Bots in Slack, GitHub Actions moves tasks forward by fixed rules, Cursor cloud agents write the code, and people decide at four gates (confirm, merge, accept, exceptions).

Everything in this repository is fictional demo material. Do not put real code or customer data here.

## Layout

| Path | What |
| --- | --- |
| `app/` | The toy "report export" tool, with planted problems (slow export, CSV garbled in Excel, missing features, a flaky test) |
| `tests/` | Tests for the toy app |
| `scripts/bench_export.py` | Measures export time; used for acceptance |
| `tasks/` | One file per task; the front matter is the task's authoritative state |
| `rules/rules.yml` | What happens next for each event. Read by the router; changed only by people |
| `rules/people.yml` | Who plays which role, Slack IDs |
| `bots/` | Rulebooks the Grok Bots read every time they wake up |
| `flow/` | The router (Python) and its tests |
| `records/` | Problem records, task summaries, scorecards (written by the router) |
| `inputs/` | Demo material: meeting minutes, a PRD, feedback issues |
| `grok/setup.md` | Text to paste into Grok Bot when creating the bots |
| `slack/manifest.yml` | Slack app for the router's status messages |

## How a task moves

```
Slack request -> Team Chief opens a candidate issue -> requirement owner labels it "accepted"
  -> Product Bot writes tasks/T-NNN.md in a PR -> confirmer approves (GitHub review or approval card)
  -> router marks it confirmed and wakes Dev Lead -> Cursor cloud agents open code PRs
  -> CI + Bugbot -> merger merges -> router wakes Acceptance Bot -> acceptance PR
  -> confirmer approves -> router closes the task and writes a summary
```

Every arrow that is not a person or a bot is the router (`.github/workflows/flow.yml` -> `flow/router.py`).

## Run locally

```bash
pip install pyyaml pytest
pytest -q tests flow/tests
python scripts/bench_export.py
python -m flow.router --event issues --payload some-event.json --dry-run
```

## Required GitHub settings

- Secrets: `FLOW_DEPLOY_KEY` (deploy key with write access), `SLACK_BOT_TOKEN`, `GROK_WEBHOOK_PRODUCT`, `GROK_WEBHOOK_DEV_LEAD`, `GROK_WEBHOOK_ACCEPTANCE`, `GROK_WEBHOOK_OUTER_LOOP`, `GROK_WEBHOOK_PROCESS_IMPROVEMENT`, and for each of those a `GROK_WEBHOOK_KEY_<BOT>` with the routine's webhook key (sent as `Authorization: Bearer <key>`). A missing secret makes the router skip that channel and log it.
- Variables: `SLACK_BOARD_CHANNEL` (channel ID), optional `FLOW_DEMO_FLAKY_RATE`.
- Ruleset on `main`: require a pull request with 1 approval and the `test` check; bypass list: the deploy key (always) and repository admins (pull requests only).
Setup check 5.
