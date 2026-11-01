"""The flow router: turns a GitHub event into the next step, using rules/rules.yml.

Run by .github/workflows/flow.yml. Locally:
    python -m flow.router --event issues --payload sample.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import yaml

from flow import taskfile
from flow.effects import GitHubReader, LiveEffects, RecordingEffects

RULEBOOKS = {
    "team_chief": "bots/team-chief.md",
    "product": "bots/product.md",
    "dev_lead": "bots/dev-lead.md",
    "acceptance": "bots/acceptance.md",
    "outer_loop": "bots/outer-loop.md",
    "process_improvement": "bots/process-improvement.md",
}
# Confirmations made in chat (an approval card, or a reply in the Slack thread)
# are written to the PR by the Team Chief as one of these lines.
CHAT_CONFIRM = re.compile(r"^/confirmed-via-(card|slack) by @(\S+) at (\S+)(?:.*?\blink:(\S+))?", re.MULTILINE)
# The Team Chief relays a person's Slack answer to the issue with this prefix.
RELAYED_ANSWER = re.compile(r"^Answer from @(\S+) \(via Slack")
SLACK_MERGE = re.compile(r"^/merge-via-slack by @(\S+) at (\S+)(?:.*?\blink:(\S+))?", re.MULTILINE)
SLACK_LINE = re.compile(r"^Slack thread:\s*(\S+)", re.MULTILINE)
PASSING = {"success", "neutral", "skipped"}
# Fields only the router writes. A bot PR based on an older version of the task
# file must not overwrite them.
ROUTER_FIELDS = ("failures", "confirmation", "checked_sha", "reminded_status", "notified_status", "created", "updated")


def parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class Router:
    def __init__(self, repo_dir: str, repo: str, effects, gh, now: datetime | None = None) -> None:
        self.repo_dir = repo_dir
        self.repo = repo
        self.fx = effects
        self.gh = gh
        self.now = now or datetime.now(timezone.utc)
        with open(os.path.join(repo_dir, "rules", "rules.yml"), encoding="utf-8") as f:
            self.rules = yaml.safe_load(f)
        with open(os.path.join(repo_dir, "rules", "people.yml"), encoding="utf-8") as f:
            self.people = yaml.safe_load(f)
        self.bot_logins = set(self.rules.get("bot_logins", []))

    # ---------- entry point ----------

    def handle(self, event_name: str, payload: dict) -> None:
        handler = {
            "issues": self.on_issues,
            "issue_comment": self.on_issue_comment,
            "pull_request": self.on_pull_request,
            "pull_request_review": self.on_review,
            "workflow_run": self.on_workflow_run,
            "check_suite": self.on_check_suite,
            "schedule": lambda _p: self.on_schedule(),
            "workflow_dispatch": self.on_dispatch,
        }.get(event_name)
        if handler is None:
            print(f"ignored event {event_name}")
            return
        handler(payload)

    # ---------- helpers ----------

    def mention(self, login: str | None) -> str:
        for person in self.people.get("people", []):
            if person.get("github") == login and person.get("slack_id"):
                return f"<@{person['slack_id']}>"
        return f"@{login}" if login else "(nobody)"

    def role_login(self, role: str, meta: dict | None) -> str | None:
        if meta and meta.get(role):
            return meta[role]
        roles = self.people.get("roles", {})
        return roles.get(role) or roles.get(f"default_{role}")

    def wake_payload(self, bot: str, event: str, task: str | None = None, issue: str | None = None,
                     pr: str | None = None, thread: str | None = None, note: str = "") -> dict:
        return {
            "bot": bot,
            "event": event,
            "repo": self.repo,
            "task": task,
            "issue": issue,
            "pr": pr,
            "slack_thread": thread or None,
            "rulebook": RULEBOOKS[bot],
            "note": note,
        }

    def record_problem(self, kind: str, task: str | None, detail: str, link: str | None = None) -> None:
        path = os.path.join(self.repo_dir, "records", "problems.jsonl")
        entry = {"time": self.now.isoformat(), "kind": kind, "task": task, "detail": detail, "link": link}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def task_exists(self, task_id: str) -> bool:
        return os.path.exists(taskfile.path_for(self.repo_dir, task_id))

    # ---------- applying a rule ----------

    def apply(self, key: str, task_id: str | None = None, issue: dict | None = None, pr: dict | None = None,
              note: str = "", depth: int = 0) -> None:
        rule = self.rules["events"].get(key)
        if not rule or depth > 3:
            return
        meta, body = taskfile.load(self.repo_dir, task_id) if task_id else ({}, "")
        thread = meta.get("slack_thread") or (self.issue_thread(issue) if issue else "")
        issue_url = issue.get("html_url") if issue else None
        pr_url = pr.get("html_url") if pr else None
        title = meta.get("title") or (issue or {}).get("title") or (pr or {}).get("title") or ""
        label = task_id or (f"#{issue['number']}" if issue else "")

        if rule.get("wake"):
            self.fx.wake(rule["wake"], self.wake_payload(rule["wake"], key, task_id, issue_url, pr_url, thread, note))
        if rule.get("record_problem"):
            self.record_problem(rule["record_problem"], task_id, note or key, pr_url or issue_url)
            self.fx.commit(f"records: {rule['record_problem']} {label}")
        if rule.get("notify"):
            who = self.mention(self.role_login(rule["notify"], meta))
            text = f"[Flow] {label} {title}: {note or key}. {who}"
            if rule.get("hint"):
                number = (pr or issue or {}).get("number", "")
                text += " " + rule["hint"].format(label=label, number=number)
            if pr_url or issue_url:
                text += f" {pr_url or issue_url}"
            self.fx.slack_thread(thread, text)
        if rule.get("board"):
            self.fx.slack_board(f"{label} · {key} · {title}")
        if rule.get("set_status") and task_id:
            self.set_status(task_id, rule["set_status"], f"after {key}", depth + 1)

    def set_status(self, task_id: str, status: str, reason: str, depth: int = 0, **fields) -> None:
        meta, body = taskfile.load(self.repo_dir, task_id)
        if meta.get("status") == status and not fields:
            return
        old = meta.get("status")
        meta["status"] = status
        meta["updated"] = taskfile.now_iso()
        meta.update(fields)
        body = taskfile.append_log(body, f"router: status {old} -> {status} ({reason})")
        taskfile.save(self.repo_dir, task_id, meta, body)
        self.fx.commit(f"{task_id}: {old} -> {status}")
        if status == "closed":
            self.write_summary(task_id)
        self.apply(f"status.{status}", task_id, note=f"now {status}, {reason}", depth=depth)

    # ---------- issues: candidates ----------

    @staticmethod
    def labels(issue: dict) -> set[str]:
        return {label["name"] for label in issue.get("labels", [])}

    @staticmethod
    def issue_thread(issue: dict) -> str:
        match = SLACK_LINE.search(issue.get("body") or "")
        return match.group(1) if match else ""

    def on_issues(self, payload: dict) -> None:
        issue, action = payload["issue"], payload["action"]
        labels = self.labels(issue)
        if "candidate" not in labels:
            return
        if action == "opened":
            if labels & {"source:minutes", "source:doc"}:
                self.apply("intake.opened", issue=issue, note="New minutes or document to split")
            else:
                self.apply("candidate.opened", issue=issue, note="New candidate, please accept, merge, defer or reject")
        elif action == "labeled" and payload.get("label", {}).get("name") == "accepted":
            self.apply("candidate.accepted", issue=issue, note="Requirement owner accepted this candidate")

    def on_issue_comment(self, payload: dict) -> None:
        issue, comment = payload["issue"], payload["comment"]
        author = comment["user"]["login"]
        if "pull_request" in issue:
            body = comment.get("body") or ""
            if author not in self.bot_logins:
                return
            confirm, merge = CHAT_CONFIRM.search(body), SLACK_MERGE.search(body)
            if confirm:
                via = "approval-card" if confirm.group(1) == "card" else "slack"
                evidence = confirm.group(4) or comment["html_url"]
                self.confirm_task_pr(issue["number"], confirm.group(2), via, evidence, confirm.group(3))
            elif merge:
                self.merge_via_slack(issue["number"], merge.group(1), merge.group(2), merge.group(3) or comment["html_url"])
            return
        labels = self.labels(issue)
        relayed = author in self.bot_logins and RELAYED_ANSWER.match(comment.get("body") or "")
        if {"candidate", "clarifying"} <= labels and (author not in self.bot_logins or relayed):
            who = relayed.group(1) if relayed else author
            self.apply("candidate.human_reply", issue=issue, note=f"{who} replied")

    # ---------- task PRs (tasks/ and records/ only) ----------

    def classify(self, number: int) -> tuple[str, dict]:
        """Return ("code", {}) or ("task", {task_id: kind}) where kind is approval/auto/invalid."""
        files = self.gh.pr_files(number)
        allowed = tuple(self.rules.get("router_may_merge_paths", []))
        if not files or not all(f.startswith(allowed) for f in files):
            return "code", {}
        pr = self.gh.pr(number)
        kinds = {}
        for path in files:
            if not taskfile.is_task_path(path):
                continue
            task_id = taskfile.task_id_from_path(path)
            head = self.gh.file_at(pr["head"]["sha"], path)
            new = taskfile.parse(head)[0].get("status") if head else None
            old = taskfile.load(self.repo_dir, task_id)[0].get("status") if self.task_exists(task_id) else None
            if old is None and new == "awaiting-confirmation":
                kinds[task_id] = "approval"
            elif new == "acceptance-proposed" and old != new:
                kinds[task_id] = "approval"
            elif new == "paused" or new == old:
                kinds[task_id] = "auto"
            else:
                kinds[task_id] = "invalid"
        return "task", kinds

    def on_pull_request(self, payload: dict) -> None:
        pr, action = payload["pull_request"], payload["action"]
        if action == "closed":
            if pr.get("merged"):
                self.on_merged(pr)
            return
        if action not in ("opened", "reopened", "synchronize"):
            return
        kind, tasks = self.classify(pr["number"])
        if kind == "code" or pr["user"]["login"] not in self.bot_logins:
            return
        if any(k == "invalid" for k in tasks.values()):
            self.fx.comment(pr["number"], "[Flow] This PR changes a task status in a way only the router may. "
                                          "Bots may create a task package, propose acceptance, pause, or leave status unchanged.")
            return
        if any(k == "approval" for k in tasks.values()):
            if action != "synchronize":
                for task_id, k in tasks.items():
                    if k == "approval":
                        self.notify_pending(task_id, pr)
            return
        self.after_task_merge(self.apply_task_pr(pr["number"]))

    def apply_task_pr(self, number: int) -> list[str]:
        """Bring a bot's tasks/ or records/ PR into main and close it.

        Not a git merge: the bot's branch is usually based on an older main, so
        its copy of router fields and Log is stale. Content sections and the
        status the bot proposed come from the PR; router fields come from main;
        Log lines from both are kept.
        """
        pr = self.gh.pr(number)
        sha = pr["head"]["sha"]
        task_ids = []
        for path in self.gh.pr_files(number):
            head = self.gh.file_at(sha, path)
            if head is None:
                continue
            target = os.path.join(self.repo_dir, path)
            if taskfile.is_task_path(path):
                task_id = taskfile.task_id_from_path(path)
                task_ids.append(task_id)
                head = self.merge_task_text(task_id, head)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(head)
        self.fx.commit(f"Apply PR #{number} ({', '.join(task_ids) or 'records'})")
        self.fx.close_pr(number, f"[Flow] Applied to main by the flow router; closing. Tasks: {', '.join(task_ids) or 'none'}.")
        return task_ids

    def merge_task_text(self, task_id: str, head: str) -> str:
        new_meta, new_body = taskfile.parse(head)
        if not self.task_exists(task_id):
            new_meta.setdefault("created", taskfile.now_iso())
            new_meta["updated"] = taskfile.now_iso()
            return taskfile.dump(new_meta, new_body)
        old_meta, old_body = taskfile.load(self.repo_dir, task_id)
        for field in ROUTER_FIELDS:
            if field in old_meta:
                new_meta[field] = old_meta[field]
        old_log = old_body.split("## Log", 1)[1].strip().splitlines() if "## Log" in old_body else []
        head_text, _, head_log = new_body.partition("## Log")
        extra = [line for line in head_log.strip().splitlines() if line.strip() and line not in old_log]
        log = "\n".join(old_log + extra)
        return taskfile.dump(new_meta, f"{head_text.rstrip()}\n\n## Log\n\n{log}\n" if log else f"{head_text.rstrip()}\n\n## Log\n\n")

    def notify_pending(self, task_id: str, pr: dict) -> None:
        head = self.gh.file_at(pr["head"]["sha"], f"tasks/{task_id}.md")
        meta = taskfile.parse(head)[0] if head else {}
        thread = meta.get("slack_thread", "")
        who = self.mention(meta.get("confirmer") or self.role_login("confirmer", None))
        verb = "验收" if meta.get("status") == "acceptance-proposed" else "确认"
        self.fx.slack_thread(thread, f"[Flow] {task_id} {meta.get('title', '')}: {who}, please review. "
                                     f"Reply here `team chief {verb} {task_id}` (or `team chief 不要 {task_id}: reason`), "
                                     f"or approve on GitHub. {pr['html_url']}")
        self.fx.slack_board(f"{task_id} · task_pr.opened · {meta.get('title', '')}")

    def on_review(self, payload: dict) -> None:
        review, pr = payload["review"], payload["pull_request"]
        state = review["state"].lower()
        kind, tasks = self.classify(pr["number"])
        if kind == "code":
            task_id = taskfile.task_from_pr_body(pr.get("body"))
            if state == "changes_requested" and task_id and self.task_exists(task_id):
                self.apply("code_pr.changes_requested", task_id, pr=pr, note="Review requested changes")
            return
        if state == "approved" and any(k == "approval" for k in tasks.values()):
            self.confirm_task_pr(pr["number"], review["user"]["login"], "github-review", review["html_url"], review.get("submitted_at"))

    def confirm_task_pr(self, number: int, login: str, via: str, evidence: str, at: str | None) -> None:
        kind, tasks = self.classify(number)
        pending = [t for t, k in tasks.items() if k == "approval"]
        if kind != "task" or not pending or any(k == "invalid" for k in tasks.values()):
            return
        pr = self.gh.pr(number)
        for task_id in pending:
            head = self.gh.file_at(pr["head"]["sha"], f"tasks/{task_id}.md")
            confirmer = taskfile.parse(head)[0].get("confirmer") if head else None
            if login != confirmer:
                self.fx.comment(number, f"[Flow] {task_id}: only the confirmer @{confirmer} can confirm this; @{login} cannot.")
                self.record_problem("wrong-confirmer", task_id, f"{login} tried to confirm via {via}", evidence)
                self.fx.commit(f"records: wrong-confirmer {task_id}")
                return
        self.after_task_merge(self.apply_task_pr(number), {"via": via, "by": login, "at": at or taskfile.now_iso(), "evidence": evidence})

    def after_task_merge(self, task_ids: list[str], confirmation: dict | None = None) -> None:
        """Idempotent: safe to run again when the merge's own `closed` event arrives."""
        for task_id in task_ids:
            if not self.task_exists(task_id):
                continue
            meta, _ = taskfile.load(self.repo_dir, task_id)
            status = meta.get("status")
            if status == "awaiting-confirmation":
                extra = {"confirmation": confirmation} if confirmation else {}
                self.set_status(task_id, "confirmed", f"confirmed via {(confirmation or {}).get('via', 'merge')}", **extra)
            elif status == "acceptance-proposed":
                self.set_status(task_id, "closed", f"acceptance approved, outcome {meta.get('outcome') or 'unknown'}")
            elif status == "paused" and meta.get("notified_status") != "paused":
                meta["notified_status"] = "paused"
                _, body = taskfile.load(self.repo_dir, task_id)
                taskfile.save(self.repo_dir, task_id, meta, body)
                self.fx.commit(f"{task_id}: pause noted")
                self.apply("status.paused", task_id, note="Task paused, decision needed")

    # ---------- code PRs ----------

    def on_merged(self, pr: dict) -> None:
        kind, tasks = self.classify(pr["number"])
        if kind == "task":  # a person merged a task PR by hand
            self.after_task_merge(list(tasks))
            return
        task_id = taskfile.task_from_pr_body(pr.get("body"))
        if not task_id or not self.task_exists(task_id):
            return
        self.after_code_merge(task_id, pr)

    def after_code_merge(self, task_id: str, pr: dict) -> None:
        still_open = [p for p in self.gh.open_prs()
                      if taskfile.task_from_pr_body(p.get("body")) == task_id and p["number"] != pr["number"]]
        if still_open:
            self.fx.slack_board(f"{task_id} · code PR merged, {len(still_open)} still open")
            return
        if taskfile.load(self.repo_dir, task_id)[0].get("status") in ("in-progress", "awaiting-merge"):
            self.apply("code_pr.merged", task_id, pr=pr, note="All code merged, acceptance starts")

    def merge_risk(self, meta: dict, files: list[str]) -> tuple[str, str]:
        policy = self.rules.get("merge_policy", {})
        for path in files:
            if path.startswith(tuple(policy.get("github_only_paths", []))):
                return "high", f"it changes {path}"
        risk = meta.get("risk") or policy.get("default_risk", "medium")
        return risk, "the task package says so" if meta.get("risk") else "no risk level was set"

    def merge_via_slack(self, number: int, login: str, at: str, evidence: str) -> None:
        """A person said `team chief 合并 #N` in Slack; merge it if the policy allows."""
        pr = self.gh.pr(number)
        task_id = taskfile.task_from_pr_body(pr.get("body"))
        if not task_id or not self.task_exists(task_id):
            self.fx.comment(number, "[Flow] Not merged: this PR has no `Task: T-NNN` line linking it to a task.")
            return
        meta, body = taskfile.load(self.repo_dir, task_id)
        merger = self.role_login("merger", meta)
        risk, why = self.merge_risk(meta, self.gh.pr_files(number))
        allowed = self.rules.get("merge_policy", {}).get("slack_merge_allowed_risk", [])
        refusal = None
        if login != merger:
            refusal = f"only the merger @{merger} can merge, not @{login}"
        elif meta.get("status") != "awaiting-merge" or meta.get("checked_sha") != pr["head"]["sha"]:
            refusal = "checks have not all passed on the latest commit yet"
        elif risk not in allowed:
            refusal = f"this change is {risk} risk ({why}), so it has to be merged on GitHub"
        if refusal:
            self.fx.comment(number, f"[Flow] Not merged from Slack: {refusal}.")
            self.fx.slack_thread(meta.get("slack_thread", ""), f"[Flow] {task_id}: not merged from Slack: {refusal}. {pr['html_url']}")
            self.record_problem("slack-merge-refused", task_id, refusal, evidence)
            self.fx.commit(f"records: slack-merge-refused {task_id}")
            return
        self.fx.merge_code_pr(number, f"Merge PR #{number} via Slack by @{login}\n\nEvidence: {evidence}")
        body = taskfile.append_log(body, f"router: PR #{number} merged via Slack by @{login} at {at} ({risk} risk), evidence {evidence}")
        taskfile.save(self.repo_dir, task_id, meta, body)
        self.fx.commit(f"{task_id}: PR #{number} merged via Slack")
        self.fx.comment(number, f"[Flow] Merged by the router on behalf of @{login} (Slack, {risk} risk). Evidence: {evidence}")
        self.after_code_merge(task_id, pr)

    def on_workflow_run(self, payload: dict) -> None:
        run = payload["workflow_run"]
        if run.get("name") != "CI" or run.get("status") != "completed":
            return
        for ref in run.get("pull_requests", []):
            self.evaluate_checks(self.gh.pr(ref["number"]))

    def on_check_suite(self, payload: dict) -> None:
        """Another app's checks (for example Bugbot) finished after CI did."""
        suite = payload["check_suite"]
        if suite.get("status") != "completed":
            return
        for ref in suite.get("pull_requests", []):
            self.evaluate_checks(self.gh.pr(ref["number"]))

    def evaluate_checks(self, pr: dict) -> None:
        task_id = taskfile.task_from_pr_body(pr.get("body"))
        if not task_id or not self.task_exists(task_id):
            return
        meta, body = taskfile.load(self.repo_dir, task_id)
        sha = pr["head"]["sha"]
        if meta.get("status") not in ("in-progress", "awaiting-merge") or meta.get("checked_sha") == sha:
            return
        states = self.gh.check_states(sha)
        if not states or any(status != "completed" for status, _ in states):
            return  # still running; the hourly sweep looks again
        meta["checked_sha"] = sha
        if all(conclusion in PASSING for _, conclusion in states):
            taskfile.save(self.repo_dir, task_id, meta, body)
            self.fx.commit(f"{task_id}: checks passed on {sha[:7]}")
            if meta.get("status") == "in-progress":
                self.apply("code_pr.checks_passed", task_id, pr=pr, note="All checks passed")
            return
        meta["failures"] = int(meta.get("failures") or 0) + 1
        taskfile.save(self.repo_dir, task_id, meta, body)
        self.fx.commit(f"{task_id}: check failure {meta['failures']}")
        limit = self.rules["limits"]["failures_per_task"]
        if meta["failures"] >= limit:
            self.record_problem("circuit-break", task_id, f"{meta['failures']} failed check runs", pr["html_url"])
            admin = self.mention(self.role_login("admin", None))
            self.fx.slack_thread(meta.get("slack_thread", ""), f"[Flow] {task_id} paused by the circuit breaker after {meta['failures']} failures. {admin} please look. {pr['html_url']}")
            meta, body = taskfile.load(self.repo_dir, task_id)
            meta["notified_status"] = "paused"
            taskfile.save(self.repo_dir, task_id, meta, body)
            self.set_status(task_id, "paused", "circuit breaker")
        else:
            self.apply("code_pr.checks_failed", task_id, pr=pr, note=f"Checks failed ({meta['failures']}/{limit})")

    # ---------- schedule and manual runs ----------

    def on_schedule(self) -> None:
        self.sweep()
        for name, job in self.rules.get("schedules", {}).items():
            if self.now.hour != job["hour_utc"]:
                continue
            if job["every"] == "week" and self.now.strftime("%A").lower() != job["weekday"]:
                continue
            self.fx.wake(job["wake"], self.wake_payload(job["wake"], f"schedule.{name}", note=f"Scheduled {name}"))

    def on_dispatch(self, payload: dict) -> None:
        job = (payload.get("inputs") or {}).get("job", "sweep")
        if job == "sweep":
            self.sweep()
        elif job in self.rules.get("schedules", {}):
            bot = self.rules["schedules"][job]["wake"]
            self.fx.wake(bot, self.wake_payload(bot, f"schedule.{job}", note=f"Manual {job}"))
        else:
            print(f"unknown job {job}")

    def sweep(self) -> None:
        """Timeouts, plus checks that finished after the CI event (for example Bugbot)."""
        tasks_dir = os.path.join(self.repo_dir, "tasks")
        open_prs = None
        for name in sorted(os.listdir(tasks_dir)):
            if not taskfile.is_task_path(f"tasks/{name}"):
                continue
            task_id = name[:-3]
            meta, body = taskfile.load(self.repo_dir, task_id)
            status = meta.get("status")
            if status == "in-progress":
                if open_prs is None:
                    open_prs = self.gh.open_prs()
                for pr in open_prs:
                    if taskfile.task_from_pr_body(pr.get("body")) == task_id:
                        self.evaluate_checks(pr)
                meta, body = taskfile.load(self.repo_dir, task_id)
                status = meta.get("status")
            timer = self.rules.get("timeouts_hours", {}).get(status)
            if not timer or meta.get("reminded_status") == status:
                continue
            if self.now - parse_time(meta["updated"]) < timedelta(hours=timer["hours"]):
                continue
            who = self.mention(self.role_login(timer["remind"], meta))
            self.fx.slack_thread(meta.get("slack_thread", ""), f"[Flow] {task_id} has been {status} for over {timer['hours']}h. {who}, can you take a look?")
            meta["reminded_status"] = status
            taskfile.save(self.repo_dir, task_id, meta, body)
            self.record_problem("timeout", task_id, f"{status} over {timer['hours']}h")
            self.fx.commit(f"{task_id}: timeout reminder ({status})")

    # ---------- task summary ----------

    def write_summary(self, task_id: str) -> None:
        meta, _ = taskfile.load(self.repo_dir, task_id)
        problems = []
        path = os.path.join(self.repo_dir, "records", "problems.jsonl")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                problems = [p for p in map(json.loads, filter(None, f.read().splitlines())) if p.get("task") == task_id]
        counts: dict[str, int] = {}
        for p in problems:
            counts[p["kind"]] = counts.get(p["kind"], 0) + 1
        created = meta.get("created") or self.created_at(task_id)
        duration = f"{(self.now - parse_time(created)).total_seconds() / 3600:.1f} h" if created else "unknown"
        confirmation = meta.get("confirmation") or {}
        lines = [
            f"# Summary {task_id}: {meta.get('title', '')}",
            "",
            f"- Outcome: {meta.get('outcome') or 'unknown'}",
            f"- Duration: {duration} (task file created {created or 'unknown'}, closed {self.now.isoformat()})",
            f"- Confirmed via: {confirmation.get('via', 'unknown')} by {confirmation.get('by', 'unknown')}",
            f"- Failed check runs: {meta.get('failures') or 0}",
            f"- Rework (changes requested): {counts.get('rework', 0)}",
            f"- Problems recorded: {sum(counts.values())} {counts if counts else ''}".rstrip(),
        ]
        os.makedirs(os.path.join(self.repo_dir, "records", "summaries"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "records", "summaries", f"{task_id}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        card = [
            f"# Scorecard {task_id}",
            "",
            "Auto-filled lines come from the summary. Fill the rest by hand.",
            "",
            f"- Ran through: (pass / conditional / fail)",
            f"- Human interventions outside the four gates: ",
            f"- Duration: {duration}",
            f"- Rework: {counts.get('rework', 0)}",
            f"- Usage this task (from the Cursor usage page): ",
            f"- Experience 1-5 (requester / confirmer / merger): ",
            f"- Problems noticed: ",
        ]
        os.makedirs(os.path.join(self.repo_dir, "records", "scorecards"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "records", "scorecards", f"{task_id}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(card) + "\n")
        self.fx.commit(f"{task_id}: summary and scorecard")

    def created_at(self, task_id: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "log", "--diff-filter=A", "--format=%cI", "--", f"tasks/{task_id}.md"],
                cwd=self.repo_dir, capture_output=True, text=True, check=True,
            ).stdout.split()
            return out[-1] if out else None
        except (OSError, subprocess.CalledProcessError):
            return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--payload", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "OWNER/flow-demo"))
    parser.add_argument("--dir", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = {}
    if args.payload:
        with open(args.payload, encoding="utf-8") as f:
            payload = json.load(f)
    effects = RecordingEffects() if args.dry_run else LiveEffects(args.dir, args.repo)
    Router(args.dir, args.repo, effects, GitHubReader(args.repo)).handle(args.event, payload)
    if args.dry_run:
        for call in effects.calls:
            print(json.dumps(call, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
