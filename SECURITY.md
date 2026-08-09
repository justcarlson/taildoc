# Security policy

## Supported version

Security fixes apply to the latest tagged release.
Upgrade to the latest release before you report an old defect.

## Release source authenticity

The release workflow checks out protected `main`.
The workflow loads `.github/release-allowed-signers` from the fetched `origin/main` commit.
The file authorizes principal `taildoc-release` with the dedicated public SSH key.
The workflow fetches the pushed tag and rejects a lightweight tag.
The workflow requires the tag object name to match the pushed tag reference.
The workflow requires the tag target commit to equal the fetched `origin/main` commit.
Git verifies the tag SSH signature with the allowed-signers trust anchor before tests or builds.

Use the commands in [README.md](README.md) to repeat the tag-object, tag-name, commit, and SSH signature checks.
Download the archive only when all source verification commands pass.
For each release asset, run the complete `gh attestation verify` command from `README.md`.

The SSH signature proves that a key authorized by protected `main` signed the annotated tag object.
The commit check proves that the signed tag identifies the protected `main` commit.
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
