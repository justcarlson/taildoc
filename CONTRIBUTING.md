# Contributing

## Make a change

Create a branch from the current `main` branch.
Give each change one clear purpose.
Add a test for each new observable contract.
Do not add a test for an internal implementation detail.

Run the required checks:

```sh
python -m pytest -q
bash tests/smoke.sh
python tests/check_ste100.py
```

## Write controlled text

Use ASD-STE100 Simplified Technical English in documents and source comments.
Use short active sentences.
Write one instruction in each sentence.
Use one term for each component and action.
Use an explicit noun when a pronoun can be ambiguous.
Use no more than 20 words in an instruction.
Use no more than 25 words in a descriptive sentence.

The automated check finds overlength sentences and disallowed phrases.
A contributor must also review meaning and terminology.
The check does not prove full ASD-STE100 conformance.

Legal text and required code identifiers can keep their required form.
Record each legal-text or required-code-identifier exception in the pull request.

## Prepare a release

1. Update the version in `pyproject.toml` and `uv.lock`.
2. Keep an empty `Unreleased` section at the top of `CHANGELOG.md`.
3. Add the version and release date below the `Unreleased` section.
4. Run all commands from the required check section.
5. Review the complete tracked tree for credentials and private infrastructure.
6. Review the complete Git history before the first public release.
7. Create an SSH-signed annotated tag with the dedicated release signing key.
8. Verify the tag with `.github/release-allowed-signers`.
9. Push only that tag after all local checks pass.
10. Wait for the Release workflow to create a draft GitHub release.
11. Download the archive and checksum from the draft release.
12. Run both attestation verification commands from `README.md`.
13. Run `sha256sum --check tailplan-<version>.tar.gz.sha256`.
14. Test both native installers from the extracted release archive.
15. Test the remote client installer from the extracted release archive.
16. Compare the generated release notes with `CHANGELOG.md`.
17. Publish the draft release after all release checks pass.

Use these commands for release 0.2.1.
Keep the private release signing key outside the repository:

```sh
RELEASE_SIGNING_KEY='<path-to-private-release-signing-key>'
git \
  -c gpg.format=ssh \
  -c user.signingkey="$RELEASE_SIGNING_KEY" \
  tag --sign v0.2.1 -m 'Tailplan v0.2.1'
git \
  -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile=.github/release-allowed-signers \
  verify-tag v0.2.1
git push origin v0.2.1
```

The Release workflow fetches protected `main` and the pushed tag from the repository.
The workflow reads the allowed-signers trust anchor from the fetched `origin/main` commit.
The workflow rejects a lightweight tag or a tag object with a different name.
The workflow requires the tag target commit to equal the fetched `origin/main` commit.
Git verifies the SSH signature before tests, image builds, attestations, or release asset creation.

The verified annotated tag establishes source commit authenticity.
The release workflow attests the archive and checksum before release upload.
Each attestation binds an artifact digest to the repository workflow and verified source commit.
The SHA-256 result proves that the archive matches the attested checksum file.

Do not publish a release if the security scan finds credentials, private infrastructure, personal paths, or personal data.
