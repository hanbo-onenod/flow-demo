from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from flow import taskfile
from flow.effects import slack_thread_target
from flow.fakes import FakeEffects, FakeGH
from flow.router import Router

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THREAD = "https://demo.slack.com/archives/C0DEMO/p1730000000123456"
NOW = datetime(2026, 11, 3, 1, 5, tzinfo=timezone.utc)  # Tuesday 01:05 UTC


def task_text(status: str, **extra) -> str:
    meta = {
        "id": "T-001",
        "title": "Faster export",
        "status": status,
        "outcome": "",
        "requester": "alice",
        "confirmer": "carol",
        "merger": "mike",
        "slack_thread": THREAD,
        "failures": 0,
        "updated": "2026-11-03T00:00:00Z",
        "reminded_status": "",
    }
    meta.update(extra)
    return taskfile.dump(meta, "## Goal\n\nFaster.\n\n## Log\n\n")


@pytest.fixture
def env(tmp_path):
    shutil.copytree(os.path.join(REPO_ROOT, "rules"), tmp_path / "rules")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "records").mkdir()
    gh = FakeGH()
    fx = FakeEffects(str(tmp_path), gh)
    router = Router(str(tmp_path), "o/flow-demo", fx, gh, now=NOW)
    router.bot_logins = {"flow-demo-bot"}
    return router, gh, fx, tmp_path


def write_task(tmp_path, status, **extra):
    (tmp_path / "tasks" / "T-001.md").write_text(task_text(status, **extra), encoding="utf-8")


def status_of(tmp_path):
    return taskfile.parse((tmp_path / "tasks" / "T-001.md").read_text(encoding="utf-8"))[0]


def wakes(fx):
    return [(c[1], c[2]["event"]) for c in fx.calls if c[0] == "wake"]


def issue(labels, number=7, body=f"Wanted: x\nSlack thread: {THREAD}\n"):
    return {"number": number, "title": "Export is slow", "body": body,
            "html_url": f"https://github.com/o/flow-demo/issues/{number}", "labels": [{"name": n} for n in labels]}


def test_slack_permalink_parsing():
    assert slack_thread_target(THREAD) == ("C0DEMO", "1730000000.123456")
    reply = THREAD + "?thread_ts=1729999999.000100&cid=C0DEMO"
    assert slack_thread_target(reply) == ("C0DEMO", "1729999999.000100")


def test_minutes_wake_product_and_ideas_notify_owner(env):
    router, _, fx, _ = env
    router.handle("issues", {"action": "opened", "issue": issue(["candidate", "source:minutes"])})
    assert wakes(fx) == [("product", "intake.opened")]
    fx.calls.clear()
    router.handle("issues", {"action": "opened", "issue": issue(["candidate", "source:idea"])})
    assert wakes(fx) == []
    assert fx.calls[0][0] == "slack_thread" and fx.calls[0][1] == THREAD


def test_accepting_a_candidate_wakes_product(env):
    router, _, fx, _ = env
    router.handle("issues", {"action": "labeled", "label": {"name": "accepted"}, "issue": issue(["candidate", "accepted"])})
    assert wakes(fx) == [("product", "candidate.accepted")]


def test_human_reply_on_clarifying_candidate_wakes_product_but_bot_reply_does_not(env):
    router, _, fx, _ = env
    labels = ["candidate", "clarifying"]
    router.handle("issue_comment", {"issue": issue(labels), "comment": {"user": {"login": "flow-demo-bot"}, "body": "Q1?"}})
    assert wakes(fx) == []
    router.handle("issue_comment", {"issue": issue(labels), "comment": {"user": {"login": "alice"}, "body": "Under 1s"}})
    assert wakes(fx) == [("product", "candidate.human_reply")]


def open_package_pr(gh):
    return gh.add_pr(10, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task_text("awaiting-confirmation")})


def test_task_package_waits_for_confirmer_then_starts_dev_lead(env):
    router, gh, fx, tmp_path = env
    pr = open_package_pr(gh)
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    assert "close_pr" not in fx.names()
    assert any(c[0] == "slack_thread" and "carol" in c[2] for c in fx.calls)

    review = {"state": "APPROVED", "user": {"login": "carol"}, "html_url": "https://github.com/r/1", "submitted_at": "2026-11-03T01:00:00Z"}
    router.handle("pull_request_review", {"review": review, "pull_request": pr})
    meta = status_of(tmp_path)
    assert meta["status"] == "in-progress"
    assert meta["confirmation"]["via"] == "github-review" and meta["confirmation"]["by"] == "carol"
    assert wakes(fx) == [("dev_lead", "status.confirmed")]

    # The merge's own `closed` event arrives later; nothing must happen twice.
    fx.calls.clear()
    pr["merged"] = True
    router.handle("pull_request", {"action": "closed", "pull_request": pr})
    assert wakes(fx) == []


def test_only_the_confirmer_can_confirm(env):
    router, gh, fx, tmp_path = env
    pr = open_package_pr(gh)
    review = {"state": "APPROVED", "user": {"login": "mallory"}, "html_url": "u", "submitted_at": None}
    router.handle("pull_request_review", {"review": review, "pull_request": pr})
    assert "close_pr" not in fx.names()
    assert not (tmp_path / "tasks" / "T-001.md").exists()
    assert "wrong-confirmer" in (tmp_path / "records" / "problems.jsonl").read_text()


def test_approval_card_confirmation_recorded_by_bot(env):
    router, gh, fx, tmp_path = env
    pr = open_package_pr(gh)
    pr_issue = {"number": 10, "pull_request": {}, "labels": []}
    fake = {"user": {"login": "alice"}, "body": "/confirmed-via-card by @carol at 2026-11-03T01:00:00Z card:1", "html_url": "c0"}
    router.handle("issue_comment", {"issue": pr_issue, "comment": fake})
    assert "close_pr" not in fx.names(), "only the bot account may record card confirmations"
    real = dict(fake, user={"login": "flow-demo-bot"}, html_url="c1")
    router.handle("issue_comment", {"issue": pr_issue, "comment": real})
    meta = status_of(tmp_path)
    assert meta["status"] == "in-progress"
    assert meta["confirmation"] == {"via": "approval-card", "by": "carol", "at": "2026-11-03T01:00:00Z", "evidence": "c1"}


def test_bot_cannot_jump_status(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    pr = gh.add_pr(11, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task_text("closed")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    assert fx.names() == ["comment"]
    assert status_of(tmp_path)["status"] == "in-progress"


def test_plan_update_and_pause_are_merged_without_waiting(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    pr = gh.add_pr(12, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task_text("in-progress")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    assert "close_pr" in fx.names()
    fx.calls.clear()
    pr = gh.add_pr(13, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task_text("paused")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    assert "close_pr" in fx.names()
    assert any(c[0] == "slack_thread" and "carol" in c[2] for c in fx.calls)


def code_pr(gh, number=20, sha="abc1234"):
    return gh.add_pr(number, "cursor-agent", ["app/export_report.py"], body="Fix\nTask: T-001\n", sha=sha)


def test_failures_wake_dev_lead_then_circuit_breaker_pauses(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    for attempt in range(3):
        pr = code_pr(gh, sha=f"sha-{attempt}")
        gh.checks[f"sha-{attempt}"] = [("completed", "failure")]
        router.handle("workflow_run", {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}})
    assert wakes(fx) == [("dev_lead", "code_pr.checks_failed")] * 2
    meta = status_of(tmp_path)
    assert meta["status"] == "paused" and meta["failures"] == 3
    kinds = [json.loads(l)["kind"] for l in (tmp_path / "records" / "problems.jsonl").read_text().splitlines()]
    assert kinds == ["check-failure", "check-failure", "circuit-break"]


def test_same_commit_is_not_counted_twice(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    code_pr(gh)
    gh.checks["abc1234"] = [("completed", "failure")]
    run = {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}}
    router.handle("workflow_run", run)
    router.handle("workflow_run", run)
    assert status_of(tmp_path)["failures"] == 1


def test_passing_checks_wait_for_all_then_call_merger(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    code_pr(gh)
    gh.checks["abc1234"] = [("completed", "success"), ("in_progress", None)]
    run = {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}}
    router.handle("workflow_run", run)
    assert status_of(tmp_path)["status"] == "in-progress"
    gh.checks["abc1234"] = [("completed", "success"), ("completed", "neutral")]
    router.handle("schedule", {})  # the hourly sweep picks it up
    assert status_of(tmp_path)["status"] == "awaiting-merge"
    assert any(c[0] == "slack_thread" and "mike" in c[2] for c in fx.calls)


def test_acceptance_starts_only_when_all_code_prs_are_merged(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "awaiting-merge")
    first, second = code_pr(gh, 20, "s1"), code_pr(gh, 21, "s2")
    first["merged"] = True
    router.handle("pull_request", {"action": "closed", "pull_request": first})
    assert wakes(fx) == []
    second["merged"] = True
    router.handle("pull_request", {"action": "closed", "pull_request": second})
    assert wakes(fx) == [("acceptance", "code_pr.merged")]
    assert status_of(tmp_path)["status"] == "awaiting-acceptance"


def test_acceptance_approval_closes_task_and_writes_summary(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "awaiting-acceptance")
    head = task_text("acceptance-proposed", outcome="met")
    pr = gh.add_pr(30, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": head})
    review = {"state": "APPROVED", "user": {"login": "carol"}, "html_url": "r", "submitted_at": None}
    router.handle("pull_request_review", {"review": review, "pull_request": pr})
    meta = status_of(tmp_path)
    assert meta["status"] == "closed" and meta["outcome"] == "met"
    assert "Outcome: met" in (tmp_path / "records" / "summaries" / "T-001.md").read_text()
    assert (tmp_path / "records" / "scorecards" / "T-001.md").exists()
    assert any(c[0] == "slack_thread" and "alice" in c[2] for c in fx.calls)  # requester told


def test_timeout_reminds_once(env):
    router, _, fx, tmp_path = env
    write_task(tmp_path, "awaiting-merge", updated=(NOW - timedelta(hours=5)).isoformat())
    router.handle("schedule", {})
    router.handle("schedule", {})
    reminders = [c for c in fx.calls if c[0] == "slack_thread" and "over 4h" in c[2]]
    assert len(reminders) == 1


def test_daily_schedule_wakes_outer_loop_at_its_hour(env):
    router, _, fx, _ = env
    router.handle("schedule", {})
    assert wakes(fx) == [("outer_loop", "schedule.outer_loop")]
    router.now = NOW.replace(hour=5)
    fx.calls.clear()
    router.handle("schedule", {})
    assert wakes(fx) == []


def test_manual_dispatch(env):
    router, _, fx, _ = env
    router.handle("workflow_dispatch", {"inputs": {"job": "process_improvement"}})
    assert wakes(fx) == [("process_improvement", "schedule.process_improvement")]


def test_bot_pr_from_stale_branch_keeps_router_fields_and_log(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress", failures=2, confirmation={"via": "github-review", "by": "carol"})
    meta, body = taskfile.load(str(tmp_path), "T-001")
    taskfile.save(str(tmp_path), "T-001", meta, taskfile.append_log(body, "router: something happened"))
    stale = task_text("in-progress").replace("## Log\n\n", "## Log\n\n- dev lead: plan written\n")
    pr = gh.add_pr(14, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": stale})
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    meta, body = taskfile.load(str(tmp_path), "T-001")
    assert meta["failures"] == 2 and meta["confirmation"]["by"] == "carol"
    assert "router: something happened" in body and "dev lead: plan written" in body
    assert "close_pr" in fx.names()
