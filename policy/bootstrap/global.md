# LLM Wiki Policy Governance

- For normal project rule creation, restoration, or updates, invoke the `project-rules-init` skill; it selects the correct workflow without requiring the user to remember lower-level commands.
- Before other tool use that can modify files or external state, run `obsidian-wiki rules check --preflight --record` in the target repository.
- When a repository has no current preflight record, only the narrowly validated `obsidian-wiki rules project --apply` recovery command may materialize an explicitly approved policy for that same repository; it must finish by recording a passing preflight.
- Treat a missing, stale, or conflicting policy lock as fail-closed: continue read-only investigation, but do not modify state.
- Read `.ai-policy/policy-pack.md` before implementation and use `.ai-policy/policy.lock.json` as the resolved policy identity.
- Keep three assurance classes separate in every report:
  - **Proven preflight**: deterministic manifest, source hashes, resolver result, pack, lock, and installed bootstrap checks.
  - **Executed checks**: commands that actually ran, with exit codes and results.
  - **AI understanding**: guidance supplied to the model; never claim this is mechanically proven.
- Preserve user-authored instructions. Automation may create or replace only the matching `llm-wiki-global-bootstrap` or `llm-wiki-repository-policy` managed block and the structurally identified LLM Wiki Codex hook entry.
- Treat machine-specific dependency, permission, encoding, network, and tool failures as local environment issues; do not promote their workarounds into repository workflows.
- Require explicit user approval before changing public install/update commands, build configuration, versioning, or supported workflows.
- Determine whether software comes from a published package, editable checkout, or another source before giving install/update instructions; never replace a local editable fork with a remote package unless the user explicitly requests it.
