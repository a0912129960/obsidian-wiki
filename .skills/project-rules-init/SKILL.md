---
name: project-rules-init
description: Bootstrap or update repository-specific coding rules in AGENTS.md using a three-gate workflow that verifies tooling, applies universal and language-specific principles, and derives project conventions from repository evidence. Use when the user asks to initialize project rules, create AGENTS.md rules, bootstrap code rules for a repository, or invokes /project-rules-init, including requests such as "幫這個專案建規則".
---

# Project Rules Init — 三道閘專案規則啟動

You are bootstrapping a code-rules system for a target repository: tool verification (L0) + universal principles (L1/L2) + project-specific rules (L3), converging into the repo's `AGENTS.md`. **This skill is a thin shell — the actual procedure lives in the wiki.** Read the playbook pages and follow them; do not improvise a different flow.

## Before You Start

1. **Resolve config** — follow the Config Resolution Protocol in `llm-wiki/SKILL.md` (inline `@name` override → walk up CWD for `.env` → `~/.obsidian-wiki/config` → prompt setup). This gives `OBSIDIAN_VAULT_PATH`.
2. **Read the wiki source pages** (all under `$OBSIDIAN_VAULT_PATH`):
   - `skills/new-project-rules-bootstrap.md` — **the playbook. Execute its Step 0–5 exactly.**
   - `references/agents-md-template.md` — the five-section template for Step 3
   - `concepts/good-code-principles.md` — L1 principles (rule source)
   - `references/code-style-<language>.md` — L2 page(s) matching the target repo's detected language(s)
   - If any of these pages is missing, stop and tell the user which one — do not substitute invented content.
3. **Determine the target repo** — the current working directory, unless the user names a path.
4. **Detect mode**:
   - Default — real run: writes/updates `AGENTS.md` in the target repo
   - `--dry-run` — full analysis, but the draft is written to the scratchpad only; the target repo is not touched

## Execution Contract

Run the playbook's steps against the target repo:

- **Step 0** — classify project size (small → stop and say so; medium/large → continue)
- **Step 1** — detect existing linter/formatter/test tooling; if missing, follow the matching language page's 工具鏈 section. **Hard limits:** never modify build files (csproj, package.json dependencies, requirements) — write recommendations instead; for legacy repos with large error counts, propose baseline/incremental mode, don't demand a cleanup.
- **Step 2** — style archaeology on 3–5 recently active representative files (skip for greenfield). Also read the vault's project pages for this repo (guidelines/risks/architecture) if they exist — known gotchas become rules.
- **Step 3** — fill the template. If `AGENTS.md` exists, **append the rules sections and preserve all existing content**; never rewrite what's already there.
- **Step 4–5** — report the acceptance checklist and iteration loop to the user (these are theirs to run, not yours).

## Safety Rules

- **Never commit or push the target repo.** Leave all changes in the working tree for the owner.
- **Never copy secrets** (connection strings, tokens, keys) into rules, examples, or reports — reference their location generically if a rule concerns them.
- Every rule must be a **decidable one-liner** — you can tell from a diff whether it's violated. Abstract adjectives (clean, good, reasonable) are banned.
- If evidence for a rule is missing or uncertain, ask the user — do not fabricate a convention the repo doesn't show.

## Output

1. The `AGENTS.md` rules sections (real run) or scratchpad draft path (`--dry-run`).
2. A short report: tools detected/recommended, archaeology findings (files sampled, conventions extracted, 目標vs現況 gaps), rule count, and the user's next steps (commit AGENTS.md, run a small task to verify the self-check fires).
3. Append one line to `$OBSIDIAN_VAULT_PATH/log.md`:
   ```
   - [TIMESTAMP] PROJECT_RULES_INIT repo="<path>" mode=real|dry-run size=small|medium|large tools_found=N tools_recommended=M rules=K
   ```
   This skill writes no wiki pages, so skip manifest/index/hot/QMD updates.

## Feedback Loop

If running the playbook surfaces a flaw in the procedure itself (a missing step, a wrong assumption), fix the wiki playbook/template page — that's where the logic lives. This SKILL.md should almost never need editing.
