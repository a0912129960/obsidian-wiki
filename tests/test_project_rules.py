"""Tests for centrally managed, locally materialized project rules."""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from obsidian_wiki import cli, policy, project_rules


def _project(name: str = "example", *, statement: str | None = None) -> dict[str, object]:
    rules: list[dict[str, str]] = []
    if statement:
        rules.append(
            {
                "id": f"{name}.rule",
                "assurance": "guidance",
                "severity": "error",
                "statement": statement,
            }
        )
    return {
        "schema_version": 1,
        "project": name,
        "languages": [],
        "packs": ["default"],
        "checks": [],
        "rules": rules,
    }


def _git_repo(path: Path, remote: str = "git@github.com:example/example.git") -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


def test_remote_identity_matches_ssh_and_https_clones(tmp_path: Path) -> None:
    first = _git_repo(tmp_path / "first", "git@github.com:Example/MyDimerco.git")
    second = _git_repo(tmp_path / "second", "https://github.com/example/mydimerco.git")

    assert project_rules.repository_identity(first)["key"] == project_rules.repository_identity(second)["key"]


def test_repository_without_remote_uses_root_commit_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    with pytest.raises(policy.PolicyError, match="stable --project-id"):
        project_rules.repository_identity(repo)

    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    assert project_rules.repository_identity(repo)["kind"] == "git-root"
    assert project_rules.repository_identity(repo, project_id="internal/mydimerco")["kind"] == "explicit"


def test_first_use_requires_research_without_writing(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    vault.mkdir()

    result = project_rules.inspect_project_rules(repo, vault)

    assert result["action"] == "research-required"
    assert result["inferred_project"]["project"] == "repo"
    assert result["tooling_assessment"]["online_verification_required"] is False
    assert not (vault / project_rules.STORE_DIR).exists()
    assert not (repo / policy.POLICY_DIRNAME).exists()


def test_python_tooling_assessment_reports_existing_tools_and_missing_capabilities(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname='example'\n[tool.ruff]\n[tool.pytest.ini_options]\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()

    assessment = policy.assess_project_tooling(repo)

    detected = {(item["language"], item["capability"]) for item in assessment["detected"]}
    gaps = {(item["language"], item["capability"]) for item in assessment["gaps"]}
    assert {("python", "lint"), ("python", "format"), ("python", "test")} <= detected
    assert gaps == {("python", "type-check")}
    assert assessment["gaps"][0]["official_url"].startswith("https://")
    assert assessment["gaps"][0]["requires_approval"] is True

    inferred = policy.infer_project_config(repo)
    checks = {item["id"]: item["argv"] for item in inferred["checks"]}
    assert checks["python-lint"] == ["python", "-m", "ruff", "check", "."]
    assert checks["python-format-check"] == ["python", "-m", "ruff", "format", "--check", "."]
    assert checks["python-tests"] == ["python", "-m", "pytest"]


def test_vue_tooling_assessment_uses_declared_dependencies_and_reports_gaps(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {"vue": "latest", "eslint": "latest", "vitest": "latest"},
                "scripts": {"lint": "eslint .", "test": "vitest run"},
            }
        ),
        encoding="utf-8",
    )

    assessment = policy.assess_project_tooling(repo)

    detected = {item["capability"] for item in assessment["detected"]}
    gaps = {item["capability"] for item in assessment["gaps"]}
    assert {"lint", "test"} <= detected
    assert gaps == {"format", "style-lint", "type-check"}
    checks = {item["id"]: item["argv"] for item in policy.infer_project_config(repo)["checks"]}
    assert checks["vue-lint"] == ["npm", "run", "lint"]
    assert checks["vue-unit-tests"] == ["npm", "test", "--", "--run"]


def test_csharp_tooling_assessment_detects_nested_analyzers_and_format_check(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project_dir = repo / "src" / "Example"
    project_dir.mkdir(parents=True)
    (repo / "Example.sln").write_text("", encoding="utf-8")
    (repo / ".editorconfig").write_text("root = true\n", encoding="utf-8")
    (project_dir / "Example.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>'
        '<PackageReference Include="StyleCop.Analyzers" Version="1.2.0" />'
        "</ItemGroup></Project>",
        encoding="utf-8",
    )

    assessment = policy.assess_project_tooling(repo)

    assert assessment["gaps"] == []
    detected = {item["capability"] for item in assessment["detected"]}
    assert detected == {"analyze", "format", "test"}
    checks = {item["id"]: item["argv"] for item in policy.infer_project_config(repo)["checks"]}
    assert checks["dotnet-format-check"] == [
        "dotnet",
        "format",
        "Example.sln",
        "--verify-no-changes",
    ]
    assert checks["dotnet-tests"] == ["dotnet", "test", "Example.sln"]


def test_inferred_policy_excludes_report_only_tooling_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")

    project = policy.infer_project_config(repo)

    assert "tooling_assessment" not in project


def test_reviewed_policy_is_saved_centrally_and_materialized(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    state_home = tmp_path / "home"
    vault.mkdir()
    state_home.mkdir()

    result = project_rules.apply_project_rules(
        repo,
        vault,
        proposed=_project("mydimerco", statement="Controllers must use services."),
        state_home=state_home,
    )

    central = Path(result["central_policy"])
    assert result["action"] == "create"
    assert central.is_file()
    assert json.loads(central.read_text(encoding="utf-8"))["policy"]["project"] == "mydimerco"
    assert (repo / "AGENTS.md").is_file()
    assert policy.preflight(repo)["status"] == "pass"
    assert policy.preflight_record_is_current(repo, home=state_home)


def test_new_clone_restores_central_policy(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    first = _git_repo(tmp_path / "first")
    project_rules.apply_project_rules(first, vault, proposed=_project("example"), state_home=home)
    second = _git_repo(tmp_path / "renamed-clone")

    preview = project_rules.inspect_project_rules(second, vault)
    restored = project_rules.apply_project_rules(second, vault, state_home=home)

    assert preview["action"] == "restore"
    assert restored["action"] == "restore"
    assert (second / policy.POLICY_DIRNAME / policy.PROJECT_FILE).is_file()
    assert policy.preflight(second)["status"] == "pass"


def test_existing_local_policy_is_captured_when_central_is_missing(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    policy.initialize_repository(repo, apply=True, project=_project())

    result = project_rules.apply_project_rules(repo, vault, state_home=home)

    assert result["action"] == "capture"
    assert Path(result["central_policy"]).is_file()


def test_divergent_local_and_central_policy_requires_review(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    project_rules.apply_project_rules(repo, vault, proposed=_project(statement="First"), state_home=home)
    local_path = repo / policy.POLICY_DIRNAME / policy.PROJECT_FILE
    local_path.write_bytes(policy.canonical_json_bytes(_project(statement="Changed locally")))

    assert project_rules.inspect_project_rules(repo, vault)["action"] == "review-required"
    with pytest.raises(policy.PolicyError, match="central and local project policies differ"):
        project_rules.apply_project_rules(repo, vault, state_home=home)


def test_reviewed_update_changes_central_and_local_together(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    project_rules.apply_project_rules(repo, vault, proposed=_project(statement="First"), state_home=home)

    updated = _project(statement="Second")
    result = project_rules.apply_project_rules(repo, vault, proposed=updated, state_home=home)

    central = json.loads(Path(result["central_policy"]).read_text(encoding="utf-8"))
    local = policy.load_json(repo / policy.POLICY_DIRNAME / policy.PROJECT_FILE)
    assert result["action"] == "update"
    assert central["policy"] == updated
    assert local == updated
    assert policy.preflight(repo)["status"] == "pass"


def test_unchanged_policy_does_not_rewrite_central_record(monkeypatch, tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    first = project_rules.apply_project_rules(repo, vault, proposed=_project(), state_home=home)
    central_path = Path(first["central_policy"])
    original_write = policy._atomic_write

    def guarded_write(path: Path, content: bytes, *, root: Path) -> None:
        if path == central_path:
            raise AssertionError("unchanged central policy was rewritten")
        original_write(path, content, root=root)

    monkeypatch.setattr(policy, "_atomic_write", guarded_write)

    result = project_rules.apply_project_rules(repo, vault, state_home=home)

    assert result["action"] == "unchanged"


def test_project_rules_cli_uses_one_command_for_preview_and_apply(tmp_path: Path, capsys) -> None:
    repo = _git_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    config = tmp_path / "proposal.json"
    vault.mkdir()
    home.mkdir()
    config.write_text(json.dumps(_project()), encoding="utf-8")
    base = {
        "repo": str(repo),
        "vault": str(vault),
        "project_id": None,
        "config": None,
        "apply": False,
        "no_record": False,
        "state_home": str(home),
        "pretty": False,
    }

    assert cli.cmd_rules_project(Namespace(**base)) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "research-required"

    applied = {**base, "config": str(config), "apply": True}
    assert cli.cmd_rules_project(Namespace(**applied)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["action"] == "create"
    assert output["preflight"]["status"] == "pass"
