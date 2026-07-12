"""Manual trust-review ledger for confidence linting.

Confidence depends on semantic judgments that source-string heuristics cannot make:
independent evidence lineages must be collapsed and material claim coverage must be
reviewed.  This module therefore validates an approved review ledger instead of
pretending to recompute confidence from URLs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

TRUST_LEDGER_RELATIVE_PATH = Path("_meta/trust-ledger.json")
TRUST_LEDGER_SCHEMA_VERSION = 1
TRUST_REVIEW_METHOD = "manual-lineage-and-claim-coverage-v1"
TRUST_SKIP_DIRS = frozenset(
    "_raw _archived _staging _archives _bootstrap .obsidian .git".split()
)
TRUST_RESERVED_STEMS = frozenset({"index", "log", "hot", "_insights"})
_VOLATILE_CONFIDENCE_KEYS = frozenset(
    {
        "updated",
        "base_confidence",
        "lifecycle",
        "lifecycle_changed",
        "lifecycle_reason",
        "superseded_by",
    }
)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)
_TOP_LEVEL_FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*):")
_CONFIDENCE_RE = re.compile(r"^base_confidence:\s*([^#\n]+)", re.MULTILINE)
_LIFECYCLE_RE = re.compile(r"^lifecycle:\s*([^#\n]+)", re.MULTILINE)


def _normalise_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _frontmatter(text: str) -> str:
    match = _FRONTMATTER_RE.match(_normalise_text(text))
    return match.group(1) if match else ""


def _strip_volatile_confidence_fields(frontmatter: str) -> str:
    """Remove confidence/lifecycle bookkeeping while retaining material metadata."""
    kept: list[str] = []
    skipping = False
    for line in frontmatter.splitlines():
        field = _TOP_LEVEL_FIELD_RE.match(line)
        if field:
            skipping = field.group(1) in _VOLATILE_CONFIDENCE_KEYS
        if not skipping:
            kept.append(line.rstrip())
    return "\n".join(kept).strip()


def page_fingerprint(path: Path) -> str:
    """Hash material claims and evidence, excluding volatile review bookkeeping."""
    text = _normalise_text(path.read_text(encoding="utf-8", errors="replace"))
    match = _FRONTMATTER_RE.match(text)
    if not match:
        material = text.strip() + "\n"
    else:
        stable_frontmatter = _strip_volatile_confidence_fields(match.group(1))
        body = text[match.end() :].strip()
        material = f"---\n{stable_frontmatter}\n---\n{body}\n"
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_confidence(path: Path) -> float:
    match = _CONFIDENCE_RE.search(_frontmatter(path.read_text(encoding="utf-8", errors="replace")))
    if not match:
        raise ValueError("missing base_confidence")
    try:
        value = float(match.group(1).strip().strip("'\""))
    except ValueError as exc:
        raise ValueError("base_confidence is not numeric") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError("base_confidence is outside [0.0, 1.0]")
    return value


def iter_trust_pages(vault: Path) -> list[Path]:
    """Return content pages carrying the confidence/lifecycle trust schema."""
    pages: list[Path] = []
    for path in vault.rglob("*.md"):
        rel = path.relative_to(vault)
        if any(part in TRUST_SKIP_DIRS for part in rel.parts):
            continue
        if path.stem in TRUST_RESERVED_STEMS:
            continue
        frontmatter = _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if _CONFIDENCE_RE.search(frontmatter) and _LIFECYCLE_RE.search(frontmatter):
            pages.append(path)
    return sorted(pages, key=lambda item: str(item.relative_to(vault)))


def build_trust_ledger(vault: Path, *, reviewed_at: str) -> dict[str, Any]:
    """Capture explicitly approved confidence values and material fingerprints."""
    pages: dict[str, dict[str, Any]] = {}
    for path in iter_trust_pages(vault):
        rel = str(path.relative_to(vault))
        pages[rel] = _review_entry(path, reviewed_at)
    return {
        "schema_version": TRUST_LEDGER_SCHEMA_VERSION,
        "method": TRUST_REVIEW_METHOD,
        "reviewed_at": reviewed_at,
        "pages": pages,
    }


def _review_entry(path: Path, reviewed_at: str) -> dict[str, Any]:
    return {
        "reviewed_confidence": _parse_confidence(path),
        "material_fingerprint": page_fingerprint(path),
        "reviewed_at": reviewed_at,
    }


def update_trust_ledger(
    vault: Path,
    ledger_path: Path,
    *,
    reviewed_at: str,
    page_paths: list[str],
) -> dict[str, Any]:
    """Update only explicitly reviewed pages while preserving every other entry."""
    if ledger_path.is_file():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot update unreadable trust ledger: {exc}") from exc
        if not isinstance(ledger, dict):
            raise RuntimeError("cannot update trust ledger: top level must be an object")
        if ledger.get("schema_version") != TRUST_LEDGER_SCHEMA_VERSION:
            raise RuntimeError("cannot update unsupported trust ledger schema")
        if ledger.get("method") != TRUST_REVIEW_METHOD or not isinstance(ledger.get("pages"), dict):
            raise RuntimeError("cannot update malformed trust ledger")
    else:
        ledger = {
            "schema_version": TRUST_LEDGER_SCHEMA_VERSION,
            "method": TRUST_REVIEW_METHOD,
            "reviewed_at": reviewed_at,
            "pages": {},
        }

    current = {str(path.relative_to(vault)): path for path in iter_trust_pages(vault)}
    selected: list[str] = []
    for raw in page_paths:
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RuntimeError(f"trust page must be a safe vault-relative path: {raw}")
        rel = candidate.as_posix().removeprefix("./")
        page = current.get(rel)
        if page is None:
            raise RuntimeError(f"trust page is missing or lacks the trust schema: {raw}")
        if rel not in selected:
            selected.append(rel)
            ledger["pages"][rel] = _review_entry(page, reviewed_at)
    ledger["reviewed_at"] = reviewed_at
    return ledger


def write_trust_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """Write a ledger atomically with stable ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _empty_report(ledger_path: Path) -> dict[str, Any]:
    return {
        "status": "pass",
        "ledger_path": str(ledger_path),
        "reviewed": [],
        "stale": [],
        "unreviewed": [],
        "score_mismatches": [],
        "missing_pages": [],
        "errors": [],
        "counts": {},
    }


def check_trust_ledger(vault: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    """Validate current pages against an approved manual review ledger."""
    path = ledger_path or vault / TRUST_LEDGER_RELATIVE_PATH
    report = _empty_report(path)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report["errors"].append({"issue": "ledger_missing", "path": str(path)})
        return _finalise_report(report)
    except (OSError, json.JSONDecodeError) as exc:
        report["errors"].append({"issue": "ledger_unreadable", "detail": str(exc)})
        return _finalise_report(report)

    if not isinstance(ledger, dict):
        report["errors"].append({"issue": "ledger_must_be_an_object"})
        return _finalise_report(report)
    if ledger.get("schema_version") != TRUST_LEDGER_SCHEMA_VERSION:
        report["errors"].append(
            {
                "issue": "unsupported_schema_version",
                "found": ledger.get("schema_version"),
                "expected": TRUST_LEDGER_SCHEMA_VERSION,
            }
        )
        return _finalise_report(report)
    if ledger.get("method") != TRUST_REVIEW_METHOD:
        report["errors"].append(
            {"issue": "unsupported_review_method", "found": ledger.get("method")}
        )
        return _finalise_report(report)
    entries = ledger.get("pages")
    if not isinstance(entries, dict):
        report["errors"].append({"issue": "pages_must_be_an_object"})
        return _finalise_report(report)

    current: dict[str, Path] = {
        str(page.relative_to(vault)): page for page in iter_trust_pages(vault)
    }
    for rel, page in current.items():
        if rel not in entries:
            report["unreviewed"].append({"page": rel, "reason": "not_in_manual_ledger"})
            continue
        entry = entries[rel]
        if not isinstance(entry, dict):
            report["errors"].append({"page": rel, "issue": "invalid_ledger_entry"})
            continue
        fingerprint = entry.get("material_fingerprint")
        reviewed_confidence = entry.get("reviewed_confidence")
        reviewed_value = (
            float(reviewed_confidence)
            if isinstance(reviewed_confidence, (int, float))
            and not isinstance(reviewed_confidence, bool)
            else math.nan
        )
        valid_confidence = math.isfinite(reviewed_value) and 0.0 <= reviewed_value <= 1.0
        valid_fingerprint = isinstance(fingerprint, str) and bool(
            re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint)
        )
        if not valid_fingerprint or not valid_confidence:
            report["errors"].append({"page": rel, "issue": "invalid_ledger_entry"})
            continue
        if page_fingerprint(page) != fingerprint:
            report["stale"].append({"page": rel, "reason": "material_fingerprint_changed"})
            continue
        try:
            stored_confidence = _parse_confidence(page)
        except ValueError as exc:
            report["errors"].append({"page": rel, "issue": str(exc)})
            continue
        if abs(stored_confidence - reviewed_value) > 1e-9:
            report["score_mismatches"].append(
                {
                    "page": rel,
                    "stored": stored_confidence,
                    "reviewed": reviewed_value,
                }
            )
            continue
        report["reviewed"].append(
            {
                "page": rel,
                "reviewed_confidence": stored_confidence,
                "reviewed_at": entry.get("reviewed_at", ledger.get("reviewed_at", "")),
            }
        )

    for rel in sorted(set(entries) - set(current)):
        report["missing_pages"].append({"page": rel, "reason": "reviewed_page_missing"})
    return _finalise_report(report)


def _finalise_report(report: dict[str, Any]) -> dict[str, Any]:
    keys = ("reviewed", "stale", "unreviewed", "score_mismatches", "missing_pages", "errors")
    report["counts"] = {key: len(report[key]) for key in keys}
    if report["errors"] or report["score_mismatches"]:
        report["status"] = "fail"
    elif report["stale"] or report["unreviewed"] or report["missing_pages"]:
        report["status"] = "warn"
    else:
        report["status"] = "pass"
    return report
