#!/usr/bin/env bash
# Assert every job in .github/workflows/*.yml declares timeout-minutes.
#
# Background: issue #420 — `check-jsonschema` validates schema syntax only,
# not the semantic rule that every job MUST set timeout-minutes. Without it,
# a hung step blocks the workflow until GitHub's 6-hour default, wasting CI
# minutes and stuck PRs. Reusable-workflow callers (job with top-level
# `uses:`) cannot accept timeout-minutes at the job level and are skipped
# with a `::notice::` annotation.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
cd "$ROOT"

mapfile -t files < <(git ls-files -- \
  '.github/workflows/*.yml' '.github/workflows/*.yaml')

if [ "${#files[@]}" -eq 0 ]; then
  echo "check-workflow-timeouts: no workflow files found, nothing to scan"
  exit 0
fi

python3 - "${files[@]}" <<'PY'
import sys
import yaml

failed = False
for path in sys.argv[1:]:
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        print(f"::error file={path}::workflow root is not a mapping")
        failed = True
        continue
    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict):
        print(f"::error file={path}::`jobs:` is not a mapping")
        failed = True
        continue
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            print(f"::error file={path}::job `{job_id}` is not a mapping")
            failed = True
            continue
        if "uses" in job:
            print(f"::notice file={path}::skipping reusable-workflow caller `{job_id}`")
            continue
        tm = job.get("timeout-minutes")
        if tm is None:
            print(
                f"::error file={path}::job `{job_id}` is missing required "
                f"`timeout-minutes` (see CLAUDE.md / issue #420)"
            )
            failed = True
            continue
        if not isinstance(tm, int) or tm <= 0:
            print(
                f"::error file={path}::job `{job_id}` `timeout-minutes` "
                f"must be a positive integer, got {tm!r}"
            )
            failed = True

sys.exit(1 if failed else 0)
PY

echo "check-workflow-timeouts: OK (${#files[@]} files scanned)"
