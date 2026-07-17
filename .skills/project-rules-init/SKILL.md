---
name: project-rules-init
description: Bootstrap or revise repository-specific AI coding rules through a research-and-approval gate, then materialize deterministic LLM Wiki policy inputs, pack, lock, and an AGENTS.md managed block. Use for /project-rules-init, creating project rules, policy-initializing a repository, or deriving decidable rules from repository evidence. Preserve this existing skill name; use ai-policy-sync for later synchronization and global bootstrap installation.
---

# Project Rules Init — research, approve, initialize

Create or revise a repository's L3 rules without replacing user-authored instructions. The LLM performs evidence-based archaeology; the `obsidian-wiki rules` CLI performs deterministic materialization and verification.

## Before you start

1. Resolve config with the Config Resolution Protocol in `llm-wiki/SKILL.md`.
2. Read the target repository's `AGENTS.md` and every rule/SOP it explicitly references. Read nested instruction files relevant to the requested scope.
3. Read these vault knowledge sources when present:
   - `skills/new-project-rules-bootstrap.md`
   - `references/agents-md-template.md`
   - `concepts/good-code-principles.md`
   - matching `references/code-style-<language>.md`
4. Treat those vault pages as research provenance, not executable policy. Canonical hard policy comes from the versioned LLM Wiki `policy/manifest.json`.
5. Determine the target repository from the request or current directory. Never commit or push it.

## Gate 1 — research only

Do not modify the target repository during this gate.

1. Run `obsidian-wiki rules init --repo <repo> --pretty` without `--apply` to collect the deterministic language/tool proposal.
2. Classify the project as small, medium, or large. Small disposable projects may stop with a recommendation rather than creating policy.
3. Inspect existing linters, formatters, tests, build commands, rule hierarchy, and change-control requirements.
4. For an established repository, sample 3–5 recently active representative files. Separate observed convention from desired convention.
5. Draft a Research Report containing:
   - instruction hierarchy and conflicts;
   - tools found and exact executable commands;
   - sampled files and observed patterns;
   - target-versus-current gaps;
   - proposed project policy JSON;
   - the exact managed files that would change.

Every proposed rule must have a stable ID and be a decidable one-liner. Use one of these assurance classes:

- `preflight`: deterministic facts the resolver/checker can prove;
- `executable`: behavior checked by a declared argv-based command;
- `guidance`: instructions supplied to an AI but not mechanically provable.

Never label AI acknowledgement, self-reporting, or text loading as proof of understanding.

## Gate 2 — explicit approval

Ask the user to approve the Research Report and project policy proposal. Approval must identify the target repository. If approval is absent or ambiguous, stop without writing.

The proposal follows this shape:

```json
{
  "schema_version": 1,
  "project": "example",
  "languages": ["python"],
  "packs": ["python"],
  "checks": [
    {"id": "tests", "argv": ["python", "-m", "pytest"], "required": true}
  ],
  "rules": [
    {
      "id": "example.no-direct-database-access",
      "assurance": "guidance",
      "severity": "error",
      "statement": "Controllers must call the service layer and must not access database clients directly."
    }
  ]
}
```

Commands are argv arrays, never shell strings. Do not propose dependency or build-file changes merely to install policy tooling. For legacy repositories, prefer existing incremental checks over full historical cleanup.

## Gate 3 — approved materialization

After approval only:

1. Save the reviewed proposal to a scratch file outside the target repository.
2. Run:

   ```text
   obsidian-wiki rules init --repo <repo> --config <proposal.json> --apply --pretty
   ```

3. The CLI may create only:
   - `.ai-policy/project.json`
   - `.ai-policy/policy-pack.md`
   - `.ai-policy/policy.lock.json`
   - the `llm-wiki-repository-policy` managed block in `AGENTS.md`
4. Run `obsidian-wiki rules check --repo <repo> --preflight --record --pretty`. The explicit `--record` authorizes only the local preflight state write used by the Codex hook.
5. Run `obsidian-wiki rules check --repo <repo> --execute --pretty` only when the user approved executing the declared checks. Add `--record` only when the successful result should refresh hook state.

The fail-closed Codex hook intentionally does not allow `rules init --apply` as a preflight-safe recovery command. If a newly initialized repository has no record yet and the hook blocks Gate 3, present the already-approved exact command for the user to run in a trusted terminal; do not weaken or bypass the hook.

Never replace content outside the managed block. Malformed, duplicated, or user-edited managed boundaries are a hard stop.

## Existing policy

If `.ai-policy/project.json` already exists, do not run `rules init`. Research the delta and present an updated complete project policy proposal for approval. After approval, preserve every unrelated field, replace only the reviewed JSON input through an atomic write, run read-only `rules resolve` and `rules sync` previews, then use `ai-policy-sync` to apply generated outputs. Any schema, conflict, or preview failure is a hard stop.

## Report

Report these separately:

1. **Proven preflight** — manifest/source hashes, selected packs, lock/pack freshness, managed block.
2. **Executed checks** — each argv, exit code, and result, or `not executed`.
3. **AI understanding** — always `not mechanically provable`; describe only what guidance was supplied.

Do not append to the vault log unless the user asked to record the operation in the wiki.
