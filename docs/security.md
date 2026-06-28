# Security Verification

## Secrets-scan end-to-end verification (#507, follow-up to #341)

The `security/secrets-scan` job in `.github/workflows/_required.yml`
(lines 299-339) runs Gitleaks with `--exit-code 1` so detected secrets block
PRs from merging. The regression tests in
`tests/test_workflow_secrets_scan.py` keep the workflow YAML honest, but only
an end-to-end run proves Gitleaks itself enforces the gate. This section
records how to verify that, and the outcome of the last run.

### How to verify (local)

```bash
# One-time install (matches GITLEAKS_VERSION in _required.yml:16):
curl -sSfL \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  | tar -xz gitleaks && sudo mv gitleaks /usr/local/bin/

just verify-secret-scan       # or: bash scripts/verify-secret-scan.sh
```

The script (`scripts/verify-secret-scan.sh`) creates a throwaway worktree,
plants a synthetic Stripe-style live key in a top-level file
`__secret_scan_tripwire__.txt` (outside the `tests/.*` allowlist in
`.gitleaks.toml`), runs the same command flags as the CI gate but with the
working-tree `.gitleaks.toml` (not the committed config at HEAD), and asserts
a non-zero exit. This tests the fix even before it's committed. The worktree
and branch are discarded on exit; nothing persists in the repo.

### Why the synthetic key is built at runtime, not stored here

The script constructs the key by concatenating two halves at runtime. Storing
the literal value anywhere under `git ls-files` — including this doc, the
script, or any test fixture outside the `tests/` allowlist — would itself be
flagged by every future PR's `security/secrets-scan` job, permanently breaking
the gate this verification exists to confirm. So:

- The script source contains only the prefix (`sk_live_`) and suffix as
  separate strings.
- This document refers to the key only via the obfuscated form
  `sk_live_<EXAMPLE-24-CHARS>`, which fails Gitleaks' Stripe rule because
  angle brackets are not in the alphanumeric character class.

### Captured outcome

Last verified: `2026-06-20` on Gitleaks `v8.30.1` against
`.github/workflows/_required.yml` at HEAD.

```
Finding:     stripe_api_key = sk_live_<EXAMPLE-24-CHARS>
RuleID:      stripe-access-token
Entropy:     4.687500
File:        __secret_scan_tripwire__.txt
leaks found: 1
gitleaks exit code: 1
```

The `RuleID` and `Entropy` values shown above were captured from a real run;
the `Finding` field is obfuscated here (`sk_live_<EXAMPLE-24-CHARS>`) for the
reason described above. The non-zero exit confirms the CI job would mark the
PR check failed, blocking merge.

### Root cause found during verification

Running this E2E test revealed that the original `.gitleaks.toml` lacked
`[extend] useDefault = true`. Without this stanza, Gitleaks treats the config
as a full replacement, loading zero rules — so the CI gate was silently passing
every PR even if a real secret was committed. This fix was applied in this PR
alongside the verification script.

### Why this is a manual procedure, not a CI job

A CI job that *expects* Gitleaks to fail would need either
`continue-on-error: true` or a `|| true` shim. Both are rejected by the
`forbid-suppressions` job in `.github/workflows/_required.yml:54-103` (the
regexes at lines 83 and 98). Keeping this as a `just` recipe avoids that
conflict while still giving a single-command reproducible test.
