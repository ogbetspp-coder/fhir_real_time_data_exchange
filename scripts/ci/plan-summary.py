#!/usr/bin/env python3
"""Summarises a `terraform plan -no-color` output for a pull request (foundations B4).

  plan-summary.py <plan text> <summary out> <terraform exit code>

Writes resource addresses and actions only, never attribute values; a for_each key holding an
account or an address is replaced by a short hash. Exits 4 when the plan destroys or replaces
anything, 0 otherwise — the caller decides whether that is acknowledged.
"""
import hashlib, re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
code = int(sys.argv[3])
# Resource addresses only — never attribute values. A for_each key that is an account or an
# address is replaced by a short hash, as the deploy log does for the same values.
def redact(line):
    return re.sub(r'\["([^"]*@[^"]*)"\]', lambda m: '["sha256:' + hashlib.sha256(m.group(1).encode()).hexdigest()[:12] + '"]', line)
actions = [redact(m.group(0).strip()) for m in re.finditer(r"^  # \S+ (will be|must be)[^\n]*", text, re.M)]
headline = next((l for l in text.splitlines() if l.startswith("Plan:") or "No changes." in l), None)
errors = [l for l in text.splitlines() if l.startswith("│ Error") or l.startswith("Error:")]
lines = ["### Terraform plan against live state", ""]
if code == 1:
    lines += ["**The plan failed.**", "", "```text"] + [redact(e) for e in errors[:20]] + ["```"]
else:
    lines += [f"**{headline or 'No summary line found.'}**", ""]
    lines += [f"- `{a[2:]}`" for a in actions] or ["No resource changes."]
destroys = [a for a in actions if "destroyed" in a or "replaced" in a]
if destroys:
    lines += ["", f"**{len(destroys)} destroy or replace.** Applied unattended on merge; the check fails unless the pull request carries the `allow-replace` label."]
lines += ["", "_Inputs are the deployed images and version; the dashboard's perpetual diff (foundations C10) is expected._"]
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(4 if destroys else 0)
