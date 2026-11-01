# Bot rulebooks

Each Grok Bot reads its own rulebook from this folder **every time it wakes up**, before doing anything else. The Grok-side setup only says "read your rulebook"; the real instructions live here so they are versioned and reviewed.

Only people can change these files. Process-improvement proposals that change how a bot works are PRs against this folder.

| Bot | Rulebook | Woken by |
| --- | --- | --- |
| Team chief | [team-chief.md](team-chief.md) | People in Slack |
| Product | [product.md](product.md) | Router webhook |
| Dev lead | [dev-lead.md](dev-lead.md) | Router webhook |
| Acceptance | [acceptance.md](acceptance.md) | Router webhook |
| Outer loop | [outer-loop.md](outer-loop.md) | Router webhook, daily |
| Process improvement | [process-improvement.md](process-improvement.md) | Router webhook, weekly |
| Personal chief | [personal-chief.md](personal-chief.md) | You, in a direct message |

## Rules every bot follows

1. **Read first.** Your rulebook, then the task file or issue named in the wake-up payload. Do not rely on memory for task state; the repository is the source of truth.
2. **Write through GitHub only**, using the `flow-demo-bot` account: PRs, issues, issue comments. Never push to `main`, never merge, never approve a PR.
3. **Never confirm for a person.** Confirmations, merges and acceptance are made by people.
4. **Say who you are in Slack.** On this plan every bot posts as the account owner, so start every Slack message with your name in brackets, for example `[Product Bot]`.
5. **Reply in the language the person used.** Repository files are written in English; quotes from sources stay in their original language.
6. **Stay in scope.** If a request goes beyond the confirmed scope, stop and ask the team chief to raise an exception. Do not quietly do extra work.
7. **Leave a trail.** When something goes wrong (you were corrected, you got stuck, a card expired), add a line to the task's Log section in your next PR, starting with `PROBLEM:`.
8. **Ask people in Slack, not in the Grok app.** When you need a person's answer or decision, ask in the task's Slack thread and end with the exact reply that will move things on, for example ``Reply `team chief 确认 T-003` ``. Do not wait for an answer inside your own run: finish, and the reply wakes the Team Chief, who records it (see `team-chief.md`).
9. **Never write the words "team chief" in Slack except in your `[Team Chief]` prefix.** They wake the Team Chief.

## Wake-up payload

The router sends this JSON to the bot's Grok webhook:

```json
{
  "bot": "product",
  "event": "candidate.accepted",
  "repo": "hanbo-onenod/flow-demo",
  "task": "T-001",
  "issue": "https://github.com/hanbo-onenod/flow-demo/issues/12",
  "pr": null,
  "slack_thread": "https://WORKSPACE.slack.com/archives/C0123/p1730000000000000",
  "rulebook": "bots/product.md",
  "note": "Requirement owner accepted this candidate."
}
```

Fields that do not apply are `null`.
