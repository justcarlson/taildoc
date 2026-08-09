# Security policy

## Supported version

Security fixes apply to the latest tagged release.
Upgrade to the latest release before you report an old defect.

## Release source authenticity

The release workflow accepts only a cryptographically verified annotated tag.
The workflow rejects a lightweight tag.
The workflow reads the tag reference from `repos/<owner>/taildoc/git/ref/tags/<tag>`.
The workflow then requires `verification.verified == true` from `repos/<owner>/taildoc/git/tags/<tag-object-sha>`.

Use the commands in [README.md](README.md) to repeat the tag-reference and tag-object API checks.
Download the archive only when the tag-reference and tag-object API checks pass.
For each release asset, run the complete `gh attestation verify` command from `README.md`.

Attestation verification proves that the named repository workflow produced the release asset for the verified source commit.
The attestation signature also binds the release asset digest to that provenance record.
The SHA-256 result proves that the archive matches the attested checksum file.
The SHA-256 result does not authenticate the source by itself.

## Report a vulnerability

Use the repository **Report a vulnerability** function.
This function creates a private GitHub Security Advisory.
Include the affected version and the reproduction steps.
Include the expected effect and the observed effect.
Remove real upload tokens, private hostnames, and personal data.

Do not publish exploit details in a public issue.
If private reporting is not available, open a short issue.
Ask the maintainer to enable a private reporting channel.
Do not include exploit details, tokens, private hostnames, or personal data in that issue.

## Operator action

Revoke an exposed upload token immediately.
Create a new random upload token on the server.
Set the upload-token file mode to `0600`.
Restart the Tailplan service after upload-token replacement.
Remove any draft that contains content prohibited by `docs/security.md`.
