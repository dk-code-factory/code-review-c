# Evidence and result discipline

Inspect every owned hunk before deeper candidate verification. Read bounded base/head
context; check for omitted tool output. Missing or truncated ranges are coverage gaps.
Compare the same conditions at the pinned base and head. State a concrete trigger,
changed mechanism, and consequence. Check supported configurations and counterexamples.

| Finding | Evidence needed |
| --- | --- |
| Runtime/security defect | Reachable changed path, trigger, impact, and base comparison. |
| Test/build defect | Wrong behavior accepted by a claimed check, missing stimulus, supported build failure, assertion or cleanup hazard. |
| Diagnostic defect | Changed visibility/meaning under the same configuration, including alternate caller/operator signals. |
| Documentation defect | Source-backed contradiction of the exact assertion with its scope and conditions. |
| Rule violation | Exact applicable mandatory rule, scope, violated predicate, and exceptions. |
| Maintenance defect | Named duplicated sites, coupled state, API obligations, or supported failure mode and its coordination consequence. |
| Requirement defect | Explicit promised condition and evidence that changed behavior violates it. |

Omit taste, generic warnings, and speculative hardening. Label directly related
pre-existing issues explicitly; do not call them introduced regressions. Keep distinct
mechanisms separate, merge equivalent claims, retain unresolved applicability.
Use priorities 0 (critical), 1 (high), 2 (normal), 3 (low). Improvements are labeled
separately and require a concrete benefit justified by changed behavior.

Return a `task-result` v1 with `plan_id`, `task_id`, `input_hash`, `identity` (null
when unavailable), `status` (`complete`, `incomplete`, `failed`), `inspected_hunks`,
`extra_visible_bytes`, `findings`, and `gaps`. Each finding has a local `id`, priority,
category (`defect` or `improvement`), `claim`, owned `hunks`, and nonempty `evidence`
strings identifying pinned locations, conditions, and supporting observations. No
private reasoning is requested. Include gaps as concise strings. Count all extra
source/tool text shown after the handoff in `extra_visible_bytes`.
