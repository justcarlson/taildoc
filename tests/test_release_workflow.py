from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"
ALLOWED_SIGNERS = Path(__file__).parents[1] / ".github" / "release-allowed-signers"



def workflow_sections() -> tuple[str, str, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    prefix, remainder = text.split("\n  verify-tag:\n", 1)
    verify, release = remainder.split("\n  release:\n", 1)
    return prefix, verify, release


def test_release_assets_require_an_ssh_signed_annotated_tag() -> None:
    prefix, verify, release = workflow_sections()

    assert "permissions:" not in prefix
    assert "contents: read" in verify
    assert "uses: actions/checkout@v4" in verify
    assert "ref: main" in verify
    assert '"refs/heads/main:${main_ref}"' in verify
    assert '"${main_ref}:.github/release-allowed-signers"' in verify
    assert '"${tag_ref}:${tag_ref}"' in verify
    assert 'git cat-file -t "$tag_ref"' in verify
    assert "git for-each-ref --format='%(tag)' \"$tag_ref\"" in verify
    assert '"$tag_commit" != "$main_commit"' in verify
    assert "-c gpg.format=ssh" in verify
    assert '-c gpg.ssh.allowedSignersFile="$allowed_signers"' in verify
    assert 'verify-tag "$tag_ref"' in verify
    assert "gh api" not in verify
    assert "verification.verified" not in verify
    assert "commit-sha=$tag_commit" in verify

    assert "needs: verify-tag" in release
    assert "contents: write" in release
    assert "ref: ${{ needs.verify-tag.outputs.commit-sha }}" in release
    assert "RELEASE_COMMIT: ${{ needs.verify-tag.outputs.commit-sha }}" in release
    assert 'git archive --format=tar.gz' in release
    assert '"$RELEASE_COMMIT"' in release


def test_release_allowed_signers_has_one_dedicated_ssh_key() -> None:
    lines = ALLOWED_SIGNERS.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1
    principal, key_type, public_key = lines[0].split()
    assert principal == "taildoc-release"
    assert key_type == "ssh-ed25519"
    assert public_key.startswith("AAAA")


def test_release_attests_versioned_assets_before_upload() -> None:
    _prefix, verify, release = workflow_sections()
    permissions = release.split("permissions:", 1)[1].split("steps:", 1)[0]

    assert "id-token: write" not in verify
    assert "attestations: write" not in verify
    assert {
        line.strip()
        for line in permissions.splitlines()
        if line.strip()
    } == {
        "contents: write",
        "id-token: write",
        "attestations: write",
    }
    assert "uses: actions/attest-build-provenance@v4" in release
    assert "tailplan-${{ needs.verify-tag.outputs.version }}.tar.gz" in release
    assert "tailplan-${{ needs.verify-tag.outputs.version }}.tar.gz.sha256" in release

    archive = release.index("git archive --format=tar.gz")
    attest = release.index("uses: actions/attest-build-provenance@v4")
    upload = release.index('gh release create "$GITHUB_REF_NAME"')
    assert archive < attest < upload


def test_tag_verification_precedes_root_capable_builds() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    signature = text.index('verify-tag "$tag_ref"')
    assert signature < text.index("docker build")
    assert signature < text.index("uses: actions/attest-build-provenance@v4")
