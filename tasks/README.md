# Task records

One file per task: `tasks/T-NNN.md`, copied from [`_template.md`](_template.md). The front matter is the authoritative state of the task; Slack only mirrors it.

Who writes what:

| Field | Written by | How |
| --- | --- | --- |
| Goal, scope, acceptance criteria, confirmer, sources | Product bot | Task package PR, `status: awaiting-confirmation` |
| `status: confirmed`, `confirmation` | Router | After the confirmer approves the PR on GitHub, or the team chief records an approval-card confirmation |
| Plan | Dev lead | Direct PR, merged by the router |
| `status`, `failures`, `updated`, `reminded_status`, Log lines | Router | Commits to `main` |
| Acceptance section, `status: acceptance-proposed` | Acceptance bot | Acceptance PR, approved by the confirmer |
| `status: paused` and a reason | Team chief | PR; the router merges pause requests without waiting, resuming needs the confirmer |

Statuses and what happens next are defined in [`rules/rules.yml`](../rules/rules.yml).

Code PRs link back to their task with a line `Task: T-NNN` in the PR description.
