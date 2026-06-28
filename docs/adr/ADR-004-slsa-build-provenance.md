# ADR-004: SLSA Build Provenance and SBOM Attestation for GHCR Images

**Status:** Accepted
**Date:** 2026-06-20
**Deciders:** Hermes maintainers

---

## Context

Closes #491 (follow-up from #336). The publish workflow fires only on real release tags
(`v*.*.*`) and `workflow_dispatch`, so the GHCR image is a release artifact rather than a
per-commit build. Consumers need a way to verify that a given image was produced by this repo
at a known commit on a known GitHub-hosted runner, satisfying SLSA Level 1/2.

---

## Decision

Enable `provenance: mode=max` and `sbom: true` on the existing `docker/build-push-action` step
in `.github/workflows/publish.yml`. Grant the publish job `id-token: write` and
`attestations: write`.

**Choice: in-place edit over `slsa-github-generator`.** The reusable
`slsa-framework/slsa-github-generator` workflow splits build and signing across separate jobs
and re-pushes the image. `docker/build-push-action`'s built-in attestation support achieves
the same SLSA level in a single job, keeping the workflow shape identical to before.

**Choice: `mode=max` over `mode=min` (the `provenance: true` default).** `mode=min` omits the
build materials section, leaving the attestation cosmetic — it records that a build happened
but not what source inputs it consumed. `mode=max` records source materials, build arguments,
and the BuildKit frontend invocation, satisfying downstream supply-chain audits.

---

## Security Tradeoff: id-token: write

`id-token: write` lets any step in the publish job mint a GitHub OIDC token identifying the
workflow. This risk is mitigated by:

- Every action in `publish.yml` is pinned to a 40-character commit SHA, not a floating tag,
  so unreviewed action updates cannot introduce a new step silently:
  - `actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10`
  - `docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f`
  - `docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9`
  - `docker/metadata-action@c299e40c65443455700f0fdfc63efafe5b349051`
  - `docker/build-push-action@ca052bb54ab0790a636c9b5f226502c73d547a25`
- The job runs only on tag pushes matching `v*.*.*` and on `workflow_dispatch`; both require
  repo push access.
- The permission is at the job level, not workflow level, so any future sibling jobs do not
  inherit it.

We accept this tradeoff. Any future change that adds an unpinned action or a step that handles
untrusted input in this job must update this ADR.

---

## Verification

Consumers verify an image with:

```bash
gh attestation verify oci://ghcr.io/<org>/projecthermes:<tag> --owner <org>
```

The command exits non-zero if the attestation is absent, forged, or does not match the
expected repo/workflow identity.

---

## Reversal

The change is CI-only configuration plus one new test file. To roll back:

```bash
git revert <commit>
```

No runtime state, no data migration.

---

## References

- Issue #491, #336
- `docker/build-push-action` attestation docs:
  https://github.com/docker/build-push-action/blob/master/docs/advanced/attestations.md
- SLSA specification: https://slsa.dev/spec/v1.0/
- GitHub build attestations: https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds
