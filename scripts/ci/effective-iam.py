#!/usr/bin/env python3
"""Which roles each service identity holds, anywhere the deploy can read, against what Terraform
declares for it (audit B08, D-3; UR-18 in docs/validation/README.md; ADR 0004 decision 5).

  effective-iam.py <index> <terraform state json> <out dir> <label>=<e-mail> ...

<index> lists the IAM policies scripts/gcp/deploy.sh (export_effective_iam) read, one per line:
"<policy file>\t<scope>\t<resource>\t<kind>", where kind is "iam" (bindings[].role/members) or
"bigquery-dataset" (a dataset's access list); and one line "-\t<scope>\t<resource>\tunread" for
each policy it could not read. The state is `terraform show -json`.

For each identity writes <out dir>/effective-iam-<label>.json: every grant it holds (scope,
resource, role, condition title), the roles Terraform declares for it, the roles it holds that no
Terraform resource declares ("undeclared"), and the policies that could not be read. An identity
given as <label>=<e-mail>! has no declared set (the deployer, whose roles are bootstrapped outside
Terraform): its grants are exported and not judged.

Prints one line per identity, and a GitHub warning annotation per undeclared role and per unread
scope: evidence, never a failure. Exits 0 unless its own inputs cannot be read (2).
"""
import json
import os
import sys

# A BigQuery dataset's access list writes the predefined dataset roles as primitive ones.
BIGQUERY_PRIMITIVE = {
    "READER": "roles/bigquery.dataViewer",
    "WRITER": "roles/bigquery.dataEditor",
    "OWNER": "roles/bigquery.dataOwner",
}


def declared_roles(state):
    """member -> the roles Terraform's *_iam_member and *_iam_binding resources give it."""
    roles = {}

    def walk(module):
        for resource in module.get("resources") or []:
            kind = resource.get("type", "")
            values = resource.get("values") or {}
            if kind.endswith("_iam_member"):
                members = [values.get("member")]
            elif kind.endswith("_iam_binding"):
                members = values.get("members") or []
            else:
                continue
            for member in members:
                if isinstance(member, str) and isinstance(values.get("role"), str):
                    roles.setdefault(member, set()).add(values["role"])
        for child in module.get("child_modules") or []:
            walk(child)

    walk((state.get("values") or {}).get("root_module") or {})
    return roles


def grants(policy, kind, email):
    """(role, condition title) for each grant `policy` gives the service account `email`."""
    member = f"serviceAccount:{email}"
    found = []
    if kind == "bigquery-dataset":
        for entry in policy.get("access") or []:
            if entry.get("userByEmail") == email or entry.get("iamMember") == member:
                role = entry.get("role", "")
                found.append((BIGQUERY_PRIMITIVE.get(role, role), ""))
        return found
    for binding in policy.get("bindings") or []:
        if member in (binding.get("members") or []):
            found.append((binding.get("role", ""), (binding.get("condition") or {}).get("title", "")))
    return found


def main():
    index_file, state_file, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    identities = []
    for argument in sys.argv[4:]:
        label, _, email = argument.partition("=")
        judged = not email.endswith("!")
        identities.append((label, email.rstrip("!"), judged))
    with open(state_file, encoding="utf-8") as handle:
        declared = declared_roles(json.load(handle))
    policies, unread = [], []
    with open(index_file, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            path, scope, resource, kind = line.rstrip("\n").split("\t")
            if kind == "unread":
                unread.append(f"{scope} {resource}")
                continue
            with open(path, encoding="utf-8") as policy_handle:
                policies.append((scope, resource, kind, json.load(policy_handle)))

    for label, email, judged in identities:
        held = []
        for scope, resource, kind, policy in policies:
            for role, condition in grants(policy, kind, email):
                held.append({"scope": scope, "resource": resource, "role": role, "condition": condition})
        held.sort(key=lambda grant: (grant["scope"], grant["resource"], grant["role"]))
        report = {"identity": email, "grants": held, "unread": unread}
        summary = f"{label}: {len(held)} grant(s) in {len(policies)} policies read"
        if judged:
            expected = sorted(declared.get(f"serviceAccount:{email}", set()))
            undeclared = sorted({grant["role"] for grant in held} - set(expected))
            report.update({"declared": expected, "undeclared": undeclared})
            summary += f"; {len(undeclared)} role(s) Terraform does not declare"
            for role in undeclared:
                where = ", ".join(
                    f"{grant['scope']} {grant['resource']}" for grant in held if grant["role"] == role
                )
                print(f"::warning title=Grant outside Terraform::{label} holds {role} ({where}), which no Terraform resource declares for it.")
        else:
            summary += "; not judged (its roles are granted outside Terraform)"
        with open(os.path.join(out_dir, f"effective-iam-{label}.json"), "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(summary)
    for scope in unread:
        print(f"::warning title=IAM evidence incomplete::Could not read the IAM policy of {scope}; the export does not cover it.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 -- evidence collection reports, never raises
        print(f"::warning title=IAM evidence::effective-iam.py could not build the report ({type(error).__name__}).")
        sys.exit(2)
