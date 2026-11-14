import os
from common import post, repo_path, get

number = int(os.environ["PR_NUMBER"])
head_sha = os.environ["HEAD_SHA"]
pr = get(repo_path(f"pulls/{number}"))
if pr.get("merged"):
    print("Pull request is already merged.")
    raise SystemExit(0)

# Pin the merge to the exact commit that was validated. If the author pushed
# another commit after validation, GitHub answers 409 and nothing is merged.
if pr["head"]["sha"] != head_sha:
    raise SystemExit("PR head changed after validation; refusing to merge.")

result = post(repo_path(f"pulls/{number}/merge"), {
    "commit_title": f"Publish package submission from PR #{number}",
    "merge_method": "squash",
    "sha": head_sha,
})
if not result or not result.get("merged"):
    raise SystemExit(f"GitHub did not confirm the merge: {result}")
print(f"Merged PR #{number}: {result.get('sha')}")
