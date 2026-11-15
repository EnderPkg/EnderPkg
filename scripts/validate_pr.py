import os
import re
from functools import cmp_to_key
from urllib.parse import urlparse

import yaml

from common import (
    PACKAGES_DIR, REQUIRE_HIGHER_VERSION, commit_author_is_bot, compare_versions,
    default_branch, get, get_file, list_package_dirs, load_json, package_dir_name,
    paginate, parse_package_dir, repo_path, safe_slug, safe_version,
)

pr_number = int(os.environ["PR_NUMBER"])
expected_head = os.environ["HEAD_SHA"]

pr = get(repo_path(f"pulls/{pr_number}"))
if not pr:
    raise SystemExit("Pull request not found.")
    
repository = get(repo_path(""))

if not repository:
    raise SystemExit(
        "Could not retrieve repository information from GitHub API. "
        "Check the repository name and GitHub token permissions."
    )

if pr["base"]["ref"] != default_branch():
    raise SystemExit("Pull requests must target the default branch.")
    
# Validate exactly the commit the workflow was triggered for; the merge step
# later pins the merge to the same SHA.
if pr["head"]["sha"] != expected_head:
    raise SystemExit("PR head changed while the workflow was running. Push again or re-run.")

# ---------------------------------------------------------------------------
# Who is submitting? GitHub authenticates this, so it cannot be faked.
# ---------------------------------------------------------------------------
submitter_user = pr.get("user")
if commit_author_is_bot(submitter_user):
    raise SystemExit("Submissions from bot accounts are not accepted.")
submitter = submitter_user["login"]

# ---------------------------------------------------------------------------
# File-level checks: exactly one NEW directory Packages/<slug>-<version>/
# ---------------------------------------------------------------------------
files = paginate(repo_path(f"pulls/{pr_number}/files"))
if not files:
    raise SystemExit("Pull request contains no files.")
if len(files) > 100:
    raise SystemExit("A package submission may change at most 100 files.")

package_dirs = set()
for item in files:
    filename = item["filename"]
    if item["status"] != "added":
        raise SystemExit(f"Only newly added files are accepted; found {item['status']}: {filename}")
    parts = filename.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise SystemExit(f"Unsafe path: {filename}")
    if len(parts) < 3 or parts[0] != PACKAGES_DIR:
        raise SystemExit(f"Only files under {PACKAGES_DIR}/<slug>-<version>/ are allowed: {filename}")
    package_dirs.add("/".join(parts[:2]))

if len(package_dirs) != 1:
    raise SystemExit("Each pull request must submit exactly one package directory.")

package_dir = package_dirs.pop()
parsed = parse_package_dir(package_dir)
if not parsed:
    raise SystemExit(f"Package directory must be named {PACKAGES_DIR}/<slug>-<semver>, e.g. {PACKAGES_DIR}/dose-1.0.0.")
dir_slug, dir_version = parsed

if not any(x["filename"] == package_dir + "/metadata.yaml" for x in files):
    raise SystemExit("metadata.yaml is required.")
if any(x["filename"].endswith((".yml", ".yaml")) and x["filename"] not in (package_dir + "/metadata.yaml", package_dir + "/config.yaml") for x in files):
    raise SystemExit("Only metadata.yaml and config.yaml YAML files are accepted in package submissions.")

# ---------------------------------------------------------------------------
# metadata.yaml
# ---------------------------------------------------------------------------
raw = get_file(package_dir + "/metadata.yaml", pr["head"]["sha"])
if raw is None:
    raise SystemExit("Could not read metadata.yaml from the PR head.")
try:
    metadata = yaml.safe_load(raw)
except yaml.YAMLError as exc:
    raise SystemExit(f"Invalid metadata.yaml: {exc}")

if not isinstance(metadata, dict):
    raise SystemExit("metadata.yaml must contain a YAML mapping.")
required = ["slug", "name", "version", "type", "description", "author", "license"]
missing = [key for key in required if not metadata.get(key)]
if missing:
    raise SystemExit("Missing required metadata fields: " + ", ".join(missing))

# Rule 1: metadata author == GitHub username of the person who opened the PR.
if not isinstance(metadata["author"], str) or metadata["author"].lower() != submitter.lower():
    raise SystemExit(
        f"metadata.author is {metadata['author']!r} but this PR was opened by {submitter!r}. "
        "The author must be your own GitHub username."
    )

# Rule 2: metadata slug == folder name (slug part), version == folder version.
if not safe_slug(metadata["slug"]) or metadata["slug"] != dir_slug:
    raise SystemExit(
        f"metadata.slug is {metadata['slug']!r} but the folder is {package_dir!r} "
        f"(slug {dir_slug!r}). They must be identical (lowercase letters, digits, hyphens)."
    )
if not safe_version(metadata["version"]) or metadata["version"] != dir_version:
    raise SystemExit(
        f"metadata.version is {metadata['version']!r} but the folder is {package_dir!r} "
        f"(version {dir_version!r}). They must be identical and valid SemVer."
    )
slug, version = dir_slug, dir_version
if package_dir != package_dir_name(slug, version):
    raise SystemExit("Package directory name is not canonical.")

# Rule 3: ownership of the slug and a NEW version.
index = load_json("registry/index.json", {"schema_version": 1, "packages": {}})
known = [e for e in index.get("packages", {}).values() if e.get("slug") == slug]
on_disk = [v for s, v in list_package_dirs() if s == slug]

if on_disk and not known:
    raise SystemExit(f"Registry is inconsistent for slug {slug!r} (files exist but no index entry). Please contact the maintainers.")

if known:
    owner = known[0].get("author", "")
    if owner.lower() != submitter.lower():
        raise SystemExit(f"The package slug {slug!r} belongs to {owner!r}. Only the owner can publish new versions.")

existing_versions = {e["version"] for e in known} | set(on_disk)
if version in existing_versions:
    raise SystemExit(f"Version {version} of {slug!r} already exists. Published versions are immutable; publish a new version instead.")
if REQUIRE_HIGHER_VERSION and existing_versions:
    highest = max(existing_versions, key=cmp_to_key(compare_versions))
    if compare_versions(version, highest) <= 0:
        raise SystemExit(f"Version {version} must be higher than the latest published version {highest} of {slug!r}.")

# ---------------------------------------------------------------------------
# Remaining field validation
# ---------------------------------------------------------------------------
if not isinstance(metadata["name"], str) or len(metadata["name"]) > 100:
    raise SystemExit("metadata.name must be a string of at most 100 characters.")
if not isinstance(metadata["description"], str) or len(metadata["description"]) > 1000:
    raise SystemExit("metadata.description must be a string of at most 1000 characters.")
if not isinstance(metadata["license"], str) or len(metadata["license"]) > 50:
    raise SystemExit("metadata.license must be a string of at most 50 characters.")
for key in ("dependencies", "compatibility"):
    if key in metadata and not isinstance(metadata[key], dict):
        raise SystemExit(f"metadata.{key} must be a mapping.")
if "tags" in metadata:
    if not isinstance(metadata["tags"], list) or len(metadata["tags"]) > 20 or not all(isinstance(x, str) and len(x) <= 32 for x in metadata["tags"]):
        raise SystemExit("metadata.tags must be a list of at most 20 short strings.")
if "external_repository" in metadata:
    url = metadata["external_repository"]
    parsed_url = urlparse(url) if isinstance(url, str) else None
    if (
        not parsed_url
        or parsed_url.scheme != "https"
        or parsed_url.netloc.lower() != "github.com"
        or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.@/-]+)?/?", parsed_url.path)
        or parsed_url.query
        or parsed_url.fragment
        or ".." in parsed_url.path.split("/")
    ):
        raise SystemExit("external_repository must be a safe HTTPS GitHub repository or tree URL.")

config_raw = get_file(package_dir + "/config.yaml", pr["head"]["sha"])
if config_raw is not None:
    try:
        config = yaml.safe_load(config_raw)
    except yaml.YAMLError as exc:
        raise SystemExit(f"Invalid config.yaml: {exc}")
    if not isinstance(config, dict) or not isinstance(config.get("files", {}), dict):
        raise SystemExit("config.yaml must contain a files mapping.")
    submitted_paths = {x["filename"][len(package_dir) + 1:] for x in files}
    for source, settings in config.get("files", {}).items():
        if not isinstance(source, str) or source.startswith("/") or "\\" in source or ".." in source.split("/"):
            raise SystemExit(f"Unsafe source path in config.yaml: {source}")
        if source not in submitted_paths:
            raise SystemExit(f"config.yaml references a source file that is not in this package: {source}")
        if not isinstance(settings, dict):
            raise SystemExit(f"File settings must be a mapping: {source}")
        if "overwrite" in settings and not isinstance(settings["overwrite"], bool):
            raise SystemExit(f"overwrite must be true or false: {source}")

print(f"Validated package {slug}@{version} by {submitter} from PR #{pr_number}.")
