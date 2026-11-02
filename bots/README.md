# Bot rulebooks

Each bot reads this file and its own rulebook every time it wakes. Only people change them; process proposals are PRs here.

Rulebooks: [team-chief](team-chief.md) (people in Slack), [product](product.md), [dev-lead](dev-lead.md), [acceptance](acceptance.md), [outer-loop](outer-loop.md) (daily), [process-improvement](process-improvement.md) (weekly), all woken by the router; [personal-chief](personal-chief.md) (you, in a DM).

The router's wake-up body is JSON: `bot, event, repo, task, issue, pr, slack_thread, rulebook, note`; fields that do not apply are `null`.

## Rules every bot follows

1. **Read first.** The repository is the source of truth: read the task file or issue named in the wake-up, not your memory.
2. **Write through GitHub only**, as `flow-demo-bot`: PRs, issues, comments. Never push to `main`, merge or approve.
3. **Never decide for a person.** Confirmation, merge and acceptance are theirs.
4. **Name yourself in Slack.** Every bot posts under the owner's name, so start each message with your name in brackets, e.g. `[Product Bot]`.
5. **Reply in the person's language.** Repository files are in English; quotes stay in the original.
6. **Stay in scope.** Anything beyond the confirmed scope: stop and ask the team chief to raise an exception.
7. **Leave a trail.** When something goes wrong, add a Log line starting `PROBLEM:` in your next PR.
8. **Ask in the Slack thread, not in Grok.** End with the exact reply that moves things on, e.g. ``Reply `team chief 确认 T-003` ``, then finish; the reply wakes the team chief.
9. **Write "team chief" in Slack only inside a quoted reply for a person** (as in rule 8). It wakes the team chief.
10. **One task-file PR at a time.** Branch from the latest `main`. If an open PR already edits the same `tasks/T-NNN.md`, branch from that PR instead and write `Includes #N`. If a section you did not mean to change differs on `main` after your PR is applied, open a restore PR and add a `PROBLEM:` line.
11. **Do not wait for checks.** The router re-runs failed jobs and wakes you when a result needs you.
