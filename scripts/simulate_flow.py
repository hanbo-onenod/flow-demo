"""Walk one task through the whole flow, offline, in a few seconds.

The router is real; GitHub, Slack and the Grok Bots are stand-ins. Lines
starting with "BOT" are what a Grok Bot or Cursor agent would do in the real
demo; lines starting with "->" are what the router did in response.

Usage:
    python scripts/simulate_flow.py            # scenario 1, end to end
    python scripts/simulate_flow.py --breaker  # checks keep failing until the circuit breaker trips
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow import taskfile  # noqa: E402
from flow.fakes import FakeEffects, FakeGH  # noqa: E402
from flow.router import Router  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THREAD = "https://demo.slack.com/archives/C0DEMO/p1730000000123456"


class PrintingEffects(FakeEffects):
    def wake(self, bot, payload):
        super().wake(bot, payload)
        print(f"   -> wake {bot} ({payload['event']})")

    def slack_thread(self, thread_url, text):
        super().slack_thread(thread_url, text)
        print(f"   -> Slack thread: {text}")

    def slack_board(self, text):
        super().slack_board(text)
        print(f"   -> Slack board: {text}")

    def comment(self, number, text):
        super().comment(number, text)
        print(f"   -> comment on #{number}: {text}")

    def merge_code_pr(self, number, message):
        super().merge_code_pr(number, message)
        print(f"   -> router merged code PR #{number} on Mike's behalf")

    def close_pr(self, number, text):
        super().close_pr(number, text)
        print(f"   -> router applied PR #{number} to main and closed it")


def task(status: str, **extra) -> str:
    meta = {
        "id": "T-001", "title": "Make the report export faster", "status": status, "outcome": "",
        "requester": "alice", "confirmer": "carol", "merger": "mike", "risk": "low", "candidate_issue": 1,
        "slack_thread": THREAD, "sources": [THREAD], "failures": 0,
        "updated": taskfile.now_iso(), "reminded_status": "", "confirmation": {},
    }
    meta.update(extra)
    body = (
        "## Goal\n\nCustomers wait ~5 s per export.\n\n## Scope\n\n- In: CSV export query.\n- Out: everything else.\n\n"
        "## Acceptance criteria\n\n- 10,000 orders export in under 1 s (`python scripts/bench_export.py`).\n\n"
        "## Plan\n\n" + extra.pop("plan", "") + "\n\n## Acceptance\n\n" + extra.pop("acceptance", "") + "\n\n## Log\n\n"
    )
    return taskfile.dump(meta, body)


def status(repo_dir: str) -> str:
    return taskfile.load(repo_dir, "T-001")[0]["status"]


def step(title: str) -> None:
    print(f"\n== {title}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--breaker", action="store_true")
    args = parser.parse_args()

    repo_dir = tempfile.mkdtemp(prefix="flow-sim-")
    shutil.copytree(os.path.join(ROOT, "rules"), os.path.join(repo_dir, "rules"))
    os.makedirs(os.path.join(repo_dir, "tasks"))
    os.makedirs(os.path.join(repo_dir, "records"))
    gh = FakeGH()
    fx = PrintingEffects(repo_dir, gh)
    router = Router(repo_dir, "OWNER/flow-demo", fx, gh)
    candidate = {"number": 1, "title": "Report export is too slow", "html_url": "https://github.com/OWNER/flow-demo/issues/1",
                 "body": f"Wanted: faster export\nSlack thread: {THREAD}\n", "labels": [{"name": "candidate"}, {"name": "source:idea"}]}

    step("1. Alice asks Team Chief in #demo-需求: 'Export is too slow, can we speed it up?'")
    print("BOT Team Chief opens candidate issue #1")
    router.handle("issues", {"action": "opened", "issue": candidate})

    step("2. Requirement owner adds the 'accepted' label")
    candidate["labels"].append({"name": "accepted"})
    router.handle("issues", {"action": "labeled", "label": {"name": "accepted"}, "issue": candidate})

    step("3. Product Bot clarifies, then opens the task package PR #2")
    pr2 = gh.add_pr(2, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task("awaiting-confirmation")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr2})

    step("4. GATE: Carol (confirmer) replies in Slack `team chief 确认 T-001`")
    print("BOT Team Chief relays it to PR #2 as /confirmed-via-slack")
    slack_ok = f"/confirmed-via-slack by @carol at {taskfile.now_iso()} link:{THREAD}"
    router.handle("issue_comment", {"issue": {"number": 2, "pull_request": {}, "labels": []},
                                    "comment": {"user": {"login": "flow-demo-bot"}, "body": slack_ok, "html_url": "c"}})
    print(f"   status now: {status(repo_dir)}")

    step("5. Dev Lead writes the plan (PR #3, status unchanged) and starts a Cursor cloud agent")
    pr3 = gh.add_pr(3, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task("in-progress", plan="1. Replace per-row lookup with a join (agent A)")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr3})

    step("6. Cursor agent opens code PR #4; CI fails on the first try")
    pr4 = gh.add_pr(4, "cursor-agent", ["app/export_report.py"], body="Use a join\n\nTask: T-001\n", sha="c0ffee1")
    gh.checks["c0ffee1"] = [("completed", "failure")]
    ci = {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 4}]}}
    router.handle("workflow_run", ci)

    if args.breaker:
        for n, sha in enumerate(["c0ffee2", "c0ffee3"], start=2):
            step(f"7.{n} Agent pushes another fix; CI fails again")
            pr4["head"]["sha"] = sha
            gh.checks[sha] = [("completed", "failure")]
            router.handle("workflow_run", ci)
        print(f"\n   status now: {status(repo_dir)}  (circuit breaker; a person decides what next)")
        return report(repo_dir)

    step("7. Agent pushes a fix; CI passes, Bugbot is still running")
    pr4["head"]["sha"] = "c0ffee2"
    gh.checks["c0ffee2"] = [("completed", "success"), ("in_progress", None)]
    router.handle("workflow_run", ci)
    print("   (router waits)")

    step("8. Hourly sweep: Bugbot has finished too")
    gh.checks["c0ffee2"] = [("completed", "success"), ("completed", "neutral")]
    router.handle("schedule", {})
    print(f"   status now: {status(repo_dir)}")

    step("9. GATE: Mike (merger) replies in Slack `team chief 合并 #4` (low risk, so allowed)")
    print("BOT Team Chief relays it to PR #4 as /merge-via-slack")
    merge = f"/merge-via-slack by @mike at {taskfile.now_iso()} link:{THREAD}"
    router.handle("issue_comment", {"issue": {"number": 4, "pull_request": {}, "labels": []},
                                    "comment": {"user": {"login": "flow-demo-bot"}, "body": merge, "html_url": "c"}})

    step("10. Acceptance Bot measures on main and opens acceptance PR #5")
    measured = '{"orders": 10000, "runs": 5, "best_seconds": 0.08, "median_seconds": 0.09}'
    pr5 = gh.add_pr(5, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task(
        "acceptance-proposed", outcome="met", acceptance=f"- Under 1 s: met. Measured: `{measured}`")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr5})

    step("11. GATE: Carol approves the acceptance PR on GitHub (both ways work)")
    review = {"state": "APPROVED", "user": {"login": "carol"}, "html_url": "https://github.com/OWNER/flow-demo/pull/5#review", "submitted_at": taskfile.now_iso()}
    router.handle("pull_request_review", {"review": review, "pull_request": pr5})
    print(f"   status now: {status(repo_dir)}")

    step("12. Nobody touches anything for 5 hours: timeout sweep on a second, stalled task")
    with open(taskfile.path_for(repo_dir, "T-002"), "w", encoding="utf-8") as f:
        stale = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        f.write(task("awaiting-merge", id="T-002", title="Fix CSV encoding", updated=stale))
    router.handle("schedule", {})
    return report(repo_dir)


def report(repo_dir: str) -> int:
    print("\n== Records written by the router")
    for sub in ("problems.jsonl", "summaries/T-001.md"):
        path = os.path.join(repo_dir, "records", sub)
        if os.path.exists(path):
            print(f"--- records/{sub}")
            print(open(path, encoding="utf-8").read().rstrip())
    print("--- tasks/T-001.md log")
    print(taskfile.load(repo_dir, "T-001")[1].split("## Log", 1)[1].strip())
    print(f"\n(simulation files left in {repo_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
