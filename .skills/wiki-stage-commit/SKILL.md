---
name: wiki-stage-commit
description: >
  Review and promote staged wiki pages to their final locations. Use when WIKI_STAGED_WRITES=true
  and the user says "/wiki-stage-commit", "review staged pages", "commit staged writes",
  "promote staged pages", "approve staged changes", or "what's waiting in staging".
  Shows each staged file, lets the user accept or reject it, and moves accepted files to
  their final wiki locations. Rejected files are moved back to _raw/ for manual editing.
---

# Wiki Stage Commit — Staged Write Promotion

You are reviewing LLM-written pages that are waiting in `_staging/` for human approval before they land in the live wiki. This skill is only useful when `WIKI_STAGED_WRITES=true` in the vault config.

## Before You Start

1. **Resolve config** — follow the Config Resolution Protocol in `llm-wiki/SKILL.md`. This gives `OBSIDIAN_VAULT_PATH` and `WIKI_STAGED_WRITES`.
2. If `WIKI_STAGED_WRITES` is not set or is `false`, tell the user: "Staged writes mode is not enabled. Set `WIKI_STAGED_WRITES=true` in your `.env` to use this feature." Then stop.
3. Read `.manifest.json`, including top-level `asset_batches`, then read the `_staging/` directory inventory. A page or patch with `asset_batch_id` participates in that batch's attachment lifecycle.

## Invocation Forms

```
/wiki-stage-commit               # interactive review: show each file and ask accept/reject
/wiki-stage-commit --all         # accept all staged files without per-file review
/wiki-stage-commit --reject-all  # reject all staged files (move to _raw/ for manual editing)
/wiki-stage-commit --list        # list staged files with summary, no changes
```

## Step 1: Inventory Staged Files

Glob `$OBSIDIAN_VAULT_PATH/_staging/**/*.md` — these are the pending pages. Do not treat binary files in `_staging/attachments/` as pages.

Also glob `$OBSIDIAN_VAULT_PATH/_staging/**/*.patch.md` — these are pending *updates* to existing pages (diff-style files showing proposed additions and deletions).

For every page or patch, read `asset_batch_id` and use the manifest mapping to count its associated staged attachments. Verify each `_staging/attachments/...` file still matches the recorded full SHA-256 before offering acceptance. Report the inventory:

```
Staged files: 4 new pages, 2 updates

New pages:
  _staging/concepts/attention-mechanism.md        (ingested 2 days ago)
  _staging/entities/andrej-karpathy.md            (ingested 2 days ago)
  _staging/skills/fine-tuning-llms.md             (ingested yesterday)
  _staging/references/attention-is-all-you-need.md (ingested 3 hours ago; 2 attachments)

Updates (patch files):
  _staging/concepts/transformer-architecture.patch.md  (target: concepts/transformer-architecture.md)
  _staging/skills/prompt-engineering.patch.md          (target: skills/prompt-engineering.md)
```

If `_staging/` is empty, report: "Nothing staged. All writes have been committed or no staged writes have been produced yet."

## Step 2: Per-File Review (interactive mode)

For each staged file (new pages first, then updates):

### For new pages:

Display a summary:

```
--- New page: concepts/attention-mechanism.md ---
Title:    Attention Mechanism
Tags:     #ml #architecture
Summary:  Core building block of transformers — computes weighted sum of values based on query-key similarity.
Tier:     supporting
Confidence: 0.72
Sources:  papers/attention.pdf

[Preview first 20 lines of body]
...

Accept [a], Reject [r], Skip [s], Preview full [p]?
```

### For patch files:

Display a structured diff:

```
--- Update: concepts/transformer-architecture.md ---
Source: _staging/concepts/transformer-architecture.patch.md

Proposed additions (+):
+ Transformers outperform RNNs on tasks requiring long-range dependencies. ^[inferred]
+ New source: papers/survey-2026.pdf

Proposed deletions (-):
- The attention mechanism was first described in [Bahdanau 2015].  (to be replaced by updated claim)

⚠️  Conflict check: target page was modified 3 days after staging. Review carefully.

Accept [a], Reject [r], Skip [s], Preview full diff [p]?
```

If `--all` flag is set, skip prompting and accept every file.
If `--reject-all` flag is set, skip prompting and reject every file.
If `--list` flag is set, stop after printing the inventory (Step 1).

## Step 3: Apply Decisions

### Accepting a new page

1. If the page has an `asset_batch_id`, publish each mapped attachment it consumes from `_staging/attachments/<name>` to `attachments/<name>` first. Reuse an existing destination only when its full SHA-256 matches; never overwrite different bytes. Update `published_path` in the manifest mapping.
2. Move `_staging/<category>/page.md` → `<category>/page.md` (the final location).
3. Update `index.md` with the new page entry.
4. Remove the staged file. Remove a staged attachment copy only when every accepted consumer has a hash-verified published copy and no pending or skipped consumer still needs the staged copy.

### Accepting a patch/update

1. Read the current page at the target path.
2. If the patch has an `asset_batch_id`, publish and hash-verify its mapped attachments exactly as for a new page before applying the patch.
3. Apply the proposed additions and deletions (merge, don't just overwrite).
4. Update the `updated` frontmatter timestamp.
5. Update `index.md` if the summary changed.
6. Remove the staged patch file and any no-longer-consumed staged attachment copies.

### Rejecting a file

Move it to `$OBSIDIAN_VAULT_PATH/_raw/` for manual editing:
- `_staging/concepts/page.md` → `_raw/rejected-concepts-page.md`
- `_staging/concepts/page.patch.md` → `_raw/rejected-patch-concepts-page.md`
- Prefix with `rejected-` so the user can identify it

If the rejected file has an `asset_batch_id`, set that batch to `needs_rework` and keep every original in `_raw/assets/`. A derived `_staging/attachments/` copy may be removed only when no pending or skipped page consumes it, every accepted consumer has a hash-verified published copy, and the corresponding raw original exists with the recorded hash. Never delete or archive a raw original on rejection.

### Skipping a file

Leave the page or patch and its derived attachments in `_staging/`. Keep the batch `awaiting_review` and leave `_raw/assets/` unchanged.

### Conflict detection on patch accept

Before applying a patch, check whether the target page's `updated` frontmatter is newer than the patch file's own `updated` field:
- If the target was modified AFTER the patch was staged, warn: `⚠️ Conflict: target was updated since this patch was staged. Applying may lose recent changes.`
- Give the user a chance to abort: `Apply anyway [y], Skip [s], Reject [r]?`

## Step 4: Update Tracking Files

After processing all staged files:

1. Reconcile each affected asset batch:
   - Only when **every associated page or patch is accepted**, verify every final embed resolves under `attachments/`, then move every recorded raw file by exact path to `_raw/_archived/assets/`, preserve its relative layout, record `archived_path`, and set the batch to `archived`. Never overwrite a collision; append a numeric suffix and record the actual path.
   - If any file was rejected, keep the raw pool and set `needs_rework`.
   - If any file was skipped or remains pending, keep the raw pool and set `awaiting_review`.
   - Leave the empty `_raw/assets/` and `_staging/attachments/` directories in place after successful finalization.
2. **`hot.md`** — update the Recent Activity section: "Committed N staged pages; rejected M."
3. **`log.md`** — append attachment counts and affected batch IDs:
   ```
   - [TIMESTAMP] STAGE_COMMIT accepted=N rejected=M skipped=K attachments_published=P asset_batches="id,..."
   ```

## Step 5: Report

```
Stage commit complete.

✅  Accepted (N):
  concepts/attention-mechanism.md     → now live
  entities/andrej-karpathy.md         → now live
  concepts/transformer-architecture.md → updated (patch applied)

❌  Rejected (M):
  skills/fine-tuning-llms.md          → moved to _raw/rejected-skills-fine-tuning-llms.md

⏭️  Skipped (K):
  references/attention-is-all-you-need.md → still in _staging/

Staging queue: K files remaining
Attachments published: P; asset batches archived: B; batches awaiting review/rework: R
```

## Notes

- Staged files use the same page template as live pages — they are ready to land, just awaiting approval
- Patch files use a human-readable diff format: lines starting with `+` are additions, lines starting with `-` are deletions
- `index.md` and `log.md` are always updated immediately on ingest (they are low-risk tracking files) — only category pages go through staging
- The `_staging/` directory is not tracked by Obsidian's graph view — pages only appear in the wiki after promotion
- `_staging/attachments/` contains derived review copies, not source backups. `_raw/assets/` remains the recoverable source pool until its entire batch is accepted and archived.
