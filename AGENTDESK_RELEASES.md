# AgentDesk release publication

The fork-specific `.github/workflows/agentdesk-release.yml` validates an exact
main push on standard GitHub-hosted Ubuntu. Upstream synchronization is a
separate development operation. Publication never contacts or deploys a VPS.
The inherited workflows are disabled in this fork; reusable local PM actions
and build scripts remain available to this workflow.

The release job runs regression/lint checks, exports an image, reloads and tests
those exact exported bytes, and builds the canonical relocatable PM native
payload with its locked dependencies and compiled web product. The native
archive is unpacked and exercised through real dashboard and API-only gateway
processes. Both adapters must report the candidate revision through their
running health endpoint, survive restart/abrupt termination, and retain fresh
synthetic state. There are no production credentials, provider calls or copied
VPS configuration. Logs and temporary state stay on the disposable runner.

Image supervision also checks both fixed loopback health endpoints. After the
initial startup grace, six consecutive failed probes stop both processes and
exit for the existing container restart policy. A wrong running commit also
fails health. Disposable validation freezes a live dashboard with SIGSTOP,
requires the image supervisor to exit, starts it again and verifies recovery
with the same state volume. The host bootstrap delegates to this image-owned
supervisor, so subsequent runtime fixes arrive in the tested release artifact.

Only successful validation reaches attestation/publication. `release.json`
binds the exact commit, workflow, monotonically increasing run number, version,
notes, protocol/platform/state-schema compatibility, supported adapters,
SHA-256 archives, the original image configuration-blob digest and lifecycle results. Docker image stores can expose different local IDs for the same exported image; consumers compare its exact rootfs layers and normalized configuration to the verified archive before selecting a local immutable image ID. A tag is never an authorization decision. The manifest is attested
with GitHub's Sigstore integration, and its bundle is a release asset.

Consumers must run `gh attestation verify` with the expected repository,
`agentdesk-release.yml`, `refs/heads/main`, exact source digest and denial of
self-hosted runners. Tags and GitHub's latest designation are only discovery
hints. A frozen job stores the manifest digest, provenance-bundle digest, record and selected artifact;
workers reverify these before mutation. Repository/workflow administrators and
GitHub/Sigstore trust roots are trusted. A valid signature proves who published
the bytes; lifecycle checks, compatibility and artifact digests remain separate
requirements. A compromised authorized workflow can publish malicious code.

Publication uses one concurrency group plus an explicit current-main SHA and
strictly-newer sequence fence both before upload and immediately before publishing. An older queued
run, same-sequence rerun or superseded candidate cannot replace a release.
Release assets are uploaded without clobber to a draft, then the final step
publishes it with `latest=false`. Failed/cancelled validation publishes nothing;
an interrupted upload can leave a private draft requiring inspection. Previous
successful releases remain available.

Distribution uses GitHub Releases, not expiring Actions artifacts or paid
larger runners. Archives up to 8 GiB are split into ordered 1 GiB release assets when
necessary. The manifest binds every part size/digest plus the complete archive
digest. Consumers verify reassembled bytes before installation. Each asset
therefore remains below the 2 GiB GitHub limit. No Actions artifact upload is used. PM and Docker build caches share the
repository's default 10 GiB ceiling and can be evicted without affecting a
published release. Retain releases needed by supported installations and
recovery; removing them can prevent old jobs from re-verifying their payloads.

Official limits checked 2026-10-17:
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
  Standard public runners are free; larger runners are charged. GitHub Free
  has 500 MB of shared Actions/Packages artifact storage and 10 GB cache storage.
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
  Each release asset must be under 2 GiB; total release size and bandwidth have
  no documented limit.
- https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations
  Attestations are available for public repositories on GitHub Free.

## Acceptance requirements

Every main push must pass all gates before attestation and publication.
Do not call the update flow operational until two distinct main commits
have published and been installed without manual pins, including the real
Electron → persisted backend job → independent staging2 worker → runtime
restart → reconnection → verified final receipt test. Native host launchers and
fixed services also need a reviewed compatibility check before activation.
