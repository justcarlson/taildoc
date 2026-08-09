from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"


def workflow_sections() -> tuple[str, str, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    prefix, remainder = text.split("\n  verify-tag:\n", 1)
    verify, release = remainder.split("\n  release:\n", 1)
    return prefix, verify, release


def test_release_assets_require_a_verified_annotated_tag() -> None:
    prefix, verify, release = workflow_sections()

    assert "permissions:" not in prefix
    assert "contents: read" in verify
    assert "repos/${GITHUB_REPOSITORY}/git/ref/tags/${GITHUB_REF_NAME}" in verify
    assert '"$object_type" != "tag"' in verify
    assert "repos/${GITHUB_REPOSITORY}/git/tags/${tag_sha}" in verify
    assert ".verification.verified == true" in verify
    assert '.object.type == "commit"' in verify
    assert "commit-sha=$commit_sha" in verify

    assert "needs: verify-tag" in release
    assert "contents: write" in release
    assert "ref: ${{ needs.verify-tag.outputs.commit-sha }}" in release
    assert "RELEASE_COMMIT: ${{ needs.verify-tag.outputs.commit-sha }}" in release
    assert 'git archive --format=tar.gz' in release
    assert '"$RELEASE_COMMIT"' in release


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

    assert text.index(".verification.verified == true") < text.index("docker build")
