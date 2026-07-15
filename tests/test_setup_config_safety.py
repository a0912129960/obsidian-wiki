"""Config ownership and skill-update workflow regression tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import obsidian_wiki.cli as cli


def _redirect_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    config_dir = tmp_path / ".obsidian-wiki"
    config = config_dir / "config"
    install_state = config_dir / "install-state.json"
    monkeypatch.setattr(cli, "GLOBAL_CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(cli, "INSTALL_STATE", install_state)
    return config, install_state


def _setup_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "vault": None,
        "copy": True,
        "project_only": False,
        "project": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_setup_preserves_existing_config_byte_for_byte(monkeypatch, tmp_path: Path) -> None:
    config, install_state = _redirect_paths(monkeypatch, tmp_path)
    config.parent.mkdir(parents=True)
    original = (
        "# 個人預設 🧠\r\n"
        'OBSIDIAN_VAULT_PATH="D:\\My Vault"\r\n'
        'OBSIDIAN_WIKI_REPO="D:\\custom-repo"\r\n'
        'OBSIDIAN_WIKI_VERSION="my-version"\r\n'
        "WIKI_STAGED_WRITES=true\r\n"
    ).encode("utf-8")
    config.write_bytes(original)
    calls: list[str] = []
    monkeypatch.setattr(cli, "install_global_skills", lambda mode: calls.append(mode))

    assert cli.cmd_setup(_setup_args()) == 0

    assert config.read_bytes() == original
    assert calls == ["copy"]
    assert json.loads(install_state.read_text(encoding="utf-8"))["version"] == cli.__version__


def test_setup_rejects_vault_override_when_config_exists(monkeypatch, tmp_path: Path) -> None:
    config, _install_state = _redirect_paths(monkeypatch, tmp_path)
    config.parent.mkdir(parents=True)
    original = b'OBSIDIAN_VAULT_PATH="D:\\My Vault"\n'
    config.write_bytes(original)
    called = False

    def install(_mode: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "install_global_skills", install)

    assert cli.cmd_setup(_setup_args(vault="D:\\Other Vault")) == 1
    assert config.read_bytes() == original
    assert called is False


def test_setup_with_unset_existing_config_does_not_suggest_rejected_override(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config, _install_state = _redirect_paths(monkeypatch, tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text("# user-owned\n", encoding="utf-8")
    monkeypatch.setattr(cli, "install_global_skills", lambda _mode: None)

    assert cli.cmd_setup(_setup_args()) == 0

    output = capsys.readouterr().out
    assert "Edit OBSIDIAN_VAULT_PATH" in output
    assert "--vault" not in output


def test_install_skills_never_reads_or_writes_config(monkeypatch, tmp_path: Path) -> None:
    config, install_state = _redirect_paths(monkeypatch, tmp_path)
    config.parent.mkdir(parents=True)
    original = b"not even dotenv\r\n\x00user-owned"
    config.write_bytes(original)
    calls: list[str] = []
    monkeypatch.setattr(cli, "install_global_skills", lambda mode: calls.append(mode))

    args = argparse.Namespace(copy=True, project=None, project_only=False)
    assert cli.cmd_install_skills(args) == 0

    assert config.read_bytes() == original
    assert calls == ["copy"]
    assert json.loads(install_state.read_text(encoding="utf-8"))["mode"] == "copy"


def test_initial_config_does_not_store_install_version(monkeypatch, tmp_path: Path) -> None:
    config, _install_state = _redirect_paths(monkeypatch, tmp_path)
    skills = tmp_path / "repo" / ".skills"
    skills.mkdir(parents=True)
    monkeypatch.setattr(cli, "skills_dir", lambda: skills)

    cli.write_config("D:\\My Vault")

    content = config.read_text(encoding="utf-8")
    assert 'OBSIDIAN_VAULT_PATH="D:\\My Vault"' in content
    assert f'OBSIDIAN_WIKI_REPO="{skills.parent}"' in content
    assert "OBSIDIAN_WIKI_VERSION" not in content


def test_update_skills_alias_routes_to_config_free_command() -> None:
    args = cli.build_parser().parse_args(["update-skills", "--copy"])

    assert args.func is cli.cmd_install_skills
    assert args.copy is True
