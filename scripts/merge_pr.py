"""Publishes ONLY the validated new package folders to the default branch.

The pull request itself is never merged: its branch may contain unrelated or
stale changes (deleted files, other folders, ...). We copy just the validated
files from the PR head into one fast-forward commit on the default branch
and close the PR.
"""
import base64
import json
import os
import time

from common import (
    default_branch, get, get_file_bytes, patch, path_exists, post, repo_path,
    validated_file,
)

MAX_ATTEMPTS = 5


def main():
    number = int(os.environ["PR_NUMBER"])
    head_sha = os.environ["HEAD_SHA"]

    pr = get(repo_path(f"pulls/{number}"))
    if not pr:
        raise SystemExit("Pull request not found.")
    if pr["state"] != "open":
        raise SystemExit("Pull request is not open anymore; refusing to publish.")
    if pr["head"]["sha"] != head_sha:
        raise SystemExit("PR head changed after validation; refusing to publish.")

    with open(validated_file()) as fh:
        validated = json.load(fh)
    if validated["head_sha"] != head_sha:
        raise SystemExit("Validation result belongs to a different commit; refusing to publish.")

    entries = []
    for paths in validated["files"].values():
        for path in paths:
            raw = get_file_bytes(path, head_sha)
            if raw is None:
                raise SystemExit(f"Could not read {path} from the PR head.")
            blob = post(repo_path("git/blobs"), {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    branch = default_branch()
    user = pr["user"]
    names = ", ".join(sorted(validated["files"]))
    author = {"name": user["login"], "email": f"{user['id']}+{user['login']}@users.noreply.github.com"}

    commit_sha = None
    for attempt in range(MAX_ATTEMPTS):
        base_sha = get(repo_path(f"git/ref/heads/{branch}"))["object"]["sha"]
        # Race protection: nobody may have published the same folder in the meantime.
        for package_dir in validated["files"]:
            if path_exists(package_dir, base_sha):
                raise SystemExit(f"{package_dir} already exists. Overwriting is not allowed.")
        base_tree = get(repo_path(f"git/commits/{base_sha}"))["tree"]["sha"]
        tree = post(repo_path("git/trees"), {"base_tree": base_tree, "tree": entries})
        commit = post(repo_path("git/commits"), {
            "message": f"Publish {names} (from PR #{number})",
            "tree": tree["sha"],
            "parents": [base_sha],
            "author": author,
        })
        try:
            patch(repo_path(f"git/refs/heads/{branch}"), {"sha": commit["sha"], "force": False})
            commit_sha = commit["sha"]
            break
        except RuntimeError as exc:
            if "HTTP 422" in str(exc) and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))   # default branch moved; rebuild on top of it
                continue
            raise
    if not commit_sha:
        raise SystemExit("Could not update the default branch.")

    post(repo_path(f"issues/{number}/comments"), {
        "body": f"Published {names} to `{branch}` in {commit_sha[:7]}. "
                "Only the new package folder(s) were taken over; everything else in this PR was ignored."
    })
    patch(repo_path(f"pulls/{number}"), {"state": "closed"})

    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write(f"commit_sha={commit_sha}\n")
    print(f"Published {names} in {commit_sha}.")


if __name__ == "__main__":
    main()
