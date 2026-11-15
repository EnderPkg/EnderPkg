import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import PurePosixPath

API = "https://api.github.com"
TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ["GH_REPOSITORY"]
OWNER, NAME = REPO.split("/", 1)
HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "EnderPKG-Registry-Automation",
}

def request(method, path, data=None, expected=(200, 201, 202, 204)):
    url = path if path.startswith("https://") else f"{API}{path}"
    body = None if data is None else json.dumps(data).encode()
    headers = dict(HEADERS)
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if response.status not in expected:
                raise RuntimeError(f"GitHub API returned HTTP {response.status}: {raw[:500]!r}")
            return None if not raw else json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail[:1000]}") from exc

def get(path):
    return request("GET", path)

def post(path, data):
    return request("POST", path, data)

def patch(path, data):
    return request("PATCH", path, data)

def put(path, data):
    return request("PUT", path, data)

def paginate(path):
    page = 1
    out = []
    while True:
        sep = "&" if "?" in path else "?"
        batch = get(f"{path}{sep}per_page=100&page={page}") or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1

def repo_path(path):
    return f"/repos/{OWNER}/{NAME}/{path.lstrip('/')}"

def get_file(path, ref=None):
    q = "" if ref is None else "?ref=" + urllib.parse.quote(ref, safe="")
    item = get(repo_path("contents/" + urllib.parse.quote(path, safe="/") + q))
    if not item:
        return None
    if item.get("type") != "file":
        raise RuntimeError(f"Expected a file at {path}")
    return base64.b64decode(item["content"]).decode("utf-8")

def put_file(path, content, message, branch=None):
    current = get_file_metadata(path, branch)
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if current:
        payload["sha"] = current["sha"]
    if branch:
        payload["branch"] = branch
    return put(repo_path("contents/" + urllib.parse.quote(path, safe="/")), payload)

def get_file_metadata(path, ref=None):
    q = "" if ref is None else "?ref=" + urllib.parse.quote(ref, safe="")
    return get(repo_path("contents/" + urllib.parse.quote(path, safe="/") + q))
    
def default_branch():
    repository = get(f"/repos/{OWNER}/{NAME}")

    if not repository:
        raise RuntimeError(
            f"Could not retrieve repository information for {OWNER}/{NAME}."
        )

    branch = repository.get("default_branch")

    if not branch:
        raise RuntimeError(
            f"Could not determine the default branch for {OWNER}/{NAME}."
        )

    return branch

# --- Package layout / submission policy ---------------------------------
PACKAGES_DIR = "Packages"          # folder layout: Packages/<slug>-<version>/
# True: a new version of an existing slug must be strictly HIGHER than every
# existing version. False: it only has to be different from existing ones.
REQUIRE_HIGHER_VERSION = True

_SEMVER = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"

def safe_slug(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is not None

def safe_version(value):
    # Plain SemVer (optionally with a pre-release tag). Build metadata ("+...")
    # is not allowed because the version is part of the directory name.
    return isinstance(value, str) and re.fullmatch(_SEMVER, value) is not None

def package_dir_name(slug, version):
    return f"{PACKAGES_DIR}/{slug}-{version}"

def parse_package_dir(path):
    """'Packages/dose-1.0.0' -> ('dose', '1.0.0'). Slugs cannot contain dots,
    so the split between slug and version is unambiguous."""
    m = re.fullmatch(rf"{re.escape(PACKAGES_DIR)}/([a-z0-9][a-z0-9-]{{0,63}})-({_SEMVER})", path)
    return (m.group(1), m.group(2)) if m else None

def compare_versions(a, b):
    """SemVer precedence: returns -1, 0 or 1."""
    core_a, _, pre_a = a.partition("-")
    core_b, _, pre_b = b.partition("-")
    ta = tuple(int(x) for x in core_a.split("."))
    tb = tuple(int(x) for x in core_b.split("."))
    if ta != tb:
        return -1 if ta < tb else 1
    if pre_a == pre_b:
        return 0
    if not pre_a:
        return 1          # a release is higher than its pre-release
    if not pre_b:
        return -1
    for x, y in zip(pre_a.split("."), pre_b.split(".")):
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            if int(x) != int(y):
                return -1 if int(x) < int(y) else 1
        elif x_num:
            return -1
        elif y_num:
            return 1
        elif x != y:
            return -1 if x < y else 1
    la, lb = len(pre_a.split(".")), len(pre_b.split("."))
    return 0 if la == lb else (-1 if la < lb else 1)

def list_package_dirs():
    """All (slug, version) pairs found in Packages/ on the default branch.
    Note: the GitHub contents API lists at most 1000 entries per directory."""
    items = get(repo_path("contents/" + PACKAGES_DIR))
    out = []
    for item in items or []:
        if item.get("type") == "dir":
            parsed = parse_package_dir(f"{PACKAGES_DIR}/{item['name']}")
            if parsed:
                out.append(parsed)
    return out

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def load_json(path, default):
    raw = get_file(path)
    return default if raw is None else json.loads(raw)

def save_json(path, value, message):
    put_file(path, json.dumps(value, indent=2, sort_keys=True) + "\n", message)

def ensure_label(name, color, description):
    labels = paginate(repo_path("labels"))
    if any(x["name"].lower() == name.lower() for x in labels):
        return
    post(repo_path("labels"), {"name": name, "color": color, "description": description[:100]})

def add_label(issue_number, label):
    post(repo_path(f"issues/{issue_number}/labels"), {"labels": [label]})

def remove_label(issue_number, label):
    request("DELETE", repo_path(f"issues/{issue_number}/labels/{urllib.parse.quote(label, safe='')}"), expected=(200, 204))

def find_issue(title):
    issues = paginate(repo_path("issues?state=all"))
    for issue in issues:
        if issue.get("title") == title and "pull_request" not in issue:
            return issue
    return None

def create_issue(title, body, labels):
    existing = find_issue(title)
    if existing:
        return existing
    return post(repo_path("issues"), {"title": title, "body": body, "labels": labels})

def issue_reactions(number):
    return paginate(repo_path(f"issues/{number}/reactions"))

def commit_author_is_bot(user):
    return not user or user.get("type") == "Bot" or (user.get("login") or "").endswith("[bot]")

def package_status_key(slug, version):
    return f"{slug}@{version}"
