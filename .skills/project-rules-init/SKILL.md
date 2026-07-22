---
name: project-rules-init
description: Centrally manage one repository's AI rules through LLM Wiki and materialize them into the repository's AGENTS.md. Use this single entrypoint for first-time rule creation, restoring rules on a new computer, updating existing rules, or requests such as /project-rules-init, "set up this project's rules", "restore project rules", and "update this repo's AI policy". The skill automatically detects the situation; users should not need to remember the lower-level rules commands.
---

# Project Rules — one entrypoint

Give the user one workflow for creating, restoring, and updating repository-specific rules. LLM Wiki is the central source; the repository-local `AGENTS.md` and `.ai-policy/` files are generated enforcement artifacts. Never commit or push the target repository.

## Contract

- Determine the target repository from the current working directory unless the user explicitly names another repository.
- Resolve `OBSIDIAN_VAULT_PATH` with the Config Resolution Protocol in `llm-wiki/SKILL.md`, then read the vault's `AGENTS.md` when present.
- Store canonical project policies under `$OBSIDIAN_VAULT_PATH/_meta/project-rules/`.
- Identify a repository by normalized Git `remote.origin.url`; the deterministic command handles SSH/HTTPS normalization. If no remote exists, use the repository's root commit identity. Only a repository with neither a remote nor commit history requires asking once for a stable `--project-id`. Never match centrally managed rules by folder name alone.
- Materialize rules into the target repository's `AGENTS.md` managed block and `.ai-policy/` files. Agents must read the local artifacts, never the vault copy directly.
- Preserve all user-authored `AGENTS.md` content outside the managed block.
- Never commit, stage, or push generated policy files.
- Policy creation or content changes require an explicit approval of the complete proposed policy. Restoring an unchanged, already-approved central policy does not require a second content approval.

## Single state check

Run this read-only command first:

```text
obsidian-wiki rules project --repo <repo> --vault <vault> --pretty
```

Do not expose the lower-level command sequence unless the user asks for diagnostics. Branch on `action`:

| Action | Meaning | Workflow |
|---|---|---|
| `research-required` | Neither the vault nor repository has rules | Research, propose, approve, then create both |
| `restore` | Vault has rules; repository does not | Materialize the vault policy locally |
| `capture` | Repository has rules; vault does not | Validate and copy the existing policy into the vault |
| `unchanged` | Vault and repository agree | Verify/materialize generated outputs; report no policy change |
| `review-required` | Vault and repository policies differ | Research the delta and obtain approval before choosing a replacement |

## First-time research

Do not modify the vault or target repository during research.

1. Read the target repository's `AGENTS.md` and every rule or SOP it references.
2. Inspect existing linters, formatters, tests, build commands, rule hierarchy, and change-control requirements.
3. For an established repository, sample 3–5 recently active representative files. Separate observed convention from desired convention.
4. Use `inferred_project` from the state check for mechanically detected languages, packs, and commands.
5. Inspect `tooling_assessment`. When it reports gaps, read `references/toolchain-research.md`, verify candidates against official documentation, and prepare a tooling proposal. Do not install anything during research.
6. Draft a concise report containing the observed evidence, tooling gaps, complete tooling proposal, complete proposed project policy JSON, and exact files that will change.

The tooling proposal must list every selected or rejected gap, official source and verification date, exact version constraint, install argv, files changed, configuration, final check argv, and legacy adoption mode. Prefer an existing repository-native tool over a catalog default. If official browsing is unavailable, report the gap but do not propose an unverified installation.

Do not reject a missing capability merely because its tool is new, requires a development-only dependency, or needs approval. The complete proposal is the approval boundary. Recommend the smallest verified development-tool setup when it materially adds lint, format, analyzer, type-check, or test coverage. Keep development tools separate from runtime dependencies and explain interpreter/runtime compatibility. Reject a candidate only for a repository-specific technical or scope reason, and state that reason.

Every rule needs a stable ID and one decidable statement. Assurance values mean:

- `preflight`: deterministic policy integrity that the checker proves;
- `executable`: behavior enforced by a declared argv-based command;
- `guidance`: local instructions supplied to an AI but not mechanically provable.

Commands are argv arrays, never shell strings. Do not add a tool merely to satisfy policy bookkeeping; every proposed tool must provide a named project-quality capability. Prefer incremental checks for legacy repositories.

## Approval and materialization

Ask the user to approve the complete policy when creating or changing policy content. Approval must identify the target repository. Save the approved proposal to a scratch file outside the target repository, then run one applying command:

```text
obsidian-wiki rules project --repo <repo> --vault <vault> --config <approved-proposal.json> --apply --pretty
```

When the approved proposal includes toolchain changes, apply only those listed changes before materializing policy. Do not modify dependency manifests, project files, lock files, or tool configuration that were absent from the approved proposal. Add executable checks only after their commands exist. If tool setup fails, stop without writing the central policy and report the partial repository changes for review.

For `restore`, `capture`, or `unchanged`, run:

```text
obsidian-wiki rules project --repo <repo> --vault <vault> --apply --pretty
```

The applying command centrally stores the selected policy, writes local artifacts, runs deterministic preflight, and records the successful state for the Codex hook. A conflict without an approved `--config` fails closed.

The Codex hook treats this exact project-policy applying command as the only mutation-safe recovery path when a new checkout has no preflight record. The recovery parser rejects shell composition, unknown options, `--no-record`, and a `--repo` target that differs from the current repository.

## Global enforcement bootstrap

The project command does not silently alter user-level AI configuration. Check the global bootstrap once with:

```text
obsidian-wiki rules install-bootstrap --agent all --pretty
```

If it is missing or stale, explain that this is a one-time machine-level installation, obtain approval, and apply it within the same skill invocation:

```text
obsidian-wiki rules install-bootstrap --agent all --apply --pretty
```

Codex enforcement remains `installed-untrusted` until the user reviews the hook through Codex `/hooks`. File-backed instructions do not mechanically prove that an AI understood them.

## Optional executable checks

Preflight is automatic. Run declared test, lint, or build commands only when the user authorizes execution:

```text
obsidian-wiki rules check --repo <repo> --execute --record --pretty
```

## Hard stops

- Malformed or duplicate managed markers.
- Unknown policy packs, invalid schemas, source hash mismatches, or unresolved rule conflicts.
- Multiple central records matching one project identity.
- A requested content update without explicit approval.

Continue with read-only diagnosis; never bypass the check or replace user-authored instructions.

## Report

Report only:

1. **Central policy** — created, restored, updated, captured, or unchanged; include its vault-relative path.
2. **Project enforcement** — local files written and preflight result.
3. **Executed checks** — exact commands and results, or `not executed`.
4. **AI understanding** — always `not mechanically provable`; state whether the Codex hook is installed and trusted separately.
