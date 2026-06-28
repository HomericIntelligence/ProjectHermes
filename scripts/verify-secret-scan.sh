#!/usr/bin/env bash
# End-to-end verification for issue #507 (follow-up to #341).
# Confirms the security/secrets-scan job's Gitleaks command actually fails
# when a real-looking secret is committed.  See docs/security.md.
#
# Usage:  bash scripts/verify-secret-scan.sh
# Exit:   0 if Gitleaks correctly blocked, non-zero otherwise.

set -euo pipefail

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "::error::gitleaks not on PATH." >&2
  echo "Install (matches GITLEAKS_VERSION in .github/workflows/_required.yml:16):" >&2
  echo "  curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz | tar -xz gitleaks && sudo mv gitleaks /usr/local/bin/" >&2
  exit 2
fi

REPO_ROOT=$(git rev-parse --show-toplevel)
WORKTREE=$(mktemp -d -t hermes-secret-scan-XXXXXX)
BRANCH="verify-secret-scan-$$"
TRIPWIRE_FILE="__secret_scan_tripwire__.txt"

# Build the synthetic Stripe live key at RUNTIME by concatenation so this
# script itself does not contain a literal that matches Gitleaks' stripe-access-token
# rule (sk_live_[0-9a-zA-Z]{24}).  Neither half alone matches the rule.
PREFIX="sk_live_"
SUFFIX="1234567890abcdefghijklmn"
FAKE_STRIPE_KEY="${PREFIX}${SUFFIX}"

# Cleanup uses the repo's established "|| echo WARN" idiom (see
# scripts/smoke-readonly-fs.sh:31-32), not "|| true" — the latter is
# rejected by the forbid-suppressions job at _required.yml:54-103.
cleanup() {
  git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || \
    echo "WARN: 'git worktree remove' failed for $WORKTREE during cleanup" >&2
  git -C "$REPO_ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || \
    echo "WARN: 'git branch -D $BRANCH' failed during cleanup" >&2
  rm -rf "$WORKTREE"
}
trap cleanup EXIT

echo "==> Creating throwaway worktree at $WORKTREE on branch $BRANCH"
git -C "$REPO_ROOT" worktree add -B "$BRANCH" "$WORKTREE" HEAD >/dev/null

# Propagate the working-tree .gitleaks.toml into the worktree so the scan
# uses the current config (which may not be committed yet at HEAD).
cp "$REPO_ROOT/.gitleaks.toml" "$WORKTREE/.gitleaks.toml"

echo "==> Planting synthetic Stripe-style secret at $TRIPWIRE_FILE (outside tests/ allowlist)"
printf 'stripe_api_key = %s\n' "$FAKE_STRIPE_KEY" > "$WORKTREE/$TRIPWIRE_FILE"
git -C "$WORKTREE" add "$TRIPWIRE_FILE" .gitleaks.toml
git -C "$WORKTREE" -c user.email=verify@local -c user.name=verify \
  commit -m "test: synthetic secret for issue #507 verification" >/dev/null

echo "==> Running CI-equivalent Gitleaks command (with --verbose for outcome capture)"
REPORT="$WORKTREE/gitleaks.sarif"
set +e
( cd "$WORKTREE" && gitleaks detect --source . --config .gitleaks.toml \
    --report-format sarif --report-path "$REPORT" \
    --exit-code 1 --verbose )
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
  echo "::error::Gitleaks exited 0 — secret NOT detected. The gate is broken." >&2
  exit 1
fi

echo ""
echo "==> SUCCESS: Gitleaks exited rc=$rc — synthetic secret correctly blocked."
echo "    SARIF report (will be cleaned up on exit): $REPORT"
echo ""
echo "    Copy the 'Finding:' / 'RuleID:' / 'Entropy:' lines above into the"
echo "    'Captured outcome' block in docs/security.md."
