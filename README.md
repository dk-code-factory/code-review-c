# code-review-c

Standalone adaptive Codex review skill. Version 0.1.0, MIT licensed. The implementation
lives on `feat/adaptive-review-controller`; `main` contains the public scaffold.

Review quality comes first. Initial workload limits are provisional, not measured
model capacities. The first release supplies onboarding tools and runs no paid
calibration campaigns.

## Use

Check out the feature branch and copy `codex/skills/code-review-c` into your Codex
skills directory, or add this checkout as a local Codex plugin marketplace. The
package contains exactly one skill, `code-review-c`. It needs no sibling checkout,
external review plugin, or build step. Select it in Codex and request:

- “Review PR URL with code-review-c.”
- “Preview a strategy for base REV and head REV.”
- “Prepare model onboarding; do not run trials.”

Python 3.11+, Git and native Codex agents support review/preview; authenticated `gh`
supports PR pinning. Models and effort inherit the host selection. A missing or
incompatible profile uses a provisional 58,000-byte total visible-input budget.
No measured profiles are distributed. Binary, submodule and type changes remain
explicit gaps requiring separate inspection; indivisible oversized hunks also remain
gaps. Dirty worktrees are recorded, but evidence comes only from pinned commits.

The controller identifies semantic relationships and applicable aspects; deterministic
helpers enforce exact hunk/aspect assignment, byte budgets, task dependencies and
whole-PR essential gates. Tasks queue within native concurrency. Security and serious
resource/performance checks remain essential after correctness findings. Optional
improvements open only after complete, verified, clean essential coverage.

Read the [skill](codex/skills/code-review-c/SKILL.md),
[controller procedure](codex/skills/code-review-c/references/controller.md),
[record contracts](codex/skills/code-review-c/references/contracts.md), and
[onboarding procedure](codex/skills/code-review-c/references/onboarding.md).

## Development

```sh
python3 scripts/check.py
python3 scripts/test.py
```

Runtime and tests use the Python standard library. Tests simulate reviewers and
calibration receipts; they make no model calls. Onboarding preparation freezes
fixtures and candidate strategies without inference. Model trials require explicit
call/time limits and an immutable Docker image with Codex installed. Trial containers
see only pinned source/evidence and temporary Codex authentication, keeping answer
keys outside the reviewer filesystem. Three matched repetitions, held-out fixtures,
critical-defect recall, precision and completion gates precede profile promotion.

Keep review artifacts, campaign data, local profiles, credentials, private rules,
caches and benchmark results outside the package and reviewed repository. Provenance
for adapted review guidance is pinned in [PROVENANCE.json](PROVENANCE.json), with
upstream license notices retained in [NOTICE](NOTICE). This scaffold does not change
the source `code-review-ad` repository or its configured reviewers.
