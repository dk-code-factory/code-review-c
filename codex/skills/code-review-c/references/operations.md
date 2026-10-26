# Operating code-review-c 0.1.0

## Supported environment and installation

The first-release support target is Linux with CPython 3.11–3.15, Git, and a Codex
host providing native agents. GitHub PR pinning additionally requires authenticated
`gh`. Container trials require a working Linux Docker engine and an immutable image
containing the selected Codex version. Review and preview do not need Docker.
Other host platforms and Python versions have not been validated for this release.

Install the `v0.1.0` tag as described in the repository README. Keep an old installed
skill directory outside the active skills directory for rollback. A copied skill
and a local plugin marketplace are both supported. Neither installation needs
another reviewer plugin or an editable sibling repository.

Check `python3 --version`, `git --version`, native-agent availability, and, for PRs,
`gh auth status` before beginning. Resolve bundled helpers relative to the installed
skill. Run `python3 SKILL_DIR/scripts/review.py --help` to check helper discovery.

## Private state and pinned evidence

Create a new private artifact directory outside both the installed package and the
reviewed repository. For example, `mktemp -d` creates a temporary directory with
private permissions. Keep the inventory bundle, analysis, plan, worker prompts,
receipts, and verification together there. Preserve it while a review is active;
temporary storage can disappear after restart. Never commit it or place credentials
or campaign results in the public package.

Review evidence comes from resolved Git commits. Dirty worktree changes are recorded
but excluded. Commit changes through the normal development process before asking
this release to review them; the reviewer must not commit user work to create pins.
Remote branch movement does not change an existing bundle. A changed review scope
requires fresh inventory and planning.

The controller must supply applicable rules and specification evidence and account
for host-visible instructions. Unknown model identity uses provisional limits;
controller identity must not be substituted for reviewer identity. The 58,000-byte
limit is a provisional total visible-input budget, not a measured model capacity.
No calibrated model profile is distributed, and no paid calibration campaign is
required or run for this release.

## Reading review outcomes

| Essential gate | Meaning | Operator action |
| --- | --- | --- |
| `pending` | Essential work or candidate verification remains unfinished. | Continue scheduling and verification; do not claim complete coverage. |
| `blocked` | A verified essential defect or an essential coverage gap exists. | Address findings or inspect the gap separately; optional reviews stay closed. |
| `clean` | Essential tasks completed and verification found no essential defects or gaps. | Inspect the report's remaining coverage gaps and any warranted optional work. |

`clean` describes the essential gate. Optional work can still be pending, and its
missing receipts remain report gaps. A passing helper command alone does not prove
that a review completed. Check `findings`, `coverage_gaps`, and the gate together.
Binary, submodule, type changes, and indivisible oversized hunks require separate
inspection in this release and remain explicit gaps. Do not erase or relabel those
gaps to obtain a clean result. No meaningful specification means an explicit
requirements gap, rather than invented acceptance criteria.

## Interrupted reviews

Preserve the plan, all attempts, and completed receipts. After verifying that a
worker stopped, remove it from the controller's active-task list; never remove a
live worker merely because observation timed out. Resume `next` against the same
pinned bundle and plan with valid receipts and an accurate active list.

A failed or incomplete receipt stays a gap. The helper does not automatically retry
it, and duplicate task receipts are rejected. Keep the original attempt when the
controller replans or reissues work. `expand` changes the plan ID, invalidating
receipts from the previous plan; retain those receipts as historical evidence and
collect fresh receipts for the new plan. Do not edit old plan IDs or input hashes
to make stale evidence pass. If safe replacement work cannot be completed, report
the gap and the reason optional work was skipped.

## Interrupted container trials

Use only explicitly authorized call/time limits. Every started call consumes its
allowance, including failures and interruptions. SIGINT attempts container cleanup
and records an interrupted attempt. Abrupt process loss can leave a `started`
receipt and an orphaned container. Preserve the receipt and reserve its recorded
time when resuming; never delete attempts to reclaim an allowance. Started,
interrupted, and cleanup-failed attempts charge the larger of reserved and actual
elapsed time. Ordinary completed attempts charge actual elapsed time.

The preliminary version probe prints its unique `crc-version-` container name to
stderr before launch and attempts bounded cleanup on every exit. If cleanup fails
or the runner is lost, verify the runner has stopped, inspect that exact printed
container, and remove it with `docker rm -f EXACT_CONTAINER_NAME`. Version probes
have no trial receipt and launch no model calls.

Before recovery, verify the original runner has stopped. Its container name is
`crc-` followed by the first 20 hexadecimal characters of SHA-256 of the canonical
JSON string containing the absolute attempt-directory path, as implemented by
`reviewlib.records.digest`. Match that exact container to the retained attempt;
inspect it and remove it with `docker rm -f EXACT_CONTAINER_NAME` if necessary.
Do not remove containers belonging to other runs. Confirm any temporary mounts
are unused before removing leftover temporary authentication directories.

An existing attempt directory is never overwritten. A resumed campaign can launch
only remaining attempts within its frozen allowance; replacement trials require a
new campaign. Missing outputs, failed cleanup, unresolved grades, or incomplete
measurements block profile promotion. Keep keys and human grading evidence outside
reviewer mounts and never use the fake runtime integration results as calibration.
