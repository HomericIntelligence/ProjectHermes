#!/usr/bin/env bash
set -euo pipefail
echo "=== Technical Debt Discovery: ProjectHermes ==="
# grep -r exits 0 on match, 1 on no-match. We surface that as the script's own
# exit code so the CI workflow can decide whether to comment on Epic #544.
# `set -e` would abort on a non-match, so guard with `|| rc=$?`.
rc=0
grep -rn "FIXME\|TODO\|DEPRECATED\|HACK\|XXX" \
  --include="*.py" --include="*.toml" --include="*.yml" \
  --exclude-dir='.git' --exclude-dir='.pixi' \
  --exclude-dir='build' --exclude-dir='.claude' \
  . 2>/dev/null || rc=$?

if [ "$rc" -eq 0 ]; then
  # Markers found and already printed above; signal "dirty".
  exit 1
elif [ "$rc" -ge 2 ]; then
  # grep exits >=2 on a real error (bad path, regex error) — do not mask it as
  # a clean scan.
  echo "Tech-debt scan error: grep exited with status $rc." >&2
  exit "$rc"
else
  # rc == 1: no matches. Sentinel intentionally avoids the bare marker tokens so
  # it does not perpetuate noise when echoed into logs or comments.
  echo "Tech-debt scan clean: no debt markers found in source."
  exit 0
fi
