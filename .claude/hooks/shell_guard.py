#!/usr/bin/env python3
import json
import re
import sys


payload = json.load(sys.stdin)
command = payload.get("tool_input", {}).get("command", "")
patterns = [
    r"(^|\s)git\s+reset\s+--hard(\s|$)",
    r"(^|\s)git\s+clean\s+-[a-zA-Z]*f",
    r"(^|\s)git\s+push\b.*\s--force(?:-with-lease)?(\s|$)",
    r"(^|\s)rm\s+-[a-zA-Z]*rf\s+/(?:\s|$)",
]

if any(re.search(pattern, command) for pattern in patterns):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Destructive command blocked by project policy.",
                }
            }
        )
    )
else:
    sys.exit(0)
