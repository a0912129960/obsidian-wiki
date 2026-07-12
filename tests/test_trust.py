"""Regression tests for manually reviewed confidence and relationship linting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from obsidian_wiki.lint import lint_vault
from obsidian_wiki.trust import build_trust_ledger, check_trust_ledger, write_trust_ledger


def _page(
    vault: Path,
    relpath: str,
    *,
    confidence: float = 0.80,
    updated: str = "2026-07-12T17:38:39+07:00",
    body: str = "Reviewed material claim.",
    relationships: list[tuple[str, str]] | None = None,
) -> Path:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {path.stem}",
        f"category: {path.parts[-2] if len(path.parts) > 1 else 'concepts'}",
        "tags: [test]",
        "sources:",
        "  - github.com/example/repo@0123456789abcdef0123456789abcdef01234567",
        "created: 2026-07-01",
        f"updated: {updated}",
        f"base_confidence: {confidence:.2f}",
        "lifecycle: reviewed",
    ]
    if relationships:
        lines.append("relationships:")
        for relation_type, target in relationships:
            lines.extend([f"  - type: {relation_type}", f'    target: "[[{target}]]"'])
    lines.extend(["---", f"# {path.stem}", "", body, ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_ledger(vault: Path) -> Path:
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    path = vault / "_meta" / "trust-ledger.json"
    write_trust_ledger(path, ledger)
    return path


def _run_cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_reviewed_ledger_is_authoritative_instead_of_reclassifying_sources(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", confidence=0.53)
    _page(vault, "skills/beta.md", confidence=0.80)
    ledger_path = _write_ledger(vault)

    report = check_trust_ledger(vault, ledger_path)

    assert report["status"] == "pass"
    assert report["counts"] == {
        "reviewed": 2,
        "stale": 0,
        "unreviewed": 0,
        "score_mismatches": 0,
        "missing_pages": 0,
        "errors": 0,
    }
    assert {item["page"] for item in report["reviewed"]} == {
        "concepts/alpha.md",
        "skills/beta.md",
    }


def test_claim_change_invalidates_review_but_updated_timestamp_does_not(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md", confidence=0.53)
    ledger_path = _write_ledger(vault)

    page.write_text(page.read_text().replace("updated: 2026-07-12T17:38:39+07:00", "updated: 2026-07-13T09:00:00+07:00"))
    assert check_trust_ledger(vault, ledger_path)["counts"]["reviewed"] == 1

    page.write_text(page.read_text().replace("Reviewed material claim.", "Changed material claim."))
    report = check_trust_ledger(vault, ledger_path)
    assert report["status"] == "warn"
    assert report["stale"] == [{"page": "concepts/alpha.md", "reason": "material_fingerprint_changed"}]


def test_confidence_change_is_reported_as_score_mismatch_not_stale_review(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md", confidence=0.53)
    ledger_path = _write_ledger(vault)

    page.write_text(page.read_text().replace("base_confidence: 0.53", "base_confidence: 0.88"))
    report = check_trust_ledger(vault, ledger_path)

    assert report["status"] == "fail"
    assert report["stale"] == []
    assert report["score_mismatches"] == [
        {"page": "concepts/alpha.md", "stored": 0.88, "reviewed": 0.53}
    ]


def test_new_page_requires_manual_review_instead_of_formula_guess(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    ledger_path = _write_ledger(vault)
    _page(vault, "concepts/new.md", confidence=0.95)

    report = check_trust_ledger(vault, ledger_path)

    assert report["status"] == "warn"
    assert report["unreviewed"] == [{"page": "concepts/new.md", "reason": "not_in_manual_ledger"}]


def test_non_object_ledger_fails_cleanly(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    ledger_path = vault / "_meta" / "trust-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text("[]\n")

    report = check_trust_ledger(vault, ledger_path)

    assert report["status"] == "fail"
    assert report["errors"] == [{"issue": "ledger_must_be_an_object"}]


def test_out_of_range_reviewed_confidence_is_invalid_ledger_data(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    ledger_path = _write_ledger(vault)
    ledger = json.loads(ledger_path.read_text())
    ledger["pages"]["concepts/alpha.md"]["reviewed_confidence"] = 2.0
    ledger_path.write_text(json.dumps(ledger))

    report = check_trust_ledger(vault, ledger_path)

    assert report["status"] == "fail"
    assert report["errors"] == [{"page": "concepts/alpha.md", "issue": "invalid_ledger_entry"}]


def test_non_object_page_entry_is_invalid_not_unreviewed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    ledger_path = _write_ledger(vault)
    ledger = json.loads(ledger_path.read_text())
    ledger["pages"]["concepts/alpha.md"] = []
    ledger_path.write_text(json.dumps(ledger))

    report = check_trust_ledger(vault, ledger_path)

    assert report["status"] == "fail"
    assert report["unreviewed"] == []
    assert report["errors"] == [{"page": "concepts/alpha.md", "issue": "invalid_ledger_entry"}]


def test_lint_vault_surfaces_manual_trust_status_without_formula_drift(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", confidence=0.53, relationships=[("related_to", "skills/beta")])
    _page(vault, "skills/beta.md", confidence=0.80, relationships=[("related_to", "concepts/alpha")])
    _write_ledger(vault)

    report = lint_vault(vault)

    assert report["findings"]["confidence_review_stale"] == []
    assert report["findings"]["confidence_unreviewed"] == []
    assert report["findings"]["confidence_mismatches"] == []
    assert report["findings"]["typed_relationship_issues"] == []
    assert report["stats"]["trust"]["reviewed"] == 2


def test_typed_relationship_resolver_uses_exact_vault_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "projects/demo/alpha.md", relationships=[("implements", "projects/demo/beta")])
    _page(vault, "projects/demo/beta.md", relationships=[("related_to", "projects/demo/alpha")])
    _page(vault, "concepts/broken.md", relationships=[("related_to", "skills/missing")])

    report = lint_vault(vault)

    assert report["findings"]["typed_relationship_issues"] == [
        {
            "page": "concepts/broken.md",
            "index": 0,
            "issue": "missing_target",
            "target": "skills/missing",
        }
    ]


def test_lint_excludes_bootstrap_scaffolding_from_page_health(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", relationships=[("related_to", "concepts/beta")])
    _page(vault, "concepts/beta.md", relationships=[("related_to", "concepts/alpha")])
    bootstrap = vault / "_bootstrap" / "README.md"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text("# Setup instructions\n")

    report = lint_vault(vault)

    serialized = json.dumps(report)
    assert "_bootstrap/README.md" not in serialized


def test_lint_flags_content_page_missing_trust_schema(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    page.write_text(
        "\n".join(
            line
            for line in page.read_text().splitlines()
            if not line.startswith(("base_confidence:", "lifecycle:"))
        )
        + "\n"
    )

    report = lint_vault(vault)

    assert report["status"] == "fail"
    assert report["findings"]["missing_frontmatter"] == [
        {"page": "concepts/alpha.md", "missing": ["base_confidence", "lifecycle"]}
    ]


def test_required_trust_ledger_fails_closed_when_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")

    report = lint_vault(vault, require_trust_ledger=True)

    assert report["status"] == "fail"
    assert report["findings"]["confidence_ledger_errors"] == [
        {"issue": "ledger_missing", "path": str(vault / "_meta" / "trust-ledger.json")}
    ]


def test_trust_record_cli_requires_explicit_approval(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")

    proc = _run_cli(
        home,
        "trust-record",
        str(vault),
        "--all",
        "--reviewed-at",
        "2026-07-12T17:38:39+07:00",
        "--json",
    )

    assert proc.returncode == 2
    assert "--approved" in proc.stderr
    assert not (vault / "_meta" / "trust-ledger.json").exists()


def test_trust_record_and_check_cli_round_trip(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", confidence=0.53)

    record = _run_cli(
        home,
        "trust-record",
        str(vault),
        "--all",
        "--reviewed-at",
        "2026-07-12T17:38:39+07:00",
        "--approved",
        "--json",
    )
    check = _run_cli(home, "trust-check", str(vault), "--json")

    assert record.returncode == 0, record.stderr
    assert json.loads(record.stdout)["recorded_pages"] == 1
    assert check.returncode == 0, check.stderr
    payload = json.loads(check.stdout)
    assert payload["status"] == "pass"
    assert payload["counts"]["reviewed"] == 1


def test_partial_trust_record_does_not_approve_other_stale_pages(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    alpha = _page(vault, "concepts/alpha.md", confidence=0.53)
    beta = _page(vault, "skills/beta.md", confidence=0.80)
    _write_ledger(vault)
    alpha.write_text(alpha.read_text().replace("Reviewed material claim.", "Reviewed alpha change."))
    beta.write_text(beta.read_text().replace("Reviewed material claim.", "Unreviewed beta change."))

    record = _run_cli(
        home,
        "trust-record",
        str(vault),
        "--page",
        "concepts/alpha.md",
        "--reviewed-at",
        "2026-07-13T09:00:00+07:00",
        "--approved",
        "--json",
    )
    check = _run_cli(home, "trust-check", str(vault), "--json")

    assert record.returncode == 0, record.stderr
    assert json.loads(record.stdout)["recorded_pages"] == 1
    payload = json.loads(check.stdout)
    assert payload["counts"]["reviewed"] == 1
    assert payload["stale"] == [
        {"page": "skills/beta.md", "reason": "material_fingerprint_changed"}
    ]


def test_partial_trust_record_reports_malformed_ledger_without_traceback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    ledger_path = vault / "_meta" / "trust-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text("[]\n")

    record = _run_cli(
        home,
        "trust-record",
        str(vault),
        "--page",
        "concepts/alpha.md",
        "--reviewed-at",
        "2026-07-13T09:00:00+07:00",
        "--approved",
        "--json",
    )

    assert record.returncode == 1
    assert "error: cannot update trust ledger" in record.stderr
    assert "Traceback" not in record.stderr
    assert ledger_path.read_text() == "[]\n"
