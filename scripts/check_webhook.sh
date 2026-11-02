#!/bin/bash
# Day-1 checks 3 and 4: can a webhook wake a Grok routine, does the bot read
# the task number, and can it post into a given Slack thread?
# Usage:
#   GROK_WEBHOOK_URL=... [GROK_WEBHOOK_KEY=...] scripts/check_webhook.sh OWNER/flow-demo SLACK_THREAD_PERMALINK
# Then look in Slack: the bot should reply in that thread mentioning CHECK-001.
set -euo pipefail
: "${GROK_WEBHOOK_URL:?set GROK_WEBHOOK_URL to the webhook URL of the routine}"
REPO="$1"; THREAD="$2"
AUTH=()
[[ -n "${GROK_WEBHOOK_KEY:-}" ]] && AUTH=(-H "Authorization: Bearer $GROK_WEBHOOK_KEY")
curl -sS -X POST "$GROK_WEBHOOK_URL" "${AUTH[@]}" -H "Content-Type: application/json" -d @- <<EOF
{"bot": "product", "event": "check.ping", "repo": "$REPO", "task": "CHECK-001", "issue": null, "pr": null,
 "slack_thread": "$THREAD", "rulebook": "bots/product.md",
 "note": "Day-1 check. Do not change anything. Reply in the Slack thread with: your name, the task value from this payload, and the first heading of bots/product.md."}
EOF
echo
echo "Sent. A 2xx answer only means the routine started; check the Slack thread for the reply."

# Test PR only: checks that the router asks Bugbot to review code PRs. Will be closed, not merged.
