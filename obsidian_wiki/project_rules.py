"""Central project-policy storage and local repository materialization."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import policy


STORE_DIR = Path("_meta") / "project-rules"
STORE_SCHEMA_VERSION = 1


def _git_value(repo: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    value = completed.stdout.strip()
    return value or None


def _git_origin(repo: Path) -> str | None:
    return _git_value(repo, "config", "--get", "remote.origin.url")


def normalize_git_remote(remote: str) -> str:
    """Normalize common SSH and URL forms to one stable repository identity."""

    value = remote.strip().replace("\\", "/")
    scp_match = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", value)
    if scp_match and "://" not in value:
        host, path = scp_match.groups()
        normalized = f"{host}/{path}"
    else:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            normalized = f"{parsed.hostname}{port}/{parsed.path.lstrip('/')}"
        else:
            normalized = value
    return normalized.rstrip("/").removesuffix(".git").casefold()


def repository_identity(repo: Path, *, project_id: str | None = None) -> dict[str, str]:
    repo = repo.expanduser().resolve(strict=True)
    if project_id:
        value = project_id.strip().casefold()
        if not value:
            raise policy.PolicyError("project id must not be empty", code=4)
        kind = "explicit"
    else:
        remote = _git_origin(repo)
        if remote:
            value = normalize_git_remote(remote)
            kind = "git-remote"
        else:
            root_commits = _git_value(repo, "rev-list", "--max-parents=0", "HEAD")
            if not root_commits:
                raise policy.PolicyError(
                    "repository has no remote or commit history; supply one stable --project-id",
                    code=4,
                )
            value = ";".join(sorted(root_commits.casefold().splitlines()))
            kind = "git-root"
    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8")).hexdigest()
    return {"kind": kind, "value": value, "key": digest}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "project"


def canonical_policy_path(vault: Path, identity: dict[str, str], project_name: str) -> Path:
    vault = vault.expanduser().resolve(strict=True)
    store = vault / STORE_DIR
    suffix = identity["key"][:12]
    matches = sorted(store.glob(f"*--{suffix}.json")) if store.is_dir() else []
    if len(matches) > 1:
        raise policy.PolicyError(f"multiple central policies match project identity: {suffix}", code=4)
    if matches:
        return matches[0]
    return store / f"{_slug(project_name)}--{suffix}.json"


def _validate_record(record: object, path: Path) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != {"schema_version", "identity", "policy"}:
        raise policy.PolicyError(f"invalid central project policy record: {path}", code=4)
    if record.get("schema_version") != STORE_SCHEMA_VERSION:
        raise policy.PolicyError(f"unsupported central project policy schema: {path}", code=4)
    identity = record.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"kind", "value", "key"}:
        raise policy.PolicyError(f"invalid central project identity: {path}", code=4)
    if not all(isinstance(identity.get(key), str) and identity[key] for key in identity):
        raise policy.PolicyError(f"invalid central project identity values: {path}", code=4)
    project = record.get("policy")
    policy._validate_project(project, path)
    return record


def _read_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _validate_record(policy.load_json(path), path)


def _read_local_project(repo: Path) -> dict[str, Any] | None:
    path = repo / policy.POLICY_DIRNAME / policy.PROJECT_FILE
    if not path.is_file():
        return None
    project = policy.load_json(path)
    policy._validate_project(project, path)
    return project


def _same_project(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if left is None or right is None:
        return left is right
    return policy.canonical_json_bytes(left) == policy.canonical_json_bytes(right)


def inspect_project_rules(
    repo: Path,
    vault: Path,
    *,
    project_id: str | None = None,
    proposed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo = repo.expanduser().resolve(strict=True)
    vault = vault.expanduser().resolve(strict=True)
    identity = repository_identity(repo, project_id=project_id)
    if proposed is not None:
        policy._validate_project(proposed, Path("<reviewed-project-policy>"))
    local = _read_local_project(repo)
    project_name = str((proposed or local or {"project": repo.name})["project"])
    central_path = canonical_policy_path(vault, identity, project_name)
    record = _read_record(central_path)
    if record is not None and record["identity"] != identity:
        raise policy.PolicyError(f"central project identity does not match its lookup key: {central_path}", code=4)
    central = record["policy"] if record else None

    if proposed is not None:
        action = "create" if central is None else "update"
        if _same_project(proposed, central) and _same_project(proposed, local):
            action = "unchanged"
    elif central is None and local is None:
        action = "research-required"
    elif central is None:
        action = "capture"
    elif local is None:
        action = "restore"
    elif _same_project(central, local):
        action = "unchanged"
    else:
        action = "review-required"

    return {
        "action": action,
        "repo": str(repo),
        "vault": str(vault),
        "identity": identity,
        "central_policy": str(central_path),
        "central_exists": central is not None,
        "local_exists": local is not None,
        "inferred_project": policy.infer_project_config(repo) if action == "research-required" else None,
        "tooling_assessment": policy.assess_project_tooling(repo)
        if action in {"research-required", "review-required"}
        else None,
    }


def apply_project_rules(
    repo: Path,
    vault: Path,
    *,
    project_id: str | None = None,
    proposed: dict[str, Any] | None = None,
    record_preflight: bool = True,
    state_home: Path | None = None,
) -> dict[str, Any]:
    """Store the canonical policy and materialize it into one repository."""

    status = inspect_project_rules(repo, vault, project_id=project_id, proposed=proposed)
    repo = Path(status["repo"])
    vault = Path(status["vault"])
    central_path = Path(status["central_policy"])
    local = _read_local_project(repo)
    existing_record = _read_record(central_path)
    central = existing_record["policy"] if existing_record else None

    if proposed is not None:
        selected = proposed
    elif central is not None and local is not None and not _same_project(central, local):
        raise policy.PolicyError(
            "central and local project policies differ; review the change and supply --config",
            code=4,
        )
    else:
        selected = central or local
    if selected is None:
        raise policy.PolicyError("project rules require research and a reviewed --config", code=4)

    central_record = {
        "schema_version": STORE_SCHEMA_VERSION,
        "identity": status["identity"],
        "policy": selected,
    }
    central_bytes = policy.canonical_json_bytes(central_record)
    if not central_path.is_file() or central_path.read_bytes() != central_bytes:
        policy._atomic_write(central_path, central_bytes, root=vault)

    local_path = repo / policy.POLICY_DIRNAME / policy.PROJECT_FILE
    if local is None:
        policy.initialize_repository(repo, apply=True, project=selected)
    else:
        if not _same_project(local, selected):
            policy._atomic_write(local_path, policy.canonical_json_bytes(selected), root=repo)
        policy.sync_repository(repo, apply=True)

    report = policy.preflight(repo)
    if report["status"] != "pass":
        raise policy.PolicyError("materialized project policy failed preflight", code=6)
    record_path = None
    if record_preflight:
        record_path = policy.record_preflight(repo, report, home=state_home)
    return {
        **status,
        "applied": True,
        "preflight": report,
        "preflight_record": str(record_path) if record_path else None,
    }
