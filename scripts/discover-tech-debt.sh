#!/usr/bin/env bash
set -euo pipefail
echo "=== Technical Debt Discovery: ProjectHermes ==="
grep -rn "FIXME\|TODO\|DEPRECATED\|HACK\|XXX" \
  --include="*.py" --include="*.toml" --include="*.yml" \
  --exclude-dir='.git' --exclude-dir='.pixi' \
  . 2>/dev/null || echo "(none found)"
