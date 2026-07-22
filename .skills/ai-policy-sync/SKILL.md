---
name: ai-policy-sync
description: Advanced internal and diagnostic workflow for resolving, synchronizing, checking, and installing LLM Wiki policy artifacts. Use for explicit policy drift diagnosis, pack/lock repair previews, executable rule checks, global bootstrap administration, Codex hook setup, or /ai-policy-sync. For normal project rule creation, restoration, and updates, use project-rules-init as the single user-facing entrypoint.
---

# AI Policy Sync

Synchronize already-reviewed policy inputs. This is the advanced diagnostic layer used internally by `project-rules-init`; users should not need it for the normal create/restore/update workflow. This skill does not invent project rules.

## Resolve scope

1. Resolve LLM Wiki config using `llm-wiki/SKILL.md`.
2. Read the target repository's `AGENTS.md` and `.ai-policy/project.json` when repository sync/check is requested.
3. Determine whether the request targets:
   - a repository pack/lock;
   - canonical global bootstrap files;
   - deterministic preflight;
   - executable checks;
   - or a combination.
4. Default to preview. Global installation and repository writes require explicit user approval and an `--apply` flag. `rules resolve` is always read-only.

## Repository workflow

Preview drift:

```text
obsidian-wiki rules resolve --repo <repo> --pretty
obsidian-wiki rules sync --repo <repo> --pretty
```

After approval, synchronize:

```text
obsidian-wiki rules sync --repo <repo> --apply --pretty
obsidian-wiki rules check --repo <repo> --preflight --record --pretty
```

Use `--execute` only when the user authorized running the lock's declared commands. The CLI executes argv arrays without a shell. `rules check` is read-only unless `--record` is explicitly supplied.

Missing inputs, unknown packs, source hash mismatch, rule conflicts, stale generated artifacts, or malformed managed blocks are fail-closed. Continue read-only investigation; do not bypass or regenerate around the failure.

For stale state, recover in this order: diagnose the differing input or generated artifact read-only; correct only a separately reviewed policy input; preview deterministic `rules resolve` and `rules sync`; request approval; then apply and re-run preflight. Never treat regeneration itself as approval.

## Global bootstrap workflow

Preview first:

```text
obsidian-wiki rules install-bootstrap --agent codex --pretty
```

Supported file-backed adapters are `codex`, `claude`, `gemini`, and `copilot`; `all` selects all supported adapters. Apply only after approval:

```text
obsidian-wiki rules install-bootstrap --agent codex --apply --pretty
```

The installer preserves user-authored Markdown outside the `llm-wiki-global-bootstrap` managed block. Codex targets `$CODEX_HOME/AGENTS.md` when `CODEX_HOME` is set, otherwise `~/.codex/AGENTS.md`; it also merges one identifiable `PreToolUse` handler into the same directory's `hooks.json`, preserving all unrelated hook entries.

Codex non-managed hooks require user trust after installation. Report the state as `installed-untrusted` until the user reviews it through Codex `/hooks`; never report installation as active enforcement.

If an AI exposes only a settings UI or has no verified file-backed user instruction entry, report it as unsupported/manual. Do not edit undocumented application databases.

## Assurance report

Always use these three headings:

1. **Proven preflight** — deterministic inputs, hashes, resolution, pack, lock, managed block, and hook presence.
2. **Executed checks** — exact argv, exit codes, and results; say `not executed` when applicable.
3. **AI understanding** — `not mechanically provable`; instruction loading and hook presence do not prove comprehension.

## Safety

- Never edit content outside a managed block or the single managed Codex hook entry.
- Never silently repair duplicate/malformed markers or multiple managed hook entries.
- Never install global files during a preview.
- Never modify pilot or target repositories not explicitly approved for writes.
- Never commit or push.
