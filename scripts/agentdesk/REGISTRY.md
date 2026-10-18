# Registry release delivery

The main-branch workflow builds with the existing locked Dockerfile and native
bundle tooling, imports its transient export in a separate publisher job, then
validates the exact pulled registry digest on a fresh daemon. It publishes the
GitHub-attested release record only after regression, native lifecycle and pulled
image lifecycle checks succeed. Both publication-order fences remain mandatory.

Configure `DOCKERHUB_USERNAME`, `HERMES_IMAGE_REPOSITORY` (canonical
`docker.io/<owner>/<repo>`) and separate GitHub secrets `DOCKERHUB_PUSH_TOKEN`
(read/write, without delete) and `DOCKERHUB_PULL_TOKEN` (read only). Use private
stdin credential entry. Personal tokens have account-wide scope. Builds and
running candidates never receive publishing credentials; validation logs out
before running a candidate. Keep repositories private until image layers,
history, private source and licensing have been reviewed for public distribution.

New image artifacts have `transport=registry`, repository, manifest digest,
config imageId and linux/amd64 platform. The sha256 compatibility field equals
the manifest digest without its prefix. Native artifacts remain checksummed
installation bundles. Candidate tags do not authorize updates. Repository and
workflow administrators remain trusted by GitHub's provenance mechanism.

Image archives are not permanent release assets. The one build-to-publisher
transfer has one-day Actions retention so the publisher cannot execute build
scripts. This still consumes temporary storage. The owner authorized a $4 Actions budget
on 2026-10-18; keep transfers to one-day retention and use standard hosted runners.
Keep native bundles, release records, proofs and supported/recovery registry
images. Do not automatically prune referenced digests.

Migration is not yet accepted: Docker Hub credential preflight passed. Supporting
image migration and two distinct Electron-triggered registry updates remain
required. Existing archive-based acceptance is separate. Publication never
updates production. No production rollout is authorized by this workflow.
