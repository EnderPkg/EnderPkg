# EnderPKG GitHub Actions starter

This is a starter implementation for a GitHub-hosted EnderPKG registry.

## Implemented policy

- Package submissions are pull requests adding exactly one new directory:
  `Packages/<slug>-<version>/` (e.g. `Packages/dose-1.0.0/`).
- Submission rules (all enforced by `scripts/validate_pr.py`, see
  "Submission rules" below): author = PR author, slug = folder name, new
  version required, only the slug owner may publish new versions.
- A PR is validated using trusted scripts from the default branch. The workflow
  does **not** check out or execute PR contents.
- Valid PRs are automatically squash-merged.
- A vote issue is created after merge.
- New packages start as `Potentially Unsafe`.
- 10 distinct eligible positive reactions (`+1`) immediately mark a package
  `Normal`.
- 10 distinct eligible negative reactions (`-1`) and a rejection ratio of at
  least 2:1 mark a package `Rejected`.
- At 48 hours, a package becomes `Normal` unless the rejection threshold was
  reached.
- Voting remains open after `Normal`; a later rejection threshold can still
  reject the package.
- Package reports set `reported: true`. They do not automatically delete or
  reject the package; clients should show an additional warning.
- Rejected packages remain in Git history and status data. The client should
  refuse installation by default.

## Required repository layout

```text
.github/
  workflows/
  ISSUE_TEMPLATE/
scripts/
  common.py
  validate_pr.py
  merge_pr.py
  publish.py
  evaluate_votes.py
  process_report.py
Packages/
  example-0.1.0/
    metadata.yaml
    config.yaml
    page.xml
    src/
registry/
  index.json
  status.json
```

Create initial registry files:

`registry/index.json`
```json
{"schema_version": 1, "packages": {}}
```

`registry/status.json`
```json
{"schema_version": 1, "packages": {}}
```

## Submission rules

For every PR the validator checks, in this order:

1. **Author.** `author` in `metadata.yaml` must equal the GitHub username that
   opened the PR (case-insensitive). Bot accounts are rejected.
2. **Slug / folder.** `slug` in `metadata.yaml` must be exactly the slug part of
   the folder name, and `version` must be exactly the version part:
   `Packages/dose-1.0.0/metadata.yaml` needs `slug: dose` and `version: 1.0.0`.
3. **New version.** A version that already exists for the slug is rejected;
   published versions are immutable. With `REQUIRE_HIGHER_VERSION = True`
   (in `scripts/common.py`) the new version must also be higher than every
   existing version (SemVer order, `1.0.0-rc.1 < 1.0.0`). A new version
   becomes the entry in `index.json -> latest[slug]`; older versions stay
   installable by exact version.
4. **Slug ownership.** The first author to publish a slug owns it. Only that
   user can publish further versions. Transfers must be done by a maintainer
   by editing `registry/index.json`.
5. **Registry consistency.** If files for a slug exist in `Packages/` but not
   in the index, submissions for that slug are blocked until a maintainer fixes it.
6. **Pinned merge.** The PR is validated and merged at the exact same commit
   SHA; a push after validation makes the merge fail.

`publish.py` re-checks rules 1 and 2 on the merged commit.

## Package metadata

Required fields in `metadata.yaml`:

```yaml
slug: example
name: Example
version: 0.1.0
type: library
description: An example package.
author: ExampleUser
license: MIT
dependencies:
  enderpkg: "^1.0.0"
compatibility:
  example: ">=1.20.0"
tags:
  - example
```

`external_repository` is optional. If present, it must be a safe HTTPS GitHub
repository URL; a path to a subdirectory is also accepted if the URL is on
github.com and contains no traversal segments.

`config.yaml` is optional at validation time. If present, the starter validates
that `files` is a mapping and that source paths do not contain traversal
segments. The actual client-side installer must independently validate both
source and destination paths.

## GitHub repository setup

1. Copy the files and folders in this archive into the registry repository.
2. Create `registry/index.json` and `registry/status.json` as shown above.
3. In **Settings → Actions → General → Workflow permissions**, enable
   **Read and write permissions**. The workflows still declare narrow
   per-workflow permissions.
4. Ensure Actions are allowed to create and approve pull requests if your
   repository requires that setting. This implementation uses the GitHub API
   to merge PRs; it does not create PRs.
5. Protect `.github/workflows/**`, `scripts/**`, and `registry/**` with
   CODEOWNERS / branch protection. Package PRs cannot modify these paths (only new files in `Packages/` are accepted).
6. Enable Issues and allow GitHub Actions to create issues.
7. Keep the default branch name stable and set it as the repository default.

## Security notes — read before enabling auto-merge

- `pull_request_target` has write permissions. It is used here only because
  publishing must be automatic. The workflow checks out **only the trusted
  default branch**, never the PR head, and executes only scripts from that
  trusted branch. Do not add steps that run package code, PR scripts, build
  hooks, or downloaded executables.
- Keep the workflow and scripts protected by branch rules and CODEOWNERS.
  Otherwise a malicious change to the default branch could weaken validation.
- This is metadata/path validation, not a Lua security audit. A package can
  still contain malicious Lua code. The `Potentially Unsafe` warning is not
  proof of safety or danger.
- GitHub reactions are public signals, not strong identity verification.
  Account age, reputation, or verified-human checks are not implemented.
- The `GITHUB_TOKEN` is scoped by each workflow's `permissions` block.
- Pin third-party Actions to full commit SHAs. The SHAs in the workflow are
  pinned references; review them before adopting or updating them.
- GitHub Actions scheduled runs can be delayed. The 30-minute cadence is a
  best-effort evaluation interval, not an exact timer.
- This starter stores registry data in JSON committed to the default branch.
  For a production registry, consider adding atomic Git-tree commits, audit
  logs, stronger spam controls, and a documented appeal process.

## Important client contract

The client should read `registry/index.json` and `registry/status.json` from
the registry's raw GitHub content or API endpoint.

For a package key such as `example@0.1.0`:

- `Potentially Unsafe`: show a prominent warning and require confirmation.
- `Normal`: no initial community warning, but do not imply a security audit.
- `Reported`: if `reported: true`, show an additional report warning regardless
  of the main status.
- `Rejected`: block installation by default and explain the rejection.

Do not trust package-supplied paths blindly. The client must reject absolute
paths, `..`, drive prefixes, backslashes where unsupported, and any resolved
path outside its allowed installation root. Validate SHA-256 checksums and pin
downloads to an immutable commit or release when you add those fields.

## Known limitations to address before production

1. GitHub API pagination and rate limits apply.
2. The validator currently accepts package payloads as data only; it does not
   scan Lua for malicious behavior.
3. Vote issues are created by the Actions bot. The author exclusion uses the
   `author` value in `metadata.yaml`, which is now verified to be the GitHub
   account that opened the PR.
4. The current validator permits up to 100 added files but does not yet enforce
   per-file or total package size limits.
5. Rejected packages are marked in status and their vote issue is closed; their
   package source is intentionally retained for audit/history. The client must
   consult status and not offer rejected packages as installable.
6. Two PRs for the same *new* slug opened at the same moment by different
   users are validated independently; the first one merged wins, the
   second's ownership check only fails if it runs after the first publish
   finished. A global queue is not used because GitHub concurrency groups
   drop all but one pending run.
7. The contents API lists at most 1000 entries in `Packages/`; switch
   `list_package_dirs()` to the Git trees API when the registry grows beyond that.
8. This implementation uses GitHub Issues and reactions. Users need repository
   access sufficient to view and react to issues.
