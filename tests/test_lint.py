"""Tests for vault linting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from obsidian_wiki.lint import lint_vault
from obsidian_wiki.trust import build_trust_ledger, write_trust_ledger


def _page(
    vault: Path,
    relpath: str,
    *,
    title: str | None = None,
    summary: str | None = "Short summary.",
    tags: str = "[test]",
    sources: str = "[manual]",
    created: str = "2026-07-01",
    updated: str = "2026-07-01",
    links: list[str] | None = None,
    include_frontmatter: bool = True,
) -> Path:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if include_frontmatter:
        lines.extend(
            [
                "---",
                f"title: {title or path.stem}",
                "category: concepts",
                f"tags: {tags}",
                f"sources: {sources}",
                f"created: {created}",
                f"updated: {updated}",
                "base_confidence: 0.80",
                "lifecycle: reviewed",
            ]
        )
        if summary is not None:
            lines.append(f"summary: {summary}")
        lines.append("---")
    lines.append(f"# {title or path.stem}")
    for link in links or []:
        lines.append(f"[[{link}]]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def test_lint_vault_passes_clean_graph(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "index.md", links=["alpha"])
    _page(vault, "log.md", links=["alpha"])
    _page(vault, "hot.md", links=["alpha"])
    _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger, vault=vault)

    report = lint_vault(vault)

    assert report["status"] == "pass"
    assert report["findings"]["broken_links"] == []
    assert report["findings"]["missing_frontmatter"] == []


def test_lint_vault_fails_on_broken_links_and_missing_frontmatter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", links=["ghost"])
    _page(vault, "concepts/beta.md", include_frontmatter=False)

    report = lint_vault(vault)

    assert report["status"] == "fail"
    assert report["findings"]["broken_links"] == [{"page": "concepts/alpha.md", "target": "ghost"}]
    assert any(item["page"] == "concepts/beta.md" for item in report["findings"]["missing_frontmatter"])


def test_lint_vault_warns_on_duplicates_missing_summaries_and_orphans(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", title="Same Title", summary=None)
    _page(vault, "references/beta.md", title="Same Title")
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger, vault=vault)

    report = lint_vault(vault)

    assert report["status"] == "warn"
    assert report["findings"]["duplicate_titles"]
    assert "concepts/alpha.md" in report["findings"]["missing_summaries"]
    assert "references/beta.md" in report["findings"]["orphan_pages"]


def test_lint_cli_uses_configured_vault_and_strict_mode(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", summary=None)

    config_dir = home / ".obsidian-wiki"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config").write_text(f'OBSIDIAN_VAULT_PATH="{vault}"\n', encoding="utf-8")
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger)

    proc = _run(home, "lint", "--json", "--strict")

    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["status"] == "warn"
    assert "concepts/alpha.md" in data["findings"]["missing_summaries"]


def test_lint_rejects_raw_asset_embed_from_formal_page(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    asset = vault / "_raw" / "assets" / "image.webp"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")
    _append(page, "![[_raw/assets/image.webp]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_embeds"] == [
        {"page": "concepts/alpha.md", "target": "_raw/assets/image.webp"}
    ]
    assert report["stats"]["attachments"] == 0
    assert report["stats"]["embed_count"] == 1


def test_lint_resolves_formal_attachment_embed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    asset = vault / "attachments" / "image.webp"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")
    _append(page, "![[attachments/image.webp]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_embeds"] == []


def test_lint_rejects_attachment_outside_formal_directory(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    asset = vault / "references" / "image.webp"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")
    _append(page, "![[image.webp]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_embeds"] == [
        {"page": "concepts/alpha.md", "target": "image.webp"}
    ]
    assert report["stats"]["attachments"] == 0


def test_lint_reports_missing_attachment_separately(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    _append(page, "![[missing.png]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_links"] == []
    assert report["findings"]["broken_embeds"] == [
        {"page": "concepts/alpha.md", "target": "missing.png"}
    ]
    assert report["status"] == "fail"


def test_lint_excludes_raw_markdown_from_page_lint(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    raw_page = _page(vault, "_raw/note.md", include_frontmatter=False)
    _append(raw_page, "[[missing-page]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["stats"]["pages"] == 0
    assert report["findings"]["broken_links"] == []
    assert report["findings"]["missing_frontmatter"] == []
    assert report["findings"]["orphan_pages"] == []


def test_lint_exempts_tag_taxonomy_from_content_page_requirements(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    taxonomy = vault / "_meta" / "taxonomy.md"
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text("---\ntitle: Tag Taxonomy\n---\n\n# Tag Taxonomy\n", encoding="utf-8")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["stats"]["pages"] == 1
    assert report["findings"]["missing_frontmatter"] == []
    assert report["findings"]["missing_summaries"] == []
    assert report["findings"]["orphan_pages"] == []


def test_lint_still_reports_missing_page_link(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", links=["missing-page"])

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_links"] == [
        {"page": "concepts/alpha.md", "target": "missing-page"}
    ]
    assert report["findings"]["broken_embeds"] == []


def test_lint_resolves_markdown_page_embed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    _append(page, "![[Existing Note]]")
    _page(vault, "concepts/existing-note.md", title="Existing Note", links=["alpha"])

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_links"] == []
    assert report["findings"]["broken_embeds"] == []
    assert report["stats"]["link_count"] == 2


def test_lint_resolves_sized_attachment_embed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    asset = vault / "attachments" / "image.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")
    _append(page, "![[image.png|300]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_embeds"] == []


def test_lint_resolves_pdf_page_and_height_suffix(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    asset = vault / "attachments" / "document.pdf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"pdf")
    _append(page, "![[attachments/document.pdf#page=2|500]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_embeds"] == []


def test_lint_reports_ambiguous_bare_attachment_name(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    for relpath in (
        "attachments/source-a/image.png",
        "attachments/source-b/image.png",
    ):
        asset = vault / relpath
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"image")
    _append(page, "![[image.png]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["ambiguous_embeds"] == [
        {
            "page": "concepts/alpha.md",
            "target": "image.png",
            "matches": [
                "attachments/source-a/image.png",
                "attachments/source-b/image.png",
            ],
        }
    ]
    assert report["status"] == "fail"


def test_lint_excludes_archived_raw_attachments(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    asset = vault / "_raw" / "_archived" / "assets" / "image.webp"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")
    _append(page, "![[image.webp]]")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_embeds"] == [
        {"page": "concepts/alpha.md", "target": "image.webp"}
    ]
    assert report["stats"]["attachments"] == 0


def test_lint_cli_strict_fails_for_broken_and_ambiguous_embeds(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    _append(page, "![[missing.png]]\n![[duplicate.pdf]]")
    for relpath in (
        "attachments/source-a/duplicate.pdf",
        "attachments/source-b/duplicate.pdf",
    ):
        asset = vault / relpath
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"pdf")
    config_dir = home / ".obsidian-wiki"
    config_dir.mkdir(parents=True)
    (config_dir / "config").write_text(f'OBSIDIAN_VAULT_PATH="{vault}"\n', encoding="utf-8")
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger)

    proc = _run(home, "lint", "--json", "--strict")

    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["findings"]["broken_embeds"]
    assert data["findings"]["ambiguous_embeds"]

    human = _run(home, "lint", "--strict")
    assert human.returncode == 1
    assert "broken_embeds: 1" in human.stdout
    assert "ambiguous_embeds: 1" in human.stdout
