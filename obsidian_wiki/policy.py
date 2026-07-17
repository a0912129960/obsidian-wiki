"""Deterministic policy resolution and managed bootstrap support.

The policy engine intentionally uses only the Python standard library.  Policy
inputs are JSON, commands are argv arrays executed without a shell, and every
generated artifact is a pure function of versioned inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RESOLVER_VERSION = "1"
CANONICAL_JSON = "llm-wiki-canonical-json-v1"
POLICY_DIRNAME = ".ai-policy"
PROJECT_FILE = "project.json"
PACK_FILE = "policy-pack.md"
LOCK_FILE = "policy.lock.json"
REPO_MANAGED_ID = "llm-wiki-repository-policy"
GLOBAL_MANAGED_ID = "llm-wiki-global-bootstrap"
HOOK_STATUS_MESSAGE = "LLM Wiki policy preflight"
SUPPORTED_AGENTS = ("claude", "codex", "copilot", "gemini")
_RULE_KEYS = {"id", "assurance", "severity", "statement", "replaces", "reason"}
_CHECK_KEYS = {"id", "argv", "required", "timeout_seconds"}
_PROJECT_KEYS = {"schema_version", "project", "languages", "packs", "checks", "rules"}


class PolicyError(RuntimeError):
    """A deterministic policy failure with a stable CLI exit code."""

    def __init__(self, message: str, *, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedPolicy:
    pack: bytes
    lock: dict[str, Any]
    project: dict[str, Any]

    @property
    def lock_bytes(self) -> bytes:
        return canonical_json_bytes(self.lock)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical_json_bytes(value: Any) -> bytes:
    """Return the restricted canonical JSON representation used by policy locks."""

    def validate(item: Any, location: str = "$") -> None:
        if item is None or isinstance(item, (str, bool)) or type(item) is int:
            return
        if isinstance(item, float):
            raise PolicyError(f"canonical JSON forbids floating-point values at {location}")
        if isinstance(item, list):
            for index, child in enumerate(item):
                validate(child, f"{location}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise PolicyError(f"canonical JSON requires string object keys at {location}")
                validate(child, f"{location}.{key}")
            return
        raise PolicyError(f"canonical JSON does not support {type(item).__name__} at {location}")

    validate(value)

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except PolicyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read valid UTF-8 JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"expected a JSON object: {path}")
    return value


def policy_root() -> Path:
    package_root = Path(__file__).resolve().parent
    for candidate in (package_root / "_data" / "policy", package_root.parent / "policy"):
        if (candidate / "manifest.json").is_file():
            return candidate
    raise PolicyError("canonical policy assets are missing; reinstall obsidian-wiki")


def _safe_asset(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise PolicyError(f"policy asset path must stay below policy root: {relative}")
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PolicyError(f"policy asset escapes policy root: {relative}") from exc
    if not path.is_file():
        raise PolicyError(f"policy asset is missing: {relative}")
    return path


def _validate_manifest(root: Path, manifest: dict[str, Any]) -> None:
    allowed = {"schema_version", "canonical_json", "policy_set", "schemas", "sources", "packs", "bootstraps", "adapters"}
    unknown = set(manifest) - allowed
    if unknown:
        raise PolicyError(f"unknown policy manifest fields: {', '.join(sorted(unknown))}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PolicyError("unsupported policy manifest schema_version")
    if manifest.get("canonical_json") != CANONICAL_JSON:
        raise PolicyError("unsupported policy manifest canonical_json")
    if not isinstance(manifest.get("policy_set"), str) or not manifest["policy_set"]:
        raise PolicyError("policy manifest requires a non-empty policy_set")
    for collection in ("schemas", "sources", "packs", "bootstraps"):
        entries = manifest.get(collection)
        if not isinstance(entries, list) or not entries:
            raise PolicyError(f"policy manifest requires a non-empty {collection} array")
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise PolicyError(f"policy manifest {collection} entries must be objects")
            unknown_entry = set(entry) - {"id", "path", "sha256"}
            if unknown_entry:
                raise PolicyError(
                    f"unknown policy manifest {collection} entry fields: {', '.join(sorted(unknown_entry))}"
                )
            item_id = entry.get("id")
            relative = entry.get("path")
            expected_hash = entry.get("sha256")
            if not all(isinstance(v, str) and v for v in (item_id, relative, expected_hash)):
                raise PolicyError(f"invalid policy manifest {collection} entry")
            if item_id in seen:
                raise PolicyError(f"duplicate {collection} id: {item_id}")
            seen.add(item_id)
            path = _safe_asset(root, relative)
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                raise PolicyError(
                    f"policy asset hash mismatch for {relative}: expected {expected_hash}, got {actual_hash}"
                )
    adapters = manifest.get("adapters")
    if not isinstance(adapters, list) or not adapters:
        raise PolicyError("policy manifest requires a non-empty adapters array")
    adapter_ids: set[str] = set()
    bootstrap_ids = {str(entry["id"]) for entry in manifest["bootstraps"]}
    for adapter in adapters:
        if not isinstance(adapter, dict):
            raise PolicyError("policy manifest adapters must be objects")
        allowed_adapter = {"id", "bootstrap", "managed_id", "default_path", "home_env", "hook"}
        unknown_adapter = set(adapter) - allowed_adapter
        if unknown_adapter:
            raise PolicyError(f"unknown adapter fields: {', '.join(sorted(unknown_adapter))}")
        adapter_id = adapter.get("id")
        bootstrap_id = adapter.get("bootstrap")
        managed_id = adapter.get("managed_id")
        default_path = adapter.get("default_path")
        if not all(isinstance(value, str) and value for value in (adapter_id, bootstrap_id, managed_id, default_path)):
            raise PolicyError("invalid policy adapter")
        if adapter_id in adapter_ids:
            raise PolicyError(f"duplicate policy adapter id: {adapter_id}")
        if adapter_id not in SUPPORTED_AGENTS:
            raise PolicyError(f"unsupported policy adapter id: {adapter_id}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", managed_id):
            raise PolicyError(f"invalid managed_id for adapter: {adapter_id}")
        relative_target = Path(default_path)
        if relative_target.is_absolute() or ".." in relative_target.parts:
            raise PolicyError(f"adapter default_path must stay below its managed home: {adapter_id}")
        if bootstrap_id not in bootstrap_ids:
            raise PolicyError(f"unknown adapter bootstrap: {bootstrap_id}")
        home_env = adapter.get("home_env")
        if home_env is not None and (not isinstance(home_env, str) or not home_env):
            raise PolicyError(f"invalid home_env for adapter: {adapter_id}")
        hook = adapter.get("hook", False)
        if not isinstance(hook, bool):
            raise PolicyError(f"adapter hook must be boolean: {adapter_id}")
        adapter_ids.add(adapter_id)
    if adapter_ids != set(SUPPORTED_AGENTS):
        missing = set(SUPPORTED_AGENTS) - adapter_ids
        raise PolicyError(f"policy manifest is missing adapters: {', '.join(sorted(missing))}")


def load_manifest(root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    resolved_root = (root or policy_root()).resolve()
    manifest = load_json(resolved_root / "manifest.json")
    _validate_manifest(resolved_root, manifest)
    return resolved_root, manifest


def _entry_map(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entry["id"]): entry for entry in entries}


def _load_indexed_assets(
    root: Path, entries: list[dict[str, Any]], *, expected_key: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        value = load_json(_safe_asset(root, str(entry["path"])))
        if value.get("schema_version") != SCHEMA_VERSION or value.get("id") != entry["id"]:
            raise PolicyError(f"{expected_key} identity mismatch: {entry['path']}")
        allowed = {"schema_version", "id", "rules"} if expected_key == "source" else {
            "schema_version", "id", "extends", "sources"
        }
        unknown = set(value) - allowed
        if unknown:
            raise PolicyError(
                f"unknown {expected_key} fields in {entry['path']}: {', '.join(sorted(unknown))}"
            )
        result[str(entry["id"])] = value
    return result


def _expand_packs(selected: list[str], packs: dict[str, dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(pack_id: str) -> None:
        if pack_id in visiting:
            raise PolicyError(f"policy pack cycle includes: {pack_id}")
        if pack_id in visited:
            return
        pack = packs.get(pack_id)
        if pack is None:
            raise PolicyError(f"unknown policy pack: {pack_id}")
        visiting.add(pack_id)
        parents = pack.get("extends", [])
        if not isinstance(parents, list) or not all(isinstance(item, str) for item in parents):
            raise PolicyError(f"invalid extends list in policy pack: {pack_id}")
        for parent in parents:
            visit(parent)
        visiting.remove(pack_id)
        visited.add(pack_id)
        ordered.append(pack_id)

    for item in selected:
        visit(item)
    return ordered


def _validate_project(project: dict[str, Any], path: Path) -> None:
    unknown = set(project) - _PROJECT_KEYS
    if unknown:
        raise PolicyError(f"unknown project policy fields in {path}: {', '.join(sorted(unknown))}")
    if project.get("schema_version") != SCHEMA_VERSION:
        raise PolicyError(f"unsupported project policy schema_version: {path}")
    if not isinstance(project.get("project"), str) or not project["project"]:
        raise PolicyError(f"project policy requires project name: {path}")
    packs = project.get("packs")
    if not isinstance(packs, list) or not packs or not all(isinstance(item, str) for item in packs):
        raise PolicyError(f"project policy requires non-empty packs: {path}")
    if len(set(packs)) != len(packs):
        raise PolicyError(f"project policy packs must be unique: {path}")
    languages = project.get("languages", [])
    if not isinstance(languages, list) or not all(isinstance(item, str) for item in languages):
        raise PolicyError(f"project policy languages must be strings: {path}")
    if len(set(languages)) != len(languages):
        raise PolicyError(f"project policy languages must be unique: {path}")
    checks = project.get("checks", [])
    if not isinstance(checks, list):
        raise PolicyError(f"project policy checks must be an array: {path}")
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise PolicyError("project checks must be objects")
        unknown_check = set(check) - _CHECK_KEYS
        if unknown_check:
            raise PolicyError(f"unknown fields for project check: {', '.join(sorted(unknown_check))}")
        check_id = check.get("id")
        argv = check.get("argv")
        required = check.get("required", True)
        if not isinstance(check_id, str) or not check_id or check_id in seen:
            raise PolicyError(f"invalid or duplicate project check id: {check_id}")
        seen.add(check_id)
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise PolicyError(f"check {check_id} requires a non-empty argv string array")
        if not isinstance(required, bool):
            raise PolicyError(f"check {check_id} required must be boolean")
        timeout = check.get("timeout_seconds", 600)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise PolicyError(f"check {check_id} timeout_seconds must be an integer from 1 to 3600")
    project_rules = project.get("rules", [])
    if not isinstance(project_rules, list):
        raise PolicyError(f"project policy rules must be an array: {path}")
    rule_ids: set[str] = set()
    for rule in project_rules:
        if not isinstance(rule, dict):
            raise PolicyError("project rules must be objects")
        unknown_rule = set(rule) - _RULE_KEYS
        if unknown_rule:
            raise PolicyError(f"unknown fields for project rule: {', '.join(sorted(unknown_rule))}")
        required_fields = ("id", "assurance", "severity", "statement")
        if not all(isinstance(rule.get(key), str) and rule[key] for key in required_fields):
            raise PolicyError("project rules require id, assurance, severity, and statement")
        if rule["id"] in rule_ids:
            raise PolicyError(f"duplicate project rule id: {rule['id']}")
        rule_ids.add(rule["id"])
        if rule["assurance"] not in {"preflight", "executable", "guidance"}:
            raise PolicyError(f"invalid assurance for project rule {rule['id']}")
        if rule["severity"] not in {"error", "warning"}:
            raise PolicyError(f"invalid severity for project rule {rule['id']}")
        replaces = rule.get("replaces", [])
        if not isinstance(replaces, list) or not all(isinstance(item, str) and item for item in replaces):
            raise PolicyError(f"rule {rule['id']} replaces must be a string array")
        if len(set(replaces)) != len(replaces):
            raise PolicyError(f"rule {rule['id']} replaces entries must be unique")
        reason = rule.get("reason")
        if replaces and (not isinstance(reason, str) or not reason.strip()):
            raise PolicyError(f"rule {rule['id']} requires a reason when replacing canonical rules")
        if not replaces and reason is not None:
            raise PolicyError(f"rule {rule['id']} reason is only valid with replaces")


def _render_pack(
    project: dict[str, Any], pack_ids: list[str], rules: list[dict[str, Any]], origins: dict[str, str]
) -> bytes:
    sections = (
        ("preflight", "Proven preflight"),
        ("executable", "Executable rule checks"),
        ("guidance", "AI guidance (not mechanically provable)"),
    )
    lines = [
        "# Resolved Policy Pack",
        "",
        f"Project: `{project['project']}`",
        f"Packs: {', '.join(f'`{item}`' for item in pack_ids)}",
        "",
        "This file is generated. Update policy inputs and run `obsidian-wiki rules sync --apply`.",
    ]
    for assurance, heading in sections:
        lines.extend(("", f"## {heading}", ""))
        matching = [rule for rule in rules if rule["assurance"] == assurance]
        if not matching:
            lines.append("- None declared.")
            continue
        for rule in matching:
            lines.append(
                f"- **{rule['id']}** [{rule['severity']}; source={origins[rule['id']]}]: {rule['statement']}"
            )
    checks = sorted(project.get("checks", []), key=lambda item: item["id"])
    lines.extend(("", "## Declared checks", ""))
    if not checks:
        lines.append("- None declared. Executable compliance is not established.")
    for check in checks:
        rendered = " ".join(shlex.quote(arg) for arg in check["argv"])
        requirement = "required" if check.get("required", True) else "optional"
        lines.append(f"- **{check['id']}** ({requirement}): `{rendered}`")
    lines.extend(
        (
            "",
            "## Assurance boundary",
            "",
            "A valid lock proves deterministic resolution only. Command results prove only what those commands check. AI understanding remains unprovable and must be reported as such.",
            "",
        )
    )
    return "\n".join(lines).encode("utf-8")


def _resolve_project(
    repo: Path, project: dict[str, Any], project_path: Path, *, root: Path | None = None
) -> ResolvedPolicy:
    _validate_project(project, project_path)
    policy_assets, manifest = load_manifest(root)
    source_entries = _entry_map(manifest["sources"])
    pack_entries = _entry_map(manifest["packs"])
    sources = _load_indexed_assets(policy_assets, manifest["sources"], expected_key="source")
    packs = _load_indexed_assets(policy_assets, manifest["packs"], expected_key="pack")
    selected = list(project["packs"])
    pack_ids = _expand_packs(selected, packs)
    source_ids: set[str] = set()
    for pack_id in pack_ids:
        declared = packs[pack_id].get("sources")
        if not isinstance(declared, list) or not declared or not all(isinstance(item, str) for item in declared):
            raise PolicyError(f"policy pack requires source ids: {pack_id}")
        source_ids.update(declared)
    unknown = source_ids - sources.keys()
    if unknown:
        raise PolicyError(f"unknown policy sources: {', '.join(sorted(unknown))}")

    by_rule_id: dict[str, dict[str, Any]] = {}
    origins: dict[str, str] = {}
    for source_id in sorted(source_ids):
        declared_rules = sources[source_id].get("rules")
        if not isinstance(declared_rules, list):
            raise PolicyError(f"policy source rules must be an array: {source_id}")
        for rule in declared_rules:
            if not isinstance(rule, dict):
                raise PolicyError(f"policy rules must be objects: {source_id}")
            unknown_rule = set(rule) - {"id", "assurance", "severity", "statement"}
            if unknown_rule:
                raise PolicyError(
                    f"unknown fields for canonical rule in {source_id}: {', '.join(sorted(unknown_rule))}"
                )
            required = ("id", "assurance", "severity", "statement")
            if not all(isinstance(rule.get(key), str) and rule[key] for key in required):
                raise PolicyError(f"invalid rule in policy source: {source_id}")
            if rule["assurance"] not in {"preflight", "executable", "guidance"}:
                raise PolicyError(f"invalid assurance for rule {rule['id']}")
            if rule["severity"] not in {"error", "warning"}:
                raise PolicyError(f"invalid severity for rule {rule['id']}")
            existing = by_rule_id.get(rule["id"])
            if existing is not None:
                raise PolicyError(f"duplicate policy rule id: {rule['id']}")
            by_rule_id[rule["id"]] = rule
            origins[rule["id"]] = f"source:{source_id}"

    for rule in project.get("rules", []):
        replacements = rule.get("replaces", [])
        for target in replacements:
            if target not in by_rule_id:
                raise PolicyError(f"project rule {rule['id']} replaces unknown canonical rule: {target}")
        if rule["id"] in by_rule_id and rule["id"] not in replacements:
            raise PolicyError(
                f"project rule conflicts with canonical rule id without explicit replaces: {rule['id']}"
            )
        for target in replacements:
            by_rule_id.pop(target)
            origins.pop(target)
        effective_rule = {key: rule[key] for key in ("id", "assurance", "severity", "statement")}
        by_rule_id[rule["id"]] = effective_rule
        origins[rule["id"]] = "project"

    rules = [by_rule_id[key] for key in sorted(by_rule_id)]
    pack_bytes = _render_pack(project, pack_ids, rules, origins)
    checks = sorted(project.get("checks", []), key=lambda item: item["id"])
    lock = {
        "schema_version": SCHEMA_VERSION,
        "resolver_version": RESOLVER_VERSION,
        "canonical_json": CANONICAL_JSON,
        "manifest_sha256": sha256_file(policy_assets / "manifest.json"),
        "project_sha256": sha256_bytes(canonical_json_bytes(project)),
        "pack_sha256": sha256_bytes(pack_bytes),
        "bootstraps": [
            {"id": str(entry["id"]), "sha256": str(entry["sha256"])}
            for entry in manifest["bootstraps"]
        ],
        "packs": [
            {"id": pack_id, "sha256": str(pack_entries[pack_id]["sha256"])} for pack_id in pack_ids
        ],
        "sources": [
            {"id": source_id, "sha256": str(source_entries[source_id]["sha256"])}
            for source_id in sorted(source_ids)
        ],
        "rules": [
            {
                "id": rule["id"],
                "origin": origins[rule["id"]],
                "sha256": sha256_bytes(canonical_json_bytes(rule)),
            }
            for rule in rules
        ],
        "checks": [
            {
                "id": check["id"],
                "sha256": sha256_bytes(canonical_json_bytes(check)),
            }
            for check in checks
        ],
    }
    return ResolvedPolicy(pack=pack_bytes, lock=lock, project=project)


def resolve_policy(repo: Path, *, root: Path | None = None) -> ResolvedPolicy:
    repo = repo.resolve(strict=True)
    project_path = repo / POLICY_DIRNAME / PROJECT_FILE
    _safe_repo_path(repo, project_path)
    if not project_path.is_file():
        raise PolicyError(f"project policy is not initialized: {project_path}")
    project = load_json(project_path)
    return _resolve_project(repo, project, project_path, root=root)


def _iter_files(repo: Path, suffix: str, *, limit: int = 1) -> bool:
    skipped = {".git", ".venv", "node_modules", "dist", "build"}
    count = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(item for item in dirs if item not in skipped)
        for name in sorted(files):
            if name.endswith(suffix):
                count += 1
                if count >= limit:
                    return True
    return False


def infer_project_config(repo: Path) -> dict[str, Any]:
    """Infer only facts that are mechanically visible; never invent conventions."""

    repo = repo.resolve()
    languages: list[str] = []
    checks: list[dict[str, Any]] = []
    if (repo / "pyproject.toml").is_file() or (repo / "requirements.txt").is_file():
        languages.append("python")
        if (repo / "tests").is_dir():
            checks.append({"id": "python-tests", "argv": ["python", "-m", "pytest"], "required": True})
    solution = next(iter(sorted(repo.glob("*.sln"))), None)
    if solution is not None or _iter_files(repo, ".csproj"):
        languages.append("csharp")
        if solution is not None:
            checks.append({"id": "dotnet-tests", "argv": ["dotnet", "test", solution.name], "required": True})
        architecture_lint = repo / "scripts" / "lint-mydimerco-architecture.ps1"
        if architecture_lint.is_file():
            checks.append(
                {
                    "id": "architecture-lint-strict",
                    "argv": [
                        "powershell",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        "scripts/lint-mydimerco-architecture.ps1",
                        "-Strict",
                    ],
                    "required": True,
                }
            )
    package_path = repo / "package.json"
    package: dict[str, Any] = {}
    if package_path.is_file():
        package = load_json(package_path)
    runtime_dependencies = package.get("dependencies", {}) if package else {}
    development_dependencies = package.get("devDependencies", {}) if package else {}
    if not isinstance(runtime_dependencies, dict):
        runtime_dependencies = {}
    if not isinstance(development_dependencies, dict):
        development_dependencies = {}
    dependencies = {**runtime_dependencies, **development_dependencies}
    if "vue" in dependencies or _iter_files(repo, ".vue"):
        languages.append("vue-js")
        scripts = package.get("scripts", {}) if isinstance(package.get("scripts", {}), dict) else {}
        if "lint:modified" in scripts:
            checks.append({"id": "vue-lint-modified", "argv": ["npm", "run", "lint:modified"], "required": True})
        if "test:unit" in scripts:
            checks.append({"id": "vue-unit-tests", "argv": ["npm", "run", "test:unit"], "required": True})
        elif "test" in scripts:
            checks.append(
                {"id": "vue-unit-tests", "argv": ["npm", "test", "--", "--run"], "required": True}
            )
    languages = sorted(set(languages))
    packs = languages or ["default"]
    return {
        "schema_version": SCHEMA_VERSION,
        "project": repo.name,
        "languages": languages,
        "packs": packs,
        "checks": sorted(checks, key=lambda item: item["id"]),
        "rules": [],
    }


def managed_block(existing: bytes, managed_id: str, body: bytes) -> bytes:
    """Insert or replace one managed Markdown block while preserving all other bytes."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", managed_id):
        raise PolicyError(f"invalid managed block id: {managed_id}", code=4)
    bom = b"\xef\xbb\xbf" if existing.startswith(b"\xef\xbb\xbf") else b""
    payload = existing[len(bom) :]
    try:
        text = payload.decode("utf-8")
        body_text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError(f"managed Markdown must be valid UTF-8: {exc}", code=4) from exc
    marker_pattern = re.compile(
        r"(?m)^<!-- llm-wiki-policy:(begin|end) id=([A-Za-z0-9._-]+) -->[ \t]*(?:\r?$)"
    )
    markers = list(marker_pattern.finditer(text))
    marker_fragments = text.count("<!-- llm-wiki-policy:begin") + text.count("<!-- llm-wiki-policy:end")
    if marker_fragments != len(markers):
        raise PolicyError("malformed llm-wiki-policy managed marker", code=4)
    blocks: dict[str, tuple[int, int]] = {}
    open_marker: tuple[str, int] | None = None
    for marker in markers:
        kind, marker_id = marker.group(1), marker.group(2)
        if kind == "begin":
            if open_marker is not None or marker_id in blocks:
                raise PolicyError(f"nested or duplicate managed block: {marker_id}", code=4)
            open_marker = (marker_id, marker.start())
            continue
        if open_marker is None or open_marker[0] != marker_id:
            raise PolicyError(f"unmatched managed block end: {marker_id}", code=4)
        blocks[marker_id] = (open_marker[1], marker.end())
        open_marker = None
    if open_marker is not None:
        raise PolicyError(f"unterminated managed block: {open_marker[0]}", code=4)

    newline = "\r\n" if "\r\n" in text else "\n"
    normalized_body = body_text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline).rstrip("\r\n")
    begin = f"<!-- llm-wiki-policy:begin id={managed_id} -->"
    end = f"<!-- llm-wiki-policy:end id={managed_id} -->"
    block = begin + newline + normalized_body + newline + end
    if managed_id in blocks:
        start, finish = blocks[managed_id]
        result = text[:start] + block + text[finish:]
    elif not text:
        result = block + newline
    else:
        separator = newline if text.endswith(("\n", "\r")) else newline + newline
        result = text + separator + block + newline
    return bom + result.encode("utf-8")


def _manifest_asset(root: Path, manifest: dict[str, Any], collection: str, item_id: str) -> bytes:
    entries = _entry_map(manifest[collection])
    entry = entries.get(item_id)
    if entry is None:
        raise PolicyError(f"unknown policy {collection.rstrip('s')}: {item_id}")
    return _safe_asset(root, str(entry["path"])).read_bytes()


def repository_bootstrap(*, root: Path | None = None) -> bytes:
    assets, manifest = load_manifest(root)
    return _manifest_asset(assets, manifest, "bootstraps", "repository")


def _safe_repo_path(repo: Path, path: Path) -> Path:
    """Reject reads or writes that traverse symlinks or leave a repository."""

    repo = repo.expanduser().resolve(strict=True)
    requested = path.expanduser().absolute()
    try:
        relative = requested.relative_to(repo)
    except ValueError as exc:
        raise PolicyError(f"policy path is outside the repository: {path}", code=4) from exc
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PolicyError(f"refusing to traverse symlinked policy path: {current}", code=4)
    return requested


def _atomic_write(path: Path, content: bytes, *, root: Path) -> None:
    root = root.expanduser().resolve(strict=True)
    requested = path.expanduser().absolute()
    try:
        relative = requested.relative_to(root)
    except ValueError as exc:
        raise PolicyError(f"write target is outside the managed root: {path}", code=4) from exc
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise PolicyError(f"refusing to traverse symlinked directory: {current}", code=4)
    requested.parent.mkdir(parents=True, exist_ok=True)
    if requested.parent.resolve(strict=True) != requested.parent.absolute():
        raise PolicyError(f"write target parent resolves through a symlink: {requested.parent}", code=4)
    if requested.is_symlink():
        raise PolicyError(f"refusing to replace symlink: {requested}", code=4)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=requested.parent, prefix=f".{requested.name}.llm-wiki-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, requested)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise PolicyError(f"cannot atomically write {requested}: {exc}", code=4) from exc


def write_resolved(repo: Path, resolved: ResolvedPolicy) -> list[Path]:
    repo = repo.resolve(strict=True)
    policy_dir = repo / POLICY_DIRNAME
    pack_path = policy_dir / PACK_FILE
    lock_path = policy_dir / LOCK_FILE
    _atomic_write(pack_path, resolved.pack, root=repo)
    _atomic_write(lock_path, resolved.lock_bytes, root=repo)
    return [pack_path, lock_path]


def _repository_outputs(
    repo: Path, resolved: ResolvedPolicy, *, root: Path | None = None
) -> dict[str, bytes]:
    agents_path = _safe_repo_path(repo, repo / "AGENTS.md")
    existing_agents = agents_path.read_bytes() if agents_path.is_file() else b""
    expected_agents = managed_block(existing_agents, REPO_MANAGED_ID, repository_bootstrap(root=root))
    return {
        str(repo / POLICY_DIRNAME / PACK_FILE): resolved.pack,
        str(repo / POLICY_DIRNAME / LOCK_FILE): resolved.lock_bytes,
        str(agents_path): expected_agents,
    }


def sync_repository(repo: Path, *, apply: bool, root: Path | None = None) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    resolved = resolve_policy(repo, root=root)
    expected = _repository_outputs(repo, resolved, root=root)
    changes = [
        path
        for path, content in expected.items()
        if not _safe_repo_path(repo, Path(path)).is_file() or Path(path).read_bytes() != content
    ]
    if apply:
        for raw_path in changes:
            _atomic_write(Path(raw_path), expected[raw_path], root=repo)
    return {"changed": changes, "applied": apply, "lock": resolved.lock}


def initialize_repository(
    repo: Path, *, apply: bool, project: dict[str, Any] | None = None, root: Path | None = None
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    project_path = _safe_repo_path(repo, repo / POLICY_DIRNAME / PROJECT_FILE)
    project = project or infer_project_config(repo)
    _validate_project(project, project_path)
    resumed = project_path.is_file()
    if project_path.exists() and not resumed:
        raise PolicyError(f"project policy path is not a regular file: {project_path}", code=4)
    if resumed:
        existing_project = load_json(project_path)
        if canonical_json_bytes(existing_project) != canonical_json_bytes(project):
            raise PolicyError(f"project policy already exists with different content; use rules sync: {project_path}", code=4)
    resolved = _resolve_project(repo, project, project_path, root=root)
    expected = _repository_outputs(repo, resolved, root=root)
    changes = [] if resumed else [str(project_path)]
    changes.extend(
        raw_path
        for raw_path, content in expected.items()
        if not Path(raw_path).is_file() or Path(raw_path).read_bytes() != content
    )
    if not apply:
        return {
            "project": project,
            "changed": changes,
            "applied": False,
            "resumed": resumed,
            "lock": resolved.lock,
        }
    if not resumed:
        _atomic_write(project_path, canonical_json_bytes(project), root=repo)
    for raw_path in changes:
        if raw_path != str(project_path):
            _atomic_write(Path(raw_path), expected[raw_path], root=repo)
    return {
        "project": project,
        "changed": changes,
        "applied": True,
        "resumed": resumed,
        "lock": resolved.lock,
    }


def preflight(repo: Path, *, root: Path | None = None) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    resolved = resolve_policy(repo, root=root)
    checks: list[dict[str, str]] = []
    expected_files = (
        (repo / POLICY_DIRNAME / PACK_FILE, resolved.pack, "policy-pack"),
        (repo / POLICY_DIRNAME / LOCK_FILE, resolved.lock_bytes, "policy-lock"),
    )
    for path, expected, name in expected_files:
        try:
            safe_path = _safe_repo_path(repo, path)
        except PolicyError as exc:
            checks.append({"name": name, "status": "fail", "detail": str(exc)})
            continue
        if not safe_path.is_file():
            checks.append({"name": name, "status": "fail", "detail": f"missing {path}"})
        elif safe_path.read_bytes() != expected:
            checks.append({"name": name, "status": "fail", "detail": f"stale {path}"})
        else:
            checks.append({"name": name, "status": "pass", "detail": sha256_bytes(expected)})
    agents_path = repo / "AGENTS.md"
    try:
        safe_agents_path = _safe_repo_path(repo, agents_path)
        current = safe_agents_path.read_bytes() if safe_agents_path.is_file() else b""
        expected = managed_block(current, REPO_MANAGED_ID, repository_bootstrap(root=root))
        status = "pass" if current == expected else "fail"
        detail = "managed block current" if status == "pass" else "managed block missing or stale"
        checks.append({"name": "repository-bootstrap", "status": status, "detail": detail})
    except PolicyError as exc:
        checks.append({"name": "repository-bootstrap", "status": "fail", "detail": str(exc)})
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "status": "pass" if passed else "fail",
        "assurance": "proven-preflight",
        "checks": checks,
        "lock_sha256": sha256_bytes(resolved.lock_bytes),
        "ai_understanding": "not-provable",
    }


def execute_checks(repo: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    proof = preflight(repo, root=root)
    if proof["status"] != "pass":
        raise PolicyError("cannot execute policy checks before deterministic preflight passes")
    resolved = resolve_policy(repo, root=root)
    results: list[dict[str, Any]] = []
    for check in sorted(resolved.project.get("checks", []), key=lambda item: item["id"]):
        try:
            completed = subprocess.run(
                check["argv"],
                cwd=repo,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=check.get("timeout_seconds", 600),
            )
            results.append(
                {
                    "id": check["id"],
                    "argv": check["argv"],
                    "required": check.get("required", True),
                    "exit_code": completed.returncode,
                    "status": "pass" if completed.returncode == 0 else "fail",
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(
                {
                    "id": check["id"],
                    "argv": check["argv"],
                    "required": check.get("required", True),
                    "exit_code": None,
                    "status": "fail",
                    "stdout": "",
                    "stderr": str(exc),
                }
            )
    return results


def _state_path(repo: Path, home: Path) -> Path:
    identity = sha256_bytes(str(repo.resolve()).encode("utf-8"))[:16]
    return home / ".obsidian-wiki" / "policy-state" / f"{identity}.json"


def record_preflight(repo: Path, report: dict[str, Any], *, home: Path | None = None) -> Path:
    if report.get("status") != "pass":
        raise PolicyError("cannot record a failed policy preflight")
    state_home = (home or Path.home()).resolve(strict=True)
    destination = _state_path(repo, state_home)
    state = {
        "schema_version": SCHEMA_VERSION,
        "repo": str(repo.resolve()),
        "lock_sha256": report["lock_sha256"],
        "status": "pass",
    }
    _atomic_write(destination, canonical_json_bytes(state), root=state_home)
    return destination


def preflight_record_is_current(repo: Path, *, home: Path | None = None) -> bool:
    state_path = _state_path(repo, (home or Path.home()).resolve())
    if not state_path.is_file():
        return False
    try:
        state = load_json(state_path)
        report = preflight(repo)
    except PolicyError:
        return False
    return (
        state.get("status") == "pass"
        and state.get("repo") == str(repo.resolve())
        and state.get("lock_sha256") == report.get("lock_sha256")
        and report.get("status") == "pass"
    )


def _codex_hook_group() -> dict[str, Any]:
    return {
        "matcher": ".*",
        "hooks": [
            {
                "type": "command",
                "command": "obsidian-wiki rules hook",
                "commandWindows": "obsidian-wiki rules hook",
                "timeout": 30,
                "statusMessage": HOOK_STATUS_MESSAGE,
            }
        ],
    }


def merge_codex_hooks(existing: bytes) -> bytes:
    if existing:
        try:
            document = json.loads(existing.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PolicyError(f"cannot merge invalid Codex hooks.json: {exc}", code=4) from exc
        if not isinstance(document, dict):
            raise PolicyError("Codex hooks.json must contain an object", code=4)
    else:
        document = {}
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise PolicyError("Codex hooks.json hooks must be an object", code=4)
    groups = hooks.setdefault("PreToolUse", [])
    if not isinstance(groups, list):
        raise PolicyError("Codex PreToolUse hooks must be an array", code=4)
    replacement = _codex_hook_group()
    replacement_handler = replacement["hooks"][0]
    managed_handlers: list[tuple[int, int]] = []
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for handler_index, handler in enumerate(group["hooks"]):
            if handler == replacement_handler:
                managed_handlers.append((group_index, handler_index))
    if len(managed_handlers) > 1:
        raise PolicyError("multiple LLM Wiki Codex hook entries found", code=4)
    if managed_handlers:
        group_index, handler_index = managed_handlers[0]
        groups[group_index]["hooks"][handler_index] = replacement_handler
    else:
        groups.append(replacement)
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def install_bootstrap(
    agents: list[str], *, home: Path | None = None, apply: bool, root: Path | None = None
) -> dict[str, Any]:
    policy_assets, manifest = load_manifest(root)
    bootstrap = _manifest_asset(policy_assets, manifest, "bootstraps", "global")
    adapters = {str(item["id"]): item for item in manifest["adapters"]}
    selected = list(SUPPORTED_AGENTS) if "all" in agents else sorted(set(agents))
    unknown = set(selected) - set(SUPPORTED_AGENTS)
    if unknown:
        raise PolicyError(f"unsupported bootstrap agents: {', '.join(sorted(unknown))}", code=5)
    changes: list[str] = []
    targets: dict[str, str] = {}
    outputs: dict[Path, tuple[bytes, Path, bytes | None]] = {}
    for agent in selected:
        adapter = adapters[agent]
        relative = Path(str(adapter["default_path"]))
        managed_id = str(adapter["managed_id"])
        explicit_home = home.expanduser().resolve(strict=True) if home is not None else None
        home_env = adapter.get("home_env")
        configured_home = os.environ.get(str(home_env)) if explicit_home is None and home_env else None
        if configured_home:
            managed_root = Path(configured_home).expanduser().resolve(strict=True)
            target = managed_root / relative.name
        else:
            managed_root = explicit_home or Path.home().resolve(strict=True)
            target = managed_root / relative
        targets[agent] = str(target)
        original = target.read_bytes() if target.is_file() else None
        existing = original or b""
        expected = managed_block(existing, managed_id, bootstrap)
        if existing != expected:
            changes.append(str(target))
            outputs[target] = (expected, managed_root, original)
        if adapter.get("hook"):
            hook_path = target.parent / "hooks.json"
            hook_original = hook_path.read_bytes() if hook_path.is_file() else None
            hook_existing = hook_original or b""
            hook_expected = merge_codex_hooks(hook_existing)
            if hook_existing != hook_expected:
                changes.append(str(hook_path))
                outputs[hook_path] = (hook_expected, managed_root, hook_original)
    if apply:
        applied: list[Path] = []
        try:
            for path, (content, managed_root, _original) in outputs.items():
                _atomic_write(path, content, root=managed_root)
                applied.append(path)
        except PolicyError as exc:
            rollback_errors: list[str] = []
            for path in reversed(applied):
                _content, managed_root, original = outputs[path]
                try:
                    if original is None:
                        if path.is_symlink():
                            raise PolicyError(f"refusing to remove symlink during rollback: {path}", code=4)
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_write(path, original, root=managed_root)
                except (OSError, PolicyError) as rollback_exc:
                    rollback_errors.append(f"{path}: {rollback_exc}")
            if rollback_errors:
                raise PolicyError(
                    f"bootstrap apply failed ({exc}); rollback also failed: {'; '.join(rollback_errors)}",
                    code=4,
                ) from exc
            raise
    return {
        "agents": selected,
        "targets": targets,
        "changed": changes,
        "applied": apply,
        "enforcement": {
            agent: ("installed-untrusted" if agent == "codex" and apply else "guidance-only")
            for agent in selected
        },
    }


def find_repo(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / POLICY_DIRNAME / PROJECT_FILE).is_file() or (candidate / ".git").exists():
            return candidate
    return current


_SAFE_COMMANDS = {
    "dir",
    "get-childitem",
    "get-content",
    "ls",
    "pwd",
    "rg",
    "select-string",
    "test-path",
    "where",
    "which",
}
_SAFE_GIT_SUBCOMMANDS = {"diff", "log", "show", "status"}


def command_is_preflight_safe(command: str) -> bool:
    if any(token in command for token in (";", "&", "|", ">", "<", "`", "$(", "\r", "\n")):
        return False
    try:
        argv = shlex.split(command, posix=False)
    except ValueError:
        return False
    if not argv:
        return False
    executable = Path(argv[0].strip('"')).name.lower()
    if executable in _SAFE_COMMANDS:
        if executable == "rg" and any(arg.lower() == "--pre" or arg.lower().startswith("--pre=") for arg in argv[1:]):
            return False
        return True
    if executable == "git":
        if len(argv) <= 1 or argv[1].lower() not in _SAFE_GIT_SUBCOMMANDS:
            return False
        unsafe_git_options = ("--output", "--ext-diff", "--textconv", "--exec-path", "--config-env")
        return not any(arg.lower().startswith(unsafe_git_options) for arg in argv[2:])
    if executable not in {"obsidian-wiki", "obsidian-wiki.exe"} or len(argv) < 3:
        return False
    if argv[1:3] == ["rules", "resolve"]:
        return all(arg not in {"--apply", "--write"} for arg in argv[3:])
    if argv[1:3] == ["rules", "init"]:
        return all(arg != "--apply" for arg in argv[3:])
    if argv[1:3] == ["rules", "check"]:
        return "--execute" not in argv[3:] and "--record" not in argv[3:]
    return False


def evaluate_hook(payload: dict[str, Any], *, home: Path | None = None) -> dict[str, Any] | None:
    """Return a blocking hook response, or None when the tool may proceed."""

    if payload.get("hook_event_name") != "PreToolUse":
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Policy preflight cannot determine the working directory.",
            }
        }
    repo = find_repo(Path(cwd))
    if preflight_record_is_current(repo, home=home):
        return None
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name == "Bash" and isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str) and command_is_preflight_safe(command):
            return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "LLM Wiki policy preflight is missing or stale. Continue read-only investigation, then run "
                "`obsidian-wiki rules check --preflight --record`."
            ),
        }
    }


def read_hook_payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, PolicyError) as exc:
        raise PolicyError(f"invalid hook input: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError("hook input must be a JSON object")
    return value
