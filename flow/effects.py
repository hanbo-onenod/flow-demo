"""Everything the router does to the outside world goes through here.

`LiveEffects` talks to GitHub, Slack, Grok and git. `RecordingEffects` only
records what would have happened; tests and dry runs use it. A live run with a
missing secret skips that one channel and says so in the Actions log.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

BOT_ENV = {
    "team_chief": "GROK_WEBHOOK_TEAM_CHIEF",
    "product": "GROK_WEBHOOK_PRODUCT",
    "dev_lead": "GROK_WEBHOOK_DEV_LEAD",
    "acceptance": "GROK_WEBHOOK_ACCEPTANCE",
    "outer_loop": "GROK_WEBHOOK_OUTER_LOOP",
    "process_improvement": "GROK_WEBHOOK_PROCESS_IMPROVEMENT",
}


def slack_thread_target(url: str) -> tuple[str, str] | None:
    """Turn a Slack permalink into (channel, thread_ts)."""
    if not url:
        return None
    parsed = urlparse(url)
    match = re.search(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})", parsed.path)
    if not match:
        return None
    channel, secs, micros = match.groups()
    thread_ts = parse_qs(parsed.query).get("thread_ts", [f"{secs}.{micros}"])[0]
    return channel, thread_ts


def _http_json(method: str, url: str, body: dict | None, headers: dict) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"{method} {url} -> {err.code}: {err.read().decode()[:300]}") from err
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


class RecordingEffects:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def wake(self, bot: str, payload: dict) -> None:
        self.calls.append(("wake", bot, payload))

    def slack_thread(self, thread_url: str, text: str) -> None:
        self.calls.append(("slack_thread", thread_url, text))

    def slack_board(self, text: str) -> None:
        self.calls.append(("slack_board", text))

    def comment(self, number: int, text: str) -> None:
        self.calls.append(("comment", number, text))

    def close_pr(self, number: int, text: str) -> None:
        self.calls.append(("close_pr", number, text))

    def commit(self, message: str) -> None:
        self.calls.append(("commit", message))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class LiveEffects:
    def __init__(self, repo_dir: str, repo: str) -> None:
        self.repo_dir = repo_dir
        self.repo = repo
        self.env = os.environ

    def _skip(self, what: str) -> None:
        print(f"SKIPPED {what}: secret not configured")

    def wake(self, bot: str, payload: dict) -> None:
        url = self.env.get(BOT_ENV[bot], "")
        if not url:
            return self._skip(f"wake {bot}")
        headers = {}
        token = self.env.get("GROK_WEBHOOK_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        _http_json("POST", url, payload, headers)
        print(f"woke {bot} for {payload.get('event')}")

    def _slack_post(self, channel: str, text: str, thread_ts: str | None = None) -> None:
        token = self.env.get("SLACK_BOT_TOKEN", "")
        if not token or not channel:
            return self._skip("slack post")
        body = {"channel": channel, "text": text, "unfurl_links": False}
        if thread_ts:
            body["thread_ts"] = thread_ts
        result = _http_json("POST", "https://slack.com/api/chat.postMessage", body, {"Authorization": f"Bearer {token}"})
        if not result.get("ok"):
            print(f"slack error: {result.get('error')}")

    def slack_thread(self, thread_url: str, text: str) -> None:
        target = slack_thread_target(thread_url)
        if target:
            self._slack_post(target[0], text, target[1])
        else:
            self.slack_board(text)

    def slack_board(self, text: str) -> None:
        self._slack_post(self.env.get("SLACK_BOARD_CHANNEL", ""), text)

    def _gh(self, method: str, path: str, body: dict | None = None) -> dict:
        token = self.env["GITHUB_TOKEN"]
        return _http_json(
            method,
            f"https://api.github.com/repos/{self.repo}{path}",
            body,
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )

    def comment(self, number: int, text: str) -> None:
        self._gh("POST", f"/issues/{number}/comments", {"body": text})

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo_dir, check=True, capture_output=True, text=True).stdout

    def close_pr(self, number: int, text: str) -> None:
        self.comment(number, text)
        self._gh("PATCH", f"/pulls/{number}", {"state": "closed"})

    def commit(self, message: str) -> None:
        # Pushed with the deploy key, the only actor allowed to bypass branch
        # protection. The router only writes under tasks/ and records/.
        self._git("add", "tasks", "records")
        if not self._git("status", "--porcelain", "--", "tasks", "records").strip():
            return
        self._git("commit", "-m", message)
        self._git("push", "origin", "HEAD:main")


class GitHubReader:
    """Read-only GitHub queries the router needs."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.token = os.environ.get("GITHUB_TOKEN", "")

    def _get(self, path: str) -> dict | list:
        return _http_json(
            "GET",
            f"https://api.github.com/repos/{self.repo}{path}",
            None,
            {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"},
        )

    def pr(self, number: int) -> dict:
        return self._get(f"/pulls/{number}")

    def pr_files(self, number: int) -> list[str]:
        return [f["filename"] for f in self._get(f"/pulls/{number}/files?per_page=100")]

    def open_prs(self) -> list[dict]:
        return self._get("/pulls?state=open&per_page=100")

    def file_at(self, ref: str, path: str) -> str | None:
        try:
            data = self._get(f"/contents/{path}?ref={ref}")
        except RuntimeError:
            return None
        return base64.b64decode(data["content"]).decode()

    def check_states(self, sha: str) -> list[tuple[str, str | None]]:
        runs = self._get(f"/commits/{sha}/check-runs?per_page=100")["check_runs"]
        return [(r["status"], r["conclusion"]) for r in runs]
