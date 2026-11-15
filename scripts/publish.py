import json
import os
import yaml
from common import (
    get, repo_path, get_file, load_json, save_json, ensure_label, create_issue,
    package_status_key, now_iso, package_dir_name, parse_package_dir,
    compare_versions, author_of, validated_file
)

number = int(os.environ["PR_NUMBER"])
commit_sha = os.environ["COMMIT_SHA"]
pr = get(repo_path(f"pulls/{number}"))
with open(validated_file()) as fh:
    package_dirs = sorted(json.load(fh)["files"])


def publish(package_dir):
    raw = get_file(package_dir + "/metadata.yaml", commit_sha)
    if raw is None:
        raise SystemExit("Published metadata.yaml could not be read.")
    metadata = yaml.safe_load(raw)
    slug, version = metadata["slug"], metadata["version"]
    author = author_of(metadata)

    # Defense in depth: re-check the rules on the published content.
    if parse_package_dir(package_dir) != (slug, version) or package_dir != package_dir_name(slug, version):
        raise SystemExit("Published metadata does not match the package directory name.")
    if not author or author.lower() != pr["user"]["login"].lower():
        raise SystemExit("Published metadata author does not match the PR author.")
    key = package_status_key(slug, version)

    index = load_json("registry/index.json", {"schema_version": 1, "packages": {}})
    statuses = load_json("registry/status.json", {"schema_version": 1, "packages": {}})
    index.setdefault("packages", {})[key] = {
        "slug": slug,
        "name": metadata["name"],
        "version": version,
        "type": metadata["type"],
        "description": metadata["description"],
        "author": author,
        "license": metadata["license"],
        "repository_path": package_dir,
        "external_repository": metadata.get("external_repository"),
        "published_at": now_iso(),
        "source_commit": commit_sha,
    }
    # "latest" always points at the newest version of a slug (new versions replace
    # older ones as the default; old versions stay installable by exact version).
    latest = index.setdefault("latest", {})
    if slug not in latest or compare_versions(version, latest[slug]) > 0:
        latest[slug] = version
    statuses.setdefault("packages", {})[key] = {
        "status": "Potentially Unsafe",
        "author": author,
        "reported": False,
        "published_at": now_iso(),
        "vote_issue": None,
        "approve_count": 0,
        "reject_count": 0,
    }
    save_json("registry/index.json", index, f"Register {key} in EnderPKG index")
    save_json("registry/status.json", statuses, f"Initialize community status for {key}")

    ensure_label("package-vote", "1D76DB", "Community vote for an EnderPKG package")
    title = f"[EnderPKG Vote] {slug}@{version}"
    body = f"""## Community evaluation: {metadata['name']} ({slug}@{version})

**Current status:** Potentially Unsafe

This package is available immediately, but EnderPKG clients must show a warning until the package becomes Normal.

### How to vote
- React with **👍** only if you consider the package acceptable.
- React with **👎** only if you have a concrete reason to believe the package is harmful or violates registry rules.
- Do not downvote merely because you dislike the package.
- One vote per GitHub account. The package author is not counted.
- Voting remains open after the initial 48-hour period.

### Automatic status rules
- 10 distinct eligible 👍 reactions: immediately mark as **Normal**.
- 10 distinct eligible 👎 reactions and at least twice as many 👎 as 👍: mark as **Rejected**.
- After 48 hours, mark as **Normal** unless the rejection threshold has been reached.
- A later rejection threshold can still change Normal to Rejected.

**Package author:** `{author}`  
**Registry path:** `{package_dir}`  
**Source commit:** `{commit_sha}`  
**Submission PR:** #{number}

This vote is a community signal, not a security audit.
"""
    issue = create_issue(title, body, ["package-vote"])
    statuses["packages"][key]["vote_issue"] = issue["number"]
    save_json("registry/status.json", statuses, f"Link community vote for {key}")
    print(f"Published {key}; vote issue #{issue['number']}.")


for package_dir in package_dirs:
    publish(package_dir)
