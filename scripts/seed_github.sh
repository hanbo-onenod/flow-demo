#!/bin/bash
# Create the labels the flow uses and (optionally) the 7 demo feedback issues.
# Usage:
#   scripts/seed_github.sh OWNER/flow-demo            # labels only
#   scripts/seed_github.sh OWNER/flow-demo --feedback # labels + feedback issues (run at the start of scenario 2)
set -euo pipefail
REPO="$1"
cd "$(dirname "$0")/.."

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force >/dev/null; echo "label $1"; }
label candidate        1d76db "A possible requirement, waiting for the requirement owner"
label accepted         0e8a16 "Requirement owner accepted; product bot writes the task package"
label deferred         c5def5 "Kept for later, not now"
label clarifying       fbca04 "Product bot is asking questions"
label feedback         d4c5f9 "Raw user feedback"
label triaged          ededed "Outer loop has processed this feedback"
label not-a-requirement ededed "A question, answered"
label out-of-scope     ededed "Not ours"
label source:idea      bfdadc "Came from a one-line idea"
label source:minutes   bfdadc "Came from meeting minutes"
label source:doc       bfdadc "Came from a requirements document"
label source:feedback  bfdadc "Came from user feedback"
label source:flow      bfdadc "Came from inside the flow (failed acceptance, review)"
label flow:seen        ededed "Set by the flow router once it has handled a new candidate"

if [[ "${2:-}" == "--feedback" ]]; then
  python3 - "$REPO" <<'EOF'
import json, subprocess, sys
repo = sys.argv[1]
for item in json.load(open("inputs/feedback-issues.json", encoding="utf-8")):
    subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", item["title"],
                    "--body", item["body"], "--label", "feedback"], check=True)
EOF
fi
