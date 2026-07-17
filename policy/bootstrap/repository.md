# Repository Policy Governance

- Before modifying files or external state, run `obsidian-wiki rules check --preflight --record` in this repository.
- Read `.ai-policy/policy-pack.md` and treat a missing, stale, or conflicting `.ai-policy/policy.lock.json` as fail-closed.
- Run declared executable checks only when the user authorizes them, and report their argv, exit codes, and results.
- Preserve user-authored instructions outside this managed block.
- AI understanding is not mechanically provable; report it separately from preflight and executed checks.
