#!/usr/bin/env python3
"""Summarises a Terraform plan for a pull request (docs/foundations.md, B4).

  plan-summary.py <plan json | -> <plan text> <summary out> <terraform plan exit code>

The verdict comes from `terraform show -json`'s resource_changes, not from the human-readable
text: the text has several phrasings for a destroy ("must be replaced", "is tainted, so must be
replaced", a deposed object "will be destroyed") and a pattern that misses one would pass a
destroy. The text is read only for its one-line "Plan:" headline and, on failure, its errors.

Writes resource addresses and actions only, never attribute values. An instance key that holds
an IAM member or an address ("user:…", "domain:…", "…@…") is replaced by a short hash, and so is
any such string in an error line.

Exit codes: 0 no destroy; 4 the plan destroys or replaces something (the caller decides whether
that is acknowledged); 1 the plan failed or could not be read — never 0, so a crash here fails
the check rather than passing it.
"""
import json
import sys

# The deploy's failure issue is redacted by the same function (scripts/ci/redact.py).
from redact import redact

plan_json, plan_text, summary_out, code = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
text = open(plan_text, encoding="utf-8", errors="replace").read()


def write(lines: list[str]) -> None:
    body = "\n".join(lines) + "\n"
    with open(summary_out, "w", encoding="utf-8") as handle:
        handle.write(body)
    print(body)


header = ["### Terraform plan against live state", ""]
if code == 1 or plan_json == "-":
    errors = [l for l in text.splitlines() if l.startswith("│ Error") or l.startswith("Error:")]
    write(header + ["**The plan failed.**", "", "```text"] + [redact(e) for e in errors[:20]] + ["```"])
    sys.exit(1)

changes = json.load(open(plan_json, encoding="utf-8")).get("resource_changes", [])
VERB = {
    ("create",): "created",
    ("update",): "updated in place",
    ("delete",): "DESTROYED",
    ("delete", "create"): "REPLACED (destroy, then create)",
    ("create", "delete"): "REPLACED (create, then destroy)",
    ("forget",): "removed from Terraform, left in place",
}
rows, destroys = [], 0
for change in changes:
    actions = tuple(change.get("change", {}).get("actions", []))
    if actions in (("no-op",), ("read",)):
        continue
    address = redact(change.get("address", "?"))
    if change.get("deposed"):
        address += f" (deposed {change['deposed']})"
    rows.append(f"- `{address}` — {VERB.get(actions, '/'.join(actions))}")
    if "delete" in actions:
        destroys += 1

headline = next((l for l in text.splitlines() if l.startswith("Plan:") or l.startswith("No changes.")), "")
lines = header + [f"**{headline or 'Plan summary line not found.'}**", ""] + (rows or ["No resource changes."])
if destroys:
    lines += ["", f"**{destroys} destroy or replace.** On a pull request the check fails unless it carries the `allow-replace` label; the deploy applies it only when ALLOW_REPLACE_ACK names the commit being deployed and exactly these destroys."]
# No change is expected here: the dashboard's text is ignored by Terraform and drift-checked
# separately (the line after this summary), and the environment's inputs come from one file.
lines += ["", "_Inputs are the deployed images and version, so a change listed is this pull request's, or drift in the live project; none is expected otherwise._"]
write(lines)
sys.exit(4 if destroys else 0)
