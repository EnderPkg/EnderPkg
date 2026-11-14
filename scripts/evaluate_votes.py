from datetime import datetime, timezone
from common import (
    paginate, repo_path, issue_reactions, load_json, save_json, patch,
    commit_author_is_bot, package_status_key
)

APPROVE_THRESHOLD = 10
REJECT_THRESHOLD = 10
REJECT_RATIO = 2
VOTE_LABEL = "package-vote"

statuses = load_json("registry/status.json", {"schema_version": 1, "packages": {}})
changed = False

issues = paginate(repo_path("issues?state=open&labels=package-vote"))
for issue in issues:
    if "pull_request" in issue:
        continue
    reactions = issue_reactions(issue["number"])
    approve_users = set()
    reject_users = set()
    for reaction in reactions:
        user = reaction.get("user") or {}
        login = user.get("login")
        if commit_author_is_bot(user) or not login:
            continue
        if reaction.get("content") == "+1":
            approve_users.add(login.lower())
        elif reaction.get("content") == "-1":
            reject_users.add(login.lower())

    # Find the matching status entry by exact vote issue number.
    matched = None
    for key, entry in statuses.get("packages", {}).items():
        if entry.get("vote_issue") == issue["number"]:
            matched = (key, entry)
            break
    if not matched:
        continue

    key, entry = matched
    author = (entry.get("author") or "").lower()
    approve_users.discard(author)
    reject_users.discard(author)

    # If a user has both reactions, neither vote is counted. This prevents
    # reaction ordering from determining the user's final vote.
    both = approve_users & reject_users
    approve_users -= both
    reject_users -= both

    approves, rejects = len(approve_users), len(reject_users)
    current = entry.get("status", "Potentially Unsafe")
    published = datetime.fromisoformat(entry["published_at"].replace("Z", "+00:00"))
    age_seconds = (datetime.now(timezone.utc) - published).total_seconds()

    new_status = current
    if current != "Rejected":
        if rejects >= REJECT_THRESHOLD and rejects >= REJECT_RATIO * max(approves, 1):
            new_status = "Rejected"
        elif approves >= APPROVE_THRESHOLD:
            new_status = "Normal"
        elif age_seconds >= 48 * 60 * 60:
            new_status = "Normal"

    if (
        entry.get("approve_count") != approves
        or entry.get("reject_count") != rejects
        or entry.get("status") != new_status
    ):
        entry["approve_count"] = approves
        entry["reject_count"] = rejects
        entry["status"] = new_status
        entry["last_evaluated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        changed = True

    if new_status == "Rejected" and current != "Rejected":
        patch(repo_path(f"issues/{issue['number']}"), {
            "state": "closed",
            "state_reason": "not_planned",
            "title": issue["title"] + " [REJECTED]"
        })
        entry["rejected_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        changed = True

    # Keep vote open while package is not rejected. Normal does not close voting.
    if current == "Rejected":
        continue

if changed:
    save_json("registry/status.json", statuses, "Update EnderPKG community vote statuses")
print(f"Evaluated {len(issues)} open vote issues.")
