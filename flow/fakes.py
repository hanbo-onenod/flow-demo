"""In-memory stand-ins for GitHub and the outside world, for tests and the simulation."""
from __future__ import annotations

from flow.effects import RecordingEffects


class FakeGH:
    def __init__(self, repo: str = "OWNER/flow-demo") -> None:
        self.repo = repo
        self.prs: dict[int, dict] = {}
        self.files: dict[int, list[str]] = {}
        self.heads: dict[tuple[str, str], str] = {}
        self.checks: dict[str, list] = {}

    def add_pr(self, number, author, files, body="", heads=None, sha=None):
        sha = sha or f"sha{number}"
        pr = {"number": number, "user": {"login": author}, "head": {"sha": sha}, "body": body,
              "html_url": f"https://github.com/{self.repo}/pull/{number}", "title": f"PR {number}", "merged": False}
        self.prs[number] = pr
        self.files[number] = files
        for path, text in (heads or {}).items():
            self.heads[(sha, path)] = text
        return pr

    def pr(self, number):
        return self.prs[number]

    def pr_files(self, number):
        return self.files[number]

    def open_prs(self):
        return [p for p in self.prs.values() if not p["merged"] and p.get("state") != "closed"]

    def file_at(self, ref, path):
        return self.heads.get((ref, path))

    def check_states(self, sha):
        return self.checks.get(sha, [])


class FakeEffects(RecordingEffects):
    """Records calls; closing a PR marks it closed (not merged) like the real router does."""

    def __init__(self, repo_dir, gh):
        super().__init__()
        self.repo_dir, self.gh = repo_dir, gh

    def close_pr(self, number, text):
        super().close_pr(number, text)
        self.gh.prs[number]["state"] = "closed"

    def merge_code_pr(self, number, message):
        super().merge_code_pr(number, message)
        self.gh.prs[number]["merged"] = True
