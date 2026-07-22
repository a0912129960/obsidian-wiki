"""Regression tests for deterministic policy governance."""

from __future__ import annotations

import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from obsidian_wiki import cli, policy


def _project(name: str = "example") -> dict[str, object]:
    return {
        "schema_version": 1,
        "project": name,
        "languages": [],
        "packs": ["default"],
        "checks": [],
        "rules": [],
    }


def test_canonical_policy_manifest_is_complete_and_hash_pinned() -> None:
    root, manifest = policy.load_manifest()

    assert root == policy.policy_root()
    assert {item["id"] for item in manifest["bootstraps"]} == {"global", "repository"}
    assert {item["id"] for item in manifest["adapters"]} == set(policy.SUPPORTED_AGENTS)


def test_change_authority_guardrails_are_shipped() -> None:
    root = policy.policy_root()
    source = policy.load_json(root / "sources" / "global-governance.policy.json")
    rule_ids = {rule["id"] for rule in source["rules"]}
    bootstrap = (root / "bootstrap" / "global.md").read_text(encoding="utf-8")
    agents = (Path(__file__).parents[1] / "AGENTS.md").read_text(encoding="utf-8")

    assert "governance.local-workaround-boundary" in rule_ids
    assert "governance.public-workflow-change-approval" in rule_ids
    assert "governance.install-source-awareness" in rule_ids
    assert "do not promote their workarounds into repository workflows" in bootstrap
    assert "never replace a local editable fork with a remote package" in bootstrap
    assert "## Change Authority Gate" in agents


def test_manifest_rejects_a_tampered_asset(tmp_path: Path) -> None:
    root = tmp_path / "policy"
    shutil.copytree(policy.policy_root(), root)
    (root / "bootstrap" / "repository.md").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(policy.PolicyError, match="hash mismatch"):
        policy.load_manifest(root)


def test_initialize_preserves_agents_content_and_can_resume(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    agents = repo / "AGENTS.md"
    agents.write_bytes(b"# User rules\r\n\r\nKeep this exactly.\r\n")

    first = policy.initialize_repository(repo, apply=True, project=_project())

    assert first["resumed"] is False
    assert agents.read_bytes().startswith(b"# User rules\r\n\r\nKeep this exactly.\r\n")
    pack = repo / ".ai-policy" / policy.PACK_FILE
    pack.unlink()

    resumed = policy.initialize_repository(repo, apply=True, project=_project())

    assert resumed["resumed"] is True
    assert pack.is_file()
    assert policy.preflight(repo)["status"] == "pass"


def test_initialize_refuses_to_replace_different_existing_project(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy.initialize_repository(repo, apply=True, project=_project("first"))

    with pytest.raises(policy.PolicyError, match="different content"):
        policy.initialize_repository(repo, apply=True, project=_project("second"))


def test_policy_directory_symlink_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside-policy"
    repo.mkdir()
    outside.mkdir()
    (outside / policy.PROJECT_FILE).write_text(json.dumps(_project()), encoding="utf-8")
    try:
        (repo / policy.POLICY_DIRNAME).symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(policy.PolicyError, match="symlinked policy path"):
        policy.resolve_policy(repo)


def test_managed_block_rejects_duplicate_markers() -> None:
    existing = (
        b"<!-- llm-wiki-policy:begin id=test -->\n"
        b"one\n"
        b"<!-- llm-wiki-policy:end id=test -->\n"
        b"<!-- llm-wiki-policy:begin id=test -->\n"
        b"two\n"
        b"<!-- llm-wiki-policy:end id=test -->\n"
    )

    with pytest.raises(policy.PolicyError, match="duplicate managed block"):
        policy.managed_block(existing, "test", b"replacement\n")


def test_codex_hook_merge_preserves_unrelated_entries() -> None:
    existing = json.dumps(
        {
            "custom": {"preserved": True},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "custom",
                        "hooks": [{"type": "command", "command": "custom-command"}],
                    }
                ]
            },
        }
    ).encode()

    merged = json.loads(policy.merge_codex_hooks(existing))

    assert merged["custom"] == {"preserved": True}
    groups = merged["hooks"]["PreToolUse"]
    assert groups[0]["hooks"][0]["command"] == "custom-command"
    assert groups[1]["hooks"][0]["statusMessage"] == policy.HOOK_STATUS_MESSAGE


def test_codex_hook_merge_preserves_same_group_siblings_and_status_collisions() -> None:
    managed = policy._codex_hook_group()["hooks"][0]
    sibling = {"type": "command", "command": "custom-command"}
    collision = {
        "type": "command",
        "command": "not-llm-wiki",
        "statusMessage": policy.HOOK_STATUS_MESSAGE,
    }
    existing = json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [sibling, managed, collision]}]}}
    ).encode()

    merged = json.loads(policy.merge_codex_hooks(existing))
    handlers = merged["hooks"]["PreToolUse"][0]["hooks"]

    assert handlers == [sibling, managed, collision]
    assert policy.merge_codex_hooks(policy.merge_codex_hooks(existing)) == policy.merge_codex_hooks(existing)


def test_bootstrap_validates_hooks_before_writing_instructions(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text("not-json", encoding="utf-8")

    with pytest.raises(policy.PolicyError, match="invalid Codex hooks"):
        policy.install_bootstrap(["codex"], home=tmp_path, apply=True)

    assert not (codex_dir / "AGENTS.md").exists()


def test_bootstrap_rolls_back_when_a_later_write_fails(monkeypatch, tmp_path: Path) -> None:
    original_atomic_write = policy._atomic_write
    calls = 0

    def flaky_atomic_write(path: Path, content: bytes, *, root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise policy.PolicyError("simulated second write failure", code=4)
        original_atomic_write(path, content, root=root)

    monkeypatch.setattr(policy, "_atomic_write", flaky_atomic_write)

    with pytest.raises(policy.PolicyError, match="simulated second write failure"):
        policy.install_bootstrap(["codex"], home=tmp_path, apply=True)

    assert not (tmp_path / ".codex" / "AGENTS.md").exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()


def test_execute_checks_replaces_invalid_utf8_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project = _project()
    project["checks"] = [
        {
            "id": "binary-output",
            "argv": [sys.executable, "-c", "import os; os.write(1, b'\\xff')"],
            "required": True,
        }
    ]
    policy.initialize_repository(repo, apply=True, project=project)

    results = policy.execute_checks(repo)

    assert results[0]["status"] == "pass"
    assert "\ufffd" in results[0]["stdout"]


def test_preflight_record_becomes_stale_when_generated_policy_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    policy.initialize_repository(repo, apply=True, project=_project())
    report = policy.preflight(repo)
    policy.record_preflight(repo, report, home=home)

    assert policy.preflight_record_is_current(repo, home=home) is True
    (repo / policy.POLICY_DIRNAME / policy.PACK_FILE).write_text("stale\n", encoding="utf-8")
    assert policy.preflight_record_is_current(repo, home=home) is False


def test_hook_allows_only_safe_commands_without_a_current_record(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "preflight_record_is_current", lambda _repo, home=None: False)
    safe = {
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
    }
    unsafe = {**safe, "tool_input": {"command": "git status & powershell evil.ps1"}}

    assert policy.evaluate_hook(safe) is None
    response = policy.evaluate_hook(unsafe)
    assert response is not None
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status", True),
        ("git reset --hard", False),
        ("obsidian-wiki rules check --preflight", True),
        ("obsidian-wiki rules check --preflight --record", False),
        ("obsidian-wiki rules project --pretty", True),
        ("obsidian-wiki rules project --apply --pretty", False),
        ("rg needle . | powershell evil.ps1", False),
        ("git status & powershell evil.ps1", False),
        ("git status\npowershell evil.ps1", False),
        ("git diff --output=owned.txt", False),
        ("git diff --ext-diff", False),
        ("rg --pre 'powershell evil.ps1' needle .", False),
    ],
)
def test_preflight_safe_command_allowlist(command: str, expected: bool) -> None:
    assert policy.command_is_preflight_safe(command) is expected


def test_project_policy_recovery_is_limited_to_current_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    (repo / ".git").mkdir()
    (other / ".git").mkdir()

    assert policy.command_is_project_policy_recovery(
        f'obsidian-wiki rules project --repo "{repo}" --vault "{tmp_path}" --config proposal.json --apply --pretty',
        repo,
    )
    assert not policy.command_is_project_policy_recovery(
        f'obsidian-wiki rules project --repo "{other}" --apply',
        repo,
    )
    assert not policy.command_is_project_policy_recovery(
        "obsidian-wiki rules project --apply --no-record",
        repo,
    )
    assert not policy.command_is_project_policy_recovery(
        "obsidian-wiki rules project --apply & powershell evil.ps1",
        repo,
    )


def test_hook_allows_only_current_repo_project_policy_recovery(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    (repo / ".git").mkdir()
    (other / ".git").mkdir()
    monkeypatch.setattr(policy, "preflight_record_is_current", lambda _repo, home=None: False)
    payload = {
        "hook_event_name": "PreToolUse",
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": "obsidian-wiki rules project --config proposal.json --apply --pretty"},
    }

    assert policy.evaluate_hook(payload) is None
    payload["tool_input"] = {
        "command": f'obsidian-wiki rules project --repo "{other}" --config proposal.json --apply'
    }
    assert policy.evaluate_hook(payload) is not None


def test_rules_cli_parses_every_public_subcommand() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["rules", "init"]).func is cli.cmd_rules_init
    assert parser.parse_args(["rules", "resolve"]).func is cli.cmd_rules_resolve
    assert parser.parse_args(["rules", "sync"]).func is cli.cmd_rules_sync
    assert parser.parse_args(["rules", "project"]).func is cli.cmd_rules_project
    assert parser.parse_args(["rules", "check"]).func is cli.cmd_rules_check
    assert parser.parse_args(["rules", "install-bootstrap", "--agent", "codex"]).func is cli.cmd_rules_install_bootstrap


def test_rules_init_apply_requires_reviewed_config(tmp_path: Path) -> None:
    args = Namespace(repo=str(tmp_path), config=None, apply=True, pretty=False)

    with pytest.raises(policy.PolicyError, match="requires a reviewed --config"):
        cli.cmd_rules_init(args)


def test_failed_executable_check_is_not_recorded(monkeypatch, tmp_path: Path) -> None:
    report = {"status": "pass", "lock_sha256": "abc"}
    monkeypatch.setattr(policy, "preflight", lambda _repo: report)
    monkeypatch.setattr(
        policy,
        "execute_checks",
        lambda _repo: [{"id": "tests", "required": True, "status": "fail"}],
    )
    recorded = False

    def record(*_args, **_kwargs):
        nonlocal recorded
        recorded = True
        return tmp_path / "state.json"

    monkeypatch.setattr(policy, "record_preflight", record)
    args = Namespace(repo=str(tmp_path), record=True, execute=True, state_home=None, pretty=False)

    assert cli.cmd_rules_check(args) == 6
    assert recorded is False


def test_policy_assets_are_forced_to_lf() -> None:
    attributes = (Path(__file__).parents[1] / ".gitattributes").read_text(encoding="utf-8")

    assert "policy/** text eol=lf" in attributes
