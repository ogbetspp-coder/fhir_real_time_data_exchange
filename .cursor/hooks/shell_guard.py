#!/usr/bin/env python3
import json
import re
import sys


def main() -> None:
    payload = json.load(sys.stdin)
    command = payload.get("command", "")

    denied = [
        r"(^|\s)git\s+reset\s+--hard(\s|$)",
        r"(^|\s)git\s+clean\s+-[a-zA-Z]*f",
        r"(^|\s)git\s+push\b.*\s--force(?:-with-lease)?(\s|$)",
        r"(^|\s)rm\s+-[a-zA-Z]*rf\s+/(?:\s|$)",
    ]
    approval_required = [
        r"(^|\s)terraform(?:\s+-chdir=\S+)?\s+(?:apply|destroy)(\s|$)",
        r"(^|\s)gcloud\s+projects\s+delete(\s|$)",
        r"(^|\s)gcloud\s+storage\s+buckets\s+update\b.*--lock-retention-period",
    ]

    if any(re.search(pattern, command) for pattern in denied):
        print(
            json.dumps(
                {
                    "continue": True,
                    "permission": "deny",
                    "user_message": "Blocked destructive repository or host command.",
                    "agent_message": "Use a reversible operation and preserve existing work.",
                }
            )
        )
        return

    if any(re.search(pattern, command) for pattern in approval_required):
        print(
            json.dumps(
                {
                    "continue": True,
                    "permission": "ask",
                    "user_message": "This changes or irreversibly locks cloud infrastructure.",
                    "agent_message": "Confirm the target project, plan, and rollback procedure.",
                }
            )
        )
        return

    print(json.dumps({"continue": True, "permission": "allow"}))


if __name__ == "__main__":
    main()
