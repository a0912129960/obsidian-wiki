# LLM Wiki Policy Governance

- Before using a tool that can modify files or external state, run `obsidian-wiki rules check --preflight --record` in the target repository.
- Treat a missing, stale, or conflicting policy lock as fail-closed: continue read-only investigation, but do not modify state.
- Read `.ai-policy/policy-pack.md` before implementation and use `.ai-policy/policy.lock.json` as the resolved policy identity.
- Keep three assurance classes separate in every report:
  - **Proven preflight**: deterministic manifest, source hashes, resolver result, pack, lock, and installed bootstrap checks.
  - **Executed checks**: commands that actually ran, with exit codes and results.
  - **AI understanding**: guidance supplied to the model; never claim this is mechanically proven.
- Preserve user-authored instructions. Automation may create or replace only the matching `llm-wiki-global-bootstrap` or `llm-wiki-repository-policy` managed block and the structurally identified LLM Wiki Codex hook entry.
