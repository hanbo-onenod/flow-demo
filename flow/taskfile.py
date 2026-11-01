"""Read and write task files: YAML front matter plus a Markdown body."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import yaml

TASK_ID = re.compile(r"\bT-\d{3,}\b")
TASK_LINE = re.compile(r"^\s*Task:\s*(T-\d{3,})\s*$", re.MULTILINE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("task file has no front matter")
    _, front, body = text.split("---", 2)
    return yaml.safe_load(front) or {}, body.lstrip("\n")


def dump(meta: dict, body: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    return f"---\n{front}---\n\n{body}"


def path_for(repo_dir: str, task_id: str) -> str:
    return os.path.join(repo_dir, "tasks", f"{task_id}.md")


def is_task_path(path: str) -> bool:
    return bool(re.fullmatch(r"tasks/T-\d{3,}\.md", path))


def task_id_from_path(path: str) -> str:
    return os.path.basename(path)[: -len(".md")]


def load(repo_dir: str, task_id: str) -> tuple[dict, str]:
    with open(path_for(repo_dir, task_id), encoding="utf-8") as f:
        return parse(f.read())


def save(repo_dir: str, task_id: str, meta: dict, body: str) -> None:
    with open(path_for(repo_dir, task_id), "w", encoding="utf-8") as f:
        f.write(dump(meta, body))


def append_log(body: str, line: str) -> str:
    entry = f"- {now_iso()} {line}"
    if "## Log" not in body:
        return body.rstrip("\n") + f"\n\n## Log\n\n{entry}\n"
    return body.rstrip("\n") + f"\n{entry}\n"


def task_from_pr_body(body: str | None) -> str | None:
    match = TASK_LINE.search(body or "")
    return match.group(1) if match else None
