"""Contract tests for the documented attachment lifecycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_canonical_skills_use_one_hashed_attachment_name_contract():
    architecture = _read(".skills/llm-wiki/SKILL.md")
    ingest = _read(".skills/wiki-ingest/SKILL.md")

    assert "<source-slug>-<purpose>-<hash8>.<ext>" in ingest
    assert "<slug>-architecture-<hash8>.<ext>" in architecture
    assert "<slug>-results-chart-<hash8>.<ext>" in architecture
    assert "attachments/<slug>-figN.<ext>" not in ingest
    assert "attachments/<slug>-fig1.png" not in architecture
    assert "attachments/<slug>-resultsN.png" not in architecture


def test_flat_raw_asset_pool_has_manifest_batch_ownership():
    architecture = _read(".skills/llm-wiki/SKILL.md")
    ingest = _read(".skills/wiki-ingest/SKILL.md")
    url_sources = _read(".skills/wiki-ingest/references/url-sources.md")

    for text in (architecture, ingest):
        assert "asset_batches" in text
        assert "asset_batch_id" in text
    assert "entire current flat pool" in ingest
    assert "entire resulting flat pool" in url_sources
    assert "raw_path" in ingest
    assert "published_path" in ingest
    assert "archived_path" in ingest


def test_staged_review_controls_attachment_publication_and_archival():
    ingest = _read(".skills/wiki-ingest/SKILL.md")
    stage_commit = _read(".skills/wiki-stage-commit/SKILL.md")

    assert "_staging/attachments/<published-name>" in ingest
    assert "Do not archive or clear `_raw/assets/` during ingest" in ingest
    assert "every associated page or patch is accepted" in stage_commit
    assert "Never delete or archive a raw original on rejection" in stage_commit
    assert "Keep the batch `awaiting_review`" in stage_commit


def test_entrypoint_docs_show_the_same_attachment_directories():
    for relative in ("AGENTS.md", "SETUP.md"):
        text = _read(relative)
        assert "attachments/" in text
        assert "_raw/assets/" in text
        assert "_raw/_archived/assets/" in text
        assert "_staging/attachments/" in text


def test_brain_capture_archives_promoted_raw_sources_instead_of_deleting_them():
    text = _read("extensions/brain-capture/README.md")

    assert "move successfully promoted `_raw/` sources into `_raw/_archived/`" in text
    assert "delete promoted `_raw/` files" not in text
