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
    notice = next(c for c in fx.calls if c[0] == "slack_thread")
    assert notice[1] == THREAD


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


def feedback_issue(number, *labels):
    return {"number": number, "title": "slow", "labels": [{"name": n} for n in ("feedback", *labels)]}


def test_daily_schedule_wakes_outer_loop_at_its_hour(env):
    router, gh, fx, _ = env
    gh.issues = [feedback_issue(5)]
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


def test_candidate_notification_says_how_to_answer_in_slack(env):
    router, _, fx, _ = env
    router.handle("issues", {"action": "opened", "issue": issue(["candidate", "source:idea"])})
    assert "team chief 接受" in next(c for c in fx.calls if c[0] == "slack_thread")[2]


def test_slack_confirmation_is_recorded_with_its_permalink(env):
    router, gh, fx, tmp_path = env
    pr = open_package_pr(gh)
    link = "https://onenod.slack.com/archives/C0DEMO/p1730000000999999"
    body = f"/confirmed-via-slack by @carol at 2026-11-03T01:00:00Z link:{link}"
    router.handle("issue_comment", {"issue": {"number": 10, "pull_request": {}, "labels": []},
                                    "comment": {"user": {"login": "flow-demo-bot"}, "body": body, "html_url": "c"}})
    meta = status_of(tmp_path)
    assert meta["status"] == "in-progress"
    assert meta["confirmation"]["via"] == "slack" and meta["confirmation"]["evidence"] == link


def test_relayed_slack_answer_wakes_product_but_other_bot_comments_do_not(env):
    router, _, fx, _ = env
    labels = ["candidate", "clarifying"]
    bot = {"login": "flow-demo-bot"}
    router.handle("issue_comment", {"issue": issue(labels), "comment": {"user": bot, "body": "Questions: 1. ..."}})
    assert wakes(fx) == []
    router.handle("issue_comment", {"issue": issue(labels), "comment": {"user": bot, "body": "Answer from @alice (via Slack, https://x): under 1 s"}})
    assert wakes(fx) == [("product", "candidate.human_reply")]


def ready_to_merge(gh, tmp_path, risk="low", files=("app/export_report.py",)):
    write_task(tmp_path, "awaiting-merge", risk=risk, checked_sha="abc1234")
    pr = code_pr(gh)
    gh.files[20] = list(files)
    return pr


def merge_comment(router, login="mike"):
    body = f"/merge-via-slack by @{login} at 2026-11-03T02:00:00Z link:https://onenod.slack.com/archives/C0DEMO/p1"
    router.handle("issue_comment", {"issue": {"number": 20, "pull_request": {}, "labels": []},
                                    "comment": {"user": {"login": "flow-demo-bot"}, "body": body, "html_url": "c"}})


def test_low_risk_pr_is_merged_from_slack_and_acceptance_starts(env):
    router, gh, fx, tmp_path = env
    ready_to_merge(gh, tmp_path)
    merge_comment(router)
    assert "merge_code_pr" in fx.names()
    assert wakes(fx) == [("acceptance", "code_pr.merged")]
    meta, body = taskfile.load(str(tmp_path), "T-001")
    assert meta["status"] == "awaiting-acceptance" and "merged via Slack by @mike" in body


@pytest.mark.parametrize("risk,files,login,reason", [
    ("medium", ("app/export_report.py",), "mike", "medium risk"),
    ("low", ("app/x.py", "flow/router.py"), "mike", "it changes flow/router.py"),
    ("low", ("app/export_report.py",), "carol", "only the merger @mike"),
])
def test_slack_merge_is_refused_when_policy_says_github(env, risk, files, login, reason):
    router, gh, fx, tmp_path = env
    ready_to_merge(gh, tmp_path, risk, files)
    merge_comment(router, login)
    assert "merge_code_pr" not in fx.names()
    assert any(c[0] == "slack_thread" and reason in c[2] for c in fx.calls)
    assert status_of(tmp_path)["status"] == "awaiting-merge"
    assert "slack-merge-refused" in (tmp_path / "records" / "problems.jsonl").read_text()


def test_slack_merge_waits_for_checks_on_the_latest_commit(env):
    router, gh, fx, tmp_path = env
    pr = ready_to_merge(gh, tmp_path)
    pr["head"]["sha"] = "newcommit"
    merge_comment(router)
    assert "merge_code_pr" not in fx.names()
    assert any(c[0] == "comment" and "checks have not all passed" in c[2] for c in fx.calls)


def test_late_bugbot_result_is_picked_up_without_waiting_for_the_sweep(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    code_pr(gh)
    gh.checks["abc1234"] = [("completed", "success"), ("in_progress", None)]
    router.handle("workflow_run", {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}})
    assert status_of(tmp_path)["status"] == "in-progress"
    gh.checks["abc1234"] = [("completed", "success"), ("completed", "success")]
    router.handle("check_suite", {"check_suite": {"status": "completed", "pull_requests": [{"number": 20}]}})
    assert status_of(tmp_path)["status"] == "awaiting-merge"


def test_rerun_on_the_same_commit_is_evaluated(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    code_pr(gh)
    run = {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}}
    gh.checks["abc1234"] = [("completed", "failure", 1), ("completed", "success", 2)]
    router.handle("workflow_run", run)
    gh.checks["abc1234"] = [("completed", "success", 3), ("completed", "success", 2)]  # re-run of CI
    router.handle("workflow_run", run)
    meta = status_of(tmp_path)
    assert meta["failures"] == 1 and meta["status"] == "awaiting-merge"


FEEDBACK_BODY = "Wanted: readable CSV\nSlack thread: none (from feedback, no Slack thread)\n\nreports: 5\n"


def test_candidate_without_thread_gets_one_in_the_requests_channel(env):
    router, _, fx, _ = env
    cand = issue(["candidate", "source:feedback"], number=21, body=FEEDBACK_BODY)
    router.handle("issues", {"action": "opened", "issue": cand})
    new_link = "https://slack.com/archives/C0REQ/p1730000000000001"
    update = next(c for c in fx.calls if c[0] == "update_issue_body")
    assert f"Slack thread: {new_link}\n" in update[2] and "none" not in update[2] and "reports: 5" in update[2]
    assert any(c[0] == "slack_thread" and c[1] == new_link and "team chief 接受" in c[2] for c in fx.calls)


def test_candidate_with_a_thread_keeps_it(env):
    router, _, fx, _ = env
    router.handle("issues", {"action": "opened", "issue": issue(["candidate", "source:idea"])})
    assert "slack_open_thread" not in fx.names()


def test_threads_job_backfills_existing_candidates(env):
    router, gh, fx, _ = env
    gh.issues = [issue(["candidate", "source:feedback"], number=21, body=FEEDBACK_BODY),
                 issue(["candidate", "source:idea"], number=7)]
    router.handle("workflow_dispatch", {"inputs": {"job": "threads"}})
    assert [c[1] for c in fx.calls if c[0] == "update_issue_body"] == [21]


def test_candidate_labelled_after_creation_is_handled_once(env):
    router, gh, fx, _ = env
    bare = issue([], number=30)
    gh.issues = [issue(["candidate", "source:idea"], number=30)]  # labels added after creation
    router.handle("issues", {"action": "opened", "issue": bare})
    router.handle("issues", {"action": "labeled", "label": {"name": "candidate"}, "issue": bare})
    router.handle("issues", {"action": "labeled", "label": {"name": "source:idea"}, "issue": bare})
    notices = [c for c in fx.calls if c[0] == "slack_thread" and "#30" in c[2]]
    assert len(notices) == 1
    assert ("add_label", 30, "flow:seen") in fx.calls


def test_deferred_candidate_bothers_nobody(env):
    router, gh, fx, _ = env
    gh.issues = [issue(["candidate", "deferred", "source:minutes-split"], number=31)]
    router.handle("issues", {"action": "labeled", "label": {"name": "deferred"}, "issue": gh.issues[0]})
    assert not [c for c in fx.calls if c[0] == "slack_thread"]
    assert any(c[0] == "slack_board" and "candidate.deferred" in c[1] for c in fx.calls)


def test_pause_remembers_status_and_confirmer_resumes_via_slack(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress", candidate_issue=37)
    pr = gh.add_pr(50, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task_text("paused")})
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    assert status_of(tmp_path)["paused_from"] == "in-progress"
    fx.calls.clear()

    def resume(login):
        body = f"/resume-via-slack T-001 by @{login} at 2026-11-03T03:00:00Z link:https://slack/x"
        router.handle("issue_comment", {"issue": {"number": 37, "labels": [{"name": "candidate"}]},
                                        "comment": {"user": {"login": "flow-demo-bot"}, "body": body, "html_url": "c"}})

    resume("mallory")
    assert status_of(tmp_path)["status"] == "paused" and wakes(fx) == []
    resume("carol")
    meta = status_of(tmp_path)
    assert meta["status"] == "in-progress" and not meta["paused_from"]
    assert wakes(fx) == [("dev_lead", "status.resumed")]


def test_waiting_task_package_pr_gets_one_timeout_reminder(env):
    router, gh, fx, _ = env
    pr = open_package_pr(gh)
    pr["created_at"] = (NOW - timedelta(hours=3)).isoformat()
    router.handle("schedule", {})
    router.handle("schedule", {})
    reminders = [c for c in fx.calls if c[0] == "slack_thread" and "awaiting-confirmation for over 2h" in c[2]]
    assert len(reminders) == 1 and "carol" in reminders[0][2]
    assert ("add_label", 10, "flow:reminded") in fx.calls


def test_young_task_package_pr_is_not_reminded(env):
    router, gh, fx, _ = env
    open_package_pr(gh)["created_at"] = (NOW - timedelta(minutes=30)).isoformat()
    router.handle("schedule", {})
    assert not [c for c in fx.calls if c[0] == "slack_thread"]


def test_stuck_acceptance_reminds_the_admin(env):
    router, _, fx, tmp_path = env
    write_task(tmp_path, "awaiting-acceptance", updated=(NOW - timedelta(hours=3)).isoformat())
    router.handle("schedule", {})
    assert any(c[0] == "slack_thread" and "awaiting-acceptance for over 2h" in c[2] for c in fx.calls)


def test_stale_pause_pr_does_not_overwrite_a_newer_plan(env):
    """Replays T-004: the plan PR lands, then a pause PR based on the older file."""
    router, gh, fx, tmp_path = env
    original = task_text("in-progress").replace("## Log", "## Plan\n\nFilled in by the dev lead.\n\n## Log")
    planned = original.replace("Filled in by the dev lead.", "1. Add --start/--end (agent A)")
    paused_from_old = original.replace("status: in-progress", "status: paused").replace(
        "## Log\n\n", "## Log\n\n- Team Chief: PAUSED: scope change\n")
    (tmp_path / "tasks" / "T-001.md").write_text(original, encoding="utf-8")

    plan_pr = gh.add_pr(60, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": planned},
                        bases={"tasks/T-001.md": original})
    router.handle("pull_request", {"action": "opened", "pull_request": plan_pr})
    pause_pr = gh.add_pr(61, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": paused_from_old},
                         bases={"tasks/T-001.md": original})
    router.handle("pull_request", {"action": "opened", "pull_request": pause_pr})

    meta, body = taskfile.load(str(tmp_path), "T-001")
    assert meta["status"] == "paused" and meta["paused_from"] == "in-progress"
    assert "1. Add --start/--end (agent A)" in body          # the newer plan survived
    assert "PAUSED: scope change" in body                    # the pause note arrived


def test_stale_plan_pr_does_not_undo_a_status_change(env):
    """A plan PR based on an older file must not move the status back either."""
    router, gh, fx, tmp_path = env
    original = task_text("in-progress")
    (tmp_path / "tasks" / "T-001.md").write_text(original.replace("status: in-progress", "status: awaiting-merge"), encoding="utf-8")
    planned = original.replace("Faster.", "Faster, with a plan.")
    pr = gh.add_pr(62, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": planned},
                   bases={"tasks/T-001.md": original})
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    meta, body = taskfile.load(str(tmp_path), "T-001")
    assert meta["status"] == "awaiting-merge" and "Faster, with a plan." in body


def test_scheduled_bots_sleep_when_there_is_nothing_new(env):
    router, gh, fx, tmp_path = env
    gh.issues = [feedback_issue(5, "triaged")]
    router.handle("schedule", {})
    assert wakes(fx) == []
    router.now = datetime(2026, 11, 2, 2, 5, tzinfo=timezone.utc)  # Monday, process improvement hour
    router.handle("schedule", {})
    assert wakes(fx) == []
    (tmp_path / "records" / "problems.jsonl").write_text(
        json.dumps({"time": "2026-11-01T08:53:06+00:00", "kind": "rework"}) + "\n", encoding="utf-8")
    router.handle("schedule", {})
    assert wakes(fx) == [("process_improvement", "schedule.process_improvement")]


def test_manual_run_wakes_even_with_nothing_new(env):
    router, _, fx, _ = env
    router.handle("workflow_dispatch", {"inputs": {"job": "outer_loop"}})
    assert wakes(fx) == [("outer_loop", "schedule.outer_loop")]


def test_only_code_prs_ask_bugbot_for_a_review(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    pr = code_pr(gh)
    router.handle("pull_request", {"action": "opened", "pull_request": pr})
    router.handle("pull_request", {"action": "synchronize", "pull_request": pr})
    task = gh.add_pr(31, "flow-demo-bot", ["tasks/T-001.md"], heads={"tasks/T-001.md": task_text("in-progress")})
    router.handle("pull_request", {"action": "opened", "pull_request": task})
    assert [c[1:] for c in fx.calls if c[0] == "comment"] == [(20, "bugbot run"), (20, "bugbot run")]


def test_failed_ci_job_is_rerun_once_before_waking_dev_lead(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    code_pr(gh)
    run = {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}}
    gh.checks["abc1234"] = [("completed", "failure", 1, "github-actions"), ("completed", "success", 2, "cursor")]
    router.handle("workflow_run", run)
    assert ("rerun_failed_checks", "abc1234") in fx.calls and wakes(fx) == []
    assert status_of(tmp_path)["failures"] == 0
    gh.checks["abc1234"] = [("completed", "failure", 3, "github-actions"), ("completed", "success", 2, "cursor")]
    router.handle("workflow_run", run)
    assert wakes(fx) == [("dev_lead", "code_pr.checks_failed")]
    assert status_of(tmp_path)["failures"] == 1
    assert fx.names().count("rerun_failed_checks") == 1


def test_rerun_passes_and_nobody_is_woken(env):
    router, gh, fx, tmp_path = env
    write_task(tmp_path, "in-progress")
    code_pr(gh)
    run = {"workflow_run": {"name": "CI", "status": "completed", "pull_requests": [{"number": 20}]}}
    gh.checks["abc1234"] = [("completed", "failure", 1, "github-actions")]
    router.handle("workflow_run", run)
    gh.checks["abc1234"] = [("completed", "success", 3, "github-actions")]
    router.handle("workflow_run", run)
    assert wakes(fx) == [] and status_of(tmp_path)["status"] == "awaiting-merge"
