# Record contracts (version 1)

All durable records contain `kind` and integer `version: 1`; unknown versions fail.
Hashes are SHA-256. Structured identities use canonical JSON (sorted keys, ASCII
escapes, compact separators). Byte hashes use the exact bytes. Paths to private
artifacts resolve relative to their bundle and cannot escape it. Public examples
below describe shapes, not measured evidence.

| Kind | Required content |
| --- | --- |
| `inventory` | `id`, absolute source `repo`, resolved `base`, `head`, `diff_base`, `dirty`, frozen `pr` or null, `files`, `hunks`. |
| `review-plan` | `id`, `inventory_id`, pinned revisions, resolved reviewer `identity` or null, `instruction_hash`, `expected` hunk/aspect pairs, `tasks`, explicit `gaps`, `optional_reason`. |
| `task-result` | Plan/task IDs, input hash, observed identity or null, completion status, inspected hunk IDs, extra visible bytes, finding candidates, gap strings. See [JSON schema](schemas/task-result.json). |
| `verification` | `plan_id`, `decisions` with candidate `t0001:local-id`, disposition, evidence, and `into` for merges. |
| `model-profile` | Promoted status, exact identity/scope, measured limits, quality/cost, strategy, hashed comparison reference and campaign identity. |
| `campaign-spec` | Exact identity/scope, focused `baseline` strategy ID, candidate strategies, fixtures with gold keys, explicit call/time limits. |
| `campaign` | Immutable ID, identity/scope, strategies, baseline, limits, frozen fixture trees/prompts/hashes, keys hash, three repetitions. |
| `trial-receipt` | Campaign/fixture/strategy/repetition, identity/input hash, launched flag, status, elapsed/reserved time, usage or null, trace/result hashes. |
| `calibration-result` | Completion status, inspected hunks, extra visible bytes, findings, gaps. See [JSON schema](schemas/calibration-result.json). |
| `grading-decisions` | Per-finding entries with `finding`, `disposition`, nonempty evidence, and `gold` for a match. |
| `trial-grade` | Receipt hash, all grading decisions, matched gold IDs, false positives, unresolved candidates, critical misses, recall, precision. |
| `comparison` | Campaign/identity/scope, matrix/split eligibility, all strategy metrics and trial references, missing measurements, qualifying flag. |

An identity is exactly `{model, reasoning_effort, codex_version, instruction_hash}`.
Scope is `{languages: [sorted exact language names], domain: backend|generic,
aspect: primary-purpose}`. Profile lookup matches all fields; aliases and unknown
effort/version do not match. No profile is shipped by default.

Each inventory hunk has stable `id`, new and old paths, status, language/component
hints, raw bytes/hash/ref, optional `[old_start, old_count, new_start, new_count]`
span, binary/opaque flags. Metadata-only changes have a null span and remain covered.
Task patch byte accounting can exceed original file diff size because headers repeat
across independent hunk handoffs and invalid UTF-8 bytes are visibly escaped.

Each plan task has one aspect/purpose and stage, exact owned hunks, component,
languages/domain, instruction refs/hashes, supporting ranges/hashes, prerequisites,
profile ID, limits and host reservation. `bytes` separates patch, instructions,
evidence, envelope and host and gives their total; `input_hash` identifies the
rendered handoff. Limits are `total_visible_bytes`, `patch_bytes`, `support_bytes`,
`file_count`. Gaps have hunk IDs, aspects and reason. Supplemental interface tasks
do not double-count ordinary hunk/aspect coverage.

Controller analysis is an optional JSON object:

```json
{
  "files": {
    "api/handler.py": {
      "component": "account-write",
      "domain": "backend",
      "aspects": ["data-consistency", "simplification"],
      "evidence": [{"revision": "PINNED_SHA", "path": "api/store.py", "start": 1, "end": 30}]
    }
  },
  "interfaces": [{
    "hunks": ["ENDPOINT_HUNK_1", "ENDPOINT_HUNK_2"],
    "reason": "Handler response is consumed by the changed client schema",
    "evidence": [{"revision": "PINNED_SHA", "path": "api/schema.py", "start": 1, "end": 12}]
  }],
  "optional_reason": "Changed response mapping duplicates an existing conversion"
}
```

The six essential aspects cannot be removed. Optional aspects are explicit; no
default improvement pass runs. Requesting an optional aspect requires an explicit,
nonblank `optional_reason` identifying the changed behavior that warrants it,
including when its hunks become coverage gaps. The controller checks the reason
against pinned evidence; the helper rejects missing or empty reasons. Unknown
paths/aspects and unpinned evidence fail.

Campaign fixtures have `id`, `repo`, explicit `base`/`head`, `split: train|heldout`,
`dimensions` (`patch_size`, `support`, `file_count`, `languages`, `defect_position`),
`gold: [{id, critical: boolean, criterion}]`, optional supporting `evidence` ranges,
and optional `host_input_bytes`. Strategies have safe unique `id`, `layout:
focused|combined`, `aspects` starting with the primary purpose, and positive limits.
Frozen `limits` have positive `max_calls`, `max_wall_seconds`, `trial_seconds` before
execution. Preparation defaults to zero. Combined strategies are comparison controls.
The fixture matrix reference lists dimension values; measurable labels must match
actual rendered evidence. Keys remain in `grading/keys.json` outside trial mounts.

Grading decisions example:

```json
{
  "kind": "grading-decisions",
  "version": 1,
  "decisions": [{
    "finding": "f1",
    "disposition": "matched",
    "gold": "g1",
    "evidence": ["The finding demonstrates the same missing ownership check as g1"]
  }]
}
```

State is intentionally auditable JSON. Private state is trusted local evidence;
hashes detect accidental drift, not malicious edits by its owner. A profile cannot
prove that a human's gold keys, adjudications, or resolved model identity are true.
