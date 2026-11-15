import json
import os
import re
from functools import cmp_to_key
from urllib.parse import urlparse

import yaml

from common import (
    PACKAGES_DIR, REQUIRE_HIGHER_VERSION, author_of, commit_author_is_bot,
    compare_versions, default_branch, get, get_file, list_package_dirs, load_json,
    package_dir_name, paginate, parse_package_dir, repo_path, safe_slug,
    safe_version, validated_file,
)

MAX_PACKAGES_PER_PR = 10
MAX_FILES_PER_PACKAGE = 100
SAFE_PART = re.compile(r"[A-Za-z0-9._-]{1,100}")


def fail(message):
    raise SystemExit(message)


def collect_submission(files):
    """Only files under Packages/<dir>/... matter. Everything else in the PR
    (other folders, deletions, README changes, a stale branch ...) is ignored
    and will NOT be merged. Returns {package_dir: [file paths]}."""
    new_files = {}
    ignored = 0
    for item in files:
        filename, status = item["filename"], item["status"]
        parts = filename.split("/")
        if parts[0] != PACKAGES_DIR or len(parts) < 3 or status == "removed":
            ignored += 1
            continue
        if status == "modified":
            fail(f"Existing files may not be changed: {filename}. "
                 "Published versions are immutable; publish a new version instead.")
        if status not in ("added", "renamed", "copied"):
            fail(f"Unsupported change type {status!r}: {filename}")
        if any(p in (".", "..") or not SAFE_PART.fullmatch(p) for p in parts):
            fail(f"Unsafe path (allowed characters: letters, digits, . _ -): {filename}")
        new_files.setdefault("/".join(parts[:2]), []).append(filename)
    print(f"Ignored {ignored} file(s) outside of new {PACKAGES_DIR}/<slug>-<version>/ folders.")
    return new_files


def validate_package(package_dir, paths, submitter, head_sha, index, on_disk_all):
    parsed = parse_package_dir(package_dir)
    if not parsed:
        fail(f"{package_dir}: folder must be named {PACKAGES_DIR}/<slug>-<semver>, e.g. {PACKAGES_DIR}/dose-1.0.0.")
    slug, version = parsed
    if package_dir != package_dir_name(slug, version):
        fail(f"{package_dir}: folder name is not canonical.")
    if len(paths) > MAX_FILES_PER_PACKAGE:
        fail(f"{package_dir}: a package may contain at most {MAX_FILES_PER_PACKAGE} files.")
    if package_dir + "/metadata.yaml" not in paths:
        fail(f"{package_dir}: metadata.yaml is required.")
    allowed_yaml = (package_dir + "/metadata.yaml", package_dir + "/config.yaml")
    if any(p.endswith((".yml", ".yaml")) and p not in allowed_yaml for p in paths):
        fail(f"{package_dir}: only metadata.yaml and config.yaml YAML files are accepted.")

    raw = get_file(package_dir + "/metadata.yaml", head_sha)
    if raw is None:
        fail(f"{package_dir}: could not read metadata.yaml from the PR head.")
    try:
        metadata = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        fail(f"{package_dir}: invalid metadata.yaml: {exc}")
    if not isinstance(metadata, dict):
        fail(f"{package_dir}: metadata.yaml must contain a YAML mapping.")
    required = ["slug", "name", "version", "type", "description", "author", "license"]
    missing = [k for k in required if not metadata.get(k)]
    if missing:
        fail(f"{package_dir}: missing required metadata fields: " + ", ".join(missing))

    author = author_of(metadata)
    if author is None:
        fail(f"{package_dir}: metadata.author must be a single name.")
    if author.lower() != submitter.lower():
        fail(f"{package_dir}: metadata.author is {author!r} but this PR was opened by {submitter!r}. "
             "The author must be your own GitHub username.")
    if not safe_slug(metadata["slug"]) or metadata["slug"] != slug:
        fail(f"{package_dir}: metadata.slug is {metadata['slug']!r}, folder slug is {slug!r}. They must be identical.")
    if not safe_version(metadata["version"]) or metadata["version"] != version:
        fail(f"{package_dir}: metadata.version is {metadata['version']!r}, folder version is {version!r}. "
             "They must be identical and valid SemVer.")

    # Ownership and version rules are checked against what is already on main.
    known = [e for e in index.get("packages", {}).values() if e.get("slug") == slug]
    on_disk = [v for s, v in on_disk_all if s == slug]
    if on_disk and not known:
        fail(f"Registry is inconsistent for slug {slug!r} (files exist but no index entry). Please contact the maintainers.")
    if known:
        owner = known[0].get("author", "")
        if str(owner).lower() != submitter.lower():
            fail(f"The package slug {slug!r} belongs to {owner!r}. Only the owner can publish new versions.")
    existing_versions = {e["version"] for e in known} | set(on_disk)
    if version in existing_versions:
        fail(f"{package_dir} already exists. Overwriting is not allowed; publish a new version instead.")
    if REQUIRE_HIGHER_VERSION and existing_versions:
        highest = max(existing_versions, key=cmp_to_key(compare_versions))
        if compare_versions(version, highest) <= 0:
            fail(f"{package_dir}: version {version} must be higher than the latest published version {highest}.")

    if not isinstance(metadata["name"], str) or len(metadata["name"]) > 100:
        fail(f"{package_dir}: metadata.name must be a string of at most 100 characters.")
    if not isinstance(metadata["description"], str) or len(metadata["description"]) > 1000:
        fail(f"{package_dir}: metadata.description must be a string of at most 1000 characters.")
    if not isinstance(metadata["license"], str) or len(metadata["license"]) > 50:
        fail(f"{package_dir}: metadata.license must be a string of at most 50 characters.")
    for key in ("dependencies", "compatibility"):
        if key in metadata and not isinstance(metadata[key], dict):
            fail(f"{package_dir}: metadata.{key} must be a mapping.")
    if "tags" in metadata:
        tags = metadata["tags"]
        if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(x, str) and len(x) <= 32 for x in tags):
            fail(f"{package_dir}: metadata.tags must be a list of at most 20 short strings.")
    if "external_repository" in metadata:
        url = metadata["external_repository"]
        u = urlparse(url) if isinstance(url, str) else None
        if (
            not u or u.scheme != "https" or u.netloc.lower() != "github.com"
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.@/-]+)?/?", u.path)
            or u.query or u.fragment or ".." in u.path.split("/")
        ):
            fail(f"{package_dir}: external_repository must be a safe HTTPS GitHub repository or tree URL.")

    config_raw = get_file(package_dir + "/config.yaml", head_sha)
    if config_raw is not None:
        try:
            config = yaml.safe_load(config_raw)
        except yaml.YAMLError as exc:
            fail(f"{package_dir}: invalid config.yaml: {exc}")
        if not isinstance(config, dict) or not isinstance(config.get("files", {}), dict):
            fail(f"{package_dir}: config.yaml must contain a files mapping.")
        submitted = {p[len(package_dir) + 1:] for p in paths}
        for source, settings in config.get("files", {}).items():
            if not isinstance(source, str) or source.startswith("/") or "\\" in source or ".." in source.split("/"):
                fail(f"{package_dir}: unsafe source path in config.yaml: {source}")
            if source not in submitted:
                fail(f"{package_dir}: config.yaml references a source file that is not in this package: {source}")
            if not isinstance(settings, dict):
                fail(f"{package_dir}: file settings must be a mapping: {source}")
            if "overwrite" in settings and not isinstance(settings["overwrite"], bool):
                fail(f"{package_dir}: overwrite must be true or false: {source}")
    return slug, version


def main():
    pr_number = int(os.environ["PR_NUMBER"])
    expected_head = os.environ["HEAD_SHA"]

    pr = get(repo_path(f"pulls/{pr_number}"))
    if not pr:
        fail("Pull request not found.")
    if pr["base"]["ref"] != default_branch():
        fail("Pull requests must target the default branch.")
    if pr["head"]["sha"] != expected_head:
        fail("PR head changed while the workflow was running. Push again or re-run.")

    submitter_user = pr.get("user")
    if commit_author_is_bot(submitter_user):
        fail("Submissions from bot accounts are not accepted.")
    submitter = submitter_user["login"]

    files = paginate(repo_path(f"pulls/{pr_number}/files"))
    new_packages = collect_submission(files)
    if not new_packages:
        hint = " (note: the folder must be spelled 'Packages' with a capital P)" \
            if any(f["filename"].lower().startswith("packages/") for f in files) else ""
        fail(f"No new package found. Add files under {PACKAGES_DIR}/<slug>-<version>/{hint}.")
    if len(new_packages) > MAX_PACKAGES_PER_PR:
        fail(f"A pull request may add at most {MAX_PACKAGES_PER_PR} package folders.")

    index = load_json("registry/index.json", {"schema_version": 1, "packages": {}})
    on_disk_all = list_package_dirs()
    existing_dirs = {package_dir_name(s, v).lower() for s, v in on_disk_all}

    for package_dir in new_packages:
        if package_dir.lower() in existing_dirs:
            fail(f"{package_dir} already exists. Overwriting is not allowed; publish a new version instead.")

    summary = []
    for package_dir, paths in sorted(new_packages.items()):
        slug, version = validate_package(package_dir, paths, submitter, pr["head"]["sha"], index, on_disk_all)
        summary.append(f"{slug}@{version}")

    with open(validated_file(), "w") as fh:
        json.dump({"head_sha": pr["head"]["sha"], "files": new_packages}, fh)
    print(f"Validated {', '.join(summary)} by {submitter} from PR #{pr_number}.")


if __name__ == "__main__":
    main()
