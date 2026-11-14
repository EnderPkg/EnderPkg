import os
import re
from common import get, repo_path, load_json, save_json, now_iso, package_status_key

number = int(os.environ["ISSUE_NUMBER"])
issue = get(repo_path(f"issues/{number}"))
body = issue.get("body") or ""

def field(label):
    # GitHub issue forms render fields as headings followed by their values.
    pattern = rf"### {re.escape(label)}\s*\n\s*(.+?)(?=\n### |\Z)"
    match = re.search(pattern, body, re.S | re.I)
    return match.group(1).strip() if match else ""

slug = field("Package slug")
version = field("Package version")
reason = field("Reason and evidence")
if not slug or not version or not reason:
    raise SystemExit("Report issue is missing slug, version, or evidence.")

key = package_status_key(slug, version)
statuses = load_json("registry/status.json", {"schema_version": 1, "packages": {}})
entry = statuses.get("packages", {}).get(key)
if not entry:
    raise SystemExit(f"Package {key} is not present in the EnderPKG status registry.")

reports = entry.setdefault("reports", [])
if not any(x.get("issue") == issue["html_url"] for x in reports):
    reports.append({
        "issue": issue["html_url"],
        "reporter": issue["user"]["login"],
        "reason": reason[:3000],
        "reported_at": now_iso(),
    })
entry["reported"] = True
entry["last_reported_at"] = now_iso()
save_json("registry/status.json", statuses, f"Flag {key} with a community report")
print(f"Recorded report for {key}; clients should display the report warning.")
