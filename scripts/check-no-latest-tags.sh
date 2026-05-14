#!/usr/bin/env bash
# Assert no `:latest` image tags appear in ops manifests (Dockerfiles,
# docker-compose, GitHub workflows, etc.).
#
# Background: issue #316 (and follow-up #566) — a bare `nats:latest` slipped
# into docker-compose.yml because no automated check rejected unpinned tags.
# Floating `:latest` tags break reproducibility and silently pull breaking
# upstream changes. Pin to an explicit version (and ideally a digest).
#
# Scope: files that actually pull/build images, i.e. Dockerfiles,
# docker-compose YAMLs, GitHub Actions workflows, and any other ops YAML.
# Markdown/comments are ignored (you can document `:latest` is forbidden).
#
# justfile parameter defaults (e.g. `docker-build tag="hermes:latest"`) are
# recipe arguments, not pulled images, and are out of scope here.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
cd "$ROOT"

# Files we scan. Keep this list intentionally narrow so docs/comments aren't
# falsely flagged; broaden it as new ops manifest types appear in the repo.
mapfile -t files < <(git ls-files -- \
  'Dockerfile*' '**/Dockerfile*' \
  'docker-compose*.yml' '**/docker-compose*.yml' \
  'docker-compose*.yaml' '**/docker-compose*.yaml' \
  'compose*.yml' '**/compose*.yml' \
  'compose*.yaml' '**/compose*.yaml' \
  '.github/workflows/*.yml' '.github/workflows/*.yaml')

if [ "${#files[@]}" -eq 0 ]; then
  echo "check-no-latest-tags: no ops manifests found, nothing to scan"
  exit 0
fi

# `<name>:latest` in image/FROM contexts. We require a leading character
# that's part of an image-name token ([A-Za-z0-9._/-]) so prose like
# "no :latest tags" or "the :latest spec" does NOT trigger. We also require
# the `:latest` to be at a token boundary (end-of-line, whitespace, quote,
# or `@<digest>`).
pattern_image='[A-Za-z0-9._/-]:latest([[:space:]]|$|"|'\''|@)'

# GitHub Actions floating action ref: `uses: owner/action@latest`.
pattern_uses_latest='uses:[[:space:]]*[^[:space:]]+@latest([[:space:]]|$)'

# Combined ERE for a single grep pass per file.
combined="(${pattern_image})|(${pattern_uses_latest})"

# `set +e` bracket is documented in CLAUDE.md as the approved alternative to
# `|| true` (which the forbid-suppressions guard rejects). We need grep's
# exit status here because "no match" (1) is the success case for us.
fail=0
for f in "${files[@]}"; do
  set +e
  raw=$(grep -nE "$combined" -- "$f" 2>/dev/null)
  rc=$?
  set -e
  # rc==0: match; rc==1: no match (good); rc>=2: real grep error.
  if [ "$rc" -ge 2 ]; then
    echo "::error file=$f::grep failed (exit $rc) while scanning"
    fail=1
    continue
  fi
  if [ "$rc" -ne 0 ]; then
    continue
  fi
  # Filter out full-line comments so a doc note like `# never use :latest`
  # doesn't trip the check.
  set +e
  hits=$(printf '%s\n' "$raw" | grep -vE '^[[:digit:]]+:[[:space:]]*#')
  hrc=$?
  set -e
  if [ "$hrc" -ne 0 ] || [ -z "$hits" ]; then
    continue
  fi
  echo "::error file=$f::found ':latest' / '@latest' — pin to an explicit version (and digest)"
  printf '%s\n' "$hits"
  fail=1
done

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "check-no-latest-tags: FAIL — replace ':latest' / '@latest' with a pinned version."
  exit 1
fi

echo "check-no-latest-tags: OK (${#files[@]} files scanned)"
